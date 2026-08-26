from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IMMUTABLE_IMAGE = re.compile(r"(?:@sha256:[0-9a-f]{64}|:[0-9a-f]{40})$")
PROMOTION_IMAGE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
LOAD_SCRIPT = ROOT / "tests" / "load" / "smoke" / "scenario.js"
IMAGE_ROLES = ("backend", "frontend", "document_worker")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_image(value: str, field: str) -> str:
    if not IMMUTABLE_IMAGE.search(value):
        raise ValueError(f"{field} must end in a 40-character commit tag or sha256 digest")
    return value


def _require_promotion(value: str, field: str) -> str:
    if not PROMOTION_IMAGE.fullmatch(value) or value.endswith(":latest"):
        raise ValueError(f"{field} must be an explicit registry tag and must not be latest")
    return value


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _run(argv: list[str], *, env: dict[str, str], dry_run: bool) -> None:
    if dry_run:
        print("DRY_RUN", json.dumps(argv))
        return
    subprocess.run(argv, cwd=ROOT, env=env, check=True)


def _health(base_url: str, *, attempts: int = 20, dry_run: bool = False) -> None:
    if dry_run:
        print("DRY_RUN health", f"{base_url.rstrip('/')}/api/health")
        return
    endpoint = f"{base_url.rstrip('/')}/api/health"
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(endpoint, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("status") == "ok":
                    return
        except Exception as exc:  # noqa: BLE001 - receipt retains only the final type
            last_error = exc
        time.sleep(3)
    raise RuntimeError(f"health gate failed after {attempts} attempts: {type(last_error).__name__}")


def _compose(compose_file: Path, images: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    env["MENTORFLOW_BACKEND_IMAGE"] = images["backend"]
    env["MENTORFLOW_FRONTEND_IMAGE"] = images["frontend"]
    env["MENTORFLOW_DOCUMENT_WORKER_IMAGE"] = images["document_worker"]
    env.setdefault("MENTORFLOW_ALLOWED_ORIGINS", "https://invalid.example")
    return ["docker", "compose", "-f", str(compose_file)], env


def _smoke(base_url: str, env: dict[str, str], *, dry_run: bool) -> None:
    k6 = shutil.which("k6")
    if not k6 and not dry_run:
        raise RuntimeError("k6 is required on the staging runner")
    _run(
        [k6 or "k6", "run", "-e", f"BASE_URL={base_url.rstrip('/')}", str(LOAD_SCRIPT)],
        env=env,
        dry_run=dry_run,
    )


def _promote(candidates: dict[str, str], promotions: dict[str, str], env: dict[str, str], *, dry_run: bool) -> None:
    for role in IMAGE_ROLES:
        _run(
            ["docker", "buildx", "imagetools", "create", "--tag", promotions[role], candidates[role]],
            env=env,
            dry_run=dry_run,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed three-image staging release with automatic rollback.")
    parser.add_argument("--compose-file", type=Path, required=True)
    for role in IMAGE_ROLES:
        option = role.replace("_", "-")
        parser.add_argument(f"--{option}-image", required=True)
        parser.add_argument(f"--{option}-rollback-image", required=True)
        parser.add_argument(f"--{option}-promotion-image")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    compose_file = (ROOT / args.compose_file).resolve() if not args.compose_file.is_absolute() else args.compose_file.resolve()
    if not compose_file.is_relative_to(ROOT) or not compose_file.is_file():
        raise SystemExit("compose file must be an existing repository file")
    if not LOAD_SCRIPT.is_file():
        raise SystemExit("tests/load/smoke/scenario.js is required before staging release")
    candidates = {role: _require_image(getattr(args, f"{role}_image"), f"{role}_image") for role in IMAGE_ROLES}
    rollbacks = {role: _require_image(getattr(args, f"{role}_rollback_image"), f"{role}_rollback_image") for role in IMAGE_ROLES}
    promotion_values = {role: getattr(args, f"{role}_promotion_image") for role in IMAGE_ROLES}
    if any(promotion_values.values()) != all(promotion_values.values()):
        raise SystemExit("all three promotion image tags must be provided together")
    promotions = (
        {role: _require_promotion(str(promotion_values[role]), f"{role}_promotion_image") for role in IMAGE_ROLES}
        if all(promotion_values.values())
        else {}
    )
    if not args.base_url.startswith("https://") and not args.dry_run:
        raise SystemExit("staging base URL must use HTTPS")

    receipt: dict[str, object] = {
        "schema": "mentorflow.enterprise-release-receipt.v2",
        "started_at_utc": _now(),
        "candidate_images": candidates,
        "rollback_images": rollbacks,
        "promotion_images": promotions,
        "rollback_retained": False,
        "phases": [],
        "status": "running",
    }
    receipt_path = (ROOT / args.receipt).resolve() if not args.receipt.is_absolute() else args.receipt.resolve()
    if not receipt_path.is_relative_to(ROOT) and not args.dry_run:
        raise SystemExit("receipt must remain inside the repository workspace")
    _write_receipt(receipt_path, receipt)

    base, env = _compose(compose_file, candidates)
    try:
        for image in rollbacks.values():
            _run(["docker", "pull", image], env=env, dry_run=args.dry_run)
        receipt["rollback_retained"] = True
        receipt["phases"].append({"name": "retain_rollback_images", "status": "passed"})
        for image in candidates.values():
            _run(["docker", "pull", image], env=env, dry_run=args.dry_run)
        receipt["phases"].append({"name": "pull_candidate_images", "status": "passed"})
        _run(base + ["up", "--detach", "--wait", "frontend"], env=env, dry_run=args.dry_run)
        receipt["phases"].append({"name": "migration_and_deploy", "status": "passed"})
        _health(args.base_url, dry_run=args.dry_run)
        receipt["phases"].append({"name": "candidate_health", "status": "passed"})
        _smoke(args.base_url, env, dry_run=args.dry_run)
        receipt["phases"].append({"name": "candidate_smoke", "status": "passed"})
        if promotions:
            _promote(candidates, promotions, env, dry_run=args.dry_run)
            receipt["phases"].append({"name": "promote_three_images", "status": "passed"})
        else:
            receipt["phases"].append({"name": "promote_three_images", "status": "not_requested"})
        receipt["status"] = "released"
    except Exception as release_error:  # noqa: BLE001 - every candidate failure triggers rollback
        receipt["phases"].append({"name": "candidate_gate", "status": "failed", "error_type": type(release_error).__name__})
        rollback_base, rollback_env = _compose(compose_file, rollbacks)
        try:
            if promotions:
                _promote(rollbacks, promotions, rollback_env, dry_run=args.dry_run)
                receipt["phases"].append({"name": "rollback_promotion_tags", "status": "passed"})
            _run(rollback_base + ["up", "--detach", "--wait", "frontend"], env=rollback_env, dry_run=args.dry_run)
            receipt["phases"].append({"name": "rollback_deploy", "status": "passed"})
            _health(args.base_url, dry_run=args.dry_run)
            receipt["phases"].append({"name": "rollback_health", "status": "passed"})
            _smoke(args.base_url, rollback_env, dry_run=args.dry_run)
            receipt["phases"].append({"name": "post_rollback_smoke", "status": "passed"})
            receipt["status"] = "rolled_back"
        except Exception as rollback_error:  # noqa: BLE001 - record terminal rollback failure
            receipt["phases"].append({"name": "rollback_verify", "status": "failed", "error_type": type(rollback_error).__name__})
            receipt["status"] = "rollback_failed"
        raise
    finally:
        receipt["finished_at_utc"] = _now()
        _write_receipt(receipt_path, receipt)
    print(json.dumps({"status": receipt["status"], "receipt": str(receipt_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

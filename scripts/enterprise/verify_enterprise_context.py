from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def verify(root: Path = ROOT) -> dict[str, object]:
    lock = json.loads((root / ".github" / "enterprise-context.lock.json").read_text(encoding="utf-8"))
    if lock.get("schema") != "mentorflow.e1-ci-context-lock.v1":
        raise RuntimeError("CONTEXT_LOCK_INVALID: unsupported lock schema")
    if lock.get("contract_version") != "enterprise-context.v1":
        raise RuntimeError("CONTEXT_LOCK_INVALID: contract_version is not enterprise-context.v1")
    if lock.get("provider_mode") != "fake" or lock.get("real_provider_calls") != 0:
        raise RuntimeError("PROVIDER_BOUNDARY_INVALID: CI must remain provider=0")
    if lock.get("owned_contract_paths") != []:
        raise RuntimeError("CONTRACT_OWNERSHIP_INVALID: E1-07 owns no shared contract paths")
    dependencies = lock.get("integration_dependencies")
    if dependencies != [f"E1-{index:02d}" for index in range(1, 7)]:
        raise RuntimeError("DEPENDENCY_LOCK_INVALID: E1-01 through E1-06 are required in order")

    manifest_path = root / str(lock["contract_manifest"])
    test_path = root / str(lock["contract_test"])
    if not manifest_path.is_file() or not test_path.is_file():
        raise RuntimeError(
            "UPSTREAM_CONTEXT_MISSING: integrate the frozen enterprise-context.v1 dependency before running gates"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract_version") != lock["contract_version"]:
        raise RuntimeError("CONTEXT_VERSION_MISMATCH")
    bundle = manifest.get("hashes", {}).get("bundle")
    if bundle != lock["contract_bundle_sha256"]:
        raise RuntimeError("CONTEXT_BUNDLE_HASH_MISMATCH")
    return {
        "status": "PASS",
        "contract_version": lock["contract_version"],
        "bundle": bundle,
        "dependencies": dependencies,
        "real_provider_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the frozen upstream Enterprise context dependency.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = verify(args.root.resolve())
    except (KeyError, OSError, ValueError, RuntimeError) as exc:
        print(str(exc))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

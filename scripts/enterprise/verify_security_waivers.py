from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WAIVERS = ROOT / ".github" / "enterprise-security-waivers.json"


def load_waivers(path: Path, *, today: date | None = None) -> dict[tuple[str, str], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "mentorflow.enterprise-security-waivers.v1":
        raise RuntimeError("unsupported security waiver schema")
    current = today or date.today()
    result: dict[tuple[str, str], dict[str, str]] = {}
    ids: set[str] = set()
    for raw in payload.get("waivers", []):
        required = ("id", "scanner", "finding", "scope", "reason", "owner", "expires_on")
        missing = [field for field in required if not str(raw.get(field, "")).strip()]
        if missing:
            raise RuntimeError(f"security waiver missing fields {missing}")
        if len(str(raw["reason"]).strip()) < 12:
            raise RuntimeError(f"security waiver {raw['id']} reason is too short")
        if "*" in str(raw["finding"]) or str(raw["finding"]).lower() in {"all", "any"}:
            raise RuntimeError(f"security waiver {raw['id']} must identify one exact finding")
        expiry = date.fromisoformat(str(raw["expires_on"]))
        if expiry < current:
            raise RuntimeError(f"security waiver {raw['id']} expired on {expiry.isoformat()}")
        if raw["id"] in ids:
            raise RuntimeError(f"duplicate security waiver id: {raw['id']}")
        ids.add(str(raw["id"]))
        key = (str(raw["scanner"]), str(raw["finding"]))
        if key in result:
            raise RuntimeError(f"duplicate waiver target: {key}")
        result[key] = {field: str(raw[field]) for field in required}
    return result


def _osv_findings(path: Path) -> set[tuple[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    findings: set[tuple[str, str]] = set()

    def walk(value: object, *, under_vulnerabilities: bool = False) -> None:
        if isinstance(value, dict):
            if under_vulnerabilities and value.get("id"):
                findings.add(("osv", str(value["id"])))
            for key, child in value.items():
                walk(child, under_vulnerabilities=under_vulnerabilities or key == "vulnerabilities")
        elif isinstance(value, list):
            for child in value:
                walk(child, under_vulnerabilities=under_vulnerabilities)

    walk(payload)
    return findings


def _semgrep_findings(path: Path) -> set[tuple[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        ("semgrep", str(item["check_id"]))
        for item in payload.get("results", [])
        if item.get("check_id") and str(item.get("extra", {}).get("severity", "")).upper() == "ERROR"
    }


def _npm_findings(path: Path) -> set[tuple[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        ("npm-audit", str(package))
        for package, item in payload.get("vulnerabilities", {}).items()
        if str(item.get("severity", "")).lower() == "critical"
    }


def evaluate(
    waiver_path: Path,
    *,
    osv_paths: list[Path],
    semgrep_paths: list[Path],
    npm_paths: list[Path],
    today: date | None = None,
) -> dict[str, object]:
    waivers = load_waivers(waiver_path, today=today)
    findings: set[tuple[str, str]] = set()
    for path in osv_paths:
        findings.update(_osv_findings(path))
    for path in semgrep_paths:
        findings.update(_semgrep_findings(path))
    for path in npm_paths:
        findings.update(_npm_findings(path))
    waived = sorted(f"{scanner}:{finding}" for scanner, finding in findings if (scanner, finding) in waivers)
    unwaived = sorted(f"{scanner}:{finding}" for scanner, finding in findings if (scanner, finding) not in waivers)
    return {
        "schema": "mentorflow.enterprise-security-gate.v1",
        "status": "PASS" if not unwaived else "FAIL",
        "finding_count": len(findings),
        "waived": waived,
        "unwaived": unwaived,
        "waiver_count": len(waivers),
    }


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate expiring security waivers and gate mature scanner results.")
    parser.add_argument("--waivers", type=Path, default=DEFAULT_WAIVERS)
    parser.add_argument("--osv", type=Path, action="append", default=[])
    parser.add_argument("--semgrep", type=Path, action="append", default=[])
    parser.add_argument("--npm-audit", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(
        args.waivers.resolve(),
        osv_paths=[path.resolve() for path in args.osv],
        semgrep_paths=[path.resolve() for path in args.semgrep],
        npm_paths=[path.resolve() for path in args.npm_audit],
    )
    if args.output:
        _write_atomic(args.output.resolve(), result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

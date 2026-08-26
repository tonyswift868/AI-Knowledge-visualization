from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SBOM = ROOT / "reports" / "enterprise" / "machine" / "lockfile-sbom.cdx.json"
DEFAULT_OUTPUT = ROOT / "reports" / "enterprise" / "machine" / "dependency-risk-report.json"
COPYLEFT_MARKERS = ("GPL", "AGPL", "LGPL", "MPL", "EPL", "CDDL")
CANONICAL_JSON_HASH_ALGORITHM = "sha256:canonical-json-v1"


def canonical_json_sha256(payload: object) -> str:
    """Hash JSON meaning, not checkout-specific whitespace or line endings."""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _properties(component: dict[str, object]) -> dict[str, str]:
    return {
        str(item["name"]): str(item["value"])
        for item in component.get("properties", [])
        if isinstance(item, dict) and "name" in item and "value" in item
    }


def _licenses(component: dict[str, object]) -> list[str]:
    values: list[str] = []
    for wrapper in component.get("licenses", []):
        if not isinstance(wrapper, dict):
            continue
        license_value = wrapper.get("license")
        if isinstance(license_value, dict):
            value = license_value.get("id") or license_value.get("name")
            if value:
                values.append(str(value))
        elif wrapper.get("expression"):
            values.append(str(wrapper["expression"]))
    return sorted(set(values))


def _deprecated_npm() -> set[tuple[str, str]]:
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    deprecated: set[tuple[str, str]] = set()
    for location, item in lock.get("packages", {}).items():
        if not location or "node_modules/" not in location or not item.get("deprecated"):
            continue
        name = location.rsplit("node_modules/", 1)[-1]
        version = str(item.get("version") or "")
        if version:
            deprecated.add((name, version))
    return deprecated


def render(sbom_path: Path) -> str:
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    deprecated = _deprecated_npm()
    entries: list[dict[str, object]] = []
    flags = Counter()
    for component in sbom.get("components", []):
        properties = _properties(component)
        licenses = _licenses(component)
        scope = properties.get("mentorflow:dependency-scope", "UNKNOWN")
        maintenance = "DEPRECATED_DECLARED" if (str(component.get("name")), str(component.get("version"))) in deprecated else "UNKNOWN"
        risk_flags: list[str] = []
        if not licenses:
            risk_flags.append("UNKNOWN_LICENSE")
        if any(marker in license_value.upper() for marker in COPYLEFT_MARKERS for license_value in licenses):
            risk_flags.append("COPYLEFT_REVIEW_CANDIDATE")
        if maintenance == "DEPRECATED_DECLARED":
            risk_flags.append("DEPRECATED_PACKAGE")
        if scope == "direct-runtime":
            risk_flags.append("CRITICAL_RUNTIME_DEPENDENCY")
        flags.update(risk_flags)
        entries.append(
            {
                "package": component.get("name"),
                "version": component.get("version"),
                "purl": component.get("purl"),
                "ecosystem": properties.get("mentorflow:ecosystem", "UNKNOWN"),
                "dependency_scope": scope,
                "source": properties.get("mentorflow:source", "UNKNOWN"),
                "licenses": licenses,
                "known_vulnerabilities": None,
                "vulnerability_status": "PENDING_SECURITY_SCAN",
                "maintenance_status": maintenance,
                "risk_flags": sorted(risk_flags),
            }
        )
    document = {
        "schema": "mentorflow.enterprise-dependency-risk-report.v1",
        "status": "PARTIAL",
        "legal_audit": False,
        "license_compliant": None,
        "source_sbom_sha256": canonical_json_sha256(sbom),
        "source_sbom_hash_algorithm": CANONICAL_JSON_HASH_ALGORITHM,
        "vulnerability_gate": "PENDING_SECURITY_SCAN",
        "summary": {
            "components": len(entries),
            "unknown_license": flags["UNKNOWN_LICENSE"],
            "copyleft_review_candidates": flags["COPYLEFT_REVIEW_CANDIDATE"],
            "deprecated_declared": flags["DEPRECATED_PACKAGE"],
            "critical_runtime_dependencies": flags["CRITICAL_RUNTIME_DEPENDENCY"],
        },
        "components": sorted(entries, key=lambda item: (str(item["ecosystem"]), str(item["purl"]))),
        "notes": [
            "Known-vulnerability fields remain null until OSV or an equivalent mature scanner runs.",
            "Missing licenses remain UNKNOWN; no license was inferred from package names.",
            "Copyleft markers are review candidates, not a legal compliance decision.",
            "source_sbom_sha256 hashes canonical JSON semantics and is independent of LF/CRLF checkout conversion.",
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic dependency and license risk inventory.")
    parser.add_argument("--sbom", type=Path, default=DEFAULT_SBOM)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render(args.sbom.resolve())
    output = args.output.resolve()
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("dependency risk report is missing or stale")
        print(f"verified dependency risk report: {len(json.loads(rendered)['components'])} components")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

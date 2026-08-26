from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "enterprise" / "sbom.cdx.json"
PYTHON_LOCK = ROOT / "backend" / "requirements.lock.txt"
PYTHON_DIRECT = ROOT / "backend" / "requirements.txt"
NODE_LOCK = ROOT / "frontend" / "package-lock.json"
NODE_MANIFEST = ROOT / "frontend" / "package.json"
PINNED = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^;\s]+)")
DECLARED = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?")


def _normalized(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _python_direct_names() -> set[str]:
    names: set[str] = set()
    for raw in PYTHON_DIRECT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = DECLARED.match(line)
        if match:
            names.add(_normalized(match.group(1)))
    return names


def _pypi_components() -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    direct = _python_direct_names()
    for raw in PYTHON_LOCK.read_text(encoding="utf-8").splitlines():
        match = PINNED.match(raw.strip())
        if not match:
            continue
        name, version = match.groups()
        normalized = _normalized(name)
        scope = "direct-runtime" if normalized in direct else "transitive-runtime"
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{urllib.parse.quote(normalized)}@{urllib.parse.quote(version)}",
                "properties": [
                    {"name": "mentorflow:ecosystem", "value": "python"},
                    {"name": "mentorflow:dependency-scope", "value": scope},
                    {"name": "mentorflow:source", "value": "backend/requirements.lock.txt"},
                    {"name": "mentorflow:license-evidence", "value": "unknown-in-lockfile"},
                ],
            }
        )
    return components


def _npm_components() -> list[dict[str, object]]:
    payload = json.loads(NODE_LOCK.read_text(encoding="utf-8"))
    manifest = json.loads(NODE_MANIFEST.read_text(encoding="utf-8"))
    runtime_direct = set(manifest.get("dependencies", {}))
    development_direct = set(manifest.get("devDependencies", {}))
    components: list[dict[str, object]] = []
    for location, item in sorted(payload.get("packages", {}).items()):
        if not location or "node_modules/" not in location:
            continue
        name = location.rsplit("node_modules/", 1)[-1]
        version = str(item.get("version") or "")
        if not version:
            continue
        if name in runtime_direct:
            scope = "direct-runtime"
        elif name in development_direct:
            scope = "direct-development"
        elif item.get("dev"):
            scope = "transitive-development"
        else:
            scope = "transitive-runtime"
        component: dict[str, object] = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:npm/{urllib.parse.quote(name, safe='')}@{urllib.parse.quote(version)}",
            "properties": [
                {"name": "mentorflow:ecosystem", "value": "npm"},
                {"name": "mentorflow:development", "value": str(bool(item.get("dev"))).lower()},
                {"name": "mentorflow:dependency-scope", "value": scope},
                {"name": "mentorflow:source", "value": "frontend/package-lock.json"},
                {
                    "name": "mentorflow:license-evidence",
                    "value": "declared-in-lockfile" if item.get("license") else "unknown-in-lockfile",
                },
            ],
        }
        if item.get("license"):
            component["licenses"] = [{"license": {"name": str(item["license"])}}]
        components.append(component)
    return components


def _render() -> str:
    components = sorted(
        _pypi_components() + _npm_components(),
        key=lambda item: (str(item["purl"]), str(item["name"])),
    )
    identity = "\n".join(str(item["purl"]) for item in components)
    serial = uuid.uuid5(uuid.NAMESPACE_URL, "https://mentorflow.local/sbom\n" + identity)
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "timestamp": "1970-01-01T00:00:00Z",
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "mentorflow-enterprise-sbom-generator",
                        "version": "1",
                    }
                ]
            },
            "component": {"type": "application", "name": "MentorFlow", "version": "2.0.0"},
            "properties": [
                {"name": "mentorflow:evidence-scope", "value": "declared-lockfiles"},
                {
                    "name": "mentorflow:limitation",
                    "value": "CI image SBOM remains authoritative for installed transitive/runtime contents",
                },
                {"name": "mentorflow:container-sbom-seam", "value": "CI_CONFIGURED"},
                {"name": "mentorflow:container-targets", "value": "backend,frontend,document-worker"},
            ],
        },
        "components": components,
    }
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic lockfile CycloneDX SBOM.")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    rendered = _render()
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("SBOM is missing or stale; regenerate and review the lockfile delta")
        print(f"verified {len(json.loads(rendered)['components'])} SBOM components")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    print(f"wrote {output} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import csv
import hashlib
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "enterprise" / "asvs-control-matrix.md"
SOURCE_SHA256 = "638a5775109804623e301fda4cc97280e9f1ace8863a7ead2b5d558d1f4a761e"
ALLOWED = {"IMPLEMENTED", "TESTED", "PARTIAL", "NOT_APPLICABLE", "PLANNED", "BLOCKED"}
OWNER_BY_CHAPTER = {
    "V1": "07-security + module owner",
    "V2": "module owner",
    "V3": "frontend owner",
    "V4": "API owner + 04-authorization",
    "V5": "05-documents-workflows",
    "V6": "04-identity-authorization",
    "V7": "04-identity-authorization",
    "V8": "02-tenancy-db + 04-identity-authorization",
    "V9": "04-identity-authorization",
    "V10": "04-identity-authorization",
    "V11": "07-security + platform owner",
    "V12": "07-security + platform owner",
    "V13": "07-security-release",
    "V14": "02-tenancy-db + 03-observability-recovery",
    "V15": "07-security-release",
    "V16": "03-observability-recovery + 07-security",
    "V17": "PENDING_OWNER_ASSIGNMENT",
}

# status, evidence, test, owner, notes
DECISIONS = {
    "V1.2.4": (
        "PARTIAL",
        "ORM and parameterized-query patterns exist; repository-wide injection evidence is incomplete.",
        "backend/tests/test_local_migration.py; Semgrep PENDING_CI",
        "module owner + 07-security",
        "Do not promote to TESTED without query inventory and mature SAST output.",
    ),
    "V5.1.1": (
        "PARTIAL",
        "DocumentSourcePolicy defines file, batch, size, and archive limits.",
        "backend/tests/test_document_source_v1.py",
        "05-documents-workflows",
        "End-user malicious-file handling owner evidence is pending.",
    ),
    "V5.2.3": (
        "TESTED",
        "Archive size, expansion ratio, and member count rejection have executable tests.",
        "backend/tests/test_local_migration.py",
        "05-documents-workflows",
        "Offline test evidence only; Linux staging remains pending.",
    ),
    "V5.3.2": (
        "TESTED",
        "Trusted-path, traversal, link, and outside-write denial tests exist.",
        "backend/tests/test_archive_import_security.py; backend/tests/test_local_migration.py",
        "05-documents-workflows",
        "Verified by the focused offline security corpus.",
    ),
    "V5.3.3": (
        "TESTED",
        "Archive member paths and zip-slip defenses have regression tests.",
        "backend/tests/test_archive_import_security.py; backend/tests/test_local_migration.py",
        "05-documents-workflows",
        "Verified by the focused offline security corpus.",
    ),
    "V8.4.1": (
        "BLOCKED",
        "Frozen tenant context and a three-organization foreign-ID load corpus exist; integrated RLS/server evidence does not.",
        "tests/load/tenant-isolation/scenario.js; PostgreSQL RLS STAGING_EXECUTION_PENDING",
        "02-tenancy-db + 04-identity-authorization",
        "REAL_TENANT_LOAD_GATE = PENDING_STAGING; unauthorized_success_count target is zero.",
    ),
    "V13.3.1": (
        "PARTIAL",
        "CI secret contexts, Gitleaks rules, and expiring exact-finding waiver contract are configured.",
        ".github/workflows/enterprise-security.yml; scripts/enterprise/verify_security_waivers.py",
        "07-security-release",
        "Vault/HSM creation, rotation, and destruction evidence is PENDING_OWNER_EVIDENCE.",
    ),
    "V15.1.2": (
        "PARTIAL",
        "CycloneDX Python/Node lockfile SBOM and three-image SBOM seams are configured.",
        "scripts/enterprise/generate_sbom.py; container-release workflow",
        "07-security-release",
        "Installed container SBOMs require CI execution.",
    ),
    "V16.5.1": (
        "PARTIAL",
        "Security redaction regression tests exist; full endpoint/error-path coverage is not established.",
        "backend/tests/test_provider_runtime_v2_security.py; backend/tests_w6/test_data_security_recovery.py",
        "03-observability-recovery + 07-security",
        "PENDING_OWNER_EVIDENCE for integrated logging/error policy.",
    ),
}


def _cell(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def generate(source: Path, output: Path = OUTPUT) -> Counter[str]:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise RuntimeError(f"unexpected ASVS source hash: {digest}")
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 345 or len({row["req_id"] for row in rows}) != 345:
        raise RuntimeError("OWASP ASVS 5.0.0 source must contain exactly 345 unique controls")
    rendered: list[str] = [
        "# OWASP ASVS 5.0.0 control matrix",
        "",
        "Status: **PARTIAL**. This is a complete 345-control inventory, not an all-green certification. Missing owner evidence remains `PENDING_OWNER_EVIDENCE`; live/scanner/tenant execution remains `STAGING_EXECUTION_PENDING`.",
        "",
        f"Source: OWASP ASVS 5.0.0 English CSV, SHA-256 `{SOURCE_SHA256}`.",
        "",
        "| ASVS_ID | CONTROL | STATUS | EVIDENCE | TEST | OWNER | NOTES |",
        "|---|---|---|---|---|---|---|",
    ]
    counts: Counter[str] = Counter()
    for row in rows:
        control_id = row["req_id"]
        decision = DECISIONS.get(control_id)
        if decision:
            status, evidence, test, owner, notes = decision
        else:
            status = "PLANNED"
            evidence = "PENDING_OWNER_EVIDENCE"
            test = "NOT_MAPPED"
            owner = OWNER_BY_CHAPTER.get(row["chapter_id"], "PENDING_OWNER_ASSIGNMENT")
            notes = "Control-specific implementation and test evidence has not been received by E1-07B."
        if status not in ALLOWED:
            raise RuntimeError(f"invalid status for {control_id}: {status}")
        counts[status] += 1
        control = f"L{row['L']} {row['chapter_id']} {row['chapter_name']} / {row['section_id']} {row['section_name']}: {row['req_description']}"
        rendered.append(
            f"| {control_id} | {_cell(control)} | {status} | {_cell(evidence)} | {_cell(test)} | {_cell(owner)} | {_cell(notes)} |"
        )
    summary = ", ".join(f"{key}={counts.get(key, 0)}" for key in sorted(ALLOWED))
    rendered[5:5] = [f"Status counts: {summary}.", ""]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(rendered) + "\n", encoding="utf-8", newline="\n")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the full OWASP ASVS 5.0.0 evidence matrix.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    counts = generate(args.source.resolve(), args.output.resolve())
    print(dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

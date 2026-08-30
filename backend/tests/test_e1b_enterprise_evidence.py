from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "enterprise"
sys.path.insert(0, str(SCRIPTS))

import generate_readiness_snapshot as readiness_module  # noqa: E402
from generate_readiness_snapshot import (  # noqa: E402
    canonical_json_sha256 as readiness_canonical_json_sha256,
    generate as generate_readiness,
    generate_from_git as generate_readiness_from_git,
    resolve_source_identity,
    verify_remote_reachability,
    verify_snapshot_file,
)
from generate_dependency_risk_report import (  # noqa: E402
    CANONICAL_JSON_HASH_ALGORITHM,
    canonical_json_sha256,
    render as render_dependency_risk,
)
from verify_release_assets import ENVIRONMENT_NOT_RUN_FACTS, _verify_sbom_and_risk  # noqa: E402
from verify_security_waivers import evaluate as evaluate_security, load_waivers  # noqa: E402


def test_workflow_schema_and_fast_full_lane_contracts() -> None:
    workflow_root = ROOT / ".github" / "workflows"
    parsed = {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in workflow_root.glob("*.yml")
    }
    assert all(isinstance(value, dict) for value in parsed.values())
    assert "enterprise-pr-fast.yml" in parsed
    assert "enterprise-main-full.yml" in parsed
    reusable_payload = parsed["enterprise-ci.yml"]
    jobs = reusable_payload["jobs"]
    for job_name in ("backend-fast", "backend-full", "enterprise-contract"):
        checkout = jobs[job_name]["steps"][0]
        assert checkout["uses"] == "actions/checkout@v4"
        assert checkout["with"] == {"fetch-depth": 0, "fetch-tags": True}

    migration_steps = jobs["migrations"]["steps"]
    migration_setup = next(
        step for step in migration_steps if step.get("uses") == "actions/setup-python@v5"
    )
    assert set(migration_setup["with"]["cache-dependency-path"].splitlines()) == {
        "backend/requirements.txt",
        "backend/requirements-desktop.txt",
    }
    assert any(
        step.get("run") == "python -m pip install -r backend/requirements-desktop.txt"
        for step in migration_steps
    )

    rls_step = next(
        step
        for step in jobs["rls"]["steps"]
        if step.get("name") == "Require integrated upstream RLS tests"
    )
    assert rls_step["run"] == (
        "python -m pytest -q backend/tests/test_enterprise_tenant_persistence.py -m rls"
    )
    assert rls_step["env"]["MENTORFLOW_RLS_DSN"].startswith("postgresql://")
    assert "|| true" not in rls_step["run"]

    reusable = (workflow_root / "enterprise-ci.yml").read_text(encoding="utf-8")
    assert "PR_FAST" in reusable and "MAIN_FULL" in reusable
    assert 'python: ["3.11", "3.12", "3.13"]' in reusable
    assert 'node-version: "22.20.0"' in reusable
    assert "python -m tabnanny" in reusable
    pr_fast = (workflow_root / "enterprise-pr-fast.yml").read_text(encoding="utf-8")
    assert "ENTERPRISE_SECURITY_SCANNER_LEASE" in pr_fast
    assert "gitleaks/gitleaks-action@v3" in pr_fast


def test_asvs_and_threat_model_are_field_complete() -> None:
    asvs = (ROOT / "docs" / "enterprise" / "asvs-control-matrix.md").read_text(encoding="utf-8")
    assert "| ASVS_ID | CONTROL | STATUS | EVIDENCE | TEST | OWNER | NOTES |" in asvs
    rows = [line for line in asvs.splitlines() if line.startswith("| V")]
    assert len(rows) == 345
    assert len({line.split(" | ", 1)[0].removeprefix("| ") for line in rows}) == 345
    allowed = {"IMPLEMENTED", "TESTED", "PARTIAL", "NOT_APPLICABLE", "PLANNED", "BLOCKED"}
    assert all(any(f" | {status} | " in line for status in allowed) for line in rows)

    threat = (ROOT / "docs" / "enterprise" / "threat-model.md").read_text(encoding="utf-8")
    threat_rows = [line for line in threat.splitlines() if line.startswith("| TM-")]
    assert [line.split(" | ", 1)[0].removeprefix("| ") for line in threat_rows] == [
        f"TM-{index:02d}" for index in range(1, 17)
    ]
    assert all(len(line.split(" | ")) == 7 for line in threat_rows)


def test_sbom_risk_report_and_tenant_corpus_are_deterministic() -> None:
    subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_sbom.py"), "--check", "--output", "reports/enterprise/machine/lockfile-sbom.cdx.json"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_dependency_risk_report.py"), "--check"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_tenant_load_corpus.py"), "--check"],
        cwd=ROOT,
        check=True,
    )
    sbom = json.loads((ROOT / "reports" / "enterprise" / "machine" / "lockfile-sbom.cdx.json").read_text(encoding="utf-8"))
    component_count = len(sbom["components"])
    assert component_count > 100
    assert all(
        {"mentorflow:dependency-scope", "mentorflow:source", "mentorflow:license-evidence"}.issubset(
            {item["name"] for item in component["properties"]}
        )
        for component in sbom["components"]
    )
    python_scopes = {
        item["value"]
        for component in sbom["components"]
        if component["purl"].startswith("pkg:pypi/")
        for item in component["properties"]
        if item["name"] == "mentorflow:dependency-scope"
    }
    assert python_scopes == {"direct-runtime", "transitive-runtime"}
    risk = json.loads((ROOT / "reports" / "enterprise" / "machine" / "dependency-risk-report.json").read_text(encoding="utf-8"))
    assert risk["legal_audit"] is False
    assert risk["license_compliant"] is None
    assert risk["vulnerability_gate"] == "PENDING_SECURITY_SCAN"
    assert risk["source_sbom_hash_algorithm"] == CANONICAL_JSON_HASH_ALGORITHM
    assert risk["summary"]["components"] == component_count
    assert len(risk["components"]) == component_count
    assert risk["summary"]["unknown_license"] > 0

    corpus = json.loads((ROOT / "tests" / "load" / "fixtures" / "tenant-corpus.v1.json").read_text(encoding="utf-8"))
    assert len(corpus["organizations"]) == 3
    assert sum(len(item["users"]) for item in corpus["organizations"]) == 21
    assert all(len(item["workspace_ids"]) >= 2 for item in corpus["organizations"])
    assert set(corpus["requirements"]["foreign_resource_types"]) == {
        "document", "knowledge_map", "plan", "progress", "review", "cost", "audit",
    }
    assert corpus["contains_credentials"] is False


def test_sbom_canonical_source_hash_is_lf_crlf_checkout_invariant(tmp_path: Path) -> None:
    source = ROOT / "reports" / "enterprise" / "machine" / "lockfile-sbom.cdx.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    lf = tmp_path / "sbom-lf.json"
    crlf = tmp_path / "sbom-crlf.json"
    lf.write_bytes(rendered.encode("utf-8"))
    crlf.write_bytes(rendered.replace("\n", "\r\n").encode("utf-8"))

    lf_risk = json.loads(render_dependency_risk(lf))
    crlf_risk = json.loads(render_dependency_risk(crlf))
    expected = canonical_json_sha256(payload)
    assert lf_risk["source_sbom_sha256"] == expected
    assert crlf_risk["source_sbom_sha256"] == expected
    assert lf_risk["source_sbom_hash_algorithm"] == CANONICAL_JSON_HASH_ALGORITHM


def test_sbom_semantic_tamper_still_fails_closed_with_canonical_hash(tmp_path: Path) -> None:
    machine = tmp_path / "reports" / "enterprise" / "machine"
    machine.mkdir(parents=True)
    sbom_source = ROOT / "reports" / "enterprise" / "machine" / "lockfile-sbom.cdx.json"
    risk_source = ROOT / "reports" / "enterprise" / "machine" / "dependency-risk-report.json"
    sbom = json.loads(sbom_source.read_text(encoding="utf-8"))
    component_count = len(sbom["components"])
    crlf = (json.dumps(sbom, ensure_ascii=False, indent=2, sort_keys=True) + "\n").replace("\n", "\r\n")
    (machine / "lockfile-sbom.cdx.json").write_bytes(crlf.encode("utf-8"))
    (machine / "dependency-risk-report.json").write_bytes(risk_source.read_bytes())
    assert _verify_sbom_and_risk(tmp_path) == (component_count, component_count)

    sbom["components"][0]["version"] = "semantic-tamper"
    tampered = (json.dumps(sbom, ensure_ascii=False, indent=2, sort_keys=True) + "\n").replace("\n", "\r\n")
    (machine / "lockfile-sbom.cdx.json").write_bytes(tampered.encode("utf-8"))
    with pytest.raises(RuntimeError, match="stale relative to the SBOM"):
        _verify_sbom_and_risk(tmp_path)


def test_k6_static_syntax_thresholds_and_async_metric() -> None:
    node = shutil.which("node")
    if node:
        for path in sorted((ROOT / "tests" / "load").rglob("*.js")):
            subprocess.run([node, "--check", str(path)], cwd=ROOT, check=True)
    thresholds = (ROOT / "tests" / "load" / "lib" / "thresholds.js").read_text(encoding="utf-8")
    core = (ROOT / "tests" / "load" / "lib" / "core.js").read_text(encoding="utf-8")
    assert "BASELINE_REQUIRED" in thresholds
    assert 'http_req_failed: ["rate<0.01"]' in thresholds
    assert 'unauthorized_success_count: ["count==0"]' in thresholds
    assert "document_job_completion_ms" in thresholds and "new Trend" in core
    assert "AI_TEACHER_PATH" in core and "PROVIDER_ADAPTER=fake" in core
    assert "Math.random()" in core


def test_readiness_snapshot_has_exact_domains_and_cannot_hide_blockers(tmp_path: Path) -> None:
    source = ROOT / "reports" / "enterprise" / "readiness-evidence.json"
    snapshot = generate_readiness(source, source_commit="1" * 40, source_tree="2" * 40)
    assert snapshot["score"] is None
    assert snapshot["overall_status"] == "PARTIAL"
    assert len(snapshot["domains"]) == 12

    tampered = json.loads(source.read_text(encoding="utf-8"))
    tampered["domains"]["IDENTITY"]["status"] = "PASS"
    invalid = tmp_path / "readiness.json"
    invalid.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot PASS"):
        generate_readiness(invalid, source_commit="1" * 40, source_tree="2" * 40)


def test_readiness_provenance_is_regenerated_from_current_canonical_git_truth() -> None:
    source_commit = "8599e8fbbc13f08e35dba761d31eaa3fb988ca3f"
    source_tree = "0b7a47303784d6d7d8fb142823e279e8b137e0c7"
    expected_sha256 = "d8fc494e827a000f74ab6ed74aa46b65e297903a65c16890cd7b4472e039067a"
    source = ROOT / "reports" / "enterprise" / "readiness-evidence.json"
    snapshot_path = ROOT / "reports" / "enterprise" / "machine" / "readiness-snapshot.json"

    regenerated = generate_readiness_from_git(
        ROOT,
        source,
        source_commit=source_commit,
        source_tree=source_tree,
    )
    assert regenerated["source_commit"] == source_commit
    assert regenerated["source_tree"] == source_tree
    assert regenerated["overall_status"] == "PARTIAL"
    assert regenerated["score"] is None
    assert verify_snapshot_file(
        snapshot_path,
        regenerated,
        expected_sha256=expected_sha256,
    ) == expected_sha256
    assert readiness_canonical_json_sha256(regenerated) == expected_sha256

    workflow = (ROOT / ".github" / "workflows" / "enterprise-ci.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 0" in workflow
    assert "--provenance-remote origin" in workflow
    assert "--provenance-ref refs/heads/checkpoint/mentorflow-e1s1-20260829" in workflow
    assert '--provenance-ref "refs/tags/mentorflow-e1s1-checkpoint-20260829^{}"' in workflow
    assert f"--expected-snapshot-sha256 {expected_sha256}" in workflow
    assert "ls-remote" in (SCRIPTS / "generate_readiness_snapshot.py").read_text(encoding="utf-8")


def test_readiness_provenance_rejects_wrong_commit_and_tree() -> None:
    source_commit = "8599e8fbbc13f08e35dba761d31eaa3fb988ca3f"
    source_tree = "0b7a47303784d6d7d8fb142823e279e8b137e0c7"
    with pytest.raises(RuntimeError, match="source tree mismatch"):
        resolve_source_identity(ROOT, source_commit=source_commit, source_tree="0" * 40)
    with pytest.raises(RuntimeError, match="git cat-file"):
        resolve_source_identity(ROOT, source_commit="0" * 40, source_tree=source_tree)


def test_readiness_provenance_rejects_semantic_and_hash_tamper(tmp_path: Path) -> None:
    snapshot = json.loads(
        (ROOT / "reports" / "enterprise" / "machine" / "readiness-snapshot.json").read_text(encoding="utf-8")
    )
    tampered = json.loads(json.dumps(snapshot))
    tampered["domains"]["RELEASE"]["blockers"] = []
    path = tmp_path / "readiness-snapshot.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(RuntimeError, match="semantically tampered"):
        verify_snapshot_file(path, snapshot)

    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(RuntimeError, match="canonical hash mismatch"):
        verify_snapshot_file(path, snapshot, expected_sha256="0" * 64)


def test_readiness_provenance_rejects_old_unreachable_ref_and_stale_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = "8599e8fbbc13f08e35dba761d31eaa3fb988ca3f"
    old = "4d61b3547a8d0865a53634abd549be004c904ce1"
    branch = "refs/heads/checkpoint/mentorflow-e1s1-20260829"
    tag = "refs/tags/mentorflow-e1s1-checkpoint-20260829^{}"
    monkeypatch.setattr(
        readiness_module,
        "_git_output",
        lambda _root, *_args: f"{current}\t{branch}\n{current}\t{tag}",
    )
    with pytest.raises(RuntimeError, match="not exact-reachable"):
        verify_remote_reachability(
            ROOT,
            source_commit=old,
            remote="github",
            refs=(branch, tag),
        )

    monkeypatch.undo()
    real_run = readiness_module.subprocess.run

    def reject_lineage(command: list[str], *args: object, **kwargs: object) -> object:
        if command[:3] == ["git", "merge-base", "--is-ancestor"]:
            return SimpleNamespace(returncode=1)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(readiness_module.subprocess, "run", reject_lineage)
    with pytest.raises(RuntimeError, match="not an ancestor"):
        resolve_source_identity(
            ROOT,
            source_commit=current,
            source_tree="0b7a47303784d6d7d8fb142823e279e8b137e0c7",
        )


def test_environment_truth_preserves_six_explicit_not_run_facts() -> None:
    environment = json.loads(
        (ROOT / "reports" / "enterprise" / "machine" / "environment-truth.json").read_text(encoding="utf-8")
    )
    expected = {name: "NOT_RUN" for name in ENVIRONMENT_NOT_RUN_FACTS}
    assert environment["gates"] == expected

    evidence_pack = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "docs" / "enterprise").glob("*.md")
    )
    assert all(f"{name} = NOT_RUN" in evidence_pack for name in ENVIRONMENT_NOT_RUN_FACTS)


def test_security_waiver_requires_exact_finding_owner_reason_and_future_expiry(tmp_path: Path) -> None:
    valid = {
        "schema": "mentorflow.enterprise-security-waivers.v1",
        "waivers": [
            {
                "id": "ESW-2026-001",
                "scanner": "osv",
                "finding": "GHSA-example-0001",
                "scope": "backend/requirements.lock.txt",
                "reason": "Temporary transitive issue with a reviewed mitigation.",
                "owner": "security-team",
                "expires_on": "2026-09-30",
            }
        ],
    }
    path = tmp_path / "waivers.json"
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert len(load_waivers(path, today=date(2026, 8, 26))) == 1
    with pytest.raises(RuntimeError, match="expired"):
        load_waivers(path, today=date(2026, 10, 1))
    valid["waivers"][0]["finding"] = "*"
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(RuntimeError, match="exact finding"):
        load_waivers(path, today=date(2026, 8, 26))


def test_security_gate_fails_unwaived_mature_scanner_findings_and_accepts_exact_waivers(tmp_path: Path) -> None:
    osv = tmp_path / "osv.json"
    semgrep = tmp_path / "semgrep.json"
    npm = tmp_path / "npm.json"
    osv.write_text(json.dumps({"results": [{"packages": [{"vulnerabilities": [{"id": "GHSA-example"}]}]}]}), encoding="utf-8")
    semgrep.write_text(json.dumps({"results": [{"check_id": "python.security.example", "extra": {"severity": "ERROR"}}]}), encoding="utf-8")
    npm.write_text(json.dumps({"vulnerabilities": {"danger-package": {"severity": "critical"}}}), encoding="utf-8")
    waivers = tmp_path / "waivers.json"
    waivers.write_text(json.dumps({"schema": "mentorflow.enterprise-security-waivers.v1", "waivers": []}), encoding="utf-8")

    failed = evaluate_security(waivers, osv_paths=[osv], semgrep_paths=[semgrep], npm_paths=[npm], today=date(2026, 8, 26))
    assert failed["status"] == "FAIL"
    assert failed["unwaived"] == [
        "npm-audit:danger-package", "osv:GHSA-example", "semgrep:python.security.example",
    ]

    exact = []
    for index, (scanner, finding) in enumerate(
        (("osv", "GHSA-example"), ("semgrep", "python.security.example"), ("npm-audit", "danger-package")),
        start=1,
    ):
        exact.append(
            {
                "id": f"ESW-2026-{index:03d}",
                "scanner": scanner,
                "finding": finding,
                "scope": "locked dependency or exact rule",
                "reason": "Reviewed temporary mitigation with a bounded expiry.",
                "owner": "security-team",
                "expires_on": "2026-09-30",
            }
        )
    waivers.write_text(json.dumps({"schema": "mentorflow.enterprise-security-waivers.v1", "waivers": exact}), encoding="utf-8")
    passed = evaluate_security(waivers, osv_paths=[osv], semgrep_paths=[semgrep], npm_paths=[npm], today=date(2026, 8, 26))
    assert passed["status"] == "PASS"
    assert len(passed["waived"]) == 3 and passed["unwaived"] == []


def test_release_and_readiness_schemas_expose_named_contracts() -> None:
    release = json.loads((ROOT / "contracts" / "enterprise" / "release-contracts.schema.json").read_text(encoding="utf-8"))
    assert set(release["$defs"]) == {"ImageVersion", "ImageManifest", "EnterpriseReleaseManifest"}
    readiness = json.loads((ROOT / "contracts" / "enterprise" / "readiness-snapshot.schema.json").read_text(encoding="utf-8"))
    assert readiness["title"] == "EnterpriseReadinessSnapshot"
    assert readiness["properties"]["score"] == {"type": "null"}

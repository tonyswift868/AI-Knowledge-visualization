from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import yaml

from generate_dependency_risk_report import CANONICAL_JSON_HASH_ALGORITHM, canonical_json_sha256
from generate_readiness_snapshot import (
    generate_from_git as generate_readiness_from_git,
    verify_remote_reachability,
    verify_snapshot_file,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    "enterprise-ci.yml",
    "enterprise-pr-fast.yml",
    "enterprise-main-full.yml",
    "enterprise-security.yml",
    "enterprise-load.yml",
    "container-release.yml",
    "staging-release.yml",
)
EVIDENCE_DOCS = (
    "architecture.md", "deployment-topology.md", "identity-tenancy.md", "authorization-matrix.md",
    "data-classification.md", "threat-model.md", "asvs-control-matrix.md", "observability-slo.md",
    "load-test-report.md", "tenant-isolation-report.md", "backup-restore-report.md", "ai-evaluation-report.md",
    "cost-capacity-report.md", "incident-runbook.md", "release-rollback.md", "dependency-risk-report.md",
    "digitalocean-staging-runbook.md", "INTERVIEW_ENGINEERING_EVIDENCE.md",
)
PENDING_REPORTS = (
    "dependency-scan.pending.json", "secret-scan.pending.json", "sast.pending.json",
    "container-build.pending.json", "load-test.pending.json", "release.pending.json",
)
LOAD_PROFILES = ("smoke", "average", "stress", "spike", "soak", "tenant-isolation")
RESOURCE_TYPES = {"document", "knowledge_map", "plan", "progress", "review", "cost", "audit"}
READINESS_DOMAINS = {
    "IDENTITY", "TENANCY", "AUTHORIZATION", "DATABASE", "WORKFLOWS", "STAGING",
    "OBSERVABILITY", "BACKUP_RESTORE", "SECURITY", "LOAD", "RELEASE", "AI_QUALITY",
}
ENVIRONMENT_NOT_RUN_FACTS = (
    "LOCAL_WINDOWS_DOCKER",
    "LOCAL_WINDOWS_COMPOSE",
    "REAL_POSTGRES_RLS",
    "REAL_K6",
    "REAL_CONTAINER",
    "REAL_LINUX_STAGING",
)
ASVS_ROW = re.compile(
    r"^\| (V\d+\.\d+\.\d+) \| (.+) \| (IMPLEMENTED|TESTED|PARTIAL|NOT_APPLICABLE|PLANNED|BLOCKED) \| (.*) \| (.*) \| (.*) \| (.*) \|$"
)
THREAT_ROW = re.compile(r"^\| (TM-\d{2}) \|")
PINNED_ACTION = re.compile(r"[^@\s]+@[0-9a-f]{40}")
BACKEND_TEST_ROOTS = ("backend/tests", "backend/tests_w2", "backend/tests_w6")
DOCUMENT_ML_TESTS = {
    "backend/tests/test_chunked_adamw.py",
    "backend/tests/test_document_ingestion_windows_compat.py",
    "backend/tests/test_document_source_v1.py",
    "backend/tests/test_qwen3_tts_warm_start.py",
    "backend/tests/test_streaming_safetensors.py",
}
DESKTOP_TESTS = {
    "backend/tests/test_classroom_plan_landing.py",
    "backend/tests/test_classroom_visual.py",
    "backend/tests/test_knowledge_map_progress.py",
    "backend/tests/test_life_analogy_prefetch_buffer.py",
    "backend/tests/test_local_migration.py",
    "backend/tests/test_native_desktop_knowledge_map.py",
    "backend/tests/test_plan_time_metrics.py",
    "backend/tests/test_plans_page_scroll_layout.py",
}
RELEASE_IMAGE_ROLES = ("backend", "frontend", "document-worker", "worker")
RELEASE_REQUIRED_JOBS = ("source-contract", "quality", "scanner-lease", "build", "scan", "publish")


def _require(text: str, values: tuple[str, ...], source: str) -> None:
    missing = [value for value in values if value not in text]
    if missing:
        raise RuntimeError(f"{source} missing required tokens: {missing}")


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be an object: {path.relative_to(ROOT)}")
    return value


def _job_needs(job: dict[str, object]) -> set[str]:
    value = job.get("needs", [])
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    raise RuntimeError("workflow job needs must be a string or list of strings")


def _steps(job: dict[str, object], source: str) -> list[dict[str, object]]:
    value = job.get("steps")
    if not isinstance(value, list) or not all(isinstance(step, dict) for step in value):
        raise RuntimeError(f"{source} must declare structured steps")
    return value


def _verify_action_pins(workflow: dict[str, object], source: str) -> None:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        raise RuntimeError(f"{source} must declare jobs")
    for job_name, raw_job in jobs.items():
        if not isinstance(raw_job, dict):
            raise RuntimeError(f"{source} job is not a mapping: {job_name}")
        uses_values = [raw_job.get("uses")]
        raw_steps = raw_job.get("steps", [])
        if not isinstance(raw_steps, list):
            raise RuntimeError(f"{source} steps are not a list: {job_name}")
        uses_values.extend(step.get("uses") for step in raw_steps if isinstance(step, dict))
        for uses in uses_values:
            if uses is None or (isinstance(uses, str) and uses.startswith("./")):
                continue
            if not isinstance(uses, str) or not PINNED_ACTION.fullmatch(uses):
                raise RuntimeError(f"{source} has an unpinned external action in {job_name}: {uses}")


def _run_for_named_step(job: dict[str, object], name: str, source: str) -> str:
    matches = [step.get("run") for step in _steps(job, source) if step.get("name") == name]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise RuntimeError(f"{source} must declare exactly one executable step named {name}")
    return matches[0]


def _test_paths(command: str) -> set[str]:
    return set(re.findall(r"backend/(?:tests|tests_w2|tests_w6)/test_[A-Za-z0-9_]+\.py", command))


def _verify_backend_suite_membership(root: Path, workflow: dict[str, object]) -> None:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        raise RuntimeError("enterprise-ci.yml jobs are missing")
    names = ("backend-full", "backend-document-ml", "backend-desktop")
    if any(name not in jobs or not isinstance(jobs[name], dict) for name in names):
        raise RuntimeError("MAIN_FULL must declare required core, document/ML, and desktop jobs")
    core_job = jobs["backend-full"]
    document_job = jobs["backend-document-ml"]
    desktop_job = jobs["backend-desktop"]
    for name, job in (("backend-full", core_job), ("backend-document-ml", document_job), ("backend-desktop", desktop_job)):
        if job.get("if") != "inputs.lane == 'MAIN_FULL'" or _job_needs(job) != {"lane-contract"}:
            raise RuntimeError(f"{name} must be a required MAIN_FULL job behind only the lane contract")
        if job.get("continue-on-error") is not None:
            raise RuntimeError(f"{name} must not mask a required suite result")

    core_run = _run_for_named_step(
        core_job, "Complete core backend regression excluding explicit optional suites", "backend-full"
    )
    document_run = _run_for_named_step(document_job, "Required document and ML suite", "backend-document-ml")
    desktop_run = _run_for_named_step(desktop_job, "Required desktop suite", "backend-desktop")
    if not all(root_name in core_run for root_name in BACKEND_TEST_ROOTS):
        raise RuntimeError("core backend suite must start from all three canonical test roots")
    ignored = set(re.findall(r"--ignore=(backend/(?:tests|tests_w2|tests_w6)/test_[A-Za-z0-9_]+\.py)", core_run))
    if ignored != DOCUMENT_ML_TESTS | DESKTOP_TESTS:
        raise RuntimeError("core suite exclusions must exactly match the two required optional suites")
    if _test_paths(document_run) != DOCUMENT_ML_TESTS:
        raise RuntimeError("document/ML suite membership is incomplete or ambiguous")
    if _test_paths(desktop_run) != DESKTOP_TESTS:
        raise RuntimeError("desktop suite membership is incomplete or ambiguous")
    if "backend/requirements-documents.txt" not in str(document_job):
        raise RuntimeError("document/ML suite must install its canonical optional manifest")
    if "backend/requirements-desktop.txt" not in str(desktop_job) or desktop_job.get("runs-on") != "windows-latest":
        raise RuntimeError("desktop suite must install its canonical manifest on Windows")

    all_tests = {
        path.relative_to(root).as_posix()
        for root_name in BACKEND_TEST_ROOTS
        for path in (root / root_name).rglob("test_*.py")
    }
    optional = DOCUMENT_ML_TESTS | DESKTOP_TESTS
    if not DOCUMENT_ML_TESTS or not DESKTOP_TESTS or DOCUMENT_ML_TESTS & DESKTOP_TESTS:
        raise RuntimeError("optional suite definitions must be non-empty and pairwise disjoint")
    if not optional.issubset(all_tests):
        raise RuntimeError("optional suite definition points outside the current backend test universe")
    core_tests = all_tests - ignored
    if not core_tests or core_tests & optional or core_tests | optional != all_tests:
        raise RuntimeError("backend test membership is not complete, disjoint, and exactly-once")


def _verify_required_failure_semantics(workflow: dict[str, object], source: str) -> None:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        raise RuntimeError(f"{source} jobs are missing")
    for job_name, raw_job in jobs.items():
        if not isinstance(raw_job, dict):
            raise RuntimeError(f"{source} job is malformed: {job_name}")
        if raw_job.get("continue-on-error") is not None:
            raise RuntimeError(f"{source} job masks failure: {job_name}")
        for step in raw_job.get("steps", []):
            if not isinstance(step, dict):
                raise RuntimeError(f"{source} step is malformed: {job_name}")
            if step.get("continue-on-error") is not None:
                raise RuntimeError(f"{source} step masks failure: {job_name}")
            run = step.get("run")
            if isinstance(run, str) and ("|| true" in run or re.search(r";\s*exit\s+0(?:\s|$)", run)):
                raise RuntimeError(f"{source} shell masks failure: {job_name}")


def _verify_container_release(workflow: dict[str, object], text: str) -> None:
    trigger = workflow.get("on", workflow.get(True))
    if not isinstance(trigger, dict) or set(trigger) != {"workflow_dispatch"}:
        raise RuntimeError("container release must be an explicit workflow_dispatch-only final release")
    dispatch = trigger["workflow_dispatch"]
    if not isinstance(dispatch, dict) or not isinstance(dispatch.get("inputs"), dict):
        raise RuntimeError("container release dispatch inputs are missing")
    expected_inputs = {
        "expected_source_sha",
        "expected_source_tree",
        *(f"{role.replace('-', '_')}_rollback_image" for role in RELEASE_IMAGE_ROLES),
    }
    if set(dispatch["inputs"]) != expected_inputs:
        raise RuntimeError("container release dispatch must require exact source/tree and four rollback digests")

    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or set(RELEASE_REQUIRED_JOBS) | {"required-gate"} != set(jobs):
        raise RuntimeError("container release required job set is incomplete or ambiguous")
    expected_needs = {
        "source-contract": set(),
        "quality": {"source-contract"},
        "scanner-lease": {"quality"},
        "build": {"source-contract", "quality", "scanner-lease"},
        "scan": {"build", "scanner-lease"},
        "publish": set(RELEASE_REQUIRED_JOBS) - {"publish"},
        "required-gate": set(RELEASE_REQUIRED_JOBS),
    }
    for name, needs in expected_needs.items():
        job = jobs[name]
        if not isinstance(job, dict) or _job_needs(job) != needs:
            raise RuntimeError(f"container release needs graph is not fail-closed for {name}")
        runner = job.get("runs-on")
        if runner is not None and runner != "ubuntu-24.04":
            raise RuntimeError(f"container release runner is not pinned to Ubuntu 24.04: {name}")
    required_gate = jobs["required-gate"]
    if not isinstance(required_gate, dict) or required_gate.get("if") != "always()":
        raise RuntimeError("container release aggregate must inspect every required result even after failure")
    gate_run = "\n".join(
        str(step.get("run", "")) for step in _steps(required_gate, "required-gate")
    )
    if gate_run.count('test "$result" = "success"') != 1:
        raise RuntimeError("container release aggregate must reject skipped, neutral, cancelled, and failed jobs")

    _require(
        text,
        (
            "refs/heads/core-v2/integration",
            "git rev-parse 'HEAD^{tree}'",
            "git ls-remote origin",
            "ENTERPRISE_SECURITY_SCANNER_LEASE",
            "docker buildx imagetools inspect",
            "docker buildx build",
            "--metadata-file",
            "--provenance=mode=max",
            "base-image-digests.json",
            "NODE_BASE_IMAGE=",
            "PYTHON_BASE_IMAGE=",
            "NGINX_BASE_IMAGE=",
            "containerimage.digest",
            "image-sbom.cdx.json",
            "trivy.sarif",
            "repository_digest",
            "final-image-evidence.json",
            "FINAL_RELEASE_VERIFIED",
            "final-release-manifest.json",
            "final-image-digest-lock.json",
            "rp05-application-images.json",
        ),
        "container-release.yml",
    )
    if re.search(r"\bdocker\s+build(?!x)\b", text):
        raise RuntimeError("container release must use Buildx, not legacy docker build")
    if "ubuntu-latest" in text or "continue-on-error" in text:
        raise RuntimeError("container release contains floating runner or failure masking")
    if text.count("for target in backend frontend document-worker worker") < 3:
        raise RuntimeError("build, publish, and rollback contracts must cover all four images")
    if "digest_ref_re='^.+@sha256:[0-9a-f]{64}$'" not in text:
        raise RuntimeError("rollback validation must reject mutable tags and accept only resolved digests")
    _verify_action_pins(workflow, "container-release.yml")
    _verify_required_failure_semantics(workflow, "container-release.yml")


def _verify_asvs(path: Path) -> Counter[str]:
    text = path.read_text(encoding="utf-8")
    _require(text, ("OWASP ASVS 5.0.0", "| ASVS_ID | CONTROL | STATUS | EVIDENCE | TEST | OWNER | NOTES |"), path.name)
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = ASVS_ROW.match(line)
        if match:
            rows.append((match.group(1), match.group(3)))
            if not all(match.group(index).strip() for index in range(2, 8)):
                raise RuntimeError(f"ASVS row has an empty required field: {match.group(1)}")
    if len(rows) != 345 or len({control for control, _ in rows}) != 345:
        raise RuntimeError(f"ASVS matrix must contain 345 unique field-complete controls, found {len(rows)}")
    counts = Counter(status for _, status in rows)
    if counts["TESTED"] == 0 or counts["PLANNED"] == 0 or counts["BLOCKED"] == 0:
        raise RuntimeError("ASVS matrix must preserve truthful TESTED, PLANNED, and BLOCKED states")
    return counts


def _verify_threat_model(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    _require(text, ("THREAT_ID", "ASSET", "ATTACK", "CONTROL", "TEST", "RESIDUAL_RISK", "OWNER"), path.name)
    ids = [match.group(1) for line in text.splitlines() if (match := THREAT_ROW.match(line))]
    expected = [f"TM-{index:02d}" for index in range(1, 17)]
    if ids != expected:
        raise RuntimeError(f"threat model must contain ordered TM-01..TM-16, found {ids}")
    for value in (
        "Cross-tenant read", "Cross-tenant write", "IDOR", "Forged tenant", "Role escalation",
        "Forge JWT", "Session hijack", "Provider Credentials", "Audit Log", "Cache key",
        "Malicious PDF", "Path traversal", "Prompt injection", "Replay", "Backup", "supply-chain",
    ):
        if value.casefold() not in text.casefold():
            raise RuntimeError(f"threat model missing required MentorFlow threat: {value}")
    return len(ids)


def _verify_load(root: Path) -> tuple[int, int, int]:
    for profile in LOAD_PROFILES:
        if not (root / "tests" / "load" / profile / "scenario.js").is_file():
            raise RuntimeError(f"load profile missing: {profile}")
    core = (root / "tests" / "load" / "lib" / "core.js").read_text(encoding="utf-8")
    thresholds = (root / "tests" / "load" / "lib" / "thresholds.js").read_text(encoding="utf-8")
    _require(
        core,
        (
            "PROVIDER_ADAPTER=fake", "unauthorized_success_count", "document_job_completion_ms",
            "SESSION_PATH", "WORKSPACE_LIST_PATH", "KNOWLEDGE_MAP_PATH", "PLAN_READ_PATH",
            "PROGRESS_READ_PATH", "REVIEW_READ_PATH", "COST_READ_PATH", "AUDIT_READ_PATH",
            "DOCUMENT_SUBMIT_PATH", "DOCUMENT_STATUS_PATH", "AI_TEACHER_PATH", "LOAD_USERS_JSON",
            "foreignProbe", "provider calls are zero",
        ),
        "tests/load/lib/core.js",
    )
    _require(
        thresholds,
        (
            "BASELINE_REQUIRED", 'http_req_failed: ["rate<0.01"]', 'checks: ["rate>0.99"]',
            'document_job_completion_ms: ["p(95)<120000"]', 'unauthorized_success_count: ["count==0"]',
            'real_provider_calls: ["count==0"]', "ai_teacher_controlled", "tenant_isolation",
            "Architect approval required",
        ),
        "tests/load/lib/thresholds.js",
    )
    corpus = _json(root / "tests" / "load" / "fixtures" / "tenant-corpus.v1.json")
    organizations = corpus.get("organizations", [])
    if len(organizations) != 3 or corpus.get("contains_credentials") is not False:
        raise RuntimeError("tenant load corpus must contain three synthetic organizations and no credentials")
    users = sum(len(org.get("users", [])) for org in organizations)
    if users < 20 or any(len(org.get("workspace_ids", [])) < 2 for org in organizations):
        raise RuntimeError("tenant load corpus must contain >=20 users and multiple workspaces per organization")
    types = set(corpus.get("requirements", {}).get("foreign_resource_types", []))
    if types != RESOURCE_TYPES:
        raise RuntimeError(f"tenant load corpus resource coverage mismatch: {types}")
    for org in organizations:
        if set(org.get("resources", {})) != RESOURCE_TYPES or any(len(values) < 1 for values in org["resources"].values()):
            raise RuntimeError("every organization must have foreign IDs for all seven resource families")
    return len(LOAD_PROFILES), len(organizations), users


def _verify_sbom_and_risk(root: Path) -> tuple[int, int]:
    sbom_path = root / "reports" / "enterprise" / "machine" / "lockfile-sbom.cdx.json"
    sbom = _json(sbom_path)
    components = sbom.get("components", [])
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6" or len(components) <= 100:
        raise RuntimeError("CycloneDX lockfile SBOM is missing or incomplete")
    ecosystems: set[str] = set()
    scopes: set[str] = set()
    for component in components:
        properties = {
            item["name"]: item["value"]
            for item in component.get("properties", [])
            if isinstance(item, dict) and "name" in item and "value" in item
        }
        required = {
            "mentorflow:ecosystem", "mentorflow:dependency-scope", "mentorflow:source", "mentorflow:license-evidence",
        }
        if not required.issubset(properties):
            raise RuntimeError(f"SBOM component lacks scope/source/license evidence: {component.get('purl')}")
        ecosystems.add(str(properties["mentorflow:ecosystem"]))
        scopes.add(str(properties["mentorflow:dependency-scope"]))
        if properties["mentorflow:ecosystem"] == "python" and component.get("licenses"):
            raise RuntimeError("Python lockfile license must not be guessed")
    if ecosystems != {"python", "npm"} or not {"direct-runtime", "transitive-runtime"}.issubset(scopes):
        raise RuntimeError(f"SBOM ecosystem/scope coverage incomplete: ecosystems={ecosystems}, scopes={scopes}")
    risk = _json(root / "reports" / "enterprise" / "machine" / "dependency-risk-report.json")
    if risk.get("legal_audit") is not False or risk.get("license_compliant") is not None:
        raise RuntimeError("dependency report must not claim legal compliance")
    if risk.get("vulnerability_gate") != "PENDING_SECURITY_SCAN" or len(risk.get("components", [])) != len(components):
        raise RuntimeError("dependency risk report vulnerability/component state is invalid")
    if risk.get("source_sbom_hash_algorithm") != CANONICAL_JSON_HASH_ALGORITHM:
        raise RuntimeError("dependency risk report does not declare the canonical SBOM hash algorithm")
    if risk.get("source_sbom_sha256") != canonical_json_sha256(sbom):
        raise RuntimeError("dependency risk report is stale relative to the SBOM")
    required_risk_fields = {
        "known_vulnerabilities", "vulnerability_status", "maintenance_status", "licenses", "risk_flags",
        "dependency_scope", "source",
    }
    if any(not required_risk_fields.issubset(item) for item in risk["components"]):
        raise RuntimeError("dependency risk entries are not field-complete")
    return len(components), len(risk["components"])


def _verify_readiness(
    root: Path,
    *,
    provenance_remote: str | None = None,
    provenance_refs: tuple[str, ...] = (),
) -> tuple[int, int]:
    evidence = _json(root / "reports" / "enterprise" / "readiness-evidence.json")
    if evidence.get("schema") != "mentorflow.enterprise-readiness-evidence.v1" or "score" in evidence:
        raise RuntimeError("readiness evidence schema/score rule invalid")
    domains = evidence.get("domains", {})
    if set(domains) != READINESS_DOMAINS:
        raise RuntimeError("readiness evidence does not contain the exact twelve domains")
    for name, domain in domains.items():
        if domain.get("status") not in {"PASS", "PARTIAL", "FAIL", "PENDING"} or not domain.get("evidence"):
            raise RuntimeError(f"readiness domain is not evidence-backed: {name}")
        if domain["status"] == "PASS" and (domain.get("blockers") or domain.get("owner_evidence_status") != "PRESENT"):
            raise RuntimeError(f"readiness domain cannot hide a blocker behind PASS: {name}")
        for item in domain["evidence"]:
            if item.get("kind") == "LOCAL_FILE":
                ref = str(item.get("ref", ""))
                path = (root / ref.removeprefix("repo:")).resolve()
                if not ref.startswith("repo:") or not path.is_relative_to(root) or not path.is_file():
                    raise RuntimeError(f"readiness local evidence is missing: {ref}")
    snapshot_path = root / "reports" / "enterprise" / "machine" / "readiness-snapshot.json"
    snapshot = _json(snapshot_path)
    if snapshot.get("schema") != "mentorflow.enterprise-readiness-snapshot.v1" or snapshot.get("score") is not None:
        raise RuntimeError("readiness snapshot schema or no-score invariant failed")
    if snapshot.get("domains") != domains or snapshot.get("overall_status") != "PARTIAL":
        raise RuntimeError("readiness snapshot drifted from its evidence or truthful aggregate status")
    source_commit = str(snapshot.get("source_commit", ""))
    source_tree = str(snapshot.get("source_tree", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or not re.fullmatch(r"[0-9a-f]{40}", source_tree):
        raise RuntimeError("readiness snapshot source identity is not exact")
    regenerated = generate_readiness_from_git(
        root,
        root / "reports" / "enterprise" / "readiness-evidence.json",
        source_commit=source_commit,
        source_tree=source_tree,
    )
    verify_snapshot_file(snapshot_path, regenerated)
    if bool(provenance_remote) != bool(provenance_refs):
        raise RuntimeError("readiness provenance remote and approved refs must be supplied together")
    if provenance_remote:
        verify_remote_reachability(
            root,
            source_commit=source_commit,
            remote=provenance_remote,
            refs=provenance_refs,
        )
    return len(domains), 1


def verify(
    root: Path = ROOT,
    *,
    provenance_remote: str | None = None,
    provenance_refs: tuple[str, ...] = (),
) -> dict[str, object]:
    required_files = [
        root / ".dockerignore", root / ".gitleaks.toml", root / "Dockerfile",
        root / ".github" / "enterprise-context.lock.json", root / ".github" / "enterprise-security-waivers.json",
        root / ".github" / "dependabot.yml", root / "deploy" / "enterprise" / "compose.staging.yml",
        root / "deploy" / "enterprise" / "nginx.conf", root / "contracts" / "enterprise" / "release-contracts.schema.json",
        root / "contracts" / "enterprise" / "readiness-snapshot.schema.json",
        root / "contracts" / "enterprise" / "security-waivers.schema.json",
        root / "scripts" / "enterprise" / "generate_asvs_matrix.py",
        root / "scripts" / "enterprise" / "generate_dependency_risk_report.py",
        root / "scripts" / "enterprise" / "generate_readiness_snapshot.py",
        root / "scripts" / "enterprise" / "generate_release_manifest.py",
        root / "scripts" / "enterprise" / "generate_sbom.py",
        root / "scripts" / "enterprise" / "generate_tenant_load_corpus.py",
        root / "scripts" / "enterprise" / "release_rollback.py",
        root / "scripts" / "enterprise" / "verify_enterprise_context.py",
        root / "scripts" / "enterprise" / "verify_security_waivers.py",
        root / "reports" / "enterprise" / "machine" / "lockfile-sbom.cdx.json",
        root / "reports" / "enterprise" / "machine" / "dependency-risk-report.json",
        root / "reports" / "enterprise" / "machine" / "environment-truth.json",
        root / "reports" / "enterprise" / "machine" / "readiness-snapshot.json",
    ]
    required_files.extend(root / ".github" / "workflows" / name for name in WORKFLOWS)
    required_files.extend(root / "docs" / "enterprise" / name for name in EVIDENCE_DOCS)
    required_files.extend(root / "reports" / "enterprise" / "machine" / name for name in PENDING_REPORTS)
    missing = [path.relative_to(root).as_posix() for path in required_files if not path.is_file()]
    if missing:
        raise RuntimeError(f"required E1-07B assets missing: {missing}")

    parsed_workflows: dict[str, dict[str, object]] = {}
    for name in WORKFLOWS:
        path = root / ".github" / "workflows" / name
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError(f"workflow does not parse as a mapping: {name}")
        parsed_workflows[name] = parsed
    for path in (root / ".github" / "dependabot.yml", root / "deploy" / "enterprise" / "compose.staging.yml"):
        if not isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict):
            raise RuntimeError(f"YAML does not parse as a mapping: {path.relative_to(root)}")

    ci = (root / ".github" / "workflows" / "enterprise-ci.yml").read_text(encoding="utf-8")
    _require(
        ci,
        (
            "PR_FAST", "MAIN_FULL", "ubuntu-24.04", "windows-latest", 'python: ["3.11", "3.12", "3.13"]',
            'python: ["3.12"]', 'node-version: "22.20.0"', "python -m compileall", 'python -c "import app.main"',
            "python -m tabnanny", "npm test", "npm run build", "verify_enterprise_context.py", "test_enterprise_contracts_v1.py",
            "test_local_auth.py", "test_planning_resource_authorization.py", "test_e1b_enterprise_evidence.py",
            "test_local_migration.py", "test_archive_import_security.py", "backend-document-ml", "backend-desktop",
            "backend/requirements-documents.txt", "backend/requirements-desktop.txt",
        ),
        "enterprise-ci.yml",
    )
    if "ubuntu-latest" in ci:
        raise RuntimeError("enterprise-ci.yml must use the canonical Ubuntu 24.04 runner")
    _verify_action_pins(parsed_workflows["enterprise-ci.yml"], "enterprise-ci.yml")
    _verify_required_failure_semantics(parsed_workflows["enterprise-ci.yml"], "enterprise-ci.yml")
    _verify_backend_suite_membership(root, parsed_workflows["enterprise-ci.yml"])
    pr = (root / ".github" / "workflows" / "enterprise-pr-fast.yml").read_text(encoding="utf-8")
    _require(
        pr,
        (
            "lane: PR_FAST", "ENTERPRISE_SECURITY_SCANNER_LEASE", "gitleaks/gitleaks-action@v3",
            "actions/dependency-review-action@v4", "fail-on-severity: critical",
        ),
        "enterprise-pr-fast.yml",
    )
    main = (root / ".github" / "workflows" / "enterprise-main-full.yml").read_text(encoding="utf-8")
    _require(main, ("lane: MAIN_FULL", "core-v2/integration"), "enterprise-main-full.yml")

    security = (root / ".github" / "workflows" / "enterprise-security.yml").read_text(encoding="utf-8")
    _require(
        security,
        (
            "CodeQL", "gitleaks git", "gitleaks dir", "osv-python.json", "osv-node.json", "npm audit",
            "semgrep", "python-sbom.cdx.json", "node-sbom.cdx.json", "dependency-risk-report.json",
            "verify_security_waivers.py", "security-gate.json", "ENTERPRISE_SECURITY_SCANNER_LEASE",
        ),
        "enterprise-security.yml",
    )
    for prohibited in ("pip install pip-audit", "pip install semgrep", "npm install", "npx "):
        if prohibited in security:
            raise RuntimeError(f"unleased scanner installation found: {prohibited}")
    dependabot = (root / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    _require(dependabot, ("package-ecosystem: pip", "package-ecosystem: npm", "package-ecosystem: github-actions"), "dependabot.yml")
    waivers = _json(root / ".github" / "enterprise-security-waivers.json")
    if waivers != {"schema": "mentorflow.enterprise-security-waivers.v1", "waivers": []}:
        raise RuntimeError("initial security waiver registry must be empty and schema-exact")

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    _require(
        dockerfile,
        (
            "AS backend-image", "AS frontend-image", "AS document-worker-image", "AS worker-image", "APP_VERSION",
            "ARG NODE_BASE_IMAGE", "ARG PYTHON_BASE_IMAGE", "ARG NGINX_BASE_IMAGE",
            "FROM ${NODE_BASE_IMAGE}", "FROM ${PYTHON_BASE_IMAGE}", "FROM ${NGINX_BASE_IMAGE}",
            "org.opencontainers.image.version", "MENTORFLOW_PROVIDER_MODE=fake", "USER 10001:10001", "USER 101:101",
        ),
        "Dockerfile",
    )
    container = (root / ".github" / "workflows" / "container-release.yml").read_text(encoding="utf-8")
    _verify_container_release(parsed_workflows["container-release.yml"], container)

    release_schema = _json(root / "contracts" / "enterprise" / "release-contracts.schema.json")
    definitions = release_schema.get("$defs", {})
    if set(definitions) != {"ImageVersion", "ImageManifest", "EnterpriseReleaseManifest"}:
        raise RuntimeError("release schema must define ImageVersion, ImageManifest, and EnterpriseReleaseManifest")
    manifest_required = set(definitions["EnterpriseReleaseManifest"].get("required", []))
    required_manifest_fields = {
        "source_commit", "source_tree", "enterprise_contract_version", "migration_version", "image_tags",
        "sbom_refs", "security_report_refs", "asvs_matrix_version", "load_report_refs", "qa_report_refs",
        "known_blockers", "pending_staging_gates",
    }
    if not required_manifest_fields.issubset(manifest_required):
        raise RuntimeError("EnterpriseReleaseManifest schema is missing required traceability fields")
    release_generator = (root / "scripts" / "enterprise" / "generate_release_manifest.py").read_text(encoding="utf-8")
    _require(
        release_generator,
        (
            "source_tree", "semantic_version", "migration_version", "sbom_refs", "security_report_refs",
            "pending_staging_gates", "real_provider_calls", "sha256:git-blob-bytes-v1",
            "sha256:artifact-bytes-v1", "sha256:canonical-json-v1",
            "mentorflow.enterprise-final-release-manifest.v1", "mentorflow.enterprise-final-digest-lock.v1",
            "docker buildx imagetools inspect", "obviously synthetic digest",
        ),
        "generate_release_manifest.py",
    )
    release = (root / "scripts" / "enterprise" / "release_rollback.py").read_text(encoding="utf-8")
    _require(release, ("tests\" / \"load\" / \"smoke", "migration_and_deploy", "promote_three_images", "rollback_promotion_tags", "rollback_health", "post_rollback_smoke"), "release_rollback.py")
    release_doc = (root / "docs" / "enterprise" / "release-rollback.md").read_text(encoding="utf-8")
    _require(release_doc, ("Database migration strategy", "forward-fix", "pre-migration backup", "post-rollback smoke", "not rolled back by checking out"), "release-rollback.md")

    asvs_counts = _verify_asvs(root / "docs" / "enterprise" / "asvs-control-matrix.md")
    threat_count = _verify_threat_model(root / "docs" / "enterprise" / "threat-model.md")
    data = (root / "docs" / "enterprise" / "data-classification.md").read_text(encoding="utf-8")
    _require(
        data,
        (
            "PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "Email and account metadata",
            "Organization membership", "Uploaded documents", "Student Model", "Misconceptions", "Review history",
            "Provider credentials", "Audit metadata", "Cost events", "Logs and traces", "backups",
            "Encryption", "Logging policy", "Export policy", "Retention seam",
        ),
        "data-classification.md",
    )
    for name in EVIDENCE_DOCS:
        text = (root / "docs" / "enterprise" / name).read_text(encoding="utf-8")
        if name == "authorization-matrix.md":
            _require(
                text,
                (
                    "default is **DENY**",
                    "GovernanceEnforcementService",
                    "ResourceVisibilityFilter",
                    "Break glass",
                ),
                name,
            )
        elif "Status:" not in text:
            raise RuntimeError(f"enterprise evidence lacks an explicit status: {name}")
    owner_evidence = "\n".join((root / "docs" / "enterprise" / name).read_text(encoding="utf-8") for name in EVIDENCE_DOCS)
    _require(
        owner_evidence,
        ("PENDING_OWNER_EVIDENCE", *(f"{name} = NOT_RUN" for name in ENVIRONMENT_NOT_RUN_FACTS)),
        "enterprise evidence pack",
    )
    runbook = (root / "docs" / "enterprise" / "digitalocean-staging-runbook.md").read_text(encoding="utf-8")
    _require(runbook, ("SGP1", "Ubuntu 24.04", "SSH", "non-root", "firewall", "IPv6", "Docker", "Compose", "volume", "TLS", "backup", "Monitoring", "rollback", "Safe staging destruction"), "digitalocean-staging-runbook.md")
    interview = (root / "docs" / "enterprise" / "INTERVIEW_ENGINEERING_EVIDENCE.md").read_text(encoding="utf-8")
    _require(interview, ("Why PostgreSQL RLS", "Why OIDC / Keycloak", "Why OpenTelemetry", "Why durable workflows", "Why k6", "cannot yet truthfully say"), "INTERVIEW_ENGINEERING_EVIDENCE.md")

    load_profiles, tenant_orgs, tenant_users = _verify_load(root)
    sbom_components, risk_components = _verify_sbom_and_risk(root)
    readiness_domains, readiness_snapshot = _verify_readiness(
        root,
        provenance_remote=provenance_remote,
        provenance_refs=provenance_refs,
    )
    environment = _json(root / "reports" / "enterprise" / "machine" / "environment-truth.json")
    expected_environment_truth = {name: "NOT_RUN" for name in ENVIRONMENT_NOT_RUN_FACTS}
    if environment.get("gates") != expected_environment_truth:
        raise RuntimeError("current environment truth must preserve six exact NOT_RUN facts")
    if environment.get("installed_for_e1_07b") != []:
        raise RuntimeError("E1-07B must not install unavailable staging/scanner tooling")
    for name in PENDING_REPORTS:
        report = _json(root / "reports" / "enterprise" / "machine" / name)
        if report.get("status") != "STAGING_EXECUTION_PENDING":
            raise RuntimeError(f"unexecuted machine report must remain STAGING_EXECUTION_PENDING: {name}")

    for path in required_files:
        text = path.read_text(encoding="utf-8")
        if "class ActorContext" in text or "class TenantContext" in text:
            raise RuntimeError(f"shared context redefinition found in {path.relative_to(root)}")

    return {
        "status": "PASS",
        "workflows": len(WORKFLOWS),
        "ci_lanes": 2,
        "container_images": len(RELEASE_IMAGE_ROLES),
        "evidence_documents": len(EVIDENCE_DOCS),
        "asvs_controls": sum(asvs_counts.values()),
        "threat_classes": threat_count,
        "load_profiles": load_profiles,
        "tenant_organizations": tenant_orgs,
        "tenant_users": tenant_users,
        "tenant_resource_types": len(RESOURCE_TYPES),
        "sbom_components": sbom_components,
        "dependency_risk_components": risk_components,
        "readiness_domains": readiness_domains,
        "readiness_snapshot": readiness_snapshot,
        "environment_not_run_facts": len(ENVIRONMENT_NOT_RUN_FACTS),
        "pending_machine_reports": len(PENDING_REPORTS),
        "security_waivers": 0,
        "provider_calls": 0,
        "shared_contract_paths_owned": 0,
        "scanner_installations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify E1 release and readiness assets.")
    parser.add_argument("--provenance-remote")
    parser.add_argument("--provenance-ref", action="append", default=[])
    args = parser.parse_args()
    try:
        result = verify(
            provenance_remote=args.provenance_remote,
            provenance_refs=tuple(args.provenance_ref),
        )
    except (KeyError, OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        print(f"E1_RELEASE_ASSET_GATE_FAILED: {exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

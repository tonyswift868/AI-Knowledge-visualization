from __future__ import annotations

import json
import os
import platform
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
from verify_release_assets import (  # noqa: E402
    BACKEND_TEST_ROOTS,
    DESKTOP_TESTS,
    DOCUMENT_ML_TESTS,
    ENVIRONMENT_NOT_RUN_FACTS,
    PINNED_ACTION,
    RELEASE_IMAGE_ROLES,
    RELEASE_REQUIRED_JOBS,
    _verify_sbom_and_risk,
)
from verify_security_waivers import evaluate as evaluate_security, load_waivers  # noqa: E402
import pytest_windows_diagnostics as windows_diagnostics  # noqa: E402


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
        assert checkout["uses"] == "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        assert checkout["with"] == {"fetch-depth": 0, "fetch-tags": True}

    migration_steps = jobs["migrations"]["steps"]
    migration_setup = next(
        step
        for step in migration_steps
        if step.get("uses") == "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
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


def _external_uses(workflow: dict[str, object]) -> list[str]:
    result: list[str] = []
    for job in workflow["jobs"].values():
        if "uses" in job and not job["uses"].startswith("./"):
            result.append(job["uses"])
        result.extend(
            step["uses"]
            for step in job.get("steps", [])
            if "uses" in step and not step["uses"].startswith("./")
        )
    return result


def _named_run(job: dict[str, object], name: str) -> str:
    return next(step["run"] for step in job["steps"] if step.get("name") == name)


def _test_paths(command: str) -> set[str]:
    import re

    return set(re.findall(r"backend/(?:tests|tests_w2|tests_w6)/test_[A-Za-z0-9_]+\.py", command))


def test_main_full_backend_membership_is_complete_disjoint_and_required() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "enterprise-ci.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    core_job = jobs["backend-full"]
    document_job = jobs["backend-document-ml"]
    desktop_job = jobs["backend-desktop"]
    for job in (core_job, document_job, desktop_job):
        assert job["if"] == "inputs.lane == 'MAIN_FULL'"
        assert job["needs"] == "lane-contract"
        assert "continue-on-error" not in job
        assert all("continue-on-error" not in step for step in job["steps"])

    core_run = _named_run(core_job, "Complete core backend regression excluding explicit optional suites")
    document_run = _named_run(document_job, "Required document and ML suite")
    desktop_run = _named_run(desktop_job, "Required desktop suite")
    ignored = {
        value.removeprefix("--ignore=")
        for value in core_run.split()
        if value.startswith("--ignore=")
    }
    assert ignored == DOCUMENT_ML_TESTS | DESKTOP_TESTS
    assert _test_paths(document_run) == DOCUMENT_ML_TESTS
    assert _test_paths(desktop_run) == DESKTOP_TESTS
    assert "backend/requirements-documents.txt" in str(document_job)
    assert "backend/requirements-desktop.txt" in str(desktop_job)
    assert desktop_job["runs-on"] == "windows-latest"

    all_tests = {
        path.relative_to(ROOT).as_posix()
        for test_root in BACKEND_TEST_ROOTS
        for path in (ROOT / test_root).rglob("test_*.py")
    }
    core = all_tests - ignored
    assert core
    assert DOCUMENT_ML_TESTS
    assert DESKTOP_TESTS
    assert not (core & DOCUMENT_ML_TESTS)
    assert not (core & DESKTOP_TESTS)
    assert not (DOCUMENT_ML_TESTS & DESKTOP_TESTS)
    assert core | DOCUMENT_ML_TESTS | DESKTOP_TESTS == all_tests
    assert all(root in core_run for root in BACKEND_TEST_ROOTS)
    assert (
        "--deselect=backend/tests/test_enterprise_tenant_persistence.py::"
        "test_real_postgres_rls_is_fail_closed_and_pool_safe"
    ) in core_run
    assert any(
        step.get("name") == "Bootstrap exact frozen relock tool"
        and '"uv==0.11.32"' in step.get("run", "")
        for step in core_job["steps"]
    )


def test_shadow_repair_e107_workflow_contract() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "enterprise-ci.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"push", "workflow_call", "workflow_dispatch"}
    assert trigger["push"] == {
        "branches": ["codex/e1s1-shadow-repair-e107-ci-windows"],
        "paths": [
            ".github/workflows/enterprise-ci.yml",
            "backend/tests/test_e1b_enterprise_evidence.py",
            "scripts/enterprise/pytest_windows_diagnostics.py",
            "scripts/enterprise/run_windows_pytest_diagnostic.ps1",
        ],
    }
    dispatch_inputs = trigger["workflow_dispatch"]["inputs"]
    call_inputs = trigger["workflow_call"]["inputs"]
    assert set(dispatch_inputs) == {"lane", "expected_source_sha"}
    assert set(call_inputs) == {"lane", "expected_source_sha"}
    assert dispatch_inputs["lane"]["options"] == [
        "PR_FAST",
        "MAIN_FULL",
        "SHADOW_REPAIR_E107",
    ]
    assert dispatch_inputs["expected_source_sha"]["default"] == ""
    assert call_inputs["expected_source_sha"]["default"] == ""

    jobs = workflow["jobs"]
    lane_run = _named_run(jobs["lane-contract"], "Validate lane and exact focused source")
    assert "PR_FAST|MAIN_FULL|SHADOW_REPAIR_E107" in lane_run
    assert 'test "$GITHUB_SHA" = "$EXPECTED_SOURCE_SHA"' in lane_run
    assert jobs["lane-contract"]["steps"][0]["env"]["EXPECTED_SOURCE_SHA"] == (
        "${{ inputs.expected_source_sha || github.event.after }}"
    )

    windows = jobs["backend-full-windows"]
    focused_job_if = (
        "inputs.lane == 'MAIN_FULL' || inputs.lane == 'SHADOW_REPAIR_E107' || "
        "(github.event_name == 'push' && "
        "github.ref == 'refs/heads/codex/e1s1-shadow-repair-e107-ci-windows')"
    )
    focused_only_if = (
        "inputs.lane == 'SHADOW_REPAIR_E107' || (github.event_name == 'push' && "
        "github.ref == 'refs/heads/codex/e1s1-shadow-repair-e107-ci-windows')"
    )
    assert windows["if"] == focused_job_if
    assert windows["needs"] == "lane-contract"
    assert windows["runs-on"] == "windows-latest"
    assert windows["timeout-minutes"] == 120
    assert windows["strategy"]["fail-fast"] is False
    assert windows["strategy"]["matrix"] == {
        "python": ["3.11", "3.12", "3.13"],
        "shard": [1, 2, 3, 4, 5, 6],
    }
    diagnostic = next(
        step for step in windows["steps"]
        if step.get("name") == "Bounded diagnostic core backend shard"
    )
    assert diagnostic["shell"] == "pwsh"
    assert diagnostic["timeout-minutes"] == 105
    assert "run_windows_pytest_diagnostic.ps1" in diagnostic["run"]
    assert "-TimeoutMinutes 100" in diagnostic["run"]
    assert "-ShardCount 6" in diagnostic["run"]
    assert "--deselect=backend/tests/test_enterprise_tenant_persistence.py::test_real_postgres_rls_is_fail_closed_and_pool_safe" in diagnostic["run"]
    assert all(root in diagnostic["run"] for root in BACKEND_TEST_ROOTS)
    assert diagnostic["env"]["MENTORFLOW_DIAGNOSTIC_SOURCE_SHA"] == "${{ github.sha }}"
    assert "matrix.shard" in diagnostic["env"]["MENTORFLOW_DB"]
    assert "matrix.shard" in diagnostic["env"]["MENTORFLOW_ORM_DB"]
    assert any(
        step.get("name") == "Bootstrap exact frozen relock tool"
        and '"uv==0.11.32"' in step.get("run", "")
        for step in windows["steps"]
    )
    upload = next(
        step for step in windows["steps"]
        if step.get("name") == "Upload Windows pytest diagnostics"
    )
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    assert upload["with"]["if-no-files-found"] == "error"

    security = jobs["enterprise-security-corpus"]
    assert security["if"] == focused_job_if
    security_step = next(
        step for step in security["steps"]
        if step.get("name") == "File, path, provider, URL, and redaction regression"
    )
    assert security_step["env"]["MENTORFLOW_PROVIDER_MODE"] == "fake"
    assert security_step["env"]["MENTORFLOW_DB"].startswith("${{ runner.temp }}")
    assert security_step["env"]["MENTORFLOW_ORM_DB"].startswith("${{ runner.temp }}")

    migrations = jobs["migrations"]
    assert migrations["if"] == focused_job_if
    assert any(
        step.get("run") == "sudo apt-get update && sudo apt-get install --yes libegl1"
        for step in migrations["steps"]
    )
    migration_test = next(
        step for step in migrations["steps"]
        if step.get("name") == "Alembic and portable migration gates"
    )
    assert migration_test["env"]["QT_QPA_PLATFORM"] == "offscreen"
    assert migration_test["env"]["MENTORFLOW_DB"].startswith("${{ runner.temp }}")
    assert migration_test["env"]["MENTORFLOW_ORM_DB"].startswith("${{ runner.temp }}")

    focused = jobs["shadow-repair-contract"]
    assert focused["if"] == focused_only_if
    assert focused["runs-on"] == "windows-latest"
    assert focused["timeout-minutes"] == 20
    watchdog = next(
        step for step in focused["steps"]
        if step.get("name") == "Watchdog timeout captures stalled node, stack, and process tree"
    )
    assert watchdog["timeout-minutes"] == 10
    assert "-TimeoutSeconds 5" in watchdog["run"]
    assert "-ReturnToCaller" in watchdog["run"]
    assert "Expected watchdog exit 124" in watchdog["run"]
    assert "processes-timeout-before-cleanup.json" in watchdog["run"]
    assert "TIMED_OUT_CLEANUP_COMPLETE" in watchdog["run"]
    assert "executable_path" in watchdog["run"] and "full_command_line" in watchdog["run"]
    wrapper_text = (SCRIPTS / "run_windows_pytest_diagnostic.ps1").read_text(encoding="utf-8")
    for token in (
        "mentorflow.windows-process-tree.v2",
        "pid =",
        "ppid =",
        "executable_path =",
        "full_command_line =",
        "process_creation_time =",
        "captured_at =",
        "capture_phase =",
        "pytest_phase =",
        "cleanup_attempted =",
        "cleanup_result =",
        "final_exit_state =",
        'CapturePhase "timeout-before-cleanup"',
        '$finalExitState = "TIMEOUT_PENDING_CLEANUP"',
    ):
        assert token in wrapper_text

    coverage = jobs["shadow-shard-coverage"]
    assert coverage["if"] == f"always() && ({focused_only_if})"
    assert set(coverage["needs"]) == {"lane-contract", "backend-full-windows"}
    assert coverage["timeout-minutes"] == 15
    download = next(
        step for step in coverage["steps"]
        if step.get("name") == "Download every Windows shard diagnostic artifact"
    )
    assert download["uses"] == "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
    coverage_run = _named_run(coverage, "Prove mutually exclusive and collectively exhaustive shards")
    for token in (
        "--expected-shards 6",
        "--expected-python-version 3.11",
        "--expected-python-version 3.12",
        "--expected-python-version 3.13",
        '--expected-source-sha "${{ github.sha }}"',
    ):
        assert token in coverage_run

    gate = jobs["shadow-required-gate"]
    assert gate["if"] == f"always() && ({focused_only_if})"
    assert set(gate["needs"]) == {
        "lane-contract",
        "backend-full-windows",
        "enterprise-security-corpus",
        "migrations",
        "shadow-repair-contract",
        "shadow-shard-coverage",
    }
    gate_run = _named_run(
        gate,
        "Reject failed, cancelled, missing, neutral, timed out, or skipped focused jobs",
    )
    assert 'test "$result" = "success"' in gate_run
    assert 'test "$GITHUB_SHA" = "$EXPECTED_SOURCE_SHA"' in gate_run
    assert gate["steps"][0]["env"]["EXPECTED_SOURCE_SHA"] == (
        "${{ inputs.expected_source_sha || github.event.after }}"
    )
    assert gate["steps"][0]["env"]["SHARD_COVERAGE_RESULT"] == (
        "${{ needs.shadow-shard-coverage.result }}"
    )

    for job_name in ("readiness-provenance", "frontend", "enterprise-contract"):
        assert jobs[job_name]["if"] == "inputs.lane == 'PR_FAST' || inputs.lane == 'MAIN_FULL'"

    for job_name, job in jobs.items():
        rendered = json.dumps(job)
        if "windows-latest" in rendered:
            assert "timeout-minutes" in job, f"{job_name} lacks a Windows job timeout"
        pytest_steps = [
            step for step in job.get("steps", [])
            if "python -m pytest" in str(step.get("run", ""))
        ]
        if "windows-latest" in rendered and pytest_steps:
            for step in pytest_steps:
                assert "timeout-minutes" in step, (
                    f"{job_name}/{step.get('name', '<unnamed>')} lacks a pytest step timeout"
                )
        for step in job.get("steps", []):
            if step.get("if") == "always()":
                assert step.get("name", "").startswith("Upload "), (
                    f"{job_name} applies always() outside a diagnostic upload step"
                )


def test_windows_diagnostic_shards_are_stable_disjoint_and_complete() -> None:
    nodeids = [f"backend/tests/test_shard_fixture.py::test_case[{index}]" for index in range(257)]
    assignments = {
        shard: {
            nodeid
            for nodeid in nodeids
            if windows_diagnostics.shard_for_nodeid(nodeid, 6) == shard
        }
        for shard in range(1, 7)
    }
    assert all(assignments.values())
    assert set().union(*assignments.values()) == set(nodeids)
    assert sum(len(values) for values in assignments.values()) == len(nodeids)
    assert all(
        not (assignments[left] & assignments[right])
        for left in assignments
        for right in assignments
        if left < right
    )
    assert [windows_diagnostics.shard_for_nodeid(nodeid, 6) for nodeid in nodeids] == [
        windows_diagnostics.shard_for_nodeid(nodeid, 6) for nodeid in nodeids
    ]
    manifest = windows_diagnostics.build_collection_manifest(
        nodeids,
        shard_count=6,
        shard_index=3,
        source_sha="a" * 40,
    )
    assert manifest["duplicate_test_count"] == manifest["omitted_test_count"] == 0
    assert manifest["python_version"] == ".".join(platform.python_version_tuple()[:2])
    assert manifest["python_version_full"] == platform.python_version()
    assert manifest["shards_mutually_exclusive"] is True
    assert manifest["shards_collectively_exhaustive"] is True
    assert manifest["no_tests_silently_dropped"] is True
    assert sum(manifest["shard_counts"].values()) == len(nodeids)
    assert manifest["selected_nodeids"] == sorted(assignments[3])


def test_windows_diagnostic_plugin_records_completed_run(tmp_path: Path) -> None:
    fixture = tmp_path / "test_diagnostic_fixture.py"
    fixture.write_text(
        "def test_first():\n    assert True\n\ndef test_second():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(SCRIPTS), environment.get("PYTHONPATH", ""))
        if value
    )
    environment[windows_diagnostics.ARTIFACT_ENV] = str(artifacts)
    environment[windows_diagnostics.STACK_INTERVAL_ENV] = "0"
    environment[windows_diagnostics.SOURCE_SHA_ENV] = "f" * 40
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_windows_diagnostics",
            "--mentorflow-shard-index",
            "1",
            "--mentorflow-shard-count",
            "1",
            f"--confcutdir={tmp_path}",
            str(fixture),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    status = json.loads((artifacts / "status.json").read_text(encoding="utf-8"))
    assert status["exit_state"] == "COMPLETED"
    assert status["pytest_exit_code"] == 0
    assert status["current_phase"] == "process-exit"
    assert status["collected_total"] == status["selected_for_shard"] == 2
    assert status["last_started_test"] == status["last_completed_test"]
    assert status["source_sha"] == "f" * 40
    manifest = json.loads(
        (artifacts / windows_diagnostics.COLLECTION_MANIFEST).read_text(encoding="utf-8")
    )
    assert manifest["collected_test_count"] == manifest["selected_test_count"] == 2
    assert manifest["duplicate_test_count"] == manifest["omitted_test_count"] == 0
    assert manifest["no_tests_silently_dropped"] is True
    events = [
        json.loads(line)
        for line in (artifacts / "node-timeline.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    kinds = {event["kind"] for event in events}
    assert {"pytest-configured", "collection-complete", "test-start", "test-complete", "session-finish"} <= kinds
    assert (artifacts / "faulthandler.log").stat().st_size > 0


def test_windows_shard_manifest_verifier_rejects_omission_or_duplication(tmp_path: Path) -> None:
    nodeids = [f"backend/tests/test_manifest_fixture.py::test_case[{index}]" for index in range(97)]
    source_sha = "b" * 40

    def write_manifests(root: Path) -> dict[tuple[str, int], Path]:
        paths: dict[tuple[str, int], Path] = {}
        for version in ("3.11", "3.12", "3.13"):
            for shard in range(1, 7):
                manifest = windows_diagnostics.build_collection_manifest(
                    nodeids,
                    shard_count=6,
                    shard_index=shard,
                    source_sha=source_sha,
                )
                manifest["python_version"] = version
                path = root / f"python-{version}-shard-{shard}" / windows_diagnostics.COLLECTION_MANIFEST
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                paths[(version, shard)] = path
        return paths

    valid_root = tmp_path / "valid"
    valid_paths = write_manifests(valid_root)
    report = windows_diagnostics.verify_collection_manifests(
        valid_root,
        expected_shards=6,
        expected_python_versions=("3.11", "3.12", "3.13"),
        expected_source_sha=source_sha,
        output_path=tmp_path / "coverage-pass.json",
    )
    assert report["status"] == "PASS"
    assert report["manifest_count"] == 18
    assert report["duplicated_test_count"] == report["omitted_test_count"] == 0

    omitted = json.loads(valid_paths[("3.12", 1)].read_text(encoding="utf-8"))
    omitted["selected_nodeids"].pop()
    valid_paths[("3.12", 1)].write_text(json.dumps(omitted), encoding="utf-8")
    with pytest.raises(RuntimeError, match="E1_SHARD_COVERAGE_FAILED"):
        windows_diagnostics.verify_collection_manifests(
            valid_root,
            expected_shards=6,
            expected_python_versions=("3.11", "3.12", "3.13"),
            expected_source_sha=source_sha,
        )

    duplicate_root = tmp_path / "duplicate"
    duplicate_paths = write_manifests(duplicate_root)
    shard_one = json.loads(duplicate_paths[("3.13", 1)].read_text(encoding="utf-8"))
    shard_two = json.loads(duplicate_paths[("3.13", 2)].read_text(encoding="utf-8"))
    shard_one["selected_nodeids"].append(shard_two["selected_nodeids"][0])
    shard_one["selected_nodeids_sha256"] = windows_diagnostics._canonical_json_sha256(
        sorted(shard_one["selected_nodeids"])
    )
    duplicate_paths[("3.13", 1)].write_text(json.dumps(shard_one), encoding="utf-8")
    with pytest.raises(RuntimeError, match="E1_SHARD_COVERAGE_FAILED"):
        windows_diagnostics.verify_collection_manifests(
            duplicate_root,
            expected_shards=6,
            expected_python_versions=("3.11", "3.12", "3.13"),
            expected_source_sha=source_sha,
        )


def test_owned_workflows_pin_actions_and_do_not_mask_required_failures() -> None:
    for name in ("enterprise-ci.yml", "container-release.yml"):
        path = ROOT / ".github" / "workflows" / name
        text = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)
        uses = _external_uses(workflow)
        assert uses and all(PINNED_ACTION.fullmatch(value) for value in uses)
        assert "continue-on-error" not in text
        assert "|| true" not in text
        assert "; exit 0" not in text
        assert "ubuntu-latest" not in text


def test_final_release_workflow_is_source_bound_four_image_and_fail_closed() -> None:
    path = ROOT / ".github" / "workflows" / "container-release.yml"
    text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "expected_source_sha",
        "expected_source_tree",
        *(f"{role.replace('-', '_')}_rollback_image" for role in RELEASE_IMAGE_ROLES),
    }
    jobs = workflow["jobs"]
    assert set(jobs) == set(RELEASE_REQUIRED_JOBS) | {"required-gate"}
    assert jobs["quality"]["needs"] == "source-contract"
    assert set(jobs["build"]["needs"]) == {"source-contract", "quality", "scanner-lease"}
    assert set(jobs["scan"]["needs"]) == {"build", "scanner-lease"}
    assert set(jobs["publish"]["needs"]) == set(RELEASE_REQUIRED_JOBS) - {"publish"}
    assert set(jobs["required-gate"]["needs"]) == set(RELEASE_REQUIRED_JOBS)
    assert jobs["required-gate"]["if"] == "always()"
    assert all(
        job.get("runs-on") in {None, "ubuntu-24.04"}
        for job in jobs.values()
    )
    assert 'test "$result" = "success"' in _named_run(
        jobs["required-gate"], "Reject failed, cancelled, missing, neutral or skipped required jobs"
    )

    for token in (
        "test \"$GITHUB_SHA\" = \"$EXPECTED_SOURCE_SHA\"",
        "git rev-parse 'HEAD^{tree}'",
        "refs/heads/core-v2/integration",
        "docker buildx build",
        "--metadata-file",
        "--provenance=mode=max",
        "for target in backend frontend document-worker worker",
        "docker buildx imagetools inspect",
        "base-image-digests.json",
        "repository_digest",
        "build_metadata",
        "image_archive",
        "sbom_path",
        "security_path",
        "FINAL_RELEASE_VERIFIED",
        "final-release-manifest.json",
        "final-image-digest-lock.json",
        "rp05-application-images.json",
    ):
        assert token in text
    assert "docker build " not in text
    assert "digest_ref_re='^.+@sha256:[0-9a-f]{64}$'" in text
    assert "permissions:\n  contents: read\n  packages: write\n  security-events: write" in text


def test_dockerfile_base_images_are_resolver_overridable_and_all_targets_exist() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG NODE_BASE_IMAGE=node:${NODE_VERSION}-bookworm-slim" in dockerfile
    assert "ARG PYTHON_BASE_IMAGE=python:${PYTHON_VERSION}-slim-bookworm" in dockerfile
    assert "ARG NGINX_BASE_IMAGE=nginx:${NGINX_VERSION}-alpine" in dockerfile
    assert dockerfile.count("FROM ${PYTHON_BASE_IMAGE}") >= 3
    assert "FROM ${NODE_BASE_IMAGE} AS frontend-build" in dockerfile
    assert "FROM ${NGINX_BASE_IMAGE} AS frontend-image" in dockerfile
    for role in RELEASE_IMAGE_ROLES:
        assert f"AS {role}-image" in dockerfile


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

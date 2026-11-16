"""Check the 406 no-argument contract and source manifest through 0056."""
from __future__ import annotations

import shutil
import subprocess

import pytest
import test_preflight391 as prior


REVISION = "0056_closure_session_cleanup"
EXTRA_0056 = (
    "refactor/server/migrations/versions/0055_purge_deleted_plan_progress.py",
    "refactor/server/migrations/versions/0056_closure_session_cleanup.py",
    "refactor/server-java/deploy/deploy406.sh",
    "refactor/server-java/deploy/rollback406.sh",
    "refactor/server-java/deploy/preflight406.sh",
    "refactor/server-java/deploy/cleanup-docker406.sh",
    "refactor/server-java/deploy/finalize406.py",
)
PATHS_0056 = (*prior.PATHS_0054, *EXTRA_0056)


class Preflight406(prior.Preflight401):
    def _setup_files(self):
        super()._setup_files()
        for rel in EXTRA_0056:
            target = self.release / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(prior.legacy.ROOT / rel, target)
        self.write_manifest(self.manifest_lines(PATHS_0056))
        self.write_deploy_env({
            "MF_JAVA_DEPLOY_DIR": prior.legacy.posix(self.deploy),
            "MF_JAVA_TRUSTSTORE_DIR": prior.legacy.posix(self.trust),
            "MF_OIDC_PROXY_IMAGE": "mentorflow-local/oidc:current",
            "MF_OIDC_PROXY_CONFIG": prior.legacy.posix(self.proxy_config),
            "MF_JAVA_VERSION": "406",
        })

    def env(self, **overrides):
        values = super().env()
        values.update(JAVA_IMAGE="mentorflow-java-server:406",
                      EXPECTED_ALEMBIC_REVISION=REVISION,
                      PREFLIGHT_SOURCE_REVISION=REVISION,
                      FAKE_DB_REVISION=REVISION)
        values.update(overrides)
        return values

    def _setup_fake_docker(self):
        super()._setup_fake_docker()
        source = self.fake_docker.read_text(encoding="utf-8")
        source = source.replace("echo mentorflow-java-server:401", 'echo "${FAKE_JAVA_IMAGE:-mentorflow-java-server:406}"')
        source = source.replace('      echo "mentorflow|${FAKE_DB_REVISION:-0052_workbench_account_rows}|$schema_ok"',
                                '      case "$*" in *"closure_sessions_read"*) [ "${FAKE_0056_POLICIES:-true}" = true ] || schema_ok=false ;; esac\n'
                                '      case "$*" in *"native_session_families"*) [ "${FAKE_0056_POLICIES:-true}" = true ] || schema_ok=false ;; esac\n'
                                '      echo "mentorflow|${FAKE_DB_REVISION:-0052_workbench_account_rows}|$schema_ok"')
        self.fake_docker.write_text(source, encoding="utf-8", newline="\n")


@pytest.fixture
def preflight406(tmp_path):
    return Preflight406(tmp_path / "preflight406")


def run_406(preflight, *args, **overrides):
    wrapper = prior.legacy.ROOT / "refactor/server-java/deploy/preflight406.sh"
    return subprocess.run(
        [str(prior.legacy.BASH), "-c", 'script=$1; shift; sh "$script" "$@"', "_", prior.legacy.posix(wrapper), *args],
        env=preflight.env(**overrides), capture_output=True,
    )


def test_preflight406_manifest_and_defaults_cover_0056(preflight406):
    wrapper = prior.legacy.ROOT / "refactor/server-java/deploy/preflight406.sh"
    source = wrapper.read_text(encoding="ascii")
    assert "mentorflow-java-server:406" in source
    assert f"EXPECTED_ALEMBIC_REVISION:-{REVISION}" in source
    result = run_406(preflight406, "--source-manifest")
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    lines = result.stdout.decode("ascii").splitlines()
    assert sorted(lines) == sorted(preflight406.manifest_lines(PATHS_0056))
    assert len(lines) == len(set(lines))


def test_preflight406_checks_final_schema_and_receipt_cleanup(preflight406):
    result = run_406(preflight406)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    calls = preflight406.log.read_text(encoding="utf-8")
    assert "closure_sessions_read" in calls
    assert "native_session_families" in calls
    assert "plan_progress_scrub_0055" in calls
    assert "response::jsonb ? 'payload'" in calls


def test_preflight406_uses_candidate_code_with_393_runtime_config(preflight406, tmp_path):
    wrapper = prior.legacy.ROOT / "refactor/server-java/deploy/preflight406.sh"
    online = tmp_path / "online393/deploy"
    online.mkdir(parents=True)
    for name in (".env", ".java.env", "compose.generated.yml", "build.sh", "migrate.sh",
                 "migration.Dockerfile", "java-compose.sh", "python-compose.sh", "probe.py", "compose.yml"):
        shutil.copyfile(preflight406.deploy / name, online / name)
    shutil.copyfile(prior.legacy.ROOT / "refactor/server-java/deploy/preflight.sh", online / "preflight.sh")
    (online / "cutover.sh").write_text("#!/bin/sh\necho java\n", encoding="ascii")
    (online / ".env").write_text(
        f"MF_JAVA_DEPLOY_DIR={prior.legacy.posix(online)}\n"
        f"MF_JAVA_TRUSTSTORE_DIR={prior.legacy.posix(preflight406.trust)}\n"
        "MF_OIDC_PROXY_IMAGE=mentorflow-local/oidc:current\n"
        f"MF_OIDC_PROXY_CONFIG={prior.legacy.posix(preflight406.proxy_config)}\nMF_JAVA_VERSION=393\n",
        encoding="utf-8",
    )
    env = preflight406.env(
        DEPLOY_DIR=prior.legacy.posix(online), JAVA_IMAGE="mentorflow-java-server:393",
        EXPECTED_ALEMBIC_REVISION="0053_workbench_question_index",
        PREFLIGHT_SOURCE_REVISION=REVISION, FAKE_DB_REVISION="0053_workbench_question_index",
        FAKE_JAVA_IMAGE="mentorflow-java-server:393",
    )
    result = subprocess.run([str(prior.legacy.BASH), "-c", 'sh "$1"', "_", prior.legacy.posix(wrapper)],
                            env=env, capture_output=True)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    assert "PREFLIGHT_FAILURES=0" in output


def test_preflight406_rejects_missing_closure_session_policy(preflight406):
    result = run_406(preflight406, FAKE_0056_POLICIES="false")
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0
    assert "postgres_schema_revision_and_table FAIL" in output

"""Current deployment defaults, approved sources and retained 0053 checks."""
from __future__ import annotations

import shutil

import pytest
import test_preflight277 as legacy

REVISION = "0053_workbench_question_index"
REVISION_0054 = "0054_sync_receipts_without_payload"
INDEX = "structured_sync_entities_question_bank_issuer_subject_idx"
EXTRA = (
    "refactor/server/migrations/versions/0053_workbench_question_index.py",
    "refactor/server-java/deploy/deploy391.sh",
    "refactor/server-java/deploy/rollback391.sh",
    "refactor/server-java/deploy/deploy393.sh",
    "refactor/server-java/deploy/rollback393.sh",
    "refactor/server-java/deploy/monitoring/healthchecks-send.sh",
    "refactor/server-java/deploy/edge/edge-logging393.conf",
    "refactor/server-java/deploy/edge/edge-config393.py",
    "refactor/server-java/deploy/edge/install-edge-logging393.sh",
    "refactor/server-java/deploy/edge/rotate-edge-access393.sh",
)
PATHS = (*legacy.SOURCE_PATHS_0052, *EXTRA)


class Preflight391(legacy.PreflightFixture):
    def _setup_files(self):
        super()._setup_files()
        for rel in EXTRA:
            target = self.release / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(legacy.ROOT / rel, target)
        self.write_deploy_env({
            "MF_JAVA_DEPLOY_DIR": legacy.posix(self.deploy),
            "MF_JAVA_TRUSTSTORE_DIR": legacy.posix(self.trust),
            "MF_OIDC_PROXY_IMAGE": "mentorflow-local/oidc:current",
            "MF_OIDC_PROXY_CONFIG": legacy.posix(self.proxy_config),
            "MF_JAVA_VERSION": "393",
        })
        self.write_manifest(self.manifest_lines(PATHS))

    def _setup_fake_docker(self):
        super()._setup_fake_docker()
        source = self.fake_docker.read_text(encoding="utf-8")
        source = source.replace("echo mentorflow-java-server:0aefe606", "echo mentorflow-java-server:393")
        anchor = '      echo "mentorflow|${FAKE_DB_REVISION:-0052_workbench_account_rows}|$schema_ok"'
        assert source.count(anchor) == 1
        check = ('      case "$*" in *"' + INDEX + '"*) '
                 '[ "${FAKE_QUESTION_INDEX_STATE:-valid}" = valid ] || schema_ok=false ;; esac\n')
        self.fake_docker.write_text(source.replace(anchor, check + anchor), encoding="utf-8", newline="\n")

    def env(self, **overrides):
        values = super().env()
        values.update(JAVA_IMAGE="mentorflow-java-server:393",
                      EXPECTED_ALEMBIC_REVISION=REVISION,
                      PREFLIGHT_SOURCE_REVISION=REVISION,
                      FAKE_DB_REVISION=REVISION)
        values.update(overrides)
        return values


@pytest.fixture
def preflight391(tmp_path):
    return Preflight391(tmp_path / "preflight391")


def test_preflight401_defaults_match_current_release():
    source = legacy.PREFLIGHT.read_text(encoding="utf-8")
    for setting in (
        "release_root=${RELEASE_ROOT:-/opt/mentorflow/releases/java-401}",
        "deploy_dir=${DEPLOY_DIR:-/opt/mentorflow/releases/java-401/refactor/server-java/deploy}",
        "source_manifest=${SOURCE_SHA256_MANIFEST:-/opt/mentorflow/state/release401/source.sha256}",
        "java_image=${JAVA_IMAGE:-mentorflow-java-server:401}",
        "expected_revision=${EXPECTED_ALEMBIC_REVISION:-" + REVISION_0054 + "}",
        "source_revision=${PREFLIGHT_SOURCE_REVISION:-" + REVISION_0054 + "}",
    ):
        assert setting in source


def test_preflight391_source_manifest_is_complete_unique_and_offline(preflight391):
    result = preflight391.run(("--source-manifest",))
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    lines = result.stdout.decode("ascii").splitlines()
    assert sorted(lines) == sorted(preflight391.manifest_lines(PATHS))
    assert len(lines) == len(set(lines)) == 37
    assert not preflight391.log.exists()


def test_preflight391_accepts_valid_index_and_keeps_prior_schema_checks(preflight391):
    result = preflight391.run()
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    assert "PREFLIGHT_FAILURES=0" in output
    calls = preflight391.log.read_text(encoding="utf-8")
    for required in (INDEX, "indisvalid", "rag_source_chunks", "account_daily_activity",
                     "ix_rag_chunks_account_document", "account_closure.assert_open", "mentorflow_admin_account_rows"):
        assert required in calls


@pytest.mark.parametrize("state", ["absent", "invalid"])
def test_preflight391_rejects_missing_or_invalid_index(preflight391, state):
    result = preflight391.run(FAKE_QUESTION_INDEX_STATE=state)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0
    assert "postgres_schema_revision_and_table FAIL" in output


REVISION_0054_PATHS = (
    "refactor/server/migrations/versions/0054_sync_receipts_without_payload.py",
    "refactor/server-java/src/main/java/win/clannad/mentorflow/sync/SyncRepository.java",
    "refactor/server-java/deploy/deploy401.sh",
    "refactor/server-java/deploy/rollback401.sh",
    "refactor/server-java/deploy/storage-report.sh",
)
PATHS_0054 = (*PATHS, *REVISION_0054_PATHS)


class Preflight401(Preflight391):
    def _setup_files(self):
        super()._setup_files()
        for rel in REVISION_0054_PATHS:
            target = self.release / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(legacy.ROOT / rel, target)
        self.write_manifest(self.manifest_lines(PATHS_0054))
        self.write_deploy_env({
            "MF_JAVA_DEPLOY_DIR": legacy.posix(self.deploy),
            "MF_JAVA_TRUSTSTORE_DIR": legacy.posix(self.trust),
            "MF_OIDC_PROXY_IMAGE": "mentorflow-local/oidc:current",
            "MF_OIDC_PROXY_CONFIG": legacy.posix(self.proxy_config),
            "MF_JAVA_VERSION": "401",
        })

    def env(self, **overrides):
        values = super().env()
        values.update(
            JAVA_IMAGE="mentorflow-java-server:401",
            EXPECTED_ALEMBIC_REVISION=REVISION_0054,
            PREFLIGHT_SOURCE_REVISION=REVISION_0054,
            FAKE_DB_REVISION=REVISION_0054,
        )
        values.update(overrides)
        return values

    def _setup_fake_docker(self):
        super()._setup_fake_docker()
        source = self.fake_docker.read_text(encoding="utf-8")
        source = source.replace("echo mentorflow-java-server:393", "echo mentorflow-java-server:401")
        source = source.replace("echo mentorflow-java-server:0aefe606", "echo mentorflow-java-server:401")
        source = source.replace('      echo "mentorflow|${FAKE_DB_REVISION:-0052_workbench_account_rows}|$schema_ok"',
                                '      case "$*" in *"receipt_scrub_0054"*) [ "${FAKE_0054_POLICY_GONE:-true}" = true ] || schema_ok=false ;; esac\n'
                                '      case "$*" in *"response::jsonb ? \'payload\'"*) [ "${FAKE_0054_PAYLOAD_GONE:-true}" = true ] || schema_ok=false ;; esac\n'
                                '      echo "mentorflow|${FAKE_DB_REVISION:-0052_workbench_account_rows}|$schema_ok"')
        self.fake_docker.write_text(source, encoding="utf-8", newline="\n")


@pytest.fixture
def preflight401(tmp_path):
    return Preflight401(tmp_path / "preflight401")


def test_preflight401_defaults_and_manifest_are_0054(preflight401):
    source = legacy.PREFLIGHT.read_text(encoding="utf-8")
    for setting in (
        "release_root=${RELEASE_ROOT:-/opt/mentorflow/releases/java-401}",
        "source_manifest=${SOURCE_SHA256_MANIFEST:-/opt/mentorflow/state/release401/source.sha256}",
        "java_image=${JAVA_IMAGE:-mentorflow-java-server:401}",
        "expected_revision=${EXPECTED_ALEMBIC_REVISION:-" + REVISION_0054 + "}",
        "source_revision=${PREFLIGHT_SOURCE_REVISION:-" + REVISION_0054 + "}",
    ):
        assert setting in source
    result = preflight401.run(("--source-manifest",))
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    lines = result.stdout.decode("ascii").splitlines()
    assert sorted(lines) == sorted(preflight401.manifest_lines(PATHS_0054))
    assert len(lines) == len(set(lines)) == 42
    assert not preflight401.log.exists()


def test_preflight401_requires_scrubbed_receipts_and_removed_policy(preflight401):
    result = preflight401.run()
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    assert "PREFLIGHT_FAILURES=0" in output
    calls = preflight401.log.read_text(encoding="utf-8")
    assert "structured_sync_requests" in calls
    assert "receipt_scrub_0054" in calls
    assert "response::jsonb ? 'payload'" in calls


@pytest.mark.parametrize("override", [
    {"FAKE_0054_POLICY_GONE": "false"},
    {"FAKE_0054_PAYLOAD_GONE": "false"},
])
def test_preflight401_rejects_incomplete_scrub(preflight401, override):
    result = preflight401.run(**override)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0
    assert "postgres_schema_revision_and_table FAIL" in output

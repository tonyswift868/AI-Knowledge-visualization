"""Read-only deployment preflight contract exercised with fake Docker."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
PREFLIGHT = ROOT / "refactor" / "server-java" / "deploy" / "preflight.sh"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
HASH_PATHS = (
    "refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java",
    "refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagRepository.java",
    "refactor/server-java/src/main/java/win/clannad/mentorflow/rag/PgVectorStore.java",
    "refactor/server-java/Dockerfile",
    "refactor/server/Dockerfile",
    "refactor/server/enterprise_tenancy/schema.py",
    "refactor/server/migrations/versions/0047_rag_promoted_manifests.py",
    "refactor/server-java/deploy/build.sh",
    "refactor/server-java/deploy/migrate.sh",
    "refactor/server-java/deploy/java-compose.sh",
    "refactor/server-java/deploy/python-compose.sh",
    "refactor/server-java/deploy/probe.py",
    "refactor/server-java/deploy/entrypoint.sh",
    "refactor/server-java/deploy/compose.yml",
)
SOURCE_REVISION = "PREFLIGHT_SOURCE_REVISION"
SOURCE_PATH_0048 = "refactor/server/migrations/versions/0048_rag_source_chunks.py"
SOURCE_PATHS_0048 = (*HASH_PATHS, SOURCE_PATH_0048)
SOURCE_PATH_0049 = "refactor/server/migrations/versions/0049_drop_redundant_rag_indexes.py"
SOURCE_PATH_0050 = "refactor/server/migrations/versions/0050_account_daily_activity.py"
SOURCE_PATH_0051 = "refactor/server/migrations/versions/0051_sync_closure_fence.py"
SOURCE_PATH_0052 = "refactor/server/migrations/versions/0052_workbench_account_rows.py"
SOURCE_PATHS_0052_EXTRA = (
    "refactor/server-java/src/main/java/win/clannad/mentorflow/admin/AdminOverviewService.java",
    "refactor/server-java/src/main/java/win/clannad/mentorflow/admin/AdminAccountPageController.java",
    "refactor/server-java/src/main/java/win/clannad/mentorflow/admin/AdminAccountPageConfiguration.java",
    "refactor/server-java/deploy/deploy388.sh",
)
SOURCE_PATHS_0049 = (*SOURCE_PATHS_0048, SOURCE_PATH_0049)
SOURCE_PATHS_0050 = (*SOURCE_PATHS_0049, SOURCE_PATH_0050)
SOURCE_PATHS_0051 = (*SOURCE_PATHS_0050, SOURCE_PATH_0051)
SOURCE_PATHS_0052 = (
    *SOURCE_PATHS_0051,
    SOURCE_PATH_0052,
    *SOURCE_PATHS_0052_EXTRA,
)
CONTAINERS = {
    "mentorflow-java-backend": ("running", "mentorflow-java-server:0aefe606"),
    "mentorflow-java-oidc": ("running", "mentorflow-local/oidc:current"),
    "mentorflow-e1-preview-backend-1": ("exited", "mentorflow-slim-backend:269-python-fallback"),
    "mentorflow-e1-preview-oidc-loopback-proxy-1": ("exited", "mentorflow-local/oidc:current"),
    "mentorflow-e1-preview-edge-1": ("running", "mentorflow-local/edge:current"),
    "mentorflow-e1-preview-postgres-1": ("running", "mentorflow-postgres:17"),
}


def posix(path: Path) -> str:
    value = path.resolve().as_posix()
    if len(value) >= 2 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


class PreflightFixture:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.release = tmp_path / "release"
        self.deploy = self.release / "refactor/server-java/deploy"
        self.server = self.release / "refactor/server"
        self.state = tmp_path / "state"
        self.runtime = tmp_path / "runtime.env"
        self.edge_runtime = tmp_path / "edge-runtime.env"
        self.network45 = tmp_path / "network45.override.yml"
        self.proxy_config = tmp_path / "proxy.conf"
        self.trust = tmp_path / "trust"
        self.sources = tmp_path / "approved-source"
        self.secrets = tmp_path / "migration-secrets"
        self.edge_conf = tmp_path / "edge.conf"
        self.volume = tmp_path / "volume"
        self.log = tmp_path / "fake-docker.log"
        self.fake_docker = tmp_path / "docker"
        self.fake_curl = tmp_path / "curl"
        self._setup_files()
        self._setup_fake_docker()
        self._setup_fake_curl()

    def _setup_files(self):
        for path in (self.deploy, self.server / "migrations/versions", self.state, self.sources,
                     self.secrets, self.trust, self.volume):
            path.mkdir(parents=True, exist_ok=True)
        for name in (".java.env", "compose.generated.yml", "migration.Dockerfile", "cutover.sh",
                     "build.sh", "migrate.sh", "java-compose.sh", "python-compose.sh", "probe.py",
                     "entrypoint.sh", "compose.yml"):
            target = self.deploy / name
            target.write_text("fixture\n", encoding="utf-8")
        (self.deploy / "build.sh").write_text('printf "MF_JAVA_VERSION=%s\\n" "$version" >> "$env_file"\n', encoding="utf-8")
        (self.deploy / "java-compose.sh").write_text('if [ "$action" = config ]; then exit 0; fi\n', encoding="utf-8")
        (self.deploy / "cutover.sh").write_text("#!/bin/sh\necho java\n", encoding="utf-8")
        (self.server / "alembic.ini").write_text("fixture\n", encoding="utf-8")
        for item in ("lifecycle", "native-mtls", "catalog", "tls", "secrets"):
            folder = self.root / item
            folder.mkdir(exist_ok=True)
            (folder / "present.pem").write_text("fixture\n", encoding="utf-8")
        (self.trust / "trust.p12").write_text("fixture\n", encoding="utf-8")
        self.runtime.write_text("RUNTIME_KEY=fixture\n", encoding="utf-8")
        self.edge_runtime.write_text("EDGE_RUNTIME_KEY=fixture\n", encoding="utf-8")
        self.network45.write_text("networks: {}\n", encoding="utf-8")
        self.proxy_config.write_text("server {}\n", encoding="utf-8")
        self.edge_conf.write_text("upstream mentorflow_account_core_backend {\n  server java-backend:8080 resolve;\n}\n", encoding="utf-8")
        (self.secrets / "postgres_migrator_password").write_text("fixture-secret\n", encoding="utf-8")
        (self.state / "source.sha256").write_text("placeholder\n", encoding="utf-8")
        (self.volume / "present.pem").write_text("fixture\n", encoding="utf-8")

        env = {
            "MF_JAVA_DEPLOY_DIR": posix(self.deploy),
            "MF_JAVA_TRUSTSTORE_DIR": posix(self.trust),
            "MF_OIDC_PROXY_IMAGE": "mentorflow-local/oidc:current",
            "MF_OIDC_PROXY_CONFIG": posix(self.proxy_config),
            "MF_JAVA_VERSION": "0aefe606",
        }
        self.write_deploy_env(env)
        for rel in HASH_PATHS:
            target = self.release / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, target)
        self.write_synthetic_source_0048()
        self.write_synthetic_source_0049_0052()
        self.manifest = self.state / "source.sha256"
        self.write_manifest(self.manifest_lines(SOURCE_PATHS_0052))

    def write_synthetic_source_0048(self):
        source = self.release / SOURCE_PATH_0048
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# Synthetic 0048 source manifest fixture.\n", encoding="ascii")

    def write_synthetic_source_0049_0052(self):
        for rel in (SOURCE_PATH_0049, SOURCE_PATH_0050, SOURCE_PATH_0051, SOURCE_PATH_0052):
            source = self.release / rel
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(f"# Synthetic {rel.rsplit('/', 1)[-1]} fixture.\n", encoding="ascii")
        for rel in SOURCE_PATHS_0052_EXTRA:
            source = self.release / rel
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(f"# Synthetic {rel.rsplit('/', 1)[-1]} fixture.\n", encoding="ascii")

    def manifest_lines(self, paths: tuple[str, ...]) -> list[str]:
        return [
            f"{hashlib.sha256((self.release / rel).read_bytes()).hexdigest()}  {rel}"
            for rel in paths
        ]

    def write_manifest(self, lines: list[str], *, separator: str = "\n", final_newline: bool = True):
        text = separator.join(lines) + (separator if final_newline else "")
        self.manifest.write_bytes(text.encode("ascii"))

    def write_deploy_env(self, values: dict[str, str]):
        (self.deploy / ".env").write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")

    def _setup_fake_docker(self):
        fake = r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1 $2" in
  'info ') exit 0 ;;
  'compose version') echo '2.39.2'; exit 0 ;;
  'compose --project-directory'*) case "$*" in *'config --quiet'*) exit 0 ;; esac ;;
  'image inspect')
    if [ -n "${FAKE_MISSING_IMAGE:-}" ]; then
      case "$*" in *"$FAKE_MISSING_IMAGE"*) exit 1 ;; esac
    fi
    echo 'sha256:fixture'; exit 0 ;;
  'network inspect')
    case "$*" in *'Name'* ) echo 'mentorflow-e1-preview_internal|172.18.0.0/16' ;; *) echo '172.18.0.0/16' ;; esac
    exit 0 ;;
  'volume inspect')
    if [ "${FAKE_MISSING_VOLUME:-0}" = 1 ]; then exit 1; fi
    echo "$FAKE_VOLUME_PATH"; exit 0 ;;
  'inspect --format')
    fmt=$3; container=$4
    case "$fmt" in
      *'.State.Status'*) case "$container" in
        mentorflow-java-backend) echo running ;;
        mentorflow-e1-preview-backend-1|mentorflow-e1-preview-oidc-loopback-proxy-1) echo exited ;;
        *) echo running ;;
      esac ;;
      *'.Config.Image'*) case "$container" in
        mentorflow-java-backend) echo mentorflow-java-server:0aefe606 ;;
        mentorflow-e1-preview-backend-1) echo mentorflow-slim-backend:269-python-fallback ;;
        mentorflow-java-oidc|mentorflow-e1-preview-oidc-loopback-proxy-1) echo mentorflow-local/oidc:current ;;
        mentorflow-e1-preview-edge-1) echo mentorflow-local/edge:current ;;
        *) echo mentorflow-postgres:17 ;;
      esac ;;
      *'project.environment_file'*) if [ "$container" = mentorflow-e1-preview-edge-1 ]; then
          printf '%s\n' "$FAKE_EDGE_ENV"
        else printf '%s,%s\n' "$FAKE_BACKEND_ENV" "$FAKE_DEPLOY_ENV"; fi ;;
      *'project.config_files'*) if [ "$container" = mentorflow-e1-preview-edge-1 ]; then
          printf '%s\n' "$FAKE_COMPOSE_FILE"
        else printf '%s,%s,%s\n' "$FAKE_COMPOSE_FILE" "$FAKE_DEPLOY_ENV" "$FAKE_NETWORK45"; fi ;;
      *'project.working_dir'*) echo "$FAKE_WORKING_DIR" ;;
      *'compose.project'*) echo mentorflow-e1-preview ;;
      *'.Id'*) echo 'java-backend-id' ;;
      *'NetworkMode'*) echo 'container:java-backend-id' ;;
      *'HostConfig.Tmpfs'*) echo 'rw,exec,size=134217728,mode=1777' ;;
      *'Mounts'*)
        for mount in "$FAKE_RUNTIME_DIR|/run/secrets|bind|false|" \
          "$FAKE_NATIVE_DIR|/run/mentorflow/native-mtls|bind|false|" \
          "$FAKE_LIFECYCLE_DIR|/run/mentorflow/lifecycle|bind|false|" \
          "$FAKE_TRUST_DIR|/run/mentorflow/trust|bind|false|" \
          "$FAKE_CATALOG_DIR|/run/mentorflow/catalog|bind|false|" \
          "$FAKE_TLS_DIR|/run/mentorflow/tls|bind|false|"; do echo "$mount"; done
        case "$container" in
          mentorflow-e1-preview-backend-1) echo "$FAKE_VOLUME_PATH|/var/lib/mentorflow|volume|true|mentorflow-backend-data" ;;
          mentorflow-e1-preview-postgres-1) echo "$FAKE_VOLUME_PATH|/var/lib/postgresql/data|volume|true|mentorflow-postgres-data" ;;
          mentorflow-java-oidc|mentorflow-e1-preview-oidc-loopback-proxy-1) echo "$FAKE_PROXY_CONFIG|/etc/nginx/nginx.conf|bind|false|" ;;
        esac ;;
      *) exit 40 ;;
    esac
    exit 0 ;;
  'run --pull=never')
    case "$*" in
      *'--network none'*)
        if [ "${FAKE_MISSING_SECRET:-0}" = 1 ]; then exit 1; fi
        echo 'present bytes=20 mode=400'; exit 0 ;;
      *'current'*)
        if [ "${FAKE_CURRENT_WRONG:-0}" = 1 ]; then echo 'Current revision(s): 0046_account_profiles'; else echo "Current revision(s): ${FAKE_DB_REVISION:-0052_workbench_account_rows} (head)"; fi
        exit 0 ;;
    esac ;;
  'exec mentorflow-e1-preview-postgres-1')
    case "$*" in *'psql -XqAt'*)
      schema_ok=true
      case "$*" in *"to_regclass('public.rag_source_chunks')"*) schema_ok=${FAKE_DB_SOURCE_TABLE:-true} ;; esac
      case "$*" in *"to_regclass('public.ix_rag_chunks_account_document')"*) schema_ok=${FAKE_DB_REDUNDANT_INDEXES_GONE:-true} ;; esac
      case "$*" in *"to_regclass('public.ix_rag_source_chunks_logical')"*) schema_ok=${FAKE_DB_REDUNDANT_INDEXES_GONE:-true} ;; esac
      case "$*" in *"to_regclass('public.account_daily_activity')"*) schema_ok=${FAKE_DB_DAILY_ACTIVITY:-true} ;; esac
      case "$*" in *"has_function_privilege('mentorflow_app', 'account_closure.assert_open(text,text)'"*) schema_ok=${FAKE_DB_CLOSURE_FENCE:-true} ;; esac
      case "$*" in *"mentorflow_admin_account_rows(text,text[])"*) schema_ok=${FAKE_DB_ACCOUNT_ROWS:-true} ;; esac
      echo "mentorflow|${FAKE_DB_REVISION:-0052_workbench_account_rows}|$schema_ok"; exit 0 ;; *) exit 42 ;; esac ;;
esac
exit 41
'''
        self.fake_docker.write_text(fake, encoding="utf-8", newline="\n")

    def _setup_fake_curl(self):
        self.fake_curl.write_text("#!/bin/sh\nprintf '%s\\n' 200\n", encoding="utf-8", newline="\n")

    def env(self, **overrides):
        values = os.environ.copy()
        values.update({
            "DOCKER_BIN": posix(self.fake_docker),
            "CURL_BIN": posix(self.fake_curl),
            "FAKE_DOCKER_LOG": posix(self.log),
            "FAKE_MISSING_IMAGE": "",
            "FAKE_MISSING_SECRET": "0",
            "FAKE_MISSING_VOLUME": "0",
            "FAKE_VOLUME_PATH": posix(self.volume),
            "FAKE_BACKEND_ENV": posix(self.runtime),
            "FAKE_EDGE_ENV": posix(self.edge_runtime),
            "FAKE_PROXY_CONFIG": posix(self.proxy_config),
            "FAKE_DEPLOY_ENV": posix(self.deploy / ".env"),
            "FAKE_COMPOSE_FILE": posix(self.deploy / "compose.yml"),
            "FAKE_NETWORK45": posix(self.network45),
            "FAKE_WORKING_DIR": posix(self.release),
            "FAKE_RUNTIME_DIR": posix(self.root / "secrets"),
            "FAKE_NATIVE_DIR": posix(self.root / "native-mtls"),
            "FAKE_LIFECYCLE_DIR": posix(self.root / "lifecycle"),
            "FAKE_TRUST_DIR": posix(self.trust),
            "FAKE_CATALOG_DIR": posix(self.root / "catalog"),
            "FAKE_TLS_DIR": posix(self.root / "tls"),
            "RELEASE_ROOT": posix(self.release),
            "DEPLOY_DIR": posix(self.deploy),
            "SERVER_ROOT": posix(self.server),
            "BACKEND_ENV": posix(self.runtime),
            "NETWORK45_OVERRIDE": posix(self.network45),
            "SOURCE_SHA256_MANIFEST": posix(self.manifest),
            "FAKE_DB_REVISION": "0052_workbench_account_rows",
            "FAKE_DB_SOURCE_TABLE": "true",
            "EDGE_CONF_PATH": posix(self.edge_conf),
        })
        values.update(overrides)
        return values

    def run(self, args=(), **overrides):
        return subprocess.run(
            [str(BASH), "-c", 'sh -s -- "$@"', "_", *args], cwd=ROOT, env=self.env(**overrides),
            input=PREFLIGHT.read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60,
        )


@pytest.fixture
def preflight(tmp_path):
    return PreflightFixture(tmp_path / "preflight277")


def test_preflight_sh_syntax_and_full_fake_docker_pass(preflight):
    syntax = subprocess.run([str(BASH), "-n", posix(PREFLIGHT)], capture_output=True, text=True, timeout=20)
    assert syntax.returncode == 0, syntax.stderr
    result = preflight.run()
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    assert len(preflight.manifest.read_text(encoding="ascii").splitlines()) == 23
    assert "docker_daemon OK" in output
    assert "current_java OK" in output
    assert "migration_secret_metadata OK" in output
    assert "database_migration_current OK" in output
    assert "postgres_schema_revision_and_table OK" in output
    assert "java_tmpfs_exec_tmpfs OK" in output
    assert "public_capabilities OK" in output
    assert "PREFLIGHT_FAILURES=0" in output
    calls = preflight.log.read_text(encoding="utf-8")
    assert "--pull=never" in calls
    assert "--read-only" in calls
    assert "PYTHONDONTWRITEBYTECODE=1" in calls
    assert "PGOPTIONS=-c default_transaction_read_only=on" in calls
    assert "--network none" in calls and "--network mentorflow-e1-preview_internal" in calls
    assert "config --quiet" in calls and "--dry-run" not in calls
    assert " up " not in calls and "start" not in calls
    compose_checks = [line for line in calls.splitlines() if line.startswith("compose ") and "config --quiet" in line]
    assert len(compose_checks) == 6
    assert all(line.endswith(f"-f {posix(preflight.network45)} config --quiet") for line in compose_checks), compose_checks
    assert "java_tmpfs_exec_tmpfs OK" in output
    assert " --build " not in calls and "docker build" not in calls
    assert "MF_JAVA_DEPLOY_DIR=" not in output and "postgres_migrator_password=" not in output


def test_preflight_accepts_complete_manifest_with_crlf_and_no_final_newline(preflight):
    lines = preflight.manifest.read_text(encoding="ascii").splitlines()
    preflight.write_manifest(lines, separator="\r\n", final_newline=False)

    result = preflight.run()
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    assert "approved_source_sha256 OK" in output


@pytest.mark.parametrize(
    ("profile", "paths"),
    [
        ("0047_rag_promoted_manifests", HASH_PATHS),
        ("0048_rag_source_chunks", SOURCE_PATHS_0048),
        ("0049_drop_redundant_rag_indexes", SOURCE_PATHS_0049),
        ("0050_account_daily_activity", SOURCE_PATHS_0050),
        ("0051_sync_closure_fence", SOURCE_PATHS_0051),
        ("0052_workbench_account_rows", SOURCE_PATHS_0052),
    ],
)
def test_source_manifest_generation_is_revision_specific_and_offline(preflight, profile, paths):
    result = preflight.run(
        ("--source-manifest",),
        **{SOURCE_REVISION: profile, "RELEASE_ROOT": posix(preflight.release)},
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    generated = result.stdout.decode("ascii").splitlines()
    assert generated == preflight.manifest_lines(paths)
    assert len(generated) == len(paths)
    assert not preflight.log.exists()

    preflight.write_manifest(generated)
    verified = preflight.run(
        **{SOURCE_REVISION: profile, "RELEASE_ROOT": posix(preflight.release)}
    )
    output = (verified.stdout + verified.stderr).decode("utf-8", errors="replace")
    assert verified.returncode == 0, output
    assert "approved_source_sha256 OK" in output


@pytest.mark.parametrize(
    ("args", "overrides"),
    [
        (("--source-manifest",), {SOURCE_REVISION: "unknown"}),
        (("--invalid",), {}),
        (("--source-manifest", "extra"), {}),
    ],
)
def test_source_manifest_rejects_invalid_request_without_docker(preflight, args, overrides):
    result = preflight.run(args, **overrides)
    assert result.returncode == 2
    assert result.stdout == b""
    assert not preflight.log.exists()


def test_source_manifest_rejects_missing_release_root_without_docker(preflight):
    result = preflight.run(
        ("--source-manifest",), RELEASE_ROOT=posix(preflight.root / "absent-release")
    )
    assert result.returncode != 0
    assert result.stdout == b""
    assert not preflight.log.exists()


def test_source_manifest_generation_fails_when_new_migration_file_is_absent(preflight):
    source = preflight.release / SOURCE_PATH_0048
    source.rename(source.with_suffix(".not-in-release"))
    result = preflight.run(
        ("--source-manifest",), **{SOURCE_REVISION: "0048_rag_source_chunks"}
    )
    assert result.returncode != 0
    assert len(result.stdout.decode("ascii").splitlines()) == len(HASH_PATHS)
    assert SOURCE_PATH_0048 in result.stderr.decode("utf-8", errors="replace")
    assert not preflight.log.exists()


def test_release_manifest_generation_can_retry_after_missing_migration(preflight):
    readme = (PREFLIGHT.parent / "README.md").read_text(encoding="utf-8")
    command = next(
        block for block in re.findall(r"```sh\n(.*?)\n```", readme, re.S)
        if "CANDIDATE_SOURCE_MANIFEST.tmp" in block and "--source-manifest" in block
    )
    (preflight.deploy / "preflight.sh").write_bytes(PREFLIGHT.read_bytes())
    candidate = preflight.state / "generated-source.sha256"
    source = preflight.release / SOURCE_PATH_0048
    held_source = source.with_suffix(".not-in-release")
    source.rename(held_source)
    env = preflight.env(
        NEW_RELEASE_ROOT=posix(preflight.release),
        CANDIDATE_SOURCE_MANIFEST=posix(candidate),
    )
    failed = subprocess.run(
        [str(BASH), "-c", command], cwd=ROOT, env=env, capture_output=True, timeout=20
    )
    assert failed.returncode != 0
    assert not candidate.exists()
    assert not candidate.with_suffix(".sha256.tmp").exists()

    held_source.rename(source)
    retried = subprocess.run(
        [str(BASH), "-c", command], cwd=ROOT, env=env, capture_output=True, timeout=20
    )
    assert retried.returncode == 0, retried.stderr.decode("utf-8", errors="replace")
    assert candidate.read_text(encoding="ascii").splitlines() == preflight.manifest_lines(SOURCE_PATHS_0051)
    assert not candidate.with_suffix(".sha256.tmp").exists()
    assert not preflight.log.exists()


def test_preflight_0048_sources_pass_before_database_migration(preflight):
    preflight.write_manifest(preflight.manifest_lines(SOURCE_PATHS_0048))
    result = preflight.run(**{SOURCE_REVISION: "0048_rag_source_chunks"})

    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    assert "approved_source_sha256 OK" in output
    assert "database_migration_current OK" in output
    assert "postgres_schema_revision_and_table OK" in output
    assert "to_regclass('public.rag_source_chunks')" not in preflight.log.read_text(encoding="utf-8")


def test_preflight_0048_sources_and_database_pass_after_migration(preflight):
    preflight.write_manifest(preflight.manifest_lines(SOURCE_PATHS_0048))
    result = preflight.run(
        FAKE_DB_REVISION="0048_rag_source_chunks",
        EXPECTED_ALEMBIC_REVISION="0048_rag_source_chunks",
        **{SOURCE_REVISION: "0048_rag_source_chunks"},
    )

    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    assert "approved_source_sha256 OK" in output
    assert "database_migration_current OK" in output
    assert "postgres_schema_revision_and_table OK" in output
    assert "to_regclass('public.rag_source_chunks')" in preflight.log.read_text(encoding="utf-8")


def test_preflight_0048_source_does_not_replace_database_revision_expectation(preflight):
    preflight.write_manifest(preflight.manifest_lines(SOURCE_PATHS_0048))
    result = preflight.run(
        EXPECTED_ALEMBIC_REVISION="0048_rag_source_chunks",
        **{SOURCE_REVISION: "0048_rag_source_chunks"},
    )

    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert "approved_source_sha256 OK" in output
    assert "database_migration_current FAIL:" in output
    assert "postgres_schema_revision_and_table FAIL:" in output


def test_preflight_0048_requires_source_chunks_table(preflight):
    preflight.write_manifest(preflight.manifest_lines(SOURCE_PATHS_0048))
    result = preflight.run(
        FAKE_DB_REVISION="0048_rag_source_chunks",
        FAKE_DB_SOURCE_TABLE="false",
        EXPECTED_ALEMBIC_REVISION="0048_rag_source_chunks",
        **{SOURCE_REVISION: "0048_rag_source_chunks"},
    )

    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert "approved_source_sha256 OK" in output
    assert "database_migration_current OK" in output
    assert "postgres_schema_revision_and_table FAIL:" in output


@pytest.mark.parametrize(
    ("revision", "paths", "checks"),
    [
        (
            "0049_drop_redundant_rag_indexes",
            SOURCE_PATHS_0049,
            ("to_regclass('public.ix_rag_chunks_account_document')", "to_regclass('public.ix_rag_source_chunks_logical')"),
        ),
        ("0050_account_daily_activity", SOURCE_PATHS_0050, ("to_regclass('public.account_daily_activity')",)),
        ("0051_sync_closure_fence", SOURCE_PATHS_0051, ("has_function_privilege('mentorflow_app'",)),
        ("0052_workbench_account_rows", SOURCE_PATHS_0052, ("account_closure.assert_open(text,text)", "mentorflow_admin_account_rows(text,text[])")),
    ],
)
def test_preflight_0049_to_0051_check_migration_effects(preflight, revision, paths, checks):
    preflight.write_manifest(preflight.manifest_lines(paths))
    result = preflight.run(
        FAKE_DB_REVISION=revision,
        EXPECTED_ALEMBIC_REVISION=revision,
        **{SOURCE_REVISION: revision},
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    log = preflight.log.read_text(encoding="utf-8")
    for check in checks:
        assert check in log


def test_preflight_0051_requires_closure_fence_grant(preflight):
    preflight.write_manifest(preflight.manifest_lines(SOURCE_PATHS_0051))
    result = preflight.run(
        FAKE_DB_REVISION="0051_sync_closure_fence",
        EXPECTED_ALEMBIC_REVISION="0051_sync_closure_fence",
        FAKE_DB_CLOSURE_FENCE="false",
        **{SOURCE_REVISION: "0051_sync_closure_fence"},
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert "postgres_schema_revision_and_table FAIL:" in output


@pytest.mark.parametrize("defect", ["missing", "duplicate", "wrong_hash", "extra"])
def test_preflight_0048_manifest_rejects_invalid_set(preflight, defect):
    lines = preflight.manifest_lines(SOURCE_PATHS_0048)
    new_source_line = next(line for line in lines if line.endswith(SOURCE_PATH_0048))
    if defect == "missing":
        lines.remove(new_source_line)
    elif defect == "duplicate":
        duplicate = next(line for line in lines if line.endswith(HASH_PATHS[7]))
        lines.remove(new_source_line)
        lines.append(duplicate)
    elif defect == "wrong_hash":
        digest, path = new_source_line.split("  ", 1)
        changed = ("0" if digest[0] != "0" else "1") + digest[1:]
        lines[lines.index(new_source_line)] = f"{changed}  {path}"
    else:
        extra_path = "refactor/server-java/deploy/unapproved.txt"
        extra_file = preflight.release / extra_path
        extra_file.write_text("fixture\n", encoding="ascii")
        lines.append(f"{hashlib.sha256(extra_file.read_bytes()).hexdigest()}  {extra_path}")
    preflight.write_manifest(lines)

    result = preflight.run(**{SOURCE_REVISION: "0048_rag_source_chunks"})
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert "approved_source_sha256 FAIL:" in output


@pytest.mark.parametrize("missing_path", HASH_PATHS)
def test_preflight_rejects_each_missing_manifest_path(preflight, missing_path):
    lines = preflight.manifest.read_text(encoding="ascii").splitlines()
    preflight.write_manifest([line for line in lines if not line.endswith(missing_path)])

    result = preflight.run()
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert "approved_source_sha256 FAIL:" in output


@pytest.mark.parametrize("missing_path", HASH_PATHS)
def test_preflight_rejects_each_path_replaced_by_duplicate(preflight, missing_path):
    lines = preflight.manifest.read_text(encoding="ascii").splitlines()
    missing_line = next(line for line in lines if line.endswith(missing_path))
    duplicate_path = next(path for path in HASH_PATHS if path != missing_path)
    duplicate_line = next(line for line in lines if line.endswith(duplicate_path))
    lines.remove(missing_line)
    lines.append(duplicate_line)
    preflight.write_manifest(lines)
    assert len(preflight.manifest.read_text(encoding="ascii").splitlines()) == len(HASH_PATHS)

    result = preflight.run()
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert "approved_source_sha256 FAIL:" in output


def test_preflight_rejects_duplicate_build_for_missing_base_path(preflight):
    lines = preflight.manifest.read_text(encoding="ascii").splitlines()
    migration_path = "refactor/server-java/deploy/probe.py"
    build_line = next(line for line in lines if line.endswith("refactor/server-java/deploy/build.sh"))
    lines = [line for line in lines if not line.endswith(migration_path)]
    preflight.write_manifest([*lines, build_line])
    assert len(preflight.manifest.read_text(encoding="ascii").splitlines()) == len(HASH_PATHS)

    result = preflight.run()
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert "approved_source_sha256 FAIL:" in output


@pytest.mark.parametrize("entries", ["extra", "duplicate"])
def test_preflight_rejects_extra_or_duplicate_manifest_entry(preflight, entries):
    lines = preflight.manifest.read_text(encoding="ascii").splitlines()
    first_line = lines[0]
    if entries == "extra":
        extra_path = "refactor/server-java/deploy/unapproved.txt"
        extra_file = preflight.release / extra_path
        extra_file.write_text("fixture\n", encoding="ascii")
        lines.append(f"{hashlib.sha256(extra_file.read_bytes()).hexdigest()}  {extra_path}")
    else:
        lines.append(first_line)
    preflight.write_manifest(lines)

    result = preflight.run()
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert "approved_source_sha256 FAIL:" in output


def test_preflight_rejects_wrong_manifest_hash(preflight):
    lines = preflight.manifest.read_text(encoding="ascii").splitlines()
    digest, path = lines[0].split("  ", 1)
    changed = ("0" if digest[0] != "0" else "1") + digest[1:]
    lines[0] = f"{changed}  {path}"
    preflight.write_manifest(lines)

    result = preflight.run()
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert "approved_source_sha256 FAIL:" in output


@pytest.mark.parametrize(
    ("overrides", "project_name"),
    [
        ({"FAKE_MISSING_SECRET": "1"}, "migration_secret_metadata"),
        ({"FAKE_MISSING_IMAGE": "mentorflow-migration-tool:MF-275"}, "database_migration_current"),
        ({"FAKE_MISSING_VOLUME": "1"}, "migration_secrets_volume"),
        ({"REMOVE_JAVA_DEPLOY_KEY": "1"}, "env_MF_JAVA_DEPLOY_DIR"),
    ],
)
def test_preflight_reports_specific_failures(preflight, overrides, project_name):
    if overrides.pop("REMOVE_JAVA_DEPLOY_KEY", None):
        env = {}
        for line in (preflight.deploy / ".env").read_text(encoding="utf-8").splitlines():
            key, value = line.split("=", 1)
            if key != "MF_JAVA_DEPLOY_DIR":
                env[key] = value
        preflight.write_deploy_env(env)
    result = preflight.run(**overrides)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode != 0, output
    assert f"{project_name} FAIL:" in output
    assert "PREFLIGHT_FAILURES=" in output
    if overrides.get("FAKE_MISSING_VOLUME") == "1" or overrides.get("FAKE_MISSING_IMAGE") == "mentorflow-migration-tool:MF-275":
        calls = preflight.log.read_text(encoding="utf-8")
        assert "run --pull=never" not in calls, calls


from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.credential_store import CredentialStoreError
from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    TenantContext,
    TenantMode,
)
from app.provider_service import ProviderError
from app import provider_runtime
from app.provider_runtime import (
    ProviderSession,
    create_provider_session,
    create_verified_demo_session,
    delete_provider_session,
    get_provider_session,
    provider_for_session,
    public_session,
    require_provider_session,
    reset_provider_sessions,
    revoke_provider_session,
    test_provider_session as verify_provider_session,
)
from app.provider_session_repository import (
    ProviderSessionRecord,
    ProviderSessionRepository,
    enterprise_provider_sessions,
    metadata,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "0032_enterprise_provider_sessions.py"
)
RAW_SECRET = "raw-provider-secret-that-must-never-persist"
CREDENTIAL_REF = "mfcred_durable_provider_session"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tenant(
    organization_id: str = "org-a",
    workspace_id: str = "workspace-a",
) -> TenantContext:
    return TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )


def _actor(
    subject_id: str = "subject-a",
    organization_id: str = "org-a",
    workspace_id: str = "workspace-a",
) -> ActorContext:
    return ActorContext(
        subject_id=subject_id,
        actor_kind=ActorKind.HUMAN,
        issuer="https://identity.example.test",
        token_id=f"token-{subject_id}-{organization_id}-{workspace_id}",
        session_id=f"identity-session-{subject_id}",
        request_id=f"request-{subject_id}",
        authentication_time=_now(),
        authentication_strength="mfa",
        organization_id=organization_id,
        workspace_id=workspace_id,
        roles=("teacher",),
    )


def _engines(tmp_path: Path) -> tuple[sa.Engine, sa.Engine]:
    database_path = tmp_path / "provider-sessions.sqlite"
    backend_engine = sa.create_engine(
        f"sqlite+pysqlite:///{database_path.as_posix()}",
        future=True,
    )
    metadata.create_all(backend_engine)
    worker_engine = sa.create_engine(
        f"sqlite+pysqlite:///{database_path.as_posix()}",
        future=True,
    )
    return backend_engine, worker_engine


def _repository(engine: sa.Engine) -> ProviderSessionRepository:
    return ProviderSessionRepository.for_test_engine(engine)


def _record(
    *,
    session_id: str,
    actor: ActorContext | None = None,
    tenant: TenantContext | None = None,
    credential_ref: str = CREDENTIAL_REF,
    verified: bool = True,
    expires_at: datetime | None = None,
) -> ProviderSessionRecord:
    actor = actor or _actor()
    tenant = tenant or _tenant()
    now = _now()
    return ProviderSessionRecord(
        provider_session_id=session_id,
        subject_id=actor.subject_id,
        organization_id=str(tenant.organization_id),
        workspace_id=str(tenant.workspace_id),
        provider="openai_compatible",
        base_url="https://provider.example.test/v1",
        model="model-a",
        credential_ref=credential_ref,
        temperature=0.2,
        verified_at=now if verified else None,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
        last_tested_at=now if verified else None,
    )


class _OfflineProvider:
    async def test(self) -> dict[str, str]:
        return {"model_reported": "model-a"}


@pytest.fixture(autouse=True)
def _reset_local_sessions() -> None:
    reset_provider_sessions()
    provider_runtime.configure_provider_session_repository(None)
    yield
    reset_provider_sessions()
    provider_runtime.configure_provider_session_repository(None)


def test_backend_created_session_resolves_in_separate_worker_and_survives_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_engine, worker_engine = _engines(tmp_path)
    backend_repository = _repository(backend_engine)
    actor = _actor()
    tenant = _tenant()
    provider_runtime.configure_provider_session_repository(backend_repository)
    monkeypatch.setattr(provider_runtime, "get_secret", lambda ref: RAW_SECRET)
    monkeypatch.setattr(provider_runtime, "get_provider", lambda config: object())

    created = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://provider.example.test/v1",
            "model": "model-a",
            "credential_ref": CREDENTIAL_REF,
            "temperature": 0.25,
        },
        actor=actor,
        tenant=tenant,
    )
    monkeypatch.setattr(
        provider_runtime,
        "provider_for_session",
        lambda _session: _OfflineProvider(),
    )
    verified = asyncio.run(
        verify_provider_session(
            created["provider_session_id"],
            actor=actor,
            tenant=tenant,
        )
    )
    assert verified["verified"] is True
    with backend_engine.connect() as connection:
        persisted = dict(
            connection.execute(
                sa.select(enterprise_provider_sessions).where(
                    enterprise_provider_sessions.c.provider_session_id
                    == created["provider_session_id"]
                )
            ).mappings().one()
        )
    assert RAW_SECRET not in json.dumps(persisted, default=str)
    assert RAW_SECRET not in json.dumps(created, default=str)

    reset_provider_sessions()
    provider_runtime.configure_provider_session_repository(None)
    backend_engine.dispose()
    worker_repository = _repository(worker_engine)
    provider_runtime.configure_provider_session_repository(worker_repository)
    resolved = require_provider_session(
        created["provider_session_id"],
        actor=actor,
        tenant=tenant,
    )

    assert resolved.id == created["provider_session_id"]
    assert resolved.subject_id == actor.subject_id
    assert resolved.organization_id == tenant.organization_id
    assert resolved.workspace_id == tenant.workspace_id
    assert resolved.credential_ref == CREDENTIAL_REF
    assert resolved.verified is True


def test_cross_subject_organization_and_workspace_access_is_denied(
    tmp_path: Path,
) -> None:
    backend_engine, _ = _engines(tmp_path)
    repository = _repository(backend_engine)
    owner_actor = _actor()
    owner_tenant = _tenant()
    record = repository.create(
        _record(session_id="ps_tenant_owned"),
        actor=owner_actor,
        tenant=owner_tenant,
    )
    foreign_contexts = (
        (_actor(subject_id="subject-b"), owner_tenant),
        (
            _actor(organization_id="org-b", workspace_id="workspace-b"),
            _tenant(organization_id="org-b", workspace_id="workspace-b"),
        ),
        (
            _actor(workspace_id="workspace-b"),
            _tenant(workspace_id="workspace-b"),
        ),
    )

    for foreign_actor, foreign_tenant in foreign_contexts:
        assert repository.get(
            record.provider_session_id,
            actor=foreign_actor,
            tenant=foreign_tenant,
        ) is None
        assert repository.revoke(
            record.provider_session_id,
            actor=foreign_actor,
            tenant=foreign_tenant,
            revoked_at=_now(),
        ) is False
        assert repository.delete(
            record.provider_session_id,
            actor=foreign_actor,
            tenant=foreign_tenant,
        ) is False
        with pytest.raises(ProviderError) as denied:
            require_provider_session(
                record.provider_session_id,
                actor=foreign_actor,
                tenant=foreign_tenant,
                repository=repository,
            )
        assert denied.value.code == "PROVIDER_SESSION_REQUIRED"

    assert repository.get(
        record.provider_session_id,
        actor=owner_actor,
        tenant=owner_tenant,
    ) is not None


def test_revoked_deleted_expired_and_unverified_sessions_fail_closed(
    tmp_path: Path,
) -> None:
    backend_engine, _ = _engines(tmp_path)
    repository = _repository(backend_engine)
    actor = _actor()
    tenant = _tenant()

    repository.create(
        _record(session_id="ps_revoked"),
        actor=actor,
        tenant=tenant,
    )
    assert revoke_provider_session(
        "ps_revoked",
        actor=actor,
        tenant=tenant,
        repository=repository,
    ) is True
    with pytest.raises(ProviderError) as revoked:
        require_provider_session(
            "ps_revoked",
            actor=actor,
            tenant=tenant,
            repository=repository,
        )
    assert revoked.value.code == "PROVIDER_SESSION_REVOKED"

    repository.create(
        _record(session_id="ps_deleted"),
        actor=actor,
        tenant=tenant,
    )
    assert delete_provider_session(
        "ps_deleted",
        actor=actor,
        tenant=tenant,
        repository=repository,
    ) is True
    with pytest.raises(ProviderError) as deleted:
        require_provider_session(
            "ps_deleted",
            actor=actor,
            tenant=tenant,
            repository=repository,
        )
    assert deleted.value.code == "PROVIDER_SESSION_REQUIRED"

    repository.create(
        _record(
            session_id="ps_expired",
            expires_at=_now() - timedelta(seconds=1),
        ),
        actor=actor,
        tenant=tenant,
    )
    with pytest.raises(ProviderError) as expired:
        require_provider_session(
            "ps_expired",
            actor=actor,
            tenant=tenant,
            repository=repository,
        )
    assert expired.value.code == "PROVIDER_SESSION_EXPIRED"

    repository.create(
        _record(session_id="ps_unverified", verified=False),
        actor=actor,
        tenant=tenant,
    )
    with pytest.raises(ProviderError) as unverified:
        require_provider_session(
            "ps_unverified",
            actor=actor,
            tenant=tenant,
            repository=repository,
        )
    assert unverified.value.code == "PROVIDER_SESSION_UNVERIFIED"


def test_unknown_or_missing_credential_reference_fails_closed_without_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_engine, _ = _engines(tmp_path)
    repository = _repository(backend_engine)
    actor = _actor()
    tenant = _tenant()
    monkeypatch.setattr(
        provider_runtime,
        "get_secret",
        lambda _ref: (_ for _ in ()).throw(
            CredentialStoreError(
                "CREDENTIAL_NOT_FOUND",
                f"credential unavailable: {RAW_SECRET}",
            )
        ),
    )

    for credential_ref in ("mfcred_unknown", ""):
        with pytest.raises(ProviderError) as missing:
            create_provider_session(
                {
                    "provider": "openai-compatible",
                    "base_url": "https://provider.example.test/v1",
                    "model": "model-a",
                    "credential_ref": credential_ref,
                },
                actor=actor,
                tenant=tenant,
                repository=repository,
            )
        assert missing.value.code == "PROVIDER_CONFIGURATION_REQUIRED"
        assert RAW_SECRET not in str(missing.value)

    with backend_engine.connect() as connection:
        assert connection.execute(
            sa.select(sa.func.count()).select_from(enterprise_provider_sessions)
        ).scalar_one() == 0


def test_worker_resolves_reference_only_then_missing_secret_fails_at_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_engine, worker_engine = _engines(tmp_path)
    actor = _actor()
    tenant = _tenant()
    _repository(backend_engine).create(
        _record(session_id="ps_unknown_secret", credential_ref="mfcred_unknown"),
        actor=actor,
        tenant=tenant,
    )
    worker_repository = _repository(worker_engine)
    session = require_provider_session(
        "ps_unknown_secret",
        actor=actor,
        tenant=tenant,
        repository=worker_repository,
    )
    monkeypatch.setattr(
        provider_runtime,
        "get_secret",
        lambda _ref: (_ for _ in ()).throw(
            CredentialStoreError(
                "CREDENTIAL_NOT_FOUND",
                f"credential unavailable: {RAW_SECRET}",
            )
        ),
    )

    with pytest.raises(ProviderError) as missing:
        provider_for_session(session)

    assert missing.value.code == "PROVIDER_CONFIGURATION_REQUIRED"
    assert missing.value.details == {}
    assert "mfcred_unknown" not in str(missing.value)


def test_secret_is_absent_from_database_payload_response_exception_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend_engine, _ = _engines(tmp_path)
    repository = _repository(backend_engine)
    actor = _actor()
    tenant = _tenant()
    record = repository.create(
        _record(session_id="ps_secret_safe"),
        actor=actor,
        tenant=tenant,
    )
    with backend_engine.connect() as connection:
        database_row = dict(
            connection.execute(
                sa.select(enterprise_provider_sessions).where(
                    enterprise_provider_sessions.c.provider_session_id
                    == record.provider_session_id
                )
            ).mappings().one()
        )
    job_payload = {
        "provider_session_id": record.provider_session_id,
        "actor_context": actor.model_dump(mode="json"),
        "tenant_context": tenant.model_dump(mode="json"),
    }
    session = require_provider_session(
        record.provider_session_id,
        actor=actor,
        tenant=tenant,
        repository=repository,
    )
    response = public_session(session)
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(
        provider_runtime,
        "get_secret",
        lambda _ref: (_ for _ in ()).throw(
            CredentialStoreError(
                "CREDENTIAL_NOT_FOUND",
                f"credential unavailable: {RAW_SECRET}",
            )
        ),
    )
    with pytest.raises(ProviderError) as error:
        provider_for_session(session)

    forbidden_column_fragments = (
        "api_key",
        "access_token",
        "bearer_token",
        "provider_secret",
        "credential_plaintext",
        "credential_value",
    )
    column_names = {column.name.lower() for column in enterprise_provider_sessions.c}
    assert not any(
        fragment in column_name
        for fragment in forbidden_column_fragments
        for column_name in column_names
    )
    assert database_row["credential_ref"] == CREDENTIAL_REF
    assert response["credential_ref"] == CREDENTIAL_REF
    assert RAW_SECRET not in json.dumps(database_row, default=str)
    assert RAW_SECRET not in json.dumps(job_payload, default=str)
    assert RAW_SECRET not in json.dumps(response, default=str)
    assert RAW_SECRET not in str(error.value)
    assert RAW_SECRET not in caplog.text


def test_enterprise_requires_complete_context_and_rejects_fake_success(
    tmp_path: Path,
) -> None:
    backend_engine, _ = _engines(tmp_path)
    repository = _repository(backend_engine)
    actor = _actor()
    tenant = _tenant()

    with pytest.raises(ProviderError) as incomplete:
        create_provider_session(
            {"provider": "fake"},
            actor=actor,
            repository=repository,
        )
    assert incomplete.value.code == "PROVIDER_SESSION_CONTEXT_REQUIRED"

    with pytest.raises(ProviderError) as fake:
        create_provider_session(
            {"provider": "fake"},
            actor=actor,
            tenant=tenant,
            repository=repository,
        )
    assert fake.value.code == "PROVIDER_CONFIGURATION_REQUIRED"

    organization_tenant = TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id="org-a",
    )
    organization_actor = _actor(workspace_id="workspace-a").model_copy(
        update={"workspace_id": None}
    )
    with pytest.raises(ProviderError) as missing_workspace:
        create_provider_session(
            {"provider": "fake"},
            actor=organization_actor,
            tenant=organization_tenant,
            repository=repository,
        )
    assert missing_workspace.value.code == "PROVIDER_SESSION_CONTEXT_REQUIRED"
    with backend_engine.connect() as connection:
        assert connection.execute(
            sa.select(sa.func.count()).select_from(enterprise_provider_sessions)
        ).scalar_one() == 0


def test_personal_local_process_memory_compatibility_is_preserved() -> None:
    created = create_verified_demo_session(owner_user_id="local-user")
    resolved = get_provider_session(created.id, owner_user_id="local-user")

    assert resolved is created
    assert resolved.provider == "fake"
    assert resolved.verified is True
    assert get_provider_session(created.id, owner_user_id="other-user") is None
    assert delete_provider_session(created.id, owner_user_id="local-user") is True


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0032_enterprise_provider_sessions",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingOp:
    def __init__(self, dialect_name: str = "sqlite") -> None:
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))
        self.created_table: tuple[object, ...] | None = None
        self.created_indexes: list[tuple[object, ...]] = []
        self.dropped_indexes: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.dropped_tables: list[str] = []
        self.executed: list[str] = []

    def get_bind(self):
        return self.bind

    def create_table(self, *items):
        self.created_table = items

    def create_index(self, *items, **_kwargs):
        self.created_indexes.append(items)

    def drop_index(self, *items, **kwargs):
        self.dropped_indexes.append((items, kwargs))

    def drop_table(self, table_name: str):
        self.dropped_tables.append(table_name)

    def execute(self, statement):
        self.executed.append(str(statement))


def test_migration_0031_to_0032_upgrade_downgrade_and_single_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    assert migration.revision == "0032_enterprise_provider_sessions"
    assert migration.down_revision == "0031_enterprise_tenancy_completion"

    recorder = _RecordingOp()
    monkeypatch.setattr(migration, "op", recorder)
    migration.upgrade()
    assert recorder.created_table is not None
    assert recorder.created_table[0] == "enterprise_provider_sessions"
    columns = {
        item.name: item
        for item in recorder.created_table[1:]
        if isinstance(item, sa.Column)
    }
    assert {
        "provider_session_id",
        "subject_id",
        "organization_id",
        "workspace_id",
        "provider",
        "base_url",
        "model",
        "credential_ref",
        "temperature",
        "verified_at",
        "expires_at",
        "revoked_at",
        "created_at",
        "updated_at",
    }.issubset(columns)
    assert all(
        columns[name].nullable is False
        for name in ("subject_id", "organization_id", "workspace_id")
    )
    assert not {
        "api_key",
        "access_token",
        "bearer_token",
        "provider_secret",
        "credential_plaintext",
        "credential_value",
    }.intersection(columns)
    migration.downgrade()
    assert recorder.dropped_tables == ["enterprise_provider_sessions"]

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    assert ScriptDirectory.from_config(config).get_heads() == [
        "0032_enterprise_provider_sessions"
    ]


def test_migration_enables_forces_rls_and_grants_minimal_crud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    recorder = _RecordingOp(dialect_name="postgresql")
    monkeypatch.setattr(migration, "op", recorder)
    monkeypatch.setattr(migration, "_verify_postgresql_security", lambda _bind: None)

    migration.upgrade()
    sql = "\n".join(recorder.executed)

    assert 'ALTER TABLE "enterprise_provider_sessions" ENABLE ROW LEVEL SECURITY' in sql
    assert 'ALTER TABLE "enterprise_provider_sessions" FORCE ROW LEVEL SECURITY' in sql
    assert 'CREATE POLICY "tenant_isolation_enterprise_provider_sessions"' in sql
    assert "current_setting('mentorflow.organization_id', true)" in sql
    assert "current_setting('mentorflow.workspace_id', true)" in sql
    assert "current_setting('mentorflow.actor_subject', true)" in sql
    assert "WITH CHECK" in sql
    assert "REVOKE ALL PRIVILEGES" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in sql
    assert "TO mentorflow_app" in sql

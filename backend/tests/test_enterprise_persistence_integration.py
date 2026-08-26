from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, cast

import pytest
from sqlalchemy.engine import Connection

from app.cost.cache_observatory.contracts import CacheLayer
from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    AuditEnvelope,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.enterprise_tenancy.adapters import InMemoryPersonalLocalResourceStore
from app.enterprise_tenancy.cache_boundary import (
    ENTERPRISE_CACHE_LAYERS,
    TenantCacheIdentityBuilder,
)
from app.enterprise_tenancy.context import (
    TenantDbSession,
    TenantTransactionContext,
    require_clean_dbapi_checkout,
)
from app.enterprise_tenancy.factory import EnterpriseRepositoryFactory
from app.enterprise_tenancy.inventory import (
    ENTERPRISE_PERSISTENCE_INVENTORY,
    PersistenceDomain,
)
from app.enterprise_tenancy.isolation_corpus import (
    IsolationOperation,
    build_tenant_isolation_corpus,
)
from app.enterprise_tenancy.job_context import (
    TenantJobContext,
    TenantJobResourceReference,
)
from app.enterprise_tenancy.models import (
    TenantContextLeakError,
    TenantContextRequiredError,
    TenantResourceDraft,
    TenantResourceKind,
    TenantResourceRecord,
)
from app.enterprise_tenancy.object_reference import TenantObjectReferenceFactory
from app.enterprise_tenancy.ports import (
    CostRepositoryPort,
    DocumentRepositoryPort,
    EvidenceRepositoryPort,
    KnowledgeMapRepositoryPort,
    PersistenceMode,
    PlanRepositoryPort,
    ReviewRepositoryPort,
    StudentRepositoryPort,
    TeacherSessionRepositoryPort,
    TenantDomainRepositoryPort,
    TenantDomainWrite,
)
from app.enterprise_tenancy.unit_of_work import (
    EnterpriseUnitOfWork,
    TenantOutboxIntent,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ENTERPRISE_KINDS = tuple(TenantResourceKind)
PERSONAL_LOCAL_KINDS = (
    TenantResourceKind.DOCUMENT,
    TenantResourceKind.KNOWLEDGE_MAP,
    TenantResourceKind.TEACHER_SESSION,
    TenantResourceKind.REVIEW_CAMPAIGN,
    TenantResourceKind.PLAN,
    TenantResourceKind.STUDENT_EVENT,
)


def _tenant(
    organization_id: str = "organization-a",
    workspace_id: str = "workspace-a",
) -> TenantContext:
    return TenantContext(
        organization_id=organization_id,
        workspace_id=workspace_id,
        mode=TenantMode.ENTERPRISE_SERVER,
    )


def _actor(
    tenant: TenantContext,
    *,
    kind: ActorKind = ActorKind.HUMAN,
    subject_id: str = "subject-a",
) -> ActorContext:
    return ActorContext(
        subject_id=subject_id,
        actor_kind=kind,
        issuer="offline-test",
        token_id=f"token-{subject_id}",
        session_id=f"session-{subject_id}",
        request_id=f"request-{subject_id}",
        authentication_time=datetime(2026, 8, 27, tzinfo=timezone.utc),
        authentication_strength="test",
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        membership_id=f"membership-{subject_id}",
        roles=("MEMBER",) if kind is ActorKind.HUMAN else (),
        service_permissions=("jobs.execute",) if kind is ActorKind.SERVICE else (),
    )


def _scope(
    tenant: TenantContext,
    *,
    owner_subject_id: str | None = None,
) -> ResourceScope:
    return ResourceScope(
        scope_type=ResourceScopeType.WORKSPACE,
        owner_subject_id=owner_subject_id,
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        classification=ResourceClassification.INTERNAL,
    )


def _session(tenant: TenantContext, actor: ActorContext) -> TenantDbSession:
    return TenantDbSession(
        connection=cast(Connection, object()),
        transaction_context=TenantTransactionContext(tenant=tenant, actor=actor),
    )


class _FakeTenantResourceRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[TenantResourceKind, str], TenantResourceRecord] = {}
        self.transactions: list[TenantDbSession] = []

    def _remember(self, transaction: TenantDbSession) -> None:
        self.transactions.append(transaction)

    def upsert(
        self,
        transaction: TenantDbSession,
        draft: TenantResourceDraft,
    ) -> TenantResourceRecord:
        self._remember(transaction)
        record = TenantResourceRecord(
            resource_id=draft.resource_id,
            kind=draft.kind,
            organization_id=cast(str, transaction.tenant.organization_id),
            workspace_id=cast(str, transaction.tenant.workspace_id),
            scope_type=draft.scope_type,
            owner_subject_id=draft.owner_subject_id,
            classification=draft.classification,
            title=draft.title,
            status=draft.status,
            payload=draft.payload,
            search_text=draft.search_text,
            job_id=draft.job_id,
            cache_key=draft.cache_key,
            created_at="2026-08-27T00:00:00Z",
            updated_at="2026-08-27T00:00:00Z",
        )
        self.records[(draft.kind, draft.resource_id)] = record
        return record

    def get(
        self,
        transaction: TenantDbSession,
        kind: TenantResourceKind,
        resource_id: str,
    ) -> TenantResourceRecord | None:
        self._remember(transaction)
        return self.records.get((kind, resource_id))

    def list_records(
        self,
        transaction: TenantDbSession,
        kind: TenantResourceKind,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> tuple[TenantResourceRecord, ...]:
        self._remember(transaction)
        return tuple(
            record
            for (record_kind, _), record in self.records.items()
            if record_kind is kind and (status is None or record.status == status)
        )[:limit]

    def search(
        self,
        transaction: TenantDbSession,
        kind: TenantResourceKind,
        query: str,
        *,
        limit: int = 200,
    ) -> tuple[TenantResourceRecord, ...]:
        normalized = query.casefold()
        return tuple(
            record
            for record in self.list_records(transaction, kind, limit=limit)
            if normalized in f"{record.title} {record.search_text}".casefold()
        )

    def update_payload(
        self,
        transaction: TenantDbSession,
        kind: TenantResourceKind,
        resource_id: str,
        *,
        payload: dict[str, Any],
        status: str,
    ) -> bool:
        self._remember(transaction)
        key = (kind, resource_id)
        current = self.records.get(key)
        if current is None:
            return False
        self.records[key] = replace(current, payload=payload, status=status)
        return True

    def delete(
        self,
        transaction: TenantDbSession,
        kind: TenantResourceKind,
        resource_id: str,
    ) -> bool:
        self._remember(transaction)
        return self.records.pop((kind, resource_id), None) is not None

    def export_records(
        self,
        transaction: TenantDbSession,
        kind: TenantResourceKind,
        *,
        limit: int = 10_000,
    ) -> tuple[TenantResourceRecord, ...]:
        return self.list_records(transaction, kind, limit=limit)

    def read_job(
        self,
        transaction: TenantDbSession,
        kind: TenantResourceKind,
        job_id: str,
    ) -> TenantResourceRecord | None:
        return next(
            (
                record
                for record in self.list_records(transaction, kind)
                if record.job_id == job_id
            ),
            None,
        )

    def read_cache(
        self,
        transaction: TenantDbSession,
        kind: TenantResourceKind,
        cache_key: str,
    ) -> TenantResourceRecord | None:
        return next(
            (
                record
                for record in self.list_records(transaction, kind)
                if record.cache_key == cache_key
            ),
            None,
        )


def test_inventory_covers_eight_existing_local_and_enterprise_domains() -> None:
    inventory = ENTERPRISE_PERSISTENCE_INVENTORY
    assert len(inventory.items) == len(PersistenceDomain) == 8
    assert {item.resource_kind for item in inventory.items} == set(ENTERPRISE_KINDS)
    for item in inventory.items:
        assert item.local_persistence
        assert item.local_implementations
        for implementation in item.local_implementations:
            relative_path = implementation.split(":", 1)[0]
            assert (REPO_ROOT / relative_path).exists(), implementation


def test_domain_port_names_reuse_one_protocol_and_frozen_mode() -> None:
    assert PersistenceMode is TenantMode
    assert {
        DocumentRepositoryPort,
        EvidenceRepositoryPort,
        KnowledgeMapRepositoryPort,
        PlanRepositoryPort,
        TeacherSessionRepositoryPort,
        ReviewRepositoryPort,
        StudentRepositoryPort,
        CostRepositoryPort,
    } == {TenantDomainRepositoryPort}


def test_personal_local_factory_runs_six_products_without_account_context() -> None:
    tenant = TenantContext(mode=TenantMode.PERSONAL_LOCAL)
    scope = ResourceScope(
        scope_type=ResourceScopeType.PERSONAL,
        classification=ResourceClassification.INTERNAL,
    )
    factory = EnterpriseRepositoryFactory(InMemoryPersonalLocalResourceStore())
    for kind in PERSONAL_LOCAL_KINDS:
        repository = factory.repository(kind, tenant=tenant)
        saved = repository.upsert(
            tenant=tenant,
            scope=scope,
            value=TenantDomainWrite(
                resource_id=f"local-{kind.value.lower()}",
                title=f"Offline {kind.value}",
                status="ready",
                payload={"offline": True},
                search_text="offline fixture",
            ),
        )
        assert saved.scope == scope
        assert repository.get(
            tenant=tenant,
            scope=scope,
            resource_id=saved.resource_id,
        ) == saved
        assert repository.search(tenant=tenant, scope=scope, query="fixture") == (
            saved,
        )


def test_postgres_adapter_requires_frozen_tenant_scope_and_transaction() -> None:
    tenant = _tenant()
    actor = _actor(tenant)
    transaction = _session(tenant, actor)
    low_level = _FakeTenantResourceRepository()
    factory = EnterpriseRepositoryFactory(
        personal_local_store=InMemoryPersonalLocalResourceStore(),
        enterprise_resource_repository=cast(Any, low_level),
    )
    for kind in ENTERPRISE_KINDS:
        scope = _scope(tenant)
        repository = factory.repository(kind, tenant=tenant, transaction=transaction)
        saved = repository.upsert(
            tenant=tenant,
            scope=scope,
            value=TenantDomainWrite(
                resource_id=f"enterprise-{kind.value.lower()}",
                title="Owned record",
                status="ready",
                payload={"tenant": "a"},
                search_text="owned searchable",
                job_id=f"job-{kind.value.lower()}",
                cache_key=f"cache-{kind.value.lower()}",
            ),
        )
        assert saved.scope.owner_subject_id == actor.subject_id
        assert repository.search(tenant=tenant, scope=scope, query="searchable")
        assert repository.read_job(
            tenant=tenant,
            scope=scope,
            job_id=f"job-{kind.value.lower()}",
        ) == saved
        assert repository.read_cache(
            tenant=tenant,
            scope=scope,
            cache_key=f"cache-{kind.value.lower()}",
        ) == saved

    foreign_tenant = _tenant("organization-b", "workspace-b")
    document_repository = factory.repository(
        TenantResourceKind.DOCUMENT,
        tenant=tenant,
        transaction=transaction,
    )
    with pytest.raises(TenantContextRequiredError, match="scope must match"):
        document_repository.get(
            tenant=tenant,
            scope=_scope(foreign_tenant),
            resource_id="enterprise-document",
        )
    with pytest.raises(TypeError, match="enterprise-context.v1"):
        document_repository.get(
            tenant=tenant,
            scope=cast(Any, {"workspace_id": "workspace-a"}),
            resource_id="enterprise-document",
        )


def test_cache_identity_is_tenant_proofed_for_every_existing_cache_layer() -> None:
    builder = TenantCacheIdentityBuilder(b"cache-test-key-32-bytes-minimum!!!")
    tenant_a = _tenant()
    tenant_b = _tenant("organization-b", "workspace-b")
    assert ENTERPRISE_CACHE_LAYERS == frozenset(CacheLayer)
    for layer in CacheLayer:
        identity_a = builder.build(
            tenant_a,
            layer=layer,
            workload_identity="same-workload",
        )
        identity_b = builder.build(
            tenant_b,
            layer=layer,
            workload_identity="same-workload",
        )
        assert identity_a.cache_key != identity_b.cache_key
        builder.require_for_tenant(tenant_a, identity_a)
        with pytest.raises(TenantContextRequiredError):
            builder.require_for_tenant(tenant_b, identity_a)


def test_signed_object_reference_preserves_ownership_and_rejects_foreign_tenant() -> None:
    factory = TenantObjectReferenceFactory(b"object-test-key-32-bytes-minimum!!")
    tenant_a = _tenant()
    tenant_b = _tenant("organization-b", "workspace-b")
    reference = factory.create(
        tenant_a,
        scope=_scope(tenant_a),
        resource_kind=TenantResourceKind.DOCUMENT,
        resource_id="document-a",
        filename="notes.pdf",
    )
    assert reference.object_key.startswith(
        "organizations/organization-a/workspaces/workspace-a/"
    )
    factory.require_authorized(tenant_a, reference)
    with pytest.raises(TenantContextRequiredError):
        factory.require_authorized(tenant_b, reference)
    with pytest.raises(TenantContextRequiredError, match="proof"):
        factory.require_authorized(
            tenant_a,
            replace(reference, object_key=f"{reference.object_key}.tampered"),
        )


@pytest.mark.parametrize("actor_kind", [ActorKind.HUMAN, ActorKind.SERVICE])
def test_job_context_carries_frozen_tenant_actor_and_resources(
    actor_kind: ActorKind,
) -> None:
    tenant = _tenant()
    actor = _actor(tenant, kind=actor_kind, subject_id=actor_kind.value.lower())
    job_context = TenantJobContext(
        tenant=tenant,
        actor=actor,
        request_id=actor.request_id,
        trace_id=f"trace-{actor.subject_id}",
        resources=(
            TenantJobResourceReference(
                resource_kind=TenantResourceKind.DOCUMENT,
                resource_id="document-a",
                scope=_scope(tenant),
            ),
        ),
    )
    assert job_context.context_id.startswith("tenant-job:")
    assert job_context.to_payload()["tenant"] == tenant.model_dump(mode="json")
    with pytest.raises(TenantContextRequiredError, match="must match"):
        TenantJobContext(
            tenant=tenant,
            actor=actor,
            request_id=actor.request_id,
            trace_id="trace-foreign",
            resources=(
                TenantJobResourceReference(
                    resource_kind=TenantResourceKind.DOCUMENT,
                    resource_id="document-b",
                    scope=_scope(_tenant("organization-b", "workspace-b")),
                ),
            ),
        )


def test_unit_of_work_shares_transaction_for_mutation_audit_and_outbox() -> None:
    tenant = _tenant()
    actor = _actor(tenant)
    transaction = _session(tenant, actor)
    low_level = _FakeTenantResourceRepository()
    committed: list[TenantDbSession] = []
    rolled_back: list[TenantDbSession] = []

    class TransactionManager:
        @contextmanager
        def transaction(
            self,
            _context: TenantTransactionContext,
        ) -> Iterator[TenantDbSession]:
            try:
                yield transaction
            except Exception:
                rolled_back.append(transaction)
                raise
            else:
                committed.append(transaction)

    class AuditPort:
        calls: list[tuple[TenantDbSession, AuditEnvelope]] = []

        def append(
            self,
            item_transaction: TenantDbSession,
            envelope: AuditEnvelope,
        ) -> None:
            self.calls.append((item_transaction, envelope))

    class OutboxPort:
        calls: list[tuple[TenantDbSession, TenantOutboxIntent]] = []

        def append(
            self,
            item_transaction: TenantDbSession,
            intent: TenantOutboxIntent,
        ) -> None:
            self.calls.append((item_transaction, intent))

    audit_port = AuditPort()
    outbox_port = OutboxPort()
    uow = EnterpriseUnitOfWork(
        TransactionManager(),
        EnterpriseRepositoryFactory(
            personal_local_store=InMemoryPersonalLocalResourceStore(),
            enterprise_resource_repository=cast(Any, low_level),
        ),
        audit_port,
        outbox_port,
    )
    context = transaction.transaction_context
    job_context = TenantJobContext(
        tenant=tenant,
        actor=actor,
        request_id=actor.request_id,
        trace_id="trace-a",
        resources=(),
    )
    with uow.transaction(context) as session:
        session.repository(TenantResourceKind.DOCUMENT).upsert(
            tenant=tenant,
            scope=_scope(tenant),
            value=TenantDomainWrite(
                resource_id="document-a",
                title="Document A",
                status="ready",
                payload={"ok": True},
            ),
        )
        session.append_audit(
            AuditEnvelope(
                event_id="audit-a",
                actor_context=actor,
                action="document.write",
                resource_type="DOCUMENT",
                resource_id="document-a",
                outcome="SUCCESS",
                reason_code="WRITTEN",
                occurred_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
                request_id=actor.request_id,
                trace_id="trace-a",
            )
        )
        session.append_outbox(
            TenantOutboxIntent(
                event_id="outbox-a",
                event_type="document.changed",
                job_context=job_context,
                payload_redacted={"resource_id": "document-a"},
            )
        )
    assert committed == [transaction]
    assert rolled_back == []
    assert low_level.transactions and set(low_level.transactions) == {transaction}
    assert audit_port.calls[0][0] is transaction
    assert outbox_port.calls[0][0] is transaction

    with pytest.raises(RuntimeError, match="rollback-proof"):
        with uow.transaction(context):
            raise RuntimeError("rollback-proof")
    assert rolled_back == [transaction]


def test_pool_checkout_scrubs_a_then_allows_same_connection_for_b() -> None:
    class Cursor:
        def __init__(self, connection: "DbApiConnection") -> None:
            self.connection = connection

        def execute(self, statement: str) -> None:
            self.connection.statements.append(statement.strip())
            if statement.strip() == "RESET ALL":
                self.connection.values = (None, None, None)

        def fetchone(self) -> tuple[str | None, str | None, str | None]:
            return self.connection.values

        def close(self) -> None:
            pass

    class DbApiConnection:
        def __init__(self) -> None:
            self.values: tuple[str | None, str | None, str | None] = (
                "organization-a",
                "workspace-a",
                "subject-a",
            )
            self.statements: list[str] = []
            self.autocommit = False

        def cursor(self) -> Cursor:
            return Cursor(self)

        def rollback(self) -> None:
            pass

    connection = DbApiConnection()
    with pytest.raises(TenantContextLeakError, match="retained tenant context"):
        require_clean_dbapi_checkout(connection)
    assert connection.values == (None, None, None)
    require_clean_dbapi_checkout(connection)
    connection.values = ("organization-b", "workspace-b", "subject-b")
    assert connection.values[0] != "organization-a"


def test_isolation_corpus_includes_pagination_cache_object_and_connection_reuse() -> None:
    corpus = build_tenant_isolation_corpus()
    assert corpus.operation_count == 10
    assert len(corpus.probes) == 80
    assert corpus.unauthorized_success_expected == 0
    assert {
        IsolationOperation.FOREIGN_PAGINATION,
        IsolationOperation.FOREIGN_CACHE_IDENTITY,
        IsolationOperation.FOREIGN_OBJECT_REFERENCE,
        IsolationOperation.CONNECTION_REUSE,
    } <= {probe.operation for probe in corpus.probes}
    assert all(probe.foreign_pagination_token for probe in corpus.probes)
    assert all(probe.foreign_cache_identity for probe in corpus.probes)
    assert all(probe.foreign_object_key for probe in corpus.probes)
    assert all(probe.connection_reuse_sequence for probe in corpus.probes)


def test_postgres_staging_assets_are_offline_inspectable_and_truthful() -> None:
    staging = REPO_ROOT / "enterprise_e1" / "staging" / "postgres"
    expected = {
        "README.md",
        "compose.yaml",
        "run.sh",
        "bootstrap.sh",
        "migrate.sh",
        "seed.sh",
        "rls-negative-suite.sh",
        "pool-leak-suite.sh",
        "cleanup.sh",
    }
    assert {path.name for path in staging.iterdir()} == expected
    combined = "\n".join(
        (staging / name).read_text(encoding="utf-8") for name in sorted(expected)
    )
    assert "PENDING_STAGING" in combined
    assert "postgres:16" in combined
    assert "0031_enterprise_tenancy_completion" in combined
    assert "UNAUTHORIZED_SUCCESS_EXPECTED=0" in combined
    assert "CONNECTION_REUSE" in combined
    assert "docker compose" in combined

"""One transaction for tenant context, mutation, audit, and outbox seams."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import ContextManager, Mapping, Protocol

from app.enterprise.contracts_v1 import AuditEnvelope

from .context import TenantDbSession, TenantTransactionContext
from .factory import EnterpriseRepositoryFactory
from .job_context import TenantJobContext
from .models import TenantContextRequiredError, TenantResourceKind
from .ports import TenantDomainRepositoryPort


class TenantTransactionManagerPort(Protocol):
    def transaction(
        self,
        context: TenantTransactionContext,
    ) -> ContextManager[TenantDbSession]: ...


class TransactionalAuditPort(Protocol):
    def append(self, transaction: TenantDbSession, envelope: AuditEnvelope) -> None: ...


@dataclass(frozen=True, slots=True)
class TenantOutboxIntent:
    event_id: str
    event_type: str
    job_context: TenantJobContext
    payload_redacted: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in ("event_id", "event_type"):
            value = str(getattr(self, name)).strip()
            if not value or len(value) > 160:
                raise ValueError(f"{name} must be non-empty and bounded")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "payload_redacted", dict(self.payload_redacted))


class TransactionalOutboxPort(Protocol):
    def append(self, transaction: TenantDbSession, intent: TenantOutboxIntent) -> None: ...


@dataclass(frozen=True, slots=True)
class EnterpriseUnitOfWorkSession:
    transaction: TenantDbSession
    repository_factory: EnterpriseRepositoryFactory
    audit_port: TransactionalAuditPort
    outbox_port: TransactionalOutboxPort

    def repository(
        self,
        kind: TenantResourceKind | str,
    ) -> TenantDomainRepositoryPort:
        return self.repository_factory.repository(
            kind,
            tenant=self.transaction.tenant,
            transaction=self.transaction,
        )

    def append_audit(self, envelope: AuditEnvelope) -> None:
        if envelope.actor_context != self.transaction.actor:
            raise TenantContextRequiredError("audit actor must match the unit of work")
        if envelope.request_id != self.transaction.actor.request_id:
            raise TenantContextRequiredError("audit request must match the unit of work")
        self.audit_port.append(self.transaction, envelope)

    def append_outbox(self, intent: TenantOutboxIntent) -> None:
        context = intent.job_context
        if (
            context.tenant != self.transaction.tenant
            or context.actor != self.transaction.actor
        ):
            raise TenantContextRequiredError("outbox context must match the unit of work")
        self.outbox_port.append(self.transaction, intent)


class EnterpriseUnitOfWork:
    def __init__(
        self,
        transaction_manager: TenantTransactionManagerPort,
        repository_factory: EnterpriseRepositoryFactory,
        audit_port: TransactionalAuditPort,
        outbox_port: TransactionalOutboxPort,
    ) -> None:
        self.transaction_manager = transaction_manager
        self.repository_factory = repository_factory
        self.audit_port = audit_port
        self.outbox_port = outbox_port

    @contextmanager
    def transaction(
        self,
        context: TenantTransactionContext,
    ) -> Iterator[EnterpriseUnitOfWorkSession]:
        with self.transaction_manager.transaction(context) as transaction:
            yield EnterpriseUnitOfWorkSession(
                transaction=transaction,
                repository_factory=self.repository_factory,
                audit_port=self.audit_port,
                outbox_port=self.outbox_port,
            )


__all__ = [
    "EnterpriseUnitOfWork",
    "EnterpriseUnitOfWorkSession",
    "TenantOutboxIntent",
    "TenantTransactionManagerPort",
    "TransactionalAuditPort",
    "TransactionalOutboxPort",
]

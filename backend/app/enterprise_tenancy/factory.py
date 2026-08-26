"""Central repository selection; business modules never branch on PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass

from app.enterprise.contracts_v1 import TenantContext, TenantMode

from .adapters import PersonalLocalDomainRepository, PostgresTenantDomainRepository
from .context import TenantDbSession
from .models import EnterprisePersistenceUnavailableError, TenantResourceKind
from .ports import PersonalLocalResourceStorePort, TenantDomainRepositoryPort
from .repository import TenantResourceRepository


@dataclass(frozen=True, slots=True)
class EnterpriseRepositoryFactory:
    personal_local_store: PersonalLocalResourceStorePort
    enterprise_resource_repository: TenantResourceRepository | None = None

    def repository(
        self,
        kind: TenantResourceKind | str,
        *,
        tenant: TenantContext,
        transaction: TenantDbSession | None = None,
    ) -> TenantDomainRepositoryPort:
        resolved = TenantResourceKind(kind)
        if tenant.mode is TenantMode.PERSONAL_LOCAL:
            if transaction is not None:
                raise ValueError("Personal Local repository cannot receive a DB transaction")
            return PersonalLocalDomainRepository(self.personal_local_store, resolved)
        if self.enterprise_resource_repository is None or transaction is None:
            raise EnterprisePersistenceUnavailableError(
                "ENTERPRISE_SERVER requires a PostgreSQL repository and transaction"
            )
        if transaction.tenant != tenant:
            raise EnterprisePersistenceUnavailableError(
                "repository transaction does not belong to the requested tenant"
            )
        return PostgresTenantDomainRepository(
            self.enterprise_resource_repository,
            transaction,
            resolved,
        )


__all__ = ["EnterpriseRepositoryFactory"]

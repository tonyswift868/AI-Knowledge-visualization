from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.governance.authorization import AuthorizationService, ROLE_MATRIX
from app.governance.models import GovernanceActorAdapter, RelationshipContext, Role
from app.governance.surface_enforcement import (
    ContextTrustSource,
    FastAPIRequestGuard,
    ProtectedResourceReference,
    TrustedEnterpriseContext,
)
from app.governance.surface_manifest import (
    ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ProtectedSurfaceManifest,
    ProtectedSurfaceOperation,
    RequiredPolicy,
    SurfaceDomain,
    TenantScopeRequirement,
)


class NegativeScenario(str, Enum):
    OWN_RESOURCE = "OWN_RESOURCE"
    FOREIGN_ORGANIZATION = "FOREIGN_ORGANIZATION"
    FOREIGN_WORKSPACE = "FOREIGN_WORKSPACE"
    FORGED_IDENTIFIER = "FORGED_IDENTIFIER"
    REVOKED_MEMBERSHIP = "REVOKED_MEMBERSHIP"
    WRONG_ROLE = "WRONG_ROLE"


@dataclass(frozen=True, slots=True)
class _ForgedTransportRequest:
    query_params: dict[str, str]
    headers: dict[str, str]


class _StaticTrustedResolver:
    def __init__(self, context: TrustedEnterpriseContext) -> None:
        self._context = context

    def resolve(self, request: object) -> TrustedEnterpriseContext:
        del request
        return self._context


@dataclass(frozen=True, slots=True)
class AuthorizationNegativeCase:
    domain: str
    operation_id: str
    transports: tuple[str, ...]
    scenario: str
    expected_allowed: bool
    actual_allowed: bool
    reason_code: str
    audit_sequence: int
    transport_claims_ignored: bool

    @property
    def passed(self) -> bool:
        return (
            self.actual_allowed is self.expected_allowed
            and self.audit_sequence > 0
            and self.transport_claims_ignored
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "operation_id": self.operation_id,
            "transports": self.transports,
            "scenario": self.scenario,
            "expected_allowed": self.expected_allowed,
            "actual_allowed": self.actual_allowed,
            "reason_code": self.reason_code,
            "audit_sequence": self.audit_sequence,
            "transport_claims_ignored": self.transport_claims_ignored,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class AuthorizationNegativeReport:
    pack_version: str
    cases: tuple[AuthorizationNegativeCase, ...]

    @property
    def failures(self) -> tuple[AuthorizationNegativeCase, ...]:
        return tuple(case for case in self.cases if not case.passed)

    @property
    def complete(self) -> bool:
        return (
            not self.failures
            and {case.domain for case in self.cases} == {domain.value for domain in SurfaceDomain}
            and set(self.scenario_counts) == {scenario.value for scenario in NegativeScenario}
            and {"GET", "POST", "PATCH", "DELETE", "EXPORT"}.issubset(self.transport_counts)
        )

    @property
    def scenario_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(case.scenario for case in self.cases).items()))

    @property
    def transport_counts(self) -> dict[str, int]:
        return dict(
            sorted(
                Counter(
                    transport
                    for case in self.cases
                    for transport in case.transports
                ).items()
            )
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "pack_version": self.pack_version,
            "case_count": len(self.cases),
            "allowed_case_count": sum(case.actual_allowed for case in self.cases),
            "denied_case_count": sum(not case.actual_allowed for case in self.cases),
            "domains": tuple(sorted({case.domain for case in self.cases})),
            "scenario_counts": self.scenario_counts,
            "transport_counts": self.transport_counts,
            "failures": tuple(case.to_public_dict() for case in self.failures),
            "complete": self.complete,
            "cases": tuple(case.to_public_dict() for case in self.cases),
        }


class AuthorizationNegativePack:
    PACK_VERSION = "mentorflow.enterprise-authorization-negative.v1"

    def __init__(
        self,
        manifest: ProtectedSurfaceManifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ) -> None:
        self._manifest = manifest

    @staticmethod
    def _context(
        role: Role,
        *,
        workspace_id: str | None,
        active: bool = True,
    ) -> TrustedEnterpriseContext:
        actor = ActorContext(
            subject_id=f"negative-{role.value.lower()}-{workspace_id or 'org'}",
            actor_kind=ActorKind.HUMAN,
            issuer="https://negative-pack.example.test",
            token_id=f"token-{role.value.lower()}-{workspace_id or 'org'}",
            session_id=f"session-{role.value.lower()}-{workspace_id or 'org'}",
            request_id=f"request-{role.value.lower()}-{workspace_id or 'org'}",
            authentication_time=datetime(2026, 8, 27, tzinfo=timezone.utc),
            authentication_strength="mfa",
            organization_id="org-a",
            workspace_id=workspace_id,
            membership_id=f"membership-{role.value.lower()}",
            roles=(role.value,),
        )
        return TrustedEnterpriseContext(
            subject=GovernanceActorAdapter(
                actor=actor,
                effective_role=role,
                workspace_memberships=(
                    frozenset({workspace_id}) if workspace_id is not None else frozenset()
                ),
                active=active,
            ),
            tenant=TenantContext(
                mode=TenantMode.ENTERPRISE_SERVER,
                organization_id="org-a",
                workspace_id=workspace_id,
            ),
            source=ContextTrustSource.IDENTITY_RESOLVER,
            validated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )

    @staticmethod
    def _own_scope(operation: ProtectedSurfaceOperation) -> ResourceScope:
        if operation.tenant_scope is TenantScopeRequirement.ORGANIZATION:
            return ResourceScope(
                scope_type=ResourceScopeType.ORGANIZATION,
                organization_id="org-a",
                classification=ResourceClassification.RESTRICTED,
            )
        return ResourceScope(
            scope_type=ResourceScopeType.WORKSPACE,
            organization_id="org-a",
            workspace_id="ws-a",
            classification=ResourceClassification.RESTRICTED,
        )

    @staticmethod
    def _wrong_role(operation: ProtectedSurfaceOperation) -> Role | None:
        for role in (Role.AUDITOR, Role.LEARNER, Role.INSTRUCTOR, Role.CONTENT_MANAGER):
            if operation.action not in ROLE_MATRIX.get(role, {}).get(
                operation.resource_type,
                frozenset(),
            ):
                return role
        return None

    @staticmethod
    def _request() -> _ForgedTransportRequest:
        return _ForgedTransportRequest(
            query_params={"organization_id": "org-foreign", "role": Role.OWNER.value},
            headers={"x-organization-id": "org-foreign", "x-role": Role.OWNER.value},
        )

    def _execute(
        self,
        authorization: AuthorizationService,
        operation: ProtectedSurfaceOperation,
        scenario: NegativeScenario,
        *,
        wrong_role: Role | None = None,
    ) -> AuthorizationNegativeCase:
        own_scope = self._own_scope(operation)
        workspace_id = own_scope.workspace_id
        role = wrong_role or Role.OWNER
        active = scenario is not NegativeScenario.REVOKED_MEMBERSHIP
        if scenario is NegativeScenario.FOREIGN_WORKSPACE:
            workspace_id = "ws-a"
        context = self._context(role, workspace_id=workspace_id, active=active)
        resource_scope = own_scope
        relationships = RelationshipContext()
        if scenario is NegativeScenario.FOREIGN_ORGANIZATION:
            resource_scope = ResourceScope(
                scope_type=ResourceScopeType.WORKSPACE,
                organization_id="org-foreign",
                workspace_id="ws-foreign",
                classification=ResourceClassification.RESTRICTED,
            )
        elif scenario is NegativeScenario.FOREIGN_WORKSPACE:
            resource_scope = ResourceScope(
                scope_type=ResourceScopeType.WORKSPACE,
                organization_id="org-a",
                workspace_id="ws-foreign",
                classification=ResourceClassification.RESTRICTED,
            )
        elif scenario is NegativeScenario.FORGED_IDENTIFIER:
            relationships = RelationshipContext(resource_exists=False)
        guard = FastAPIRequestGuard(
            authorization,
            _StaticTrustedResolver(context),
            manifest=self._manifest,
        )
        receipt = guard.authorize(
            self._request(),
            operation_id=operation.operation_id,
            resource=ProtectedResourceReference(
                resource_id=f"{scenario.value.lower()}:{operation.operation_id}",
                resource_scope=resource_scope,
                relationships=relationships,
            ),
            trace_id=f"negative:{operation.operation_id}:{scenario.value}",
            occurred_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )
        return AuthorizationNegativeCase(
            domain=operation.domain.value,
            operation_id=operation.operation_id,
            transports=tuple(transport.value for transport in operation.transports),
            scenario=scenario.value,
            expected_allowed=scenario is NegativeScenario.OWN_RESOURCE,
            actual_allowed=receipt.allowed,
            reason_code=receipt.reason_code,
            audit_sequence=receipt.decision.audit_sequence,
            transport_claims_ignored=receipt.transport_claims_ignored,
        )

    def run(self, authorization: AuthorizationService) -> AuthorizationNegativeReport:
        operations = tuple(
            operation
            for operation in self._manifest.operations
            if operation.required_policy is RequiredPolicy.AUTHORIZATION
        )
        cases: list[AuthorizationNegativeCase] = []
        common_scenarios = (
            NegativeScenario.OWN_RESOURCE,
            NegativeScenario.FOREIGN_ORGANIZATION,
            NegativeScenario.FOREIGN_WORKSPACE,
            NegativeScenario.FORGED_IDENTIFIER,
            NegativeScenario.REVOKED_MEMBERSHIP,
        )
        for operation in operations:
            cases.extend(
                self._execute(authorization, operation, scenario)
                for scenario in common_scenarios
            )
        for domain in SurfaceDomain:
            for operation in operations:
                if operation.domain is not domain:
                    continue
                wrong_role = self._wrong_role(operation)
                if wrong_role is not None:
                    cases.append(
                        self._execute(
                            authorization,
                            operation,
                            NegativeScenario.WRONG_ROLE,
                            wrong_role=wrong_role,
                        )
                    )
                    break
            else:
                raise AssertionError(f"no wrong-role protected operation for {domain.value}")
        return AuthorizationNegativeReport(
            pack_version=self.PACK_VERSION,
            cases=tuple(cases),
        )


__all__ = [
    "AuthorizationNegativeCase",
    "AuthorizationNegativePack",
    "AuthorizationNegativeReport",
    "NegativeScenario",
]

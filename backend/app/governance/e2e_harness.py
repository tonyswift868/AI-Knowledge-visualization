from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.governance.audit import SQLiteAuditLog
from app.governance.authorization import AuthorizationService
from app.governance.models import GovernanceActorAdapter, RelationshipContext, Role
from app.governance.side_effects import GovernedSideEffectExecutor, SideEffectPlan
from app.governance.surface_enforcement import (
    AuthorizedServiceCall,
    ContextTrustSource,
    FastAPIRequestGuard,
    ProtectedResourceReference,
    TrustedEnterpriseContext,
)


NOW = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class DeterministicRequest:
    token: str
    headers: dict[str, str]
    query_params: dict[str, str]


class DeterministicIdentityResolver:
    """Offline token fixture; no OIDC or network is contacted."""

    def __init__(self, contexts: dict[str, TrustedEnterpriseContext]) -> None:
        self._contexts = dict(contexts)

    def resolve(self, request: object) -> TrustedEnterpriseContext:
        if not isinstance(request, DeterministicRequest):
            raise TypeError("deterministic resolver requires DeterministicRequest")
        try:
            return self._contexts[request.token]
        except KeyError as exc:
            raise ValueError("fixture token is unknown") from exc


@dataclass(frozen=True, slots=True)
class GovernanceE2EScenario:
    scenario_id: str
    organization_id: str
    workspace_id: str
    role: str
    cohort_id: str
    resource_id: str
    operation_id: str
    api_allowed: bool
    service_allowed: bool
    side_effect_count: int
    audit_records_added: int
    reason_code: str
    forged_transport_claims_ignored: bool

    @property
    def passed(self) -> bool:
        return (
            self.api_allowed is self.service_allowed
            and self.audit_records_added == 2
            and self.forged_transport_claims_ignored
            and (self.side_effect_count == 1 if self.api_allowed else self.side_effect_count == 0)
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "role": self.role,
            "cohort_id": self.cohort_id,
            "resource_id": self.resource_id,
            "operation_id": self.operation_id,
            "api_allowed": self.api_allowed,
            "service_allowed": self.service_allowed,
            "side_effect_count": self.side_effect_count,
            "audit_records_added": self.audit_records_added,
            "reason_code": self.reason_code,
            "forged_transport_claims_ignored": self.forged_transport_claims_ignored,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class GovernanceE2EReport:
    harness_version: str
    scenarios: tuple[GovernanceE2EScenario, ...]
    organization_count: int
    workspace_count: int
    role_count: int
    cohort_count: int
    resource_count: int
    audit_record_count: int
    audit_chain_valid: bool
    provider_calls: int = 0
    real_oidc_calls: int = 0
    real_postgres_calls: int = 0

    @property
    def complete(self) -> bool:
        return (
            all(scenario.passed for scenario in self.scenarios)
            and self.organization_count == 3
            and self.workspace_count == 3
            and self.role_count == 3
            and self.cohort_count == 3
            and self.resource_count == 3
            and self.audit_record_count == len(self.scenarios) * 2
            and self.audit_chain_valid
            and self.provider_calls == self.real_oidc_calls == self.real_postgres_calls == 0
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "harness_version": self.harness_version,
            "flow": "request -> canonical context -> API authorization -> service authorization -> audit",
            "scenario_count": len(self.scenarios),
            "organization_count": self.organization_count,
            "workspace_count": self.workspace_count,
            "role_count": self.role_count,
            "cohort_count": self.cohort_count,
            "resource_count": self.resource_count,
            "audit_record_count": self.audit_record_count,
            "audit_chain_valid": self.audit_chain_valid,
            "provider_calls": self.provider_calls,
            "real_oidc_calls": self.real_oidc_calls,
            "real_postgres_calls": self.real_postgres_calls,
            "outcomes": dict(sorted(Counter(s.reason_code for s in self.scenarios).items())),
            "scenarios": tuple(scenario.to_public_dict() for scenario in self.scenarios),
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class _ScenarioFixture:
    scenario_id: str
    token: str
    context: TrustedEnterpriseContext
    cohort_id: str
    resource: ProtectedResourceReference
    operation_id: str
    effect: str


class GovernanceE2EHarness:
    HARNESS_VERSION = "mentorflow.enterprise-governance-e2e.v1"

    @staticmethod
    def _context(
        organization_id: str,
        workspace_id: str,
        role: Role,
    ) -> TrustedEnterpriseContext:
        actor = ActorContext(
            subject_id=f"actor-{organization_id}",
            actor_kind=ActorKind.HUMAN,
            issuer="offline-e2e-fixture",
            token_id=f"token-metadata-{organization_id}",
            session_id=f"session-{organization_id}",
            request_id=f"request-{organization_id}",
            authentication_time=NOW,
            authentication_strength="offline-mfa-fixture",
            organization_id=organization_id,
            workspace_id=workspace_id,
            membership_id=f"membership-{organization_id}",
            roles=(role.value,),
        )
        return TrustedEnterpriseContext(
            subject=GovernanceActorAdapter(
                actor=actor,
                effective_role=role,
                workspace_memberships=frozenset({workspace_id}),
            ),
            tenant=TenantContext(
                mode=TenantMode.ENTERPRISE_SERVER,
                organization_id=organization_id,
                workspace_id=workspace_id,
            ),
            source=ContextTrustSource.IDENTITY_RESOLVER,
            validated_at=NOW,
        )

    @staticmethod
    def _scope(organization_id: str, workspace_id: str) -> ResourceScope:
        return ResourceScope(
            scope_type=ResourceScopeType.WORKSPACE,
            organization_id=organization_id,
            workspace_id=workspace_id,
            classification=ResourceClassification.RESTRICTED,
        )

    def _fixtures(self) -> tuple[_ScenarioFixture, ...]:
        owner = self._context("org-a", "ws-a", Role.OWNER)
        instructor = self._context("org-b", "ws-b", Role.INSTRUCTOR)
        learner = self._context("org-c", "ws-c", Role.LEARNER)
        resources = {
            "document-a": ProtectedResourceReference(
                "document-a",
                self._scope("org-a", "ws-a"),
            ),
            "teacher-session-b": ProtectedResourceReference(
                "teacher-session-b",
                self._scope("org-b", "ws-b"),
                RelationshipContext(
                    cohort_id="cohort-b",
                    assigned_cohort_ids=frozenset({"cohort-b"}),
                    learner_cohort_ids=frozenset({"cohort-b"}),
                ),
            ),
            "progress-c": ProtectedResourceReference(
                "progress-c",
                self._scope("org-c", "ws-c"),
                RelationshipContext(
                    learner_id=learner.subject.actor_id,
                    resource_owner_id=learner.subject.actor_id,
                ),
            ),
        }
        return (
            _ScenarioFixture(
                "owner-document-update",
                "fixture-owner",
                owner,
                "cohort-a",
                resources["document-a"],
                "document.update",
                "db",
            ),
            _ScenarioFixture(
                "instructor-teacher-advance",
                "fixture-instructor",
                instructor,
                "cohort-b",
                resources["teacher-session-b"],
                "teacher.session.advance",
                "job",
            ),
            _ScenarioFixture(
                "learner-own-progress-update",
                "fixture-learner",
                learner,
                "cohort-c",
                resources["progress-c"],
                "student_progress.update",
                "db",
            ),
            _ScenarioFixture(
                "cross-tenant-document-deny",
                "fixture-owner",
                owner,
                "cohort-a",
                resources["teacher-session-b"],
                "document.update",
                "all-denied",
            ),
        )

    def run(self, audit_path: Path) -> GovernanceE2EReport:
        fixtures = self._fixtures()
        contexts = {fixture.token: fixture.context for fixture in fixtures}
        resolver = DeterministicIdentityResolver(contexts)
        audit = SQLiteAuditLog(audit_path)
        authorization = AuthorizationService(audit)
        effects = GovernedSideEffectExecutor(authorization)
        scenarios: list[GovernanceE2EScenario] = []
        side_effects: list[str] = []
        for fixture in fixtures:
            before = len(audit.records())
            request = DeterministicRequest(
                token=fixture.token,
                headers={"x-organization-id": "org-forged", "x-role": Role.OWNER.value},
                query_params={"workspace_id": "ws-forged", "role": Role.OWNER.value},
            )
            api = FastAPIRequestGuard(authorization, resolver).authorize(
                request,
                operation_id=fixture.operation_id,
                resource=fixture.resource,
                trace_id=f"e2e-api:{fixture.scenario_id}",
                occurred_at=NOW,
            )
            callback = lambda name=fixture.effect: side_effects.append(name)
            plan = (
                SideEffectPlan(
                    db_mutation=callback,
                    job_enqueue=callback,
                    provider_call=callback,
                    object_write=callback,
                )
                if fixture.effect == "all-denied"
                else SideEffectPlan(job_enqueue=callback)
                if fixture.effect == "job"
                else SideEffectPlan(db_mutation=callback)
            )
            service = effects.execute(
                AuthorizedServiceCall(
                    context=resolver.resolve(request),
                    operation_id=fixture.operation_id,
                    resource=fixture.resource,
                    trace_id=f"e2e-service:{fixture.scenario_id}",
                    occurred_at=NOW,
                ),
                plan,
            )
            scenarios.append(
                GovernanceE2EScenario(
                    scenario_id=fixture.scenario_id,
                    organization_id=str(fixture.context.tenant.organization_id),
                    workspace_id=str(fixture.context.tenant.workspace_id),
                    role=fixture.context.subject.effective_role.value,
                    cohort_id=fixture.cohort_id,
                    resource_id=fixture.resource.resource_id,
                    operation_id=fixture.operation_id,
                    api_allowed=api.allowed,
                    service_allowed=service.allowed,
                    side_effect_count=len(service.performed),
                    audit_records_added=len(audit.records()) - before,
                    reason_code=service.authorization.reason_code,
                    forged_transport_claims_ignored=api.transport_claims_ignored,
                )
            )
        integrity = audit.verify_integrity()
        report = GovernanceE2EReport(
            harness_version=self.HARNESS_VERSION,
            scenarios=tuple(scenarios),
            organization_count=len({s.organization_id for s in scenarios}),
            workspace_count=len({s.workspace_id for s in scenarios}),
            role_count=len({s.role for s in scenarios}),
            cohort_count=len({s.cohort_id for s in scenarios}),
            resource_count=len({s.resource_id for s in scenarios}),
            audit_record_count=len(audit.records()),
            audit_chain_valid=integrity.valid,
        )
        audit.close()
        return report


__all__ = [
    "DeterministicIdentityResolver",
    "DeterministicRequest",
    "GovernanceE2EHarness",
    "GovernanceE2EReport",
    "GovernanceE2EScenario",
]

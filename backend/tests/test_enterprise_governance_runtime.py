from __future__ import annotations

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
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    RelationshipContext,
    ResourceType,
    Role,
)
from app.governance.runtime import (
    AuthorizedOperation,
    AuthorizedQueryPolicy,
    AuthorizedResourceCandidate,
    GovernanceEnforcementService,
    ResourceVisibilityFilter,
)


NOW = datetime(2026, 8, 26, 13, 0, tzinfo=timezone.utc)


def actor(role: Role, *, subject_id: str = "actor-a") -> GovernanceActorAdapter:
    context = ActorContext(
        subject_id=subject_id,
        actor_kind=ActorKind.HUMAN,
        issuer="https://issuer.example.test",
        token_id="token-" + subject_id,
        session_id="session-" + subject_id,
        request_id="request-" + subject_id,
        authentication_time=NOW,
        authentication_strength="mfa",
        organization_id="org-a",
        workspace_id="ws-a",
        membership_id="membership-" + subject_id,
        roles=(role.value,),
    )
    return GovernanceActorAdapter(
        actor=context,
        effective_role=role,
        workspace_memberships=frozenset({"ws-a"}),
    )


def tenant() -> TenantContext:
    return TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id="org-a",
        workspace_id="ws-a",
    )


def scope(workspace_id: str = "ws-a") -> ResourceScope:
    return ResourceScope(
        scope_type=ResourceScopeType.WORKSPACE,
        organization_id="org-a",
        workspace_id=workspace_id,
        classification=ResourceClassification.CONFIDENTIAL,
    )


def services(tmp_path: Path):
    audit = SQLiteAuditLog(tmp_path / "runtime-audit.sqlite3")
    authorization = AuthorizationService(audit)
    return authorization, audit


def test_enforcement_denial_invokes_zero_business_mutations(tmp_path: Path) -> None:
    authorization, audit = services(tmp_path)
    enforcement = GovernanceEnforcementService(authorization)
    mutations: list[str] = []
    receipt = enforcement.enforce_write(
        AuthorizedOperation(
            subject=actor(Role.LEARNER, subject_id="learner-a"),
            tenant=tenant(),
            action=Action.MANAGE,
            resource=ResourceType.MEMBERSHIP,
            resource_id="membership-target",
            resource_scope=scope(),
            trace_id="trace-denied-mutation",
            occurred_at=NOW,
        ),
        lambda: mutations.append("must-not-run"),
    )
    assert receipt.allowed is False
    assert receipt.mutation_performed is False
    assert receipt.result is None
    assert mutations == []
    assert audit.records()[-1].envelope.outcome == "DENY"


def test_enforcement_allows_authorized_write_only_after_audit(tmp_path: Path) -> None:
    authorization, audit = services(tmp_path)
    enforcement = GovernanceEnforcementService(authorization)
    observed: list[int] = []

    def mutation() -> str:
        observed.append(len(audit.records()))
        return "created"

    receipt = enforcement.enforce_write(
        AuthorizedOperation(
            subject=actor(Role.ADMIN, subject_id="admin-a"),
            tenant=tenant(),
            action=Action.CREATE,
            resource=ResourceType.MEMBERSHIP,
            resource_id="membership-new",
            resource_scope=scope(),
            trace_id="trace-allowed-mutation",
            occurred_at=NOW,
        ),
        mutation,
    )
    assert receipt.allowed and receipt.mutation_performed
    assert receipt.result == "created"
    assert observed == [1]


def test_visibility_filter_removes_overfetched_list_search_and_detail_rows(
    tmp_path: Path,
) -> None:
    authorization, audit = services(tmp_path)
    visibility = ResourceVisibilityFilter(authorization)
    learner = actor(Role.LEARNER, subject_id="learner-a")
    policy = AuthorizedQueryPolicy(
        subject=learner,
        tenant=tenant(),
        action=Action.READ,
        resource=ResourceType.STUDENT_PROGRESS,
        trace_id="trace-overfetch",
        occurred_at=NOW,
    )
    candidates = (
        AuthorizedResourceCandidate(
            resource_id="progress-own",
            resource_scope=scope(),
            payload={"id": "progress-own", "score": 90},
            relationships=RelationshipContext(
                learner_id="learner-a", resource_owner_id="learner-a"
            ),
        ),
        AuthorizedResourceCandidate(
            resource_id="progress-foreign",
            resource_scope=scope(),
            payload={"id": "progress-foreign", "score": 10},
            relationships=RelationshipContext(
                learner_id="learner-b", resource_owner_id="learner-b"
            ),
        ),
    )
    assert visibility.list(policy, candidates) == ({"id": "progress-own", "score": 90},)
    assert visibility.search(policy, candidates) == ({"id": "progress-own", "score": 90},)
    assert visibility.detail(policy, candidates[1]) is None
    assert all("progress-foreign" not in str(record.to_public_dict()) for record in audit.records())


def test_export_filter_rechecks_each_resource_scope(tmp_path: Path) -> None:
    authorization, _audit = services(tmp_path)
    visibility = ResourceVisibilityFilter(authorization)
    owner = actor(Role.ORGANIZATION_OWNER, subject_id="owner-a")
    policy = AuthorizedQueryPolicy(
        subject=owner,
        tenant=tenant(),
        action=Action.EXPORT,
        resource=ResourceType.STUDENT_PROGRESS,
        trace_id="trace-export-filter",
        occurred_at=NOW,
    )
    candidates = (
        AuthorizedResourceCandidate("inside", scope(), {"id": "inside"}),
        AuthorizedResourceCandidate("outside", scope("ws-b"), {"id": "outside"}),
    )
    assert visibility.export(policy, candidates) == ({"id": "inside"},)


def test_bulk_authorization_uses_one_aggregate_audit_for_one_thousand_rows(
    tmp_path: Path,
) -> None:
    authorization, audit = services(tmp_path)
    visibility = ResourceVisibilityFilter(authorization)
    learner = actor(Role.LEARNER, subject_id="learner-a")
    policy = AuthorizedQueryPolicy(
        subject=learner,
        tenant=tenant(),
        action=Action.READ,
        resource=ResourceType.STUDENT_PROGRESS,
        trace_id="trace-bulk-1000",
        occurred_at=NOW,
    )
    candidates = tuple(
        AuthorizedResourceCandidate(
            resource_id=f"progress-{index}",
            resource_scope=scope(),
            payload=index,
            relationships=RelationshipContext(
                learner_id="learner-a" if index % 2 == 0 else "learner-b",
                resource_owner_id="learner-a" if index % 2 == 0 else "learner-b",
            ),
        )
        for index in range(1_000)
    )
    decision = visibility.authorize(policy, candidates)
    assert decision.candidate_count == 1_000
    assert len(decision.allowed_resource_ids) == 500
    assert len(decision.denied) == 500
    assert decision.default_deny_preserved
    assert len(audit.records()) == 1
    assert audit.records()[0].envelope.outcome == "PARTIAL"

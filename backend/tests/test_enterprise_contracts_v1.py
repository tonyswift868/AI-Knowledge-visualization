from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.enterprise.contracts_v1 import (
    ENTERPRISE_CONTEXT_CONTRACT_VERSION,
    AccessDecision,
    AccessDecisionKind,
    ActorContext,
    ActorKind,
    AuditEnvelope,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
    compute_contract_hashes,
    load_frozen_contract_hashes,
    verify_frozen_contract_hashes,
)


NOW = datetime(2026, 8, 26, 3, 0, tzinfo=timezone.utc)


def _actor() -> ActorContext:
    return ActorContext(
        subject_id="subject-1",
        actor_kind=ActorKind.HUMAN,
        issuer="https://issuer.example.test",
        token_id="token-1",
        session_id="session-1",
        request_id="request-1",
        authentication_time=NOW,
        authentication_strength="mfa",
        organization_id="org-1",
        workspace_id="workspace-1",
        membership_id="membership-1",
        roles=("learner",),
    )


def test_contract_models_are_versioned_frozen_and_forbid_extra_fields() -> None:
    actor = _actor()
    assert actor.schema_version == ENTERPRISE_CONTEXT_CONTRACT_VERSION
    with pytest.raises(ValidationError):
        ActorContext(**{**actor.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        ActorContext(**{**actor.model_dump(), "authentication_time": datetime(2026, 8, 26, 3, 0)})
    with pytest.raises(ValidationError):
        ActorContext(**{**actor.model_dump(), "roles": ("learner", "learner")})
    with pytest.raises(ValidationError):
        actor.subject_id = "changed"  # type: ignore[misc]


def test_personal_local_mode_requires_no_login_or_organization_context() -> None:
    tenant = TenantContext(mode=TenantMode.PERSONAL_LOCAL)
    scope = ResourceScope(
        scope_type=ResourceScopeType.PERSONAL,
        owner_subject_id="local-user",
        classification=ResourceClassification.CONFIDENTIAL,
    )
    assert tenant.organization_id is None
    assert tenant.workspace_id is None
    assert scope.owner_subject_id == "local-user"


def test_enterprise_mode_and_scopes_fail_closed_without_tenant_identity() -> None:
    with pytest.raises(ValidationError):
        TenantContext(mode=TenantMode.ENTERPRISE_SERVER)
    with pytest.raises(ValidationError):
        ResourceScope(
            scope_type=ResourceScopeType.WORKSPACE,
            workspace_id="workspace-1",
            classification=ResourceClassification.INTERNAL,
        )
    tenant = TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id="org-1",
        workspace_id="workspace-1",
    )
    assert tenant.mode is TenantMode.ENTERPRISE_SERVER


def test_decision_and_audit_envelopes_preserve_actor_resource_and_trace_context() -> None:
    actor = _actor()
    resource = ResourceScope(
        scope_type=ResourceScopeType.WORKSPACE,
        organization_id="org-1",
        workspace_id="workspace-1",
        classification=ResourceClassification.RESTRICTED,
    )
    decision = AccessDecision(
        decision=AccessDecisionKind.DENY,
        policy_id="policy-1",
        reason_code="workspace_mismatch",
        actor=actor,
        resource=resource,
        action="document.read",
        evaluated_at=NOW,
    )
    audit = AuditEnvelope(
        event_id="event-1",
        actor_context=actor,
        action=decision.action,
        resource_type="document",
        resource_id="document-1",
        outcome=decision.decision.value,
        reason_code=decision.reason_code,
        occurred_at=NOW,
        request_id=actor.request_id,
        trace_id="trace-1",
        metadata_redacted={"document_kind": "pdf"},
    )
    assert decision.decision is AccessDecisionKind.DENY
    assert audit.trace_id == "trace-1"
    with pytest.raises(ValidationError):
        AuditEnvelope(**{**audit.model_dump(), "metadata_redacted": {"api_key": "redacted"}})


def test_frozen_contract_hash_manifest_matches_canonical_schemas() -> None:
    frozen = load_frozen_contract_hashes()
    assert frozen["contract_version"] == ENTERPRISE_CONTEXT_CONTRACT_VERSION
    assert frozen["hash_algorithm"] == "sha256"
    assert frozen["hashes"] == compute_contract_hashes()
    assert verify_frozen_contract_hashes() is True


def test_mode_boundary_freezes_personal_compatibility_and_enterprise_requirements() -> None:
    path = (
        Path(__file__).parents[1]
        / "app"
        / "enterprise"
        / "contracts_v1"
        / "mode_boundary.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    personal = payload["modes"]["PERSONAL_LOCAL"]
    enterprise = payload["modes"]["ENTERPRISE_SERVER"]
    assert personal["login_required"] is False
    assert personal["organization_required"] is False
    assert personal["canonical_persistence"] == "LOCAL_STORES_SUPPORTED"
    assert enterprise["authentication_protocol"] == "OIDC"
    assert enterprise["organization_required"] is True
    assert enterprise["canonical_persistence"] == "POSTGRESQL"
    assert enterprise["audit_required"] is True


def test_migration_ownership_registry_has_one_owner_per_resource() -> None:
    path = Path(__file__).parents[2] / "enterprise_e1" / "MIGRATION_OWNERSHIP.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["shared_contract_version"] == ENTERPRISE_CONTEXT_CONTRACT_VERSION
    assert payload["canonical_base"]["tag"] == "mentorflow-enterprise-e1-base-20260826"
    resources: list[tuple[str, str]] = []
    owners: set[str] = set()
    for entry in payload["owners"]:
        owners.add(entry["owner"])
        resources.extend((resource["kind"], resource["name"]) for resource in entry["resources"])
    assert owners == {"E1-02", "E1-03", "E1-05"}
    assert len(resources) == len(set(resources))
    assert all(
        resource["status"] == "RESERVED_NOT_CREATED"
        for entry in payload["owners"]
        for resource in entry["resources"]
    )

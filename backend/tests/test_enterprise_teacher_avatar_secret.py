from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.enterprise.contracts_v1 import ActorContext, ActorKind, TenantContext, TenantMode
from app.governance.audit import SQLiteAuditLog
from app.governance.authorization import AuthorizationService
from app.governance.models import GovernanceActorAdapter, Role
from app.governance.provider_governance import (
    ProviderConfig,
    ProviderCredentialRef,
    ProviderGovernanceError,
    ProviderGovernanceService,
)
from app.teacher_avatar_registry import (
    EnterpriseVroidHubCredentialAdapter,
    TeacherAvatarError,
    VroidHubCredentialSource,
    list_models,
)


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)
MODEL = "vroid-hub-api"
OLD_REFERENCE = "mfcred_vroid_reference_old_123"
NEW_REFERENCE = "mfcred_vroid_reference_new_456"
PLAINTEXT = "vroid-plaintext-must-not-escape"


def _owner() -> GovernanceActorAdapter:
    return GovernanceActorAdapter(
        actor=ActorContext(
            subject_id="owner-a",
            actor_kind=ActorKind.HUMAN,
            issuer="https://issuer.example.test",
            token_id="token-owner",
            session_id="session-owner",
            request_id="request-owner",
            authentication_time=NOW,
            authentication_strength="mfa",
            organization_id="org-a",
            workspace_id="ws-a",
            membership_id="membership-owner",
            roles=(Role.OWNER.value,),
        ),
        effective_role=Role.OWNER,
        workspace_memberships=frozenset({"ws-a"}),
    )


def _tenant() -> TenantContext:
    return TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id="org-a",
        workspace_id="ws-a",
    )


def _registered_governance(tmp_path):
    audit = SQLiteAuditLog(tmp_path / "vroid-governance.sqlite3")
    governance = ProviderGovernanceService(AuthorizationService(audit))
    governance.register(
        ProviderConfig(
            config_id="vroid-hub-org-a",
            organization_id="org-a",
            provider="vroid-hub",
            secret_reference=ProviderCredentialRef(OLD_REFERENCE),
            approved_models=frozenset({MODEL}),
        ),
        subject=_owner(),
        tenant=_tenant(),
        trace_id="trace-vroid-register",
    )
    return governance, audit


def _adapter(governance: ProviderGovernanceService) -> EnterpriseVroidHubCredentialAdapter:
    return EnterpriseVroidHubCredentialAdapter(
        governance=governance,
        config_id="vroid-hub-org-a",
        model=MODEL,
    )


def test_personal_local_legacy_compatibility_is_preserved(monkeypatch) -> None:
    monkeypatch.setenv("VROID_HUB_CLIENT_ID", "local-client")
    monkeypatch.setenv("VROID_HUB_CLIENT_SECRET", PLAINTEXT)

    guidance = list_models()["provider_guidance"]

    assert guidance["vroid_hub_configured"] is True
    assert guidance["credential_source"] == (
        VroidHubCredentialSource.LEGACY_PERSONAL_LOCAL_COMPATIBILITY.value
    )
    assert guidance["credential_status"] == "ACTIVE"
    assert PLAINTEXT not in json.dumps(guidance)


def test_enterprise_missing_credential_never_falls_back_to_legacy_environment(monkeypatch) -> None:
    def forbidden_getenv(*_args, **_kwargs):
        raise AssertionError("enterprise credential path must not read legacy environment")

    monkeypatch.setattr("app.teacher_avatar_registry.os.getenv", forbidden_getenv)
    guidance = list_models(tenant_mode=TenantMode.ENTERPRISE_SERVER)["provider_guidance"]

    assert guidance["vroid_hub_configured"] is False
    assert guidance["credential_source"] == (
        VroidHubCredentialSource.ENTERPRISE_CREDENTIAL_REFERENCE_REQUIRED.value
    )
    assert guidance["credential_status"] == "MISSING_CREDENTIAL_REFERENCE"

    with pytest.raises(TeacherAvatarError, match="canonical TenantMode"):
        list_models(tenant_mode="ENTERPRISE_SERVER")  # type: ignore[arg-type]


def test_enterprise_reference_is_secret_free_in_api_audit_log_trace_and_cache(
    tmp_path,
    caplog,
) -> None:
    governance, audit = _registered_governance(tmp_path)
    adapter = _adapter(governance)
    guidance = list_models(
        tenant_mode=TenantMode.ENTERPRISE_SERVER,
        enterprise_credentials=adapter,
    )["provider_guidance"]
    grant = adapter.authorize_new_session()
    resolver_calls: list[str] = []

    def resolve(reference: str) -> str:
        resolver_calls.append(reference)
        return PLAINTEXT

    result = adapter.execute_with_secret(
        grant,
        lambda secret: "provider-session-created" if secret == PLAINTEXT else "unexpected",
        resolver=resolve,
    )
    serialized = json.dumps(
        {
            "api": guidance,
            "audit_and_trace": [record.to_public_dict() for record in audit.records()],
            "cache_identity": governance.get_internal("vroid-hub-org-a").cache_identity(MODEL),
            "logs": [record.getMessage() for record in caplog.records],
            "result": result,
        },
        ensure_ascii=False,
    )

    assert guidance["vroid_hub_configured"] is True
    assert guidance["credential_source"] == (
        VroidHubCredentialSource.ENTERPRISE_PROVIDER_GOVERNANCE.value
    )
    assert guidance["credential_revision"] == 1
    assert resolver_calls == [OLD_REFERENCE]
    assert result == "provider-session-created"
    assert PLAINTEXT not in serialized
    assert OLD_REFERENCE not in serialized
    assert "secret_reference" not in serialized


def test_revoked_credential_cannot_create_or_continue_provider_session(tmp_path) -> None:
    governance, _audit = _registered_governance(tmp_path)
    adapter = _adapter(governance)
    grant = adapter.authorize_new_session()
    governance.revoke(
        "vroid-hub-org-a",
        subject=_owner(),
        tenant=_tenant(),
        trace_id="trace-vroid-revoke",
        now=NOW + timedelta(minutes=1),
    )
    resolver_calls: list[str] = []

    with pytest.raises(ProviderGovernanceError, match="PROVIDER_CONFIG_NOT_ACTIVE"):
        adapter.authorize_new_session()
    with pytest.raises(ProviderGovernanceError, match="PROVIDER_CONFIG_NOT_ACTIVE"):
        adapter.execute_with_secret(
            grant,
            lambda _secret: "must-not-run",
            resolver=lambda reference: resolver_calls.append(reference) or PLAINTEXT,
        )

    assert resolver_calls == []
    guidance = list_models(
        tenant_mode=TenantMode.ENTERPRISE_SERVER,
        enterprise_credentials=adapter,
    )["provider_guidance"]
    assert guidance["vroid_hub_configured"] is False
    assert guidance["credential_status"] == "REVOKED"


def test_rotated_credential_fences_old_session_and_resolves_new_revision_only(tmp_path) -> None:
    governance, _audit = _registered_governance(tmp_path)
    adapter = _adapter(governance)
    old_grant = adapter.authorize_new_session()
    governance.begin_rotation(
        "vroid-hub-org-a",
        subject=_owner(),
        tenant=_tenant(),
        trace_id="trace-vroid-rotate",
        now=NOW + timedelta(minutes=1),
    )
    governance.complete_rotation(
        "vroid-hub-org-a",
        ProviderCredentialRef(NEW_REFERENCE),
        subject=_owner(),
        tenant=_tenant(),
        trace_id="trace-vroid-rotate-complete",
        now=NOW + timedelta(minutes=2),
    )
    resolver_calls: list[str] = []

    with pytest.raises(ProviderGovernanceError, match="PROVIDER_SESSION_STALE"):
        adapter.execute_with_secret(
            old_grant,
            lambda _secret: "must-not-run",
            resolver=lambda reference: resolver_calls.append(reference) or PLAINTEXT,
        )
    new_grant = adapter.authorize_new_session()
    assert adapter.execute_with_secret(
        new_grant,
        lambda secret: "rotated-session" if secret == PLAINTEXT else "unexpected",
        resolver=lambda reference: resolver_calls.append(reference) or PLAINTEXT,
    ) == "rotated-session"

    assert resolver_calls == [NEW_REFERENCE]
    guidance = list_models(
        tenant_mode=TenantMode.ENTERPRISE_SERVER,
        enterprise_credentials=adapter,
    )["provider_guidance"]
    assert guidance["credential_revision"] == 2
    assert guidance["credential_status"] == "ACTIVE"


def test_secret_bearing_resolver_and_provider_errors_are_replaced_with_safe_text(tmp_path) -> None:
    governance, _audit = _registered_governance(tmp_path)
    adapter = _adapter(governance)
    grant = adapter.authorize_new_session()

    with pytest.raises(TeacherAvatarError) as missing:
        adapter.execute_with_secret(
            grant,
            lambda _secret: "unused",
            resolver=lambda _reference: (_ for _ in ()).throw(RuntimeError(PLAINTEXT)),
        )
    assert missing.value.code == "VROID_HUB_CREDENTIAL_UNAVAILABLE"
    assert PLAINTEXT not in str(missing.value)
    assert OLD_REFERENCE not in str(missing.value)

    with pytest.raises(TeacherAvatarError) as provider_error:
        adapter.execute_with_secret(
            grant,
            lambda secret: (_ for _ in ()).throw(RuntimeError(secret)),
            resolver=lambda _reference: PLAINTEXT,
        )
    assert provider_error.value.code == "VROID_HUB_EXECUTION_FAILED"
    assert PLAINTEXT not in str(provider_error.value)
    assert OLD_REFERENCE not in str(provider_error.value)

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    TenantContext,
    TenantMode,
    verify_frozen_contract_hashes,
)
from app.enterprise.identity.config import (
    EnterpriseIdentityConfig,
    IdentityConfigurationError,
)
from app.enterprise.identity.context import (
    IdentityContextError,
    enterprise_request_context,
    personal_request_context,
)
from app.enterprise.identity.models import (
    AuthenticatedPrincipal,
    BusinessRole,
    EnterpriseSession,
    MembershipGrant,
    MembershipStatus,
    ServiceIdentityGrant,
    ServicePermission,
)


NOW = datetime(2026, 8, 26, 10, 30, tzinfo=timezone.utc)
ISSUER = "https://identity.example.test/realms/mentorflow"


def _principal(*, service: bool = False) -> AuthenticatedPrincipal:
    client_id = "mentorflow-worker" if service else "mentorflow-web"
    return AuthenticatedPrincipal(
        issuer=ISSUER,
        audiences=("mentorflow-api",),
        token_id="token-001",
        subject="service-subject" if service else "human-subject",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        authentication_time=NOW - timedelta(minutes=2),
        authentication_strength="mfa" if not service else "client-credentials",
        identity_provider_session_id="keycloak-session-001",
        preferred_username=(f"service-account-{client_id}" if service else "learner"),
        client_id=client_id,
        actor_kind=ActorKind.SERVICE if service else ActorKind.HUMAN,
    )


def _session(*, service: bool = False) -> EnterpriseSession:
    return EnterpriseSession(
        session_id="enterprise-session-0000000000000001",
        principal=_principal(service=service),
        created_at=NOW - timedelta(minutes=2),
        refreshed_at=NOW - timedelta(minutes=1),
        session_expires_at=NOW + timedelta(hours=4),
        access_token_expires_at=NOW + timedelta(minutes=5),
        selected_organization_id="org-1",
        selected_workspace_id="workspace-1",
    )


class _Directory:
    def __init__(
        self,
        *,
        membership: MembershipGrant | None = None,
        service: ServiceIdentityGrant | None = None,
    ) -> None:
        self.membership = membership
        self.service = service

    def resolve_membership(self, **_kwargs) -> MembershipGrant | None:
        return self.membership

    def resolve_service_identity(self, **_kwargs) -> ServiceIdentityGrant | None:
        return self.service


def _membership(*, status: MembershipStatus = MembershipStatus.ACTIVE) -> MembershipGrant:
    return MembershipGrant(
        membership_id="membership-1",
        issuer=ISSUER,
        subject="human-subject",
        organization_id="org-1",
        workspace_id="workspace-1",
        roles=(BusinessRole.LEARNER, BusinessRole.INSTRUCTOR),
        status=status,
    )


def _service_grant(*, status: MembershipStatus = MembershipStatus.ACTIVE) -> ServiceIdentityGrant:
    return ServiceIdentityGrant(
        service_id="service-1",
        issuer=ISSUER,
        subject="service-subject",
        client_id="mentorflow-worker",
        organization_id="org-1",
        workspace_id="workspace-1",
        permissions=(ServicePermission.JOB_READ, ServicePermission.JOB_EXECUTE),
        status=status,
    )


def test_personal_local_is_no_login_and_uses_frozen_tenant_contract() -> None:
    config = EnterpriseIdentityConfig.from_env({})
    context = personal_request_context()

    assert config.mode is TenantMode.PERSONAL_LOCAL
    assert config.keycloak is None
    assert context.actor is None
    assert type(context.tenant) is TenantContext
    assert context.tenant == TenantContext(mode=TenantMode.PERSONAL_LOCAL)
    assert verify_frozen_contract_hashes() is True


def test_enterprise_configuration_is_fail_closed() -> None:
    with pytest.raises(IdentityConfigurationError):
        EnterpriseIdentityConfig.from_env(
            {"MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER"}
        )
    with pytest.raises(IdentityConfigurationError):
        EnterpriseIdentityConfig.from_env(
            {
                "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
                "MENTORFLOW_KEYCLOAK_ISSUER": ISSUER,
                "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-web",
                "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api",
                "MENTORFLOW_KEYCLOAK_REDIRECT_URI": "https://mentorflow.example/callback",
                "MENTORFLOW_KEYCLOAK_ALGORITHMS": "HS256",
            }
        )
    with pytest.raises(IdentityConfigurationError):
        EnterpriseIdentityConfig.from_env(
            {
                "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
                "MENTORFLOW_KEYCLOAK_ISSUER": "http://identity.example.test/realms/mentorflow",
                "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-web",
                "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api",
                "MENTORFLOW_KEYCLOAK_REDIRECT_URI": "https://mentorflow.example/callback",
            }
        )


def test_enterprise_public_issuer_remains_the_jwks_trust_authority() -> None:
    issuer = "https://localhost:18080/realms/mentorflow"
    config = EnterpriseIdentityConfig.from_env(
        {
            "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
            "MENTORFLOW_KEYCLOAK_ISSUER": issuer,
            "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-web",
            "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api",
            "MENTORFLOW_KEYCLOAK_REDIRECT_URI": (
                "https://localhost:18080/api/enterprise/auth/callback"
            ),
        }
    )

    assert config.keycloak is not None
    assert config.keycloak.issuer == issuer
    assert config.keycloak.jwks_uri == (
        issuer + "/protocol/openid-connect/certs"
    )


def test_human_membership_builds_only_frozen_actor_and_tenant_models() -> None:
    context = enterprise_request_context(
        session=_session(),
        directory=_Directory(membership=_membership()),
        request_id="request-1",
        now=NOW,
    )

    assert type(context.actor) is ActorContext
    assert type(context.tenant) is TenantContext
    assert context.actor is not None
    assert context.actor.actor_kind is ActorKind.HUMAN
    assert context.actor.membership_id == "membership-1"
    assert context.actor.roles == ("LEARNER", "INSTRUCTOR")
    assert context.actor.service_permissions == ()
    assert context.tenant.mode is TenantMode.ENTERPRISE_SERVER


@pytest.mark.parametrize("status", [MembershipStatus.SUSPENDED, MembershipStatus.REVOKED])
def test_human_membership_rechecks_current_status_and_fails_closed(
    status: MembershipStatus,
) -> None:
    with pytest.raises(IdentityContextError) as caught:
        enterprise_request_context(
            session=_session(),
            directory=_Directory(membership=_membership(status=status)),
            request_id="request-1",
            now=NOW,
        )
    assert caught.value.code == "MEMBERSHIP_NOT_ACTIVE"


def test_service_identity_uses_permissions_not_business_roles() -> None:
    context = enterprise_request_context(
        session=_session(service=True),
        directory=_Directory(service=_service_grant()),
        request_id="request-service",
        now=NOW,
    )

    assert context.actor is not None
    assert context.actor.actor_kind is ActorKind.SERVICE
    assert context.actor.roles == ()
    assert context.actor.membership_id is None
    assert context.actor.service_permissions == ("JOB_READ", "JOB_EXECUTE")


def test_service_identity_grant_must_match_signed_principal() -> None:
    wrong_client = _service_grant().model_copy(update={"client_id": "different-worker"})
    with pytest.raises(IdentityContextError) as caught:
        enterprise_request_context(
            session=_session(service=True),
            directory=_Directory(service=wrong_client),
            request_id="request-service",
            now=NOW,
        )
    assert caught.value.code == "SERVICE_IDENTITY_NOT_ACTIVE"


def test_selected_scope_and_directory_grant_must_match() -> None:
    wrong_scope = _membership().model_copy(update={"organization_id": "org-2"})
    with pytest.raises(IdentityContextError) as caught:
        enterprise_request_context(
            session=_session(),
            directory=_Directory(membership=wrong_scope),
            request_id="request-1",
            now=NOW,
        )
    assert caught.value.code == "MEMBERSHIP_NOT_ACTIVE"


def test_expired_session_principal_is_rejected_before_directory_resolution() -> None:
    session = _session().model_copy(
        update={
            "principal": _principal().model_copy(
                update={"expires_at": NOW - timedelta(seconds=1)}
            )
        }
    )
    with pytest.raises(IdentityContextError) as caught:
        enterprise_request_context(
            session=session,
            directory=_Directory(membership=_membership()),
            request_id="request-1",
            now=NOW,
        )
    assert caught.value.code == "OIDC_TOKEN_EXPIRED"

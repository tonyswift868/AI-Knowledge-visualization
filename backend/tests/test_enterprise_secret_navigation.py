from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.enterprise.contracts_v1 import ActorContext, ActorKind, TenantContext, TenantMode
from app.governance.models import GovernanceActorAdapter, Role
from app.governance.secret_access import SecretAccessVerifier
from app.governance.ui_seams import EnterpriseNavigationModel


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)
APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def actor(role: Role, *, workspace_id: str = "ws-math") -> GovernanceActorAdapter:
    return GovernanceActorAdapter(
        actor=ActorContext(
            subject_id=f"subject-{role.value.lower()}",
            actor_kind=ActorKind.HUMAN,
            issuer="https://issuer.example.test",
            token_id=f"token-{role.value.lower()}",
            session_id=f"session-{role.value.lower()}",
            request_id=f"request-{role.value.lower()}",
            authentication_time=NOW,
            authentication_strength="mfa",
            organization_id="org-a",
            workspace_id=workspace_id,
            membership_id=f"membership-{role.value.lower()}",
            roles=(role.value,),
        ),
        effective_role=role,
        workspace_memberships=frozenset({workspace_id}),
    )


def tenant(workspace_id: str) -> TenantContext:
    return TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id="org-a",
        workspace_id=workspace_id,
    )


def test_whole_backend_secret_access_policy_reports_out_of_scope_plaintext_read() -> None:
    report = SecretAccessVerifier().scan(APP_ROOT)
    assert report.complete is False
    assert report.governed_domains == ("PROVIDER", "OIDC", "DATABASE", "OBJECT_STORE")
    assert "provider_runtime.py" in report.resolver_consumers
    assert "minimax_teacher_voice.py" in report.resolver_consumers
    assert "tencent_sms.py" in report.resolver_consumers
    assert report.admin_plaintext_secret_fields == ()
    assert tuple((item.path, item.symbol) for item in report.violations) == (
        ("teacher_avatar_registry.py", "VROID_HUB_CLIENT_SECRET"),
    )


def test_secret_policy_detects_direct_plaintext_environment_access(tmp_path: Path) -> None:
    source = tmp_path / "provider_adapter.py"
    source.write_text(
        "import os\nvalue = os.getenv('PROVIDER_API_KEY')\n",
        encoding="utf-8",
    )
    report = SecretAccessVerifier().scan(tmp_path)
    assert report.complete is False
    assert report.violations[0].symbol == "PROVIDER_API_KEY"


def test_enterprise_navigation_reprojects_role_and_workspace_immediately() -> None:
    owner = EnterpriseNavigationModel.for_context(actor(Role.OWNER), tenant("ws-math"))
    manager = EnterpriseNavigationModel.for_context(
        actor(Role.CONTENT_MANAGER, workspace_id="ws-science"),
        tenant("ws-science"),
    )
    assert owner.context_key != manager.context_key
    assert owner.role is Role.OWNER
    assert manager.role is Role.CONTENT_MANAGER
    assert owner.entries != manager.entries
    assert owner.authorization_still_required is True
    assert manager.to_public_dict()["contract_version"] == "enterprise-context.v1"


def test_navigation_rejects_untrusted_cross_tenant_switch() -> None:
    with pytest.raises(ValueError, match="organization must match"):
        EnterpriseNavigationModel.for_context(
            actor(Role.LEARNER),
            TenantContext(
                mode=TenantMode.ENTERPRISE_SERVER,
                organization_id="org-foreign",
                workspace_id="ws-math",
            ),
        )
    with pytest.raises(ValueError, match="active actor membership"):
        EnterpriseNavigationModel.for_context(actor(Role.LEARNER), tenant("ws-foreign"))

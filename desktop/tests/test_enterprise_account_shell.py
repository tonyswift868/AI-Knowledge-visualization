from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest
import httpx

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from desktop.account_identity import (  # noqa: E402
    AccountCredentialVault,
    AccountIdentity,
    AccountPreferenceStore,
    AccountProfile,
)
from desktop.account_vault import AccountVaultManager, VaultLayout  # noqa: E402
from desktop.account_settings_page import AccountSettingsPage  # noqa: E402
from desktop.enterprise_oidc import (  # noqa: E402
    DesktopOidcConfig,
    EnterpriseOidcError,
    EnterpriseOidcLoginResult,
)
from desktop.runtime import (  # noqa: E402
    LocalService,
    clear_enterprise_account_session,
    enterprise_account_server_state,
    preflight_desktop_account_storage,
    record_enterprise_account_session,
    startup_restore_failure,
)


ISSUER = "https://identity.example.test/realms/mentorflow"


class _Settings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value

    def value(self, key: str, default: object = "") -> object:
        return self.values.get(key, default)

    def remove(self, key: str) -> None:
        self.values.pop(key, None)

    def sync(self) -> None:
        return None

    def allKeys(self) -> list[str]:
        return list(self.values)


def _identity(subject: str, name: str) -> AccountIdentity:
    return AccountIdentity(
        issuer=ISSUER,
        subject=subject,
        display_name=name,
        organization_id=f"org-{subject[-1].lower()}",
        workspace_id=f"workspace-{subject[-1].lower()}",
    )


def _state(identity: AccountIdentity) -> dict[str, object]:
    return {
        "schema_version": "mentorflow.enterprise-identity-session.v1",
        "authenticated": True,
        "account": {
            "subject_id": identity.subject,
            "display_name": identity.display_name,
        },
        "context": {
            "contract_version": "enterprise-context.v1",
            "organization_id": identity.organization_id,
            "workspace_id": identity.workspace_id,
            "roles": ["learner"],
            "context_version": 3,
        },
    }


@pytest.fixture
def vault_root() -> Path:
    scratch = Path("D:/CodexScratch/r39")
    scratch.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="account-shell-", dir=scratch))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _configure_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        DesktopOidcConfig,
        "is_configured",
        classmethod(lambda cls, source=None: True),
    )


def test_restart_validates_locker_and_server_before_mounting_saved_account(
    vault_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_enterprise_account_session()
    _configure_restore(monkeypatch)
    identity = _identity("subject-A", "Account A")
    profile = AccountIdentity(
        issuer=ISSUER,
        subject="subject-A",
        display_name="Old Account A",
    ).profile()
    settings = _Settings()
    preferences = AccountPreferenceStore(settings)
    preferences.save_target(profile, sync_cursor="3")
    restore_calls: list[AccountProfile] = []

    def restore(target: AccountProfile, _credentials) -> EnterpriseOidcLoginResult:
        restore_calls.append(target)
        return EnterpriseOidcLoginResult(
            identity=identity,
            access_token="locker-access",
            refresh_token="locker-refresh",
            server_state=_state(identity),
            restored=True,
        )

    monkeypatch.setattr(
        "desktop.enterprise_oidc.restore_enterprise_account_from_env",
        restore,
    )
    manager = AccountVaultManager(VaultLayout(vault_root))
    env = {"MENTORFLOW_ACCOUNT_VAULT_ROOT": str(vault_root)}

    restored_profile = preflight_desktop_account_storage(
        settings=settings,
        source=env,
    )
    assert manager.current is None
    mounted = manager.mount(restored_profile)

    assert restore_calls == [profile]
    assert restored_profile == identity.profile()
    assert mounted.profile == identity.profile()
    assert mounted.root == manager.layout.root_for_profile(identity.profile())
    assert Path(env["MENTORFLOW_USER_DATA_ROOT"]) == mounted.root
    assert Path(env["MENTORFLOW_DB"]) == mounted.root / "data" / "app.db"
    assert settings.values["account/display_name"] == "Account A"
    state = enterprise_account_server_state(identity.profile())
    assert state["authenticated"] is True
    assert state["context"] == {
        "contract_version": "enterprise-context.v1",
        "organization_id": "org-a",
        "workspace_id": "workspace-a",
        "roles": ["learner"],
        "context_version": 3,
    }
    serialized = json.dumps(settings.values, sort_keys=True)
    for secret in ("locker-access", "locker-refresh", "subject-A"):
        assert secret not in serialized
    manager.unmount()


def test_failed_restart_never_mounts_previous_enterprise_vault(
    vault_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_enterprise_account_session()
    _configure_restore(monkeypatch)
    profile = _identity("subject-A", "Account A").profile()
    settings = _Settings()
    preferences = AccountPreferenceStore(settings)
    preferences.save_target(profile)
    logged_out: list[AccountProfile] = []

    class Credentials:
        def logout(self, target: AccountProfile) -> None:
            logged_out.append(target)

    monkeypatch.setattr("desktop.runtime.AccountCredentialVault", Credentials)

    def fail_restore(_target, _credentials):
        raise EnterpriseOidcError(
            "ENTERPRISE_ACCOUNT_STATE_UNAVAILABLE",
            "server unavailable",
        )

    monkeypatch.setattr(
        "desktop.enterprise_oidc.restore_enterprise_account_from_env",
        fail_restore,
    )
    manager = AccountVaultManager(VaultLayout(vault_root))
    env = {"MENTORFLOW_ACCOUNT_VAULT_ROOT": str(vault_root)}

    selected = preflight_desktop_account_storage(
        settings=settings,
        source=env,
    )
    assert manager.current is None
    assert not manager.layout.root_for_profile(profile).exists()
    assert logged_out == [profile]
    assert selected == AccountProfile.personal_local()
    assert settings.values == {}
    assert Path(env["MENTORFLOW_USER_DATA_ROOT"]) == manager.layout.personal_local_root
    assert Path(env["MENTORFLOW_DB"]) == (
        manager.layout.personal_local_root / "data" / "app.db"
    )

    manager.mount(selected)
    assert manager.current is not None
    assert manager.current.profile == AccountProfile.personal_local()
    assert manager.current.root == manager.layout.personal_local_root
    assert "重新登录" in startup_restore_failure()
    application = QApplication.instance() or QApplication([])
    del application
    page = AccountSettingsPage(
        profile=selected,
        vault_manager=manager,
        credential_vault=Credentials(),  # type: ignore[arg-type]
        switch_account=lambda _target: None,
        logout_account=lambda: None,
        oidc_login=lambda: (_ for _ in ()).throw(RuntimeError("not called")),
    )
    assert "重新登录" in page.status.text()
    page.close()
    manager.unmount()


def test_restart_without_oidc_configuration_falls_back_before_database_open(
    vault_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_enterprise_account_session()
    monkeypatch.setattr(
        DesktopOidcConfig,
        "is_configured",
        classmethod(lambda cls, source=None: False),
    )
    profile = _identity("subject-A", "Account A").profile()
    settings = _Settings()
    AccountPreferenceStore(settings).save_target(profile)
    logged_out: list[AccountProfile] = []

    class Credentials:
        def logout(self, target: AccountProfile) -> None:
            logged_out.append(target)

    monkeypatch.setattr("desktop.runtime.AccountCredentialVault", Credentials)
    env = {"MENTORFLOW_ACCOUNT_VAULT_ROOT": str(vault_root)}

    selected = preflight_desktop_account_storage(settings=settings, source=env)

    layout = VaultLayout(vault_root)
    assert selected == AccountProfile.personal_local()
    assert logged_out == [profile]
    assert settings.values == {}
    assert Path(env["MENTORFLOW_USER_DATA_ROOT"]) == layout.personal_local_root
    assert not layout.root_for_profile(profile).exists()
    assert "重新登录" in startup_restore_failure()


def test_stale_account_pointer_cannot_bypass_personal_profile_selection(
    vault_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_enterprise_account_session()
    monkeypatch.setattr(
        DesktopOidcConfig,
        "is_configured",
        classmethod(lambda cls, source=None: False),
    )
    settings = _Settings()
    layout = VaultLayout(vault_root)
    stale_account_root = layout.accounts_root / ("a" * 64) / ("b" * 64)
    env = {
        "MENTORFLOW_ACCOUNT_VAULT_ROOT": str(vault_root),
        "MENTORFLOW_USER_DATA_ROOT": str(stale_account_root),
        "MENTORFLOW_DB": str(stale_account_root / "data" / "app.db"),
    }

    selected = preflight_desktop_account_storage(settings=settings, source=env)

    assert selected == AccountProfile.personal_local()
    assert Path(env["MENTORFLOW_USER_DATA_ROOT"]) == layout.personal_local_root
    assert Path(env["MENTORFLOW_DB"]) == layout.personal_local_root / "data" / "app.db"
    assert not stale_account_root.exists()
    assert "重新登录" in startup_restore_failure()


def test_switching_server_state_cache_never_exposes_previous_account() -> None:
    clear_enterprise_account_session()
    identity_a = _identity("subject-A", "Account A")
    identity_b = _identity("subject-B", "Account B")
    record_enterprise_account_session(identity_a.profile(), _state(identity_a))
    assert enterprise_account_server_state(identity_a.profile())["context"]

    record_enterprise_account_session(identity_b.profile(), _state(identity_b))

    assert enterprise_account_server_state(identity_a.profile()) == {}
    state_b = enterprise_account_server_state(identity_b.profile())
    assert state_b["context"] == {
        "contract_version": "enterprise-context.v1",
        "organization_id": "org-b",
        "workspace_id": "workspace-b",
        "roles": ["learner"],
        "context_version": 3,
    }
    assert "subject-A" not in json.dumps(state_b, sort_keys=True)


def test_account_settings_login_action_completes_on_qt_thread(
    vault_root: Path,
) -> None:
    clear_enterprise_account_session()
    application = QApplication.instance() or QApplication([])
    manager = AccountVaultManager(VaultLayout(vault_root))
    personal = AccountProfile.personal_local()
    manager.mount(personal)
    identity = _identity("subject-A", "Account A")
    stored: list[tuple[str, str]] = []
    switched: list[AccountIdentity] = []

    class Credentials:
        def store_oauth_tokens(
            self,
            target: AccountIdentity,
            *,
            access_token: str,
            refresh_token: str,
        ) -> dict[str, str]:
            assert target == identity
            stored.append((access_token, refresh_token))
            return {"access_token_ref": "opaque", "refresh_token_ref": "opaque"}

        def logout(self, _profile: AccountProfile) -> None:
            return None

    result = EnterpriseOidcLoginResult(
        identity=identity,
        access_token="access-secret",
        refresh_token="refresh-secret",
        server_state=_state(identity),
    )
    page = AccountSettingsPage(
        profile=personal,
        vault_manager=manager,
        credential_vault=Credentials(),  # type: ignore[arg-type]
        switch_account=lambda target: switched.append(target),
        logout_account=lambda: None,
        oidc_login=lambda: result,
    )

    page.oauth_button.click()
    deadline = time.monotonic() + 3
    while not switched and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)

    assert switched == [identity]
    assert stored == [("access-secret", "refresh-secret")]
    assert page.oauth_button.isEnabled()
    assert page.logout_button.isEnabled()
    page.close()
    manager.unmount()


def test_logout_deletes_both_locker_tokens_and_clears_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_enterprise_account_session()
    identity = _identity("subject-A", "Account A")
    profile = identity.profile()
    record_enterprise_account_session(profile, _state(identity))
    deleted: list[str] = []

    def delete_secret(*, issuer_hash: str, subject_hash: str, purpose: str) -> bool:
        assert issuer_hash == identity.issuer_hash
        assert subject_hash == identity.subject_hash
        deleted.append(purpose)
        return True

    monkeypatch.setattr("desktop.account_identity.delete_account_secret", delete_secret)

    AccountCredentialVault().logout(profile)

    assert deleted == ["oauth-access-token", "oauth-refresh-token"]
    assert enterprise_account_server_state(profile) == {}


def test_personal_local_remains_login_free_when_enterprise_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_enterprise_account_session()
    _configure_restore(monkeypatch)
    settings = _Settings()
    preferences = AccountPreferenceStore(settings)
    called = False

    def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Personal Local must not restore OIDC")

    monkeypatch.setattr(
        "desktop.enterprise_oidc.restore_enterprise_account_from_env",
        unexpected,
    )

    assert preferences.load() == AccountProfile.personal_local()
    assert called is False


def test_local_service_runs_account_preflight_before_backend_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "local-service"
    data_root = runtime_root / "data"
    data_root.mkdir(parents=True)
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(runtime_root))
    monkeypatch.setenv("MENTORFLOW_DB", str(data_root / "app.db"))
    monkeypatch.setenv("MENTORFLOW_ORM_DB", str(data_root / "mentorflow_v2.db"))
    monkeypatch.setenv("MENTORFLOW_DEPLOYMENT_MODE", "PERSONAL_LOCAL")
    called: list[str] = []

    def preflight() -> AccountProfile:
        called.append("before-backend-import")
        return AccountProfile.personal_local()

    monkeypatch.setattr("desktop.runtime.preflight_desktop_account_storage", preflight)
    service = LocalService()
    service.start()
    try:
        deadline = time.monotonic() + 20
        response: httpx.Response | None = None
        while time.monotonic() < deadline:
            try:
                candidate = httpx.get(f"{service.origin}/api/health", timeout=1)
            except httpx.HTTPError:
                time.sleep(0.1)
                continue
            if candidate.status_code == 200:
                response = candidate
                break
            time.sleep(0.1)
        assert response is not None
        assert LocalService._is_expected_health(response)
        assert called == ["before-backend-import"]
    finally:
        service.stop()
    assert service.handles_closed is True

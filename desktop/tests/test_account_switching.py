from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from backend.app.credential_store import CredentialStoreError  # noqa: E402

from desktop.account_identity import (  # noqa: E402
    AccountCredentialVault,
    AccountIdentity,
    AccountPreferenceStore,
    AccountProfile,
)
from desktop.account_settings_page import AccountSettingsPage  # noqa: E402
from desktop.account_vault import AccountVaultManager, VaultLayout  # noqa: E402
from desktop.runtime import (  # noqa: E402
    AccountSwitchCoordinator,
    clear_enterprise_account_session,
)


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


class _Handle:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _identity(subject: str, name: str) -> AccountIdentity:
    return AccountIdentity(
        issuer="https://login.example.test/tenant",
        subject=subject,
        display_name=name,
    )


@pytest.mark.parametrize("states, expected", [
    (("AVAILABLE", "RELINKED"), "全部 2 项资料已有效关联"),
    (("MISSING_ON_THIS_DEVICE",), "0 项资料已关联，1 项缺少本机文件"),
    (("AVAILABLE", "MISSING_ON_THIS_DEVICE"), "1 项资料已关联，1 项缺少本机文件"),
    (("UNRECOGNIZED",), "状态尚待核验"),
])
def test_device_indicator_uses_reconciled_binding_not_empty_vault(vault_root, states, expected):
    from desktop.main_window import MainWindow
    from PySide6.QtWidgets import QPushButton

    app = QApplication.instance() or QApplication([])
    identity = _identity("binding-indicator", "Binding account")
    manager = AccountVaultManager(VaultLayout(vault_root))
    manager.mount(identity.profile())
    page = AccountSettingsPage(
        profile=identity.profile(), vault_manager=manager,
        credential_vault=_CredentialRecorder(), switch_account=lambda _: None,
        logout_account=lambda: None,
    )
    window = SimpleNamespace(account_settings_page=page,
        device_recovery_button=QPushButton(), _open_recovery_center_callback=lambda: None)
    try:
        resources = tuple(SimpleNamespace(state=state) for state in states)
        MainWindow.set_device_recovery_snapshot(window, SimpleNamespace(resources=resources))
        assert manager.current.list_assets() == []
        assert expected in page.device_state_label.text()
        page.refresh()
        assert expected in page.device_state_label.text()
        assert window.device_recovery_button.isHidden() == ("MISSING_ON_THIS_DEVICE" not in states)
        # A refresh after correct relink must replace the missing projection.
        MainWindow.set_device_recovery_snapshot(window, SimpleNamespace(
            resources=(SimpleNamespace(state="RELINKED"),)))
        assert "全部 1 项资料已有效关联" in page.device_state_label.text()
        assert window.device_recovery_button.isHidden()
        manager.unmount()
        other = _identity("other-binding-account", "Other")
        manager.mount(other.profile())
        page.set_profile(other.profile())
        assert "有效关联" not in page.device_state_label.text()
    finally:
        page.close()
        manager.unmount()
        app.processEvents()


def test_device_indicator_tracks_real_file_binding_without_vault_asset(vault_root):
    import hashlib
    from desktop.account_metadata_sync import AccountMetadataSyncService
    from desktop.local_asset_lifecycle import LocalAssetLifecycle

    app = QApplication.instance() or QApplication([])
    identity = _identity("real-binding", "Synthetic binding account")
    manager = AccountVaultManager(VaultLayout(vault_root / "v"))
    vault = manager.mount(identity.profile())
    lifecycle = LocalAssetLifecycle(vault, account_identity={
        "issuer": identity.issuer, "subject": identity.subject})
    source = vault_root / "synthetic-resource.txt"
    source.write_bytes(b"synthetic resource")
    metadata = {
        "schema_version": "mentorflow.account-core-metadata.v1",
        "account": {"issuer": identity.issuer, "subject_id": identity.subject},
        "tenant": {"organization_id": "org", "workspace_id": "workspace"},
        "workspace": {"workspace_id": "workspace"}, "plans": [], "progress": [],
        "resource_manifest": {"assets": [{"asset_id": "resource-one",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]},
    }
    sync = AccountMetadataSyncService(SimpleNamespace(account_metadata=lambda: metadata),
        lifecycle, account_identity=identity)
    page = AccountSettingsPage(profile=identity.profile(), vault_manager=manager,
        credential_vault=_CredentialRecorder(), switch_account=lambda _: None,
        logout_account=lambda: None)
    try:
        page.set_resource_device_states(sync.sync().resources)
        assert "1 项缺少本机文件" in page.device_state_label.text()
        page.set_resource_device_states(sync.relink(resource_id="resource-one",
            expected_fingerprint_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            selected_file=source).snapshot.resources)
        assert vault.list_assets() == []
        assert "全部 1 项资料已有效关联" in page.device_state_label.text()
        page.set_resource_device_states(sync.refresh_device_state().resources)
        assert "全部 1 项资料已有效关联" in page.device_state_label.text()
        # Mutate only synthetic bytes, never delete real user material.
        source.write_bytes(b"wrong content")
        page.set_resource_device_states(sync.refresh_device_state().resources)
        assert "1 项缺少本机文件" in page.device_state_label.text()
    finally:
        page.close()
        manager.unmount()
        app.processEvents()


@pytest.fixture
def vault_root() -> Path:
    scratch = Path("D:/CodexScratch/r")
    scratch.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="", dir=scratch))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _coordinator(
    *,
    manager: AccountVaultManager,
    preferences: AccountPreferenceStore,
    state: dict[str, object],
) -> AccountSwitchCoordinator:
    def stop_tasks() -> None:
        state["tasks_stopped"] = True

    def close_handles() -> None:
        state["runtime_handles_closed"] = True

    def clear_state() -> None:
        state["ui_selection"] = None
        state["cache"] = {}
        state["memory"] = {}
        state["provider_state"] = None

    def quit_window() -> None:
        state["window_closed"] = True

    def wait_closed() -> bool:
        return bool(
            state.get("tasks_stopped")
            and state.get("runtime_handles_closed")
            and state.get("window_closed")
            and manager.current is None
        )

    def relaunch() -> bool:
        state["relaunched"] = True
        return True

    return AccountSwitchCoordinator(
        vault_manager=manager,
        preferences=preferences,
        stop_current_tasks=stop_tasks,
        close_current_handles=close_handles,
        clear_ui_and_runtime_cache=clear_state,
        quit_current_window=quit_window,
        wait_for_handles_closed=wait_closed,
        relaunch_desktop=relaunch,
    )


def test_a_to_b_to_a_closes_handles_clears_state_and_never_cross_enumerates(
    tmp_path: Path,
    vault_root: Path,
) -> None:
    layout = VaultLayout(vault_root)
    manager = AccountVaultManager(layout)
    identity_a = _identity("subject-A", "Account A")
    identity_b = _identity("subject-B", "Account B")
    vault_a = manager.mount(identity_a.profile())
    source_a = tmp_path / "A-private-material.txt"
    source_a.write_text("A-only", encoding="utf-8")
    asset_a = vault_a.import_managed(source_a)
    account_a_root = vault_a.root
    watcher = _Handle()
    vault_a.register_handle(watcher)
    settings = _Settings()
    preferences = AccountPreferenceStore(settings)
    state: dict[str, object] = {
        "ui_selection": "A-selection",
        "cache": {"A": "private"},
        "memory": {"A": "private"},
        "provider_state": {"A": "private-provider"},
    }

    receipt_ab = _coordinator(
        manager=manager,
        preferences=preferences,
        state=state,
    ).switch(identity_b, sync_cursor="cursor-b", model_selection="model-public")

    assert watcher.closed is True
    assert receipt_ab.handles_closed is True
    assert receipt_ab.target_mounted_in_old_process is False
    assert manager.current is None
    assert state["ui_selection"] is None
    assert state["cache"] == {}
    assert state["memory"] == {}
    assert state["provider_state"] is None
    assert state["relaunched"] is True
    assert receipt_ab.events.index("stop_current_account_local_tasks") < receipt_ab.events.index(
        "close_current_sqlite_file_watcher_and_http_handles"
    )
    assert receipt_ab.events.index(
        "close_current_sqlite_file_watcher_and_http_handles"
    ) < receipt_ab.events.index(
        "clear_current_account_ui_models_web_profiles_and_runtime_cache_handles"
    )
    assert receipt_ab.events.index("unmount_current_account_vault") < receipt_ab.events.index(
        "open_target_account_vault_only_on_next_process"
    )
    pointer = json.loads(receipt_ab.pointer_path.read_text(encoding="utf-8"))
    assert pointer["format"] == "mentorflow-next-data-root-v1"
    assert Path(pointer["data_root"]) == layout.root_for_profile(identity_b.profile())
    assert "subject-B" not in receipt_ab.pointer_path.read_text(encoding="utf-8")

    next_process_manager = AccountVaultManager(layout)
    vault_b = next_process_manager.mount(preferences.load())
    assert vault_b.profile == identity_b.profile()
    assert vault_b.list_assets() == []
    assert asset_a.display_name not in {asset.display_name for asset in vault_b.list_assets()}
    assert str(account_a_root) not in str(vault_b.root)

    state_b: dict[str, object] = {
        "ui_selection": "B-selection",
        "cache": {"B": "private"},
        "memory": {"B": "private"},
    }
    receipt_ba = _coordinator(
        manager=next_process_manager,
        preferences=preferences,
        state=state_b,
    ).switch(identity_a)
    assert receipt_ba.handles_closed is True
    restored_manager = AccountVaultManager(layout)
    restored_a = restored_manager.mount(preferences.load())
    assert restored_a.profile == identity_a.profile()
    assert [asset.display_name for asset in restored_a.list_assets()] == [
        "A-private-material.txt"
    ]


def test_personal_local_switch_is_a_distinct_next_launch_target(
    vault_root: Path,
) -> None:
    layout = VaultLayout(vault_root)
    manager = AccountVaultManager(layout)
    manager.mount(_identity("subject-A", "Account A").profile())
    settings = _Settings()
    preferences = AccountPreferenceStore(settings)
    state: dict[str, object] = {}

    receipt = _coordinator(
        manager=manager,
        preferences=preferences,
        state=state,
    ).switch_to_profile(
        AccountProfile.personal_local(),
        initial_event="logout_current_enterprise_account",
    )

    assert receipt.target_root == layout.personal_local_root
    assert preferences.load() == AccountProfile.personal_local()
    assert "accounts" not in receipt.target_root.parts


def test_nonsecret_preferences_and_server_payload_exclude_identity_and_paths(
    tmp_path: Path,
) -> None:
    settings = _Settings()
    preferences = AccountPreferenceStore(settings)
    identity = _identity("sensitive-full-subject", "Safe Account Name")
    preferences.save_target(
        identity.profile(),
        sync_cursor="opaque-cursor",
        model_selection="public-model-checksum",
    )

    serialized = json.dumps(settings.values, sort_keys=True)
    payload = preferences.server_preference_payload()

    assert "sensitive-full-subject" not in serialized
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized
    assert "credential" not in serialized
    assert str(tmp_path) not in serialized
    assert payload == {
        "profile_mode": "ENTERPRISE_ACCOUNT",
        "model_selection": "public-model-checksum",
    }


def test_oauth_tokens_stay_in_secure_boundary_and_logout_deletes_both_refs(
    vault_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity = _identity("secret-subject", "Safe Account")
    profile = identity.profile()
    stored: list[tuple[str, str]] = []
    deleted: list[str] = []

    def fake_store(
        secret: str,
        *,
        issuer_hash: str,
        subject_hash: str,
        purpose: str,
    ) -> str:
        assert issuer_hash == identity.issuer_hash
        assert subject_hash == identity.subject_hash
        stored.append((purpose, secret))
        return f"mfcred_account_{purpose}"

    def fake_delete(
        *,
        issuer_hash: str,
        subject_hash: str,
        purpose: str,
    ) -> bool:
        assert issuer_hash == identity.issuer_hash
        assert subject_hash == identity.subject_hash
        deleted.append(purpose)
        return True

    def fake_get(
        *,
        issuer_hash: str,
        subject_hash: str,
        purpose: str,
    ) -> str:
        assert issuer_hash == identity.issuer_hash
        assert subject_hash == identity.subject_hash
        raise CredentialStoreError("CREDENTIAL_NOT_FOUND", "not found")

    monkeypatch.setattr("desktop.account_identity.store_account_secret", fake_store)
    monkeypatch.setattr("desktop.account_identity.get_account_secret", fake_get)
    monkeypatch.setattr("desktop.account_identity.delete_account_secret", fake_delete)
    settings = _Settings()
    preferences = AccountPreferenceStore(settings)
    preferences.save_target(profile)
    manager = AccountVaultManager(VaultLayout(vault_root))
    manager.mount(profile)
    credentials = AccountCredentialVault()

    with caplog.at_level(logging.DEBUG):
        references = credentials.store_oauth_tokens(
            identity,
            access_token="access-token-plaintext",
            refresh_token="refresh-token-plaintext",
        )
        credentials.logout(profile)

    assert references == {
        "access_token_ref": "mfcred_account_oauth-access-token",
        "refresh_token_ref": "mfcred_account_oauth-refresh-token",
    }
    assert stored == [
        ("oauth-access-token", "access-token-plaintext"),
        ("oauth-refresh-token", "refresh-token-plaintext"),
    ]
    assert deleted == ["oauth-access-token", "oauth-refresh-token"]
    persisted = json.dumps(settings.values, sort_keys=True) + " ".join(
        path.read_bytes().decode("utf-8", errors="ignore")
        for path in vault_root.rglob("*")
        if path.is_file()
    )
    observed_logs = " ".join(record.getMessage() for record in caplog.records)
    for secret in ("access-token-plaintext", "refresh-token-plaintext"):
        assert secret not in persisted
        assert secret not in observed_logs
    manager.unmount()


class _CredentialRecorder(AccountCredentialVault):
    def __init__(self) -> None:
        self.calls: list[tuple[AccountIdentity, str, str]] = []

    def store_oauth_tokens(
        self,
        identity: AccountIdentity,
        *,
        access_token: str,
        refresh_token: str,
    ) -> dict[str, str]:
        self.calls.append((identity, access_token, refresh_token))
        return {
            "access_token_ref": "mfcred_account_access",
            "refresh_token_ref": "mfcred_account_refresh",
        }


def test_settings_page_shows_safe_profile_root_and_readable_availability(
    tmp_path: Path,
    vault_root: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    del application
    layout = VaultLayout(vault_root)
    manager = AccountVaultManager(layout)
    identity = _identity("sensitive-full-subject", "Safe Account Name")
    switched: list[AccountIdentity] = []
    credentials = _CredentialRecorder()
    page = AccountSettingsPage(
        profile=identity.profile(),
        vault_manager=manager,
        credential_vault=credentials,
        switch_account=lambda target: switched.append(target),
        logout_account=lambda: None,
    )

    assert page.profile_label.text() == "CURRENT_PROFILE = Enterprise Account"
    assert page.account_label.text() == "CURRENT_ACCOUNT = Safe Account Name"
    assert "sensitive-full-subject" not in page.account_label.text()
    assert page.data_root_label.text() == f"CURRENT_DATA_ROOT = {page.current_data_root}"

    linked = tmp_path / "linked-only.txt"
    linked.write_text("linked", encoding="utf-8")
    manager.mount(identity.profile()).import_linked(linked)
    linked.unlink()
    page.refresh()
    assert "LINKED_FILE_MISSING" in page.assets.item(0, 2).text()
    assert page.assets.item(0, 3).text() == "NON_PORTABLE / NOT_BACKED_UP"
    assert "Not Found" not in page.status.text()
    assert "{" not in page.status.text()

    page.complete_oidc_login(
        identity,
        access_token="access-token-secret",
        refresh_token="refresh-token-secret",
    )
    assert switched == [identity]
    assert credentials.calls == [
        (identity, "access-token-secret", "refresh-token-secret")
    ]
    visible_text = " ".join(
        (
            page.profile_label.text(),
            page.account_label.text(),
            page.data_root_label.text(),
            page.status.text(),
        )
    )
    assert "access-token-secret" not in visible_text
    assert "refresh-token-secret" not in visible_text
    page.close()


def test_account_presentation_switches_a_b_a_and_shows_new_device_state(
    tmp_path: Path,
    vault_root: Path,
) -> None:
    application = QApplication.instance() or QApplication([])
    clear_enterprise_account_session()
    layout = VaultLayout(vault_root)
    manager = AccountVaultManager(layout)
    identity_a = _identity("subject-A-ui", "Account A")
    identity_b = _identity("subject-B-ui", "Account B")
    identity_new = _identity("subject-new-device", "Account A on Device 2")
    logout_calls: list[str] = []

    source_a = tmp_path / "A-only.txt"
    source_a.write_text("A", encoding="utf-8")
    manager.mount(identity_a.profile()).import_managed(source_a)
    page = AccountSettingsPage(
        profile=identity_a.profile(),
        vault_manager=manager,
        credential_vault=_CredentialRecorder(),
        switch_account=lambda _target: None,
        logout_account=lambda: logout_calls.append(page.account_label.text()),
    )
    page.show()
    assert page.account_label.text() == "CURRENT_ACCOUNT = Account A"
    assert page.assets.item(0, 0).text() == "A-only.txt"

    manager.unmount()
    source_b = tmp_path / "B-only.txt"
    source_b.write_text("B", encoding="utf-8")
    manager.mount(identity_b.profile()).import_managed(source_b)
    page.set_profile(identity_b.profile())
    assert page.account_label.text() == "CURRENT_ACCOUNT = Account B"
    assert page.assets.rowCount() == 1
    assert page.assets.item(0, 0).text() == "B-only.txt"
    assert "A-only.txt" not in page.assets.item(0, 0).text()

    manager.unmount()
    manager.mount(identity_a.profile())
    page.set_profile(identity_a.profile())
    assert page.account_label.text() == "CURRENT_ACCOUNT = Account A"
    assert page.assets.rowCount() == 1
    assert page.assets.item(0, 0).text() == "A-only.txt"
    assert "B-only.txt" not in page.assets.item(0, 0).text()

    manager.unmount()
    manager.mount(identity_new.profile())
    page.set_profile(identity_new.profile())
    page.set_synced_resource_count(1)
    assert page.account_label.text() == "CURRENT_ACCOUNT = Account A on Device 2"
    assert page.assets.rowCount() == 0
    assert "NEW_DEVICE" not in page.device_state_label.text()
    assert "尚待核验" in page.device_state_label.text()

    page.logout_button.click()
    assert logout_calls == ["CURRENT_ACCOUNT = Account A on Device 2"]
    clear_enterprise_account_session()
    page.close()
    manager.unmount()
    application.processEvents()

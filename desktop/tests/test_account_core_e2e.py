"""Round-1 Desktop acceptance harness for the Account Core story.

All identities, credentials, and resources in this module are synthetic.  The
tests use two disposable device roots and never mount the user's real Vault.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from desktop.account_identity import (  # noqa: E402
    NONSECRET_ACCOUNT_PREFERENCE_KEYS,
    AccountIdentity,
    AccountPreferenceStore,
    AccountProfile,
)
from desktop.account_settings_page import AccountSettingsPage  # noqa: E402
from desktop.account_vault import AccountVaultManager, VaultLayout  # noqa: E402
from desktop.enterprise_oidc import EnterpriseOidcLoginResult  # noqa: E402
from desktop.local_asset_lifecycle import (  # noqa: E402
    LocalAssetLifecycle,
    LocalAssetLifecycleError,
)
from desktop.runtime import (  # noqa: E402
    clear_enterprise_account_session,
    enterprise_account_server_state,
)


ISSUER = "https://identity.account-core.test/realms/mentorflow"


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


class _CredentialRecorder:
    """Records only synthetic call shape; it never writes to Credential Locker."""

    def __init__(self) -> None:
        self.subjects: list[str] = []
        self.logout_profiles: list[AccountProfile] = []

    def store_oauth_tokens(
        self,
        identity: AccountIdentity,
        *,
        access_token: str,
        refresh_token: str,
    ) -> dict[str, str]:
        assert access_token.startswith("synthetic-")
        assert refresh_token.startswith("synthetic-")
        self.subjects.append(identity.subject)
        return {
            "access_token_ref": "qa-opaque-access-reference",
            "refresh_token_ref": "qa-opaque-refresh-reference",
        }

    def logout(self, profile: AccountProfile) -> None:
        self.logout_profiles.append(profile)


@dataclass(frozen=True, slots=True)
class _FallbackResourceBinding:
    resource_id: str
    local_path: Path | None
    fingerprint_sha256: str
    availability: str


class _Round1DeviceBindingAdapter:
    """Use the integrated binding API when present, otherwise a local fixture.

    The fallback keeps Round-1 runnable against the exact frozen base.  It is
    intentionally not evidence that the Round-2 product binding is integrated.
    """

    def __init__(self, lifecycle: LocalAssetLifecycle) -> None:
        self.lifecycle = lifecycle
        self._fallback: dict[str, _FallbackResourceBinding] = {}

    def register_missing_resource(
        self,
        resource_id: str,
        *,
        fingerprint_sha256: str,
    ) -> Any:
        integrated = getattr(self.lifecycle, "register_missing_resource", None)
        if callable(integrated):
            return integrated(
                resource_id,
                fingerprint_sha256=fingerprint_sha256,
            )
        binding = _FallbackResourceBinding(
            resource_id=resource_id,
            local_path=None,
            fingerprint_sha256=fingerprint_sha256,
            availability="MISSING_ON_THIS_DEVICE",
        )
        self._fallback[resource_id] = binding
        return binding

    def resolve_resource_binding(self, resource_id: str) -> Any:
        integrated = getattr(self.lifecycle, "resolve_resource_binding", None)
        if callable(integrated):
            return integrated(resource_id)
        try:
            return self._fallback[resource_id]
        except KeyError as exc:
            raise LocalAssetLifecycleError(
                "RESOURCE_BINDING_NOT_FOUND",
                "Synthetic device has no local resource binding",
            ) from exc

    def relink_resource(self, resource_id: str, source: Path | str) -> Any:
        integrated = getattr(self.lifecycle, "relink_resource", None)
        if callable(integrated):
            return integrated(resource_id, source)
        current = self._fallback[resource_id]
        source = Path(source)
        if not source.is_file():
            raise LocalAssetLifecycleError(
                "RESOURCE_SOURCE_UNAVAILABLE",
                "Synthetic resource source is unavailable",
            )
        observed = hashlib.sha256(source.read_bytes()).hexdigest()
        if observed != current.fingerprint_sha256:
            raise LocalAssetLifecycleError(
                "RESOURCE_FINGERPRINT_MISMATCH",
                "Synthetic resource fingerprint did not match",
            )
        updated = _FallbackResourceBinding(
            resource_id=current.resource_id,
            local_path=source.absolute(),
            fingerprint_sha256=current.fingerprint_sha256,
            availability="AVAILABLE_LOCAL",
        )
        self._fallback[resource_id] = updated
        return updated


class _MetadataApi:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = copy.deepcopy(payload)
        self.metadata_calls = 0
        self.progress_payloads: list[dict[str, Any]] = []

    def account_metadata(self) -> dict[str, Any]:
        self.metadata_calls += 1
        return copy.deepcopy(self.payload)

    def put_account_progress(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.progress_payloads.append(copy.deepcopy(payload))
        return copy.deepcopy(payload)


def _identity(suffix: str) -> AccountIdentity:
    return AccountIdentity(
        issuer=ISSUER,
        subject=f"qa-user-{suffix}",
        display_name=f"QA Account {suffix.upper()}",
        organization_id=f"qa-org-{suffix}",
        workspace_id=f"qa-workspace-{suffix}",
    )


def _server_state(identity: AccountIdentity) -> dict[str, object]:
    return {
        "schema_version": "mentorflow.enterprise-identity-session.v1",
        "authenticated": True,
        "account": {"display_name": identity.display_name},
        "context": {
            "contract_version": "enterprise-context.v1",
            "organization_id": identity.organization_id,
            "workspace_id": identity.workspace_id,
            "roles": ["learner"],
            "context_version": 1,
        },
    }


def _binding_state(binding: Any) -> str:
    value = getattr(binding, "availability", getattr(binding, "state", None))
    return str(getattr(value, "value", value))


def _identity_mapping(identity: AccountIdentity) -> dict[str, str]:
    return {"issuer": identity.issuer, "subject": identity.subject}


def _integrated_metadata_sync_types() -> tuple[type[Any], type[Exception]] | None:
    try:
        from desktop.account_metadata_sync import (
            AccountMetadataSyncError,
            AccountMetadataSyncService,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "desktop.account_metadata_sync":
            raise
        return None
    return AccountMetadataSyncService, AccountMetadataSyncError


@pytest.fixture
def isolated_device_roots() -> Iterator[tuple[Path, Path]]:
    base = Path("E:/Codex/Temp")
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ac8-", dir=base) as temporary:
        root = Path(temporary)
        yield root / "d1", root / "d2"


def test_in_app_login_completion_switches_account_on_qt_thread(qtbot, tmp_path: Path) -> None:
    clear_enterprise_account_session()
    identity = _identity("a")
    manager = AccountVaultManager(VaultLayout(tmp_path / "login-device"))
    personal = AccountProfile.personal_local()
    manager.mount(personal)
    credentials = _CredentialRecorder()
    switched: list[AccountIdentity] = []
    result = EnterpriseOidcLoginResult(
        identity=identity,
        access_token="synthetic-access-token",
        refresh_token="synthetic-refresh-token",
        server_state=_server_state(identity),
    )
    page = AccountSettingsPage(
        profile=personal,
        vault_manager=manager,
        credential_vault=credentials,  # type: ignore[arg-type]
        switch_account=lambda target: switched.append(target),
        logout_account=lambda: None,
        oidc_login=lambda: result,
    )
    qtbot.addWidget(page)
    page.show()
    try:
        qtbot.mouseClick(page.oauth_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: switched == [identity], timeout=3_000)

        assert credentials.subjects == [identity.subject]
        assert enterprise_account_server_state(identity.profile())["authenticated"] is True
        assert page.oauth_button.isEnabled()
        assert page.logout_button.isEnabled()
    finally:
        page.close()
        manager.unmount()
        clear_enterprise_account_session()


def test_two_device_account_story_isolated_relinked_and_restart_safe(
    isolated_device_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device1_root, device2_root = (
        path.absolute() for path in isolated_device_roots
    )
    assert device1_root != device2_root

    account_a = _identity("a")
    account_b = _identity("b")
    profile_a = account_a.profile()
    profile_b = account_b.profile()
    settings = _Settings()
    preferences = AccountPreferenceStore(settings)
    monkeypatch.setattr(
        "desktop.runtime.restore_enterprise_profile_for_startup",
        lambda profile: profile,
    )
    preferences.save_target(
        profile_a,
        sync_cursor="qa-sync-1",
        model_selection="qa-text-model",
    )
    assert preferences.load() == profile_a
    assert set(settings.values) == NONSECRET_ACCOUNT_PREFERENCE_KEYS
    assert not any("token" in key.casefold() for key in settings.values)

    manager1 = AccountVaultManager(VaultLayout(device1_root))
    manager2 = AccountVaultManager(VaultLayout(device2_root))
    restarted_manager1: AccountVaultManager | None = None
    source_a = device1_root / "qa-source-a.pdf"
    source_b = device1_root / "qa-source-b.txt"
    source_a.parent.mkdir(parents=True, exist_ok=True)
    source_a.write_bytes(b"synthetic account A revision")
    source_b.write_bytes(b"synthetic account B revision")

    try:
        vault_a1 = manager1.mount(profile_a)
        lifecycle_a1 = LocalAssetLifecycle(
            vault_a1,
            account_identity=_identity_mapping(account_a),
        )
        local_a = lifecycle_a1.import_managed(source_a)
        server_resource_id = "qa-server-resource-a"
        server_metadata = {
            "schema_version": "mentorflow.account-core-metadata.v1",
            "context_contract": "enterprise-context.v1",
            "account": {
                "issuer": account_a.issuer,
                "subject_id": account_a.subject,
            },
            "tenant": {
                "organization_id": account_a.organization_id,
                "workspace_id": account_a.workspace_id,
            },
            "workspace": {
                "workspace_id": account_a.workspace_id,
                "title": "QA Workspace A",
                "status": "ACTIVE",
            },
            "plans": [
                {
                    "plan_id": "qa-plan-a",
                    "resource_ids": [server_resource_id],
                }
            ],
            "progress": [
                {
                    "progress_id": "qa-progress-a",
                    "plan_id": "qa-plan-a",
                    "resource_id": server_resource_id,
                }
            ],
            "learning_metadata": {
                "resource_id": server_resource_id,
                "evidence_revision": "qa-evidence-a",
            },
            "resource_manifest": {
                "schema_version": "mentorflow.asset-manifest.read.v1",
                "assets": [
                    {
                        "schema_version": "mentorflow.asset-manifest.read.v1",
                        "asset_id": server_resource_id,
                        "asset_revision_id": "qa-server-revision-a",
                        "local_asset_id": local_a.local_asset_id,
                        "filename": local_a.filename,
                        "mime_type": local_a.mime_type,
                        "size_bytes": local_a.size_bytes,
                        "sha256": local_a.sha256,
                        "import_mode": "MANAGED",
                        "portability": "PORTABLE",
                        "availability": "AVAILABLE",
                    }
                ],
            },
        }
        manifest = lifecycle_a1.server_manifest_dto(local_a.local_asset_id)
        serialized_manifest = json.dumps(manifest, sort_keys=True)
        assert str(device1_root) not in serialized_manifest
        assert str(device2_root) not in serialized_manifest
        assert not any(isinstance(value, (bytes, bytearray)) for value in manifest.values())

        vault_b1 = manager1.mount(profile_b)
        assert vault_b1.list_assets() == []
        local_b = LocalAssetLifecycle(
            vault_b1,
            account_identity=_identity_mapping(account_b),
        ).import_managed(source_b)
        assert local_b.local_asset_id != local_a.local_asset_id
        assert {asset.local_asset_id for asset in vault_b1.list_assets()} == {
            local_b.local_asset_id
        }

        vault_a1_again = manager1.mount(profile_a)
        lifecycle_a1_again = LocalAssetLifecycle(
            vault_a1_again,
            account_identity=_identity_mapping(account_a),
        )
        restored_local_a = lifecycle_a1_again.resolve(local_a.local_asset_id)
        assert restored_local_a.sha256 == local_a.sha256
        assert {asset.local_asset_id for asset in vault_a1_again.list_assets()} == {
            local_a.local_asset_id
        }

        vault_a2 = manager2.mount(profile_a)
        lifecycle_a2 = LocalAssetLifecycle(
            vault_a2,
            account_identity=_identity_mapping(account_a),
        )
        assert vault_a2.list_assets() == []
        bindings = _Round1DeviceBindingAdapter(lifecycle_a2)
        wrong_revision = device2_root / "qa-wrong-revision.pdf"
        wrong_revision.write_bytes(b"synthetic wrong revision")
        correct_revision = device2_root / "qa-correct-revision.pdf"
        correct_revision.write_bytes(b"synthetic account A revision")
        sync_types = _integrated_metadata_sync_types()
        if sync_types is None:
            missing = bindings.register_missing_resource(
                server_resource_id,
                fingerprint_sha256=local_a.sha256,
            )
            assert missing.resource_id == server_resource_id
            assert missing.local_path is None
            assert _binding_state(missing) == "MISSING_ON_THIS_DEVICE"
            with pytest.raises(LocalAssetLifecycleError) as mismatch:
                bindings.relink_resource(server_resource_id, wrong_revision)
            assert mismatch.value.code in {
                "RESOURCE_FINGERPRINT_MISMATCH",
                "REVISION_MISMATCH",
            }
            unchanged = bindings.resolve_resource_binding(server_resource_id)
            assert unchanged.local_path is None
            assert _binding_state(unchanged) == "MISSING_ON_THIS_DEVICE"
            relinked = bindings.relink_resource(server_resource_id, correct_revision)
            relink_state = "RELINKED"
            assert relinked.resource_id == server_resource_id
            assert relinked.local_path == correct_revision.absolute()
            assert _binding_state(relinked) == "AVAILABLE_LOCAL"
        else:
            sync_service_type, sync_error_type = sync_types
            api = _MetadataApi(server_metadata)
            sync_service = sync_service_type(
                api,
                bindings,
                account_identity=account_a,
            )
            before_relink = sync_service.sync()
            missing_resource = before_relink.resource(server_resource_id)
            assert _binding_state(missing_resource) == "MISSING_ON_THIS_DEVICE"
            with pytest.raises(sync_error_type) as mismatch:
                sync_service.relink(
                    resource_id=server_resource_id,
                    expected_fingerprint_sha256=local_a.sha256,
                    selected_file=wrong_revision,
                )
            assert getattr(mismatch.value, "code", "") in {
                "RESOURCE_FINGERPRINT_MISMATCH",
                "REVISION_MISMATCH",
            }
            unchanged = bindings.resolve_resource_binding(server_resource_id)
            assert unchanged.local_path is None
            assert _binding_state(unchanged) == "MISSING_ON_THIS_DEVICE"
            result = sync_service.relink(
                resource_id=server_resource_id,
                expected_fingerprint_sha256=local_a.sha256,
                selected_file=correct_revision,
            )
            relinked = bindings.resolve_resource_binding(server_resource_id)
            relink_state = _binding_state(result.resource)
            assert result.resource.resource_id == server_resource_id
            assert result.snapshot.plans == before_relink.plans
            assert result.snapshot.progress == before_relink.progress
            assert result.snapshot.learning_metadata == before_relink.learning_metadata
            assert result.snapshot.server_metadata == before_relink.server_metadata
            assert api.metadata_calls == 1
            assert api.progress_payloads == []
            assert relinked.local_path == correct_revision.absolute()
            assert _binding_state(relinked) == "AVAILABLE_LOCAL"
        assert relink_state == "RELINKED"
        assert server_metadata["plans"][0]["resource_ids"] == [server_resource_id]
        assert server_metadata["progress"][0]["resource_id"] == server_resource_id
        assert server_metadata["learning_metadata"]["resource_id"] == (
            server_resource_id
        )
        serialized_server = json.dumps(server_metadata, sort_keys=True)
        assert str(device1_root) not in serialized_server
        assert str(device2_root) not in serialized_server

        manager1.unmount()
        restarted_manager1 = AccountVaultManager(VaultLayout(device1_root))
        restarted_vault_a = restarted_manager1.mount(preferences.load())
        restarted_lifecycle_a = LocalAssetLifecycle(
            restarted_vault_a,
            account_identity=_identity_mapping(account_a),
        )
        after_restart = restarted_lifecycle_a.resolve(local_a.local_asset_id)
        assert after_restart.sha256 == local_a.sha256
        assert restarted_vault_a.managed_object_path(
            restarted_vault_a.list_assets()[0]
        ).read_bytes() == b"synthetic account A revision"
    finally:
        manager1.unmount()
        manager2.unmount()
        if restarted_manager1 is not None:
            restarted_manager1.unmount()

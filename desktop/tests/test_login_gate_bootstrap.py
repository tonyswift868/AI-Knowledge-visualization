from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
from types import SimpleNamespace
from pathlib import Path
from scripts.test_storage_policy import configured_test_temp_root
import tempfile
from typing import Any, Mapping

import pytest
import httpx

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QCheckBox, QListWidget

from desktop.account_identity import AccountIdentity
from desktop.account_metadata_sync import (
    AccountMetadataSnapshot,
    ResourceDeviceState,
    SyncedResource,
)
from desktop.account_vault import AccountVaultManager, VaultLayout
from desktop.enterprise_oidc import DesktopOidcConfig, EnterpriseOidcLoginResult
from desktop.local_asset_lifecycle import LocalAssetLifecycle, LocalAssetLifecycleError
from desktop.main import MentorFlowApplication
from desktop.main_window import MainWindow
from desktop.startup_flow import (
    AccountBootstrapError,
    DeviceBootstrapState,
    DeviceMigrationCenter,
    EnterpriseAccountMetadataApi,
    LoginGateState,
    LoginGateWindow,
    RecoveryItemState,
    bootstrap_enterprise_account,
    classify_device,
)


ISSUER = "https://identity.example.test/realms/mentorflow"
RESOURCE_BYTES = b"e107 canonical resource"
RESOURCE_SHA = hashlib.sha256(RESOURCE_BYTES).hexdigest()


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_startup_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Startup binds many account paths directly. Never let those point later
    # test modules at this test's already-deleted disposable Vault.
    monkeypatch.setattr(os, "environ", dict(os.environ))


@pytest.fixture
def short_root() -> Path:
    scratch = configured_test_temp_root()
    scratch.mkdir(parents=True, exist_ok=True)
    root = None
    for name in "0123456789abcdefghijklmnopqrstuvwxyz":
        candidate = scratch / name
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        root = candidate
        break
    if root is None:
        raise RuntimeError("No short E: test directory is available")
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _identity(subject: str) -> AccountIdentity:
    return AccountIdentity(
        issuer=ISSUER,
        subject=subject,
        display_name=f"Account {subject[-1].upper()}",
        organization_id="org-1",
        workspace_id="workspace-1",
    )


def _metadata(subject: str, *, assets: int = 1) -> dict[str, Any]:
    rows = []
    for index in range(assets):
        suffix = index + 1
        rows.append(
            {
                "schema_version": "mentorflow.asset-manifest.read.v1",
                "asset_id": f"asset-{suffix}",
                "asset_revision_id": f"revision-{suffix}",
                "filename": f"lesson-{suffix}.pdf",
                "mime_type": "application/pdf",
                "size_bytes": len(RESOURCE_BYTES),
                "sha256": RESOURCE_SHA,
                "source_kind": "LOCAL_LINKED",
                "availability": "AVAILABLE",
            }
        )
    return {
        "schema_version": "mentorflow.account-core-metadata.v1",
        "context_contract": "enterprise-context.v1",
        "account": {"issuer": ISSUER, "subject_id": subject},
        "tenant": {"organization_id": "org-1", "workspace_id": "workspace-1"},
        "workspace": {
            "workspace_id": "workspace-1",
            "title": "Algebra",
            "status": "ACTIVE",
        },
        "plans": [{"plan_id": "plan-1", "title": "Algebra plan", "version": 4}],
        "progress": [{"snapshot_id": "progress-1", "plan_id": "plan-1", "version": 8}],
        "resource_manifest": {
            "schema_version": "mentorflow.asset-manifest.read.v1",
            "assets": rows,
        },
    }


class _MetadataApi:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = copy.deepcopy(dict(payload))
        self.calls = 0
        self.closed = False

    def account_metadata(self) -> Mapping[str, Any]:
        self.calls += 1
        return copy.deepcopy(self.payload)

    def put_account_progress(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        return copy.deepcopy(payload)

    def close(self) -> None:
        self.closed = True


def _login(identity: AccountIdentity) -> EnterpriseOidcLoginResult:
    return EnterpriseOidcLoginResult(
        identity=identity,
        access_token="access-token",
        refresh_token="refresh-token",
        server_state={
            "authenticated": True,
            "account": {
                "subject_id": identity.subject,
                "display_name": identity.display_name,
                "preferred_username": identity.subject,
            },
            "context": {
                "organization_id": identity.organization_id,
                "workspace_id": identity.workspace_id,
                "context_version": 1,
            },
        },
    )


def _config() -> DesktopOidcConfig:
    return DesktopOidcConfig(
        issuer=ISSUER,
        client_id="mentorflow-desktop",
        audience="mentorflow-api",
        api_base_url="https://api.example.test",
    )


def test_device_classification_uses_real_local_resource_state() -> None:
    base = _metadata("account-a")
    missing = SyncedResource(
        resource_id="asset-1",
        expected_fingerprint_sha256=RESOURCE_SHA,
        server_metadata=base["resource_manifest"]["assets"][0],
        state=ResourceDeviceState.MISSING_ON_THIS_DEVICE,
    )
    available = SyncedResource(
        resource_id="asset-2",
        expected_fingerprint_sha256=RESOURCE_SHA,
        server_metadata={**base["resource_manifest"]["assets"][0], "asset_id": "asset-2"},
        state=ResourceDeviceState.AVAILABLE,
    )

    assert classify_device(AccountMetadataSnapshot(base, ())) is DeviceBootstrapState.RETURNING_DEVICE_READY
    assert classify_device(AccountMetadataSnapshot(base, (missing,))) is DeviceBootstrapState.NEW_DEVICE_OR_EMPTY_LOCAL_STATE
    assert classify_device(AccountMetadataSnapshot(base, (available, missing))) is DeviceBootstrapState.RETURNING_DEVICE_PARTIAL
    assert classify_device(AccountMetadataSnapshot(base, (available,))) is DeviceBootstrapState.RETURNING_DEVICE_READY


def test_bootstrap_fetches_metadata_once_and_detects_empty_device(
    short_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault_root = short_root / "vaults"
    identity = _identity("account-a")
    api = _MetadataApi(_metadata(identity.subject))
    monkeypatch.setenv("MENTORFLOW_ACCOUNT_VAULT_ROOT", str(vault_root))

    result = bootstrap_enterprise_account(
        _login(identity),
        config=_config(),
        vault_root=vault_root,
        metadata_api=api,  # type: ignore[arg-type]
    )
    try:
        assert api.calls == 1
        assert result.device_state is DeviceBootstrapState.NEW_DEVICE_OR_EMPTY_LOCAL_STATE
        assert result.missing_count == 1
        assert result.snapshot.plans[0]["plan_id"] == "plan-1"
        assert result.snapshot.progress[0]["version"] == 8
        # Worker-side bootstrap must not redirect process-global paths before
        # the UI accepts its account/generation. Root binds only after that gate.
        assert os.environ.get("MENTORFLOW_USER_DATA_ROOT") != str(result.vault_manager.current.root)
    finally:
        result.close()


def test_account_scoped_fast_path_and_a_b_isolation(
    short_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault_root = short_root / "vaults"
    selected = short_root / "lesson.pdf"
    selected.write_bytes(RESOURCE_BYTES)
    identity_a = _identity("account-a")
    profile_a = identity_a.profile()
    manager = AccountVaultManager(VaultLayout(vault_root))
    lifecycle = LocalAssetLifecycle(
        manager.mount(profile_a),
        account_identity={"issuer": identity_a.issuer, "subject": identity_a.subject},
    )
    local_asset = lifecycle.import_linked(selected)
    manager.unmount()
    monkeypatch.setenv("MENTORFLOW_ACCOUNT_VAULT_ROOT", str(vault_root))

    result_a = bootstrap_enterprise_account(
        _login(identity_a),
        config=_config(),
        vault_root=vault_root,
        metadata_api=_MetadataApi(
            {
                **_metadata(identity_a.subject),
                "resource_manifest": {
                    "schema_version": "mentorflow.asset-manifest.read.v1",
                    "assets": [
                        {
                            **_metadata(identity_a.subject)["resource_manifest"]["assets"][0],
                            "local_asset_id": local_asset.local_asset_id,
                        }
                    ],
                },
            }
        ),  # type: ignore[arg-type]
    )
    identity_b = _identity("account-b")
    result_b = bootstrap_enterprise_account(
        _login(identity_b),
        config=_config(),
        vault_root=vault_root,
        metadata_api=_MetadataApi(_metadata(identity_b.subject)),  # type: ignore[arg-type]
    )
    try:
        assert result_a.device_state is DeviceBootstrapState.RETURNING_DEVICE_READY
        assert result_a.available_count == 1
        assert result_b.device_state is DeviceBootstrapState.NEW_DEVICE_OR_EMPTY_LOCAL_STATE
        assert result_b.missing_count == 1
        assert result_a.profile.subject_hash != result_b.profile.subject_hash
        assert result_a.vault_manager.current.root != result_b.vault_manager.current.root
    finally:
        result_a.close()
        result_b.close()


def test_empty_account_enters_main_without_recovery_loop(
    short_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity("empty-account")
    payload = _metadata(identity.subject, assets=0)
    payload["plans"] = []
    payload["progress"] = []
    monkeypatch.setenv("MENTORFLOW_ACCOUNT_VAULT_ROOT", str(short_root))
    result = bootstrap_enterprise_account(
        _login(identity), config=_config(), vault_root=short_root,
        metadata_api=_MetadataApi(payload),
    )
    try:
        assert result.device_state is DeviceBootstrapState.RETURNING_DEVICE_READY
        assert result.resource_count == result.missing_count == 0
        assert not result.snapshot.plans and not result.snapshot.progress
    finally:
        result.close()


def test_verified_remote_reacquire_reuses_importer_and_preserves_resource_id(
    short_root: Path,
) -> None:
    identity = _identity("account-a")
    manager = AccountVaultManager(VaultLayout(short_root / "vaults"))

    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(RESOURCE_BYTES)

    lifecycle = LocalAssetLifecycle(
        manager.mount(identity.profile()),
        account_identity={"issuer": identity.issuer, "subject": identity.subject},
        downloader=download,
    )
    lifecycle.register_missing_resource("asset-1", fingerprint_sha256=RESOURCE_SHA)
    try:
        binding = lifecycle.reacquire_resource(
            "asset-1",
            fingerprint_sha256=RESOURCE_SHA,
            verified_source_url="https://resources.example.test/lesson.pdf",
        )
        assert binding.resource_id == "asset-1"
        assert binding.fingerprint_sha256 == RESOURCE_SHA
        assert binding.local_path is not None and binding.local_path.is_file()
        assert len(lifecycle.list_assets()) == 1
    finally:
        manager.unmount()


def test_wrong_remote_reacquire_hash_keeps_missing_binding(short_root: Path) -> None:
    identity = _identity("account-a")
    manager = AccountVaultManager(VaultLayout(short_root / "vaults"))

    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(b"wrong resource")

    lifecycle = LocalAssetLifecycle(
        manager.mount(identity.profile()),
        account_identity={"issuer": identity.issuer, "subject": identity.subject},
        downloader=download,
    )
    before = lifecycle.register_missing_resource(
        "asset-1",
        fingerprint_sha256=RESOURCE_SHA,
    )
    try:
        with pytest.raises(LocalAssetLifecycleError) as raised:
            lifecycle.reacquire_resource(
                "asset-1",
                fingerprint_sha256=RESOURCE_SHA,
                verified_source_url="https://resources.example.test/lesson.pdf",
            )
        assert getattr(raised.value, "code", "") == "RESOURCE_FINGERPRINT_MISMATCH"
        after = lifecycle.resolve_resource_binding("asset-1")
        assert after.local_path == before.local_path is None
        assert after.fingerprint_sha256 == before.fingerprint_sha256
        assert lifecycle.list_assets() == []
    finally:
        manager.unmount()


def test_remote_metadata_adapter_is_tenant_bound_and_cached() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_metadata("account-a"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = EnterpriseAccountMetadataApi(
        _config(),
        _identity("account-a"),
        "access-token",
        client=client,
    )
    try:
        assert adapter.account_metadata()["account"]["subject_id"] == "account-a"
        assert adapter.account_metadata()["account"]["subject_id"] == "account-a"
        assert len(requests) == 1
        request = requests[0]
        assert request.url.path == "/api/enterprise/account/metadata"
        assert request.headers["authorization"] == "Bearer access-token"
        assert request.headers["x-mentorflow-organization-id"] == "org-1"
        assert request.headers["x-mentorflow-workspace-id"] == "workspace-1"
    finally:
        client.close()


def test_login_gate_exposes_all_truthful_states(qapp: QApplication) -> None:
    gate = LoginGateWindow()
    try:
        assert gate.objectName() == "mentorflowLoginGateWindow"
        assert 440 <= gate.width() <= 520
        assert 680 <= gate.height() <= 800
        assert gate.state is LoginGateState.LOGIN_READY
        assert gate.primary_button.isEnabled()

        gate.set_state(LoginGateState.AUTHENTICATING)
        assert not gate.primary_button.isEnabled()
        gate.set_state(LoginGateState.AUTH_SUCCESS_BOOTSTRAPPING)
        assert "正在加载" in gate.state_title.text()
        gate.set_state(LoginGateState.AUTH_FAILED, detail="网络不可用")
        assert gate.primary_button.isEnabled()
        assert gate.state_detail.text() == "网络不可用"
    finally:
        gate.close()


@pytest.mark.parametrize("status", [401, 403, 404, 503])
def test_bootstrap_http_failure_is_truthful_and_fails_closed(status: int) -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": "internal detail"})
    )) as client:
        adapter = EnterpriseAccountMetadataApi(
            _config(), _identity("account-a"), "access-token", client=client,
        )
        with pytest.raises(AccountBootstrapError) as raised:
            adapter.account_metadata()
        assert adapter._cached_metadata is None
        assert "internal detail" not in raised.value.user_message
        if status == 404:
            assert raised.value.code == "ACCOUNT_WORKSPACE_UNAVAILABLE"
            assert "网络" not in raised.value.user_message


def test_main_plan_list_uses_bootstrapped_account_metadata(qapp: QApplication) -> None:
    payload = _metadata("account-a", assets=0)
    checkbox = QCheckBox()
    target = QListWidget()
    window = SimpleNamespace(
        bootstrap_snapshot=AccountMetadataSnapshot(payload, ()),
        include_archived_plans=checkbox,
    )
    try:
        MainWindow._show_account_plan_metadata(window, target)
        assert target.count() == 1
        assert target.item(0).text() == "Algebra plan\n已同步 · 进度记录 1 项"
        assert window.bootstrap_snapshot.server_metadata == payload
    finally:
        target.close()
        checkbox.close()


def test_packaged_sidecar_fences_inherited_personal_database_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import desktop_entry

    package = tmp_path / "MentorFlow"
    package.mkdir()
    certificate = tmp_path / "Config" / "edge-tls-ca.crt"
    certificate.parent.mkdir()
    certificate.write_text("synthetic certificate fixture", encoding="utf-8")
    config = {
        "MENTORFLOW_ACCOUNT_VAULT_ROOT": str(tmp_path / "uat"),
        "MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY": "1",
        "MENTORFLOW_DATA_DIR": str(tmp_path / "uat"),
        "MENTORFLOW_DESKTOP_OIDC_ISSUER": ISSUER,
        "MENTORFLOW_DESKTOP_OIDC_CLIENT_ID": "mentorflow-desktop",
        "MENTORFLOW_DESKTOP_OIDC_AUDIENCE": "mentorflow-api",
        "MENTORFLOW_ENTERPRISE_API_BASE_URL": "https://api.example.test",
    }
    (package / "account-core-launch.json").write_text(
        json.dumps({"environment": config}), encoding="utf-8",
    )
    # Restore the entire process environment after exercising startup binding.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ.pop("MENTORFLOW_ACCOUNT_VAULT_ROOT", None)
    os.environ.pop("MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY", None)
    os.environ["MENTORFLOW_DB"] = str(tmp_path / "protected.db")
    os.environ["MENTORFLOW_ORM_DB"] = str(tmp_path / "protected-orm.db")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(package / "MentorFlow.exe"))
    desktop_entry.apply_account_core_package_config()
    assert Path(os.environ["MENTORFLOW_DB"]) == tmp_path / "uat/prelaunch/data/app.db"
    assert Path(os.environ["MENTORFLOW_ORM_DB"]) == tmp_path / "uat/prelaunch/data/mentorflow_v2.db"
    assert os.environ["MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE"] == "1"
    assert not (tmp_path / "protected.db").exists()


def test_enterprise_startup_has_visible_gate_and_no_main_window(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_DESKTOP_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("MENTORFLOW_DESKTOP_OIDC_CLIENT_ID", "mentorflow-desktop")
    monkeypatch.setenv("MENTORFLOW_DESKTOP_OIDC_AUDIENCE", "mentorflow-api")
    monkeypatch.setenv(
        "MENTORFLOW_ENTERPRISE_API_BASE_URL",
        "https://api.example.test",
    )
    monkeypatch.setenv("MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE", "1")
    monkeypatch.delenv("MENTORFLOW_DESKTOP_SMOKE_SECONDS", raising=False)

    application = MentorFlowApplication()
    try:
        assert application._enterprise_login_required is True
        assert application.main is None
        assert application.service is None
        application._show_login_gate()
        qapp.processEvents()
        assert application.login_gate.isVisible()
        assert application.login_gate.objectName() == "mentorflowLoginGateWindow"
        assert application.main is None
    finally:
        if application.login_gate is not None:
            application.login_gate.close()
        application._shutdown()


def test_recovery_center_uses_human_labels_and_preserves_missing_state(
    qapp: QApplication,
    short_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault_root = short_root / "vaults"
    identity = _identity("account-a")
    monkeypatch.setenv("MENTORFLOW_ACCOUNT_VAULT_ROOT", str(vault_root))
    result = bootstrap_enterprise_account(
        _login(identity),
        config=_config(),
        vault_root=vault_root,
        metadata_api=_MetadataApi(_metadata(identity.subject)),  # type: ignore[arg-type]
    )
    center = DeviceMigrationCenter(result, before_main=True)
    try:
        assert center.objectName() == "mentorflowDeviceMigrationCenter"
        assert center.table.rowCount() == 1
        assert center.table.item(0, 1).text() == "此设备缺少"
        assert "MISSING_ON_THIS_DEVICE" not in center.table.item(0, 1).text()
        center.table.selectRow(0)
        center._defer_selected()
        assert center._states["asset-1"] is RecoveryItemState.SKIPPED
        assert result.snapshot.resource("asset-1").state is ResourceDeviceState.MISSING_ON_THIS_DEVICE
    finally:
        center.close()
        result.close()

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from scripts.test_storage_policy import configured_test_temp_root

import pytest

import desktop.local_asset_lifecycle as lifecycle_module
from backend.app.enterprise_tenancy.asset_manifests import AssetManifestRegistrationIn
from desktop.account_identity import AccountIdentity, AccountProfile
from desktop.account_portability import PackageMode, export_account_package
from desktop.account_vault import AccountVaultManager, AssetAvailability, VaultLayout
from desktop.local_asset_lifecycle import LocalAssetLifecycle, LocalAssetLifecycleError


ACCOUNT = {
    "issuer": "https://identity.example.test/realms/mentorflow",
    "subject": "enterprise-lifecycle-user",
}
_OPEN_MANAGERS: list[AccountVaultManager] = []


def _profile(subject: str = "enterprise-lifecycle-user"):
    return AccountIdentity(
        issuer=ACCOUNT["issuer"],
        subject=subject,
        display_name=subject,
    ).profile()


@pytest.fixture
def scratch_root() -> Path:
    base = configured_test_temp_root()
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="", dir=base))
    try:
        yield root
    finally:
        for manager in reversed(_OPEN_MANAGERS):
            manager.unmount()
        _OPEN_MANAGERS.clear()
        package_temp = root / "package-temp"
        if package_temp.exists():
            shutil.rmtree(package_temp, ignore_errors=True)
        cleanup_target = str(root.resolve())
        if os.name == "nt" and not cleanup_target.startswith("\\\\?\\"):
            cleanup_target = "\\\\?\\" + cleanup_target
        shutil.rmtree(cleanup_target, ignore_errors=True)


def _service(root: Path, *, downloader=None):
    manager = AccountVaultManager(VaultLayout(root / "v"))
    _OPEN_MANAGERS.append(manager)
    vault = manager.mount(_profile())
    return manager, vault, LocalAssetLifecycle(
        vault,
        account_identity=ACCOUNT,
        downloader=downloader,
    )


def test_managed_import_survives_source_delete_and_dto_has_no_device_path_or_bytes(
    scratch_root: Path,
) -> None:
    _manager, vault, lifecycle = _service(scratch_root)
    source = scratch_root / "downloads" / "lesson.txt"
    source.parent.mkdir()
    source.write_bytes(b"managed revision")

    view = lifecycle.import_managed(source)
    source.unlink()

    assert view.source_kind == "MANAGED"
    assert lifecycle.resolve(view.local_asset_id).availability is AssetAvailability.AVAILABLE_LOCAL
    assert vault.managed_object_path(vault.list_assets()[0]).read_bytes() == b"managed revision"
    binding = lifecycle.bind_resource_to_asset("resource-managed-1", view.local_asset_id)
    assert binding.resource_id == "resource-managed-1"
    assert binding.local_path == vault.managed_object_path(vault.list_assets()[0])
    assert binding.fingerprint_sha256 == view.sha256
    assert binding.availability is AssetAvailability.AVAILABLE_LOCAL
    dto = lifecycle.server_manifest_dto(view.local_asset_id)
    assert dto == {
        "local_asset_id": view.local_asset_id,
        "filename": "lesson.txt",
        "mime_type": "text/plain",
        "size_bytes": len(b"managed revision"),
        "sha256": hashlib.sha256(b"managed revision").hexdigest(),
        "import_mode": "MANAGED",
        "portability": "PORTABLE",
    }
    assert not any(isinstance(value, (bytes, bytearray, memoryview)) for value in dto.values())
    assert not any(
        isinstance(value, str) and len(value) > 2 and value[1:3] in {":\\", ":/"}
        for value in dto.values()
    )
    manifest = AssetManifestRegistrationIn.model_validate(dto)
    assert manifest.availability == "AVAILABLE"
    assert "availability" not in manifest.model_fields_set


def test_linked_import_does_not_copy_and_relink_changes_only_device_overlay(
    scratch_root: Path,
) -> None:
    _manager, vault, lifecycle = _service(scratch_root)
    original = scratch_root / "external" / "linked.pdf"
    original.parent.mkdir()
    original.write_bytes(b"linked revision")

    linked = lifecycle.import_linked(original)
    base_record = vault.list_assets()[0]
    assert linked.labels == ("LINKED", "NOT_PORTABLE")
    assert base_record.object_relative_path is None
    assert not any((vault.root / "managed-assets" / "objects").rglob("*"))

    original.unlink()
    missing = lifecycle.resolve(linked.local_asset_id)
    assert missing.availability is AssetAvailability.LINKED_FILE_MISSING
    assert missing.actions == ("relink",)

    replacement = scratch_root / "replacement" / "same.pdf"
    replacement.parent.mkdir()
    replacement.write_bytes(b"linked revision")
    lifecycle.relink(linked.local_asset_id, replacement)

    assert (
        lifecycle.resolve(linked.local_asset_id).availability
        is AssetAvailability.AVAILABLE_LOCAL
    )
    assert vault.list_assets()[0].linked_path == base_record.linked_path
    dto = lifecycle.server_manifest_dto(linked.local_asset_id)
    assert dto["portability"] == "NONPORTABLE"
    assert dto["device_id"].startswith("device_")
    assert str(original) not in str(dto)
    assert str(replacement) not in str(dto)


def test_all_five_states_and_fail_closed_redownload(
    scratch_root: Path,
) -> None:
    payloads = {
        "available.txt": b"available",
        "missing.txt": b"missing",
        "redownload.txt": b"redownload",
        "package.txt": b"package",
    }

    def downloader(url: str, destination: Path) -> None:
        assert url == "https://downloads.example.test/redownload.txt"
        destination.write_bytes(payloads["redownload.txt"])

    _manager, vault, lifecycle = _service(scratch_root, downloader=downloader)
    views = {}
    for filename, content in payloads.items():
        source = scratch_root / filename
        source.write_bytes(content)
        views[filename] = lifecycle.import_managed(source)
    linked_source = scratch_root / "linked.txt"
    linked_source.write_bytes(b"linked")
    linked = lifecycle.import_linked(linked_source)

    records = {record.local_asset_id: record for record in vault.list_assets()}
    vault.managed_object_path(records[views["missing.txt"].local_asset_id]).unlink()
    vault.managed_object_path(records[views["redownload.txt"].local_asset_id]).unlink()
    vault.managed_object_path(records[views["package.txt"].local_asset_id]).unlink()
    linked_source.unlink()
    lifecycle.register_recovery_options(
        views["redownload.txt"].local_asset_id,
        verified_source_url="https://downloads.example.test/redownload.txt",
    )
    lifecycle.register_recovery_options(
        views["package.txt"].local_asset_id,
        restorable_from_package=True,
    )

    observed = {item.filename: item.availability.value for item in lifecycle.list_assets()}
    assert observed == {
        "available.txt": "AVAILABLE_LOCAL",
        "missing.txt": "MISSING_ON_DEVICE",
        "redownload.txt": "REDOWNLOAD_AVAILABLE",
        "package.txt": "RESTORABLE_FROM_PACKAGE",
        "linked.txt": "LINKED_FILE_MISSING",
    }
    restored = lifecycle.redownload(views["redownload.txt"].local_asset_id)
    assert restored.availability is AssetAvailability.AVAILABLE_LOCAL

    with pytest.raises(LocalAssetLifecycleError, match="SOURCE_NOT_VERIFIED"):
        lifecycle.redownload(views["missing.txt"].local_asset_id)


def test_restore_from_verified_current_account_package_preserves_sha(
    scratch_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(scratch_root / "package-temp"))
    _manager, vault, lifecycle = _service(scratch_root)
    source = scratch_root / "restore-me.pdf"
    source.write_bytes(b"package revision bytes")
    imported = lifecycle.import_managed(source)
    record = vault.list_assets()[0]
    package = scratch_root / "current-account.mentorflowpack"
    # This focused package contains only the raw asset under test. Avoid
    # copying unrelated empty runtime databases into disposable staging.
    for database_name in ("app.db", "mentorflow_v2.db"):
        (vault.root / "data" / database_name).unlink(missing_ok=True)
    export_account_package(
        vault.root,
        package,
        account_identity=ACCOUNT,
        mode=PackageMode.FULL,
        asset_manifests=[
            {
                "asset_id": "asset-restore-1",
                "asset_revision_id": "revision-restore-1",
                "local_asset_id": record.local_asset_id,
                "storage_kind": "MANAGED",
                "filename": record.display_name,
                "mime_type": record.mime_type,
                "size_bytes": record.size_bytes,
                "sha256": record.sha256,
                "vault_relative_path": record.object_relative_path,
            }
        ],
    )
    target = vault.managed_object_path(record)
    target.unlink()
    lifecycle.register_recovery_options(imported.local_asset_id, restorable_from_package=True)

    restored = lifecycle.restore_from_package(imported.local_asset_id, package)

    assert restored.availability is AssetAvailability.AVAILABLE_LOCAL
    assert restored.sha256 == imported.sha256
    assert target.read_bytes() == b"package revision bytes"


def test_account_b_cannot_enumerate_account_a_local_metadata(scratch_root: Path) -> None:
    manager, vault_a, lifecycle_a = _service(scratch_root)
    private = scratch_root / "account-a-private.txt"
    private.write_bytes(b"account A")
    lifecycle_a.import_managed(private)
    root_a = vault_a.root

    account_b = {
        "issuer": ACCOUNT["issuer"],
        "subject": "enterprise-lifecycle-user-b",
    }
    vault_b = manager.mount(_profile("enterprise-lifecycle-user-b"))
    lifecycle_b = LocalAssetLifecycle(vault_b, account_identity=account_b)

    assert lifecycle_b.list_assets() == []
    assert vault_b.root != root_a
    assert not lifecycle_b._overlay_path.exists()
    assert manager.layout.shared_models_root not in (vault_a.root, vault_b.root)


def test_empty_device_restores_package_resource_by_server_id(scratch_root: Path) -> None:
    manager_a, vault_a, lifecycle_a = _service(scratch_root / 'a')
    source = scratch_root / 'original.txt'
    source.write_bytes(b'User original document, not redownloadable.')
    asset = lifecycle_a.import_managed(source)
    record = vault_a.list_assets()[0]
    package = scratch_root / 'originals.mentorflowpack'
    for name in ('app.db', 'mentorflow_v2.db'):
        (vault_a.root / 'data' / name).unlink(missing_ok=True)
    export_account_package(vault_a.root, package, account_identity=ACCOUNT, mode=PackageMode.FULL,
        asset_manifests=[dict(asset_id='server-resource-1', asset_revision_id='revision-1',
            local_asset_id=record.local_asset_id, storage_kind='MANAGED', filename=record.display_name,
            mime_type=record.mime_type, size_bytes=record.size_bytes, sha256=record.sha256,
            vault_relative_path=record.object_relative_path)])
    manager_a.unmount()
    source.unlink()
    manager_b, vault_b, lifecycle_b = _service(scratch_root / 'b')
    assert vault_b.list_assets() == []
    from desktop.product_asset_metadata import bind_vault_metadata
    class NoCloudWrite:
        def request(self, *args, **kwargs):
            raise AssertionError('Recovery must not create another cloud resource')
    bind_vault_metadata(vault_b, NoCloudWrite())
    result = lifecycle_b.restore_resource_from_package('server-resource-1', package, fingerprint_sha256=asset.sha256)
    assert result.resource_id == 'server-resource-1'
    assert result.local_path.read_bytes() == b'User original document, not redownloadable.'
    assert result.fingerprint_sha256 == asset.sha256
    repeated = lifecycle_b.restore_resource_from_package('server-resource-1', package, fingerprint_sha256=asset.sha256)
    assert (repeated.resource_id, repeated.local_path, repeated.fingerprint_sha256) == (
        result.resource_id, result.local_path, result.fingerprint_sha256)
    assert len(vault_b.list_assets()) == 1
    # A package cannot override the authenticated server revision.
    with pytest.raises(LocalAssetLifecycleError, match='PACKAGE_REVISION_MISMATCH'):
        lifecycle_b.restore_resource_from_package('server-resource-1', package, fingerprint_sha256='0' * 64)
    vault_c = manager_b.mount(_profile('other-user'))
    lifecycle_c = LocalAssetLifecycle(vault_c, account_identity=dict(ACCOUNT, subject='other-user'))
    with pytest.raises(LocalAssetLifecycleError, match='PACKAGE_ACCOUNT_MISMATCH'):
        lifecycle_c.restore_resource_from_package('server-resource-1', package, fingerprint_sha256=asset.sha256)
    assert vault_c.list_assets() == []


def test_reacquire_known_model_stays_local_and_preserves_server_resource(scratch_root: Path) -> None:
    content = b'synthetic model bytes'
    def download(url, target):
        assert url == 'https://models.example.test/model.bin'
        target.write_bytes(content)
    _, vault, lifecycle = _service(scratch_root, downloader=download)
    from desktop.product_asset_metadata import bind_vault_metadata
    class NoCloudWrite:
        def request(self, *args, **kwargs):
            raise AssertionError('Model recovery must not publish USER_FILE metadata')
    bind_vault_metadata(vault, NoCloudWrite())
    binding = lifecycle.reacquire_resource('model-stable-1',
        fingerprint_sha256=hashlib.sha256(content).hexdigest(),
        verified_source_url='https://models.example.test/model.bin')
    assert binding.resource_id == 'model-stable-1'
    assert binding.local_path.read_bytes() == content
    assert vault._database.execute('SELECT count(*) FROM asset_metadata_pending').fetchone()[0] == 0


def test_remote_resource_binding_detects_missing_relinks_and_never_syncs_path(
    scratch_root: Path,
) -> None:
    _manager, _vault, lifecycle = _service(scratch_root)
    expected = hashlib.sha256(b"server-known-revision").hexdigest()

    missing = lifecycle.register_missing_resource(
        "resource-server-1",
        fingerprint_sha256=expected,
    )
    assert missing.local_path is None
    assert missing.availability is AssetAvailability.MISSING_ON_THIS_DEVICE
    assert missing.last_verified_at.tzinfo is not None
    assert missing.sync_safe_projection() == {
        "resource_id": "resource-server-1",
        "fingerprint_sha256": expected,
    }

    wrong = scratch_root / "wrong-resource.pdf"
    wrong.write_bytes(b"wrong-revision")
    with pytest.raises(LocalAssetLifecycleError, match="RESOURCE_FINGERPRINT_MISMATCH"):
        lifecycle.relink_resource("resource-server-1", wrong)
    assert (
        lifecycle.resolve_resource_binding("resource-server-1").availability
        is AssetAvailability.MISSING_ON_THIS_DEVICE
    )

    local = scratch_root / "selected" / "resource.pdf"
    local.parent.mkdir()
    local.write_bytes(b"server-known-revision")
    relinked = lifecycle.relink_resource("resource-server-1", local)
    assert relinked.availability is AssetAvailability.AVAILABLE_LOCAL
    assert relinked.local_path == local.absolute()
    assert relinked.fingerprint_sha256 == expected
    assert not any((lifecycle.vault_root / "managed-assets" / "objects").rglob("*"))

    serialized = json.dumps(relinked.sync_safe_projection(), sort_keys=True)
    assert str(local) not in serialized
    assert "local_path" not in serialized
    assert "availability" not in serialized

    local.unlink()
    missing_again = lifecycle.resolve_resource_binding("resource-server-1")
    assert missing_again.availability is AssetAvailability.MISSING_ON_THIS_DEVICE
    assert missing_again.fingerprint_sha256 == expected


def test_local_fingerprint_hashes_with_bounded_streaming_reads() -> None:
    chunks = [b"a" * 7, b"b" * 5]
    read_sizes: list[int] = []

    class _Reader:
        def __init__(self) -> None:
            self.remaining = list(chunks)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size: int) -> bytes:
            read_sizes.append(size)
            return self.remaining.pop(0) if self.remaining else b""

    class _StreamingPath:
        def open(self, mode: str):
            assert mode == "rb"
            return _Reader()

    first = lifecycle_module._sha256(_StreamingPath())  # type: ignore[arg-type]
    second = lifecycle_module._sha256(_StreamingPath())  # type: ignore[arg-type]

    assert first == second == hashlib.sha256(b"".join(chunks)).hexdigest()
    assert read_sizes
    assert set(read_sizes) == {lifecycle_module._CHUNK_SIZE}


def test_personal_local_mount_is_unchanged_until_an_explicit_asset_action(
    scratch_root: Path,
) -> None:
    manager = AccountVaultManager(VaultLayout(scratch_root / "p"))
    _OPEN_MANAGERS.append(manager)
    vault = manager.mount(AccountProfile.personal_local())
    before = {path.relative_to(vault.root).as_posix() for path in vault.root.rglob("*")}

    lifecycle = LocalAssetLifecycle(
        vault,
        account_identity={"id": "personal-local-user"},
    )

    after = {path.relative_to(vault.root).as_posix() for path in vault.root.rglob("*")}
    assert before == after
    assert lifecycle.list_assets() == []
    assert not (vault.root / "metadata" / "local-asset-lifecycle.v1.json").exists()


def test_desktop_page_renders_linked_missing_without_raw_exception(
    scratch_root: Path,
) -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton
    from desktop.local_asset_page import LocalAssetPage, safe_lifecycle_message

    app = QApplication.instance() or QApplication([])
    _manager, _vault, lifecycle = _service(scratch_root)
    linked_source = scratch_root / "page-linked.txt"
    linked_source.write_bytes(b"page linked")
    linked = lifecycle.import_linked(linked_source)
    linked_source.unlink()
    page = LocalAssetPage(lifecycle)
    try:
        assert page.table.item(0, 1).text() == "LINKED · NOT_PORTABLE"
        assert "LINKED_FILE_MISSING" in page.table.item(0, 2).text()
        buttons = page.table.cellWidget(0, 4).findChildren(QPushButton)
        assert [button.text() for button in buttons] == ["重新关联"]
        message = safe_lifecycle_message(FileNotFoundError('{"detail":"Not Found"}'))
        assert "FileNotFoundError" not in message
        assert "Not Found" not in message
        assert '{"detail"' not in message
        assert lifecycle.resolve(linked.local_asset_id).actions == ("relink",)
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_public_model_cache_install_cancel_retry_and_private_denial(scratch_root, monkeypatch):
    from backend.app import model_material_catalog as catalog
    from threading import Event
    raw=b'synthetic pinned public model';digest=hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(catalog,'TRUSTED_MODEL_FILES',{'model.safetensors':(len(raw),digest)})
    monkeypatch.setattr(catalog,'TRUSTED_MODEL_REVISION','synthetic-pinned-revision')
    manager,vault,lifecycle=_service(scratch_root)
    cache=scratch_root/'cache';cache.mkdir();(cache/'model.safetensors').write_bytes(raw)
    def forbidden(*args,**kwargs):raise AssertionError('no network or metadata publication allowed')
    monkeypatch.setattr(lifecycle,'_download_https',forbidden)
    from desktop.product_asset_metadata import bind_vault_metadata
    class NoCloudWrite:
        request=staticmethod(forbidden)
    bind_vault_metadata(vault,NoCloudWrite())
    cancel=Event();cancel.set()
    with pytest.raises(LocalAssetLifecycleError,match='DOWNLOAD_CANCELLED'):
        lifecycle.install_public_model('model-server-id',fingerprint_sha256=digest,cache_root=cache,cancel=cancel)
    assert not vault.list_assets()
    cancel.clear()
    result=lifecycle.install_public_model('model-server-id',fingerprint_sha256=digest,cache_root=cache,cancel=cancel)
    assert result.local_path.read_bytes()==raw
    assert (vault.root/'models'/'synthetic-pinned-revision'/'model.safetensors').read_bytes()==raw
    lifecycle.install_public_model('model-server-id',fingerprint_sha256=digest,cache_root=cache)
    assert len(vault.list_assets())==1
    with pytest.raises(LocalAssetLifecycleError,match='MODEL_SOURCE_UNTRUSTED'):
        lifecycle.install_public_model('private-model',fingerprint_sha256='0'*64)


def test_declared_model_stream_limits_cancel_and_retry(scratch_root,monkeypatch):
    from threading import Event
    data=b'fixture-stream'*2048
    reads=[]
    class Response:
        headers={'Content-Length':str(len(data))}
        def __enter__(self):self.pos=0;return self
        def __exit__(self,*a):pass
        def geturl(self):return 'https://huggingface.co/public/pinned/file'
        def read(self,size):
            reads.append(size);v=data[self.pos:self.pos+size];self.pos+=len(v);return v
    monkeypatch.setattr(lifecycle_module,'urlopen',lambda *a,**kw:Response())
    target=scratch_root/'stream.part';cancel=Event();cancel.set()
    with pytest.raises(LocalAssetLifecycleError):
        LocalAssetLifecycle._download_https('https://huggingface.co/public/pinned/file',target,expected_size=len(data),cancel=cancel)
    target.unlink();cancel.clear()
    progress=[]
    LocalAssetLifecycle._download_https('https://huggingface.co/public/pinned/file',target,expected_size=len(data),cancel=cancel,progress=lambda a,b:progress.append((a,b)))
    assert target.read_bytes()==data and progress[-1]==(len(data),len(data))
    assert set(reads)=={lifecycle_module._CHUNK_SIZE}
    target.unlink()
    with pytest.raises(LocalAssetLifecycleError):
        LocalAssetLifecycle._download_https('https://huggingface.co/public/pinned/file',target,expected_size=len(data)-1)


def test_intake_originals_export_canonical_package_for_empty_b(scratch_root):
    from backend.app.product_intake import stage_original_files
    source=scratch_root/'ordinary.txt';source.write_bytes(b'intake ordinary original')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    prepared={'original_files':[{'source_path':str(source),'resource_id':'product_asset_metadata:canonical-intake-resource','sha256':digest}],'missing_originals':[]}
    manager,vault,lifecycle=_service(scratch_root/'a')
    with pytest.raises(ValueError,match='ACCOUNT_MISMATCH'):
        stage_original_files(prepared,lifecycle,expected_account_id='personal-local')
    prepared['original_files'].append({**prepared['original_files'][0],'resource_id':'product_asset_metadata:second-reference'})
    stage_original_files(prepared,lifecycle,expected_account_id=vault.profile.account_id)
    stage_original_files(prepared,lifecycle,expected_account_id=vault.profile.account_id)
    assert len(vault.list_assets())==2
    package=scratch_root/'canonical.mentorflowpack'
    from desktop.account_portability import discover_portability_catalogs
    for name in ('app.db','mentorflow_v2.db'):
        (vault.root/'data'/name).unlink(missing_ok=True)
    catalogs=discover_portability_catalogs(vault.root)
    export_account_package(vault.root,package,account_identity=ACCOUNT,mode=PackageMode.FULL,
        asset_manifests=catalogs['asset_manifests'])
    manager.unmount();source.unlink()
    _,_,b=_service(scratch_root/'b')
    restored=b.restore_resource_from_package('product_asset_metadata:canonical-intake-resource',package,fingerprint_sha256=digest)
    assert restored.local_path.read_bytes()==b'intake ordinary original'


def test_resource_colon_identity_does_not_relax_package_member_ids():
    from desktop.account_portability import _safe_id,AccountPortabilityError
    assert _safe_id('product_asset_metadata:abc',field='asset_id')=='product_asset_metadata:abc'
    for field in ('local_asset_id','package_id','artifact_id'):
        with pytest.raises(AccountPortabilityError):_safe_id('unsafe:stream',field=field)

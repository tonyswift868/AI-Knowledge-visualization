from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from scripts.test_storage_policy import configured_test_temp_root

import pytest

import desktop.account_vault as account_vault_module
from desktop.account_identity import AccountIdentity, AccountProfile
from desktop.account_vault import (
    AccountVaultManager,
    AssetAvailability,
    ImportMode,
    VaultLayout,
)


def _identity(subject: str, display_name: str) -> AccountIdentity:
    return AccountIdentity(
        issuer="https://Login.Example.test/tenant/",
        subject=subject,
        display_name=display_name,
    )


@pytest.fixture
def vault_root() -> Path:
    # Keep the test root short: the frozen account layout intentionally uses
    # two full SHA-256 path components and must also run on non-long-path hosts.
    scratch = configured_test_temp_root()
    scratch.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="", dir=scratch))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_personal_local_and_enterprise_account_roots_are_distinct_and_hashed(
    vault_root: Path,
) -> None:
    layout = VaultLayout(vault_root)
    personal = layout.root_for_profile(AccountProfile.personal_local())
    account_a = layout.root_for_profile(_identity("subject-A", "Account A").profile())
    account_b = layout.root_for_profile(_identity("subject-B", "Account B").profile())

    assert personal != account_a
    assert account_a != account_b
    assert personal == layout.root / "personal-local"
    assert account_a.parts[-2:] == (
        _identity("subject-A", "Account A").issuer_hash,
        _identity("subject-A", "Account A").subject_hash,
    )
    assert account_a.parts[-1] == account_a.parts[-1].lower()
    assert "subject-A" not in str(account_a)
    assert "Login.Example.test" not in str(account_a)


def test_managed_import_is_an_owned_copy_with_sha_and_no_source_path_persistence(
    tmp_path: Path,
    vault_root: Path,
) -> None:
    layout = VaultLayout(vault_root)
    manager = AccountVaultManager(layout)
    vault = manager.mount(_identity("subject-A", "Account A").profile())
    download = tmp_path / "Downloads" / "lesson-notes.txt"
    download.parent.mkdir()
    download.write_text("managed bytes", encoding="utf-8")

    asset = vault.import_managed(download)
    managed_object = vault.managed_object_path(asset)
    expected_sha = hashlib.sha256(b"managed bytes").hexdigest()

    assert asset.import_mode is ImportMode.MANAGED
    assert asset.local_asset_id.startswith("la_")
    assert asset.sha256 == expected_sha
    assert managed_object.read_bytes() == b"managed bytes"
    assert str(download) not in vault.root.joinpath("metadata", "vault.sqlite").read_bytes().decode(
        "utf-8",
        errors="ignore",
    )
    download.unlink()
    assert managed_object.read_bytes() == b"managed bytes"
    assert vault.availability(asset) is AssetAvailability.AVAILABLE_LOCAL


def test_linked_import_is_explicitly_nonportable_and_missing_is_readable(
    tmp_path: Path,
    vault_root: Path,
) -> None:
    manager = AccountVaultManager(VaultLayout(vault_root))
    vault = manager.mount(_identity("subject-A", "Account A").profile())
    linked = tmp_path / "external" / "linked.pdf"
    linked.parent.mkdir()
    linked.write_bytes(b"linked-only")

    asset = vault.import_linked(linked)

    assert asset.import_mode is ImportMode.LINKED
    assert asset.portability_labels == ("NON_PORTABLE", "NOT_BACKED_UP")
    assert asset.object_relative_path is None
    assert vault.availability(asset) is AssetAvailability.AVAILABLE_LOCAL
    linked.unlink()
    assert vault.availability(asset) is AssetAvailability.LINKED_FILE_MISSING


def test_accounts_share_only_checksum_addressed_model_binary_not_user_data(
    tmp_path: Path,
    vault_root: Path,
) -> None:
    layout = VaultLayout(vault_root)
    manager = AccountVaultManager(layout)
    model_source = tmp_path / "downloads" / "model.bin"
    model_source.parent.mkdir()
    model_source.write_bytes(b"public-model-binary")
    checksum = hashlib.sha256(model_source.read_bytes()).hexdigest()
    shared_a = manager.install_shared_model(
        model_source,
        expected_sha256=checksum,
    )
    vault_a = manager.mount(_identity("subject-A", "Account A").profile())
    source_a = tmp_path / "a-private.txt"
    source_a.write_text("A private material", encoding="utf-8")
    asset_a = vault_a.import_managed(source_a)
    a_root = vault_a.root
    manager.unmount()

    shared_b = manager.install_shared_model(
        model_source,
        expected_sha256=checksum,
    )
    vault_b = manager.mount(_identity("subject-B", "Account B").profile())

    assert shared_a == shared_b
    assert shared_b.is_file()
    assert vault_b.list_assets() == []
    assert not str(vault_b.root).startswith(str(a_root))
    assert asset_a.display_name not in {asset.display_name for asset in vault_b.list_assets()}
    shared_parts = set(shared_b.relative_to(layout.shared_models_root).parts)
    assert not {"metadata", "cache", "derived", "sync-journal"}.intersection(
        shared_parts
    )


def test_traversal_case_and_reparse_inputs_fail_before_vault_write(
    tmp_path: Path,
    vault_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = VaultLayout(vault_root)
    manager = AccountVaultManager(layout)
    vault = manager.mount(_identity("subject-A", "Account A").profile())
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="traversal"):
        account_vault_module._safe_child(vault.root, "..", "outside.txt")
    with pytest.raises(ValueError, match="absolute"):
        VaultLayout(Path("relative-vault"))

    native_reparse = account_vault_module._is_reparse_point
    monkeypatch.setattr(
        account_vault_module,
        "_is_reparse_point",
        lambda path: Path(path) == outside or native_reparse(Path(path)),
    )
    with pytest.raises(ValueError, match="reparse"):
        vault.import_managed(outside)
    assert vault.list_assets() == []

    identity_upper = AccountIdentity(
        issuer="https://LOGIN.EXAMPLE.TEST/tenant",
        subject="subject-A",
        display_name="Account A",
    )
    identity_lower = AccountIdentity(
        issuer="https://login.example.test/tenant/",
        subject="subject-A",
        display_name="Account A",
    )
    assert layout.root_for_profile(identity_upper.profile()) == layout.root_for_profile(
        identity_lower.profile()
    )


def test_symlink_mode_is_classified_as_a_reparse_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthetic_link = os.stat_result((stat.S_IFLNK, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(Path, "lstat", lambda _path: synthetic_link)

    assert account_vault_module._is_reparse_point(Path("synthetic-link")) is True


def test_resource_bindings_are_isolated_and_restored_per_account(
    tmp_path: Path,
    vault_root: Path,
) -> None:
    manager = AccountVaultManager(VaultLayout(vault_root))
    profile_a = _identity("subject-A", "Account A").profile()
    profile_b = _identity("subject-B", "Account B").profile()
    source_a = tmp_path / "account-a.pdf"
    source_b = tmp_path / "account-b.pdf"
    source_a.write_bytes(b"account-a-resource")
    source_b.write_bytes(b"account-b-resource")
    fingerprint_a = hashlib.sha256(b"account-a-resource").hexdigest()
    fingerprint_b = hashlib.sha256(b"account-b-resource").hexdigest()

    vault_a = manager.mount(profile_a)
    binding_a = vault_a.upsert_resource_binding(
        resource_id="resource-shared-id",
        local_path=source_a,
        fingerprint_sha256=fingerprint_a,
        availability=AssetAvailability.AVAILABLE_LOCAL,
    )
    assert binding_a.account_profile == profile_a
    assert binding_a.local_path == source_a.absolute()

    vault_b = manager.mount(profile_b)
    assert vault_b.get_resource_binding("resource-shared-id") is None
    binding_b = vault_b.upsert_resource_binding(
        resource_id="resource-shared-id",
        local_path=source_b,
        fingerprint_sha256=fingerprint_b,
        availability=AssetAvailability.AVAILABLE_LOCAL,
    )
    assert binding_b.account_profile == profile_b
    assert binding_b.local_path == source_b.absolute()
    assert str(source_a) not in json.dumps(binding_b.sync_safe_projection())

    restored_a = manager.mount(profile_a).get_resource_binding("resource-shared-id")
    assert restored_a is not None
    assert restored_a.account_profile == profile_a
    assert restored_a.local_path == source_a.absolute()
    assert restored_a.fingerprint_sha256 == fingerprint_a
    assert str(source_b) not in str(restored_a.local_path)


def test_vault_database_opens_only_under_the_explicit_test_root(
    vault_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[Path] = []
    connect = account_vault_module.sqlite3.connect

    def recording_connect(database, *args, **kwargs):
        opened.append(Path(str(database)).absolute())
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(account_vault_module.sqlite3, "connect", recording_connect)
    manager = AccountVaultManager(VaultLayout(vault_root))
    manager.mount(_identity("subject-test", "Test Account").profile())
    manager.unmount()

    assert opened
    assert all(path.is_relative_to(vault_root.absolute()) for path in opened)
    assert not any(
        str(path).casefold().startswith(r"e:\mentorflowdata\personal-local")
        for path in opened
    )

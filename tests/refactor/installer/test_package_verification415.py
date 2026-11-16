from pathlib import Path

import pytest

from refactor.desktop.delivery.package_verification import (
    APPLICATION_MANAGED_INTERNAL_ROOTS,
    PackageError,
    _find_user_data_files,
)


@pytest.mark.parametrize(('relative', 'is_private'), [
    ('UserRuntime/_internal/refactor/desktop/resources/trust/account-api.pem', False),
    ('UserRuntime/_internal/data/account-api.pem', True),
    ('UserRuntime/account-api.pem', True),
    ('Sidecar/_internal/account-api.pem', True),
])
def test_private_name_scan_respects_only_known_runtime_dependency_root(
        tmp_path: Path, relative: str, is_private: bool):
    package = tmp_path / 'package'
    target = package / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b'public fixture')

    findings = _find_user_data_files(package)

    assert (relative in findings) is is_private


@pytest.mark.parametrize('prefix', ['_internal', 'UserRuntime/_internal'])
@pytest.mark.parametrize('managed_root', sorted(APPLICATION_MANAGED_INTERNAL_ROOTS))
def test_application_managed_roots_still_reject_private_directories(
        tmp_path: Path, prefix: str, managed_root: str):
    target = tmp_path / prefix / managed_root / 'private' / 'asset.txt'
    target.parent.mkdir(parents=True)
    target.write_text('fixture', encoding='utf-8')

    with pytest.raises(PackageError, match='private/development path'):
        _find_user_data_files(tmp_path)


@pytest.mark.parametrize(('name', 'content'), [
    ('settings.json', b'{"api_key":"real-credential-415"}'),
    ('database.sqlite3', b'not a public static database'),
    ('asset.bin', b'SQLite format 3\x00private database'),
])
def test_runtime_dependencies_still_scan_secrets_and_databases(
        tmp_path: Path, name: str, content: bytes):
    relative = f'UserRuntime/_internal/vendor/{name}'
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(content)

    assert relative in _find_user_data_files(tmp_path)

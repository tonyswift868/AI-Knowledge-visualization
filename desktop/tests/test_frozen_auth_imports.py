"""Authentication must use the canonical packaged app namespace, not backend.app."""
import sys
from pathlib import Path

import pytest

from desktop import enterprise_oidc


@pytest.mark.parametrize('frozen', [False, True])
def test_only_source_adds_checkout_backend_path(monkeypatch, frozen):
    root = str(Path(enterprise_oidc.__file__).resolve().parents[1] / 'backend')
    paths = [entry for entry in sys.path if entry != root]
    monkeypatch.setattr(sys, 'path', paths.copy())
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    enterprise_oidc._prepare_backend_imports()
    assert sys.path == (paths if frozen else [root] + paths)
    enterprise_oidc._prepare_backend_imports()
    assert sys.path.count(root) == (0 if frozen else 1)


def test_frozen_config_still_loads_canonical_migration_contract(monkeypatch):
    from app.enterprise.identity import issuer_migration
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    before = sys.path.copy()
    assert enterprise_oidc._migration_contract() is issuer_migration
    assert sys.path == before
    config = enterprise_oidc.DesktopOidcConfig.from_env({
        'MENTORFLOW_DESKTOP_OIDC_ISSUER':'https://identity.example.test/realms/fixture',
        'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID':'mentorflow-desktop',
        'MENTORFLOW_DESKTOP_OIDC_AUDIENCE':'mentorflow-api',
        'MENTORFLOW_ENTERPRISE_API_BASE_URL':'https://api.example.test',
    })
    assert config.issuer_migration is None
    assert config.api_base_url == 'https://api.example.test'
    assert sys.path == before


def test_frozen_missing_contract_fails_closed(monkeypatch):
    import builtins
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == 'app.enterprise.identity':
            raise ModuleNotFoundError(name='app.enterprise.identity')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    with pytest.raises(ModuleNotFoundError):
        enterprise_oidc._migration_contract()

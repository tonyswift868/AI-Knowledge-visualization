"""Authentication must use the canonical packaged app namespace, not backend.app."""
import sys
from pathlib import Path

import pytest

from desktop import enterprise_oidc


def test_source_loads_canonical_package_without_search_path_mutation():
    # A fresh interpreter proves root-only source mode; do not clear modules.
    import os
    import subprocess
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-c', "from desktop.backend_imports import prepare_backend_imports; import sys; before=list(sys.path); prepare_backend_imports(); prepare_backend_imports(); import app; assert sys.path == before; assert app.__name__ == 'app'"], cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_config_still_loads_canonical_migration_contract(monkeypatch):
    from desktop.backend_imports import prepare_backend_imports
    prepare_backend_imports()
    from app.enterprise.identity import issuer_migration
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
    monkeypatch.setattr(builtins, '__import__', unavailable)
    with pytest.raises(ModuleNotFoundError):
        enterprise_oidc._migration_contract()

"""Production shell/source contract. Native visible acceptance remains pending."""
from pathlib import Path
import pytest
from scripts.build_inno_installer import verify_core_payload

ROOT = Path(__file__).resolve().parents[3]
SHELL = (ROOT / 'installer/inno/MentorFlow.iss').read_text(encoding='utf-8')
ADAPTER = (ROOT / 'installer/inno/per-user-core.iss').read_text(encoding='utf-8')


def test_production_shortcut_and_finish_use_stable_runtime():
    assert 'PrivilegesRequired=lowest' in SHELL
    assert 'PrivilegesRequiredOverridesAllowed=' not in SHELL
    assert SHELL.count('Parameters: "launch --root') == 2
    assert 'UninstallFilesDir={app}\\Maintenance' in SHELL
    assert 'Result := InstallThroughCore(Target)' in SHELL
    assert 'AppId={#ProductAppId}' in SHELL


def test_payload_is_temp_only_and_core_owns_transaction():
    assert 'DestDir: "{tmp}\\payload"; Flags: dontcopy' in SHELL
    assert "'preflight --root '" in ADAPTER
    assert "'install --root '" in ADAPTER
    assert "' --release '" in ADAPTER and "' --close-after-health'" in ADAPTER
    assert ADAPTER.index("'preflight --root '") < ADAPTER.index("'install --root '")
    assert 'CopyFile(' not in ADAPTER and 'CreateDir(' not in ADAPTER
    assert 'CurUninstallStep <> usUninstall' in SHELL
    assert "'uninstall --root '" in SHELL and 'RaiseException(' in SHELL


def test_flat_old_payload_cannot_be_promoted(tmp_path):
    with pytest.raises(ValueError, match='Signed per-user BASE'):
        verify_core_payload(tmp_path, None)

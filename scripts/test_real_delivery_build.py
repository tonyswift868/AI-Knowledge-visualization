"""Offline release provisioning and build contract tests; no compiler/install."""
import ast
import base64
import json
import os
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import pytest
from scripts import build_real_update_delivery as build


def test_source_guard_preserves_only_explicit_unrelated_changes():
    def git(*args):
        if args==('diff','--name-only'):return '\n'.join(build.UNRELATED)
        if args[0]=='rev-parse':return 'a'*40
        return ''
    with patch.object(build,'git',git):assert build.source_identity()['source_sha']=='a'*40
    with patch.object(build,'git',lambda *args:'desktop/update_request.py'),pytest.raises(ValueError):build.source_identity()


def test_public_authority_contains_no_signing_private_material(tmp_path):
    public=build.provision(tmp_path)
    identity=dict(source_sha='a'*40,tree_sha='b'*40,app_id=build.APP_ID)
    directory=build.configure(tmp_path,public,identity)
    source=(directory/'mentorflow_delivery_authority.py').read_text(encoding='utf-8')
    private=(tmp_path/'keys/uat-ed25519.key').read_bytes()
    assert repr(private) not in source and base64.b64encode(private).decode() not in source
    assert build.provision(tmp_path)==public
    next_directory=build.configure(tmp_path,public,{**identity,'source_sha':'b'*40})
    assert next_directory!=directory
    assert (directory/'mentorflow_delivery_authority.py').read_text(encoding='utf-8')==source
    with pytest.raises(ValueError):build.configure(tmp_path,{**public,'key_id':'changed-key'},identity)
    assert build.version_doc('1.2.1',tmp_path,public,'http://127.0.0.1:48147')['desktop_notice']
    assert build.version_doc('1.2.2',tmp_path,public,'http://127.0.0.1:48147',bad=True)['uat_recovery_fixture']=='exit-before-health-v1'


def test_spec_real_entry_and_onedir_nsis_contract():
    spec=(build.PROJECT/'release/real-delivery-app.spec').read_text(encoding='utf-8')
    ast.parse(spec)
    assert "('desktop_gui_entry',str(root/'desktop_gui_entry.py'),'PYSOURCE')" in spec
    nsis=(build.PROJECT/'installer/ProtectedInstall.nsh').read_text(encoding='utf-8')
    assert 'File /r "${TRANSACTION_RUNTIME}\\*.*"' in nsis
    assert '"$PLUGINSDIR\\MentorFlowInstallGate.exe"' not in nsis


def test_installer_build_only_repair_preserves_runtime_identity(tmp_path):
    runtime=dict(source_sha='a'*40,tree_sha='b'*40,app_id=build.APP_ID)
    builder={**runtime,'source_sha':'c'*40,'tree_sha':'d'*40}
    app=tmp_path/'App';app.mkdir()
    evidence=tmp_path/'base.json'
    build.emit(evidence,dict(source=runtime,app=str(app)))
    build.emit(tmp_path/'base-current.json',dict(evidence=str(evidence)))
    args=SimpleNamespace(work=tmp_path,install_root=tmp_path/'Installed')
    def run(command,cwd,log,env):
        assert command[command.index('-SourceSha')+1]==runtime['source_sha']
        assert env['PSModulePath'].endswith('/WindowsPowerShell/v1.0/Modules')
        (app/'Installer').mkdir()
        (app/'Installer/Setup.exe').write_bytes(b'test-only')
    with patch.object(build,'git',lambda *a:'scripts/build_windows_installer.ps1' if a[0]=='diff' else ''),patch.object(build,'run',run):
        build.installer(args,builder)
    result=json.loads((tmp_path/'installer-evidence.json').read_bytes())
    assert result['source']==runtime and result['builder_source']==builder
    with patch.object(build,'git',lambda *a:'desktop_gui_entry.py' if a[0]=='diff' else ''),pytest.raises(ValueError,match='Runtime changed'):
        build.installer(args,builder)


def test_windows_powershell_reads_utf8_version_explicitly():
    source=(build.PROJECT/'scripts/build_windows_installer.ps1').read_text(encoding='utf-8')
    assert '$versionInput -Raw -Encoding UTF8 | ConvertFrom-Json' in source

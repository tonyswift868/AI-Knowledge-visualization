"""Offline release provisioning and build contract tests; no compiler/install."""
import ast
import base64
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
    with pytest.raises(ValueError):build.configure(tmp_path,public,{**identity,'source_sha':'b'*40})
    assert build.version_doc('1.2.1',tmp_path,public,'http://127.0.0.1:48147')['desktop_notice']
    assert build.version_doc('1.2.2',tmp_path,public,'http://127.0.0.1:48147',bad=True)['uat_recovery_fixture']=='exit-before-health-v1'


def test_spec_real_entry_and_onedir_nsis_contract():
    spec=(build.PROJECT/'release/real-delivery-app.spec').read_text(encoding='utf-8')
    ast.parse(spec)
    assert "('desktop_gui_entry',str(root/'desktop_gui_entry.py'),'PYSOURCE')" in spec
    nsis=(build.PROJECT/'installer/ProtectedInstall.nsh').read_text(encoding='utf-8')
    assert 'File /r "${TRANSACTION_RUNTIME}\\*.*"' in nsis
    assert '"$PLUGINSDIR\\MentorFlowInstallGate.exe"' not in nsis

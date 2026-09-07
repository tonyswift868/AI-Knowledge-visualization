import json
import os
from pathlib import Path
import subprocess

import pytest
from desktop.tests.test_mature_update import feed
from desktop.update_protocol import MANIFEST_PATH, digest, SecurityError
from desktop.update_tree_security import AcceptedApp, DirectoryPins, immutable_path
from scripts.runtime_path_budget import budget, runtime_root, MESSAGE


def accept(feed):
    raw=(feed.app/MANIFEST_PATH).read_bytes()
    rows={r['path']:r for r in json.loads(raw)['files']}
    rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(raw),sha256=digest(raw))
    return AcceptedApp(feed.app,rows,digest(raw))


def test_real_ntfs_links_immutable_lease_and_final_hash(feed):
    with accept(feed) as accepted:
        service=feed.service(); service.cache.accepted_app=accepted
        assert service.check()['state']=='UPDATE_AVAILABLE'
        assert service.download()['state']=='READY_TO_APPLY'
        source=feed.app/'MentorFlow.exe'; target=service.staged/'MentorFlow.exe'
        assert source.samefile(target) and source.stat().st_nlink==2
        assert not (feed.app/'_internal/frontend/ui.txt').samefile(service.staged/'_internal/frontend/ui.txt')
        with pytest.raises(OSError): source.write_bytes(b'not allowed')
        assert source.read_bytes()==feed.old_files['MentorFlow.exe']
        service.cache.verify_tree(service.staged,service.release)
        # Fixture-only fault injection models a broken immutable environment.
        # No packaged previous version is modified by this test.
        accepted.pins.kernel.CloseHandle(accepted.files.pop('MentorFlow.exe'))
        source.write_bytes(b'X'*len(feed.old_files['MentorFlow.exe']))
        with pytest.raises(SecurityError,match='hash mismatch'):
            service.cache.verify_tree(service.staged,service.release)


@pytest.mark.parametrize('code,expected',[(50,'READY_TO_APPLY'),(5,'READY_TO_APPLY'),(87,'FAILED_SECURITY')])
def test_hardlink_capability_fallback_not_unknown_failure(feed,monkeypatch,code,expected):
    with accept(feed) as accepted:
        service=feed.service(); service.cache.accepted_app=accepted; service.check()
        def fail(*args,**kwargs):
            error=OSError('fixture capability failure'); error.winerror=code; raise error
        monkeypatch.setattr(os,'link',fail)
        assert service.download()['state']==expected
        if service.staged:
            assert not (feed.app/'MentorFlow.exe').samefile(service.staged/'MentorFlow.exe')
        assert (feed.app/'MentorFlow.exe').read_bytes()==feed.old_files['MentorFlow.exe']


def test_unique_directory_pin_blocks_rename_and_reparse_escape(tmp_path):
    folder=tmp_path/'pinned'; folder.mkdir()
    with DirectoryPins() as pins:
        assert pins.check(folder,tmp_path,directory=True)
        count=pins.calls
        assert pins.check(folder,tmp_path,directory=True) and pins.calls==count+4
        (folder/'child.txt').write_bytes(b'child creation allowed')
        folder.rename(tmp_path/'renamed')
        folder.mkdir()
        with pytest.raises(SecurityError): pins.check(folder,tmp_path,directory=True)
        with pytest.raises(SecurityError): pins.check(tmp_path/'../outside',tmp_path)
    outside=tmp_path/'outside'; outside.mkdir()
    junction=tmp_path/'junction'
    result=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(outside)],capture_output=True)
    assert result.returncode==0
    with DirectoryPins() as pins:
        with pytest.raises(SecurityError): pins.check(junction,tmp_path,directory=True)


def test_budget_default_custom_near_and_deep():
    paths=['x'*145]
    for root in ('E:/MentorFlow','D:/MFU7/base'):
        assert budget(root,paths)['staging_app_absolute_max_path']<=240
    maximum=budget('E:/MentorFlow',paths)['max_supported_install_root_length']
    near='D:/'+ 'x'*(maximum-3)
    assert budget(near,paths)['staging_app_absolute_max_path']==240
    with pytest.raises(ValueError,match=MESSAGE): budget(near+'x',paths)
    with pytest.raises(ValueError,match=MESSAGE):
        budget('D:/CodexScratch/MentorFlow/MF-E107-UPDATER-REBUILD-STARTUP-PERF-20260907-06/base',paths)
    with pytest.raises(ValueError): budget('//host/share/app',paths)
    with pytest.raises(ValueError): budget('D:/safe/../escape',paths)
    with pytest.raises(ValueError,match=MESSAGE): runtime_root('D:/'+ 'x'*150,paths)
    for path in ('Config/runtime.json','logs/a.log','cache/object','state.json','_internal/update-uat/public.json'):
        assert not immutable_path(path)


def test_accepted_source_name_replacement_is_not_trusted(feed):
    with accept(feed) as accepted:
        service=feed.service(); service.cache.accepted_app=accepted; service.check()
        item=feed.app/'MentorFlow.exe'
        item.rename(feed.app/'replaced-source.fixture')
        item.write_bytes(feed.old_files['MentorFlow.exe'])
        assert service.download()['state']=='FAILED_SECURITY'


def test_cancel_hardlink_materialization_preserves_current(feed,monkeypatch):
    with accept(feed) as accepted:
        service=feed.service(); service.cache.accepted_app=accepted; service.check()
        original=os.link
        def link_then_cancel(*args,**kwargs):
            result=original(*args,**kwargs); service.cancel_download(); return result
        monkeypatch.setattr(os,'link',link_then_cancel)
        assert service.download()['state']=='DOWNLOAD_PAUSED_OR_INTERRUPTED'
        assert (feed.app/'MentorFlow.exe').read_bytes()==feed.old_files['MentorFlow.exe']

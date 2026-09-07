"""Updater Windows file/process qualification with synthetic payloads only.

Uses real existing transaction/receipt/hash verification and process termination.
ACL/elevation, HKLM and shortcut writes are fixture adapters, NOT production gates.
Restart is a real Python fixture process, NOT a shipped MentorFlow EXE/UI claim.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

import pytest

from scripts import windows_install_transaction as tx
from scripts import windows_install_trust as trust
from scripts import windows_update_apply as apply
from scripts import verify_windows_package as v
from scripts.test_install_process_recovery import adapters, Registry, Shortcut
from desktop.tests.test_mature_update import Feed
from desktop.update_protocol import canonical, digest


def prepare(base):
    feed=Feed(base)
    feed.old_files.update({
        'SELF-CHECK/MentorFlowSelfCheck.exe':b'MZ fixture helper',
        'SELF-CHECK/verify_windows_package.py':b'# fixture',
        'SELF-CHECK/mentorflow-check.bat':b'@echo fixture',
        'SELF-CHECK/mentorflow-update.bat':b'@echo fixture',
        'mentorflow-version.json':canonical(dict(schema=v.VERSION_SCHEMA,product='MentorFlow',version='1.1.0')),
        'fixture_entry.py':b'import json,pathlib,sys,time; print(json.loads(pathlib.Path(__file__).with_name("mentorflow-version.json").read_bytes())["version"], flush=True)\nif "--hold" in sys.argv: time.sleep(20)\n',
    })
    for path,data in feed.old_files.items():
        dest=feed.app/path; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(data)
    (feed.app/'_internal/runtime.dll').write_bytes(b'fixture unchanged runtime')
    feed.old_files['_internal/runtime.dll']=b'fixture unchanged runtime'
    old_manifest=feed.manifest('1.1.0',feed.old_files)
    (feed.app/'SELF-CHECK/package-manifest.json').write_bytes(canonical(old_manifest))
    source=base/'base-source'; feed.app.rename(source)
    old_ids=dict(manifest_sha256=tx.sha(source/v.MANIFEST_RELATIVE),source_sha='a'*40,tree_sha='b'*40)
    uninstaller=base/'Uninstall.exe'; uninstaller.write_bytes(b'MZ fixture uninstaller')
    with patch.object(tx,'cleanup_failed',return_value=0):
        tx.Transaction(source,feed.install,old_ids,uninstaller,registry=Registry(base),shortcut=Shortcut(base)).run()
    new_files={**feed.old_files,'_internal/frontend/ui.txt':b'updated small frontend',
        'mentorflow-version.json':canonical(dict(schema=v.VERSION_SCHEMA,product='MentorFlow',version='1.1.1'))}
    feed.signed=feed.publish('1.1.1',new_files)
    return feed,old_ids


def restart_fixture(root):
    # The fixture's binary protocol is interpreted by an existing Python runtime.
    # No user window, no elevation, no actual product exe or DB is launched.
    result=subprocess.run([getattr(sys,'_base_executable',sys.executable),'-B',str(root/'App/fixture_entry.py')],
        capture_output=True,text=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stderr
    return result.stdout.strip()


@pytest.fixture
def case():
    base=Path(tempfile.mkdtemp(prefix='up-',dir=os.environ['TEMP']))
    assert (base.parent == Path('D:/MF14W') or
            base.parent.is_relative_to(Path('D:/CodexScratch/MentorFlow')))
    with ExitStack() as stack:
        adapters(stack)
        feed,old_ids=prepare(base)
        try: yield base,feed,old_ids
        finally: feed.close()
    # Retain tiny evidence trees; do not recursively delete unknown state.


@pytest.mark.parametrize('fallback',[False,True])
def test_signed_download_apply_receipt_restart_previous_and_rollback(case,fallback):
    base,feed,old_ids=case
    user_data=base/'user-data.json'; user_data.write_bytes(b'original 56 plans fixture')
    before=digest(user_data.read_bytes())
    if fallback:
        feed.signed['base_versions']=[]; feed.sign(feed.signed)
    service=feed.service()
    assert service.check()['state']=='UPDATE_AVAILABLE'
    assert service.download()['state']=='READY_TO_APPLY'
    assert restart_fixture(feed.install)=='1.1.0'
    with patch.object(apply,'PRODUCTION_RELEASE_KEYS',feed.keys), \
         patch.object(tx,'Registry',lambda:Registry(base)), \
         patch.object(tx,'Shortcut',lambda _:Shortcut(base)), \
         patch.object(tx,'cleanup_failed',side_effect=AssertionError('update must not clean default install')):
        result=apply.apply_update(feed.install,service.staged,service.release.envelope)
        assert result['pending_update_health'] is True
        previous=Path(result['previous_retained'])
        assert (previous/'prior-program/App/MentorFlow.exe').is_file()
        assert restart_fixture(feed.install)=='1.1.1'
        receipt=json.loads((feed.install/trust.RECEIPT).read_bytes())
        assert receipt['binding']['version']=='1.1.1'
        assert tx.sha(feed.install/'App'/v.MANIFEST_RELATIVE)==digest(service.release.manifest_bytes)
        assert Registry(base).snapshot()['Version']=='1.1.1'
        rollback=apply.rollback_previous(feed.install,previous.name,registry=Registry(base),shortcut=Shortcut(base))
        assert rollback['status']=='ROLLED_BACK'
        assert restart_fixture(feed.install)=='1.1.0'
        assert trust.verify_installation(feed.install,**old_ids,workers=1)['verification']=='PASS'
    assert digest(user_data.read_bytes())==before
    print('SYNTHETIC_APPLY_RESTART='+json.dumps(dict(path=service.plan['update_path'],
        full_bytes=service.plan['full_bytes'],actual_downloaded_bytes=service.snapshot['actual_downloaded_bytes'],
        current_after_apply='1.1.1',current_after_rollback='1.1.0',real_product_exe=False,real_acl=False)))


def child(base,source,point):
    with ExitStack() as stack:
        adapters(stack)
        manifest=json.loads((source/v.MANIFEST_RELATIVE).read_bytes())
        ids=dict(manifest_sha256=tx.sha(source/v.MANIFEST_RELATIVE),source_sha='a'*40,tree_sha='b'*40)
        def fault(at):
            if at==point:
                (base/'owned-checkpoint.json').write_bytes(canonical(dict(pid=os.getpid(),point=at)))
                time.sleep(25)
                raise RuntimeError('parent did not terminate owned process')
        tx.Transaction(source,base/'install',ids,base/'Uninstall.exe',registry=Registry(base),
            shortcut=Shortcut(base),retain_previous=True,fault=fault).run()


@pytest.mark.parametrize('point',['after_swap','before_receipt_publish','receipt_published'])
def test_process_killed_during_update_preserves_launchable_version(case,point):
    base,feed,old_ids=case
    service=feed.service(); service.check(); service.download()
    marker=base/'owned-checkpoint.json'
    process=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-B','-m',
        'scripts.test_mature_update_transaction','--child',str(base),str(service.staged),point],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW,
        env={**os.environ,'PYTHONPATH':os.pathsep.join(p for p in sys.path if p)})
    try:
        deadline=time.monotonic()+15
        while not marker.exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.01)
        assert marker.exists(),process.communicate(timeout=2)
        assert json.loads(marker.read_bytes())['pid']==process.pid
        process.kill(); process.communicate(timeout=5)
        assert process.returncode is not None
    finally:
        if process.poll() is None: process.kill(); process.communicate(timeout=5)
    recovery=tx.Transaction(service.staged,feed.install,{},base/'Uninstall.exe',registry=Registry(base),shortcut=Shortcut(base))
    recovery.recover()
    expected='1.1.1' if point=='receipt_published' else '1.1.0'
    assert restart_fixture(feed.install)==expected
    trust.verify_installation(feed.install,**tx.identities_from_receipt(feed.install),workers=1)
    if expected=='1.1.1':
        assert list((feed.install/tx.STAGING/tx.PREVIOUS).glob('*/prior-program/App/MentorFlow.exe'))


def test_failed_first_launch_process_is_detected():
    process=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-B','-c','raise SystemExit(23)'],
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        result=apply.observe_first_launch(process,expected_version='1.1.1',ready_probe=lambda:None,timeout=3)
        assert result['state']=='FAILED_FIRST_LAUNCH' and result['rollback_required']
        assert result['exit_code']==23
    finally:
        if process.poll() is None: process.kill()
        process.wait(timeout=3)


def test_ready_process_observation_does_not_mark_receipt_healthy():
    process=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-B','-c','import time; time.sleep(5)'],
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        result=apply.observe_first_launch(process,expected_version='1.1.1',
            ready_probe=lambda:dict(pid=process.pid,version='1.1.1',ready=True),timeout=1)
        assert result['pending_privileged_attestation'] is True
    finally:
        process.kill(); process.wait(timeout=3)


def test_update_retention_blocks_unhealthy_second_apply(case):
    base,feed,_=case
    service=feed.service(); service.check(); service.download()
    with patch.object(apply,'PRODUCTION_RELEASE_KEYS',feed.keys), \
         patch.object(tx,'Registry',lambda:Registry(base)),patch.object(tx,'Shortcut',lambda _:Shortcut(base)):
        first=apply.apply_update(feed.install,service.staged,service.release.envelope)
        with pytest.raises(Exception,match='health'):
            apply.apply_update(feed.install,service.staged,service.release.envelope)
        assert Path(first['previous_retained']).exists()


def test_custom_volume_targets_contract_and_formal_data_refusal():
    # No target paths created: covers naming and volume admission, not real ACL.
    for name in ('E:/MentorFlow-Updater-Qualification','D:/MentorFlow-Updater-Qualification'):
        with patch.object(trust,'assert_safe_parents',lambda _:None):
            assert tx.validate_target(Path(name))==Path(name)
    with pytest.raises(ValueError): tx.validate_target(Path('E:/MentorFlowData'))


def test_update_card_check_download_apply_fixture_handoff(case):
    from PySide6.QtWidgets import QApplication
    from desktop.update_panel import UpdatePanel
    base,feed,_=case
    application=QApplication.instance() or QApplication([])
    service=feed.service(); results=[]
    def bridge(root,source,envelope):
        result=apply.apply_update(root,source,envelope)
        result['fixture_restart_version']=restart_fixture(root)
        results.append(result); return result
    service.apply_bridge=bridge
    panel=UpdatePanel(service=service,auto_delay_ms=60000); panel.show()
    def finish():
        deadline=time.monotonic()+10
        while panel._thread and panel._thread.is_alive() and time.monotonic()<deadline:
            application.processEvents(); time.sleep(.01)
        assert not panel._thread.is_alive()
        panel.refresh(); application.processEvents()
    try:
        with patch.object(apply,'PRODUCTION_RELEASE_KEYS',feed.keys), \
             patch.object(tx,'Registry',lambda:Registry(base)),patch.object(tx,'Shortcut',lambda _:Shortcut(base)):
            panel.check_button.click(); finish()
            assert panel.download_button.isEnabled()
            panel.download_button.click(); finish()
            assert service.snapshot['state']=='READY_TO_APPLY' and panel.apply_button.isEnabled()
            panel.apply_button.click(); finish()
            assert results[0]['fixture_restart_version']=='1.1.1'
            assert service.snapshot['state']=='RESTART_REQUIRED'
    finally: panel.shutdown(); panel.close()


def test_cancel_download_keeps_old_fixture_process_running(case):
    import threading
    base,feed,_=case
    feed.signed=feed.publish('1.1.1',{**feed.old_files,'_internal/frontend/ui.txt':b'new-download'*262144})
    feed.delay=.003
    service=feed.service(); service.check()
    process=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-B',
        str(feed.install/'App/fixture_entry.py'),'--hold'],stdout=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    thread=threading.Thread(target=service.download,daemon=True)
    try:
        assert process.stdout.readline().strip()==b'1.1.0'
        thread.start(); deadline=time.monotonic()+5
        while service.snapshot.get('actual_downloaded_bytes',0)<131072 and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.005)
        service.cancel_download(); thread.join(5)
        assert not thread.is_alive()
        assert service.snapshot['state']=='DOWNLOAD_PAUSED_OR_INTERRUPTED'
        assert process.poll() is None
        assert tx.identities_from_receipt(feed.install)['manifest_sha256']==tx.sha(feed.install/'App'/v.MANIFEST_RELATIVE)
    finally:
        service.cancel_download(); thread.join(5) if thread.ident else None
        if process.poll() is None: process.kill()
        process.communicate(timeout=3)


def test_helper_replay_floor_independent_of_download_cache(case):
    from desktop.update_protocol import verify_release, SecurityError
    base,feed,_=case
    service=feed.service(); service.check(); service.download()
    old_envelope=service.release.envelope
    feed.signed['metadata_version']=2; feed.sign(feed.signed)
    newer=verify_release(feed.raw,feed.keys,current='1.1.0')
    with patch.object(apply,'PRODUCTION_RELEASE_KEYS',feed.keys):
        apply.protected_witness(feed.install,'stable',newer)
        with pytest.raises(SecurityError,match='replay'):
            apply.apply_update(feed.install,service.staged,old_envelope)
        assert apply.protected_witness(feed.install,'stable')[0]==2
        assert restart_fixture(feed.install)=='1.1.0'


if __name__=='__main__':
    assert sys.argv[1]=='--child'
    child(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4])

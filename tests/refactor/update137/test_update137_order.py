"""Signed local source, real serial adapter worker; no installed executable."""
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop.updates import DesktopUpdates
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery import per_user_install as install
from tests.refactor.delivery.test_delivery_per_user_install import case
from tests.refactor.delivery.test_delivery_one_click_update_core import setup


def pump(app, predicate, seconds=10):
    end = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < end, 'adapter condition did not complete'
        app.processEvents()
        time.sleep(.002)
    app.processEvents()


@pytest.mark.parametrize('when', ['ready', 'running', 'failed'])
def test_update137_offer_before_download_and_click_continues(case, monkeypatch, when):
    root, service = setup(case, monkeypatch)
    app = QApplication.instance() or QApplication([])
    gate = QWidget()
    started, release = threading.Event(), threading.Event()
    events, states, progress = [], [], []
    monkeypatch.setattr(core, 'apply_and_relaunch', lambda *a, **k:
                        (events.append('applied') or {'state':'CURRENT_HEALTHY'}))
    class Core:
        def attach_shutdown_guard(self, app): pass
        record_update_authorization = staticmethod(core.record_update_authorization)
        def check_latest(self, root, **kw): return core.check_latest(root, service=service, **kw)
        def download_latest(self, root, **kw):
            events.append('download_started')
            started.set()
            assert release.wait(10)
            if when == 'failed': raise OSError('local test interruption')
            return core.download_latest(root, service=service, **kw)
        def update_to_latest(self, root, **kw):
            events.append('consented')
            kw.pop('pid');kw.pop('hwnd_hex')
            return core.update_to_latest(root, service=service, pid=1234, hwnd_hex='0001', **kw)
    adapter = DesktopUpdates(app, root, gate, core=Core())
    def changed(value):
        states.append(dict(value))
        if value['state']=='UPDATE_AVAILABLE' and 'offered' not in events:
            events.append('offered')
        if value.get('total_download_files') is not None: progress.append(dict(value))
    adapter.changed.connect(changed)
    try:
        adapter.run('check',gate)
        pump(app,started.is_set)
        assert events[:2]==['offered','download_started']
        assert not adapter.snapshot()['busy']
        assert adapter.snapshot()['latest']=='1.4.1'
        if when=='running':
            adapter.run('install',gate)
            assert adapter.snapshot()['busy']
            assert 'consented' not in events  # One serial writer, not a second download.
        release.set()
        if when!='running':
            pump(app,lambda:not adapter._background_preparing)
            assert adapter.snapshot()['prepared'] is (when=='ready')
            adapter.run('install',gate)
        pump(app,lambda:adapter.snapshot()['state']=='CURRENT_HEALTHY')
        assert events.count('download_started')==1
        assert events.count('consented')==events.count('applied')==1
        assert service.cache.transport.bytes_received>0
        assert progress
        assert all(0<=p['downloaded_files']<=p['total_download_files'] for p in progress)
        assert any(p.get('download_state')=='PREPARING' for p in states)
        assert any('正在下载' in p.get('message','') for p in states)
        if when!='failed':
            assert any(p.get('prepared') and '已下载好' in p.get('message','') for p in states)
    finally:
        release.set();adapter.close();adapter._pool.shutdown(wait=True);gate.close();service.close()


@pytest.mark.parametrize('action', ['startup','automatic','check'])
def test_update137_all_checks_publish_before_background(tmp_path, monkeypatch, action):
    app=QApplication.instance() or QApplication([]);gate=QWidget();events=[]
    class Core:
        def attach_shutdown_guard(self, app): pass
        def get_startup_update_state(self,*a,check_feed=False,**kw):
            return {'state':'OPTIONAL_UPDATE' if check_feed else 'HEALTHY','login_allowed':True,'latest':'9.0.1'}
        def check_latest(self,*a,**kw):return {'state':'UPDATE_AVAILABLE','latest':'9.0.1'}
        def download_latest(self,*a,**kw):events.append('download');return {'prepared':True,'state':'UPDATE_DOWNLOADED'}
    monkeypatch.setattr(install,'schedule_program_cleanup',lambda *a,**k:None)
    monkeypatch.setattr(install,'schedule_updater_refresh',lambda *a,**k:None)
    adapter=DesktopUpdates(app,tmp_path,gate,core=Core())
    adapter._show_prompt=lambda:None
    adapter.changed.connect(lambda s: events.append('offer') if s['state'] in ('UPDATE_AVAILABLE','OPTIONAL_UPDATE') else None)
    try:
        adapter.run(action,gate)
        pump(app,lambda:'download' in events and not adapter._background_preparing)
        assert events.index('offer')<events.index('download')
        assert not adapter.snapshot()['busy'] and adapter.snapshot()['prepared']
    finally:adapter.close();adapter._pool.shutdown(wait=True);gate.close()


@pytest.mark.parametrize('state,message', [
    ('UP_TO_DATE','当前已是最新版本'),
    ('FAILED_SECURITY','更新校验未通过，已阻止更新。'),
    ('FAILED_RECOVERABLE','暂时无法检查更新，当前版本可继续使用。')])
def test_update137_other_check_results_unchanged(tmp_path,state,message):
    app=QApplication.instance() or QApplication([]);gate=QWidget()
    class Core:
        def attach_shutdown_guard(self, app):pass
        def check_latest(self,*a,**k):return {'state':state,'message':message}
        def download_latest(self,*a,**k):pytest.fail('no verified offer')
    adapter=DesktopUpdates(app,tmp_path,gate,core=Core())
    try:
        adapter.run('check',gate);pump(app,lambda:not adapter.snapshot()['busy'])
        assert adapter.snapshot()['state']==state
        assert adapter.snapshot()['message']==message
    finally:adapter.close();adapter._pool.shutdown(wait=True);gate.close()


def test_update137_file_and_byte_progress_from_signed_assets(case,monkeypatch):
    root,service=setup(case,monkeypatch);values=[]
    try:
        result=core.download_latest(root,values.append,service=service)
        assert result['prepared']
        total=len(service.plan['download_paths'])
        expected=sum(service.release.assets[p]['size'] for p in service.plan['download_paths'])
        ready=next(v for v in values if v['state']=='READY_TO_APPLY')
        assert ready['downloaded_files']==ready['total_download_files']==total
        assert ready['downloaded_bytes']==ready['expected_download_bytes']==expected
        assert [v['state'] for v in values][0]=='PREPARING'
        assert any(v['state']=='DOWNLOADING' and v['downloaded_files']<total for v in values)
        assert values[-2]['state']=='PREPARING' and values[-1]['state']=='READY_TO_APPLY'
    finally:service.close()

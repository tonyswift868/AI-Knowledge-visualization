"""Real localhost body-byte qualification; synthetic files, never real installs."""
import base64
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from refactor.desktop.delivery.update_protocol import canonical
from refactor.desktop.delivery.update_protocol import digest
from refactor.desktop.delivery.update_protocol import verify_release
from refactor.desktop.delivery.update_protocol import version_key
from refactor.desktop.delivery.update_protocol import make_plan
from refactor.desktop.delivery.update_protocol import SecurityError
from refactor.desktop.delivery.update_protocol import RecoverableError
from refactor.desktop.delivery.update_protocol import DownloadCancelled
from refactor.desktop.delivery.update_protocol import MANIFEST_PATH
from refactor.desktop.delivery.update_download import HTTPTransport
from refactor.desktop.delivery.update_download import ObjectCache
from refactor.desktop.delivery.update_download import InstallUpdateLock
from refactor.desktop.delivery.update_service import MatureUpdateService


class Feed:
    def __init__(self, root):
        self.root = root
        self.key = Ed25519PrivateKey.generate()  # ephemeral TEST key, never persisted
        self.keys = {'fixture': self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}
        self.objects, self.requests = {}, []
        self.raw = b''
        self.no_range = self.corrupt = False
        self.delay = 0
        self.disconnect_once = False
        self.sent = 0
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                data = fixture.raw if self.path == '/stable.json' else fixture.objects.get(self.path)
                if data is None:
                    self.send_error(404); return
                if fixture.corrupt and self.path != '/stable.json':
                    data = b'X' + data[1:]
                range_value = self.headers.get('Range')
                offset = int(range_value[6:-1]) if range_value and not fixture.no_range else 0
                fixture.requests.append((self.path, range_value))
                self.send_response(206 if offset else 200)
                self.send_header('Content-Length', str(len(data)-offset))
                if offset:
                    self.send_header('Content-Range', f'bytes {offset}-{len(data)-1}/{len(data)}')
                self.end_headers()
                try:
                    for start in range(offset, len(data), 16384):
                        if fixture.delay and self.path != '/stable.json': time.sleep(fixture.delay)
                        block = data[start:start+16384]
                        self.wfile.write(block); self.wfile.flush(); fixture.sent += len(block)
                        if fixture.disconnect_once and self.path != '/stable.json':
                            fixture.disconnect_once=False
                            self.close_connection=True
                            return
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.install = root/'install'; self.app = self.install/'App'; self.app.mkdir(parents=True)
        self.old_files = {'MentorFlow.exe':b'MZ synthetic application',
                          '_internal/runtime.dll':b'unchanged-runtime\0' * (256*1024),
                          '_internal/frontend/ui.txt':b'old frontend\n'}
        for path, content in self.old_files.items():
            item = self.app/path; item.parent.mkdir(parents=True, exist_ok=True); item.write_bytes(content)
        self.old_manifest = self.manifest('1.1.0', self.old_files)
        manifest_path = self.app/MANIFEST_PATH; manifest_path.parent.mkdir()
        manifest_path.write_bytes(canonical(self.old_manifest))
        self.signed = self.publish('1.1.1', {**self.old_files, '_internal/frontend/ui.txt':b'new frontend\n'})

    def manifest(self, version, files):
        return dict(schema='mentorflow.windows-package-manifest.v1', product='MentorFlow',
                    version=version, executable='MentorFlow.exe',
                    files=[dict(path=p,size=len(data),sha256=digest(data)) for p,data in files.items()])

    def sign(self, signed):
        self.raw = canonical(dict(signed=signed, key_id='fixture',
            signature=base64.b64encode(self.key.sign(canonical(signed))).decode('ascii')))
        return self.raw

    def publish(self, version, files):
        manifest = self.manifest(version, files)
        files = {**files, MANIFEST_PATH:canonical(manifest)}
        assets = []
        for path, data in files.items():
            key = '/objects/'+digest(data); self.objects[key] = data
            assets.append(dict(path=path,size=len(data),sha256=digest(data),url=self.origin+key))
        now = datetime.now(timezone.utc)
        signed = dict(schema_version=1,app_id='MentorFlow',channel='stable',version=version,
            metadata_version=1, issued_at=now.isoformat(), key_id='fixture',
            rollout_percentage=100,rollout_seed='fixture-v1',withdrawn=False,
            published_at=now.isoformat(),expires_at=(now+timedelta(days=7)).isoformat(),
            min_supported_version='1.0.0',min_updater_version='1.0.0',mandatory=False,mandatory_reason='',
            release_notes='Small frontend fix',full_release=dict(format='file-set-v1',size=sum(len(d) for d in files.values()),assets=assets),
            incremental_assets=[a['path'] for a in assets],file_manifest=manifest,hash_algorithm='sha256',
            signature_algorithm='ed25519',source_sha='a'*40,tree_sha='b'*40,base_versions=['1.1.0'])
        self.signed=signed
        self.sign(signed); return signed

    def service(self, suffix='cache'):
        return MatureUpdateService(install_root=self.install, cache_root=self.root/suffix,
            metadata_url=self.origin+'/stable.json',public_keys=self.keys,current='1.1.0',
            transport=HTTPTransport(self.origin,fixture_http=True,total=3,read=1,connect=1))

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


@pytest.fixture
def feed(tmp_path):
    value = Feed(tmp_path)
    yield value
    value.close()


def test_real_http_incremental_110_to_111_and_data_untouched(feed, tmp_path):
    data = tmp_path/'user-data'; data.mkdir(); (data/'plans.json').write_bytes(b'56 original plans fixture')
    prior = {str(p):digest(p.read_bytes()) for p in feed.app.rglob('*') if p.is_file()}
    service = feed.service()
    checked = service.check()
    assert checked['state'] == 'UPDATE_AVAILABLE'
    assert checked['update_path'] == 'FILE_LEVEL_INCREMENTAL'
    result = service.download()
    assert result['state'] == 'READY_TO_APPLY'
    assert 0 < result['actual_downloaded_bytes'] < result['full_bytes']/100
    assert result['actual_downloaded_bytes'] == checked['expected_download_bytes']
    assert result['saved_bytes'] == result['full_bytes']-result['actual_downloaded_bytes']
    assert (service.staged/'_internal/frontend/ui.txt').read_bytes() == b'new frontend\n'
    assert prior == {str(p):digest(p.read_bytes()) for p in feed.app.rglob('*') if p.is_file()}
    assert (data/'plans.json').read_bytes() == b'56 original plans fixture'
    print('REAL_HTTP_INCREMENTAL_METRICS='+json.dumps({k:result[k] for k in ('full_bytes','actual_downloaded_bytes','saved_bytes','saving_percent')}))


def test_same_version_no_update(feed):
    feed.publish('1.1.0',feed.old_files)
    assert feed.service().check()['state'] == 'UP_TO_DATE'


def test_rebuild_phase_trace_and_independent_changed_object(feed):
    service = feed.service(); service.check()
    assert service.download()['state'] == 'READY_TO_APPLY'
    trace = service.telemetry.report()
    assert [r['name'].split('_')[0] for r in trace['events']] == [f'T{i:02}' for i in range(15)]
    for row in trace['events']:
        assert row['end_monotonic'] >= row['start_monotonic']
        assert not row['errors']
        assert {'file_count','bytes_read','bytes_written','bytes_reused','bytes_hashed'} <= row.keys()
    source = feed.app/'_internal/frontend/ui.txt'
    target = service.staged/'_internal/frontend/ui.txt'
    assert not source.samefile(target)
    before = source.read_bytes()
    target.write_bytes(b'isolated target changed')
    assert source.read_bytes() == before
    with pytest.raises(SecurityError):
        service.cache.verify_tree(service.staged, service.release)


def test_untrusted_unchanged_source_stream_hash_and_signed_fallback(feed):
    source = feed.app/'MentorFlow.exe'
    source.write_bytes(b'X'*len(feed.old_files['MentorFlow.exe']))
    service = feed.service(); service.check()
    assert service.download()['state'] == 'READY_TO_APPLY'
    assert (service.staged/'MentorFlow.exe').read_bytes() == feed.old_files['MentorFlow.exe']
    assert source.read_bytes() == b'X'*len(feed.old_files['MentorFlow.exe'])


def test_final_full_hash_catches_post_stream_copy_tamper(feed, monkeypatch):
    service = feed.service(); service.check()
    original = Path.rename
    def tamper_after_rename(path, destination):
        result = original(path, destination)
        if Path(destination).name == 'MentorFlow.exe':
            Path(destination).write_bytes(b'X'*len(feed.old_files['MentorFlow.exe']))
        return result
    monkeypatch.setattr(Path, 'rename', tamper_after_rename)
    assert service.download()['state'] == 'FAILED_SECURITY'
    assert service.staged is None


@pytest.mark.parametrize('field,value', [('app_id','OtherApp'),('channel','beta'),('version','0.9.0'),
    ('signature_algorithm','none'),('mandatory',True),('version','999999.0.0')])
def test_signed_wrong_contract_and_fake_high_version_rejected(feed, field, value):
    signed = {**feed.signed,field:value}
    feed.sign(signed)
    with pytest.raises(SecurityError): verify_release(feed.raw,feed.keys,current='1.1.0')


def test_one_byte_metadata_tamper_no_fallback(feed):
    feed.raw = feed.raw.replace(b'Small frontend fix', b'Small frontend fiy')
    service = feed.service()
    assert service.check()['state'] == 'FAILED_SECURITY'
    assert service.plan is None and service.staged is None
    assert all(path == '/stable.json' for path,_ in feed.requests)


def test_corrupt_asset_never_ready_or_apply(feed):
    service=feed.service(); service.check(); feed.corrupt=True
    assert service.download()['state']=='FAILED_SECURITY'
    assert service.staged is None


def test_full_fallback_without_delta_base(feed):
    feed.signed['base_versions']=[]; feed.sign(feed.signed)
    service=feed.service(); assert service.check()['update_path']=='FULL_FALLBACK'
    result=service.download()
    assert result['state']=='READY_TO_APPLY'
    assert result['actual_downloaded_bytes']==result['full_bytes']
    assert not any('Setup' in path for path,_ in feed.requests)


def test_low_disk_refuses_before_download(feed):
    service=feed.service(); service.check()
    before=len(feed.requests)
    with pytest.raises(RecoverableError,match='space'):
        service.cache.disk_preflight(service.release,service.plan,free=0)
    assert len(feed.requests)==before


def test_concurrent_updater_lock(feed):
    service=feed.service()
    with InstallUpdateLock(service.cache.root):
        with pytest.raises(RecoverableError):
            with InstallUpdateLock(service.cache.root): pass
    with InstallUpdateLock(service.cache.root): pass


@pytest.mark.parametrize('no_range',[False,True])
def test_cancel_resume_preserves_completed_objects(feed,no_range):
    content=b'new-large-front-end\0'*(128*1024)
    feed.publish('1.1.1',{**feed.old_files,'_internal/frontend/ui.txt':content})
    service=feed.service(); service.check(); feed.no_range=no_range; feed.delay=.003
    row=service.release.assets['_internal/frontend/ui.txt']
    # Pre-complete a distinct target object; cancellation must not redownload it.
    manifest_asset=service.release.assets[MANIFEST_PATH]
    service.cache.download(manifest_asset)
    counter={'bytes':0}
    def progress(n): counter['bytes']+=n
    with pytest.raises(DownloadCancelled):
        service.cache.download(row,cancel=lambda:counter['bytes']>=131072,progress=progress)
    partial=service.cache.objects/(row['sha256']+'.part')
    assert 0<partial.stat().st_size<len(content)
    # Let the bounded owned transport worker close, not a server polling loop.
    deadline=time.monotonic()+2
    while not service.cache.transport._active.acquire(blocking=False):
        assert time.monotonic()<deadline; time.sleep(.01)
    service.cache.transport._active.release()
    completed=service.cache.download(row)
    assert completed.read_bytes()==content
    before=len(feed.requests); service.cache.download(manifest_asset)
    assert len(feed.requests)==before
    assert any(r and r.startswith('bytes=') for p,r in feed.requests if p==url_path(row['url']))


def url_path(url):
    from urllib.parse import urlsplit
    return urlsplit(url).path


def test_semver_precedence_and_invalid_identifiers():
    versions=['1.0.0-alpha','1.0.0-alpha.1','1.0.0-alpha.beta','1.0.0-beta','1.0.0-beta.2','1.0.0-beta.11','1.0.0-rc.1','1.0.0']
    assert sorted(reversed(versions),key=version_key)==versions
    assert version_key('1.0.0+build.1')==version_key('1.0.0+build.2')
    for value in ('01.0.0','1.0','1.0.0-01','garbage'):
        with pytest.raises(SecurityError): version_key(value)


@pytest.mark.parametrize('path',['../escape','C:/escape','_internal/CON.txt','data/plans.db','_internal/a:stream','/root','_internal/x.'])
def test_path_contract_refuses_unsafe_assets(path):
    from refactor.desktop.delivery.update_protocol import safe_path
    with pytest.raises(SecurityError): safe_path(path)


def test_offline_check_is_bounded_and_old_files_unchanged(feed):
    service=feed.service(); service.metadata_url=feed.origin+'/missing-feed.json'
    started=time.monotonic()
    assert service.check()['state']=='FAILED_RECOVERABLE'
    assert time.monotonic()-started<3
    assert (feed.app/'MentorFlow.exe').read_bytes()==feed.old_files['MentorFlow.exe']


def test_background_throttle_manual_override(feed):
    service=feed.service(); service.check()
    before=len(feed.requests)
    service.check(manual=False); assert len(feed.requests)==before
    service.check(manual=True); assert len(feed.requests)==before+1


def test_no_production_apply_bridge_is_fail_closed(feed):
    service=feed.service(); service.check(); service.download()
    with pytest.raises(RecoverableError,match='not qualified'):
        service.apply_and_restart()


def test_https_and_origin_not_bypassable():
    with pytest.raises(SecurityError): HTTPTransport('http://example.com')
    transport=HTTPTransport('https://updates.example.com')
    for url in ('http://updates.example.com','https://other.example.com/file','https://user:secret@updates.example.com/file'):
        with pytest.raises(SecurityError): transport.validate_url(url)


def test_expired_metadata_refused(feed):
    now=datetime.now(timezone.utc)
    feed.signed.update(issued_at=(now-timedelta(days=3)).isoformat(),
        published_at=(now-timedelta(days=3)).isoformat(), expires_at=(now-timedelta(days=1)).isoformat())
    feed.sign(feed.signed)
    assert feed.service().check()['state']=='FAILED_SECURITY'


def test_metadata_replay_refused_across_service_restart(feed):
    feed.signed['metadata_version']=2; feed.sign(feed.signed)
    service=feed.service(); assert service.check()['state']=='UPDATE_AVAILABLE'
    feed.signed['metadata_version']=1; feed.sign(feed.signed)
    restarted=feed.service()
    assert restarted.check()['state']=='FAILED_SECURITY'
    assert restarted._witness()[0]==2


def test_same_metadata_version_equivocation_refused(feed):
    service=feed.service(); service.check()
    feed.signed['release_notes']='different signed payload, same epoch'; feed.sign(feed.signed)
    assert service.check()['state']=='FAILED_SECURITY'


def test_unknown_key_and_provisioned_rotation(feed):
    new_key=Ed25519PrivateKey.generate()
    feed.signed['key_id']='rotation-2'
    feed.raw=canonical(dict(signed=feed.signed,key_id='rotation-2',signature=base64.b64encode(new_key.sign(canonical(feed.signed))).decode()))
    with pytest.raises(SecurityError): verify_release(feed.raw,feed.keys,current='1.1.0')
    keys={**feed.keys,'rotation-2':new_key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)}
    assert verify_release(feed.raw,keys,current='1.1.0').version=='1.1.1'


def test_deterministic_rollout_and_withdrawal(feed):
    from refactor.desktop.delivery.update_protocol import rollout_eligible
    feed.signed['rollout_percentage']=30; feed.sign(feed.signed)
    release=verify_release(feed.raw,feed.keys,current='1.1.0')
    first=[rollout_eligible(release,str(i)) for i in range(1000)]
    assert first==[rollout_eligible(release,str(i)) for i in range(1000)]
    assert 200<sum(first)<400
    feed.signed['withdrawn']=True; feed.sign(feed.signed)
    service=feed.service(); assert service.check()['update'] is False
    assert service.download()['state']=='UP_TO_DATE'


def test_unfinished_reconstruction_copy_can_restart(feed):
    service=feed.service(); service.check()
    source=service.release.rows['_internal/runtime.dll']
    copying=service.cache.root/'copying'; copying.mkdir()
    (copying/(source['sha256']+'.part')).write_bytes(b'interrupted partial copy')
    assert service.download()['state']=='READY_TO_APPLY'






def test_real_disconnect_resume_preserves_verified_object(feed):
    payload=b'disconnect-object\0'*65536
    feed.publish('1.1.1',{**feed.old_files,'_internal/frontend/ui.txt':payload})
    service=feed.service(); service.check()
    manifest_asset=service.release.assets[MANIFEST_PATH]
    service.cache.download(manifest_asset)
    asset=service.release.assets['_internal/frontend/ui.txt']
    feed.disconnect_once=True
    with pytest.raises(RecoverableError): service.cache.download(asset)
    # Small abrupt responses may be buffered by HTTPX; regardless, only the
    # interrupted object may restart, the completed manifest must be reused.
    before=sum(1 for path,_ in feed.requests if path==url_path(manifest_asset['url']))
    service.cache.download(asset); service.cache.download(manifest_asset)
    assert before==sum(1 for path,_ in feed.requests if path==url_path(manifest_asset['url']))
    assert service.cache.complete(asset).read_bytes()==payload


def test_withdraw_after_download_prevents_apply(feed):
    service=feed.service(); service.check(); service.download()
    calls=[]; service.apply_bridge=lambda *args:calls.append(args)
    feed.signed['metadata_version']=2; feed.signed['withdrawn']=True; feed.sign(feed.signed)
    with pytest.raises(SecurityError): service.apply_and_restart()
    assert calls==[] and service._witness()[0]==2


def test_unchanged_deleted_and_new_file_plan(feed):
    files={p:d for p,d in feed.old_files.items() if p!='_internal/runtime.dll'}
    files['_internal/new.txt']=b'new file'
    feed.publish('1.1.1',files)
    service=feed.service(); plan=service.check()
    assert '_internal/runtime.dll' in plan['deleted']
    assert service.download()['state']=='READY_TO_APPLY'
    assert not (service.staged/'_internal/runtime.dll').exists()
    assert (feed.app/'_internal/runtime.dll').exists()
    assert (service.staged/'_internal/new.txt').read_bytes()==b'new file'


def test_corrupt_old_file_only_refetches_that_asset(feed):
    service=feed.service(); service.check()
    (feed.app/'_internal/runtime.dll').write_bytes(b'corrupted old binary fixture')
    result=service.download()
    assert result['state']=='READY_TO_APPLY'
    assert (service.staged/'_internal/runtime.dll').read_bytes()==feed.old_files['_internal/runtime.dll']
    assert (feed.app/'_internal/runtime.dll').read_bytes()==b'corrupted old binary fixture'




def test_uneconomic_incremental_selects_full(feed):
    release=verify_release(feed.raw,feed.keys,current='1.1.0')
    obsolete={path:{**row,'sha256':'0'*64} for path,row in release.rows.items()}
    plan=make_plan(release,obsolete,'1.1.0')
    assert plan['update_path']=='FULL_FALLBACK'
    assert plan['expected_download_bytes']==plan['full_bytes']


def test_missing_incremental_object_selects_full(feed):
    feed.signed['incremental_assets']=[]; feed.sign(feed.signed)
    assert feed.service().check()['update_path']=='FULL_FALLBACK'


def test_beta_advanced_channel_and_stable_prerelease_refusal(feed):
    feed.publish('1.1.1-beta.1',{**feed.old_files,'_internal/frontend/ui.txt':b'beta'})
    feed.signed['channel']='beta'; feed.sign(feed.signed)
    assert verify_release(feed.raw,feed.keys,current='1.1.0',channel='beta').version=='1.1.1-beta.1'
    with pytest.raises(SecurityError): verify_release(feed.raw,feed.keys,current='1.1.0',channel='stable')
    feed.signed['channel']='stable'; feed.sign(feed.signed)
    with pytest.raises(SecurityError): verify_release(feed.raw,feed.keys,current='1.1.0',channel='stable')


def test_invalid_cache_alias_or_overlap_rejected(feed):
    with pytest.raises(SecurityError):
        MatureUpdateService(install_root=feed.install,cache_root=feed.app/'cache',
            metadata_url=feed.origin+'/stable.json',public_keys=feed.keys,current='1.1.0',
            transport=HTTPTransport(feed.origin,fixture_http=True))

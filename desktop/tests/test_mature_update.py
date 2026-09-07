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

from desktop.update_protocol import (canonical, digest, verify_release, version_key,
    make_plan, SecurityError, RecoverableError, DownloadCancelled, MANIFEST_PATH)
from desktop.update_download import HTTPTransport, ObjectCache, InstallUpdateLock
from desktop.update_service import MatureUpdateService


class Feed:
    def __init__(self, root):
        self.root = root
        self.key = Ed25519PrivateKey.generate()  # ephemeral TEST key, never persisted
        self.keys = {'fixture': self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}
        self.objects, self.requests = {}, []
        self.raw = b''
        self.no_range = self.corrupt = False
        self.delay = 0
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
            published_at=now.isoformat(),expires_at=(now+timedelta(days=7)).isoformat(),
            min_supported_version='1.0.0',min_updater_version='1.0.0',mandatory=False,mandatory_reason='',
            release_notes='Small frontend fix',full_release=dict(format='file-set-v1',size=sum(len(d) for d in files.values()),assets=assets),
            incremental_assets=[a['path'] for a in assets],file_manifest=manifest,hash_algorithm='sha256',
            signature_algorithm='ed25519',source_sha='a'*40,tree_sha='b'*40,base_versions=['1.1.0'])
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
    from desktop.update_protocol import safe_path
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

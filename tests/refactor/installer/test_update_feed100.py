"""Signed release transport: domain/IP, gzip and unsigned hints; no public requests."""
import gzip
import json
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from refactor.desktop.delivery import update_download as io
from refactor.desktop.delivery.update_protocol import canonical, digest, release_pointer, pointer_matches, SecurityError
from refactor.desktop.delivery.update_service import MatureUpdateService
from test_server_installer90 import server90


@pytest.fixture
def feed100(server90, monkeypatch):
    f = server90
    raw = f.stable.read_bytes()
    state = SimpleNamespace(raw=raw, pointer=dict(version='2.0.3', metadata_version=1,
        sha256=digest(raw), size=len(raw)), requests=[], fault=None, objects={}, compressed=True)
    client_type = httpx.Client

    def handler(request):
        state.requests.append(request)
        primary = request.url.host == 'aiteacher.clannad.win'
        if primary and hasattr(state, 'block_primary'):
            state.block_primary.wait(15)
        if primary and state.fault in ('network', 'certificate'):
            raise httpx.ConnectError('CERTIFICATE_VERIFY_FAILED' if state.fault == 'certificate' else 'offline', request=request)
        if request.url.path.endswith('latest.json'):
            if state.fault == 'missing_pointer':
                return httpx.Response(404, stream=httpx.ByteStream(b'missing'))
            content = canonical(state.pointer)
        elif request.url.path.endswith('stable.json'):
            content = b'not a signed manifest' if primary and state.fault == 'bad_signature' else state.raw
        else:
            content = state.objects[request.url.path.rsplit('/', 1)[-1]]
            if primary and state.fault == 'bad_object':
                content = b'x' * len(content)
            offset = int(request.headers.get('Range', 'bytes=0-')[6:-1])
            headers = {'Content-Range':f'bytes {offset}-{len(content)-1}/{len(content)}'} if offset else {}
            return httpx.Response(206 if offset else 200, headers=headers, stream=httpx.ByteStream(content[offset:]))
        headers = {}
        if state.compressed:
            headers['Content-Encoding'] = 'gzip'
            content = gzip.compress(content)
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(content))

    def client(**kwargs):
        kwargs.pop('verify')  # Only TLS wire transport is replaced, not signature verification.
        return client_type(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(io.httpx, 'Client', client)
    state.transport = io.HTTPTransport(io.PRIMARY_UPDATE_ROOT + 'stable/stable.json')
    state.service = MatureUpdateService(install_root=f.app.parent/'install', cache_root=f.app.parent/'cache',
        metadata_url=io.PRIMARY_UPDATE_ROOT+'stable/stable.json', public_keys={'fixture90':f.private.public_key().public_bytes_raw()},
        current='2.0.3', transport=state.transport)
    state.fixture = f
    return state


def test_feed100_domain_gzip_signed_then_unchanged_hint_skips_manifest(feed100):
    s = feed100
    assert s.service.check()['state'] == 'UP_TO_DATE'
    assert s.service.release.envelope == s.raw
    assert [r.url.path.rsplit('/',1)[-1] for r in s.requests] == ['latest.json','stable.json']
    assert all(r.url.host == 'aiteacher.clannad.win' and r.headers['Accept-Encoding']=='gzip' for r in s.requests)
    s.requests.clear()
    assert s.service.check()['pointer_status'] == 'unchanged_verified_cache'
    assert len(s.requests) == 1 and s.requests[0].url.path.endswith('latest.json')
    assert s.transport.bytes_received < len(s.raw)*2


@pytest.mark.parametrize('fault', ['network', 'certificate', 'bad_signature'])
def test_feed100_domain_failure_uses_ip_without_relaxing_verification(feed100, fault):
    s=feed100; s.fault=fault
    assert s.service.check()['state']=='UP_TO_DATE'
    manifests=[r for r in s.requests if r.url.path.endswith('stable.json')]
    assert [r.url.host for r in manifests] == ['aiteacher.clannad.win','89.45.45.28']
    assert s.service.release.envelope == s.raw
    assert s.transport.last_metadata_source.startswith(io.FALLBACK_UPDATE_ROOT)


def test_feed100_pointer_change_fetches_manifest_and_mismatch_uses_signed(feed100, caplog):
    s=feed100
    s.service.check(); s.requests.clear()
    s.pointer.update(version='99.0.0', metadata_version=99, sha256='0'*64)
    result=s.service.check()
    assert result['state']=='UP_TO_DATE' and result['latest']=='2.0.3'
    assert result['pointer_mismatch'] and result['pointer_status']=='mismatch_signed_used'
    assert any(r.url.path.endswith('stable.json') for r in s.requests)
    assert 'pointer mismatch' in caplog.text


def test_feed100_new_signed_version_not_hidden_by_cached_hint(feed100):
    s=feed100
    s.service.current='2.0.2'
    s.service.check(); s.requests.clear()
    result=s.service.check()
    assert result['state']=='UPDATE_AVAILABLE' and result['mandatory']
    assert len(s.requests)==1  # Reuses signed policy; never calls old app latest.


def test_feed100_missing_pointer_falls_back_to_full_manifest(feed100):
    s=feed100; s.fault='missing_pointer'
    result=s.service.check()
    assert result['state']=='UP_TO_DATE' and result['pointer_status']=='unavailable_full_manifest'
    assert sum(r.url.path.endswith('latest.json') for r in s.requests)==2
    assert sum(r.url.path.endswith('stable.json') for r in s.requests)==1


def test_feed100_tampered_cache_cannot_authorize_pointer_shortcut(feed100):
    s=feed100
    s.service.check(); s.requests.clear()
    original=s.service.keys.copy()
    s.service.keys={'fixture90':b'x'*32}
    assert s.service.check()['state']=='FAILED_SECURITY'
    s.service.keys=original


def test_feed100_expired_cached_manifest_forces_full_verification(feed100):
    s=feed100; s.service.check(); s.requests.clear()
    def signed(body):
        return canonical(dict(signed=body, key_id='fixture90',
            signature=base64.b64encode(s.fixture.private.sign(canonical(body))).decode()))
    body=json.loads(s.raw)['signed']
    old=dict(body)
    now=datetime.now(timezone.utc)
    old.update(issued_at=(now-timedelta(days=3)).isoformat(),
               published_at=(now-timedelta(days=3)).isoformat(), expires_at=(now-timedelta(days=1)).isoformat())
    expired=signed(old)
    (s.service.cache.root/'highest-metadata-stable.json').write_bytes(expired)
    s.pointer.update(sha256=digest(expired),size=len(expired))
    body['metadata_version']=2;s.raw=signed(body)
    result=s.service.check()
    assert result['state']=='UP_TO_DATE' and s.service.release.signed['metadata_version']==2
    assert any(r.url.path.endswith('stable.json') for r in s.requests)


@pytest.mark.parametrize('fault', [None, 'network', 'bad_object'])
def test_feed100_objects_remain_identity_and_verified_with_fallback(feed100, fault):
    s=feed100; s.fault=fault
    data=b'verified update file'*50
    hashed=digest(data);s.objects[hashed]=data
    asset=dict(url=io.FALLBACK_UPDATE_ROOT+'objects/'+hashed,sha256=hashed,size=len(data))
    result=s.service.cache.download(asset)
    assert result.read_bytes()==data
    assert all(r.headers['Accept-Encoding']=='identity' for r in s.requests)
    assert s.requests[0].url.host=='aiteacher.clannad.win'
    if fault:
        assert s.requests[-1].url.host=='89.45.45.28'


def test_feed100_object_resume_range_across_source_failure(feed100):
    s=feed100; s.fault='network'
    data=b'resume the same signed bytes';hashed=digest(data);s.objects[hashed]=data
    (s.service.cache.objects/(hashed+'.part')).write_bytes(data[:8])
    asset=dict(url=io.PRIMARY_UPDATE_ROOT+'objects/'+hashed,sha256=hashed,size=len(data))
    assert s.service.cache.download(asset).read_bytes()==data
    assert all(r.headers['Range']=='bytes=8-' for r in s.requests)


@pytest.mark.parametrize('bad', [b'{}', b'[]', b'not json', b'x'*2049])
def test_feed100_invalid_pointer_is_not_metadata(bad):
    with pytest.raises(SecurityError): release_pointer(bad)


def test_feed100_non_mirror_origin_stays_strict():
    t=io.HTTPTransport('https://other.example/stable.json')
    with pytest.raises(SecurityError):t.source_urls(io.PRIMARY_UPDATE_ROOT+'objects/a')
    t=io.HTTPTransport(io.PRIMARY_UPDATE_ROOT+'stable/stable.json')
    with pytest.raises(SecurityError):t.source_urls('https://89.45.45.28/not-updates/a')


def test_feed100_changed_valid_pointer_downloads_new_signed_version(feed100):
    from test_publish_optional_installer99 import stage_version
    from scripts.release.transport import LocalServer
    s=feed100;s.service.check();s.requests.clear()
    f=s.fixture;f.server=LocalServer(f.app.parent);f.body=json.loads(s.raw)['signed']
    s.raw=stage_version(f,'2.0.4',2)
    s.pointer.update(version='2.0.4',metadata_version=2,sha256=digest(s.raw),size=len(s.raw))
    result=s.service.check()
    assert result['state']=='UPDATE_AVAILABLE' and result['latest']=='2.0.4'
    assert result['pointer_status']=='matched_manifest' and not result['pointer_mismatch']
    assert [r.url.path.rsplit('/',1)[-1] for r in s.requests]==['latest.json','stable.json']


def test_feed100_gzip_limit_is_on_decoded_bytes(feed100):
    s=feed100
    with pytest.raises(SecurityError,match='size limit'):
        s.transport.fetch_metadata(io.PRIMARY_UPDATE_ROOT+'stable/stable.json',len(s.raw)-1)


def test_feed100_system_domain_trust_and_bundled_ip_ca(feed100, monkeypatch):
    import ssl
    from refactor.desktop.shell_paths import ACCOUNT_API_TRUST
    calls=[];loaded=[]
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(load_verify_locations=lambda **kw:loaded.append(kw))
    monkeypatch.setattr(ssl,'create_default_context',create)
    s=feed100;s.fault='certificate'
    assert s.service.check()['state']=='UP_TO_DATE'
    assert {} in calls and any('cafile' in c for c in calls)
    assert loaded==[dict(cafile=str(ACCOUNT_API_TRUST))]


def test_feed100_both_invalid_signatures_cannot_pass(feed100):
    s=feed100;s.raw=b'invalid';s.fault=None
    assert s.service.check()['state']=='FAILED_SECURITY'
    assert s.service.release is None


def test_feed100_stalled_domain_worker_does_not_block_ip(feed100):
    import threading
    s=feed100;s.block_primary=threading.Event()
    s.transport.metadata_total=.08
    done=threading.Event();results=[]
    def run():
        try:results.append(s.service.check())
        finally:done.set()
    worker=threading.Thread(target=run);worker.start()
    try:
        assert done.wait(8), 'IP fallback blocked by the unfinished domain request'
        assert results[0]['state']=='UP_TO_DATE' and s.transport.busy
        assert sum(r.url.host=='aiteacher.clannad.win' for r in s.requests)==1
        assert sum(r.url.host=='89.45.45.28' for r in s.requests)==2
    finally:
        s.block_primary.set();worker.join(timeout=3)


def test_feed100_cancel_before_check_does_not_request_either_mirror(feed100):
    s=feed100
    result=s.service.check(cancel=lambda:True)
    assert result['state']=='FAILED_RECOVERABLE'
    assert not s.requests and s.service.release is None

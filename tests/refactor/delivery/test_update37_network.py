"""Offline loopback and bounded-retry qualification; never a user installation."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import json

import pytest

from refactor.desktop.delivery import update_download as io
from refactor.desktop.delivery.update_protocol import RecoverableError, DownloadCancelled, SecurityError, digest
from refactor.desktop.delivery.update_service import MatureUpdateService


def test_update37_default_timeout_contract():
    t = io.HTTPTransport('https://example.invalid')
    assert (t.connect, t.read, t.total, t.metadata_total) == (15, 30, None, 300)


@pytest.fixture
def server37():
    state = SimpleNamespace(data=b'a' * (32 * 1024 * 40), requests=[], slow=False, disconnect=False)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            offset = int(self.headers.get('Range', 'bytes=0-')[6:-1])
            state.requests.append(offset)
            self.send_response(206 if offset else 200)
            self.send_header('Content-Length', str(len(state.data) - offset))
            if offset: self.send_header('Content-Range', f'bytes {offset}-{len(state.data)-1}/{len(state.data)}')
            self.end_headers()
            try:
                for index, start in enumerate(range(offset, len(state.data), 32768)):
                    if state.slow:
                        time.sleep(5 if index == 4 else .8)  # ~40 KiB/s plus a real 5s stall
                    self.wfile.write(state.data[start:start+32768]); self.wfile.flush()
                    if state.disconnect:
                        state.disconnect = False
                        self.close_connection = True
                        return
            except (ConnectionError, OSError): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    state.url = f'http://127.0.0.1:{server.server_port}/object'
    yield state
    server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize('metadata', [False, True])
def test_update37_real_slow_response_outlives_30s(server37, tmp_path, metadata):
    server37.slow = True
    transport = io.HTTPTransport(server37.url, fixture_http=True)
    start = time.monotonic()
    if metadata:
        result = transport.fetch_metadata(server37.url, 2 * 1024 * 1024)
    else:
        asset = dict(url=server37.url, sha256=digest(server37.data), size=len(server37.data))
        result = io.ObjectCache(tmp_path / 'cache', transport).download(asset).read_bytes()
    seconds = time.monotonic() - start
    assert seconds > 30 and result == server37.data
    assert len(server37.requests) == 1
    (tmp_path/'metrics37.json').write_text(json.dumps(dict(metadata=metadata, seconds=seconds, bytes=len(result), requests=1)), encoding='utf-8')
    print(f'SLOW37 metadata={metadata} seconds={seconds:.3f} bytes={len(result)}')


def test_update37_disconnect_automatically_uses_range(server37, tmp_path):
    server37.disconnect = True
    transport = io.HTTPTransport(server37.url, fixture_http=True)
    asset = dict(url=server37.url, sha256=digest(server37.data), size=len(server37.data))
    result = io.ObjectCache(tmp_path / 'cache', transport).download(asset)
    assert result.read_bytes() == server37.data
    assert len(server37.requests) == 2 and server37.requests[1] > 0


def test_update37_metadata_deadline_and_one_network_worker(server37):
    server37.slow = True
    transport = io.HTTPTransport(server37.url, fixture_http=True, metadata_total=.2)
    started = time.monotonic()
    with pytest.raises(RecoverableError, match='total timeout'):
        transport.fetch_metadata(server37.url, 2 * 1024 * 1024)
    assert time.monotonic() - started < .6
    assert transport.busy
    with pytest.raises(RecoverableError, match='previous network'):
        next(transport.stream(server37.url))


def test_update37_stream_can_be_cancelled_while_waiting(server37):
    server37.slow = True
    transport = io.HTTPTransport(server37.url, fixture_http=True)
    stop = threading.Event()
    stream = transport.stream(server37.url, cancel=stop.is_set)
    assert next(stream)[0] == 'headers'
    stop.set(); started = time.monotonic()
    with pytest.raises(DownloadCancelled): next(stream)
    assert time.monotonic() - started < .2


def test_update37_idle_limit_is_continuous_silence(server37):
    server37.slow = True
    transport = io.HTTPTransport(server37.url, fixture_http=True, read=.2)
    started = time.monotonic()
    with pytest.raises(RecoverableError): list(transport.stream(server37.url))
    assert time.monotonic() - started < .7


def test_update37_five_resumes_keep_partial_and_cancellation(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr(io, 'retry_wait', lambda seconds, cancel: waits.append(seconds))
    class Transport:
        bytes_received = 0
        calls = []
        def stream(self, url, *, offset, cancel):
            self.calls.append(offset)
            yield 'headers', (206 if offset else 200, {'content-range': f'bytes {offset}-99/100'})
            self.bytes_received += 1
            yield 'bytes', b'x'
            raise RecoverableError('network disconnect')
    transport = Transport()
    cache = io.ObjectCache(tmp_path / 'cache', transport)
    asset = dict(url='https://example.invalid/a', sha256=digest(b'x'*100), size=100)
    with pytest.raises(RecoverableError): cache.download(asset)
    assert waits == [2, 4, 8, 16, 30] and transport.calls == list(range(6))
    assert (cache.objects / (asset['sha256'] + '.part')).read_bytes() == b'x'*6
    assert cache.downloaded_bytes([asset]) == 6
    with pytest.raises(DownloadCancelled): cache.download(asset, cancel=lambda: True)
    assert len(transport.calls) == 6


def test_update37_security_failure_is_not_retried(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr(io, 'retry_wait', lambda *args: waits.append(args))
    class Bad:
        def stream(self, *args, **kwargs):
            raise SecurityError('invalid range')
            yield
    with pytest.raises(SecurityError):
        io.ObjectCache(tmp_path/'cache', Bad()).download(dict(url='https://example.invalid/a', sha256='a'*64, size=10))
    assert not waits


@pytest.mark.parametrize('failures, expected_calls', [(2, 3), (4, 4)])
def test_update37_metadata_three_retries(monkeypatch, failures, expected_calls):
    waits = []
    monkeypatch.setattr(io, 'retry_wait', lambda seconds, cancel: waits.append(seconds))
    class Transport:
        calls = 0
        def fetch_metadata(self, *_):
            self.calls += 1
            if self.calls <= failures: raise RecoverableError('offline')
            return b'complete'
    transport = Transport()
    service = object.__new__(MatureUpdateService)
    service.cache = SimpleNamespace(transport=transport)
    service.metadata_url = 'https://example.invalid/stable.json'
    service._closed = False; service.cancel_event = threading.Event()
    if failures == 4:
        with pytest.raises(RecoverableError): service._fetch_metadata()
    else: assert service._fetch_metadata() == b'complete'
    assert transport.calls == expected_calls and waits == [2, 4, 8][:expected_calls-1]


def test_update37_retry_wait_cancellable():
    start = time.monotonic()
    with pytest.raises(DownloadCancelled): io.retry_wait(30, lambda: True)
    assert time.monotonic() - start < .1


def test_update37_install_default_uses_programs():
    base = Path(__file__).resolve().parents[3] / 'refactor/desktop/packaging/inno'
    script = (base/'MentorFlow.iss').read_text(encoding='utf-8')
    policy = (base/'directory-policy.iss').read_text(encoding='utf-8')
    assert r'DefaultDirName={localappdata}\Programs\MentorFlow' in script
    assert r"ExpandConstant('{localappdata}\Programs')" in policy
    assert 'ForceDirectories(' not in policy

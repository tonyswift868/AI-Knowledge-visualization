"""Read-only local source, exact objects and Range support; no App execution."""
import http.client
import threading
import pytest
from desktop.tests.test_mature_update import Feed
from desktop import update_protocol
from scripts.serve_signed_test_feed import make_server


@pytest.fixture
def source(tmp_path, monkeypatch):
    feed = Feed(tmp_path)
    port = feed.server.server_port
    feed.close()
    app_id = 'MentorFlow.UAT.SourceTest'
    monkeypatch.setattr(update_protocol, 'APP_ID', app_id)
    feed.signed['app_id'] = app_id
    raw = feed.sign(feed.signed)
    folder = tmp_path / 'static-feed'
    (folder / 'objects').mkdir(parents=True)
    (folder / 'stable.json').write_bytes(raw)
    (folder / 'private-key.pem').write_bytes(b'must not be served')
    for name, data in feed.objects.items(): (folder / name.lstrip('/')).write_bytes(data)
    server, release = make_server(folder, feed.keys, app_id, port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield folder, port, release, raw
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def request(port, path, method='GET', headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally: conn.close()


def test_exact_envelope_and_asset_range(source):
    folder, port, release, raw = source
    assert request(port, '/stable.json')[2] == raw
    row = release.rows['MentorFlow.exe']
    payload = (folder / 'objects' / row['sha256']).read_bytes()
    path = '/objects/' + row['sha256']
    code, headers, body = request(port, path, headers={'Range': 'bytes=2-'})
    assert code == 206 and body == payload[2:]
    assert headers['Content-Range'] == f'bytes 2-{len(payload)-1}/{len(payload)}'
    assert request(port, path, headers={'Range': 'bytes=0-'})[0] == 206
    assert request(port, path, method='HEAD')[2] == b''


@pytest.mark.parametrize('path', ['/private-key.pem', '/../private-key.pem', '/objects/../private-key.pem',
    '/objects/' + '0' * 64, '/stable.json?secret=1', '/'])
def test_no_directory_browsing_or_unlisted_assets(source, path):
    assert request(source[1], path)[0] == 404


def test_test_source_refuses_production_identity(tmp_path):
    with pytest.raises(update_protocol.SecurityError, match='UAT identity'):
        make_server(tmp_path, {}, 'MentorFlow')

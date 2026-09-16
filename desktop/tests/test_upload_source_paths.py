"""Real source I/O plus HTTP serialization, with no long-path OS opt-in."""
import hashlib
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from backend.app.plain_file_copy import _native_path, open_resource_source
from desktop.api import ApiError, DesktopApi


@pytest.mark.parametrize('case', ['short', 'unicode', 'over260', 'over350', 'extended'])
@pytest.mark.parametrize('method', ['upload_material', 'upload_teaching_attachment', 'transcribe_teaching_voice'])
def test_upload_path_and_fingerprint_reach_http(tmp_path, case, method):
    source = tmp_path / ('课程资料.txt' if case == 'unicode' else 'lesson.txt')
    if case in ('over260', 'over350', 'extended'):
        minimum = 280 if case == 'over260' else 352
        parent = tmp_path
        while len(str(parent / 'lesson.txt')) < minimum:
            parent /= 'a' * 55
        source = parent / 'lesson.txt'
        assert len(str(source)) >= minimum
    native = _native_path(source)
    native.parent.mkdir(parents=True, exist_ok=True)
    payload = b'Synthetic source-only lesson. Never copied to a shorter location.'
    native.write_bytes(payload)
    argument = native if case == 'extended' else source
    with open_resource_source(argument) as (identity, stream):
        assert not str(identity).startswith('\\\\?\\')
        assert hashlib.sha256(stream.read()).hexdigest() == hashlib.sha256(payload).hexdigest()
    reached = []
    def receive(request):
        body = request.read()
        assert payload in body
        assert source.name.encode() in body
        assert str(tmp_path).encode() not in body  # No local locator becomes domain identity.
        assert request.headers['X-MentorFlow-Command-ID']
        reached.append(request.url.path)
        return httpx.Response(200, json={'accepted': True})
    api = DesktopApi('http://127.0.0.1:12345')
    api.client.close()
    api.client = httpx.Client(base_url=api.origin, transport=httpx.MockTransport(receive))
    try:
        assert getattr(api, method)('synthetic-scope', argument) == {'accepted': True}
        assert len(reached) == 1
        assert native.read_bytes() == payload
    finally:
        api.close()


@pytest.mark.parametrize('case', ['relative', 'traversal', 'missing', 'directory'])
def test_invalid_source_never_reaches_http(tmp_path, case):
    source = {'relative': Path('lesson.txt'), 'traversal': tmp_path / '..' / 'lesson.txt',
              'missing': tmp_path / 'missing.txt', 'directory': tmp_path}[case]
    api = DesktopApi('http://127.0.0.1:12345')
    api.request = lambda *a, **kw: pytest.fail('invalid source reached HTTP')
    try:
        with pytest.raises(ApiError) as error:
            api.upload_material('workspace', source)
        assert error.value.code == ('RESOURCE_SOURCE_MISSING' if case == 'missing' else 'RESOURCE_SOURCE_UNSAFE')
    finally:
        api.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction escape')
def test_source_junction_is_rejected(tmp_path):
    outside = tmp_path / 'other-account'; outside.mkdir()
    (outside / 'lesson.txt').write_bytes(b'foreign account')
    alias = tmp_path / 'alias'
    result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(alias), str(outside)], capture_output=True)
    assert result.returncode == 0
    api = DesktopApi('http://127.0.0.1:12345')
    api.request = lambda *a, **kw: pytest.fail('junction reached HTTP')
    try:
        with pytest.raises(ApiError, match='路径不安全'):
            api.upload_material('workspace', alias / 'lesson.txt')
        assert (outside / 'lesson.txt').read_bytes() == b'foreign account'
    finally:
        api.close(); alias.rmdir()

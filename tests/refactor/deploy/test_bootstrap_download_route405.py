from pathlib import Path
import runpy

import pytest

ROOT = Path(__file__).resolve().parents[3]
prepare = runpy.run_path(str(ROOT / 'refactor/server-java/deploy/prepare_bootstrap_download_route.py'))['prepare']

FROM = 'https://aiteacher.clannad.win/updates/app/releases/bootstrap/MentorFlow-Setup.exe'
TO = 'https://github.com/tonyswift868/AI-Knowledge-visualization/releases/latest/download/MentorFlow-Setup.exe'


def test_route405_changes_only_exact_download_redirect(tmp_path):
    source = tmp_path / 'nginx.conf'
    output = tmp_path / 'candidate.conf'
    source.write_text('server {\n  location = /download {\n    return 302 ' + FROM + ';\n  }\n'
                      '  location /api { proxy_pass http://app; }\n}\n', encoding='utf-8')
    before = source.read_bytes()
    prepare(source, output, FROM, TO)
    candidate = output.read_text(encoding='utf-8')
    assert source.read_bytes() == before
    assert 'return 302 ' + TO + ';' in candidate
    assert 'location /api { proxy_pass http://app; }' in candidate


@pytest.mark.parametrize('block', [
    'location = /download { proxy_pass http://backend; }',
    'location = /download { return 302 ' + FROM + '-wrong; }',
    'location = /download { return 302 /releases/latest/MentorFlow-Setup.exe; }',
    'location /download { return 302 ' + FROM + '; }',
])
def test_route405_refuses_unrecognized_or_proxied_config(tmp_path, block):
    source = tmp_path / 'nginx.conf'
    source.write_text('server {\n  ' + block + '\n}\n', encoding='utf-8')
    with pytest.raises(ValueError):
        prepare(source, tmp_path / 'candidate.conf', FROM, TO)


def test_route405_never_overwrites_source(tmp_path):
    source = tmp_path / 'nginx.conf'
    source.write_text('server {}', encoding='utf-8')
    with pytest.raises(ValueError, match='overwrite'):
        prepare(source, source, FROM, TO)


def test_route405_requires_exact_from_and_replaces_whole_target(tmp_path):
    source, output = tmp_path / 'nginx.conf', tmp_path / 'candidate.conf'
    source.write_text('location = /download { return 302 ' + FROM + '; }\n', encoding='utf8')
    prepare(source, output, FROM, TO)
    assert output.read_text(encoding='utf8') == (
        'location = /download { return 302 ' + TO + '; }\n')


def test_route405_refuses_from_mismatch(tmp_path):
    source, output = tmp_path / 'nginx.conf', tmp_path / 'candidate.conf'
    source.write_text('location = /download { return 302 ' + FROM + '; }\n', encoding='utf8')
    with pytest.raises(ValueError, match='--from'):
        prepare(source, output, 'https://example.invalid/other.exe', TO)
    assert not output.exists()

from pathlib import Path
import runpy

import pytest

ROOT = Path(__file__).resolve().parents[3]
prepare = runpy.run_path(str(ROOT / 'refactor/server-java/deploy/prepare_bootstrap_download_route.py'))['prepare']


def test_route405_changes_only_exact_download_redirect(tmp_path):
    source = tmp_path / 'nginx.conf'
    output = tmp_path / 'candidate.conf'
    source.write_text('server {\n  location = /download {\n    return 302 /releases/latest/MentorFlow-Setup.exe;\n  }\n'
                      '  location /api { proxy_pass http://app; }\n}\n', encoding='utf-8')
    before = source.read_bytes()
    prepare(source, output)
    candidate = output.read_text(encoding='utf-8')
    assert source.read_bytes() == before
    assert 'return 302 https://aiteacher.clannad.win/updates/app/releases/bootstrap/MentorFlow-Setup.exe;' in candidate
    assert 'location /api { proxy_pass http://app; }' in candidate


@pytest.mark.parametrize('block', [
    'location = /download { proxy_pass http://backend; }',
    'location = /download { return 302 /not-the-known-old-route; }',
    'location /download { return 302 /releases/latest/MentorFlow-Setup.exe; }',
])
def test_route405_refuses_unrecognized_or_proxied_config(tmp_path, block):
    source = tmp_path / 'nginx.conf'
    source.write_text('server {\n  ' + block + '\n}\n', encoding='utf-8')
    with pytest.raises(ValueError):
        prepare(source, tmp_path / 'candidate.conf')


def test_route405_never_overwrites_source(tmp_path):
    source = tmp_path / 'nginx.conf'
    source.write_text('server {}', encoding='utf-8')
    with pytest.raises(ValueError, match='overwrite'):
        prepare(source, source)


@pytest.mark.parametrize('old', [
    'https://aiteacher.clannad.win/updates/app/releases/latest/MentorFlow-Setup.exe',
    'https://89.45.45.28/mentorflow/updates/app/releases/latest/MentorFlow-Setup.exe',
    '/updates/app/releases/latest/MentorFlow-Setup.exe',
])
def test_route405_replaces_whole_absolute_or_prefixed_redirect(tmp_path, old):
    source, output = tmp_path / 'nginx.conf', tmp_path / 'candidate.conf'
    source.write_text('location = /download { return 302 ' + old + '; }\n', encoding='utf8')
    prepare(source, output)
    assert output.read_text(encoding='utf8') == (
        'location = /download { return 302 https://aiteacher.clannad.win/updates/app/releases/bootstrap/MentorFlow-Setup.exe; }\n')

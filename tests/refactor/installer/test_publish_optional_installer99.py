"""Daily publication and later installer compilation, using real local server ops."""
import copy
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from refactor.desktop.delivery.update_protocol import canonical, digest
from scripts.release import pipeline
from test_server_installer90 import server90
from test_release_pipeline91 import release91, envelope, fake_compile


def stage_version(f, version, metadata):
    body = copy.deepcopy(f.body)
    body.update(version=version, metadata_version=metadata)
    body['file_manifest']['version'] = version
    content = canonical(body['file_manifest'])
    (f.objects / digest(content)).write_bytes(content)
    for row in body['full_release']['assets']:
        if row['path'] == 'SELF-CHECK/package-manifest.json':
            row.update(size=len(content), sha256=digest(content))
    body['full_release']['size'] = sum(r['size'] for r in body['full_release']['assets'])
    path = f.app.parent / ('signed-' + version + '.json')
    path.write_bytes(envelope(f, body))
    pipeline.stage_manifest(f.server, path, f.key)
    return path.read_bytes()


def test_publish99_false_true_then_supplemental_installer(release91, monkeypatch):
    f = release91
    prior = f.app / 'releases/2.0.2'
    prior.mkdir(parents=True)
    (prior / 'stable.json').write_bytes(f.old)
    old_exe = prior / 'MentorFlow-Setup-2.0.2.exe'
    old_exe.write_bytes(b'MZ previous installer')
    latest = f.app / 'releases/latest/MentorFlow-Setup.exe'
    latest.parent.mkdir()
    os.link(old_exe, latest)
    original_download = pipeline.download_objects
    monkeypatch.setattr(pipeline, 'download_objects', Mock(side_effect=AssertionError('full download')))
    monkeypatch.setattr(pipeline.builder, 'installer', Mock(side_effect=AssertionError('compiled')))
    raw3 = stage_version(f, '2.0.3', 2)
    pipeline.promote(f.server, '2.0.3', f.app.parent / 'false', public_key=f.key)
    f.server.call('cleanup', '2.0.3')
    assert (f.app / 'releases/2.0.3/stable.json').read_bytes() == raw3
    assert {p.name for p in (f.app / 'releases/2.0.3').iterdir()} == {'stable.json'}
    assert os.path.samefile(latest, old_exe)
    assert not (f.app / '.staging-2.0.3').exists()
    assert not (f.app.parent / 'false/payload').exists()
    assert not (f.app.parent / 'false/objects').exists()

    monkeypatch.setattr(pipeline, 'download_objects', original_download)
    raw4 = stage_version(f, '2.0.4', 3)
    pipeline.promote(f.server, '2.0.4', f.app.parent / 'true', public_key=f.key)
    result = pipeline.installer(f.server, '2.0.4', f.app.parent / 'compile4', Path('ISCC.exe'),
                               public_key=f.key, latest=True, compile_fn=fake_compile)
    f.server.call('cleanup', '2.0.4')
    assert result['publish']['retention']['keep_count'] == 3
    assert os.path.samefile(latest, f.app / 'releases/2.0.4/MentorFlow-Setup-2.0.4.exe')
    assert not (f.app / '.staging-2.0.4').exists()

    # Supplement from archived 2.0.3 metadata, NOT the newer live 2.0.4 feed.
    result = pipeline.installer(f.server, '2.0.3', f.app.parent / 'supplement3', Path('ISCC.exe'),
                               public_key=f.key, latest=True, compile_fn=fake_compile)
    assert result['publish']['latest'] is True
    assert os.path.samefile(latest, f.app / 'releases/2.0.3/MentorFlow-Setup-2.0.3.exe')
    assert f.stable.read_bytes() == raw4
    assert (f.app / 'releases/2.0.3/stable.json').read_bytes() == raw3
    assert next(f.server.state.glob('stable-2.0.2-before-2.0.3-*.json')).read_bytes() == f.old
    print('simulation99: false=2.0.3 [stable.json], latest=2.0.2; '
          'true=2.0.4 [stable.json, exe], latest=2.0.4; '
          'supplement=2.0.3 [stable.json, exe], latest=2.0.3; live stable=2.0.4')


def test_publish99_archive_conflict_preserves_live_and_staging(release91):
    f = release91
    stage_version(f, '2.0.3', 2)
    archive = f.app / 'releases/2.0.3/stable.json'
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b'immutable prior metadata')
    with pytest.raises(ValueError, match='different signed metadata'):
        pipeline.promote(f.server, '2.0.3', f.app.parent / 'promote', public_key=f.key)
    assert f.stable.read_bytes() == f.old
    assert archive.read_bytes() == b'immutable prior metadata'
    assert (f.app / '.staging-2.0.3').exists()


def test_publish99_dispatch_defaults_and_optional_compiler():
    workflow = yaml.load((pipeline.ROOT / '.github/workflows/mf-publish.yml').read_text(encoding='utf-8'),
                         Loader=yaml.BaseLoader)
    option = workflow['on']['workflow_dispatch']['inputs']['build_installer']
    assert option == dict(description='同时编安装程序', type='boolean', default='false')
    steps = workflow['jobs']['publish']['steps']
    env = next(s for s in steps if s.get('uses') == './.github/actions/release-env')
    assert env['with']['installer-tools'] == '${{ inputs.build_installer }}'
    compiler = next(s for s in steps if s.get('uses') == './.github/actions/mf-installer')
    assert compiler['if'] == '${{ inputs.build_installer }}'
    assert compiler['with']['latest'] == 'true'
    assert 'cleanup' in steps[-1]['run'] and 'if' not in steps[-1]
    action = yaml.load((pipeline.ROOT / '.github/actions/release-env/action.yml').read_text(),
                      Loader=yaml.BaseLoader)
    assert action['inputs']['installer-tools']['default'] == 'true'
    for step in action['runs']['steps']:
        if 'setup-node' in step.get('uses', '') or '--requirement' in step.get('run', ''):
            assert step['if'] == "${{ inputs.installer-tools == 'true' }}"
    small = next(s for s in action['runs']['steps'] if 'cryptography==' in s.get('run', ''))
    assert small['if'] == "${{ inputs.installer-tools != 'true' }}"
    assert 'pyinstaller' not in small['run'].lower()

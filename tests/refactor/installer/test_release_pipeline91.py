"""Same filesystem backend as the remote release runner, with ephemeral signing keys."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
import yaml

from refactor.desktop.delivery.update_protocol import canonical, SecurityError
from scripts.release import pipeline, server_ops, transport
from scripts.release import build_release as controller
from test_server_installer90 import server90


def envelope(f, body):
    return canonical(dict(signed=body, key_id='fixture90', signature=base64.b64encode(
        f.private.sign(canonical(body))).decode()))


@pytest.fixture
def release91(server90):
    f = server90
    f.server = transport.LocalServer(f.app.parent)
    new_body = json.loads(f.stable.read_bytes())['signed']
    new_body['metadata_version'] = 2
    f.new = envelope(f, new_body)
    f.body = json.loads(f.new)['signed']
    prior = json.loads(f.new)['signed']
    prior.update(version='2.0.2', metadata_version=1, min_supported_version='2.0.2')
    prior['file_manifest']['version'] = '2.0.2'
    manifest_bytes=canonical(prior['file_manifest'])
    import hashlib
    for row in prior['full_release']['assets']:
        if row['path']=='SELF-CHECK/package-manifest.json':
            row.update(size=len(manifest_bytes),sha256=hashlib.sha256(manifest_bytes).hexdigest())
    prior['full_release']['size']=sum(r['size'] for r in prior['full_release']['assets'])
    f.old = envelope(f, prior)
    f.stable.write_bytes(f.old)
    f.unsigned = f.app.parent / 'unsigned-release.json'
    f.unsigned.write_bytes(canonical(f.body))
    return f


def fake_compile(args):
    args.output.mkdir(parents=True)
    (args.output / 'MentorFlow-Setup.exe').write_bytes(b'MZ installer fixture91')


def test_release91_stage_verify_promote_compile_latest_cleanup(release91, monkeypatch):
    f = release91
    # Remove one live object, leaving the real build copy available for staging.
    row = next(r for r in f.body['full_release']['assets'] if 'lesson.txt' in r['path'])
    build_objects = f.app.parent / 'build-objects';build_objects.mkdir()
    (build_objects / row['sha256']).write_bytes((f.objects / row['sha256']).read_bytes())
    (f.objects / row['sha256']).unlink()
    staged = pipeline.stage_objects(f.server, f.unsigned, build_objects, f.app.parent / 'upload')
    assert staged['files'] == 1 and staged['existing'] == len(f.files)-1
    assert f.stable.read_bytes() == f.old
    signed = f.app.parent / 'signed.json';signed.write_bytes(f.new)
    pipeline.stage_manifest(f.server, signed, f.key)
    pipeline.promote(f.server, '2.0.3', f.app.parent / 'promote', public_key=f.key)
    assert f.stable.read_bytes() == f.new
    assert next(f.server.state.glob('stable-2.0.2-before-2.0.3-*.json')).read_bytes() == f.old
    (f.app / 'installer').mkdir()
    (f.app / 'installer/MentorFlow-Setup.exe').write_bytes(b'old installer')
    result = pipeline.installer(f.server, '2.0.3', f.app.parent / 'compile', Path('ISCC.exe'),
                                public_key=f.key, latest=True, compile_fn=fake_compile)
    assert result['publish']['latest'] is True
    assert (f.app / 'releases/latest').is_dir()
    assert not (f.app / 'releases/latest').is_symlink() and not (f.app / 'releases/latest').is_junction()
    assert (f.app / 'releases/latest/MentorFlow-Setup.exe').read_bytes() == b'MZ installer fixture91'
    assert (f.app / 'releases/2.0.3/stable.json').read_bytes() == f.new
    assert {p.name for p in (f.app/'releases/2.0.3').iterdir()}=={'MentorFlow-Setup-2.0.3.exe','stable.json'}
    assert os.path.samefile(f.app/'releases/latest/MentorFlow-Setup.exe',f.app/'releases/2.0.3/MentorFlow-Setup-2.0.3.exe')
    assert not (f.app / 'installer/MentorFlow-Setup.exe').exists()
    assert next(f.server.state.glob('installer-before-releases-*.exe')).read_bytes() == b'old installer'
    f.server.call('cleanup', '2.0.3')
    assert not (f.app / '.staging-2.0.3').exists()


@pytest.mark.parametrize('fault', ['missing', 'hash', 'size', 'signature', 'changed_manifest'])
def test_release91_failed_promotion_leaves_live_and_latest_untouched(release91, fault, monkeypatch):
    f = release91
    stage = f.app / '.staging-2.0.3/stable/stable.json';stage.parent.mkdir(parents=True)
    stage.write_bytes(f.new)
    row = f.body['full_release']['assets'][0]
    obj = f.objects / row['sha256']
    if fault == 'missing':obj.unlink()
    if fault == 'hash':obj.write_bytes(b'x' * row['size'])
    if fault == 'size':obj.write_bytes(b'x')
    if fault == 'signature':
        corrupt = json.loads(f.new);corrupt['signed']['release_notes'] = 'tampered'
        stage.write_bytes(canonical(corrupt))
    if fault == 'changed_manifest':
        original = f.server.call
        def call(action, version, **values):
            if action == 'promote':stage.write_bytes(f.new + b'\n')
            return original(action, version, **values)
        monkeypatch.setattr(f.server, 'call', call)
    with pytest.raises((ValueError, SecurityError, FileNotFoundError)):
        pipeline.promote(f.server, '2.0.3', f.app.parent / 'promote', public_key=f.key)
    assert f.stable.read_bytes() == f.old
    assert not (f.app / 'releases').exists()
    assert not list(f.server.state.glob('stable-*-before-*.json'))


def test_release91_invalid_signature_never_downloads_or_compiles(release91, monkeypatch):
    f = release91
    f.stable.write_bytes(f.new.replace(b'"test"', b'"tampered"'))
    monkeypatch.setattr(pipeline, 'download_objects', Mock(side_effect=AssertionError('downloaded')))
    with pytest.raises(SecurityError):
        pipeline.installer(f.server, '2.0.3', f.app.parent / 'compile', Path('ISCC.exe'),
                            public_key=f.key, compile_fn=fake_compile)
    assert not (f.app / 'releases').exists()


def test_release91_compiler_failure_does_not_touch_release_or_latest(release91):
    f = release91;f.stable.write_bytes(f.new)
    def fail(args):raise RuntimeError('compiler failed')
    with pytest.raises(RuntimeError, match='compiler failed'):
        pipeline.installer(f.server, '2.0.3', f.app.parent / 'compile', Path('ISCC.exe'),
                            public_key=f.key, latest=True, compile_fn=fail)
    assert not (f.app / 'releases').exists()
    assert f.stable.read_bytes() == f.new


def test_release91_unchanged_objects_not_uploaded(release91):
    f=release91
    stats=pipeline.stage_objects(f.server,f.unsigned,Path('must-not-be-read'),f.app.parent/'upload')
    assert stats['files']==0 and stats['bytes']==0 and stats['existing']==len(f.files)


@pytest.mark.parametrize('fault', [None, 'link', 'hash', 'replace'])
def test_release91_latest_hardlink_switch_is_atomic(release91, monkeypatch, fault):
    f=release91
    latest=f.app/'releases/latest';latest.mkdir(parents=True)
    old=f.app/'releases/old.exe';old.write_bytes(b'previous installer')
    os.link(old,latest/'MentorFlow-Setup.exe')
    source=f.app.parent/'incoming.exe';source.write_bytes(b'new installer')
    import hashlib
    row=dict(size=source.stat().st_size,sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    original_link=server_ops.os.link
    original_checked=server_ops.checked
    original_replace=server_ops.os.replace
    def link(src,dst):
        assert (latest/'MentorFlow-Setup.exe').read_bytes()==b'previous installer'
        if fault=='link':raise OSError('link unavailable')
        original_link(src,dst)
    def checked(path,metadata):
        if path.parent==latest:
            assert os.path.samefile(path,f.app/'releases/2.0.3/MentorFlow-Setup-2.0.3.exe')
            assert (latest/'MentorFlow-Setup.exe').read_bytes()==b'previous installer'
            if fault=='hash':raise ValueError('temporary hardlink hash mismatch')
        return original_checked(path,metadata)
    def replace(src,dst):
        if Path(dst).parent==latest:
            assert (latest/'MentorFlow-Setup.exe').read_bytes()==b'previous installer'
            if fault=='replace':raise OSError('rename failed')
        return original_replace(src,dst)
    monkeypatch.setattr(server_ops.os,'link',link)
    monkeypatch.setattr(server_ops,'checked',checked)
    monkeypatch.setattr(server_ops.os,'replace',replace)
    call=lambda:f.server.call('installer','2.0.3',transfer=str(source),installer=row,
                              manifest=base64.b64encode(f.new).decode(),latest=True)
    if fault:
        with pytest.raises((ValueError,OSError)):call()
        assert os.path.samefile(latest/'MentorFlow-Setup.exe',old)
    else:
        call()
        assert os.path.samefile(latest/'MentorFlow-Setup.exe',f.app/'releases/2.0.3/MentorFlow-Setup-2.0.3.exe')
    assert {p.name for p in latest.iterdir()}=={'MentorFlow-Setup.exe'}
    assert old.read_bytes()==b'previous installer'


def test_release91_build_only_passes_fixed_signed_snapshots_and_does_not_stage(release91,monkeypatch):
    f=release91
    (f.server.state/'stable-2.0.2-before-2.0.3.json').write_bytes(f.old)
    calls=[]
    monkeypatch.setattr(pipeline.subprocess,'run',lambda command,**kw:calls.append(command))
    monkeypatch.setattr(pipeline,'stage_objects',Mock(side_effect=AssertionError('build-only uploaded')))
    args=argparse.Namespace(work=f.app.parent/'build-job',version='2.0.3',public_key=f.key,
        baseline='a'*40,notes='quotes " and spaces are argv',base_versions='2.0.2',
        build_only=True,mandatory=False,mandatory_reason='')
    assert pipeline.build(f.server,args)['status']=='BUILT_ONLY'
    assert len(calls)==1 and '--build-only' in calls[0] and '--keep-work' in calls[0]
    assert '--previous-release' in calls[0] and '--base-release' in calls[0]
    assert args.notes in calls[0]


def test_release91_local_signing_zip_stages_only_signed_manifest(release91):
    import zipfile
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    f=release91
    private=f.app.parent/'test-private.raw'
    private.write_bytes(f.private.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()))
    archive=f.app.parent/'artifact.zip'
    with zipfile.ZipFile(archive,'w') as z:z.write(f.unsigned,'unsigned-release.json')
    command=[sys.executable,'-B',str(pipeline.ROOT/'scripts/release/sign_and_stage.py'),
             '--artifact',str(archive),'--private-key',str(private),'--public-key',str(f.key),
             '--work',str(f.app.parent/'sign'),'--local-server-root',str(f.app.parent)]
    result=subprocess.run(command,capture_output=True,text=True,check=True)
    staged=f.app/'.staging-2.0.3/stable/stable.json'
    assert pipeline.verified_release(staged,f.key).version=='2.0.3'
    assert private.read_bytes().hex() not in result.stdout+result.stderr
    assert not list(f.app.rglob('test-private*'))


def test_release91_dispatch_only_global_queue_timeouts_and_small_artifact():
    paths=sorted((pipeline.ROOT/'.github/workflows').glob('mf-*.yml'))
    assert len(paths)==3
    # BaseLoader preserves the YAML key "on", unlike PyYAML's YAML 1.1 bool conversion.
    for path in paths:
        workflow=yaml.load(path.read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
        assert list(workflow['on'])==['workflow_dispatch']
        assert workflow['concurrency']==dict(group='mentorflow-release',queue='max',**{'cancel-in-progress':'false'})
        for job in workflow['jobs'].values():
            assert job['runs-on']=='windows-latest'
            assert int(job['timeout-minutes'])<=(60 if path.stem=='mf-build' else 40)
            assert 'strategy' not in job
            for step in job['steps']:
                assert 'pytest' not in step.get('run','') and 'npm test' not in step.get('run','')
                if step.get('uses','').startswith('actions/upload-artifact@'):
                    assert step['with']['retention-days']=='3'
                    assert 'payload' not in step['with']['path']
    text=(pipeline.ROOT/'.github/actions/release-env/action.yml').read_text()
    assert "python-version: '3.12.10'" in text and "node-version: '24.13.0'" in text
    assert 'cache: pip' in text and 'cache: npm' in text
    release_env=yaml.load((pipeline.ROOT/'.github/actions/release-env/action.yml').read_text(),Loader=yaml.BaseLoader)
    node=next(step for step in release_env['runs']['steps'] if step.get('uses')=='actions/setup-node@v7')
    assert node['if']=="${{ inputs.installer-tools == 'true' && inputs.node-tools == 'true' }}"


def test_release91_inno_never_installed_on_local_machine(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    with pytest.raises(ValueError,match='runner-only'):
        pipeline.provision_inno(tmp_path)


def test_release91_pinned_public_inputs_match_trusted_package():
    auth,paths=controller.pinned_reference(controller.REFERENCE)
    assert auth['APP_ID']=='MentorFlow' and set(paths)=={'runtime','ca','seven','voices'}
    key_id,key=pipeline.builder.read_key(pipeline.PUBLIC)
    assert auth['PUBLIC_KEYS']=={key_id:key}
    # Git for Windows normally uses autocrlf=true; byte-pinned inputs cannot
    # undergo checkout newline conversion before their SHA256 check.
    attrs=subprocess.check_output(['git','check-attr','text','--',
        *[p.relative_to(pipeline.ROOT).as_posix() for p in paths.values()]],
        cwd=pipeline.ROOT,text=True)
    assert all(line.endswith(': unset') for line in attrs.splitlines())

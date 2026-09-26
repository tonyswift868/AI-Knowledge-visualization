"""Only pure package/configuration checks and dry-run CLIs; never deploy."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'deploy/enterprise/staging/rp05/mf-u09'


@pytest.fixture
def scripts(monkeypatch):
    def load(name):
        spec = importlib.util.spec_from_file_location('mf_u09_' + name, PACKAGE / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    config = load('configure')
    monkeypatch.setitem(sys.modules, 'configure', config)
    return config, load('release'), load('verify')


def baseline(config):
    return {'name': config.PROJECT, 'services': {
        'backend': {'image': config.BASE_IMAGE, 'environment': {
            'MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS': '1', 'WEB_CONCURRENCY': '1'},
            'volumes': [{'type': 'volume', 'source': 'objects', 'target': '/var/lib/mentorflow/object-storage'}]},
        'postgres': {'image': 'existing-postgres', 'volumes': ['preserved']},
        'edge': {'image': 'existing-edge'}}, 'networks': {'internal': {'internal': True}}}


def test_bundle_is_exact_copy_of_six_current_sources(scripts):
    config, _, _ = scripts
    config.check_bundle(PACKAGE)
    manifest = json.loads((PACKAGE / 'manifest.json').read_text(encoding='utf-8'))
    for source in config.SOURCES:
        # The package pins LF so Linux checkout/build bytes match the manifest.
        canonical = (ROOT / source).read_bytes().replace(b'\r\n', b'\n')
        assert (PACKAGE / 'overlay' / source).read_bytes() == canonical
        assert manifest['sha256'][source] == hashlib.sha256(canonical).hexdigest()
    lines = (PACKAGE / 'Dockerfile').read_text().splitlines()
    assert lines[0] == 'FROM ' + config.BASE_IMAGE
    assert len(lines) == 7 and all(line.startswith('COPY --chown=10001:10001 ') for line in lines[1:])


def test_override_only_changes_backend_image_and_catalog_mount(scripts):
    config, _, _ = scripts
    before = baseline(config)
    old = deepcopy(before)
    patched = config.patched_override(before)
    config.validate_config(before, patched, upgraded=True)
    assert before == old
    assert patched['services']['postgres'] == before['services']['postgres']
    assert patched['services']['backend']['volumes'][-1]['read_only'] is True


@pytest.mark.parametrize('case', ['other_image', 'environment', 'mount_writable', 'wrong_source', 'second_service'])
def test_validation_rejects_unrelated_or_unsafe_drift(scripts, case):
    config, _, _ = scripts
    before = baseline(config)
    patched = config.patched_override(before)
    backend = patched['services']['backend']
    if case == 'other_image':
        backend['image'] = 'unexpected'
    elif case == 'environment':
        backend['environment']['WEB_CONCURRENCY'] = '2'
    elif case == 'mount_writable':
        backend['volumes'][-1]['read_only'] = False
    elif case == 'wrong_source':
        backend['volumes'][-1]['source'] = '/wrong'
    else:
        patched['services']['postgres']['image'] = 'different'
    with pytest.raises(ValueError):
        config.validate_config(before, patched, upgraded=True)


def test_override_refuses_nonbaseline_or_existing_catalog(scripts):
    config, _, _ = scripts
    value = baseline(config)
    value['services']['backend']['image'] = 'someone-elses-image'
    with pytest.raises(ValueError):
        config.patched_override(value)
    value = baseline(config)
    value['services']['backend']['volumes'].append('/existing:/run/mentorflow/catalog:rw')
    with pytest.raises(ValueError):
        config.patched_override(value)


def test_release_commands_have_exact_compose_order_only_recreate_backend(scripts):
    config, release, _ = scripts
    base = config.compose('/operator/existing.env')
    assert [base[i+1] for i, part in enumerate(base) if part == '-f'] == list(config.COMPOSE_FILES)
    assert base[base.index('-p')+1] == 'mentorflow-e1-preview'
    for action in ('deploy', 'rollback'):
        commands = release.commands(action, '/operator/existing.env', PACKAGE)
        up = [c for c in commands if 'up' in c]
        assert len(up) == 1
        assert up[0][-1] == 'backend'
        assert all(flag in up[0] for flag in ('--no-deps', '--no-build', '--force-recreate'))
        assert 'never' in up[0]
        assert not any('down' in c or 'alembic' in c or 'ssh' in c for c in commands)
        stop = next(i for i, c in enumerate(commands) if 'stop' in c)
        migration = next(i for i, c in enumerate(commands) if any(str(p).endswith('migration.py') for p in c))
        switch = next(i for i, c in enumerate(commands) if ('patch' if action == 'deploy' else 'rollback') in c)
        assert stop < migration < switch < commands.index(up[0])
        if action == 'deploy': assert 'upgrade' in commands[migration]
        else: assert 'downgrade' in commands[migration]


@pytest.mark.parametrize('action', ['prepare', 'build', 'deploy', 'rollback'])
def test_release_default_never_invokes_subprocess(scripts, monkeypatch, capsys, action):
    _, release, _ = scripts
    monkeypatch.setattr(release.subprocess, 'run', lambda *a, **k: pytest.fail('default executed a command'))
    release.main([action, '--env-file', '/operator/existing.env'])
    assert 'docker' in capsys.readouterr().out


def test_helper_defaults_cannot_write_or_connect(scripts, monkeypatch, capsys):
    config, _, verify = scripts
    monkeypatch.setattr(config, 'operate', lambda *a: pytest.fail('default operated on host'))
    config.main(['patch', '--env-file', '/operator/existing.env'])
    monkeypatch.setattr(verify, 'verify', lambda *a: pytest.fail('default sent HTTP requests'))
    verify.main(['--mode', 'catalogs', '--url', 'https://api.invalid', '--ca-file', '/absent.pem',
                 '--auth-file', '/absent-secret.json'])
    assert 'DRY RUN' in capsys.readouterr().out


@pytest.mark.parametrize('script', ['migration', 'migration_db'])
@pytest.mark.parametrize('action', ['upgrade', 'check', 'downgrade'])
def test_migration_default_does_not_connect_write_or_execute(scripts, monkeypatch, capsys, script, action):
    spec = importlib.util.spec_from_file_location('mf_u09_' + script, PACKAGE / (script + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'operate', lambda *a: pytest.fail('default performed migration'))
    module.main([action] + (['--env-file', '/operator/existing.env'] if script == 'migration' else []))
    assert 'DRY RUN' in capsys.readouterr().out


def test_migration_override_only_changes_one_off_service(scripts):
    spec = importlib.util.spec_from_file_location('mf_u09_migration', PACKAGE / 'migration.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.override(PACKAGE)
    assert set(result['services']) == {'migrate'}
    assert set(result['services']['migrate']) == {'image', 'volumes'}
    assert result['services']['migrate']['volumes'][0]['read_only'] is True
    command = module.command('upgrade', '/operator/existing.env', '/temp/override.json')
    assert '--no-deps' in command and '--rm' in command and '--pull' in command and 'never' in command

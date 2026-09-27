"""No Docker/network execution: audited package and failure/rollback control flow."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'deploy/enterprise/staging/rp05/mf-u10'


@pytest.fixture
def scripts(monkeypatch):
    def load(name):
        spec = importlib.util.spec_from_file_location('mfu10_' + name, PACKAGE / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    config = load('configure')
    monkeypatch.setitem(sys.modules, 'configure', config)
    return SimpleNamespace(config=config, release=load('release'), load=load)


def test_mfu10_overlay_matches_repository_and_every_source_has_provenance(scripts):
    scripts.config.check_bundle(PACKAGE)
    manifest = json.loads((PACKAGE / 'manifest.json').read_text(encoding='utf-8'))
    for path in scripts.config.SOURCES:
        canonical = (ROOT / path).read_bytes().replace(b'\r\n', b'\n')
        assert (PACKAGE / 'overlay' / path).read_bytes() == canonical
        assert manifest['sha256'][path] == hashlib.sha256(canonical).hexdigest()
        assert manifest['originals'][path]['source']
    copies = (PACKAGE / 'Dockerfile').read_text().splitlines()
    assert copies[0] == 'FROM ' + scripts.config.BASE_IMAGE
    assert len(copies) == len(scripts.config.SOURCES) + 1
    assert all('account_object_storage' not in line for line in copies)


def test_import_checks_are_inside_both_images_before_any_backend_stop(scripts):
    release = scripts.release
    build = release.commands('build', '/operator/existing.env', PACKAGE)
    build_index = next(i for i, cmd in enumerate(build) if cmd[:2] == ['docker', 'build'])
    assert build[build_index - 1] == release.image_check('base', PACKAGE)
    assert build[build_index + 1] == release.image_check('candidate', PACKAGE)
    for mode in ('base', 'candidate'):
        cmd = release.image_check(mode, PACKAGE)
        assert '--network=none' in cmd and '--read-only' in cmd and '--tmpfs' in cmd
        assert '/bundle/image_check.py' in cmd and cmd[-1] == '--execute'
    deployment = release.commands('deploy', '/operator/existing.env', PACKAGE)
    check = deployment.index(release.image_check('candidate', PACKAGE))
    stop = next(i for i, cmd in enumerate(deployment) if cmd[-2:] == ['stop', 'backend'])
    migration = next(i for i, cmd in enumerate(deployment) if 'upgrade' in cmd)
    switch = next(i for i, cmd in enumerate(deployment) if 'patch' in cmd)
    assert check < stop < migration < switch
    assert 'app.account_core_mvp' in scripts.load('image_check').MODULES


@pytest.mark.parametrize('action', ['prepare', 'build', 'deploy', 'rollback'])
def test_default_release_never_executes_or_waits(scripts, monkeypatch, capsys, action):
    monkeypatch.setattr(scripts.release, 'operate', lambda *a: pytest.fail('default executed'))
    scripts.release.main([action, '--env-file', '/operator/existing.env'])
    assert 'docker' in capsys.readouterr().out


@pytest.mark.parametrize('name,args', [
    ('configure', ['patch', '--env-file', '/operator/existing.env']),
    ('migration', ['upgrade', '--env-file', '/operator/existing.env']),
    ('migration_db', ['upgrade']), ('image_check', ['candidate']),
    ('verify', ['--mode', 'health', '--url', 'https://example.invalid', '--ca-file', '/absent']),
])
def test_all_helpers_are_inert_by_default(scripts, monkeypatch, name, args):
    module = scripts.load(name)
    for target in ('operate', 'check', 'verify'):
        if hasattr(module, target):
            monkeypatch.setattr(module, target, lambda *a: pytest.fail('default executed'))
    module.main(args)


@pytest.mark.parametrize('phase', ['build', 'deploy'])
def test_import_failure_prevents_switch_and_does_not_stop_baseline(scripts, phase):
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        if '/bundle/image_check.py' in cmd and 'candidate' in cmd:
            raise subprocess.CalledProcessError(1, cmd)
    with pytest.raises(subprocess.CalledProcessError):
        scripts.release.operate(phase, '/operator/existing.env', PACKAGE,
            run=run, wait=lambda *_: pytest.fail('waited after import failure'))
    assert not any('stop' in cmd or 'patch' in cmd or 'downgrade' in cmd for cmd in calls)


@pytest.mark.parametrize('failure', ['unhealthy', 'upgrade', 'up'])
def test_failure_automatically_restores_mfu04_and_waits_for_healthy(scripts, failure):
    calls, waits = [], []
    failed = False
    def run(cmd, **kwargs):
        nonlocal failed
        calls.append(cmd)
        if not failed and failure != 'unhealthy' and failure in cmd:
            failed = True
            raise subprocess.CalledProcessError(1, cmd)
    def wait(_):
        waits.append(True)
        if failure == 'unhealthy' and len(waits) == 1:
            raise TimeoutError('backend unhealthy')
    with pytest.raises(RuntimeError, match='MF-U04 rollback is healthy'):
        scripts.release.operate('deploy', '/operator/existing.env', PACKAGE, run=run, wait=wait)
    downgrade = next(i for i, cmd in enumerate(calls) if 'downgrade' in cmd)
    restore = next(i for i, cmd in enumerate(calls) if 'rollback' in cmd)
    assert downgrade < restore
    assert calls[-1][-1] == 'backend' and '--force-recreate' in calls[-1]
    assert waits


def test_failed_rollback_is_reported_without_false_success(scripts):
    def wait(_):
        raise TimeoutError('unhealthy')
    with pytest.raises(RuntimeError, match='automatic rollback failed'):
        scripts.release.operate('deploy', '/operator/existing.env', PACKAGE, run=lambda *a, **k: None, wait=wait)


@pytest.mark.parametrize('healthy_after', [0, 3, None])
def test_health_wait_is_bounded_and_observes_real_container_health(scripts, healthy_after):
    now, probes = [0], [0]
    def sleep(seconds):
        now[0] += seconds
    def run(cmd, **kwargs):
        assert kwargs['timeout'] <= 5
        if cmd[:2] != ['docker', 'inspect']:
            return SimpleNamespace(stdout='owned-backend\n')
        probes[0] += 1
        health = 'healthy' if healthy_after is not None and probes[0] > healthy_after else 'unhealthy'
        return SimpleNamespace(stdout='running ' + health)
    if healthy_after is None:
        with pytest.raises(TimeoutError):
            scripts.release.wait_healthy('env', run=run, clock=lambda: now[0], sleep=sleep)
        assert now[0] == 90
    else:
        scripts.release.wait_healthy('env', run=run, clock=lambda: now[0], sleep=sleep)
        assert now[0] <= 6


def test_only_last_override_image_and_readonly_catalog_can_change(scripts):
    config = scripts.config
    before = {'services': {'backend': {'image': config.BASE_IMAGE, 'environment': {
        'MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS': '1', 'WEB_CONCURRENCY': '1'}},
        'postgres': {'image': 'untouched'}}}
    original = deepcopy(before)
    patched = config.patched_override(before)
    config.validate_config(before, patched, upgraded=True)
    assert before == original
    assert patched['services']['backend']['volumes'][0]['read_only'] is True
    patched['services']['postgres']['image'] = 'unrelated'
    with pytest.raises(ValueError):
        config.validate_config(before, patched, upgraded=True)
    docker = config.compose('env')
    assert [docker[i + 1] for i, part in enumerate(docker) if part == '-f'] == list(config.COMPOSE_FILES)
    for phase in ('deploy', 'rollback'):
        for cmd in scripts.release.commands(phase, 'env', PACKAGE):
            assert 'down' not in cmd and 'ssh' not in cmd
            if 'up' in cmd:
                assert cmd[-1] == 'backend' and '--no-deps' in cmd

"""Real Git S -> publication T admission, without a product/server launch."""
import ast
import copy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tools.dev_workbench import authority as a, targets as t
from tools.dev_workbench.test_workbench import candidate, raw
from tools.dev_workbench.test_devices import devices
from desktop import test_device_configuration as config


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def commit(root, message):
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost', 'commit', '-qm', message)
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def publication(tmp_path, candidate):
    root = tmp_path / 'repo'; root.mkdir()
    git(root, 'init', '-q', '-b', 'integration/main3')
    for name, content in {
        'desktop/test_device_profile.py': "PROFILE_CONTRACT = 'MENTORFLOW_TEST_DEVICE_V1'",
        'desktop/main.py': '_configure_test_device()',
        'desktop_entry.py': 'from desktop.test_device_configuration import apply_from_environment',
        'runtime.py': '# exact product source S',
    }.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
    build = commit(root, 'Build source S')
    value, paths = candidate
    value.update(source_sha=build, source_tree=git(root, 'show', '-s', '--format=%T', build))
    identity = Path(value['identity_evidence_path'])
    cert = identity.parent / 'ca.crt'; cert.write_bytes(b'fixture TLS certificate')
    runtime = dict(contract=config.CONTRACT, issuer='https://issuer.invalid', api_base_url='https://api.invalid',
                   client_id='desktop', audience='api', organization_id='org', workspace_id='space',
                   tls_ca_path=str(cert), tls_ca_sha256=t.sha256(cert.read_bytes()))
    identity.write_bytes(raw(dict(schema_version=1, **{key:value[key] for key in t.IDENTITY_FIELDS},
        device_profile_contract='MENTORFLOW_TEST_DEVICE_V1', packaged_entrypoint='desktop_entry.py',
        test_device_configuration=runtime)))
    value['identity_evidence_sha256'] = t.sha256(identity.read_bytes())
    from tools.dev_workbench.testing_leases import publish
    publish(value)
    path = root / a.TARGET; path.parent.mkdir(parents=True); path.write_bytes(raw(value))
    publish = commit(root, 'Publish exact S artifact and build/test identities')
    return root, value, paths, build, publish


def test_distinct_source_publication_and_ab_admission(publication, devices):
    root, value, paths, build, publish = publication
    source = a.RepoSource(root)
    assert build != publish
    view = a.load_view(source, paths)
    assert view['active_target'] == value
    assert view['candidate_identity'] == dict(build_source_sha=build, build_source_tree=value['source_tree'],
        governance_publication_sha=publish, artifact_sha256=value['artifact_sha256'], build_id=value['build_id'],
        test_id=value['test_id'], identity_evidence_sha256=value['identity_evidence_sha256'])
    calls = []
    def spawn(argv, **kwargs):
        calls.append((argv, kwargs)); return SimpleNamespace(pid=len(calls), poll=lambda: None)
    devices.launch('A', value, source, paths, spawn=spawn)
    devices.launch('B', value, source, paths, spawn=spawn)
    assert calls[0][0] == calls[1][0] == [value['artifact_path']]
    for key in ('MENTORFLOW_DB', 'MENTORFLOW_ACCOUNT_VAULT_ROOT', 'LOCALAPPDATA', 'APPDATA', 'TEMP'):
        assert calls[0][1]['env'][key] != calls[1][1]['env'][key]
    for call in calls:
        assert json.loads(call[1]['env'][config.ENV_KEY])['client_id'] == 'desktop'
    assert devices.running_targets['A']['build_source_sha'] == devices.running_targets['B']['build_source_sha'] == build


def test_user_test_launch_supplies_candidate_owned_observation_identity(publication,devices):
    root,value,paths,build,publish=publication
    from desktop.test_device_profile import environment,PROFILE_CONTRACT
    profile=devices.prepare('UserTest');env=environment(devices.root('UserTest'),profile)
    calls=[]
    t.launch_exact(value,value,a.RepoSource(root).tree_for,paths,device_environment=env,
        device_contract=PROFILE_CONTRACT,spawn=lambda argv,**options:calls.append(options))
    actual=calls[0]['env']
    assert actual['MENTORFLOW_TEST_TARGET_ID']==value['test_id']
    assert actual['MENTORFLOW_TEST_ARTIFACT_SHA256']==value['artifact_sha256']
    assert actual['MENTORFLOW_TEST_SCENARIO']=='ACCOUNT_MVP_USER_TEST'


@pytest.mark.parametrize('change', ['runtime', 'unknown', 'tree', 'unrelated'])
def test_old_unknown_and_wrong_sources_refused(publication, change):
    root, value, paths, build, _ = publication
    if change == 'runtime': (root / 'runtime.py').write_text('# newer product; rebuild required')
    elif change == 'unknown': value['source_sha'] = 'f' * 40
    elif change == 'tree': value['source_tree'] = 'e' * 40
    else:
        git(root, 'checkout', '--orphan', 'unrelated')
        (root / 'runtime.py').write_text('# unrelated source')
        other = commit(root, 'Unrelated source')
        value.update(source_sha=other, source_tree=git(root, 'show', '-s', '--format=%T', other))
        git(root, 'checkout', 'integration/main3')
    (root / a.TARGET).write_bytes(raw(value)); commit(root, 'Invalid qualification')
    source = a.RepoSource(root)
    assert a.load_view(source, paths)['active_target'] is None
    with pytest.raises(t.Refused): source.qualify(value)


@pytest.mark.parametrize('tamper', ['exe', 'receipt', 'selection', 'publication', 'superseded', 'during_hash', 'tls'])
def test_tampered_and_superseded_never_spawn(publication, devices, tamper):
    root, value, paths, _, _ = publication
    selected = copy.deepcopy(value); source = a.RepoSource(root)
    spawn = Mock()
    if tamper == 'exe': Path(value['artifact_path']).write_bytes(b'MZ same name wrong build')
    if tamper == 'receipt': Path(value['identity_evidence_path']).write_bytes(b'{}')
    if tamper == 'selection': selected['build_id'] = 'hand-edited'
    if tamper in ('publication', 'superseded'):
        updated = dict(value, artifact_sha256='c'*64) if tamper == 'publication' else dict(value, status='SUPERSEDED')
        (root / a.TARGET).write_bytes(raw(updated)); commit(root, 'Replace publication')
        source = a.RepoSource(root)
    if tamper == 'tls': (Path(value['identity_evidence_path']).parent / 'ca.crt').write_bytes(b'tampered')
    changed = False
    def cancelled():
        nonlocal changed
        if tamper == 'during_hash' and not changed:
            changed = True
            (root / a.TARGET).write_bytes(raw(dict(value, status='SUPERSEDED')))
            commit(root, 'Supersede while hashing')
        return False
    with pytest.raises(t.Refused):
        devices.launch('A', selected, source, paths, spawn=spawn, cancelled=cancelled)
    spawn.assert_not_called()


def test_runtime_config_applies_before_frozen_sidecar(publication, devices, monkeypatch):
    _, value, _, _, _ = publication
    from tools.dev_workbench import runtime_lease
    runtime = json.loads((runtime_lease.AUTHORITY/'LATEST.json').read_text())['desktop_runtime_configuration']
    from desktop.test_device_profile import environment
    # Snapshot/restore every environment assignment made by the seam.
    with monkeypatch.context() as patch:
        patch.setattr(os, 'environ', dict(os.environ))
        os.environ.update(environment(devices.root('A'), devices.owned('A')))
        os.environ[config.ENV_KEY] = json.dumps(runtime)
        # Execute the actual entry function without importing the GUI or backend.
        module = ast.parse((t.ROOT / 'desktop_entry.py').read_text(encoding='utf-8'))
        function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'apply_account_core_package_config')
        namespace = dict(os=os, json=json, Path=Path,
                         sys=SimpleNamespace(frozen=True, executable=str(devices.root('A') / 'app.exe')))
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'desktop_entry.py', 'exec'), namespace)
        # Missing ordinary package sidecar/../Config must not override the explicit seam.
        namespace['apply_account_core_package_config']()
        assert os.environ['MENTORFLOW_DESKTOP_WORKSPACE_ID'] == 'space'
        assert os.environ['SSL_CERT_FILE'] == runtime['tls_ca_path']
        assert Path(os.environ['MENTORFLOW_DB']).is_relative_to(devices.root('A'))


@pytest.mark.parametrize('key,value', [('command', 'calc.exe'), ('issuer', 'http://issuer.invalid'),
    ('api_base_url', 'https://user:password@example.test'), ('tls_ca_sha256', 'bad')])
def test_runtime_config_rejects_unallowlisted_fields(publication, key, value):
    runtime = json.loads(Path(publication[1]['identity_evidence_path']).read_text())['test_device_configuration']
    runtime[key] = value
    with pytest.raises(ValueError): config.validate(runtime)


def test_uncommitted_manifest_never_becomes_publication(publication):
    root, value, paths, _, publish = publication
    (root / a.TARGET).write_bytes(raw(dict(value, build_id='local-edit')))
    view = a.load_view(a.RepoSource(root), paths)
    assert view['active_target'] == value
    assert view['candidate_identity']['governance_publication_sha'] == publish


def test_stale_snapshot_cannot_project_current_approval(publication):
    root, value, paths, _, _ = publication
    old = a.RepoSource(root)
    (root / a.TARGET).write_bytes(raw(dict(value, status='SUPERSEDED')))
    commit(root, 'Withdraw target')
    assert a.load_view(old, paths)['active_target'] is None
    with pytest.raises(t.Refused, match='TARGET_CHANGED'): old.qualify(value)


@pytest.mark.parametrize('field', ['packaged_entrypoint', 'test_device_configuration'])
def test_old_frozen_receipt_missing_runtime_contract_refused(publication, devices, field):
    root, value, paths, _, _ = publication
    path = Path(value['identity_evidence_path'])
    receipt = json.loads(path.read_text()); receipt.pop(field)
    path.write_bytes(raw(receipt)); value['identity_evidence_sha256'] = t.sha256(path.read_bytes())
    (root / a.TARGET).write_bytes(raw(value)); commit(root, 'Publish incomplete runtime receipt')
    spawn = Mock()
    with pytest.raises(t.Refused): devices.launch('A', value, a.RepoSource(root), paths, spawn=spawn)
    spawn.assert_not_called()


def test_non_device_environment_is_unchanged(monkeypatch):
    monkeypatch.delenv('MENTORFLOW_TEST_DEVICE_ROOT', raising=False)
    before = dict(os.environ)
    assert config.apply_from_environment() is False
    assert dict(os.environ) == before

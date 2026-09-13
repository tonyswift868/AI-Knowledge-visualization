"""Two-device source harness tests; synthetic data and inert processes only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tools.dev_workbench import authority as a, targets as t
from tools.dev_workbench.devices import Devices, SCENARIOS, PROFILE_CONTRACT
from tools.dev_workbench.test_workbench import app, source, candidate, approve, raw
from desktop import test_device_profile as profile


@pytest.fixture
def devices(tmp_path, monkeypatch):
    result = Devices(tmp_path / 'owned-devices')
    result.prepare('A'); result.prepare('B')
    monkeypatch.setattr(profile, 'DEVICE_BASE', result.base)
    return result


def compatible(source, candidate):
    value = candidate[0]
    path = Path(value['identity_evidence_path'])
    identity = json.loads(path.read_text())
    identity['device_profile_contract'] = PROFILE_CONTRACT
    identity['packaged_entrypoint'] = 'desktop_entry.py'
    certificate = path.parent / 'test-ca.crt'
    certificate.write_bytes(b'fixture certificate; no network')
    identity['test_device_configuration'] = dict(contract='MENTORFLOW_TEST_RUNTIME_V1',
        issuer='https://test.invalid', api_base_url='https://test.invalid/api',
        client_id='test-client', audience='test-api', organization_id='test-org', workspace_id='test-workspace',
        tls_ca_path=str(certificate), tls_ca_sha256=t.sha256(certificate.read_bytes()))
    path.write_bytes(raw(identity)); value['identity_evidence_sha256'] = t.sha256(path.read_bytes())
    approve(source, candidate)
    def git(*args):
        if args[0] == 'show' and args[1].endswith(':desktop/test_device_profile.py'):
            return "PROFILE_CONTRACT = '" + PROFILE_CONTRACT + "'"
        if args[0] == 'show' and args[1].endswith(':desktop/main.py'): return '_configure_test_device()'
        if args[0] == 'show' and args[1].endswith(':desktop_entry.py'):
            return 'from desktop.test_device_configuration import apply_from_environment'
        raise t.Refused('REPO_STATE_UNAVAILABLE')
    source.git = git
    return value


def test_durable_roots_reset_only_one_device(devices):
    for device in ('A', 'B'):
        (devices.root(device) / 'vault/prelaunch/data/state.db').write_bytes(device.encode())
        (devices.root(device) / 'preferences/settings.ini').write_text(device)
    restarted = Devices(devices.base)
    before = restarted.owned('A')
    sibling = {str(p.relative_to(devices.root('B'))): p.read_bytes() for p in devices.root('B').rglob('*') if p.is_file()}
    assert restarted.prepare('A') == before
    after = restarted.reset('A')
    assert after['generation'] != before['generation'] and after['owner'] == before['owner']
    assert not (devices.root('A') / 'vault/prelaunch/data/state.db').exists()
    assert sibling == {str(p.relative_to(devices.root('B'))): p.read_bytes() for p in devices.root('B').rglob('*') if p.is_file()}


@pytest.mark.parametrize('path', ['E:/MentorFlowData', 'E:/MentorFlowData/A', 'E:/Codex/Temp', 'E:/Codex/TestDevices/../../MentorFlowData'])
def test_protected_and_broad_roots_refused(path):
    with pytest.raises(t.Refused): Devices(path)


def test_device_environment_cannot_become_a_real_migration_process(devices, monkeypatch):
    from desktop.account_storage import require_migration_target, TEST_ONLY_ENV
    value = devices.owned('A')
    env = profile.environment(devices.root('A'), value)
    assert env[TEST_ONLY_ENV] == '1'
    monkeypatch.setenv(TEST_ONLY_ENV, env[TEST_ONLY_ENV])
    with pytest.raises(ValueError, match='TEST_ONLY processes'):
        require_migration_target(Path('E:/MentorFlowData/test-only-migration-gate'))


def test_existing_unowned_root_and_forged_marker_refused(devices, tmp_path):
    other = Devices(tmp_path / 'not-owned'); root = other.root('A'); root.mkdir(parents=True)
    marker = devices.owned('A'); marker['root'] = str(root)
    (root/profile.MARKER).write_bytes(raw(marker))
    sentinel = root/'keep'; sentinel.write_bytes(b'unchanged')
    with pytest.raises(t.Refused): other.reset('A')
    with pytest.raises(t.Refused): other.prepare('A')
    assert sentinel.read_bytes() == b'unchanged'


def test_junction_and_hardlink_are_not_launchable(devices, tmp_path):
    outside = tmp_path/'outside'; outside.mkdir(); sentinel = outside/'keep'; sentinel.write_bytes(b'untouched')
    link = devices.root('A')/'linked'
    result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(ValueError): devices.reset('A')
        with pytest.raises(ValueError): profile.validate_storage(devices.root('A'))
        assert sentinel.read_bytes() == b'untouched'
    finally: link.rmdir()
    os.link(sentinel, devices.root('A')/'hardlink')
    with pytest.raises(ValueError): profile.validate_storage(devices.root('A'))
    devices.reset('A')
    assert sentinel.read_bytes() == b'untouched'


def test_same_account_locker_namespaces_are_disjoint_and_reset_rotates(devices, monkeypatch):
    from backend.app import credential_store as store
    locker = {}
    backend = SimpleNamespace(set_password=lambda service, ref, value: locker.__setitem__((service, ref), value),
        get_password=lambda service, ref: locker.get((service, ref)),
        delete_password=lambda service, ref: locker.pop((service, ref), None))
    monkeypatch.setattr(store, '_get_keyring', lambda: backend)
    monkeypatch.delenv('MENTORFLOW_CREDENTIAL_STORE', raising=False)
    account = dict(issuer_hash='a'*64, subject_hash='b'*64, purpose='oauth-access-token')
    services = []
    for device in ('A', 'B'):
        value = devices.owned(device)
        monkeypatch.setenv('MENTORFLOW_TEST_DEVICE_ROOT', str(devices.root(device)))
        monkeypatch.setenv('MENTORFLOW_TEST_DEVICE_GENERATION', value['generation'])
        services.append(store._service_name())
        store.store_account_secret('synthetic-' + device, **account)
    assert services[0] != services[1] and all(x != store.SERVICE_NAME for x in services)
    assert store.get_account_secret(**account) == 'synthetic-B'
    value = devices.reset('A')
    monkeypatch.setenv('MENTORFLOW_TEST_DEVICE_ROOT', str(devices.root('A')))
    monkeypatch.setenv('MENTORFLOW_TEST_DEVICE_GENERATION', value['generation'])
    with pytest.raises(store.CredentialStoreError, match='不存在'): store.get_account_secret(**account)
    monkeypatch.delenv('MENTORFLOW_TEST_DEVICE_ROOT')
    assert store._service_name() == store.SERVICE_NAME
    assert not any(key[0] == store.SERVICE_NAME for key in locker)


def test_real_qsettings_sqlite_and_process_lock_isolation(devices):
    script = r'''
import json, os, sqlite3, sys, time
from pathlib import Path
from desktop import test_device_profile as p
p.DEVICE_BASE = Path(sys.argv[1]); p.configure()
from PySide6.QtCore import QSettings
root = Path(os.environ['MENTORFLOW_TEST_DEVICE_ROOT'])
settings = p.create_settings(QSettings,'MentorFlow','MentorFlow')
assert Path(settings.fileName()).is_relative_to(root), 'Refuse write outside isolated preferences'
assert settings.value('device') is None
settings.setValue('device',root.name); settings.sync()
db = sqlite3.connect(os.environ['MENTORFLOW_DB'])
db.execute('create table identity (device text)'); db.execute('insert into identity values (?)',(root.name,)); db.commit(); db.close()
(root/'process-proof.json').write_text(json.dumps({'settings':settings.fileName(),'root':str(root),'namespace':p.credential_service('test')}))
time.sleep(2)
'''
    children = []
    try:
        for device in ('A','B'):
            env = {**os.environ, **profile.environment(devices.root(device), devices.owned(device))}
            children.append(subprocess.Popen([sys.executable, '-c', script, str(devices.base)], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW))
        deadline = time.monotonic()+8
        while not all((devices.root(d)/'process-proof.json').exists() for d in ('A','B')) and time.monotonic()<deadline:
            time.sleep(.02)
        for device in ('A','B'):
            assert (devices.root(device)/'process-proof.json').exists()
            with pytest.raises(t.Refused, match='BUSY'): Devices(devices.base).reset(device)
        proofs = [json.loads((devices.root(d)/'process-proof.json').read_text()) for d in ('A','B')]
        assert proofs[0]['settings'] != proofs[1]['settings']
        assert all(Path(p['settings']).is_relative_to(Path(p['root'])) for p in proofs)
        assert proofs[0]['namespace'] != proofs[1]['namespace']
        for child in children:
            stdout, stderr = child.communicate(timeout=8)
            assert child.returncode == 0, stderr.decode(errors='replace')
    finally:
        for child in children:
            if child.poll() is None: child.terminate(); child.wait(timeout=5)


def test_both_targets_share_verified_build_not_environment(devices, source, candidate):
    value = compatible(source, candidate)
    calls = []
    spawn = lambda argv, **kwargs: calls.append((argv,kwargs)) or SimpleNamespace(pid=99,poll=lambda:0)
    for device in ('A','B'):
        devices.launch(device, value, source, candidate[1], spawn=spawn)
    targets = [devices.target(d,value) for d in ('A','B')]
    assert {t['target_id'] for t in targets} == {'DEVICE_A_TARGET','DEVICE_B_TARGET'}
    for key in ('source_sha','source_tree','artifact_sha256','build_id'): assert targets[0][key] == targets[1][key]
    for key in ('MENTORFLOW_DB','MENTORFLOW_ACCOUNT_VAULT_ROOT','LOCALAPPDATA','APPDATA','TEMP'):
        assert calls[0][1]['env'][key] != calls[1][1]['env'][key]
    assert all(not call[1]['shell'] and len(call[0]) == 1 for call in calls)
    assert not any(k.startswith('MENTORFLOW_SECRET_') for k in calls[0][1]['env'])


def test_running_pair_cannot_mix_builds(devices, source, candidate):
    value=compatible(source,candidate)
    devices.launch('A',value,source,candidate[1],spawn=lambda *args,**kw: SimpleNamespace(pid=10,poll=lambda:None))
    value['build_id']='different-build'; approve(source,candidate)
    spawn=Mock()
    with pytest.raises(t.Refused,match='PAIR_BUILD_CHANGED'):
        devices.launch('B',value,source,candidate[1],spawn=spawn)
    spawn.assert_not_called()


@pytest.mark.parametrize('failure', ['contract','hash','source','manifest_command','identity'])
def test_bad_candidates_never_spawn(devices, source, candidate, failure):
    value = compatible(source, candidate)
    if failure == 'contract':
        path = Path(value['identity_evidence_path']); identity=json.loads(path.read_text()); identity.pop('device_profile_contract')
        path.write_bytes(raw(identity)); value['identity_evidence_sha256']=t.sha256(path.read_bytes()); approve(source,candidate)
    elif failure == 'hash': Path(value['artifact_path']).write_bytes(b'MZ tampered')
    elif failure == 'source': value['source_sha']='c'*40; approve(source,candidate)
    elif failure == 'manifest_command': value['command']='anything'
    else: Path(value['identity_evidence_path']).write_bytes(b'{}')
    spawn=Mock()
    with pytest.raises((t.Refused, ValueError)): devices.launch('A',value,source,candidate[1],spawn=spawn)
    spawn.assert_not_called()


def test_real_exact_native_two_device_launch(devices, source, candidate, tmp_path):
    compiler=Path(os.environ['SystemRoot'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    code=tmp_path/'Probe.cs'
    code.write_text('using System; using System.IO; class Probe { static void Main() { string r=Environment.GetEnvironmentVariable("MENTORFLOW_TEST_DEVICE_ROOT"); File.WriteAllLines(Path.Combine(r,"probe.txt"),new string[]{r,Environment.GetEnvironmentVariable("MENTORFLOW_DB"),Environment.GetEnvironmentVariable("LOCALAPPDATA")}); } }')
    value=candidate[0]
    build=subprocess.run([str(compiler),'/nologo','/target:winexe','/out:'+value['artifact_path'],str(code)],capture_output=True)
    assert build.returncode==0, build.stdout
    value['artifact_sha256']=t.sha256(Path(value['artifact_path']).read_bytes())
    identity=Path(value['identity_evidence_path']); identity.write_bytes(raw(dict(schema_version=1,**{k:value[k] for k in t.IDENTITY_FIELDS})))
    compatible(source,candidate)
    for device in ('A','B'):
        devices.launch(device,value,source,candidate[1]); assert devices.processes[device].wait(timeout=5)==0
    reports=[(devices.root(d)/'probe.txt').read_text().splitlines() for d in ('A','B')]
    assert all(a != b for a,b in zip(*reports))


def test_observation_is_scoped_and_never_sync_authority(devices, source, candidate, app):
    from tools.dev_workbench.window import Workbench
    from tools.dev_workbench.device_panel import DevicePanel
    from tools.dev_workbench.window import label
    value=compatible(source,candidate)
    target=devices.target('A',value)
    report=dict(target=target,scenario=SCENARIOS[0],values=dict(account='test-account',server_workspace='workspace-1',
        last_sync_revision='revision-7',cloud_entities_restored_count=12,missing_local_resources_count=3))
    path=devices.root('A')/'observation.json'; path.write_bytes(raw(report))
    window=Workbench(lambda:source,candidate[1],auto_load=False)
    panel=DevicePanel(window,label,devices)
    try:
        view=a.load_view(source,candidate[1]); panel.show_view(view)
        assert '等待云同步实现' in panel.scenario.text()
        assert '12' in panel.rows['A']['summary'].text() and '3' in panel.rows['A']['summary'].text()
        assert 'PASS' not in panel.rows['A']['summary'].text()
        assert devices.observation('B',value)['values']=={}
        report['target']['generation']='a'*32; path.write_bytes(raw(report))
        assert devices.observation('A',value)['values']=={}
        assert view['server_sync_state']=='UNKNOWN'
    finally: panel.close(); window.close()


def test_web_profile_cache_adapter_is_opt_in(devices, monkeypatch):
    class WebProfile:
        def isOffTheRecord(self): return False
        def storageName(self): return 'teacher-profile'
        def setCachePath(self,path): self.cache=path
        def setPersistentStoragePath(self,path): self.storage=path
    outputs=[]
    for device in ('A','B'):
        monkeypatch.setenv('MENTORFLOW_TEST_DEVICE_ROOT',str(devices.root(device)))
        result=profile.create_web_profile(WebProfile); outputs.append(result.cache)
        assert Path(result.storage).is_relative_to(devices.root(device))
    assert outputs[0]!=outputs[1]
    monkeypatch.delenv('MENTORFLOW_TEST_DEVICE_ROOT')
    assert not hasattr(profile.create_web_profile(WebProfile),'cache')


def test_settings_adapter_preserves_normal_call_contract(monkeypatch):
    monkeypatch.delenv('MENTORFLOW_TEST_DEVICE_ROOT',raising=False)
    factory=Mock()
    assert profile.create_settings(factory,'MentorFlow','MentorFlow') is factory.return_value
    factory.assert_called_once_with('MentorFlow','MentorFlow')
    factory.return_value.setFallbacksEnabled.assert_not_called()


def test_all_product_settings_and_persistent_browser_constructors_use_adapter():
    import ast
    for path in (t.ROOT/'desktop').glob('*.py'):
        if path.name.startswith('test_'): continue
        tree=ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                assert node.func.id != 'QSettings', str(path)
                # The unnamed login browser is off-the-record by existing contract.
                if node.func.id == 'QWebEngineProfile':
                    assert path.name == 'in_app_oidc.py' and not node.args


def test_missing_source_adapter_and_reserved_scenario_refused(devices, source, candidate):
    value=compatible(source,candidate)
    source.git=lambda *_: ''
    spawn=Mock()
    with pytest.raises(t.Refused,match='CONTRACT_REQUIRED'):
        devices.launch('A',value,source,candidate[1],spawn=spawn)
    with pytest.raises(t.Refused,match='SCENARIO_UNAVAILABLE'):
        devices.launch('A',value,source,candidate[1],spawn=spawn,scenario=SCENARIOS[2])
    spawn.assert_not_called()


def test_second_scenario_has_separate_observation_scope(devices, source, candidate):
    value=compatible(source,candidate)
    target=devices.target('B',value,SCENARIOS[1])
    report=dict(target=target,scenario=SCENARIOS[1],values=dict(missing_local_resources_count=5))
    (devices.root('B')/'observation.json').write_bytes(raw(report))
    assert devices.observation('B',value)['values']=={}
    assert devices.observation('B',value,SCENARIOS[1])['values']['missing_local_resources_count']==5
    calls=[]
    devices.launch('B',value,source,candidate[1],scenario=SCENARIOS[1],
        spawn=lambda argv,**options: calls.append(options) or SimpleNamespace(pid=7,poll=lambda:0))
    assert calls[0]['env']['MENTORFLOW_TEST_SCENARIO']==SCENARIOS[1]


def test_product_observation_missing_fields_and_wrong_identity_are_ignored(devices, candidate):
    value,_=candidate
    target=devices.target('A',value)
    report=dict(schema='mentorflow.sync-observation.v1',test_id=value['test_id'],scenario=SCENARIOS[0],
        generation=target['generation'],artifact_sha256=value['artifact_sha256'],source='SERVER_CONFIRMED',
        account='account',server_workspace='workspace',last_sync_revision='pg-snapshot:1:2:',
        cloud_entities_restored_count=4,missing_local_resources_count=1)
    path=devices.root('A')/'observation.json'
    path.write_bytes(raw(report))
    assert devices.observation('A',value)['values']['cloud_entities_restored_count']==4
    for key in ('account','test_id','generation','artifact_sha256'):
        malformed={k:v for k,v in report.items() if k!=key}
        path.write_bytes(raw(malformed))
        assert devices.observation('A',value)['values']=={}


def test_real_named_webengine_profiles_are_isolated(devices, monkeypatch, app):
    from PySide6.QtWebEngineCore import QWebEngineProfile
    profiles=[]
    try:
        for device in ('A','B'):
            monkeypatch.setenv('MENTORFLOW_TEST_DEVICE_ROOT',str(devices.root(device)))
            web=profile.create_web_profile(QWebEngineProfile,'workbench-isolation-probe-'+device)
            profiles.append(web)
            assert Path(web.cachePath()).is_relative_to(devices.root(device))
            assert Path(web.persistentStoragePath()).is_relative_to(devices.root(device))
        assert profiles[0].cachePath()!=profiles[1].cachePath()
    finally:
        for web in profiles: web.deleteLater()
        app.processEvents()


def test_device_panel_narrow_and_reset_button(devices, source, candidate, app):
    from tools.dev_workbench.window import Workbench
    from PySide6.QtTest import QTest
    value=compatible(source,candidate)
    window=Workbench(lambda:source,candidate[1],auto_load=False)
    panel=window.device_panel; panel.devices=devices
    try:
        window.show_view(a.load_view(source,candidate[1])); window.resize(320,700)
        window.pages.setCurrentWidget(panel); window.show()
        for _ in range(5): app.processEvents()
        assert panel.horizontalScrollBar().maximum()==0
        assert panel.rows['A']['launch'].text()=='启动设备 A'
        assert panel.rows['B']['launch'].text()=='启动设备 B'
        assert not panel.scenario_box.model().item(2).isEnabled()
        generation=devices.owned('A')['generation']
        panel.rows['A']['reset'].click()
        deadline=time.monotonic()+5
        while window.jobs and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.01)
        assert not window.jobs
        assert devices.owned('A')['generation']!=generation
        assert '已重置' in panel.notice.text()
    finally: window.close()

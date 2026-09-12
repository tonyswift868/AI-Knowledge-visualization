"""Bounded developer-tool fixtures; no MentorFlow installer/auth/server execution."""
import copy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest

from tools.dev_workbench import authority as a, targets as t


def raw(value): return (json.dumps(value, ensure_ascii=False) + '\n').encode()


class Source:
    sha = 'a' * 40
    tree = 'b' * 40
    timestamp = '2026-09-13T00:00:00Z'

    def __init__(self, files=None):
        self.files = files or {}

    def read(self, name): return self.files.get(name)
    def tree_for(self, sha): return self.tree if sha == self.sha else 'UNKNOWN'
    def git(self, *_): raise t.Refused('REPO_STATE_UNAVAILABLE')


@pytest.fixture
def source():
    return Source({
        a.BOARD: raw(dict(project='MentorFlow', generation='MAIN3', snapshot_kind='DELIVERY_SNAPSHOT_NOT_LIVE',
            repository_url='https://github.com/example/MentorFlow', control_plane=dict(user_action_required='NONE'),
            workers={'A': dict(status='SILENT'), '01': dict(status='RUNNING', task_id='T01',
            dispatch_id='D01', delivery_commit='c'*40, objective='Build exact candidate', blocker='NONE', next_gate='Gate A'),
                     'B': dict(status='RUNNING')})),
        a.QUEUE: raw(dict(tasks=[dict(task_id='BATCH02_EXACT_INTEGRATION', state='DONE'),
            dict(task_id='GATE_A_SCHEMA_0036_UNBLOCK', state='DONE'),
            dict(task_id='GATE_A_AUTH_LIVE_ACTIVATION', state='NOT_DISPATCHED'),
            dict(task_id='MAIN3_PROMOTION', state='BLOCKED')])),
        a.CURRENT: b'GENERATION = MAIN3\nALEMBIC_HEAD = 0036_account_import_ledger\n',
        a.PLAN: b'MAIN3 repo plan', a.HANDOFF: b'DERIVED / NOT SOURCE OF TRUTH'})


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / 'trusted'; root.mkdir()
    artifact = root / 'Candidate.exe'; artifact.write_bytes(b'MZ inert exact fixture')
    receipt = root / 'receipt.json'; receipt.write_text('{}')
    log = root / 'runtime.log'; log.write_text('no secrets')
    identity = root / 'identity.json'
    value = t.json_bytes((t.ROOT / a.TARGET).read_bytes())
    value.update(test_id='MF-EXACT-TEST', status='CURRENT', source_sha=Source.sha, source_tree=Source.tree,
        product_version='1.6.0', build_id='main3-build-exact', artifact_type='DESKTOP_EXE',
        artifact_path=str(artifact), artifact_sha256=t.sha256(artifact.read_bytes()),
        launch_type='DESKTOP_EXE', launch_target=str(artifact), environment='TEST',
        deployment_identity='fixture-local', purpose='Inert exact launch proof',
        user_action='Start the designated candidate', success_criteria='Verified process admission',
        receipt_path=str(receipt), log_path=str(log), test_username='fixture_user',
        secret_ref='secret://fixture/login', identity_evidence_path=str(identity))
    identity.write_bytes(raw(dict(schema_version=1, **{k:value[k] for k in t.IDENTITY_FIELDS})))
    value['identity_evidence_sha256'] = t.sha256(identity.read_bytes())
    return value, t.TrustedPaths([root])


def test_repo_state_is_projection_with_only_current_slots(source):
    before = copy.deepcopy(source.files)
    view = a.load_view(source)
    assert [w['slot'] for w in view['workers']] == ['A', '01', '02', '03', '04']
    assert view['workers'][1]['head'] == 'c'*40
    assert view['current_gate'] == view['database_revision'] == 'UNKNOWN'
    assert view['source_schema'] == '0036_account_import_ledger'
    assert view['action_summary'] == '当前无需用户操作。'
    assert {w['task_id']:w['status'] for w in view['workflow']}['MAIN3_PROMOTION'] == 'BLOCKED'
    assert source.files == before


@pytest.mark.parametrize('bad', [None, b'{broken', b'[]', b'{"workers":[],"control_plane":[]}'])
def test_missing_or_malformed_state_does_not_guess(source, bad):
    source.files[a.BOARD] = bad
    view = a.load_view(source)
    assert all(w['status'] == 'UNKNOWN' for w in view['workers'])
    assert view['project'] == 'UNKNOWN'
    assert view['action_summary'] == '用户操作状态 UNKNOWN'


@pytest.mark.parametrize('unknown', ['FINISHED_PROBABLY', {}, [], 5, None])
def test_unknown_worker_status_is_unknown(source, unknown):
    board = t.json_bytes(source.files[a.BOARD]); board['workers']['01']['status'] = unknown
    source.files[a.BOARD] = raw(board)
    assert a.load_view(source)['workers'][1]['status'] == 'UNKNOWN'


def test_user_action_from_machine_state(source):
    queue = t.json_bytes(source.files[a.QUEUE])
    queue['tasks'][2].update(user_action_required='Perform native login', success_criteria='Workspace ready')
    source.files[a.QUEUE] = raw(queue)
    view = a.load_view(source)
    assert view['actions'][0]['success_criteria'] == 'Workspace ready'


@pytest.mark.parametrize('deployment,expected', [('PASS', 'UNKNOWN'),
    ('NOT_RUN_AFTER_FIX', '0036 源码解阻已完成；部署验收尚未确认')])
def test_phase_does_not_override_newer_deployment_evidence(source, deployment, expected):
    source.files[a.CURRENT] = ('GATE_A_SCHEMA_BLOCKER = RESOLVED_IN_SOURCE\nGATE_A_REAL_DEPLOYMENT = '+deployment+'\n').encode()
    assert a.load_view(source)['current_phase'] == expected


@pytest.mark.parametrize('change', [dict(password='never-display'), dict(launch_type='SHELL'),
    dict(artifact_sha256='bad'), dict(created_at='yesterday'), dict(secret_ref='literal-password'), dict(source_sha=None)])
def test_manifest_schema_rejects_commands_secrets_and_missing_identity(candidate, change):
    value, _ = candidate
    value.update(change)
    with pytest.raises(t.Refused, match='INVALID_MANIFEST'): t.validate_manifest(value)


def test_manifest_valid_exact_artifact_launch(candidate):
    value, paths = candidate
    spawn = Mock(return_value=SimpleNamespace(pid=42))
    assert t.launch_exact(value, copy.deepcopy(value), Source().tree_for, paths, spawn=spawn).pid == 42
    argv = spawn.call_args.args[0]
    assert argv == [value['artifact_path']]
    assert spawn.call_args.kwargs['shell'] is False
    assert 'env' not in spawn.call_args.kwargs


def test_installer_is_direct_exact_exe_without_commands(candidate):
    value, paths = candidate
    value.update(launch_type='INSTALLER', artifact_type='INSTALLER')
    identity = Path(value['identity_evidence_path'])
    identity.write_bytes(raw(dict(schema_version=1, **{k:value[k] for k in t.IDENTITY_FIELDS})))
    value['identity_evidence_sha256'] = t.sha256(identity.read_bytes())
    spawn = Mock()
    t.launch_exact(value, value, Source().tree_for, paths, spawn=spawn)
    assert spawn.call_args.args[0] == [value['artifact_path']]
    assert spawn.call_args.kwargs['shell'] is False


@pytest.mark.parametrize('failure,code', [
    ('missing', 'ARTIFACT_MISSING'), ('hash', 'ARTIFACT_HASH_MISMATCH'),
    ('tree', 'SOURCE_IDENTITY_MISMATCH'), ('build', 'BUILD_IDENTITY_MISMATCH'),
    ('evidence_hash', 'BUILD_IDENTITY_MISMATCH'), ('target', 'LAUNCH_TARGET_MISMATCH'),
    ('cancel', 'CANCELLED')])
def test_launch_admission_fails_closed(candidate, failure, code):
    value, paths = candidate
    if failure == 'missing': Path(value['artifact_path']).unlink()
    if failure == 'hash': Path(value['artifact_path']).write_bytes(b'MZ different bytes')
    if failure == 'tree': value['source_tree'] = 'f'*40
    if failure == 'build': value['build_id'] = 'unmatched'
    if failure == 'evidence_hash': Path(value['identity_evidence_path']).write_text('{}')
    if failure == 'target': value['launch_target'] = value['artifact_path'] + ' --shell'
    spawn = Mock()
    with pytest.raises(t.Refused, match=code):
        t.launch_exact(value, copy.deepcopy(value), Source().tree_for, paths,
                       spawn=spawn, cancelled=lambda: failure == 'cancel')
    spawn.assert_not_called()


@pytest.mark.parametrize('path', [r'\\server\share\bad.exe', 'C:/Windows/System32/cmd.exe',
    'relative.exe', r'\\?\E:\Codex\Temp\evil.exe', 'E:/Codex/Temp/../evil.exe'])
def test_untrusted_paths_rejected(candidate, path):
    _, paths = candidate
    with pytest.raises(t.Refused, match='UNTRUSTED_PATH'): paths.check(path)


@pytest.mark.parametrize('status', ['WAITING_FOR_ARTIFACT', 'SUPERSEDED', 'PASS', 'FAIL'])
def test_history_or_unready_target_cannot_launch(candidate, status):
    value, paths = candidate; value['status'] = status
    with pytest.raises(t.Refused, match='OLD_OR_UNREADY_TARGET'):
        t.launch_exact(value, value, Source().tree_for, paths, spawn=Mock())


def test_changed_target_and_unknown_script_cannot_launch(candidate):
    value, paths = candidate
    with pytest.raises(t.Refused, match='TARGET_CHANGED'):
        t.launch_exact(value, {**value, 'test_id':'OTHER'}, Source().tree_for, paths)
    value.update(launch_type='SCRIPT_ID', launch_target='powershell -EncodedCommand bad')
    with pytest.raises(t.Refused, match='UNKNOWN_SCRIPT_ID'):
        t.launch_exact(value, value, Source().tree_for, paths)


def test_fixed_script_registry_starts_real_inert_child(candidate):
    value, paths = candidate
    value.update(artifact_type='SCRIPT_INPUT', launch_type='SCRIPT_ID', launch_target='WORKBENCH_SELF_CHECK')
    identity = Path(value['identity_evidence_path'])
    identity.write_bytes(raw(dict(schema_version=1, **{k:value[k] for k in t.IDENTITY_FIELDS})))
    value['identity_evidence_sha256'] = t.sha256(identity.read_bytes())
    process = t.launch_exact(value, copy.deepcopy(value), Source().tree_for, paths)
    assert process.wait(timeout=10) == 0


def test_pinned_artifact_cannot_be_changed_during_launch(candidate):
    if os.name != 'nt': pytest.skip('Windows file leases')
    value, paths = candidate
    def spawn(*_, **__):
        with pytest.raises(OSError): Path(value['artifact_path']).write_bytes(b'mutated')
        with pytest.raises(OSError): Path(value['artifact_path']).rename(Path(value['artifact_path']).with_name('moved.exe'))
        return SimpleNamespace(pid=42)
    t.launch_exact(value, value, Source().tree_for, paths, spawn=spawn)


def test_real_exact_native_exe_launch(candidate):
    compiler = Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    if os.name != 'nt' or not compiler.is_file(): pytest.skip('Windows native inert probe compiler')
    value, paths = candidate
    root = Path(value['artifact_path']).parent
    code = root / 'Probe.cs'
    code.write_text('using System.IO; class Probe { static void Main() { File.WriteAllText("probe.started", "EXACT_NATIVE_PROBE"); } }')
    built = subprocess.run([str(compiler), '/nologo', '/target:winexe', '/out:'+value['artifact_path'], str(code)],
                            capture_output=True, timeout=30)
    assert built.returncode == 0
    value['artifact_sha256'] = t.sha256(Path(value['artifact_path']).read_bytes())
    identity = Path(value['identity_evidence_path'])
    identity.write_bytes(raw(dict(schema_version=1, **{k:value[k] for k in t.IDENTITY_FIELDS})))
    value['identity_evidence_sha256'] = t.sha256(identity.read_bytes())
    process = t.launch_exact(value, copy.deepcopy(value), Source().tree_for, paths)
    assert process.wait(timeout=10) == 0
    assert (root/'probe.started').read_text() == 'EXACT_NATIVE_PROBE'


def test_windows_junction_never_admitted(candidate, tmp_path):
    if os.name != 'nt': pytest.skip('Windows reparse check')
    import _winapi
    value, paths = candidate
    outside = tmp_path/'outside'; outside.mkdir(); (outside/'payload.exe').write_bytes(b'MZ')
    link = Path(value['artifact_path']).parent/'alias'
    _winapi.CreateJunction(str(outside), str(link))
    try:
        with pytest.raises(t.Refused, match='UNTRUSTED_PATH'): paths.check(str(link/'payload.exe'))
    finally:
        os.rmdir(link)


@pytest.mark.parametrize('kind', ['receipt', 'log', 'directory'])
def test_navigation_paths_open_only_documents(candidate, kind):
    value, paths = candidate
    resolved = t.navigation_path(value, kind, paths)
    assert resolved.exists()
    if kind != 'directory':
        value[kind+'_path'] = value['artifact_path']
        with pytest.raises(t.Refused, match='UNSAFE_DOCUMENT_TYPE'): t.navigation_path(value, kind, paths)


def test_secret_reference_is_never_opened_as_receipt(candidate):
    value, paths = candidate
    value['secret_ref'] = Path(value['receipt_path']).as_posix()
    t.validate_manifest(value)
    with pytest.raises(t.Refused, match='SECRET_REFERENCE_ONLY'): t.navigation_path(value, 'receipt', paths)


def approve(source, candidate):
    value, _ = candidate
    name = 'docs/governance/receipts/test.json'
    source.files[name] = raw(dict(test_target=value))
    board = t.json_bytes(source.files[a.BOARD])
    board['workbench'] = dict(current_test_id=value['test_id'], test_targets=[dict(
        test_id=value['test_id'], receipt_path=name, receipt_sha256=t.sha256(source.files[name]))])
    source.files[a.BOARD] = raw(board)


def test_receipt_to_view_and_cache_delete_rebuild(source, candidate, tmp_path):
    approve(source, candidate)
    view = a.load_view(source, candidate[1])
    assert view['active_target'] == candidate[0]
    assert view['actions'][0]['instruction'] == candidate[0]['user_action']
    output = tmp_path / 'projection'
    a.export_projection(view, output)
    before = {p.name:p.read_bytes() for p in output.iterdir()}
    for path in output.iterdir(): path.unlink()
    a.export_projection(a.load_view(source, candidate[1]), output)
    assert before == {p.name:p.read_bytes() for p in output.iterdir()}
    (output/'ACTIVE_TEST_TARGET.json').write_text('{"status":"CURRENT","launch_target":"bad"}')
    assert a.load_view(source, candidate[1])['active_target'] == candidate[0]


def test_tampered_pinned_receipt_and_duplicate_id_refused(source, candidate):
    approve(source, candidate)
    name = 'docs/governance/receipts/test.json'
    source.files[name] += b' '
    view = a.load_view(source, candidate[1])
    assert view['active_target'] is None and view['issues']
    approve(source, candidate)
    board = t.json_bytes(source.files[a.BOARD])
    board['workbench']['test_targets'] *= 2
    source.files[a.BOARD] = raw(board)
    assert a.load_view(source, candidate[1])['active_target'] is None


def test_local_uncommitted_current_bootstrap_is_not_authority(source, candidate, monkeypatch, tmp_path):
    path = tmp_path/a.TARGET; path.parent.mkdir(parents=True); path.write_bytes(raw(candidate[0]))
    monkeypatch.setattr(a, 'ROOT', tmp_path)
    assert a.load_view(source, candidate[1])['active_target'] is None


def test_receipt_secrets_not_projected_and_missing_fields_stay_waiting(source):
    secret = 'unique-fixture-private-value'
    receipt = dict(STATUS='USER_ACTION_REQUIRED_MINIMAL_LOGIN', title='Login '+secret,
                   password=secret, secret_ref='secret://fixture/password')
    value = a.receipt_to_manifest(receipt, 'MF-INCOMPLETE')
    assert value['status'] == 'WAITING_FOR_ARTIFACT'
    assert value['build_id'] is None and secret not in json.dumps(value)
    board = t.json_bytes(source.files[a.BOARD])
    board['password'] = secret; board['workers']['01']['objective'] = 'do ' + secret
    source.files[a.BOARD] = raw(board)
    assert secret not in json.dumps(a.load_view(source))


def test_invalid_json_errors_never_include_secret():
    with pytest.raises(t.Refused) as caught: t.json_bytes(b'{"password":"secret-canary", nope')
    assert 'secret-canary' not in str(caught.value)
    with pytest.raises(t.Refused): t.json_bytes(b'{"a":1,"a":2}')


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_ui_projection_navigation_secret_reference_and_no_state_mutation(app, source, candidate, monkeypatch):
    from tools.dev_workbench.window import Workbench
    from PySide6.QtGui import QDesktopServices
    approve(source, candidate)
    before = copy.deepcopy(source.files)
    window = Workbench(lambda:source, candidate[1], auto_load=False)
    window.show_view(a.load_view(source, candidate[1]))
    opens = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opens.append(url.toLocalFile()))
    try:
        window.navigate('receipt'); window.navigate('log')
        assert [Path(p) for p in opens] == [Path(candidate[0]['receipt_path']), Path(candidate[0]['log_path'])]
        assert 'SECURE_REFERENCE_ONLY' in window.account.text()
        assert 'secret://fixture/login' in window.account.text()
        assert window.launch_button.isEnabled()
        window.view['history'][0] = {**candidate[0], 'status':'SUPERSEDED'}
        window.history_notice(0, 0)
        assert '这是旧测试版本' in window.notice.text()
        assert source.files == before
        window.failure('ARTIFACT_HASH_MISMATCH')
        assert '这个文件不是当前指定测试版本' in window.notice.text()
    finally: window.close()


def test_actual_repo_projection_snapshot_is_read_only():
    source = a.RepoSource()
    before = source.git('status', '--porcelain=v1')
    view = a.load_view(source)
    assert view['generation'] == 'MAIN3'
    assert source.git('status', '--porcelain=v1') == before
    assert len(view['workers']) == 5

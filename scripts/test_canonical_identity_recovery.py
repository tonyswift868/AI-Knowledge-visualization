"""Canonical identity recovery with real signatures and inert transaction bytes."""
import base64

import pytest

from desktop.update_protocol import MANIFEST_PATH, canonical, digest, strict_json
from scripts import per_user_install as install
from scripts import per_user_update_core as core
from scripts import per_user_launcher as launcher
from scripts.gui_bootstrap_trust import CLOSURE, REQUIRED
from scripts.test_per_user_install import case, ready
from scripts.test_one_click_update_core import setup


@pytest.fixture
def recovery_case(case, monkeypatch):
    # Exercise the real GUI closure verifier without loading/executing these files.
    for name in REQUIRED:
        case[2].old_files.setdefault(name, b'MZ inert signed GUI closure')
    case[2].old_files[CLOSURE] = canonical(dict(
        schema='mentorflow.gui-bootstrap-closure.v1', files=sorted(REQUIRED)))
    base, root, feed, _ = case
    def publish(version, epoch):
        feed.publish(version, {**feed.old_files, 'UserRuntime/UserRuntime.exe': b'MZ updater fixture',
                               'mentorflow-version.json': canonical(dict(version=version, channel='stable'))})
        feed.signed['metadata_version'] = epoch
        feed.sign(feed.signed)
        source = base / ('recovery-source-' + version)
        for row in feed.signed['full_release']['assets']:
            path = source / row['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(feed.objects['/objects/' + row['sha256']])
        return source, feed.raw
    root, service = setup((base, root, feed, publish), monkeypatch)
    monkeypatch.setattr(install, 'require_app_stopped', lambda *_: None)
    monkeypatch.setattr(launcher, 'repair_update_shell',
                        lambda *_: pytest.fail('normal launch returned to recovery'))
    old = install.installed(root)
    try:
        yield root, service, old, case[2]
    finally:
        service.close()


def damage(root, kind):
    name = install.CURRENT if kind.endswith('current') else install.STATE
    path = root / name
    if kind.startswith('missing'):
        path.unlink()
    else:
        path.write_bytes(b'{corrupt identity')


def snapshot(root):
    return {p.relative_to(root).as_posix(): digest(p.read_bytes())
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('kind', [
    'missing_current', 'corrupt_current', 'missing_state', 'corrupt_state'])
def test_identity_loss_repairs_and_next_normal_launch_stays_healthy(recovery_case, kind):
    root, service, old, feed = recovery_case
    data = root / 'Data' / 'learning.json'
    data.parent.mkdir()
    data.write_bytes(b'original user learning data')
    damage(root, kind)
    before = snapshot(root)
    state = core.get_startup_update_state(root, check_feed=False)
    assert state['state'] == 'REPAIR_REQUIRED'
    assert not state['login_allowed'] and state['current'] == old.version
    assert snapshot(root) == before  # classification cannot publish identity
    feed.raw = old.envelope
    result = core.repair_installation(root, service=service)
    assert result['state'] == 'CURRENT_HEALTHY' and result['repair']
    assert result['status'] == 'HEALTHY' and result['health']['ready']
    assert install.installed(root).envelope == old.envelope
    install.verify_tree(root / 'App', old)
    assert strict_json((root / install.JOURNAL).read_bytes())['phase'] == 'HEALTHY'
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'HEALTHY'
    assert launcher.launch(root)['health']['ready']
    assert data.read_bytes() == b'original user learning data'


@pytest.mark.parametrize('candidate', [
    install.CURRENT, install.HIGHEST, 'previous/release.json', install.UPDATER_RELEASE])
def test_each_fixed_signed_candidate_can_restore_both_records(recovery_case, candidate):
    root, service, old, feed = recovery_case
    for name in (install.CURRENT, install.HIGHEST, install.UPDATER_RELEASE, install.STATE):
        (root / name).unlink()
    path = root / candidate
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(old.envelope)
    before = snapshot(root)
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'REPAIR_REQUIRED'
    assert snapshot(root) == before
    feed.raw = old.envelope
    assert core.repair_installation(root, service=service)['state'] == 'CURRENT_HEALTHY'
    assert launcher.launch(root)['health']['ready']


@pytest.mark.parametrize('attack', [
    'no_signed_candidate', 'unknown_key', 'bad_signature', 'modified_signed_manifest',
    'signed_wrong_version', 'unsigned_manifest', 'tampered_manifest', 'tampered_version',
    'tampered_runtime', 'tampered_runtime_binding', 'tampered_highest',
    'withdrawn_candidate', 'identity_directory'])
def test_unverifiable_identity_is_fatal_and_repair_leaves_files_untouched(recovery_case, attack):
    root, service, old, feed = recovery_case
    damage(root, 'missing_current')
    if attack == 'no_signed_candidate':
        (root / install.HIGHEST).unlink()
        (root / install.UPDATER_RELEASE).unlink()
    elif attack in ('unknown_key', 'bad_signature', 'modified_signed_manifest', 'signed_wrong_version', 'withdrawn_candidate'):
        doc = strict_json(old.envelope)
        if attack == 'unknown_key': doc['key_id'] = 'not-a-compiled-key'
        if attack == 'bad_signature': doc['signature'] = base64.b64encode(b'X' * 64).decode()
        if attack == 'modified_signed_manifest': doc['signed']['file_manifest']['version'] = '9.9.9'
        raw = canonical(doc)
        if attack == 'signed_wrong_version': raw = feed.raw  # authentic 1.4.1, App is 1.4.0
        if attack == 'withdrawn_candidate':
            doc['signed']['withdrawn'] = True
            raw = feed.sign(doc['signed'])
        (root / install.HIGHEST).write_bytes(raw)
        (root / install.UPDATER_RELEASE).unlink()
    elif attack == 'unsigned_manifest':
        (root / install.HIGHEST).write_bytes(canonical(dict(signed=old.signed)))
        (root / install.UPDATER_RELEASE).unlink()
        # The complete local manifest and all its listed files still exist.
    elif attack == 'tampered_manifest':
        (root / 'App' / MANIFEST_PATH).write_bytes(b'{}')
    elif attack == 'tampered_version':
        (root / 'App/mentorflow-version.json').write_bytes(canonical(dict(version='9.9.9')))
    elif attack == 'tampered_runtime':
        (root / 'Updater/UserRuntime.exe').write_bytes(b'untrusted runtime')
    elif attack == 'tampered_runtime_binding':
        (root / install.UPDATER_RELEASE).write_bytes(b'forged binding')
    elif attack == 'tampered_highest':
        (root / install.HIGHEST).write_bytes(b'forged replay witness')
    elif attack == 'identity_directory':
        (root / install.CURRENT).mkdir()
        (root / install.CURRENT / 'user-document').write_bytes(b'preserve')
    before = snapshot(root)
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'FATAL_UNRECOVERABLE'
    assert core.repair_installation(root, service=service)['state'] == 'FAILED_SECURITY'
    assert snapshot(root) == before
    assert not feed.requests


def test_signed_manifest_allows_reconstruction_of_other_damaged_payload(recovery_case):
    root, service, old, feed = recovery_case
    damage(root, 'corrupt_current')
    damage(root, 'corrupt_state')
    (root / 'App/MentorFlow.exe').write_bytes(b'corrupt App')
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'REPAIR_REQUIRED'
    feed.raw = old.envelope
    assert core.repair_installation(root, service=service)['state'] == 'CURRENT_HEALTHY'
    assert (root / 'previous/App/MentorFlow.exe').read_bytes() == b'corrupt App'
    assert launcher.launch(root)['health']['ready']


def test_old_pinned_updater_cannot_misidentify_new_app(recovery_case):
    root, service, old, feed = recovery_case
    assert core.repair_installation(root, service=service)['version'] == '1.4.1'
    latest = install.installed(root)
    assert (root / install.UPDATER_RELEASE).read_bytes() == old.envelope
    damage(root, 'corrupt_current')
    damage(root, 'missing_state')
    state = core.get_startup_update_state(root, check_feed=False)
    assert state['state'] == 'REPAIR_REQUIRED' and state['current'] == '1.4.1'
    assert core.bootstrap_legacy_identity(root).envelope == latest.envelope
    damage(root, 'missing_current')
    (root / install.HIGHEST).unlink()
    # previous and pinned Updater contain only 1.4.0; neither binds new App.
    before = snapshot(root)
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'FATAL_UNRECOVERABLE'
    assert core.repair_installation(root, service=service)['state'] == 'FAILED_SECURITY'
    assert snapshot(root) == before


def test_authentic_current_conflict_is_not_silently_rebound(recovery_case):
    root, service, old, feed = recovery_case
    damage(root, 'missing_state')
    (root / install.CURRENT).write_bytes(feed.raw)  # authentic 1.4.1, wrong App
    before = snapshot(root)
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'FATAL_UNRECOVERABLE'
    assert core.repair_installation(root, service=service)['state'] == 'FAILED_SECURITY'
    assert snapshot(root) == before


def test_signed_configuration_version_must_match_signed_release(recovery_case):
    root, service, old, feed = recovery_case
    files = {name: (root / 'App' / name).read_bytes() for name in old.rows if name != MANIFEST_PATH}
    files['mentorflow-version.json'] = canonical(dict(version='9.9.9'))
    feed.publish(old.version, files)
    damage(root, 'missing_current')
    (root / install.HIGHEST).write_bytes(feed.raw)
    (root / 'App/mentorflow-version.json').write_bytes(files['mentorflow-version.json'])
    (root / 'App' / MANIFEST_PATH).write_bytes(canonical(feed.signed['file_manifest']))
    before = snapshot(root)
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'FATAL_UNRECOVERABLE'
    assert core.repair_installation(root, service=service)['state'] == 'FAILED_SECURITY'
    assert snapshot(root) == before


def test_missing_state_does_not_reselect_retained_flat_inno_executable(recovery_case):
    root, service, old, feed = recovery_case
    (root / 'MentorFlow.exe').write_bytes(b'MZ retained original flat Inno executable')
    damage(root, 'missing_state')
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'REPAIR_REQUIRED'
    feed.raw = old.envelope
    assert core.repair_installation(root, service=service)['state'] == 'CURRENT_HEALTHY'
    assert install.app_path(root) == root / 'App'
    assert launcher.launch(root)['health']['ready']
    assert (root / 'MentorFlow.exe').read_bytes() == b'MZ retained original flat Inno executable'


@pytest.mark.parametrize('failure', ['VERIFIED', 'OLD_MOVED', 'NEW_ACTIVE', 'health'])
def test_identity_repair_preserves_transaction_rollback(recovery_case, monkeypatch, failure):
    root, service, old, feed = recovery_case
    damage(root, 'corrupt_current')
    damage(root, 'missing_state')
    before_app = snapshot(root / 'App')
    real_transact = install.transact
    def fail(phase):
        if phase == failure: raise RuntimeError('injected transaction interruption')
    def health(root, release):
        if failure == 'health' and release.version == '1.4.1':
            raise RuntimeError('inert target health failed')
        return ready(root, release)
    monkeypatch.setattr(install, 'transact', lambda *a, **kw: real_transact(*a, **kw, fault=fail, health=health))
    result = core.repair_installation(root, service=service)
    assert result['state'] == 'FAILED_RECOVERABLE'
    assert install.installed(root).envelope == old.envelope
    assert snapshot(root / 'App') == before_app
    assert strict_json((root / install.JOURNAL).read_bytes())['phase'] == 'ROLLED_BACK'
    assert (root / install.HIGHEST).read_bytes() == (old.envelope if failure == 'VERIFIED' else feed.raw)
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'HEALTHY'
    assert launcher.launch(root)['health']['ready']


def test_recovery_revalidates_identity_under_existing_writer_lock(recovery_case, monkeypatch):
    from desktop.update_download import InstallUpdateLock
    root, service, old, feed = recovery_case
    damage(root, 'missing_current')
    real_enter = InstallUpdateLock.__enter__
    def changed_before_lock(lock):
        result = real_enter(lock)
        if lock.root == root:
            (root / 'App' / MANIFEST_PATH).write_bytes(b'tampered after initial check')
        return result
    monkeypatch.setattr(InstallUpdateLock, '__enter__', changed_before_lock)
    assert core.repair_installation(root, service=service)['state'] == 'FAILED_SECURITY'
    assert not (root / install.CURRENT).exists()


def test_identity_publication_interruption_can_retry(recovery_case, monkeypatch):
    root, service, old, feed = recovery_case
    damage(root, 'missing_current')
    damage(root, 'missing_state')
    atomic = install.atomic
    def interrupted(path, data):
        if path == root / install.STATE: raise OSError('injected STATE publication failure')
        atomic(path, data)
    with monkeypatch.context() as change:
        change.setattr(install, 'atomic', interrupted)
        assert core.repair_installation(root, service=service)['state'] == 'FAILED_RECOVERABLE'
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'REPAIR_REQUIRED'
    feed.raw = old.envelope
    assert core.repair_installation(root, service=service)['state'] == 'CURRENT_HEALTHY'
    assert launcher.launch(root)['health']['ready']


def test_service_is_constructed_only_after_signed_identity_restoration(recovery_case, monkeypatch):
    root, service, old, feed = recovery_case
    damage(root, 'corrupt_current')
    damage(root, 'missing_state')
    calls = []
    def factory(path, *, repair=False):
        assert repair and install.installed(path).envelope == old.envelope
        calls.append(path)
        return service
    monkeypatch.setattr(core, 'one_click_service', factory)
    feed.raw = old.envelope
    assert core.repair_installation(root)['state'] == 'CURRENT_HEALTHY'
    assert calls == [root] and service._closed
    assert launcher.launch(root)['health']['ready']


@pytest.mark.parametrize('mode', ['offline', 'cancelled', 'downgrade', 'replayed_epoch'])
def test_identity_recovery_does_not_bypass_fresh_apply_admission(recovery_case, monkeypatch, mode):
    root, service, old, feed = recovery_case
    damage(root, 'missing_current')
    before_app = snapshot(root / 'App')
    highest = old.envelope
    if mode == 'offline':
        from desktop.update_protocol import RecoverableError
        def offline(*_): raise RecoverableError('offline')
        monkeypatch.setattr(service.cache.transport, 'fetch_metadata', offline)
    elif mode == 'replayed_epoch':
        highest = feed.raw  # authentic newer witness for a previously tried target
        (root / install.HIGHEST).write_bytes(highest)
        feed.raw = old.envelope
    elif mode == 'downgrade':
        doc = strict_json(old.envelope)['signed']
        doc['version'] = '1.3.0'
        feed.sign(doc)
    result = core.repair_installation(root, service=service,
                                     cancel=(lambda: True) if mode == 'cancelled' else None)
    assert result['state'] != 'CURRENT_HEALTHY'
    assert snapshot(root / 'App') == before_app
    assert (root / install.HIGHEST).read_bytes() == highest
    assert not (root / 'previous').exists()
    assert launcher.launch(root)['health']['ready']


def test_historical_identity_never_admits_an_expired_download(recovery_case):
    from datetime import datetime, timedelta, timezone
    root, service, old, feed = recovery_case
    signed = strict_json(old.envelope)['signed']
    signed['published_at'] = signed['issued_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    signed['expires_at'] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    expired = feed.sign(signed)
    damage(root, 'missing_current')
    (root / install.HIGHEST).write_bytes(expired)
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'REPAIR_REQUIRED'
    before_app = snapshot(root / 'App')
    assert core.repair_installation(root, service=service)['state'] == 'FAILED_SECURITY'
    assert snapshot(root / 'App') == before_app
    signed = strict_json(old.envelope)['signed']
    signed['metadata_version'] = 3
    feed.sign(signed)
    assert core.repair_installation(root, service=service)['state'] == 'CURRENT_HEALTHY'
    assert launcher.launch(root)['health']['ready']


@pytest.mark.parametrize('location', ['App', 'Updater', 'previous'])
def test_identity_recovery_refuses_directory_aliases(recovery_case, location):
    import _winapi
    root, service, old, feed = recovery_case
    damage(root, 'missing_current')
    original = root / location
    if not original.exists(): original.mkdir()
    retained = root / ('retained-' + location)
    original.rename(retained)
    _winapi.CreateJunction(str(retained), str(original))
    before = snapshot(root)
    assert core.get_startup_update_state(root, check_feed=False)['state'] == 'FATAL_UNRECOVERABLE'
    assert core.repair_installation(root, service=service)['state'] == 'FAILED_SECURITY'
    assert snapshot(root) == before

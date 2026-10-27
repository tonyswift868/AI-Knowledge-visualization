"""Ordinary-token filesystem/signature tests, no elevation or formal data."""
import os
from pathlib import Path
import tempfile
import pytest
from tests.refactor.delivery.test_delivery_mature_update import Feed
from refactor.desktop.delivery.update_protocol import canonical
from refactor.desktop.delivery.update_protocol import strict_json
from refactor.desktop.delivery.update_protocol import SecurityError
from refactor.desktop.delivery import per_user_install as engine


@pytest.fixture
def case():
    with tempfile.TemporaryDirectory(prefix='per-user-') as value:
        base = Path(value); feed = Feed(base)
        files = {**feed.old_files, 'UserRuntime/UserRuntime.exe': b'MZ updater fixture'}
        root = base / 'MentorFlow'
        def publish(version, epoch):
            source = base / ('source-' + version); source.mkdir()
            feed.publish(version, files); feed.signed['metadata_version'] = epoch
            feed.sign(feed.signed)
            for row in feed.signed['full_release']['assets']:
                path = source / row['path']; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(feed.objects['/objects/' + row['sha256']])
            return source, feed.raw
        try: yield base, root, feed, publish
        finally: feed.close()


def ready(root, release): return dict(ready=True, version=release.version)


def test_verified_failed_first_install_can_be_repaired_in_place(case, monkeypatch):
    """I06 review reproduction: inert signed files, never executes the App."""
    base, root, feed, publish = case
    monkeypatch.setattr(engine, 'keys', lambda: feed.keys)
    sibling = base / 'user-neighbor.txt'
    sibling.write_bytes(b'preserve-neighbor')
    source, envelope = publish('1.3.0', 1)
    def fail_after_activation(phase):
        if phase == 'NEW_ACTIVE': raise RuntimeError('injected first-install interruption')
    with pytest.raises(RuntimeError, match='injected first-install interruption'):
        engine.transact(root, source, envelope, health=ready, fault=fail_after_activation)
    assert (root / engine.HIGHEST).read_bytes() == envelope
    assert not engine.is_install(root)
    assert sibling.read_bytes() == b'preserve-neighbor'
    result = engine.install_per_user(base, source, envelope, health=ready)
    assert result['status'] == 'HEALTHY'
    assert engine.installed(root).version == '1.3.0'
    assert sibling.read_bytes() == b'preserve-neighbor'


def initial_failure(case, monkeypatch):
    _, root, feed, publish = case
    monkeypatch.setattr(engine, 'keys', lambda: feed.keys)
    source, raw = publish('1.3.0', 1)
    def fail(phase):
        if phase == 'NEW_ACTIVE': raise RuntimeError('initial fault')
    with pytest.raises(RuntimeError, match='initial fault'):
        engine.transact(root, source, raw, health=ready, fault=fail)
    return source, raw


def file_snapshot(root):
    return {p.relative_to(root).as_posix(): engine.digest(p.read_bytes())
            for p in root.rglob('*') if p.is_file()}


def test_initial_retry_keeps_all_prior_evidence_and_user_files(case, monkeypatch):
    base, root, _, _ = case
    source, raw = initial_failure(case, monkeypatch)
    (root / 'user-data').mkdir()
    (root / 'user-data/plans.json').write_bytes(b'not installer-owned')
    (root / 'neighbor.txt').write_bytes(b'keep this too')
    retained = file_snapshot(root)
    assert engine.install_per_user(base, source, raw, health=ready)['status'] == 'HEALTHY'
    after = file_snapshot(root)
    for name, value in retained.items():
        # Housekeeping now appends its own evidence; user files and previous
        # failure payloads still stay byte-for-byte unchanged until startup.
        if name not in (engine.JOURNAL, 'Data/logs/mentorflow.log',
                        'Data/logs/launcher-startup.jsonl'):
            assert after[name] == value


def test_initial_retry_can_fail_again_then_repair(case, monkeypatch):
    base, root, _, _ = case
    source, raw = initial_failure(case, monkeypatch)
    retained = {p.name for p in root.glob('failed-*')}
    def bad_health(*_): raise RuntimeError('retry health failed')
    with pytest.raises(RuntimeError, match='retry health failed'):
        engine.install_per_user(base, source, raw, health=bad_health)
    assert not engine.is_install(root)
    assert len(list(root.glob('failed-*'))) == 2
    assert engine.install_per_user(base, source, raw, health=ready)['status'] == 'HEALTHY'
    assert retained <= {p.name for p in root.glob('failed-*')}


@pytest.mark.parametrize('location', ['App', 'staging'])
def test_verified_interrupted_initial_tree_without_state_can_retry(case, monkeypatch, location):
    base, root, _, _ = case
    source, raw = initial_failure(case, monkeypatch)
    journal = strict_json((root / engine.JOURNAL).read_bytes())
    # Model process interruption instead of the caught-error quarantine branch.
    destination = root / 'App' if location == 'App' else root / ('staging-' + journal['attempt']) / 'App'
    # Caught failures now delete this attempt's staging. Recreate the parent
    # to model an interruption which bypasses the finally cleanup entirely.
    destination.parent.mkdir(parents=True, exist_ok=True)
    next(root.glob('failed-*')).rename(destination)
    (root / engine.JOURNAL).write_bytes(canonical({**journal, 'phase': 'VERIFIED'}))
    if location == 'App': (root / engine.CURRENT).write_bytes(raw)
    assert not engine.is_install(root)
    before = file_snapshot(destination)
    assert engine.install_per_user(base, source, raw, health=ready)['status'] == 'HEALTHY'
    retained = next(root.glob('failed-*')) if location == 'App' else destination
    assert file_snapshot(retained) == before


@pytest.mark.parametrize('attack', [
    'highest_signature', 'wrong_keys', 'journal_base', 'journal_target', 'journal_path',
    'journal_extra', 'local_app', 'local_updater', 'local_evidence', 'input_signature',
    'input_source', 'different_release', 'expired_release', 'withdrawn_release', 'unknown_lock', 'unknown_state'])
def test_initial_retry_untrusted_inputs_fail_without_overwrite(case, monkeypatch, attack):
    base, root, feed, publish = case
    source, raw = initial_failure(case, monkeypatch)
    if attack == 'highest_signature':
        (root / engine.HIGHEST).write_bytes(raw.replace(b'Small frontend fix', b'unsigned contents!'))
    if attack == 'wrong_keys': monkeypatch.setattr(engine, 'keys', lambda: {})
    if attack.startswith('journal_'):
        doc = strict_json((root / engine.JOURNAL).read_bytes())
        if attack == 'journal_base': doc['base'] = '1.2.0'
        if attack == 'journal_target': doc['target'] = '9.9.9'
        if attack == 'journal_path': doc['attempt'] = '../user-data'
        if attack == 'journal_extra': doc['owned_paths'] = ['../user-data']
        (root / engine.JOURNAL).write_bytes(canonical(doc))
    if attack == 'local_app':
        (root / 'App').mkdir(); (root / 'App/user-document').write_bytes(b'do not move me')
    if attack == 'local_updater': (root / 'Updater/UserRuntime.exe').write_bytes(b'foreign executable')
    if attack == 'local_evidence':
        (next(root.glob('failed-*')) / 'MentorFlow.exe').write_bytes(b'forged app')
    if attack == 'input_signature': raw = raw.replace(b'Small frontend fix', b'unsigned contents!')
    if attack == 'input_source': (source / 'MentorFlow.exe').write_bytes(b'forged source')
    if attack == 'different_release': source, raw = publish('1.3.1', 2)
    if attack == 'withdrawn_release':
        doc = strict_json(raw)['signed']; doc['withdrawn'] = True; raw = feed.sign(doc)
    if attack == 'expired_release':
        from datetime import datetime, timedelta, timezone
        doc = strict_json(raw)['signed']
        doc['issued_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        doc['expires_at'] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        raw = feed.sign(doc)
    if attack == 'unknown_lock': (root / 'operation.lock').write_bytes(b'user lock contents')
    if attack == 'unknown_state':
        (root / engine.STATE).mkdir(); (root / engine.STATE / 'user-file').write_bytes(b'preserve')
    before = file_snapshot(root)
    with pytest.raises(SecurityError): engine.install_per_user(base, source, raw, health=ready)
    assert file_snapshot(root) == before


@pytest.mark.parametrize('signed_hint', [False, True])
def test_forged_initial_journal_is_not_ownership(case, monkeypatch, signed_hint):
    base, root, feed, publish = case
    monkeypatch.setattr(engine, 'keys', lambda: feed.keys)
    source, raw = publish('1.3.0', 1)
    root.mkdir(); (root / 'user-document').write_bytes(b'untouched')
    (root / engine.JOURNAL).write_bytes(canonical(dict(
        schema='mentorflow.per-user-transaction.v1', attempt='a'*32,
        phase='INITIAL_FAILED', target='1.3.0', base=None, base_manifest=None)))
    if signed_hint: (root / engine.HIGHEST).write_bytes(raw)
    before = file_snapshot(root)
    with pytest.raises(SecurityError): engine.validate_target(root)
    with pytest.raises(SecurityError): engine.install_per_user(base, source, raw, health=ready)
    assert file_snapshot(root) == before


@pytest.mark.parametrize('location', ['root', 'App', 'Updater', 'failed'])
def test_initial_retry_reparse_refused(case, monkeypatch, location):
    import _winapi
    base, root, _, _ = case
    source, raw = initial_failure(case, monkeypatch)
    outside = base / 'outside'; outside.mkdir()
    (outside / 'sentinel').write_bytes(b'do not traverse or modify')
    install_parent = base
    if location == 'root':
        install_parent = base / 'root-alias-parent'; install_parent.mkdir()
        junction = install_parent / 'MentorFlow'
    elif location == 'Updater':
        (root / 'Updater').rename(root / 'retained-updater'); junction = root / 'Updater'
    elif location == 'failed':
        junction = next(root.glob('failed-*')); junction.rename(root / 'retained-failed')
    else: junction = root / 'App'
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        before = file_snapshot(outside)
        with pytest.raises(SecurityError, match='reparse'):
            engine.install_per_user(install_parent, source, raw, health=ready)
        assert file_snapshot(outside) == before
    finally:
        # Remove only this exact test-created junction, never its destination.
        junction.rmdir()


@pytest.mark.parametrize('location', ['App', 'Updater'])
def test_initial_retry_rechecks_mutable_locations_before_commit(case, monkeypatch, location):
    base, root, _, _ = case
    source, raw = initial_failure(case, monkeypatch)
    def change_after_copy():
        if location == 'App': (root / 'App').mkdir()
        (root / location / 'user-document').write_bytes(b'preserve even on rejection')
    with pytest.raises(SecurityError):
        engine.install_per_user(base, source, raw, health=ready, before_swap=change_after_copy)
    assert not engine.is_install(root)
    assert (root / location / 'user-document').read_bytes() == b'preserve even on rejection'


def test_existing_user_parent_install_update_and_previous(case):
    base, root, feed, publish = case
    source, raw = publish('1.3.0', 1)
    assert engine.transact(root, source, raw, public_keys=feed.keys, health=ready)['install_uac_count'] == 0
    assert root.parent == base and engine.installed(root, feed.keys).version == '1.3.0'
    source, raw = publish('1.3.1', 2)
    assert engine.transact(root, source, raw, public_keys=feed.keys, health=ready)['update_uac_count'] == 0
    assert engine.installed(root, feed.keys).version == '1.3.1'
    assert (root / 'previous/App/MentorFlow.exe').exists()
    assert engine.historical(engine.read(root / 'previous/release.json'), feed.keys).version == '1.3.0'


@pytest.mark.parametrize('phase', ['VERIFIED', 'OLD_MOVED', 'NEW_ACTIVE'])
def test_fault_retains_previous_without_acl(case, phase):
    _, root, feed, publish = case
    source, raw = publish('1.3.0', 1); engine.transact(root, source, raw, public_keys=feed.keys, health=ready)
    source, raw = publish('1.3.1', 2)
    def fail(value):
        if value == phase: raise RuntimeError('injected failure')
    with pytest.raises(RuntimeError): engine.transact(root, source, raw, public_keys=feed.keys, health=ready, fault=fail)
    assert engine.installed(root, feed.keys).version == '1.3.0'
    assert (root / 'App/MentorFlow.exe').read_bytes() == feed.old_files['MentorFlow.exe']


def test_failed_health_restores_previous(case):
    _, root, feed, publish = case
    source, raw = publish('1.3.0', 1); engine.transact(root, source, raw, public_keys=feed.keys, health=ready)
    source, raw = publish('1.3.1', 2); calls = []
    def health(root, release):
        calls.append(release.version)
        if release.version == '1.3.1': raise RuntimeError('health failed')
        return ready(root, release)
    with pytest.raises(RuntimeError): engine.transact(root, source, raw, public_keys=feed.keys, health=health)
    assert calls == ['1.3.1', '1.3.0']
    assert engine.installed(root, feed.keys).version == '1.3.0'


@pytest.mark.parametrize('attack', ['metadata', 'wrong_app', 'corrupt', 'downgrade', 'replay', 'equivocation', 'staging_after_copy'])
def test_untrusted_content_rejected_without_replacing_current(case, attack, monkeypatch):
    _, root, feed, publish = case
    source, raw = publish('1.3.0', 1); engine.transact(root, source, raw, public_keys=feed.keys, health=ready)
    source, raw = publish('1.3.1', 2)
    if attack == 'metadata': raw = raw.replace(b'Small frontend fix', b'Small frontend fiy')
    if attack == 'wrong_app': feed.signed['app_id'] = 'Other'; raw = feed.sign(feed.signed)
    if attack == 'corrupt': (source / 'MentorFlow.exe').write_bytes(b'bad')
    if attack in ('downgrade', 'replay'):
        raw = (root / engine.CURRENT).read_bytes()
        if attack == 'downgrade':
            doc = strict_json(raw)['signed']; doc['version'] = '1.0.0'; raw = feed.sign(doc)
    if attack == 'equivocation': feed.signed['metadata_version'] = 1; raw = feed.sign(feed.signed)
    if attack == 'staging_after_copy':
        original = engine._copy
        def copy(*args):
            original(*args); (args[1] / 'MentorFlow.exe').write_bytes(b'changed after stream')
        monkeypatch.setattr(engine, '_copy', copy)
    with pytest.raises(SecurityError): engine.transact(root, source, raw, public_keys=feed.keys, health=ready)
    assert engine.installed(root, feed.keys).version == '1.3.0'


def test_state_receipt_cannot_supply_release_keys(case):
    _, root, feed, publish = case
    source, raw = publish('1.3.0', 1); engine.transact(root, source, raw, public_keys=feed.keys, health=ready)
    doc = strict_json((root / engine.STATE).read_bytes()); doc['public_keys'] = {'fixture':'untrusted'}
    (root / engine.STATE).write_bytes(canonical(doc))
    with pytest.raises(SecurityError): engine.installed(root, {})


def test_missing_parent_and_unknown_target_no_mutation(case):
    base, root, feed, publish = case
    source, raw = publish('1.3.0', 1)
    with pytest.raises(ValueError): engine.transact(base / 'missing/parent/MentorFlow', source, raw, public_keys=feed.keys, health=ready)
    assert not (base / 'missing').exists()
    root.mkdir(); (root / 'sentinel').write_bytes(b'untouched')
    with pytest.raises(SecurityError): engine.transact(root, source, raw, public_keys=feed.keys, health=ready)
    assert (root / 'sentinel').read_bytes() == b'untouched'


def test_protected_parent_policy_still_rejects_user_parent(case):
    _, root, _, _ = case
    from refactor.desktop.delivery.windows_install_trust import assert_safe_parents
    from refactor.desktop.delivery.windows_install_trust import RepairRequired
    engine.target(root, probe=True)
    with pytest.raises(RepairRequired): assert_safe_parents(root)


def test_builder_reads_literal_keys_not_config_or_executable_python():
    from refactor.desktop.packaging.authority import literal_authority
    code=compile("APP_ID='MentorFlow'\nPUBLIC_KEYS={'release':b'x'*32}\n",'<fixture>','exec')
    assert literal_authority(code)=={'APP_ID':'MentorFlow','PUBLIC_KEYS':{'release':b'x'*32}}
    with pytest.raises(ValueError):
        literal_authority(compile("APP_ID='MentorFlow'\nPUBLIC_KEYS=load_downloaded_config()\n",'<fixture>','exec'))


def test_old_previous_is_not_this_transaction_recovery_base(case):
    _, root, feed, publish = case
    for version,epoch in [('1.3.0',1),('1.3.1',2)]:
        source,raw=publish(version,epoch)
        engine.transact(root,source,raw,public_keys=feed.keys,health=ready)
    current=engine.installed(root,feed.keys)
    journal=dict(schema='mentorflow.per-user-transaction.v1',phase='VERIFIED',target='1.3.2',
                 base='1.3.1',base_manifest=engine.digest(current.manifest_bytes))
    (root/engine.JOURNAL).write_bytes(canonical(journal))
    assert engine.recover(root,feed.keys)
    assert engine.installed(root,feed.keys).version=='1.3.1'

"""Ordinary-token filesystem/signature tests, no elevation or formal data."""
import os
from pathlib import Path
import tempfile
import pytest
from desktop.tests.test_mature_update import Feed
from desktop.update_protocol import canonical, strict_json, SecurityError
from scripts import per_user_install as engine


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
    from scripts.windows_install_trust import assert_safe_parents, RepairRequired
    engine.target(root, probe=True)
    with pytest.raises(RepairRequired): assert_safe_parents(root)


def test_builder_reads_literal_keys_not_config_or_executable_python():
    from scripts.build_signed_per_user_installer import literal_authority
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

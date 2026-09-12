"""Signed publisher -> verifier -> existing cache/startup/planner floor contract."""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from desktop import update_protocol as protocol
from desktop.update_protocol import canonical, strict_json, verify_release, SecurityError
from desktop.update_service import MatureUpdateService, read_release_witness
from desktop.update_download import HTTPTransport
from scripts import build_release as builder, per_user_install as install, per_user_update_core as core
from scripts.test_build_release import package, serving
from scripts.test_canonical_identity_recovery import case, recovery_case


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(builder.Ed25519PrivateKey, 'generate', lambda: key)
    keys = {'ephemeral-development': key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}
    output = tmp_path / 'feed'
    notes = tmp_path / 'notes.txt'
    notes.write_text('Inert signed mandatory floor regression', encoding='utf-8')
    with serving(output) as origin:
        def publish(version, *, mandatory=False, minimum=None, channel='stable', base=None):
            source = package(tmp_path / (channel + '-' + version), version, target=True)
            args = builder.parser().parse_args([
                '--target', str(source), '--output', str(output), '--version', version,
                '--channel', channel, '--notes', str(notes), '--origin', origin,
                '--source-sha', 'a'*40, '--tree-sha', 'b'*40, '--development-signing'])
            args.mandatory, args.mandatory_reason = mandatory, 'security' if mandatory else ''
            args.min_supported_version, args.base = minimum, base
            receipt = builder.build(args)
            raw = (output / (channel + '.json')).read_bytes()
            return verify_release(raw, keys, current='0.0.0', channel=channel), args, receipt
        yield publish, output, keys, key, origin


@pytest.mark.parametrize('current,latest,expected,floor', [
    ('1.0.0', '1.1.0', 'OPTIONAL_UPDATE', '1.0.0'),  # A
    ('1.0.0', '1.2.0', 'MANDATORY_UPDATE', '1.2.0'),  # B
    ('1.0.0', '1.4.0', 'MANDATORY_UPDATE', '1.2.0'),  # C
    ('1.1.0', '1.4.0', 'MANDATORY_UPDATE', '1.2.0'),  # D
    ('1.2.0', '1.4.0', 'OPTIONAL_UPDATE', '1.2.0'),   # E
    ('1.3.0', '1.4.0', 'OPTIONAL_UPDATE', '1.2.0'),   # F
    ('1.4.0', '1.6.0', 'MANDATORY_UPDATE', '1.5.0'),  # G
    ('1.4.0', '1.4.0', 'HEALTHY', '1.2.0'),
])
def test_published_history_always_classifies_and_targets_latest(publisher, current, latest, expected, floor):
    publish, *_ = publisher
    for minor in range(1, int(latest.split('.')[1]) + 1):
        release, _, _ = publish(f'1.{minor}.0', mandatory=minor in (2, 5))
    assert release.signed['min_supported_version'] == floor
    assert protocol.startup_version_policy(release, current) == expected
    assert protocol.make_plan(release, None, current)['latest'] == latest


def test_publisher_refuses_floor_downgrade_and_inherits_without_pointers(publisher):
    publish, output, *_ = publisher
    mandatory, _, _ = publish('1.2.0', mandatory=True)
    with pytest.raises(ValueError, match='mandatory floor downgrade'):
        publish('1.3.0', minimum='1.0.0')
    assert (output / 'stable.json').read_bytes() == mandatory.envelope
    (output / 'stable.json').unlink()
    (output / '.release-state/stable.json').unlink()
    optional, args, _ = publish('1.4.0')
    assert optional.signed['min_supported_version'] == '1.2.0'
    assert builder.build(args)['status'] == 'REUSED'


def test_publisher_stable_beta_floors_are_independent(publisher):
    publish, output, *_ = publisher
    stable, _, _ = publish('1.5.0', mandatory=True)
    beta, _, _ = publish('2.0.0-beta.1', channel='beta', mandatory=True)
    later, _, _ = publish('2.1.0-beta.1', channel='beta')
    assert later.signed['min_supported_version'] == beta.version
    assert (output / 'stable.json').read_bytes() == stable.envelope
    stable, _, _ = publish('1.6.0')
    assert stable.signed['min_supported_version'] == '1.5.0'


@pytest.mark.parametrize('delta', [True, False])
def test_mandatory_direct_download_to_latest_uses_declared_bases(publisher, tmp_path, delta):
    publish, output, keys, _, origin = publisher
    base = package(tmp_path / 'installed-base', '1.0.0')
    publish('1.2.0', mandatory=True)
    latest, _, _ = publish('1.4.0', base=base if delta else None)
    root = tmp_path / 'install'
    import shutil
    shutil.copytree(base, root / 'App')
    client = MatureUpdateService(install_root=root, cache_root=tmp_path / 'cache',
        metadata_url=origin + '/stable.json', public_keys=keys, current='1.0.0',
        transport=HTTPTransport(origin, fixture_http=True))
    try:
        result = client.check()
        assert protocol.startup_version_policy(client.release, client.current) == 'MANDATORY_UPDATE'
        assert result['latest'] == '1.4.0'
        assert result['update_path'] == ('FILE_LEVEL_INCREMENTAL' if delta else 'FULL_FALLBACK')
        assert client.download()['state'] == 'READY_TO_APPLY'
        assert (client.staged / protocol.MANIFEST_PATH).read_bytes() == latest.manifest_bytes
        assert strict_json((client.staged / 'mentorflow-version.json').read_bytes())['version'] == '1.4.0'
        assert strict_json((root / 'App/mentorflow-version.json').read_bytes())['version'] == '1.0.0'
    finally:
        client.close()


def new_policy(recovery_case, *, floor='1.5.0', channel='stable', mandatory=False):
    root, service, old, feed = recovery_case
    files = {name: (root / 'App' / name).read_bytes() for name in old.rows if name != protocol.MANIFEST_PATH}
    files['mentorflow-version.json'] = canonical(dict(version='1.6.0', channel=channel))
    feed.publish('1.6.0', files)
    feed.signed.update(min_supported_version=floor, metadata_version=3, channel=channel,
                       mandatory=mandatory, mandatory_reason='security' if mandatory else '', base_versions=['1.4.0'])
    return feed.sign(feed.signed)


def restart(recovery_case, *, cache_root=None, channel='stable'):
    root, service, old, feed = recovery_case
    return MatureUpdateService(install_root=root, cache_root=cache_root or root.parent / 'cache',
        metadata_url=feed.origin + '/stable.json', public_keys=feed.keys, current=old.version,
        channel=channel, transport=HTTPTransport(feed.origin, fixture_http=True))


@pytest.mark.parametrize('failure', ['offline', 'timeout', 'server_error', 'expired', 'withdrawn'])
def test_known_mandatory_gate_survives_restart_and_feed_failure(recovery_case, monkeypatch, failure):
    root, first, old, feed = recovery_case
    raw = new_policy(recovery_case)
    learned = core.get_startup_update_state(root, service=first)
    assert learned['state'] == 'MANDATORY_UPDATE' and learned['target'] == '1.6.0'
    first.close()
    second = restart(recovery_case)
    if failure in ('offline', 'timeout', 'server_error'):
        errors = {'offline': OSError('offline'), 'timeout': TimeoutError('timeout'),
                  'server_error': protocol.RecoverableError('server unavailable')}
        def fail(*_): raise errors[failure]
        monkeypatch.setattr(second.cache.transport, 'fetch_metadata', fail)
    elif failure == 'expired':
        class Later(datetime):
            @classmethod
            def now(cls, tz=None): return datetime.now(tz) + timedelta(days=10)
        monkeypatch.setattr(protocol, 'datetime', Later)
    elif failure == 'withdrawn':
        feed.signed.update(withdrawn=True, metadata_version=4)
        feed.sign(feed.signed)
    try:
        state = core.get_startup_update_state(root, service=second)
        assert state['state'] == 'MANDATORY_UPDATE' and not state['login_allowed']
        assert state['target'] == '1.6.0' and state['mandatory_floor'] == '1.5.0'
        assert core.get_startup_update_state(root, service=second, check_feed=False)['state'] == 'MANDATORY_UPDATE'
        assert install.installed(root).version == old.version
    finally:
        second.close()


def test_default_cache_policy_gate_needs_no_feed_configuration(recovery_case, monkeypatch):
    root, _, old, feed = recovery_case
    local = root.parent / 'local'
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    client = restart(recovery_case, cache_root=local / install.configured().app_id / 'Updates')
    new_policy(recovery_case)
    assert core.get_startup_update_state(root, service=client)['state'] == 'MANDATORY_UPDATE'
    client.close()
    def no_feed(*_): raise core.NoFeedConfigured('no feed')
    monkeypatch.setattr(core, 'one_click_service', no_feed)
    for check_feed in (False, True):
        state = core.get_startup_update_state(root, check_feed=check_feed)
        assert state['state'] == 'MANDATORY_UPDATE' and state['target'] == '1.6.0'


@pytest.mark.parametrize('learn_optional', [False, True])
def test_no_mandatory_evidence_does_not_manufacture_a_gate(recovery_case, monkeypatch, learn_optional):
    root, client, old, feed = recovery_case
    if learn_optional:
        new_policy(recovery_case, floor='1.0.0')
        assert core.get_startup_update_state(root, service=client)['state'] == 'OPTIONAL_UPDATE'
    def offline(*_): raise OSError('offline')
    monkeypatch.setattr(client.cache.transport, 'fetch_metadata', offline)
    result = core.get_startup_update_state(root, service=client)
    assert result['state'] == 'HEALTHY' and result['login_allowed']
    assert result['feed'] == 'UNAVAILABLE_RECOVERABLE'


def test_verifier_service_and_apply_refuse_newer_epoch_lower_floor(recovery_case):
    root, client, old, feed = recovery_case
    known = new_policy(recovery_case)
    assert client.check()['state'] == 'UPDATE_AVAILABLE'
    feed.signed.update(metadata_version=4, min_supported_version='1.0.0')
    downgrade = feed.sign(feed.signed)
    with pytest.raises(SecurityError, match='floor downgrade'):
        verify_release(downgrade, feed.keys, current=old.version, previous_envelope=known)
    assert client.check()['state'] == 'FAILED_SECURITY'
    assert read_release_witness(client.cache.root, feed.keys, channel='stable').envelope == known
    (root / install.HIGHEST).write_bytes(known)
    with pytest.raises(SecurityError, match='floor downgrade'):
        install.admit(root, downgrade, feed.keys)
    assert core.get_startup_update_state(root, service=client)['state'] == 'MANDATORY_UPDATE'


@pytest.mark.parametrize('attack', ['unsigned_status', 'tampered_witness', 'foreign_channel'])
def test_unsigned_local_state_cannot_authorize_mandatory_policy(recovery_case, attack):
    root, client, old, feed = recovery_case
    raw = new_policy(recovery_case)
    if attack == 'unsigned_status':
        (client.cache.root / 'state.json').write_bytes(canonical(dict(mandatory=True, min_supported_version='9.9.9')))
    else:
        if attack == 'tampered_witness':
            doc = strict_json(raw)
            doc['signed']['min_supported_version'] = '9.9.9'
            raw = canonical(doc)
        else:
            raw = new_policy(recovery_case, channel='beta')
        (client.cache.root / 'highest-metadata-stable.json').write_bytes(raw)
    state = core.get_startup_update_state(root, service=client, check_feed=False)
    assert state['state'] == ('HEALTHY' if attack == 'unsigned_status' else 'REPAIR_REQUIRED')
    assert state['state'] != 'MANDATORY_UPDATE'


def test_client_stable_beta_witnesses_are_isolated(recovery_case):
    root, stable, old, feed = recovery_case
    new_policy(recovery_case)
    assert stable.check()['state'] == 'UPDATE_AVAILABLE'
    beta = restart(recovery_case, channel='beta')
    try:
        new_policy(recovery_case, floor='1.0.0', channel='beta')
        assert beta.check()['state'] == 'UPDATE_AVAILABLE'
        saved_beta = read_release_witness(beta.cache.root, feed.keys, channel='beta')
        assert protocol.startup_version_policy(saved_beta, old.version) == 'OPTIONAL_UPDATE'
        assert protocol.mandatory_floor(read_release_witness(stable.cache.root, feed.keys, channel='stable')) == '1.5.0'
    finally:
        beta.close()


def test_legacy_mandatory_flag_also_establishes_a_persistent_floor(recovery_case):
    root, client, old, feed = recovery_case
    known = new_policy(recovery_case, floor='1.0.0', mandatory=True)
    assert protocol.mandatory_floor(verify_release(known, feed.keys, current=old.version)) == '1.6.0'
    assert client.check()['state'] == 'UPDATE_AVAILABLE'
    feed.signed.update(mandatory=False, mandatory_reason='', metadata_version=4)
    raw = feed.sign(feed.signed)
    with pytest.raises(SecurityError, match='floor downgrade'):
        verify_release(raw, feed.keys, current=old.version, previous_envelope=known)


@pytest.mark.parametrize('identity_damaged', [False, True])
def test_successful_latest_update_satisfies_floor_and_next_launch_is_healthy(recovery_case, identity_damaged):
    root, client, old, feed = recovery_case
    new_policy(recovery_case)
    assert core.get_startup_update_state(root, service=client)['state'] == 'MANDATORY_UPDATE'
    if identity_damaged:
        (root / install.CURRENT).write_bytes(b'corrupt identity')
        (root / install.STATE).unlink()
    user_data = root / 'Data/learning.json'
    user_data.parent.mkdir()
    user_data.write_bytes(b'preserved learning data')
    result = core.repair_installation(root, service=client)
    assert result['state'] == 'CURRENT_HEALTHY' and result['version'] == '1.6.0'
    assert core.get_startup_update_state(root, service=client, check_feed=False)['state'] == 'HEALTHY'
    assert user_data.read_bytes() == b'preserved learning data'


def test_bad_latest_rolls_back_but_does_not_clear_the_mandatory_gate(recovery_case, monkeypatch):
    root, client, old, feed = recovery_case
    new_policy(recovery_case)
    def health(root, release):
        if release.version == '1.6.0': raise RuntimeError('inert latest failed health')
        return dict(ready=True, version=release.version)
    monkeypatch.setattr(install, 'launch_health', health)
    assert core.repair_installation(root, service=client)['state'] == 'FAILED_RECOVERABLE'
    assert install.installed(root).version == old.version
    state = core.get_startup_update_state(root, service=client, check_feed=False)
    assert state['state'] == 'MANDATORY_UPDATE' and not state['login_allowed']


def test_protected_apply_witness_also_rejects_floor_regression(recovery_case, monkeypatch):
    from scripts import windows_update_apply as protected
    root, client, old, feed = recovery_case
    known = new_policy(recovery_case)
    path = root / protected.tx.STAGING / protected.tx.UPDATE_METADATA / 'stable.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(known)
    monkeypatch.setattr(protected, 'PRODUCTION_RELEASE_KEYS', feed.keys)
    monkeypatch.setattr(protected.tx, 'assert_no_alias', lambda *_: None)
    monkeypatch.setattr(protected.trust, 'assert_protected', lambda *_: None)
    feed.signed.update(metadata_version=4, min_supported_version='1.0.0')
    lowered = verify_release(feed.sign(feed.signed), feed.keys, current=old.version)
    with pytest.raises(SecurityError, match='floor downgrade'):
        protected.protected_witness(root, 'stable', lowered)
    assert path.read_bytes() == known


def test_mandatory_floor_cannot_exceed_signed_target(recovery_case):
    root, client, old, feed = recovery_case
    raw = new_policy(recovery_case, floor='1.7.0')
    with pytest.raises(SecurityError, match='floor exceeds'):
        verify_release(raw, feed.keys, current=old.version)


def test_future_signed_policy_is_not_past_fresh_evidence(recovery_case):
    root, client, old, feed = recovery_case
    new_policy(recovery_case)
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    feed.signed.update(issued_at=tomorrow.isoformat(), published_at=tomorrow.isoformat(),
                       expires_at=(tomorrow + timedelta(days=7)).isoformat())
    raw = feed.sign(feed.signed)
    (client.cache.root / 'highest-metadata-stable.json').write_bytes(raw)
    assert core.get_startup_update_state(root, service=client, check_feed=False)['state'] == 'REPAIR_REQUIRED'


def test_beta_mandatory_policy_cannot_block_optional_stable_startup(recovery_case):
    root, stable, old, feed = recovery_case
    new_policy(recovery_case, floor='1.0.0')
    assert stable.check()['state'] == 'UPDATE_AVAILABLE'
    beta = restart(recovery_case, channel='beta')
    try:
        new_policy(recovery_case, floor='1.6.0', channel='beta', mandatory=True)
        assert beta.check()['state'] == 'UPDATE_AVAILABLE'
        assert core.get_startup_update_state(root, service=stable, check_feed=False)['state'] == 'HEALTHY'
    finally:
        beta.close()


def test_publisher_inherits_legacy_mandatory_flag_without_explicit_floor(publisher):
    import base64
    publish, output, keys, key, _ = publisher
    release, _, _ = publish('1.2.0', mandatory=True)
    document = strict_json(release.envelope)
    document['signed']['min_supported_version'] = '1.0.0'
    document['signature'] = base64.b64encode(key.sign(canonical(document['signed']))).decode()
    raw = canonical(document)
    for path in ('stable.json', '.release-state/stable.json', '1.2.0/stable/release.json'):
        (output / path).write_bytes(raw)
    later, _, _ = publish('1.3.0')
    assert later.signed['min_supported_version'] == '1.2.0'


@pytest.mark.parametrize('point', ['download', 'apply'])
def test_cached_floor_is_rechecked_at_download_and_apply(recovery_case, monkeypatch, point):
    root, client, old, feed = recovery_case
    new_policy(recovery_case, floor='1.0.0')
    assert client.check()['state'] == 'UPDATE_AVAILABLE'
    if point == 'apply':
        assert client.download()['state'] == 'READY_TO_APPLY'
        client.apply_bridge = lambda *_: pytest.fail('lowered policy must never reach transaction')
    # Another verified release at a lower epoch can still carry a stronger floor.
    # The incoming release must satisfy BOTH the epoch and mandatory floor checks.
    feed.signed.update(metadata_version=2, min_supported_version='1.5.0')
    (client.cache.root / 'highest-metadata-stable.json').write_bytes(feed.sign(feed.signed))
    if point == 'download':
        assert client.download()['state'] == 'FAILED_SECURITY'
        assert client.staged is None
    else:
        # Return the original fresh, higher-epoch metadata during apply refresh.
        feed.raw = client.release.envelope
        with pytest.raises(SecurityError, match='floor downgrade'):
            client.apply_and_restart()


def test_older_install_witness_blocks_a_regressed_cache_policy(recovery_case):
    root, client, old, feed = recovery_case
    known = new_policy(recovery_case)
    (root / install.HIGHEST).write_bytes(known)
    feed.publish('1.7.0', feed.old_files)
    feed.signed.update(metadata_version=4, min_supported_version='1.0.0')
    regressed = feed.sign(feed.signed)
    (client.cache.root / 'highest-metadata-stable.json').write_bytes(regressed)
    state = core.get_startup_update_state(root, service=client, check_feed=False)
    assert state['state'] == 'MANDATORY_UPDATE' and not state['login_allowed']
    assert state['target'] == '1.6.0'  # 1.7 is signed but violates the known policy
    with pytest.raises(SecurityError, match='floor downgrade'):
        install.admit(root, regressed, feed.keys)

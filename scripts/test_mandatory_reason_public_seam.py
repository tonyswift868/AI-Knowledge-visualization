"""Verified policy -> read-only public reason, with inert signed release fixtures."""
from datetime import datetime, timedelta

import pytest

from desktop import update_protocol as protocol
from desktop.update_lifecycle import CoreUpdatePort
from scripts import per_user_install as install, per_user_update_core as core
from scripts.test_canonical_identity_recovery import case, recovery_case, snapshot
from scripts.test_signed_mandatory_floor import publisher, new_policy, restart


def public(policy):
    port = CoreUpdatePort(None)
    try:
        port.set_startup_state(policy)
        result = port.snapshot
        assert result['current'] == policy['current']
        assert result['latest'] == policy['latest']
        assert result['mandatory_reason'] == policy['mandatory_reason']
        assert result['min_supported_version'] == policy['min_supported_version']
        assert not {'signed', 'envelope', 'cache', 'release'} & result.keys()
        result['mandatory_reason'] = 'caller mutation'
        assert port.snapshot['mandatory_reason'] == policy['mandatory_reason']
        return port.snapshot
    finally:
        port.close()


def historical_category(recovery_case, reason='security'):
    root, _, _, feed = recovery_case
    feed.publish('1.5.0', feed.old_files)
    feed.signed.update(metadata_version=2, mandatory=True, mandatory_reason=reason,
                       min_supported_version='1.5.0')
    raw = feed.sign(feed.signed)
    (root / install.HIGHEST).write_bytes(raw)
    return raw


@pytest.mark.parametrize('reason', ['security', 'protocol', 'data_compatibility'])
def test_latest_signed_category_reaches_public_snapshot(recovery_case, reason):
    root, client, old, feed = recovery_case
    new_policy(recovery_case, mandatory=True)
    feed.signed['mandatory_reason'] = reason
    feed.sign(feed.signed)
    result = core.get_startup_update_state(root, service=client)
    assert result['state'] == 'MANDATORY_UPDATE' and not result['login_allowed']
    assert result['mandatory_reason'] == reason
    assert result['target'] == result['latest'] == '1.6.0'
    assert result['min_supported_version'] == '1.6.0'
    assert public(result)['state'] == 'MANDATORY_UPDATE'
    retained = core.get_startup_update_state(root, service=client, check_feed=False)
    assert retained['mandatory_reason'] == reason and not retained['login_allowed']
    public(retained)


@pytest.mark.parametrize('category_available', [True, False])
def test_exact_historical_1_2_floor_targets_optional_1_4(publisher, category_available):
    publish, *_ = publisher
    floor, _, _ = publish('1.2.0', mandatory=True)
    latest, _, _ = publish('1.4.0')
    witnesses = [floor] if category_available else []
    assert protocol.startup_version_policy(latest, '1.0.0') == 'MANDATORY_UPDATE'
    assert protocol.startup_mandatory_reason(latest, '1.0.0', witnesses=witnesses) == (
        'security' if category_available else 'support_floor')
    assert protocol.make_plan(latest, None, '1.0.0')['latest'] == '1.4.0'


@pytest.mark.parametrize('reason', ['security', 'protocol', 'data_compatibility', None])
def test_existing_signed_witness_binds_history_reason_read_only(recovery_case, reason):
    root, client, _, _ = recovery_case
    if reason is not None:
        historical_category(recovery_case, reason)
    new_policy(recovery_case)
    fresh = core.get_startup_update_state(root, service=client)
    assert fresh['mandatory_reason'] == (reason or 'support_floor')
    before = snapshot(root.parent)
    retained = core.get_startup_update_state(root, service=client, check_feed=False)
    assert snapshot(root.parent) == before
    for result in (fresh, retained):
        assert result['state'] == 'MANDATORY_UPDATE' and not result['login_allowed']
        assert result['min_supported_version'] == '1.5.0'
        assert result['mandatory_reason'] == (reason or 'support_floor')
        assert result['target'] == '1.6.0'
        public(result)


@pytest.mark.parametrize('category_available', [True, False])
@pytest.mark.parametrize('failure', ['offline', 'expired'])
def test_persisted_reason_survives_restart_without_fresh_feed(recovery_case, monkeypatch, category_available, failure):
    root, first, _, _ = recovery_case
    if category_available:
        historical_category(recovery_case, 'protocol')
    new_policy(recovery_case)
    core.get_startup_update_state(root, service=first)
    first.close()
    second = restart(recovery_case)
    if failure == 'offline':
        def offline(*_): raise OSError('offline fixture')
        monkeypatch.setattr(second.cache.transport, 'fetch_metadata', offline)
    else:
        class Later(datetime):
            @classmethod
            def now(cls, tz=None): return datetime.now(tz) + timedelta(days=10)
        monkeypatch.setattr(protocol, 'datetime', Later)
    try:
        result = core.get_startup_update_state(root, service=second)
        assert result['state'] == 'MANDATORY_UPDATE' and not result['login_allowed']
        assert result['mandatory_reason'] == ('protocol' if category_available else 'support_floor')
        assert result['target'] == '1.6.0'
        public(result)
    finally:
        second.close()


def test_optional_metadata_reason_cannot_invent_a_gate_or_category(recovery_case, monkeypatch):
    root, client, _, feed = recovery_case
    new_policy(recovery_case, floor='1.0.0')
    feed.signed['mandatory_reason'] = 'security'  # signed optional presentation noise
    feed.sign(feed.signed)
    result = core.get_startup_update_state(root, service=client)
    assert result['state'] == 'OPTIONAL_UPDATE' and result['login_allowed']
    assert result['mandatory_reason'] == ''
    public(result)
    def offline(*_): raise OSError('offline fixture')
    monkeypatch.setattr(client.cache.transport, 'fetch_metadata', offline)
    result = core.get_startup_update_state(root, service=client)
    assert result['state'] == 'HEALTHY' and result['mandatory_reason'] == ''
    assert result['login_allowed']
    public(result)


def test_optional_latest_category_cannot_replace_missing_history(recovery_case):
    root, client, _, feed = recovery_case
    new_policy(recovery_case)
    feed.signed['mandatory_reason'] = 'security'
    feed.sign(feed.signed)
    result = core.get_startup_update_state(root, service=client)
    assert result['state'] == 'MANDATORY_UPDATE'
    assert result['mandatory_reason'] == 'support_floor'


@pytest.mark.parametrize('attack', ['unsigned_status', 'tampered_feed', 'floor_downgrade'])
def test_untrusted_reason_cannot_replace_known_gate(recovery_case, attack):
    root, client, _, feed = recovery_case
    known = new_policy(recovery_case)
    assert core.get_startup_update_state(root, service=client)['mandatory_reason'] == 'support_floor'
    if attack == 'unsigned_status':
        (client.cache.root / 'state.json').write_bytes(protocol.canonical(dict(
            state='HEALTHY', mandatory_reason='security', min_supported_version='1.0.0')))
    elif attack == 'tampered_feed':
        doc = protocol.strict_json(known)
        doc['signed']['mandatory_reason'] = 'security'
        feed.raw = protocol.canonical(doc)  # deliberately not re-signed
    else:
        feed.signed.update(metadata_version=4, min_supported_version='1.0.0', mandatory_reason='protocol')
        lowered = feed.sign(feed.signed)
        with pytest.raises(protocol.SecurityError, match='floor downgrade'):
            protocol.verify_release(lowered, feed.keys, current='1.4.0', previous_envelope=known)
    result = core.get_startup_update_state(root, service=client)
    assert result['state'] == 'MANDATORY_UPDATE' and not result['login_allowed']
    assert result['mandatory_reason'] == 'support_floor'
    assert result['target'] == '1.6.0'
    public(result)


@pytest.mark.parametrize('mismatch', ['channel', 'floor', 'future_epoch'])
def test_history_category_requires_exact_verified_floor_binding(recovery_case, mismatch):
    _, _, old, feed = recovery_case
    historical_category(recovery_case, 'data_compatibility')
    if mismatch == 'channel': feed.signed['channel'] = 'beta'
    if mismatch == 'future_epoch': feed.signed['metadata_version'] = 4
    witness = protocol.verify_release(feed.sign(feed.signed), feed.keys,
        current=old.version, channel=feed.signed['channel'])
    latest_raw = new_policy(recovery_case, floor='1.6.0' if mismatch == 'floor' else '1.5.0')
    latest = protocol.verify_release(latest_raw, feed.keys, current=old.version)
    assert protocol.startup_mandatory_reason(latest, old.version, witnesses=[witness]) == 'support_floor'
    assert protocol.startup_version_policy(latest, old.version) == 'MANDATORY_UPDATE'


def test_latest_category_takes_precedence_over_history(recovery_case):
    root, client, _, feed = recovery_case
    historical_category(recovery_case, 'protocol')
    new_policy(recovery_case, mandatory=True)
    result = core.get_startup_update_state(root, service=client)
    assert result['mandatory_reason'] == 'security'
    assert result['state'] == 'MANDATORY_UPDATE' and result['target'] == '1.6.0'


def test_tampered_persisted_reason_requires_repair(recovery_case):
    root, client, _, _ = recovery_case
    raw = new_policy(recovery_case)
    assert core.get_startup_update_state(root, service=client)['state'] == 'MANDATORY_UPDATE'
    document = protocol.strict_json(raw)
    document['signed']['mandatory_reason'] = 'security'
    (client.cache.root / 'highest-metadata-stable.json').write_bytes(protocol.canonical(document))
    result = core.get_startup_update_state(root, service=client, check_feed=False)
    assert result['state'] == 'REPAIR_REQUIRED' and not result['login_allowed']
    assert result['mandatory_reason'] == ''
    public(result)


def test_public_adapter_forwards_policy_without_private_service_access(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    policy = core._startup_result('MANDATORY_UPDATE', current='1.0.0', latest='1.4.0',
                                  mandatory_floor='1.2.0', mandatory_reason='support_floor')
    service = SimpleNamespace(current='stale display', install_root='unused', apply_bridge=None,
                              snapshot={}, close=Mock())
    monkeypatch.setattr(install, 'is_install', lambda _: True)
    monkeypatch.setattr('desktop.update_session.UpdateSession', lambda: SimpleNamespace(close=Mock()))
    monkeypatch.setattr(core, 'get_startup_update_state', lambda *_, **__: dict(policy))
    port = CoreUpdatePort(service)
    try:
        result = port.check_latest()
        for name in ('current', 'latest', 'min_supported_version', 'mandatory_reason', 'state'):
            assert result[name] == policy[name]
    finally:
        port.close()

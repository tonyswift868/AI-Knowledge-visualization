"""Contract regressions. Packaged private-desktop qualification is separate."""
from pathlib import Path
import pytest
from tests.refactor.delivery.test_delivery_per_user_install import case
from tests.refactor.delivery.test_delivery_per_user_install import ready
from tests.refactor.delivery.test_delivery_per_user_install import initial_failure
from tests.refactor.delivery.test_delivery_per_user_install import file_snapshot
from refactor.desktop.delivery import per_user_install as engine
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery.update_protocol import canonical
from refactor.desktop.delivery.update_protocol import strict_json
from refactor.desktop.delivery.update_protocol import SecurityError
from refactor.desktop.delivery.update_protocol import RecoverableError


def test_consumer_keys_do_not_import_protected_preflight(monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, 'scripts.update_preflight', None)
    monkeypatch.setitem(sys.modules, 'mentorflow_delivery_authority', SimpleNamespace(PUBLIC_KEYS={'compiled':b'x'*32}))
    assert engine.keys() == {'compiled':b'x'*32}


def test_initial_repair_detection_is_read_only_not_success_or_launch(case, monkeypatch):
    base, root, _, _ = case
    source, raw = initial_failure(case, monkeypatch)
    monkeypatch.setattr(engine, 'launch_health', lambda *_: pytest.fail('no App execution authorized'))
    before = file_snapshot(root)
    state = engine.detect_existing_install(root)
    assert state == dict(root=str(root), existing=False, version='1.3.0', repairable=True)
    assert file_snapshot(root) == before
    assert not engine.is_install(root)
    assert engine.install_per_user(base, source, raw, health=ready)['status'] == 'HEALTHY'
    assert engine.detect_existing_install(root)['existing'] is True


def test_initial_repair_cli_preflight_and_install_share_admission(case, monkeypatch):
    import sys
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    base, root, _, _ = case
    source, raw = initial_failure(case, monkeypatch)
    envelope_path = base / 'release-input.json'; envelope_path.write_bytes(raw)
    monkeypatch.setattr(ProcessPeer, 'inspect', lambda _: dict(elevated=False, integrity_rid=8192))
    monkeypatch.setattr(engine, 'launch_health', ready)  # inert contract test only
    monkeypatch.setattr(sys, 'argv', ['per_user_install', 'preflight', '--parent', str(base)])
    before = file_snapshot(root)
    assert engine.main()['repairable'] is True
    assert file_snapshot(root) == before
    monkeypatch.setattr(sys, 'argv', ['per_user_install', 'install', '--parent', str(base),
                                    '--source', str(source), '--release', str(envelope_path)])
    assert engine.main()['status'] == 'HEALTHY'


def bootstrap(case, monkeypatch):
    base, root, feed, publish = case
    monkeypatch.setattr(engine, 'keys', lambda: feed.keys)
    source, raw = publish('1.3.0', 1)
    engine.install_per_user(base, source, raw, public_keys=feed.keys, health=ready)
    return base, root, feed, publish


def test_installer_contract_and_conservative_uninstall(case, monkeypatch):
    base, root, feed, publish = bootstrap(case, monkeypatch)
    assert engine.validate_parent(base) == root
    assert engine.detect_existing_install(root)['version'] == '1.3.0'
    with pytest.raises(ValueError): engine.validate_parent(base/'missing')
    assert not (base/'missing').exists()
    sentinel = root/'unknown-user-document'; sentinel.write_bytes(b'preserve')
    result = engine.uninstall_per_user(root)
    assert result['status'] == 'UNINSTALLED' and not engine.is_install(root)
    assert sentinel.read_bytes() == b'preserve'
    assert (Path(result['quarantine'])/'App/MentorFlow.exe').exists()
    with pytest.raises(SecurityError): engine.validate_target(root)


@pytest.mark.parametrize('reason', ['shutdown', 'logoff', 'unsafe'])
def test_pending_unsafe_exit_never_fetches_or_swaps(case, monkeypatch, reason):
    _, root, feed, publish = bootstrap(case, monkeypatch)
    source, raw = publish('1.3.1', 2)
    core.remember_download(root, source, raw)
    monkeypatch.setattr(core, 'configured_service', lambda _: pytest.fail('no network/launch on teardown'))
    before = (root/engine.CURRENT).read_bytes()
    assert core.apply_update(root, reason=reason)['state'] == 'PENDING_INSTALL'
    assert (root/engine.CURRENT).read_bytes() == before
    assert core.status(root)['state'] == 'PENDING_INSTALL'


def test_pending_receipt_cannot_authorize_changed_metadata(case, monkeypatch):
    _, root, feed, publish = bootstrap(case, monkeypatch)
    source, raw = publish('1.3.1', 2)
    core.remember_download(root, source, raw)
    from refactor.desktop.delivery.update_service import MatureUpdateService
    from refactor.desktop.delivery.update_download import HTTPTransport
    service = MatureUpdateService(install_root=root, cache_root=root.parent/'cache', metadata_url=feed.origin+'/stable.json',
                                  public_keys=feed.keys, current='1.3.0', transport=HTTPTransport(feed.origin, fixture_http=True))
    feed.signed['metadata_version'] = 3; feed.sign(feed.signed)
    with pytest.raises(SecurityError): core.apply_update(root, reason='next_launch', service=service)
    assert engine.installed(root, feed.keys).version == '1.3.0'


def test_state_writer_rejects_unsigned_or_downgrade(case, monkeypatch):
    _, root, feed, publish = bootstrap(case, monkeypatch)
    with pytest.raises(SecurityError): engine.write_installed_state(root, b'{}')
    source, raw = publish('1.3.1', 2)
    with pytest.raises(SecurityError): engine.write_installed_state(root, raw)
    assert engine.installed(root, feed.keys).version == '1.3.0'


def test_previous_rollback_cannot_consume_new_pending_on_offline(case, monkeypatch):
    _, root, feed, publish = bootstrap(case, monkeypatch)
    source, raw = publish('1.3.1', 2)
    core.remember_download(root, source, raw)
    journal = strict_json((root/engine.JOURNAL).read_bytes())
    (root/engine.JOURNAL).write_bytes(canonical({**journal, 'phase':'ROLLED_BACK'}))
    class OfflineService:
        def check(self, **_): return {'state':'FAILED_RECOVERABLE'}
    with pytest.raises(RecoverableError):
        core.apply_update(root, reason='next_launch', service=OfflineService())
    assert core.read_pending(root)['state'] == 'PENDING_INSTALL'
    assert engine.installed(root, feed.keys).version == '1.3.0'

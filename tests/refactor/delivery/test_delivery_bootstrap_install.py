from types import SimpleNamespace

import pytest

from refactor.desktop.delivery.bootstrap_install import bootstrap_install
from refactor.desktop.delivery.update_protocol import DownloadCancelled
from refactor.desktop.delivery.per_user_install import strict_json
from refactor.desktop.delivery.update_download import HTTPTransport
from refactor.desktop.delivery.update_service import MatureUpdateService
from tests.refactor.delivery.test_delivery_per_user_install import case as install_case
from tests.refactor.delivery.test_delivery_per_user_install import ready


class FakeService:
    keys = {'fixture': b'k' * 32}
    current = '2.0.48'
    install_identity = 'fixture-install'
    plan = {'expected_download_bytes': 10}
    staged = 'verified-tree'
    release = SimpleNamespace(version='2.0.48', envelope=b'signed-envelope')

    def check(self, **_):
        return {'state': 'UPDATE_AVAILABLE'}

    def download(self, **kwargs):
        kwargs['progress']({'downloaded_bytes': 10, 'message': 'downloaded'})
        return {'state': 'READY_TO_APPLY'}

    def close(self):
        pass


def test_bootstrap_reports_version_only_after_healthy_transaction(tmp_path):
    status, result = tmp_path / 'state.ini', tmp_path / 'result.json'
    observed = {}

    def transaction(root, source, envelope, **kwargs):
        observed.update(root=root, source=source, envelope=envelope, kwargs=kwargs)
        kwargs['progress'](100)
        return {'status': 'HEALTHY', 'version': '2.0.48', 'health': {'ready': True}}

    value = bootstrap_install(tmp_path / 'MentorFlow', status, result_file=result,
        service_factory=lambda _: FakeService(), transaction=transaction)
    assert value['version'] == '2.0.48'
    assert observed['kwargs']['public_keys'] == FakeService.keys
    assert observed['kwargs']['refresh_runtime'] is True
    assert strict_json(result.read_bytes())['version'] == '2.0.48'
    assert 'version=2.0.48' in status.read_text(encoding='utf-16')


def test_bootstrap_failure_does_not_report_installed_version(tmp_path):
    class FailedService(FakeService):
        staged = None

        def download(self, **_):
            return {'state': 'DOWNLOAD_PAUSED_OR_INTERRUPTED', 'message': 'offline'}

    result = tmp_path / 'result.json'
    with pytest.raises(Exception, match='offline'):
        bootstrap_install(tmp_path / 'MentorFlow', result_file=result,
            service_factory=lambda _: FailedService(), transaction=lambda *a, **k: pytest.fail())
    saved = strict_json(result.read_bytes())
    assert saved['status'] == 'FAILED'
    assert 'version' not in saved


def test_bootstrap_cancelled_before_install_never_calls_transaction(tmp_path):
    cancel = tmp_path / 'cancel.signal'
    cancel.write_text('cancel')

    class CancelledService(FakeService):
        def check(self, **kwargs):
            assert kwargs['cancel']()
            raise DownloadCancelled('cancelled')

    with pytest.raises(DownloadCancelled):
        bootstrap_install(tmp_path / 'MentorFlow', cancel_file=cancel,
            service_factory=lambda _: CancelledService(), transaction=lambda *a, **k: pytest.fail())


def test_up_to_date_install_runs_real_health_and_verifies_both_trees(tmp_path, monkeypatch):
    from refactor.desktop.delivery import per_user_install as install

    release = SimpleNamespace(version='2.0.48')
    monkeypatch.setattr(install, 'validate_target', lambda _: {'root': str(tmp_path / 'MentorFlow')})
    monkeypatch.setattr(install, 'is_install', lambda _: True)
    monkeypatch.setattr(install, 'installed', lambda *args: release)
    seen = []
    monkeypatch.setattr(install, 'verify_tree', lambda path, _: seen.append(('app', path)))
    monkeypatch.setattr(install, 'verify_recovery_runtime', lambda path, _: seen.append(('runtime', path)))

    class CurrentService(FakeService):
        def check(self, **_):
            return {'state': 'UP_TO_DATE'}

    def real_health(root, current):
        seen.append(('health', root, current))
        return {'ready': True, 'pid': 101, 'hwnd_hex': '0x1'}

    value = bootstrap_install(tmp_path / 'MentorFlow', service_factory=lambda _: CurrentService(),
                              health=real_health)
    assert value['health']['ready'] is True
    assert [item[0] for item in seen] == ['app', 'runtime', 'health']


def test_initial_target_error_still_writes_failed_outputs(tmp_path, monkeypatch):
    from refactor.desktop.delivery import per_user_install as install

    monkeypatch.setattr(install, 'validate_target', lambda _: (_ for _ in ()).throw(ValueError('bad root')))
    status, result = tmp_path / 'state.ini', tmp_path / 'result.json'
    with pytest.raises(ValueError, match='bad root'):
        bootstrap_install(tmp_path / 'MentorFlow', status, result_file=result,
                          service_factory=lambda _: pytest.fail())
    assert 'error=bad root' in status.read_text(encoding='utf-16')
    saved = strict_json(result.read_bytes())
    assert saved['status'] == 'FAILED' and 'version' not in saved


def test_same_version_corrupt_app_is_repaired_through_signed_feed_and_transaction(install_case, monkeypatch):
    base, root, feed, publish = install_case
    from refactor.desktop.delivery import per_user_install as install
    monkeypatch.setattr(install, 'keys', lambda: feed.keys)
    source, envelope = publish('1.3.0', 1)
    install.transact(root, source, envelope, public_keys=feed.keys, health=ready)
    broken = root / 'App' / 'MentorFlow.exe'
    broken.write_bytes(b'MZ corrupted fixture')

    # Fresh metadata epoch with the exact same App manifest authorizes repair.
    feed.signed['metadata_version'] = 2
    feed.sign(feed.signed)
    def service_factory(target):
        return MatureUpdateService(install_root=target, cache_root=base / 'bootstrap-cache',
            metadata_url=feed.origin + '/stable.json', public_keys=feed.keys, current='1.3.0',
            transport=HTTPTransport(feed.origin, fixture_http=True, total=3, read=1, connect=1))

    result = bootstrap_install(root, service_factory=service_factory, health=ready)
    assert result['status'] == 'HEALTHY' and result['version'] == '1.3.0'
    assert broken.read_bytes() == b'MZ synthetic application'
    assert install.installed(root, feed.keys).version == '1.3.0'

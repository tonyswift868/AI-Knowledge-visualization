"""Bootstrap failure qualification with inert files and a localhost feed only."""
from datetime import datetime, timedelta, timezone
import pytest

from tests.refactor.delivery.test_delivery_mature_update import Feed
from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery.bootstrap_install import bootstrap_install
from refactor.desktop.delivery.update_download import HTTPTransport
from refactor.desktop.delivery.update_service import MatureUpdateService


@pytest.fixture
def setup_case(tmp_path, monkeypatch):
    feed = Feed(tmp_path / 'feed')
    monkeypatch.setattr(install, 'keys', lambda: feed.keys)
    feed.old_files = {
        **feed.old_files,
        'MentorFlow.exe': b'MZ synthetic inert payload\0' * 65536,
        'UserRuntime/UserRuntime.exe': b'MZ synthetic updater fixture',
    }
    feed.publish('1.1.1', feed.old_files)
    root = tmp_path / 'MentorFlow'
    root.mkdir()

    def service_factory(target):
        current = install.installed(target).version if install.is_install(target) else '0.0.0'
        return MatureUpdateService(
            install_root=target,
            cache_root=tmp_path / 'cache',
            metadata_url=feed.origin + '/stable.json',
            public_keys=feed.keys,
            current=current,
            transport=HTTPTransport(feed.origin, fixture_http=True, total=2, read=1, connect=1),
        )

    yield feed, root, service_factory
    feed.close()


def ready(_root, release):
    return {'ready': True, 'version': release.version}


def run_bootstrap(root, service_factory, **kwargs):
    kwargs.setdefault('status_file', root.parent / 'bootstrap-status.ini')
    return bootstrap_install(root, service_factory=service_factory, health=ready, **kwargs)


def attempt_bootstrap(root, service_factory, **kwargs):
    try:
        return run_bootstrap(root, service_factory, **kwargs)
    except Exception as error:
        return error


def assert_failed(result):
    assert isinstance(result, Exception) or result['status'] != 'HEALTHY'


def assert_no_healthy_install(root):
    assert not install.is_install(root)
    assert not (root / install.CURRENT).exists()
    status_file = root.parent / 'bootstrap-status.ini'
    if status_file.exists():
        values = dict(line.split('=', 1) for line in status_file.read_text(encoding='utf-16').splitlines()
                      if '=' in line)
        assert not values.get('version')


def assert_healthy(result, root, version='1.1.1'):
    assert result['status'] == 'HEALTHY'
    assert install.installed(root).version == version
    status_file = root.parent / 'bootstrap-status.ini'
    if status_file.exists():
        values = dict(line.split('=', 1) for line in status_file.read_text(encoding='utf-16').splitlines()
                      if '=' in line)
        assert values['version'] == version


def test_cold_install_downloads_verified_inert_payload_and_commits_last(setup_case):
    feed, root, service_factory = setup_case
    result = run_bootstrap(root, service_factory)
    assert_healthy(result, root)
    assert (root / 'App' / 'MentorFlow.exe').read_bytes() == feed.old_files['MentorFlow.exe']
    assert not any(path.endswith('.exe') for path, _ in feed.requests if path == '/stable.json')


def test_offline_cold_install_leaves_no_receipt_and_retry_recovers(setup_case):
    feed, root, service_factory = setup_case
    original = service_factory

    def offline(target):
        service = original(target)
        service.metadata_url = feed.origin + '/offline.json'
        return service

    failed = attempt_bootstrap(root, offline)
    assert_failed(failed)
    assert_no_healthy_install(root)
    recovered = run_bootstrap(root, service_factory)
    assert_healthy(recovered, root)


def test_disconnect_resumes_partial_object_and_completes_cold_install(setup_case):
    feed, root, service_factory = setup_case
    feed.disconnect_once = True
    result = run_bootstrap(root, service_factory)
    assert_healthy(result, root)
    object_requests = [r for p, r in feed.requests if p != '/stable.json']
    assert any(value and value.startswith('bytes=') for value in object_requests)


def test_server_ignoring_range_restarts_object_from_zero(setup_case):
    feed, root, service_factory = setup_case
    feed.no_range = True
    feed.disconnect_once = True
    result = run_bootstrap(root, service_factory)
    assert_healthy(result, root)
    requested = [r for p, r in feed.requests if p != '/stable.json']
    assert any(value for value in requested)
    assert (root / 'App' / 'MentorFlow.exe').read_bytes() == feed.old_files['MentorFlow.exe']


def test_wrong_object_hash_never_registers_and_retry_recovers(setup_case):
    feed, root, service_factory = setup_case
    feed.corrupt = True
    failed = attempt_bootstrap(root, service_factory)
    assert_failed(failed)
    assert_no_healthy_install(root)
    feed.corrupt = False
    assert_healthy(run_bootstrap(root, service_factory), root)


@pytest.mark.parametrize('problem', ['signature', 'expired', 'unknown_key'])
def test_untrusted_or_expired_manifest_never_registers_and_retry_recovers(setup_case, problem):
    feed, root, service_factory = setup_case
    if problem == 'signature':
        feed.raw = feed.raw.replace(b'Small frontend fix', b'altered release notes')
    elif problem == 'expired':
        now = datetime.now(timezone.utc)
        feed.signed.update(issued_at=(now - timedelta(days=3)).isoformat(),
                           published_at=(now - timedelta(days=3)).isoformat(),
                           expires_at=(now - timedelta(days=1)).isoformat())
        feed.sign(feed.signed)
    else:
        feed.signed['key_id'] = 'unknown-fixture-key'
        feed.sign(feed.signed)

    failed = attempt_bootstrap(root, service_factory)
    assert_failed(failed)
    assert_no_healthy_install(root)
    feed.publish('1.1.1', feed.old_files)
    assert_healthy(run_bootstrap(root, service_factory), root)


def test_low_disk_refusal_does_not_commit_and_retry_recovers(setup_case, monkeypatch):
    feed, root, service_factory = setup_case
    service = service_factory(root)
    monkeypatch.setattr(service.cache, 'disk_preflight', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('no space')))
    result = attempt_bootstrap(root, lambda _target: service)
    assert_failed(result)
    assert_no_healthy_install(root)
    assert_healthy(run_bootstrap(root, service_factory), root)


def test_cancel_during_object_download_never_commits_and_retry_recovers(setup_case, tmp_path, monkeypatch):
    feed, root, service_factory = setup_case
    cancel_file = tmp_path / 'cancel'
    service = service_factory(root)
    download = service.cache.download

    def cancel_after_first_chunk(asset, *, cancel=None, progress=None):
        def report(bytes_received):
            if bytes_received:
                cancel_file.write_text('cancel', encoding='utf-8')
            if progress:
                progress(bytes_received)
        return download(asset, cancel=cancel, progress=report)

    monkeypatch.setattr(service.cache, 'download', cancel_after_first_chunk)
    result = attempt_bootstrap(root, lambda _target: service, cancel_file=cancel_file)
    assert_failed(result)
    assert_no_healthy_install(root)
    cancel_file.unlink(missing_ok=True)
    assert_healthy(run_bootstrap(root, service_factory), root)


def test_health_failure_rolls_back_cold_attempt_and_retry_recovers(setup_case):
    feed, root, service_factory = setup_case

    def unhealthy(_root, _release):
        raise RuntimeError('injected readiness failure')

    try:
        failed = bootstrap_install(root, root.parent / 'bootstrap-status.ini',
                                   service_factory=service_factory, health=unhealthy)
    except Exception as error:
        failed = error
    assert_failed(failed)
    assert_no_healthy_install(root)
    assert_healthy(run_bootstrap(root, service_factory), root)


def test_existing_healthy_install_survives_failed_candidate_without_downgrade(setup_case):
    feed, root, service_factory = setup_case
    assert_healthy(run_bootstrap(root, service_factory), root)
    prior = install.installed(root)
    prior_exe = (root / 'App' / 'MentorFlow.exe').read_bytes()

    feed.publish('1.0.0', feed.old_files)
    refused = attempt_bootstrap(root, service_factory)
    assert_failed(refused)
    assert install.installed(root).version == prior.version
    assert (root / 'App' / 'MentorFlow.exe').read_bytes() == prior_exe

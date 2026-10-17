"""Discovery never waits for download locks or loses verified mandatory policy."""
from refactor.desktop.delivery import update_download as io
from refactor.desktop.delivery.update_protocol import digest
from test_server_installer90 import server90
from test_update_feed100 import feed100


def test_update_check160_unchanged_signed_pointer_does_not_need_download_lock(feed100):
    state = feed100
    assert state.service.check()['state'] == 'UP_TO_DATE'
    state.requests.clear()
    with io.InstallUpdateLock(state.service.cache.root):
        result = state.service.check()
    assert result['state'] == 'UP_TO_DATE'
    assert result['pointer_status'] == 'unchanged_verified_cache'
    assert [r.url.path.rsplit('/', 1)[-1] for r in state.requests] == ['latest.json']
    assert result['check_timings_ms']['pointer'] >= 0
    assert 'full_manifest' not in result['check_timings_ms']


def test_update_check160_new_mandatory_policy_survives_busy_download_lock(feed100):
    import json
    from scripts.release.transport import LocalServer
    from test_publish_optional_installer99 import stage_version
    state = feed100
    assert state.service.check()['state'] == 'UP_TO_DATE'
    witness_path = state.service.cache.root/'highest-metadata-stable.json'
    previous = witness_path.read_bytes()
    fixture = state.fixture
    fixture.server = LocalServer(fixture.app.parent)
    fixture.body = json.loads(state.raw)['signed']
    state.raw = stage_version(fixture, '2.0.4', 2)
    state.pointer.update(version='2.0.4', metadata_version=2, sha256=digest(state.raw), size=len(state.raw))
    with io.InstallUpdateLock(state.service.cache.root):
        result = state.service.check()
        assert result['state'] == 'UPDATE_AVAILABLE' and result['mandatory']
        assert result['witness_write_deferred'] is True
        assert result['latest'] == '2.0.4'
        assert state.service.release.envelope == state.raw
        # An uncommitted receipt is not an apply shortcut: no tree was built
        # and the held lock still stops download/materialization.
        assert witness_path.read_bytes() == previous
        assert state.service.download()['state'] == 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
        assert state.service.staged is None
    # A later check commits the same verified policy after the lock is free.
    result = state.service.check()
    assert result['mandatory'] and not result['witness_write_deferred']
    assert witness_path.read_bytes() == state.raw


def test_update_check160_unchanged_pointer_reconciles_concurrent_mandatory_witness(feed100):
    """A newer signed witness committed during pointer I/O must win the cache fast path."""
    import json
    from scripts.release.transport import LocalServer
    from test_publish_optional_installer99 import stage_version
    from refactor.desktop.delivery.update_protocol import verify_release
    from refactor.desktop.delivery.update_service import MatureUpdateService

    state = feed100
    service = state.service
    assert service.check()['state'] == 'UP_TO_DATE'
    old_envelope = state.raw
    witness_path = service.cache.root / 'highest-metadata-stable.json'
    old_witness = witness_path.read_bytes()

    # Prepare a valid newer mandatory release, but keep the server's pointer
    # and manifest at v1. A separate service instance commits its witness while
    # this service is fetching latest.json, exactly between initial cache read
    # and the unchanged-pointer classification.
    fixture = state.fixture
    fixture.server = LocalServer(fixture.app.parent)
    fixture.body = json.loads(old_envelope)['signed']
    new_envelope = stage_version(fixture, '2.0.4', 2)
    new_release = verify_release(
        new_envelope, service.keys, current=service.current,
        channel=service.channel, highest_seen=1,
        previous_envelope=old_witness)
    concurrent_service = MatureUpdateService(
        install_root=service.install_root,
        cache_root=state.fixture.app.parent / 'cache',
        metadata_url=service.metadata_url,
        public_keys=service.keys,
        current=service.current,
        channel=service.channel,
        transport=state.transport)

    original_fetch = state.transport.fetch_metadata
    committed = False

    def fetch_with_concurrent_commit(url, max_bytes, **kwargs):
        nonlocal committed
        result = original_fetch(url, max_bytes, **kwargs)
        if url.endswith('/latest.json') and not committed:
            with io.InstallUpdateLock(service.cache.root):
                concurrent_service._remember_release(new_release)
            committed = True
        return result

    state.transport.fetch_metadata = fetch_with_concurrent_commit
    try:
        state.requests.clear()
        result = service.check()
    finally:
        state.transport.fetch_metadata = original_fetch

    assert committed
    assert result['state'] == 'UPDATE_AVAILABLE'
    assert result['mandatory'] is True
    assert result['latest'] == '2.0.4'
    assert result['pointer_status'] != 'witness_reconciled'  # fetch path label is kept
    assert result['witness_reconciled'] is True
    assert service.release.envelope == new_envelope
    assert witness_path.read_bytes() == new_envelope
    assert [request.url.path.rsplit('/', 1)[-1] for request in state.requests] == ['latest.json']
    assert old_envelope != new_envelope


def test_update_check160_loopback_transport_uses_real_pointer_path(feed100):
    state = feed100
    url = 'https://localhost/updates/app/stable/stable.json'
    state.service.metadata_url = url
    state.service.cache.transport = io.HTTPTransport(url)
    assert state.service.check()['state'] == 'UP_TO_DATE'
    state.requests.clear()
    assert state.service.check()['pointer_status'] == 'unchanged_verified_cache'
    assert len(state.requests) == 1 and state.requests[0].url.host == 'localhost'
    assert state.requests[0].url.path.endswith('/latest.json')

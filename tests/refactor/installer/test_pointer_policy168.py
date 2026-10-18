"""Paired unsigned login-policy hint; signed manifests remain authoritative."""
import base64
import json

import httpx
import pytest

from refactor.desktop.delivery.update_protocol import (
    canonical, digest, make_login_policy, release_pointer,
    release_pointer_policy, verify_release, SecurityError,
)
from scripts.release import pipeline
from scripts.release.transport import LocalServer
from test_publish_optional_installer99 import stage_version
from test_server_installer90 import server90
from test_update_feed100 import feed100


@pytest.fixture
def release168(server90):
    fixture = server90
    fixture.server = LocalServer(fixture.app.parent)
    fixture.body = json.loads(fixture.stable.read_bytes())['signed']
    return fixture


def _install_policy_transport(state, policy, *, corrupt=False, missing=False):
    original = state.transport.fetch_metadata
    requests = []

    def fetch(url, maximum, **kwargs):
        requests.append(url)
        if url.endswith('/login-policy.json'):
            if missing:
                raise httpx.HTTPStatusError('404', request=None, response=None)
            raw = canonical(policy)
            if corrupt:
                raw = canonical({**policy, 'sha256': '0' * 64})
            validate = kwargs.get('validate')
            if validate is not None:
                validate(raw)
            return raw
        return original(url, maximum, **kwargs)

    state.transport.fetch_metadata = fetch
    return requests, original


def test_pointer_policy168_publication_keeps_legacy_pointer_exact_and_publishes_pair(release168):
    f = release168
    stage_version(f, '2.0.4', 2)
    result = pipeline.promote(f.server, '2.0.4', f.app.parent / 'promote168', public_key=f.key)
    stable = f.app / 'stable/stable.json'
    pointer = release_pointer((f.app / 'stable/latest.json').read_bytes())
    policy_raw = (f.app / 'stable/login-policy.json').read_bytes()
    policy = release_pointer_policy(policy_raw, pointer)
    release = verify_release(stable.read_bytes(), {'fixture90': f.private.public_key().public_bytes_raw()},
                             current='2.0.3')
    assert set(pointer) == {'version', 'metadata_version', 'sha256', 'size'}
    assert pointer == {key: make_login_policy(release)[key]
                       for key in ('version', 'metadata_version', 'sha256', 'size')}
    assert policy == make_login_policy(release)
    assert result['login_policy'] == policy
    assert json.loads(policy_raw) == policy


def test_pointer_policy168_promotes_old_staging_without_sidecar(release168):
    f = release168
    stage_version(f, '2.0.4', 2)
    staged_policy = f.app / '.staging-2.0.4/stable/login-policy.json'
    staged_policy.unlink()
    result = pipeline.promote(f.server, '2.0.4', f.app.parent / 'legacy-stage168', public_key=f.key)
    assert result['status'] == 'STABLE_PUBLISHED'
    assert json.loads((f.app / 'stable/login-policy.json').read_bytes()) == result['login_policy']


def test_pointer_policy168_stage_rejects_hint_that_differs_from_signed_envelope(release168):
    f = release168
    raw = f.stable.read_bytes()
    transfer = f.server.upload(f.stable)
    with pytest.raises(ValueError, match='staged login policy differs'):
        f.server.call('stage_manifest', '2.0.3', transfer=transfer,
            sha256=digest(raw), manifest=base64.b64encode(raw).decode(),
            login_policy={'version': '2.0.3', 'metadata_version': 1,
                          'sha256': digest(raw), 'size': len(raw),
                          'mandatory': False, 'mandatory_floor': '2.0.3'})
    assert not (f.app / '.staging-2.0.3/stable/stable.json').exists()


def test_pointer_policy168_parser_requires_pair_and_exact_fields(feed100):
    state = feed100
    pointer = release_pointer(canonical(state.pointer))
    release = verify_release(state.raw, state.service.keys, current=state.service.current)
    policy = make_login_policy(release)
    assert release_pointer_policy(canonical(policy), pointer) == policy
    for invalid in (
        {**policy, 'sha256': '0' * 64},
        {**policy, 'mandatory_floor': '99.0.0'},
        {**policy, 'mandatory': 1},
        {**policy, 'extra': True},
    ):
        with pytest.raises(SecurityError):
            release_pointer_policy(canonical(invalid), pointer)


def test_pointer_policy168_optional_hint_is_delivered_before_full_manifest(feed100):
    state = feed100
    state.service.current = '2.0.2'
    state.fixture.body = json.loads(state.raw)['signed']
    state.fixture.server = LocalServer(state.fixture.app.parent)
    state.fixture.body.update(mandatory=False, mandatory_reason='', min_supported_version='2.0.0')
    state.raw = stage_version(state.fixture, '2.0.4', 2)
    state.pointer.update(version='2.0.4', metadata_version=2,
                         sha256=digest(state.raw), size=len(state.raw))
    verified = verify_release(state.raw, state.service.keys, current='2.0.2')
    policy = make_login_policy(verified)
    requests, original = _install_policy_transport(state, policy)
    callbacks = []
    result = state.service.check(on_pointer_policy=callbacks.append)
    state.transport.fetch_metadata = original
    assert callbacks == [policy]
    assert result['state'] == 'UPDATE_AVAILABLE' and result['mandatory'] is False
    assert requests.index(next(url for url in requests if url.endswith('/latest.json'))) < requests.index(
        next(url for url in requests if url.endswith('/login-policy.json')))
    assert requests.index(next(url for url in requests if url.endswith('/login-policy.json'))) < requests.index(
        next(url for url in requests if url.endswith('/stable.json')))


@pytest.mark.parametrize('fault', ['missing', 'mismatch'])
def test_pointer_policy168_missing_or_mismatched_hint_falls_back_to_signed_manifest(feed100, fault):
    state = feed100
    release = verify_release(state.raw, state.service.keys, current=state.service.current)
    policy = make_login_policy(release)
    requests, original = _install_policy_transport(
        state, policy, missing=fault == 'missing', corrupt=fault == 'mismatch')
    callbacks = []
    result = state.service.check(on_pointer_policy=callbacks.append)
    state.transport.fetch_metadata = original
    assert callbacks == []
    assert result['state'] == 'UP_TO_DATE'
    assert any(url.endswith('/stable.json') for url in requests)


def test_pointer_policy168_signed_mandatory_wins_over_optional_hint(feed100):
    state = feed100
    state.service.current = '2.0.2'
    state.fixture.body = json.loads(state.raw)['signed']
    state.fixture.server = LocalServer(state.fixture.app.parent)
    state.fixture.body.update(mandatory=True, mandatory_reason='protocol', min_supported_version='2.0.3')
    state.raw = stage_version(state.fixture, '2.0.4', 2)
    state.pointer.update(version='2.0.4', metadata_version=2,
                         sha256=digest(state.raw), size=len(state.raw))
    signed_release = verify_release(state.raw, state.service.keys, current='2.0.2')
    signed_policy = make_login_policy(signed_release)
    lie = {**signed_policy, 'mandatory': False, 'mandatory_floor': '2.0.0'}
    requests, original = _install_policy_transport(state, lie)
    callbacks = []
    result = state.service.check(on_pointer_policy=callbacks.append)
    state.transport.fetch_metadata = original
    assert callbacks == [lie]  # hint may release the login view temporarily
    assert result['state'] == 'UPDATE_AVAILABLE' and result['mandatory'] is True
    assert state.service.release.envelope == state.raw
    assert any(url.endswith('/stable.json') for url in requests)


def test_pointer_policy168_unchanged_verified_cache_skips_policy_and_manifest(feed100):
    state = feed100
    assert state.service.check()['state'] == 'UP_TO_DATE'
    state.requests.clear()
    callbacks = []
    assert state.service.check(on_pointer_policy=callbacks.append)['state'] == 'UP_TO_DATE'
    assert callbacks == []
    assert [request.url.path.rsplit('/', 1)[-1] for request in state.requests] == ['latest.json']


def test_pointer_policy168_legacy_client_still_reads_new_four_field_pointer(feed100):
    state = feed100
    # Exact fields keep all already shipped pointer readers on their fast path.
    newer = {**state.pointer, 'mandatory': True, 'mandatory_floor': '2.0.3'}
    with pytest.raises(SecurityError):
        release_pointer(canonical(newer))
    parsed = release_pointer(canonical(state.pointer))
    assert parsed == state.pointer

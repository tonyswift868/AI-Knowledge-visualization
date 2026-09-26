"""MockTransport only: no real login, server, token or model."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
from threading import Event, get_ident
from types import SimpleNamespace

import httpx
import pytest

from refactor.learning.course.catalog_client import (CatalogClient, CatalogFormatError,
    REFRESH_SECONDS, validate_prices)
from refactor.learning.course.source_filter import parse_lists, SourceListsError
from refactor.learning.plan_models import PlanError


ROOT = Path(__file__).parents[3]
CA = ROOT / 'refactor/desktop/resources/trust/account-api.pem'
DATA = ROOT / 'refactor/learning/course/data'
KINDS = ('source-whitelist', 'model-prices')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('catalog tests must not connect to real servers')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)


def session(subject='synthetic-user', token='synthetic-token-one'):
    issuer = 'https://identity.example.test'
    parts = [sha256(value.encode()).hexdigest() for value in (issuer, subject)]
    account = 'mfa1_' + sha256(('mentorflow.account.v1:' + ':'.join(parts)).encode()).hexdigest()
    identity = SimpleNamespace(account_id=account, issuer=issuer, subject=subject,
        organization_id='synthetic-org', workspace_id='synthetic-workspace')
    return SimpleNamespace(identity=identity, access_token=token, server_state={
        'authenticated': True, 'account': {'account_id': account, 'subject_id': subject},
        'context': {'organization_id': identity.organization_id, 'workspace_id': identity.workspace_id}})


def catalogs():
    lists = json.loads((DATA / 'source_lists.json').read_text(encoding='utf-8'))
    lists.pop('candidates')
    lists['updated'] = '2026-09-28'
    prices = json.loads((DATA / 'model-prices.json').read_text(encoding='utf-8'))
    prices['version'] = 'synthetic-next-version'
    return {'source-whitelist': lists, 'model-prices': prices}


class Clock:
    now = 1_800_000_000.0
    def __call__(self):
        return self.now
    def next(self):
        self.now += REFRESH_SECONDS


def client(tmp_path, handler, *, state=None, clock=None, auto_refresh=False):
    state = state or [session()]
    return CatalogClient(account_id=state[0].identity.account_id, data_root=tmp_path,
        session_provider=lambda: state[0], api_base_url='https://account.example.test', ca_bundle=CA,
        http_transport=httpx.MockTransport(handler), clock=clock or Clock(), auto_refresh=auto_refresh)


def ok_handler(request):
    kind = request.url.path.rsplit('/', 1)[-1]
    return httpx.Response(200, json=catalogs()[kind], headers={'etag': '"' + kind + '-v1"'})


def test_builtin_prices_are_exact_example_copy_and_candidates_excluded(tmp_path):
    example = ROOT / 'deploy/enterprise/staging/rp05/catalog/model-prices.example.json'
    assert example.read_bytes() == (DATA / 'model-prices.json').read_bytes()
    with client(tmp_path, ok_handler) as catalog:
        assert not catalog.load_lists().candidates
        assert len(catalog.load_lists().whitelist) == 113
        assert catalog.load_prices() == json.loads(example.read_text(encoding='utf-8'))
        assert all(catalog.status(kind).origin == 'builtin' for kind in KINDS)


def test_200_authenticated_endpoints_cache_and_fresh_token_every_request(tmp_path):
    state, calls = [session()], []
    def handler(request):
        calls.append((request.url.path, request.headers['authorization']))
        assert request.headers['x-mentorflow-organization-id'] == 'synthetic-org'
        assert request.headers['x-mentorflow-workspace-id'] == 'synthetic-workspace'
        state[0] = session(token='synthetic-token-two')
        return ok_handler(request)
    with client(tmp_path, handler, state=state) as catalog:
        status = catalog.refresh_background().result(timeout=3)
        assert all(item.code == 'UPDATED' for item in status.values())
        assert catalog.load_lists().updated == '2026-09-28'
        assert catalog.load_prices()['version'] == 'synthetic-next-version'
        assert calls == [('/api/enterprise/catalog/source-whitelist', 'Bearer synthetic-token-one'),
                         ('/api/enterprise/catalog/model-prices', 'Bearer synthetic-token-two')]
        saved = list(catalog.cache_dir.glob('*.json'))
        assert len(saved) == 3
        assert all('synthetic-token-' not in path.read_text(encoding='utf-8') for path in saved)
        assert 'candidates' not in json.loads((catalog.cache_dir/'source-whitelist.json').read_text(encoding='utf-8'))['payload']
    with client(tmp_path, ok_handler) as reopened:
        assert reopened.load_lists().updated == '2026-09-28'
        assert all(reopened.status(kind).origin == 'cache' for kind in KINDS)


def test_304_uses_prior_cache_sends_etag_and_survives_restart(tmp_path):
    clock, calls = Clock(), []
    with client(tmp_path, ok_handler, clock=clock) as initial:
        initial.refresh_background().result(timeout=3)
    clock.next()
    def unchanged(request):
        calls.append(request.headers.get('if-none-match'))
        return httpx.Response(304, headers={'etag': request.headers['if-none-match']})
    with client(tmp_path, unchanged, clock=clock) as catalog:
        states = catalog.refresh_background().result(timeout=3)
        assert all(state.code == 'NOT_MODIFIED' for state in states.values())
        assert catalog.load_prices()['version'] == 'synthetic-next-version'
    assert calls == ['"source-whitelist-v1"', '"model-prices-v1"']


@pytest.mark.parametrize('mode,code', [('404', 'NOT_CONFIGURED'), ('401', 'REAUTH_REQUIRED'),
    ('403', 'REAUTH_REQUIRED'), ('503', 'HTTP_FAILED'), ('network', 'NETWORK_FAILED'),
    ('malformed', 'INVALID_CATALOG'), ('candidates', 'INVALID_CATALOG'),
    ('redirect', 'HTTP_FAILED'), ('304', 'INVALID_CATALOG'), ('oversize', 'INVALID_CATALOG'),
    ('duplicate_keys', 'INVALID_CATALOG')])
@pytest.mark.parametrize('cached', [False, True])
def test_failures_keep_valid_cache_or_builtin(tmp_path, mode, code, cached):
    clock, calls = Clock(), []
    if cached:
        with client(tmp_path, ok_handler, clock=clock) as initial:
            initial.refresh_background().result(timeout=3)
        clock.next()
    def handler(request):
        calls.append(request)
        if mode == 'network':
            raise httpx.ConnectError('synthetic offline error')
        if mode == 'malformed':
            return httpx.Response(200, json={'version': 'invalid'})
        if mode == 'candidates':
            kind = request.url.path.rsplit('/', 1)[-1]
            payload = catalogs()[kind]
            payload['candidates'] = []
            return httpx.Response(200, json=payload)
        if mode == 'redirect':
            return httpx.Response(302, headers={'location': 'https://unrelated.example.test'})
        if mode == 'oversize':
            return httpx.Response(200, content=b'x'*(1024*1024+1), headers={'content-type': 'application/json'})
        if mode == 'duplicate_keys':
            return httpx.Response(200, content=b'{"version":1,"version":2}', headers={'content-type': 'application/json'})
        if mode == '304' and cached:
            return httpx.Response(304, headers={'etag': '"wrong-etag"'})
        return httpx.Response(int(mode))
    with client(tmp_path, handler, clock=clock) as catalog:
        previous = catalog.load_prices()
        old = (catalog.cache_dir/'model-prices.json').read_bytes() if cached else None
        statuses = catalog.refresh_background().result(timeout=3)
        assert all(value.code == code for value in statuses.values())
        assert catalog.load_prices() == previous
        assert all(value.origin == ('cache' if cached else 'builtin') for value in statuses.values())
        if cached:
            assert (catalog.cache_dir/'model-prices.json').read_bytes() == old
    assert len(calls) == 2


def test_malformed_cache_falls_back_and_does_not_send_its_etag(tmp_path):
    clock = Clock()
    with client(tmp_path, ok_handler, clock=clock) as catalog:
        catalog.refresh_background().result(timeout=3)
        path = catalog.cache_dir/'source-whitelist.json'
        path.write_text('{broken', encoding='utf-8')
    clock.next()
    headers = []
    def handler(request):
        headers.append(request.headers.get('if-none-match'))
        return ok_handler(request)
    with client(tmp_path, handler, clock=clock) as catalog:
        assert catalog.status('source-whitelist').code == 'CACHE_INVALID'
        assert not catalog.load_lists().candidates
        catalog.refresh_background().result(timeout=3)
    assert headers == [None, '"model-prices-v1"']


def test_refresh_background_six_hour_limit_including_failures_and_restart(tmp_path):
    clock, calls = Clock(), []
    def handler(request):
        calls.append(request)
        return httpx.Response(404)
    with client(tmp_path, handler, clock=clock) as catalog:
        catalog.refresh_background().result(timeout=3)
        assert catalog.refresh_background() is None
        clock.now += REFRESH_SECONDS-1
        assert catalog.refresh_background() is None
    with client(tmp_path, handler, clock=clock) as reopened:
        assert reopened.refresh_background() is None
        clock.now += 1
        reopened.refresh_background().result(timeout=3)
    assert len(calls) == 4


def test_auto_refresh_is_background_and_load_does_not_wait(tmp_path):
    entered, release, threads = Event(), Event(), []
    def handler(request):
        threads.append(get_ident()); entered.set()
        release.wait(2)
        return ok_handler(request)
    with client(tmp_path, handler, auto_refresh=True) as catalog:
        current = get_ident()
        assert catalog.load_lists().updated == '2026-09-27'
        assert entered.wait(1)
        first = catalog.refresh_background()
        assert not first.done() and catalog.refresh_background() is first
        assert catalog.load_prices()['version'] == '2026-09-27'
        release.set()
        first.result(timeout=3)
    assert threads and all(thread != current for thread in threads)


@pytest.mark.parametrize('switch', [False, True])
def test_logout_or_account_switch_discards_late_response(tmp_path, switch):
    state, entered, release = [session()], Event(), Event()
    def handler(request):
        entered.set(); release.wait(2)
        return ok_handler(request)
    catalog = client(tmp_path, handler, state=state)
    future = catalog.refresh_background()
    try:
        assert entered.wait(1)
        if switch:
            state[0] = session(subject='different-account')
        else:
            catalog.close()
        release.set()
        future.result(timeout=3)
        assert not (catalog.cache_dir/'source-whitelist.json').exists()
        if switch:
            assert catalog.status('source-whitelist').code == 'SESSION_UNAVAILABLE'
        else:
            with pytest.raises(PlanError, match='关闭'):
                catalog.load_lists()
    finally:
        release.set()
        catalog.close()


def test_cache_account_isolation_and_owner_validation(tmp_path):
    clock = Clock()
    with client(tmp_path, ok_handler, clock=clock) as first:
        first.refresh_background().result(timeout=3)
        original = (first.cache_dir/'source-whitelist.json').read_bytes()
    second_state = [session(subject='second')]
    with client(tmp_path, ok_handler, state=second_state, clock=clock) as second:
        assert second.status('source-whitelist').origin == 'builtin'
        assert second.cache_dir != first.cache_dir
        second.cache_dir.mkdir(parents=True)
        (second.cache_dir/'source-whitelist.json').write_bytes(original)
    with client(tmp_path, ok_handler, state=second_state, clock=clock) as second:
        assert second.status('source-whitelist').code == 'CACHE_INVALID'
        assert second.load_lists().updated == '2026-09-27'


def test_cache_write_failure_never_replaces_previous_payload(tmp_path, monkeypatch):
    with client(tmp_path, ok_handler) as catalog:
        def failed(*args):
            raise OSError('synthetic disk full')
        monkeypatch.setattr(catalog, '_write', failed)
        states = catalog.refresh_background().result(timeout=3)
        assert all(state.code == 'CACHE_WRITE_FAILED' for state in states.values())
        assert catalog.load_lists().updated == '2026-09-27'
        assert catalog.load_prices()['version'] == '2026-09-27'


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(candidates=[]), lambda p: p.pop('blocked'),
    lambda p: p.update(version=True), lambda p: p['whitelist'][0].update(reason=''),
    lambda p: p['whitelist'][0].update(category='bad'),
    lambda p: p['github_orgs'][0].update(owner='bad/owner'),
    lambda p: p['blocked_keywords'].update(adult=[]),
])
def test_exact_server_source_schema_validation(mutate):
    data = catalogs()['source-whitelist']
    assert parse_lists(data, allow_candidates=False).candidates == ()
    mutate(data)
    with pytest.raises(SourceListsError):
        parse_lists(data, allow_candidates=False)


@pytest.mark.parametrize('mutate', [lambda p: p.update(unit='per_token'),
    lambda p: p['models'][0].update(output_per_mtok=-1),
    lambda p: p['models'][0].update(output_per_mtok=True),
    lambda p: p['models'][0].update(output_per_mtok=float('nan')),
    lambda p: p['models'][0].update(currency='unknown'),
    lambda p: p['models'][0].update(checked_on='yesterday'),
    lambda p: p['models'][0].update(verified='true'),
    lambda p: p['models'].append(deepcopy(p['models'][0])),
])
def test_price_validation(mutate):
    data = catalogs()['model-prices']
    mutate(data)
    with pytest.raises(CatalogFormatError):
        validate_prices(data)


def test_price_return_is_a_copy_and_missing_session_has_visible_status(tmp_path):
    state = [session()]
    with client(tmp_path, ok_handler, state=state) as catalog:
        prices = catalog.load_prices()
        prices['models'][0]['output_per_mtok'] = 999999
        assert catalog.load_prices()['models'][0]['output_per_mtok'] != 999999
        state[0] = None
        catalog.refresh_background().result(timeout=3)
        assert catalog.status('source-whitelist').code == 'SESSION_UNAVAILABLE'


def test_server_lists_feed_pipeline_without_candidates_or_fetching_blocked(tmp_path):
    from refactor.learning.course.research_pipeline import ResearchPipeline
    def handler(request):
        kind = request.url.path.rsplit('/', 1)[-1]
        payload = catalogs()[kind]
        if kind == 'source-whitelist':
            payload['blocked'].append({'match': 'new-block.test', 'reason': 'synthetic policy test'})
        return httpx.Response(200, json=payload)
    def forbidden(*args, **kwargs):
        pytest.fail('server-blocked source must not be fetched')
    with client(tmp_path, handler) as catalog:
        catalog.refresh_background().result(timeout=3)
        with ResearchPipeline(lists_provider=catalog.load_lists, fetcher=forbidden) as pipeline:
            result = pipeline.run([{'url': 'https://new-block.test/document', 'title': 'Python'}])
        assert result.attempted == 0 and result.rejected[0].code == 'blocked'


def test_invalid_lists_do_not_block_valid_prices_update(tmp_path):
    def handler(request):
        if request.url.path.endswith('source-whitelist'):
            return httpx.Response(200, json={'version': 999})
        return ok_handler(request)
    with client(tmp_path, handler) as catalog:
        statuses = catalog.refresh_background().result(timeout=3)
        assert statuses['source-whitelist'].code == 'INVALID_CATALOG'
        assert statuses['model-prices'].code == 'UPDATED'


def test_http_endpoint_and_path_traversal_account_are_rejected(tmp_path):
    state = session()
    with pytest.raises(ValueError, match='HTTPS'):
        CatalogClient(account_id=state.identity.account_id, data_root=tmp_path,
            session_provider=lambda: state, api_base_url='http://account.example.test', ca_bundle=CA)
    with pytest.raises(ValueError, match='账号 ID'):
        CatalogClient(account_id='../other', data_root=tmp_path,
            session_provider=lambda: state, api_base_url='https://account.example.test', ca_bundle=CA)

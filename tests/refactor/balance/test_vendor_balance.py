import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import httpx
import pytest

from refactor.learning.connections import balance

KEY = 'sk-test-never-log-this'
DS = {'is_available': True, 'balance_infos': [{'currency': 'CNY', 'total_balance': '12.3456'}]}


@pytest.mark.parametrize('provider,url,path,payload,amount,currency', [
    ('deepseek', 'https://api.deepseek.com/anthropic', '/user/balance', DS, '12.3456', 'CNY'),
    ('kimi', 'https://api.moonshot.cn/v1', '/v1/users/me/balance',
     {'status': True, 'code': 0, 'data': {'available_balance': 3.25}}, '3.25', 'CNY'),
    ('relay_openai', 'https://api.siliconflow.cn/v1', '/v1/user/info',
     {'status': True, 'code': 20000, 'data': {'balance': '1', 'chargeBalance': '2', 'totalBalance': '3'}}, '3', 'CNY'),
    ('relay_openai', 'https://openrouter.ai/api/v1', '/api/v1/credits',
     {'data': {'total_credits': 20.75, 'total_usage': 1.25}}, '19.50', 'USD'),
])
def test_official_get_contracts(provider, url, path, payload, amount, currency):
    def reply(request):
        assert request.method == 'GET' and request.url.path == path
        assert request.headers['Authorization'] == 'Bearer ' + KEY
        return httpx.Response(200, json=payload)
    client = balance.BalanceClient(http_client=httpx.MockTransport(reply))
    got = client.query(provider, url, KEY)
    assert got['balances'] == [{'amount': amount, 'currency': currency}]
    assert got['label'] == '账户余额' and got['updated_at']
    assert KEY not in json.dumps(got)


@pytest.mark.parametrize('status', [301, 302, 401, 403, 404, 429, 500])
def test_http_failure_or_redirect_is_absence_without_follow(status, caplog):
    seen = []
    def reply(r):
        seen.append(r.url.host)
        return httpx.Response(status, headers={'Location': 'https://other.test/stolen'}, json={'error': KEY})
    client = balance.BalanceClient(http_client=httpx.MockTransport(reply))
    assert client.query('deepseek', 'https://api.deepseek.com', KEY) is None
    assert seen == ['api.deepseek.com'] and KEY not in caplog.text


@pytest.mark.parametrize('body', ['{', '[]', '{"error":"' + KEY + '"}',
    '{"is_available":true,"balance_infos":[]}',
    '{"is_available":true,"balance_infos":[{"currency":"CNY","total_balance":"NaN"}]}',
    '{"is_available":true,"balance_infos":[{"currency":"USD","total_balance":true}]}',
    '{"is_available":true,"balance_infos":[{"currency":"CNY","total_balance":"1e-999999999"}]}', 'x' * 65537],
    ids=['invalid-json', 'wrong-type', 'vendor-error', 'empty', 'nonfinite', 'boolean', 'huge-exponent', 'oversized'])
def test_bad_payload_never_becomes_zero_or_error(body, caplog):
    client = balance.BalanceClient(http_client=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    assert client.query('deepseek', 'https://api.deepseek.com', KEY) is None
    assert KEY not in caplog.text


def test_zero_and_negative_balances_are_preserved():
    payload = dict(is_available=False, balance_infos=[dict(currency='CNY', total_balance='0'), dict(currency='USD', total_balance='-1.25')])
    client = balance.BalanceClient(http_client=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    assert client.query('deepseek', 'https://api.deepseek.com', KEY)['balances'][1]['amount'] == '-1.25'


def test_timeout_and_cancel_are_bounded(monkeypatch):
    monkeypatch.setattr(balance, 'TIMEOUT', .12)
    closed = threading.Event()
    async def slow(r):
        try:
            await asyncio.sleep(20)
        finally:
            closed.set()
    client = balance.BalanceClient(http_client=httpx.MockTransport(slow))
    start = time.monotonic()
    assert client.query('deepseek', 'https://api.deepseek.com', KEY) is None
    assert time.monotonic() - start < .8 and closed.is_set()
    cancel = threading.Event()
    with ThreadPoolExecutor() as pool:
        job = pool.submit(client.query, 'deepseek', 'https://api.deepseek.com', KEY, refresh=True, cancel=cancel)
        cancel.set()
        assert job.result(timeout=1) is None


def test_cache_success_failure_refresh_expiry_and_key_isolation():
    tick, seen = [0.0], []
    def reply(r):
        seen.append(r.headers['Authorization'])
        return httpx.Response(200 if len(seen) != 2 else 401, json=DS)
    client = balance.BalanceClient(http_client=httpx.MockTransport(reply), clock=lambda: tick[0])
    args = ('deepseek', 'https://api.deepseek.com', KEY)
    first = client.query(*args)
    first['balances'][0]['amount'] = 'bad'
    assert client.query(*args)['balances'][0]['amount'] == '12.3456' and len(seen) == 1
    assert client.query(*args, refresh=True) is None
    assert client.query(*args) is None and len(seen) == 2
    tick[0] = balance.TTL + 1
    assert client.query(*args) and len(seen) == 3
    assert client.query(*args[:2], KEY + '-other') and len(seen) == 4
    assert KEY not in repr(client._cache)


@pytest.mark.parametrize('currency,expected', [('CNY', 'CNY'), ('USD', 'USD'), ('TOKENS', None), ('CUSTOM', None)])
def test_relay_units_and_prefix(currency, expected):
    paths = []
    def reply(r):
        path = r.url.path
        paths.append(path)
        if path == '/tenant/api/status':
            assert 'authorization' not in r.headers
            return httpx.Response(200, json={'success': True, 'data': {'quota_display_type': currency}})
        assert r.headers['authorization'] == 'Bearer ' + KEY
        payload = {'object': 'billing_subscription', 'hard_limit_usd': 20} if path.endswith('subscription') else {'object': 'list', 'total_usage': 125}
        return httpx.Response(200, json=payload)
    client = balance.BalanceClient(http_client=httpx.MockTransport(reply))
    value = client.query('relay_anthropic', 'https://relay.test/tenant/v1/messages', KEY)
    if expected:
        assert value['balances'] == [{'amount': '18.75', 'currency': currency}]
        assert value['label'] == '站点剩余额度' and len(paths) == 3
    else:
        assert value is None and len(paths) == 1


def test_relay_no_interface_is_cached_absence():
    seen = []
    client = balance.BalanceClient(http_client=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(404)))
    for _ in range(2):
        assert client.query('relay_openai', 'https://relay.test/v1', KEY) is None
    assert len(seen) == 1


def test_relay_total_deadline_covers_all_requests(monkeypatch):
    monkeypatch.setattr(balance, 'TIMEOUT', .12)
    async def slow(r):
        await asyncio.sleep(.08)
        return httpx.Response(200, json={'success': True, 'data': {'quota_display_type': 'USD'}})
    start = time.monotonic()
    client = balance.BalanceClient(http_client=httpx.MockTransport(slow))
    assert client.query('relay_openai', 'https://relay.test/v1', KEY) is None
    assert time.monotonic() - start < .7


@pytest.mark.parametrize('settings,limit,expected', [({'display_in_currency': True}, 20, '18.75'),
    ({'display_in_currency': False}, 20, None), ({}, 20, None),
    ({'quota_display_type': 'USD'}, 100000000, None)])
def test_relay_legacy_units_missing_units_and_unlimited(settings, limit, expected):
    def reply(r):
        if r.url.path.endswith('/api/status'):
            payload = {'success': True, 'data': settings}
        elif r.url.path.endswith('subscription'):
            payload = {'object': 'billing_subscription', 'hard_limit_usd': limit}
        else:
            payload = {'object': 'list', 'total_usage': 125}
        return httpx.Response(200, json=payload)
    value = balance.BalanceClient(http_client=httpx.MockTransport(reply)).query('relay_openai', 'https://relay.test', KEY)
    assert (value['balances'][0]['amount'] if value else None) == expected


def test_concurrent_requests_share_fetch():
    seen = []
    started, finish = threading.Event(), threading.Event()
    async def slow(r):
        seen.append(r)
        started.set()
        while not finish.is_set():
            await asyncio.sleep(.01)
        return httpx.Response(200, json=DS)
    client = balance.BalanceClient(http_client=httpx.MockTransport(slow))
    with ThreadPoolExecutor() as pool:
        a = pool.submit(client.query, 'deepseek', 'https://api.deepseek.com', KEY)
        assert started.wait(1)
        b = pool.submit(client.query, 'deepseek', 'https://api.deepseek.com', KEY)
        finish.set()
        assert a.result(timeout=1) == b.result(timeout=1)
    assert len(seen) == 1


@pytest.mark.parametrize('provider,url', [('qwen', 'https://dashscope.aliyuncs.com/api/v1'),
    ('openai', 'https://api.openai.com/v1'), ('zhipu', 'https://open.bigmodel.cn/api/paas/v4'),
    ('anthropic', 'https://api.anthropic.com'), ('gemini', 'https://generativelanguage.googleapis.com/v1beta'),
    ('deepseek', 'https://other.test'), ('kimi', 'https://api.moonshot.ai/v1'),
    ('relay_openai', 'https://api.openai.com/v1'), ('deepseek', 'https://api.deepseek.com:123/v1'),
    ('relay_openai', 'http://public.test'), ('relay_openai', 'https://user:secret@host.test')])
def test_unsupported_does_not_send(provider, url):
    def reject(r):
        pytest.fail('Unsupported endpoint must not be contacted')
    assert balance.BalanceClient(http_client=httpx.MockTransport(reject)).query(provider, url, KEY) is None

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx

from refactor.learning.connections.balance import BalanceClient


def test_cancelled_owner_does_not_cancel_shared_fetch_or_cache_absence():
    started = threading.Event()
    finish = threading.Event()
    requests = []

    async def reply(request):
        requests.append(request)
        started.set()
        while not finish.is_set():
            await asyncio.sleep(.005)
        return httpx.Response(200, json={
            'is_available': True,
            'balance_infos': [{'currency': 'CNY', 'total_balance': '10'}],
        })

    client = BalanceClient(http_client=httpx.MockTransport(reply))
    args = ('deepseek', 'https://api.deepseek.com', 'sk-219-fake')
    owner_cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(client.query, *args, cancel=owner_cancel)
        assert started.wait(1)
        waiter = pool.submit(client.query, *args)
        owner_cancel.set()
        owner_value = owner.result(timeout=1)
        finish.set()
        waiter_value = waiter.result(timeout=2)

    cached_value = client.query(*args)
    assert owner_value is None
    assert waiter_value['balances'] == [{'amount': '10', 'currency': 'CNY'}]
    assert cached_value == waiter_value
    assert len(requests) == 1


def test_cancelled_waiter_does_not_cancel_owner_or_prevent_success_cache():
    started = threading.Event()
    finish = threading.Event()

    async def reply(request):
        started.set()
        while not finish.is_set():
            await asyncio.sleep(.005)
        return httpx.Response(200, json={
            'is_available': True,
            'balance_infos': [{'currency': 'CNY', 'total_balance': '7'}],
        })

    client = BalanceClient(http_client=httpx.MockTransport(reply))
    args = ('deepseek', 'https://api.deepseek.com', 'sk-219-other-fake')
    waiter_cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(client.query, *args)
        assert started.wait(1)
        waiter = pool.submit(client.query, *args, cancel=waiter_cancel)
        waiter_cancel.set()
        assert waiter.result(timeout=1) is None
        finish.set()
        owner_value = owner.result(timeout=2)

    assert owner_value['balances'] == [{'amount': '7', 'currency': 'CNY'}]
    assert client.query(*args) == owner_value

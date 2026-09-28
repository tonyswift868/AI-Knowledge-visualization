import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx

from refactor.learning.connections.balance import BalanceClient
from refactor.learning.connections.gateway import ModelGateway


def test_query_saved_preset_not_active_and_no_usage(tmp_path, vault):
    gateway = ModelGateway('mfa1_' + 'a' * 64, tmp_path)
    first = gateway.save_preset('deepseek', api_key='first-key')
    second = gateway.save_preset('deepseek', api_key='second-key')
    seen = []
    def reply(r):
        seen.append(r.headers['Authorization'])
        return httpx.Response(200, json={'is_available': True, 'balance_infos': [{'currency': 'CNY', 'total_balance': '8'}]})
    gateway._balance_client = BalanceClient(http_client=httpx.MockTransport(reply))
    assert gateway.balance(first['id'])['balances'][0]['amount'] == '8'
    assert seen == ['Bearer first-key'] and gateway.get_configuration()['id'] == second['id']
    assert gateway.usage_log.summary('2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z')['total']['calls'] == 0
    gateway.delete_preset(first['id'])
    assert gateway.balance(first['id']) is None
    gateway.close()


def test_close_cancels_and_key_edit_discards_old_result(tmp_path):
    gateway = ModelGateway('mfa1_' + 'a' * 64, tmp_path)
    config = gateway.save_preset('deepseek', api_key='old-key')
    started, release = threading.Event(), threading.Event()
    async def slow(r):
        started.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return httpx.Response(200, json={'is_available': True, 'balance_infos': [{'currency': 'USD', 'total_balance': '9'}]})
    gateway._balance_client = BalanceClient(http_client=httpx.MockTransport(slow))
    with ThreadPoolExecutor() as pool:
        job = pool.submit(gateway.balance, config['id'])
        assert started.wait(1)
        gateway.save_preset('deepseek', preset_id=config['id'], api_key='new-key')
        release.set()
        assert job.result(timeout=2) is None
        started.clear()
        release.clear()
        job = pool.submit(gateway.balance, config['id'], refresh=True)
        assert started.wait(1)
        gateway.close()
        assert job.result(timeout=1) is None


def test_unsupported_never_reads_secret(tmp_path, vault):
    gateway = ModelGateway('mfa1_' + 'a' * 64, tmp_path)
    config = gateway.save_preset('qwen', api_key='key')
    before = vault.reads
    assert gateway.balance(config['id']) is None and vault.reads == before
    gateway.close()

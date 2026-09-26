import httpx
import pytest


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    async def async_block(*args, **kwargs):
        raise AssertionError('Real network is forbidden in provider tests')

    def sync_block(*args, **kwargs):
        raise AssertionError('Real network is forbidden in provider tests')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', async_block)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', sync_block)

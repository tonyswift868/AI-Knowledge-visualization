import threading

import httpx
import pytest

from refactor.learning.connections import credentials, providers


class MemoryVault:
    def __init__(self):
        self.values, self.reads, self.lock = {}, 0, threading.RLock()

    def get_password(self, service, name):
        with self.lock:
            self.reads += 1
            return self.values.get((service, name))

    def set_password(self, service, name, value):
        with self.lock:
            self.values[(service, name)] = value

    def delete_password(self, service, name):
        with self.lock:
            self.values.pop((service, name), None)


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    backend = MemoryVault()
    monkeypatch.setattr(credentials, '_get_keyring', lambda: backend)
    def blocked(*args, **kwargs):
        raise AssertionError('No real provider, credentials or network allowed')
    async def async_blocked(*args, **kwargs):
        blocked()
    monkeypatch.setattr(providers, 'complete', blocked)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', async_blocked)
    return backend

"""Keep truncation tests out of the user's Windows Credential Manager."""

import threading

import pytest

from refactor.learning.connections import credentials


class MemoryVault:
    def __init__(self):
        self.values = {}
        self.lock = threading.RLock()

    def get_password(self, service, name):
        with self.lock:
            return self.values.get((service, name))

    def set_password(self, service, name, value):
        with self.lock:
            self.values[(service, name)] = value

    def delete_password(self, service, name):
        with self.lock:
            self.values.pop((service, name), None)


@pytest.fixture(autouse=True)
def in_memory_credentials(monkeypatch):
    """The imported gateway helpers use AccountCredentials directly."""
    backend = MemoryVault()
    monkeypatch.setattr(credentials, "_get_keyring", lambda: backend)
    return backend

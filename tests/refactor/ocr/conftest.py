"""All OCR checks use local bundled models; network access is a test failure."""
import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('离线 OCR 和测试不得访问网络')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)

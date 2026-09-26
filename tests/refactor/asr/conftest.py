import os
import socket

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def prohibit_external_network(monkeypatch):
    """Tests may reach their loopback HTTP fixture, never the Internet."""
    real_connect = socket.create_connection

    def local_only(address, *args, **kwargs):
        if address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError(f"External network forbidden in ASR tests: {address[0]}")
        return real_connect(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", local_only)


@pytest.fixture(scope="session")
def qt_app():
    from PySide6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication([])

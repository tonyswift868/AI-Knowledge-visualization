from __future__ import annotations

import socket
import time

import httpx
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("uvicorn")

from desktop.runtime import LocalService  # noqa: E402


class _HealthResponse:
    def __init__(self, payload: object) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> object:
        return self._payload


def test_constructor_reserves_selected_port_until_stop() -> None:
    service = LocalService()
    challenger = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            challenger.bind(("127.0.0.1", service.port))
    finally:
        challenger.close()
        service.stop()

    assert service._listener is None


def test_health_requires_mentorflow_identity_not_any_http_200() -> None:
    assert not LocalService._is_expected_health(_HealthResponse({"status": "ok"}))
    assert not LocalService._is_expected_health(_HealthResponse({"status": "ok", "service": "Other"}))
    assert LocalService._is_expected_health(
        _HealthResponse({"status": "ok", "service": "MentorFlow"})
    )


def test_start_and_stop_are_deterministic() -> None:
    service = LocalService()
    service.start()
    try:
        deadline = time.monotonic() + 20
        response: httpx.Response | None = None
        while time.monotonic() < deadline:
            try:
                candidate = httpx.get(f"{service.origin}/api/health", timeout=1)
            except httpx.HTTPError:
                time.sleep(0.1)
                continue
            if candidate.status_code == 200:
                response = candidate
                break
            time.sleep(0.1)
        assert response is not None
        assert LocalService._is_expected_health(response)
        assert service.thread is not None and service.thread.is_alive()
    finally:
        service.stop()

    assert service.thread is not None and not service.thread.is_alive()
    assert service.health_thread is not None and not service.health_thread.is_alive()
    assert service._listener is None

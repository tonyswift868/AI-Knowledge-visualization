from __future__ import annotations

import io
import json
import urllib.error

from app import asr_supervisor


class _Response:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._body = body
        self._offset = 0
        self.headers = headers or {}
        self.read_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            size = len(self._body) - self._offset
        start = self._offset
        self._offset += size
        return self._body[start : start + size]


def _patch_urlopen(monkeypatch, response):
    monkeypatch.setattr(asr_supervisor.urllib.request, "urlopen", lambda *_args, **_kwargs: response)


def test_request_accepts_small_response(monkeypatch):
    body = json.dumps({"ok": True, "engine": asr_supervisor.EXPECTED_ENGINE}).encode()
    response = _Response(body, {"Content-Length": str(len(body))})
    _patch_urlopen(monkeypatch, response)

    assert asr_supervisor._request("/health") == {"ok": True, "engine": asr_supervisor.EXPECTED_ENGINE}
    assert response.read_sizes
    assert max(response.read_sizes) <= asr_supervisor.MAX_RESPONSE_BYTES + 1


def test_request_rejects_oversized_response_without_content_length(monkeypatch):
    body = b"x" * (asr_supervisor.MAX_RESPONSE_BYTES + 1)
    response = _Response(body)
    _patch_urlopen(monkeypatch, response)

    assert asr_supervisor._request("/health") == {
        "ok": False,
        "error": "worker_response_too_large",
    }
    assert sum(response.read_sizes) <= asr_supervisor.MAX_RESPONSE_BYTES + 1


def test_request_rejects_declared_oversized_response_before_reading(monkeypatch):
    response = _Response(b"secret body", {"Content-Length": str(asr_supervisor.MAX_RESPONSE_BYTES + 1)})
    _patch_urlopen(monkeypatch, response)

    assert asr_supervisor._request("/health") == {
        "ok": False,
        "error": "worker_response_too_large",
    }
    assert response.read_sizes == []


def test_request_preserves_http_error_code_when_error_body_is_oversized(monkeypatch):
    body = b"secret upstream error" * ((asr_supervisor.MAX_RESPONSE_BYTES // 20) + 2)
    error = urllib.error.HTTPError(
        "http://127.0.0.1:9882/health",
        502,
        "Bad Gateway",
        {"Content-Length": str(len(body))},
        io.BytesIO(body),
    )
    monkeypatch.setattr(asr_supervisor.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    result = asr_supervisor._request("/health")
    assert result == {"ok": False, "error": "worker_http_502"}
    assert "secret upstream error" not in json.dumps(result)

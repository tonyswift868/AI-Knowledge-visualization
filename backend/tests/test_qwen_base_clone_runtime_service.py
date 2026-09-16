from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import socket
import threading
import time
import wave
import zipfile
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import qwen_base_clone_runtime as bridge
from app import qwen_engine_launch_attestation as launch_attestation
from app import release_trust_policy
from app import teacher_voice_pack as voice_pack
from app.local_voice_runtime_supervisor import (
    INSTANCE_SECRET_ENV,
    LocalVoiceRuntimeSupervisor,
    LocalVoiceRuntimeError,
    _hmac_proof,
    _runtime_request_header_proof,
    _runtime_request_proof,
)
from app.windows_install_path_budget import calculate_windows_install_path_budget
from runtime import qwen_base_clone_server as child


def _cache_root(path):
    from scripts.test_storage_policy import test_io_path
    return test_io_path(path) if path.drive.upper() == 'D:' else path


@pytest.fixture(autouse=True)
def signed_fixture_clock(monkeypatch):
    class FixtureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 8, 12, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(release_trust_policy, "datetime", FixtureDateTime)


def _wav(*, frames: int, sample: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24_000)
        writer.writeframes(int(sample).to_bytes(2, "little", signed=True) * frames)
    return output.getvalue()


def test_qwen_overlay_rejects_locked_source_input_drift():
    with pytest.raises(RuntimeError, match="input hash mismatch"):
        child._transform_qwen_source("qwen_tts/core/__init__.py", b"changed upstream source\n")


@pytest.mark.parametrize("module_name", ["sox", "sox.file_info", "qwen_tts.core.tokenizer_25hz"])
def test_qwen_overlay_finder_blocks_sox_and_25hz_imports(module_name: str):
    finder = object.__new__(child._Qwen12HzOverlayFinder)
    finder.modules = {}
    with pytest.raises(ImportError, match="forbidden Qwen 25Hz/SoX import"):
        finder.find_spec(module_name)


def test_qwen_model_identity_rejects_25hz_config(tmp_path: Path, monkeypatch):
    model_root = tmp_path / "runtime" / "models" / "qwen3-tts-12hz-0.6b-base"
    main_content = json.dumps(
        {
            "model_type": "qwen3_tts",
            "tokenizer_type": "qwen3_tts_tokenizer_25hz",
            "tts_model_size": "0b6",
            "tts_model_type": "base",
        },
        sort_keys=True,
    ).encode("utf-8")
    speech_content = json.dumps(
        {"model_type": "qwen3_tts_tokenizer_12hz"},
        sort_keys=True,
    ).encode("utf-8")
    (model_root / "speech_tokenizer").mkdir(parents=True)
    (model_root / "config.json").write_bytes(main_content)
    (model_root / "speech_tokenizer" / "config.json").write_bytes(speech_content)
    monkeypatch.setattr(child, "QWEN_MODEL_CONFIG_SHA256", hashlib.sha256(main_content).hexdigest())
    monkeypatch.setattr(child, "QWEN_SPEECH_CONFIG_SHA256", hashlib.sha256(speech_content).hexdigest())
    with pytest.raises(RuntimeError, match="not the locked 12Hz Base 0.6B identity"):
        child._validate_qwen_12hz_model_identity(tmp_path)


def _binding(tmp_path: Path) -> voice_pack.QwenBaseClonePackBinding:
    references = []
    for style, frames, sample, transcript in (
        ("lively", 720, 900, "做得很好，我们把下一步也稳稳拿下。"),
        ("relaxed", 960, -700, "先别着急，我们慢慢把这个概念拆开。"),
    ):
        audio = _wav(frames=frames, sample=sample)
        path = tmp_path / "pack" / "references" / f"{style}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        references.append(
            voice_pack.QwenBaseCloneReference(
                style=style,
                relative_path=f"references/{style}.wav",
                audio_path=path,
                audio_bytes=len(audio),
                audio_sha256=hashlib.sha256(audio).hexdigest(),
                transcript=transcript,
                transcript_sha256=hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
                language="zh",
            )
        )
    return voice_pack.QwenBaseClonePackBinding(
        pack_id="mentorflow-original-teacher",
        version="1.0.0",
        manifest_sha256="a" * 64,
        engine=voice_pack.QwenBaseCloneEngineDependency(
            kind=voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
            catalog_id="mentorflow-qwen3-tts-runtime",
            version="0.1.0",
            manifest_sha256="b" * 64,
        ),
        references=tuple(references),
    )


class FakeBackend:
    def __init__(self) -> None:
        self.prompt_calls: list[tuple[str, str]] = []
        self.synthesis_calls: list[tuple[str, str]] = []

    def create_prompt(self, *, audio_path: Path, transcript: str, language: str):
        assert audio_path.is_relative_to(audio_path.parents[2])
        assert audio_path.is_file()
        self.prompt_calls.append((audio_path.name, transcript))
        return {"audio_sha256": hashlib.sha256(audio_path.read_bytes()).hexdigest(), "language": language}

    def synthesize(self, *, prompt, text: str, language: str) -> bytes:
        self.synthesis_calls.append((text, language))
        assert prompt["language"] == "zh"
        return _wav(frames=1200, sample=1200)


class FakeSupervisor:
    def __init__(self) -> None:
        self.plan = SimpleNamespace(host="127.0.0.1", port=8892, runtime_id=bridge.RUNTIME_ID)
        self.leases = 0
        self.authorized: list[tuple[str, str, int, str]] = []

    @asynccontextmanager
    async def synthesis_lease_async(self):
        self.leases += 1
        yield

    def authorize_loopback_request(
        self, *, method: str, path: str, content_length: int, body_sha256: str
    ):
        self.authorized.append((method, path, content_length, body_sha256))
        return {
            "X-MentorFlow-Request-Nonce": f"nonce-{len(self.authorized):020d}",
            "X-MentorFlow-Request-Header-Proof": "d" * 64,
            "X-MentorFlow-Request-Proof": "c" * 64,
        }


class InMemoryTransport:
    def __init__(self, state: child.QwenBaseCloneRuntimeState) -> None:
        self.state = state
        self.calls: list[tuple[str, int]] = []

    def post_json(
        self,
        path: str,
        body: bytes,
        *,
        headers,
        max_request_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ):
        assert len(body) <= max_request_bytes
        assert timeout_seconds > 0
        assert len(headers["X-MentorFlow-Request-Header-Proof"]) == 64
        assert len(headers["X-MentorFlow-Request-Proof"]) == 64
        self.calls.append((path, len(body)))
        payload = json.loads(body.decode("utf-8"))
        response = self.state.prepare(payload) if path == "/v1/prepare" else self.state.synthesize(payload)
        assert len(bridge._canonical_json_bytes(response)) <= max_response_bytes
        return response


def _prepare_request(binding: voice_pack.QwenBaseClonePackBinding):
    return voice_pack.QwenBaseClonePrepareRequest(
        schema=voice_pack.QWEN_BASE_CLONE_PREPARE_SCHEMA,
        binding=binding,
        binding_sha256=binding.binding_sha256,
        references_sha256=binding.references_sha256,
    )


def _synthesis_payload(
    binding: voice_pack.QwenBaseClonePackBinding,
    *,
    text: str,
    style: str = "relaxed",
) -> dict[str, object]:
    request = voice_pack._qwen_synthesis_request(
        binding,
        text=text,
        style=style,
        reply_kind="teaching.explain",
    )
    return {
        "schema": request.schema,
        "binding": bridge._binding_payload(binding, include_audio=False),
        "reference": bridge._reference_payload(request.reference, include_audio=False),
        "binding_sha256": request.binding_sha256,
        "engine_kind": request.engine_kind,
        "engine_catalog_id": request.engine_catalog_id,
        "engine_version": request.engine_version,
        "engine_manifest_sha256": request.engine_manifest_sha256,
        "pack_id": request.pack_id,
        "pack_version": request.pack_version,
        "pack_manifest_sha256": request.pack_manifest_sha256,
        "text": request.text,
        "text_sha256": request.text_sha256,
        "style": request.style,
        "reference_audio_sha256": request.reference_audio_sha256,
        "reference_transcript_sha256": request.reference_transcript_sha256,
        "reply_kind": request.reply_kind,
        "request_sha256": request.request_sha256,
    }


def test_typed_bridge_and_child_fake_model_close_prepare_synthesis_and_cache(tmp_path: Path):
    binding = _binding(tmp_path)
    backend = FakeBackend()
    state = child.QwenBaseCloneRuntimeState(backend=backend, cache_root=_cache_root(tmp_path / "cache"))
    transport = InMemoryTransport(state)
    supervisor = FakeSupervisor()
    runtime = bridge.QwenBaseCloneLoopbackRuntime(supervisor=supervisor, transport=transport)

    first_prepare = asyncio.run(runtime.prepare(_prepare_request(binding)))
    second_prepare = asyncio.run(runtime.prepare(_prepare_request(binding)))
    assert first_prepare.ready is True
    assert second_prepare.binding_sha256 == binding.binding_sha256
    assert len(backend.prompt_calls) == 2

    request = voice_pack._qwen_synthesis_request(
        binding,
        text="我们把链表的指针变化一步一步画出来。",
        style="relaxed",
        reply_kind="teaching.explain",
    )
    first_audio = asyncio.run(runtime.synthesize(request))
    second_audio = asyncio.run(runtime.synthesize(request))
    assert first_audio.cache == "miss"
    assert second_audio.cache == "hit"
    assert first_audio.audio == second_audio.audio
    assert first_audio.request_sha256 == request.request_sha256
    assert first_audio.binding_sha256 == binding.binding_sha256
    assert len(backend.synthesis_calls) == 1
    assert [path for path, _size in transport.calls] == [
        "/v1/prepare",
        "/v1/prepare",
        "/v1/synthesize",
        "/v1/synthesize",
    ]
    assert supervisor.leases == 4
    assert all(
        content_length > 0 and len(body_sha256) == 64
        for _method, _path, content_length, body_sha256 in supervisor.authorized
    )


def test_bridge_rechecks_reference_bytes_before_launch_or_transport(tmp_path: Path):
    binding = _binding(tmp_path)
    binding.references[0].audio_path.write_bytes(_wav(frames=800, sample=222))
    backend = FakeBackend()
    state = child.QwenBaseCloneRuntimeState(backend=backend, cache_root=_cache_root(tmp_path / "cache"))
    transport = InMemoryTransport(state)
    supervisor = FakeSupervisor()
    runtime = bridge.QwenBaseCloneLoopbackRuntime(supervisor=supervisor, transport=transport)

    with pytest.raises(LocalVoiceRuntimeError) as caught:
        asyncio.run(runtime.prepare(_prepare_request(binding)))
    assert caught.value.code == "VOICE_RUNTIME_REFERENCE_MISMATCH"
    assert transport.calls == []
    assert supervisor.leases == 0


def test_child_rejects_request_fingerprint_mismatch(tmp_path: Path):
    binding = _binding(tmp_path)
    backend = FakeBackend()
    state = child.QwenBaseCloneRuntimeState(backend=backend, cache_root=_cache_root(tmp_path / "cache"))
    state.prepare(
        {
            "schema": voice_pack.QWEN_BASE_CLONE_PREPARE_SCHEMA,
            "binding": bridge._binding_payload(binding, include_audio=True),
        }
    )
    request = voice_pack._qwen_synthesis_request(
        binding,
        text="这是一段绑定测试。",
        style="lively",
        reply_kind="teaching.encourage",
    )
    payload = {
        "schema": request.schema,
        "binding": bridge._binding_payload(binding, include_audio=False),
        "reference": bridge._reference_payload(request.reference, include_audio=False),
        "binding_sha256": request.binding_sha256,
        "engine_kind": request.engine_kind,
        "engine_catalog_id": request.engine_catalog_id,
        "engine_version": request.engine_version,
        "engine_manifest_sha256": request.engine_manifest_sha256,
        "pack_id": request.pack_id,
        "pack_version": request.pack_version,
        "pack_manifest_sha256": request.pack_manifest_sha256,
        "text": request.text,
        "text_sha256": request.text_sha256,
        "style": request.style,
        "reference_audio_sha256": request.reference_audio_sha256,
        "reference_transcript_sha256": request.reference_transcript_sha256,
        "reply_kind": request.reply_kind,
        "request_sha256": "0" * 64,
    }
    with pytest.raises(child.ProtocolError) as caught:
        state.synthesize(payload)
    assert caught.value.code == "BINDING_MISMATCH"
    assert backend.synthesis_calls == []


def test_child_request_hmac_is_process_body_and_nonce_bound_and_replay_safe():
    instance_key = "fixture-owned-instance-key-12345"
    nonce = "nonce_for_protocol_test_0001"
    body = b"request"
    body_sha256 = hashlib.sha256(body).hexdigest()
    pid = 44123
    header_proof = _runtime_request_header_proof(
        instance_key,
        nonce=nonce,
        method="POST",
        path="/v1/prepare",
        content_length=len(body),
        service_id=bridge.SERVICE_ID,
        runtime_id=bridge.RUNTIME_ID,
        pid=pid,
    )
    proof = _runtime_request_proof(
        instance_key,
        nonce=nonce,
        method="POST",
        path="/v1/prepare",
        body_sha256=body_sha256,
        service_id=bridge.SERVICE_ID,
        runtime_id=bridge.RUNTIME_ID,
        pid=pid,
    )
    authenticator = child.RequestAuthenticator(instance_key, pid=pid)
    authenticator.verify_headers(
        nonce=nonce,
        header_proof=header_proof,
        method="POST",
        path="/v1/prepare",
        content_length=len(body),
    )
    authenticator.verify_body(
        nonce=nonce,
        proof=proof,
        method="POST",
        path="/v1/prepare",
        body_sha256=body_sha256,
    )
    with pytest.raises(child.ProtocolError) as replayed:
        authenticator.verify_headers(
            nonce=nonce,
            header_proof=header_proof,
            method="POST",
            path="/v1/prepare",
            content_length=len(body),
        )
    assert replayed.value.code == "REQUEST_REPLAYED"

    other = child.RequestAuthenticator(instance_key, pid=pid)
    changed_nonce = "nonce_for_protocol_test_0002"
    changed_header_proof = _runtime_request_header_proof(
        instance_key,
        nonce=changed_nonce,
        method="POST",
        path="/v1/prepare",
        content_length=len(b"changed"),
        service_id=bridge.SERVICE_ID,
        runtime_id=bridge.RUNTIME_ID,
        pid=pid,
    )
    other.verify_headers(
        nonce=changed_nonce,
        header_proof=changed_header_proof,
        method="POST",
        path="/v1/prepare",
        content_length=len(b"changed"),
    )
    with pytest.raises(child.ProtocolError) as changed_body:
        other.verify_body(
            nonce=changed_nonce,
            proof=proof,
            method="POST",
            path="/v1/prepare",
            body_sha256=hashlib.sha256(b"changed").hexdigest(),
        )
    assert changed_body.value.code == "REQUEST_AUTH_INVALID"


def test_child_never_evicts_authenticated_request_nonces_within_one_generation():
    instance_key = "fixture-owned-instance-key-no-eviction"
    pid = 44124
    authenticator = child.RequestAuthenticator(instance_key, pid=pid)
    first_nonce = "nonce_no_eviction_00000000"

    def authorize_headers(nonce: str) -> None:
        authenticator.verify_headers(
            nonce=nonce,
            header_proof=_runtime_request_header_proof(
                instance_key,
                nonce=nonce,
                method="POST",
                path="/v1/synthesize",
                content_length=1,
                service_id=bridge.SERVICE_ID,
                runtime_id=bridge.RUNTIME_ID,
                pid=pid,
            ),
            method="POST",
            path="/v1/synthesize",
            content_length=1,
        )

    authorize_headers(first_nonce)
    for index in range(4_200):
        authorize_headers(f"nonce_no_eviction_{index + 1:08d}")
    with pytest.raises(child.ProtocolError) as replayed:
        authorize_headers(first_nonce)
    assert replayed.value.code == "REQUEST_REPLAYED"


def test_child_audio_cache_is_bounded_by_entries_and_total_bytes_and_base64_peak(
    tmp_path: Path, monkeypatch
):
    from scripts.test_storage_policy import test_io_path
    if tmp_path.drive.upper() == 'D:':
        tmp_path = test_io_path(tmp_path)
    binding = _binding(tmp_path)
    backend = FakeBackend()
    state = child.QwenBaseCloneRuntimeState(backend=backend, cache_root=_cache_root(tmp_path / "cache"))
    state.prepare(
        {
            "schema": voice_pack.QWEN_BASE_CLONE_PREPARE_SCHEMA,
            "binding": bridge._binding_payload(binding, include_audio=True),
        }
    )
    one_audio_bytes = len(_wav(frames=1200, sample=1200))
    monkeypatch.setattr(child, "MAX_AUDIO_CACHE_ENTRIES", 2)
    monkeypatch.setattr(child, "MAX_AUDIO_CACHE_BYTES", one_audio_bytes * 2)
    for index in range(3):
        state.synthesize(_synthesis_payload(binding, text=f"缓存边界测试 {index}"))
    assert len(state._audio_cache) == 2
    assert state._audio_cache_bytes == sum(len(audio) for audio in state._audio_cache.values())
    assert state._audio_cache_bytes <= child.MAX_AUDIO_CACHE_BYTES

    monkeypatch.setattr(child, "MAX_AUDIO_BASE64_BYTES", 64)
    with pytest.raises(child.ProtocolError) as too_large:
        state.synthesize(_synthesis_payload(binding, text="编码峰值必须在编码前被拒绝"))
    assert too_large.value.code == "AUDIO_RESPONSE_TOO_LARGE"


def _authorized_http_headers(
    *,
    instance_key: str,
    path: str,
    body: bytes,
    nonce: str,
) -> dict[str, str]:
    pid = os.getpid()
    digest = hashlib.sha256(body).hexdigest()
    return {
        "Host": "127.0.0.1",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        child.REQUEST_NONCE_HEADER: nonce,
        child.REQUEST_HEADER_PROOF_HEADER: _runtime_request_header_proof(
            instance_key,
            nonce=nonce,
            method="POST",
            path=path,
            content_length=len(body),
            service_id=bridge.SERVICE_ID,
            runtime_id=bridge.RUNTIME_ID,
            pid=pid,
        ),
        child.REQUEST_PROOF_HEADER: _runtime_request_proof(
            instance_key,
            nonce=nonce,
            method="POST",
            path=path,
            body_sha256=digest,
            service_id=bridge.SERVICE_ID,
            runtime_id=bridge.RUNTIME_ID,
            pid=pid,
        ),
        "Connection": "close",
    }


def _raw_http_request(method: str, path: str, headers: dict[str, str]) -> bytes:
    lines = [f"{method} {path} HTTP/1.1", *(f"{key}: {value}" for key, value in headers.items())]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def _receive_all(connection: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = connection.recv(64 * 1024)
        except (ConnectionAbortedError, ConnectionResetError):
            # Windows may replace a best-effort overload response with a reset
            # when it closes a socket whose request was not fully consumed.
            return b"".join(chunks)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _wait_for_active_requests(server, expected: int, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while server.active_request_count != expected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.active_request_count == expected


def _health_after_deadline(server, nonce: str) -> bytes:
    port = int(server.server_address[1])
    headers = {
        "Host": "127.0.0.1",
        child.HEALTH_NONCE_HEADER: nonce,
        "Connection": "close",
    }
    with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
        connection.settimeout(1)
        connection.sendall(_raw_http_request("GET", "/health", headers))
        return _receive_all(connection)


@contextmanager
def _running_child_server(tmp_path: Path, **limits):
    instance_key = "fixture-server-instance-key-12345"
    server = child.create_server(
        pack_root=tmp_path,
        host="127.0.0.1",
        port=0,
        cache_root=_cache_root(tmp_path / "server-cache"),
        instance_key=instance_key,
        backend=FakeBackend(),
        **limits,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield server, instance_key
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_header_hmac_is_verified_before_body_read_and_body_has_deadline(tmp_path: Path):
    with _running_child_server(tmp_path, body_timeout_seconds=1.0) as (server, instance_key):
        port = int(server.server_address[1])
        body = b"{}"
        headers = _authorized_http_headers(
            instance_key=instance_key,
            path="/v1/prepare",
            body=body,
            nonce="invalid_header_proof_0001",
        )
        headers[child.REQUEST_HEADER_PROOF_HEADER] = "0" * 64
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.settimeout(0.75)
            started = time.monotonic()
            connection.sendall(_raw_http_request("POST", "/v1/prepare", headers))
            response = _receive_all(connection)
            elapsed = time.monotonic() - started
        assert b" 401 " in response
        assert elapsed < 0.75

    with _running_child_server(tmp_path, body_timeout_seconds=0.15) as (server, instance_key):
        port = int(server.server_address[1])
        body = b'{"schema":"never-sent"}'
        headers = _authorized_http_headers(
            instance_key=instance_key,
            path="/v1/prepare",
            body=body,
            nonce="body_timeout_protocol_0001",
        )
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.settimeout(2)
            connection.sendall(_raw_http_request("POST", "/v1/prepare", headers))
            response = _receive_all(connection)
        assert response == b"" or (
            b" 408 " in response and b"BODY_READ_TIMEOUT" in response
        )


def test_slow_headers_are_closed_and_worker_overload_rejects_health_with_503(tmp_path: Path):
    with _running_child_server(
        tmp_path,
        max_workers=1,
        request_queue_size=1,
        header_timeout_seconds=1.0,
    ) as (server, _instance_key):
        port = int(server.server_address[1])
        slow = socket.create_connection(("127.0.0.1", port), timeout=1)
        try:
            slow.settimeout(2)
            slow.sendall(b"POST /v1/prepare HTTP/1.1\r\nHost: 127.0.0.1\r\n")
            deadline = time.monotonic() + 1
            while server.active_request_count != 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.active_request_count == 1
            assert server.request_queue_size == 1

            health_headers = {
                "Host": "127.0.0.1",
                child.HEALTH_NONCE_HEADER: "bounded_health_nonce_0001",
                "Connection": "close",
            }
            with socket.create_connection(("127.0.0.1", port), timeout=1) as overloaded:
                overloaded.settimeout(1)
                overloaded.sendall(_raw_http_request("GET", "/health", health_headers))
                response = _receive_all(overloaded)
            assert response == b"" or b" 503 " in response

            slow_result = _receive_all(slow)
            assert slow_result == b"" or b" 400 " in slow_result or b" 408 " in slow_result
        finally:
            slow.close()


def test_absolute_header_deadline_stops_trickle_bytes_and_releases_worker_for_health(
    tmp_path: Path,
):
    timeout = 0.3
    with _running_child_server(
        tmp_path,
        max_workers=1,
        header_timeout_seconds=timeout,
    ) as (server, _instance_key):
        port = int(server.server_address[1])
        trickle = socket.create_connection(("127.0.0.1", port), timeout=1)
        try:
            trickle.settimeout(1)
            _wait_for_active_requests(server, 1)
            started = time.monotonic()
            request = b"POST /v1/prepare HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            for value in request:
                try:
                    trickle.sendall(bytes((value,)))
                except OSError:
                    break
                time.sleep(timeout / 2)
            _wait_for_active_requests(server, 0)
            assert time.monotonic() - started < timeout * 3
        finally:
            trickle.close()
        assert b" 200 " in _health_after_deadline(server, "absolute_header_health_0001")


def test_absolute_body_deadline_stops_authenticated_trickle_and_releases_worker_for_health(
    tmp_path: Path,
):
    timeout = 0.3
    with _running_child_server(
        tmp_path,
        max_workers=1,
        header_timeout_seconds=1.0,
        body_timeout_seconds=timeout,
    ) as (server, instance_key):
        port = int(server.server_address[1])
        body = b'{"schema":"authenticated-body-trickle"}'
        headers = _authorized_http_headers(
            instance_key=instance_key,
            path="/v1/prepare",
            body=body,
            nonce="absolute_body_request_0001",
        )
        trickle = socket.create_connection(("127.0.0.1", port), timeout=1)
        try:
            trickle.settimeout(1)
            trickle.sendall(_raw_http_request("POST", "/v1/prepare", headers))
            _wait_for_active_requests(server, 1)
            started = time.monotonic()
            for value in body:
                try:
                    trickle.sendall(bytes((value,)))
                except OSError:
                    break
                time.sleep(timeout / 2)
            _wait_for_active_requests(server, 0)
            assert time.monotonic() - started < timeout * 3
        finally:
            trickle.close()
        assert b" 200 " in _health_after_deadline(server, "absolute_body_health_0001")


def test_runtime_configuration_removes_instance_secret_even_when_validation_fails(monkeypatch):
    monkeypatch.setenv(child.INSTANCE_SECRET_ENV, "one-generation-secret-12345")
    monkeypatch.delenv("MENTORFLOW_VOICE_RUNTIME_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="runtime root is missing"):
        child._runtime_configuration()
    assert child.INSTANCE_SECRET_ENV not in os.environ


def _capability(tmp_path: Path):
    root = tmp_path / "engine"
    python = root / "runtime" / "python" / "python.exe"
    runner = root / "runtime" / "runner" / "qwen_base_clone_server.py"
    model = root / "runtime" / "models" / "qwen3-tts-12hz-0.6b-base"
    python.parent.mkdir(parents=True, exist_ok=True)
    runner.parent.mkdir(parents=True, exist_ok=True)
    model.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"MZ-fixture-portable-python")
    runner.write_text("# signed fixture runner\n", encoding="utf-8")
    (model / "config.json").write_text("{}\n", encoding="utf-8")
    dependency = voice_pack.QwenBaseCloneEngineDependency(
        kind=voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
        catalog_id="mentorflow-qwen3-tts-runtime",
        version="0.1.0",
        manifest_sha256="d" * 64,
    )
    verified_files = tuple(
        sorted(
            (
                path.relative_to(root).as_posix(),
                path.stat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in root.rglob("*")
            if path.is_file()
        )
    )
    verified_directories = tuple(
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir())
    )
    capability = voice_pack.VerifiedQwenEngineRegistrationCapability(
        voice_pack._QWEN_ENGINE_CAPABILITY_TOKEN,
        dependency,
        root,
        "e" * 64,
        verified_files,
        verified_directories,
        install_path_budget=calculate_windows_install_path_budget(
            item[0] for item in verified_files
        ),
    )
    return capability, root


def _trusted_test_engine_release(
    tmp_path: Path,
    *,
    registry_path: Path | None = None,
) -> dict[str, object]:
    """Build a complete fixed-test-root chain without starting a child/model."""

    package_id = "test-mentorflow-qwen3-tts-runtime"
    version = "0.1.0-test"
    leaf_key_id = "test-qwen-runtime-leaf-v1"
    engine_root = tmp_path / "engines"
    release_parent = engine_root / package_id
    package_root = release_parent / version
    payloads = {
        "runtime/python/python.exe": ("runtime_asset", b"MZ-fixture-portable-python"),
        "runtime/runner/qwen_base_clone_server.py": ("runtime_asset", b"# signed fixture runner\n"),
        "runtime/models/qwen3-tts-12hz-0.6b-base/config.json": ("model_asset", b"{}\n"),
    }
    files = []
    for relative, (role, content) in payloads.items():
        path = package_root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files.append(
            {
                "path": relative,
                "role": role,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )

    leaf_private = Ed25519PrivateKey.generate()
    leaf_public = leaf_private.public_key()
    raw_public = leaf_public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public_pem = leaf_public.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_path = release_parent / "release-public-key.pem"
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_bytes(public_pem)
    public_fingerprint = hashlib.sha256(raw_public).hexdigest()
    receipt = {
        "schema": release_trust_policy.TRUST_RECEIPT_SCHEMA,
        "algorithm": "ed25519",
        "key_id": leaf_key_id,
        "purpose": "mentorflow-engine-pack-release",
        "status": "test-only",
        "test_only": True,
        "created_at_utc": "2026-08-07T00:00:00Z",
        "fingerprint": {
            "algorithm": "sha256",
            "scope": "raw-ed25519-public-key",
            "value": public_fingerprint,
        },
        "public_key": {
            "encoding": "base64-raw-ed25519",
            "value": base64.b64encode(raw_public).decode("ascii"),
            "pem_filename": public_path.name,
            "pem_sha256": hashlib.sha256(public_pem).hexdigest(),
        },
        "rotation": {
            "starts_on": "2026-08-01",
            "rotate_after": "2026-09-01",
            "overlap_until": "2026-09-15",
            "overlap_days": 14,
            "predecessor_key_id": None,
        },
        "validity": {
            "signing": {"not_before": "2026-08-01", "not_after": "2026-09-01"},
            "verification": {"not_before": "2026-08-01", "not_after": "2026-09-15"},
        },
        "application_trust": {
            "private_key_allowed": False,
            "catalog_fields": {
                "key_id": leaf_key_id,
                "public_key": base64.b64encode(raw_public).decode("ascii"),
                "public_key_sha256": public_fingerprint,
            },
        },
    }
    receipt_payload = release_trust_policy._canonical_json_bytes(receipt)
    receipt["self_signature"] = {
        "algorithm": "ed25519",
        "key_id": leaf_key_id,
        "payload_sha256": hashlib.sha256(receipt_payload).hexdigest(),
        "signature": base64.b64encode(leaf_private.sign(receipt_payload)).decode("ascii"),
    }
    receipt_path = release_parent / "release-public-trust.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    root_private = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex("4d656e746f72466c6f772d746573742d72656c656173652d726f6f742d763121")
    )
    policy = {
        "schema": release_trust_policy.TRUST_POLICY_SCHEMA,
        "policy_id": "test-qwen-runtime-policy-v1",
        "root_key_id": "test-mentorflow-release-policy-root-v1",
        "test_only": True,
        "created_at_utc": "2026-08-07T00:00:00Z",
        "validity": {
            "not_before": "2026-08-01T00:00:00Z",
            "not_after": "2026-09-15T23:59:59Z",
        },
        "allowed_leaf_keys": [
            {
                "key_id": leaf_key_id,
                "public_key_sha256": public_fingerprint,
                "purposes": ["mentorflow-engine-pack-release"],
                "schemas": [voice_pack.QWEN_ENGINE_MANIFEST_SCHEMA],
            }
        ],
        "revoked_key_ids": [],
    }
    raw_policy = (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode("utf-8")
    policy_path = release_parent / "release-trust-policy.json"
    policy_path.write_bytes(raw_policy)
    policy_signature_path = release_parent / "release-trust-policy.sig"
    policy_signature_path.write_text(
        json.dumps(
            {
                "algorithm": "ed25519",
                "root_key_id": policy["root_key_id"],
                "signature": base64.b64encode(root_private.sign(raw_policy)).decode("ascii"),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    manifest = {
        "schema": voice_pack.QWEN_ENGINE_MANIFEST_SCHEMA,
        "kind": voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
        "package_id": package_id,
        "version": version,
        "install_path_budget": calculate_windows_install_path_budget(
            [
                *(item["path"] for item in files),
                "engine-manifest.json",
                "engine-manifest.sig",
            ]
        ),
        "files": files,
    }
    raw_manifest = release_trust_policy._canonical_json_bytes(manifest)
    manifest_path = package_root / "engine-manifest.json"
    manifest_path.write_bytes(raw_manifest)
    (package_root / "engine-manifest.sig").write_text(
        json.dumps(
            {
                "algorithm": "ed25519",
                "key_id": leaf_key_id,
                "signature": base64.b64encode(leaf_private.sign(raw_manifest)).decode("ascii"),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    archive_path = release_parent / f"{version}-win-x64.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package_root).as_posix())
    catalog = {
        "schema": voice_pack.QWEN_ENGINE_CATALOG_SCHEMA,
        "engines": [
            {
                "kind": voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
                "package_id": package_id,
                "version": version,
                "archive": {
                    "bytes": archive_path.stat().st_size,
                    "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                },
                "manifest": {
                    "bytes": len(raw_manifest),
                    "sha256": hashlib.sha256(raw_manifest).hexdigest(),
                },
                "signing_key_id": leaf_key_id,
            }
        ],
    }
    raw_catalog = release_trust_policy._canonical_json_bytes(catalog)
    catalog_path = release_parent / f"{version}-engine-catalog.json"
    catalog_path.write_bytes(raw_catalog)
    trust = release_trust_policy.verify_release_trust_chain(
        manifest_name="engine-manifest.json",
        manifest_value=manifest,
        public_key_path=public_path,
        trust_receipt_path=receipt_path,
        trust_policy_path=policy_path,
        trust_policy_signature_path=policy_signature_path,
        _allow_test_roots=True,
        _registry_path_for_tests=registry_path,
    )
    capability = voice_pack.verify_qwen_base_clone_engine_release(
        catalog=catalog,
        package_id=package_id,
        version=version,
        archive_path=archive_path,
        package_root=package_root,
        release_trust=trust,
        catalog_sha256=hashlib.sha256(raw_catalog).hexdigest(),
        catalog_bytes=len(raw_catalog),
        catalog_path=catalog_path,
    )
    descriptor = {
        "schema": bridge.ENGINE_REGISTRATION_SCHEMA,
        "enabled": True,
        "package_id": package_id,
        "version": version,
        "leaf_public_key": public_path.name,
        "leaf_trust_receipt": receipt_path.name,
        "trust_policy": policy_path.name,
        "trust_policy_signature": policy_signature_path.name,
        "engine_catalog": catalog_path.name,
        "engine_archive": archive_path.name,
        "engine_package": version,
    }
    descriptor_path = tmp_path / "registration.json"
    descriptor_path.write_text(json.dumps(descriptor, sort_keys=True), encoding="utf-8")
    return {
        "capability": capability,
        "package_root": package_root,
        "engine_root": engine_root,
        "descriptor": descriptor,
        "descriptor_path": descriptor_path,
        "policy": policy,
        "policy_path": policy_path,
        "policy_signature_path": policy_signature_path,
        "catalog_path": catalog_path,
        "archive_path": archive_path,
        "registry_path": registry_path,
    }


def _rewrite_test_policy(release: dict[str, object], policy: dict[str, object]) -> None:
    raw_policy = (json.dumps(policy, sort_keys=True, indent=2) + "\n").encode("utf-8")
    Path(release["policy_path"]).write_bytes(raw_policy)
    root_private = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex("4d656e746f72466c6f772d746573742d72656c656173652d726f6f742d763121")
    )
    Path(release["policy_signature_path"]).write_text(
        json.dumps(
            {
                "algorithm": "ed25519",
                "root_key_id": policy["root_key_id"],
                "signature": base64.b64encode(root_private.sign(raw_policy)).decode("ascii"),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows share-deny handles")
def test_launch_attestation_rejects_existing_writer_and_releases_all_handles(tmp_path: Path):
    capability, root = _capability(tmp_path)
    target = root / "runtime" / "models" / "qwen3-tts-12hz-0.6b-base" / "config.json"
    original = target.read_bytes()

    with target.open("r+b"):
        with pytest.raises(LocalVoiceRuntimeError) as existing_writer:
            launch_attestation.acquire_qwen_engine_launch_attestation(capability)
    assert existing_writer.value.code == "VOICE_ENGINE_LAUNCH_ATTESTATION_LOCK_FAILED"

    lease = launch_attestation.acquire_qwen_engine_launch_attestation(capability)
    lease.seal_for_launch()
    lease.assert_valid()
    with pytest.raises(OSError):
        target.write_bytes(b"blocked while generation lease is retained")
    lease.release()
    lease.release()

    target.write_bytes(original)
    assert target.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="requires Windows share-deny handles")
def test_launch_attestation_blocks_mutation_after_a_file_was_hashed(
    tmp_path: Path,
    monkeypatch,
):
    capability, root = _capability(tmp_path)
    first_relative = sorted(item[0] for item in capability._verified_files)[0]
    target = root.joinpath(*first_relative.split("/"))
    original = target.read_bytes()
    actual_hash = launch_attestation._win_hash
    attack_results: list[str] = []

    def hash_then_attack(handle: int, expected_size: int) -> str:
        digest = actual_hash(handle, expected_size)
        if not attack_results:
            try:
                target.write_bytes(b"mutated-after-hash")
            except OSError:
                attack_results.append("blocked")
            else:
                attack_results.append("succeeded")
        return digest

    monkeypatch.setattr(launch_attestation, "_win_hash", hash_then_attack)
    lease = launch_attestation.acquire_qwen_engine_launch_attestation(capability)
    try:
        lease.seal_for_launch()
        lease.assert_valid()
        assert attack_results == ["blocked"]
        assert target.read_bytes() == original
    finally:
        lease.release()


@pytest.mark.skipif(os.name != "nt", reason="requires ReadDirectoryChangesW")
def test_launch_attestation_detects_unlisted_file_at_seal_and_after_seal(tmp_path: Path):
    capability, root = _capability(tmp_path)
    lease = launch_attestation.acquire_qwen_engine_launch_attestation(capability)
    try:
        (root / "added-before-resume.txt").write_text("unlisted", encoding="utf-8")
        with pytest.raises(LocalVoiceRuntimeError) as before_resume:
            lease.seal_for_launch()
        assert before_resume.value.code in {
            "VOICE_ENGINE_LAUNCH_ATTESTATION_INVENTORY_CHANGED",
            "VOICE_ENGINE_LAUNCH_ATTESTATION_CHANGED",
        }
    finally:
        lease.release()

    capability_after_cleanup, second_root = _capability(tmp_path / "second")
    second = launch_attestation.acquire_qwen_engine_launch_attestation(capability_after_cleanup)
    try:
        second.seal_for_launch()
        (second_root / "added-after-resume.txt").write_text("unlisted", encoding="utf-8")
        observed: LocalVoiceRuntimeError | None = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                second.assert_valid()
            except LocalVoiceRuntimeError as exc:
                observed = exc
                break
            time.sleep(0.01)
        assert observed is not None
        assert observed.code == "VOICE_ENGINE_LAUNCH_ATTESTATION_CHANGED"
    finally:
        second.release()


@pytest.mark.skipif(os.name != "nt", reason="requires overlapped ReadDirectoryChangesW")
def test_launch_attestation_ready_waits_for_kernel_watch_queue(
    tmp_path: Path,
    monkeypatch,
):
    capability, _root = _capability(tmp_path)
    actual_queue = launch_attestation._win_queue_directory_watch
    queue_entered = threading.Event()
    allow_queue = threading.Event()
    completed = threading.Event()
    leases: list[launch_attestation.QwenEngineLaunchAttestationLease] = []
    errors: list[BaseException] = []

    def delayed_queue(handle, buffer, overlapped):  # type: ignore[no-untyped-def]
        queue_entered.set()
        assert allow_queue.wait(timeout=2.0)
        return actual_queue(handle, buffer, overlapped)

    def acquire() -> None:
        try:
            leases.append(launch_attestation.acquire_qwen_engine_launch_attestation(capability))
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    monkeypatch.setattr(launch_attestation, "_win_queue_directory_watch", delayed_queue)
    worker = threading.Thread(target=acquire)
    worker.start()
    try:
        assert queue_entered.wait(timeout=2.0)
        assert completed.wait(timeout=0.1) is False
    finally:
        allow_queue.set()
        worker.join(timeout=5.0)
        for lease in leases:
            lease.release()

    assert worker.is_alive() is False
    assert errors == []
    assert len(leases) == 1


def test_production_factory_uses_only_fixed_app_launch_plan_and_does_not_start(tmp_path: Path, monkeypatch):
    release = _trusted_test_engine_release(tmp_path)
    capability = release["capability"]
    root = release["package_root"]
    captured = {}

    class FakeProductionSupervisor(FakeSupervisor):
        def __init__(self, plan):
            super().__init__()
            self.plan = plan
            self.start_calls = 0

        def status(self):
            return {"runtime_id": self.plan.runtime_id, "state": "stopped", "owned": False}

        def shutdown(self):
            return {"stopped": False, "reason": "no_owned_process"}

    def supervisor_factory(plan, *, catalog, before_launch):
        assert catalog.owns(plan)
        captured["plan"] = plan
        captured["before_launch"] = before_launch
        return FakeProductionSupervisor(plan)

    monkeypatch.setattr(bridge, "register_qwen_base_clone_runtime", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(bridge, "_PRODUCTION_RUNTIMES", [])
    runtime = bridge.create_and_register_production_qwen_base_clone_runtime(
        capability=capability,
        cache_root=_cache_root(tmp_path / "runtime-cache"),
        supervisor_factory=supervisor_factory,
        _allow_test_trust=True,
    )
    plan = captured["plan"]
    assert plan.runtime_root == root.resolve()
    assert plan.host == "127.0.0.1"
    assert plan.port == 8892
    assert plan.executable == root.resolve() / "runtime" / "python" / "python.exe"
    assert plan.arguments == ("-I", "-B", "runtime/runner/qwen_base_clone_server.py")
    assert plan.required_file_sha256["runtime/runner/qwen_base_clone_server.py"] == hashlib.sha256(
        (root / "runtime" / "runner" / "qwen_base_clone_server.py").read_bytes()
    ).hexdigest()
    assert plan.environment["MENTORFLOW_VOICE_RUNTIME_ROOT"] == str(root.resolve())
    assert plan.environment["TRANSFORMERS_OFFLINE"] == "1"
    assert callable(captured["before_launch"])
    lease = captured["before_launch"]()
    lease.seal_for_launch()
    lease.assert_valid()
    lease.release()
    (root / "runtime" / "models" / "qwen3-tts-12hz-0.6b-base" / "config.json").write_text(
        '{"tampered":true}\n',
        encoding="utf-8",
    )
    with pytest.raises(LocalVoiceRuntimeError) as tampered_before_launch:
        captured["before_launch"]()
    assert tampered_before_launch.value.code == "VOICE_ENGINE_LAUNCH_ATTESTATION_MISMATCH"
    assert captured["capability"] is capability
    assert captured["runtime"] is runtime
    assert runtime.supervisor.start_calls == 0
    assert bridge.production_qwen_base_clone_runtime_status()["runtimes"][0]["state"] == "stopped"
    shutdown = bridge.shutdown_production_qwen_base_clone_runtimes()
    assert shutdown["owned_only"] is True
    assert shutdown["failures"] == []


def test_generation_rehash_prepare_synthesize_chain(
    tmp_path: Path, monkeypatch
):
    release = _trusted_test_engine_release(tmp_path)
    capability = release["capability"]
    root = release["package_root"]
    binding = replace(_binding(tmp_path / "voice"), engine=capability.dependency)
    backend = FakeBackend()
    transport = InMemoryTransport(
        child.QwenBaseCloneRuntimeState(backend=backend, cache_root=_cache_root(tmp_path / "c"))
    )
    attestation_calls: list[voice_pack.VerifiedQwenEngineRegistrationCapability] = []
    actual_acquire = bridge.acquire_qwen_engine_launch_attestation

    def tracked_acquire(candidate):
        attestation_calls.append(candidate)
        return actual_acquire(candidate)

    monkeypatch.setattr(bridge, "acquire_qwen_engine_launch_attestation", tracked_acquire)
    monkeypatch.setattr(bridge, "_PRODUCTION_RUNTIMES", [])
    monkeypatch.setattr(voice_pack, "_QWEN_BASE_CLONE_RUNTIMES", {})

    class Process:
        def __init__(self, pid: int):
            self.pid = pid
            self.return_code = None

        def poll(self):
            return self.return_code

    class Launcher:
        def __init__(self):
            self.calls = 0
            self.environments = []
            self.processes = []

        def launch(self, _plan, *, environment):
            self.calls += 1
            self.environments.append(dict(environment))
            process = Process(52_000 + self.calls)
            self.processes.append(process)
            return process

    launcher = Launcher()

    class Owner:
        def launch(self, runtime_launcher, plan, *, environment, pre_resume=None):
            if pre_resume is not None:
                pre_resume()
            return runtime_launcher.launch(plan, environment=environment)

        def owns_pid(self, process, pid):
            return process.poll() is None and process.pid == pid

        def terminate(self, process, *, timeout_seconds):
            process.return_code = 0
            return True

        def release(self, _process):
            return None

    class Listener:
        def verify(self, *, host, port, listener_pid):
            return host == "127.0.0.1" and port == 8892 and listener_pid > 0

    class Probe:
        def probe(self, plan, *, nonce, timeout_seconds):
            process = launcher.processes[-1]
            secret = launcher.environments[-1][INSTANCE_SECRET_ENV]
            return {
                "schema": "mentorflow.local-voice-runtime-health.v2",
                "runtime_id": plan.runtime_id,
                "service_id": plan.service_id,
                "pid": process.pid,
                "listener_pid": process.pid,
                "ready": True,
                "proof": _hmac_proof(
                    secret,
                    nonce=nonce,
                    service_id=plan.service_id,
                    runtime_id=plan.runtime_id,
                    pid=process.pid,
                    ready=True,
                ),
            }

    def supervisor_factory(plan, *, catalog, before_launch):
        supervisor = LocalVoiceRuntimeSupervisor(
            plan,
            catalog=catalog,
            launcher=launcher,
            identity_probe=Probe(),
            listener_verifier=Listener(),
            process_tree_owner=Owner(),
            port_in_use=lambda _host, _port: False,
            before_launch=before_launch,
            platform_name="nt",
        )
        supervisor.start_reaper = lambda: None  # type: ignore[method-assign]
        return supervisor

    runtime = bridge.create_and_register_production_qwen_base_clone_runtime(
        capability=capability,
        cache_root=_cache_root(tmp_path / "x"),
        supervisor_factory=supervisor_factory,
        _allow_test_trust=True,
    )
    runtime._transport = transport
    prepare_request = _prepare_request(binding)
    synthesis_request = voice_pack._qwen_synthesis_request(
        binding,
        text="新一代子进程先准备再合成。",
        style="relaxed",
        reply_kind="teaching.explain",
    )

    asyncio.run(runtime.prepare(prepare_request))
    first = asyncio.run(runtime.synthesize(synthesis_request))
    asyncio.run(runtime.prepare(prepare_request))
    repeated = asyncio.run(runtime.synthesize(synthesis_request))
    assert first.audio.startswith(b"RIFF") and repeated.audio.startswith(b"RIFF")
    assert len(attestation_calls) == 1
    assert launcher.calls == 1

    launcher.processes[-1].return_code = 0
    transport.state = child.QwenBaseCloneRuntimeState(
        backend=FakeBackend(),
        cache_root=_cache_root(tmp_path / "c"),
    )
    asyncio.run(runtime.prepare(prepare_request))
    after_natural_restart = asyncio.run(runtime.synthesize(synthesis_request))
    assert after_natural_restart.audio.startswith(b"RIFF")
    assert len(attestation_calls) == 2
    assert launcher.calls == 2

    launcher.processes[-1].return_code = 0
    assert runtime.supervisor.status()["owned"] is False
    transport.state = child.QwenBaseCloneRuntimeState(
        backend=FakeBackend(),
        cache_root=_cache_root(tmp_path / "c"),
    )
    calls_before_tamper = list(transport.calls)
    (root / "runtime" / "models" / "qwen3-tts-12hz-0.6b-base" / "config.json").write_text(
        '{"tampered":true}\n',
        encoding="utf-8",
    )
    with pytest.raises(LocalVoiceRuntimeError) as blocked:
        asyncio.run(runtime.prepare(prepare_request))
    assert blocked.value.code == "VOICE_RUNTIME_PRELAUNCH_VERIFY_FAILED"
    assert len(attestation_calls) == 3
    assert launcher.calls == 2
    assert transport.calls == calls_before_tamper
    shutdown = bridge.shutdown_production_qwen_base_clone_runtimes()
    assert shutdown["failures"] == []


def test_production_factory_fails_closed_when_signed_runner_material_is_missing(tmp_path: Path):
    release = _trusted_test_engine_release(tmp_path)
    capability = release["capability"]
    root = release["package_root"]
    (root / "runtime" / "runner" / "qwen_base_clone_server.py").unlink()
    with pytest.raises(LocalVoiceRuntimeError) as caught:
        bridge.create_and_register_production_qwen_base_clone_runtime(
            capability=capability,
            cache_root=_cache_root(tmp_path / "runtime-cache"),
            supervisor_factory=lambda *_args, **_kwargs: pytest.fail("supervisor must not be constructed"),
            _allow_test_trust=True,
        )
    assert caught.value.code == "VOICE_RUNTIME_ASSETS_MISSING"


def _reset_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "_BOOTSTRAP_RUNTIME", None)
    monkeypatch.setattr(
        bridge,
        "_BOOTSTRAP_STATUS",
        {
            "schema": "mentorflow.qwen-production-bootstrap-status.v1",
            "state": "not_attempted",
            "registered": False,
            "starts_model": False,
        },
    )


def test_production_bootstrap_missing_or_disabled_release_stays_fail_closed(tmp_path: Path, monkeypatch):
    _reset_bootstrap(monkeypatch)
    missing = bridge.bootstrap_production_qwen_base_clone_runtime(
        registration_path=tmp_path / "missing.json",
    )
    assert missing == {
        "schema": "mentorflow.qwen-production-bootstrap-status.v1",
        "state": "engine_release_not_configured",
        "registered": False,
        "starts_model": False,
    }

    descriptor = tmp_path / "registration.json"
    descriptor.write_text(
        json.dumps(
            {
                "schema": bridge.ENGINE_REGISTRATION_SCHEMA,
                "enabled": False,
                "package_id": "mentorflow-qwen3-tts-runtime",
                "version": "0.1.0",
                "leaf_public_key": "release-public-key.pem",
                "leaf_trust_receipt": "release-public-trust.json",
                "trust_policy": "release-trust-policy.json",
                "trust_policy_signature": "release-trust-policy.sig",
                "engine_catalog": "0.1.0-engine-catalog.json",
                "engine_archive": "0.1.0-win-x64.zip",
                "engine_package": "0.1.0",
            }
        ),
        encoding="utf-8",
    )
    disabled = bridge.bootstrap_production_qwen_base_clone_runtime(
        registration_path=descriptor,
    )
    assert disabled["state"] == "disabled"
    assert disabled["registered"] is False
    assert disabled["starts_model"] is False


def test_production_factory_rejects_a_real_test_chain_without_internal_test_gate(
    tmp_path: Path,
):
    release = _trusted_test_engine_release(tmp_path)
    with pytest.raises(LocalVoiceRuntimeError) as blocked:
        bridge.create_and_register_production_qwen_base_clone_runtime(
            capability=release["capability"],
            cache_root=_cache_root(tmp_path / "cache"),
            supervisor_factory=lambda *_args, **_kwargs: pytest.fail(
                "test trust must be rejected before supervisor construction"
            ),
        )
    assert blocked.value.code == "VOICE_ENGINE_TEST_TRUST_PRODUCTION_FORBIDDEN"


def test_legacy_inline_leaf_registration_descriptor_is_rejected(
    tmp_path: Path,
    monkeypatch,
):
    _reset_bootstrap(monkeypatch)
    descriptor = tmp_path / "legacy-registration.json"
    public_key = bytes(range(32))
    descriptor.write_text(
        json.dumps(
            {
                "schema": bridge.ENGINE_REGISTRATION_SCHEMA,
                "enabled": True,
                "package_id": "mentorflow-qwen3-tts-runtime",
                "version": "0.1.0",
                "signing_key_id": "release-key-v1",
                "trusted_public_key_base64": base64.b64encode(public_key).decode("ascii"),
                "trusted_public_key_sha256": hashlib.sha256(public_key).hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    status = bridge.bootstrap_production_qwen_base_clone_runtime(
        registration_path=descriptor,
    )
    assert status["state"] == "failed_closed"
    assert status["registered"] is False
    assert status["starts_model"] is False
    assert status["error"]["code"] == "VOICE_RUNTIME_PROTOCOL_INVALID"


def test_production_bootstrap_fails_when_fixed_registry_has_no_production_roots(
    tmp_path: Path,
    monkeypatch,
):
    release = _trusted_test_engine_release(tmp_path)
    _reset_bootstrap(monkeypatch)
    monkeypatch.setattr(
        bridge,
        "create_and_register_production_qwen_base_clone_runtime",
        lambda **_kwargs: pytest.fail("factory must not receive unanchored/test trust"),
    )
    status = bridge.bootstrap_production_qwen_base_clone_runtime(
        registration_path=release["descriptor_path"],
        _engine_root_for_tests=release["engine_root"],
    )
    assert status["state"] == "failed_closed"
    assert status["error"]["code"] == "TRUST_PRODUCTION_ROOTS_EMPTY"


def test_explicit_internal_test_chain_bootstrap_registers_without_starting_model(
    tmp_path: Path,
    monkeypatch,
):
    release = _trusted_test_engine_release(tmp_path)
    _reset_bootstrap(monkeypatch)
    captured: dict[str, object] = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(name="registered-test-runtime")

    monkeypatch.setattr(bridge, "create_and_register_production_qwen_base_clone_runtime", factory)
    status = bridge.bootstrap_production_qwen_base_clone_runtime(
        registration_path=release["descriptor_path"],
        _engine_root_for_tests=release["engine_root"],
        _allow_test_trust=True,
    )
    assert status["state"] == "registered"
    assert status["registered"] is True
    assert status["starts_model"] is False
    assert captured["_allow_test_trust"] is True
    capability = captured["capability"]
    assert capability.release_trust_binding.is_valid()
    receipt = status["release_trust"]
    assert receipt["test_only"] is True
    assert receipt["catalog_sha256"] == hashlib.sha256(
        Path(release["catalog_path"]).read_bytes()
    ).hexdigest()
    assert receipt["archive_sha256"] == hashlib.sha256(
        Path(release["archive_path"]).read_bytes()
    ).hexdigest()
    assert len(receipt["binding_sha256"]) == 64


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("revoked", "TRUST_POLICY_REVOKED"),
        ("purpose", "TRUST_POLICY_PURPOSE_FORBIDDEN"),
        ("schema", "TRUST_POLICY_SCHEMA_FORBIDDEN"),
        ("expired", "TRUST_POLICY_OUTSIDE_VALIDITY"),
    ],
)
def test_bootstrap_enforces_policy_scope_revocation_and_validity(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
    expected_code: str,
):
    release = _trusted_test_engine_release(tmp_path)
    policy = deepcopy(release["policy"])
    leaf = policy["allowed_leaf_keys"][0]
    if mutation == "revoked":
        policy["revoked_key_ids"] = [leaf["key_id"]]
    elif mutation == "purpose":
        leaf["purposes"] = ["mentorflow-voice-pack-release"]
    elif mutation == "schema":
        leaf["schemas"] = ["mentorflow.voice-pack.v1"]
    else:
        policy["created_at_utc"] = "2026-08-01T00:00:00Z"
        policy["validity"]["not_after"] = "2026-08-06T23:59:59Z"
    _rewrite_test_policy(release, policy)
    _reset_bootstrap(monkeypatch)
    monkeypatch.setattr(
        bridge,
        "create_and_register_production_qwen_base_clone_runtime",
        lambda **_kwargs: pytest.fail("invalid policy must fail before factory"),
    )
    status = bridge.bootstrap_production_qwen_base_clone_runtime(
        registration_path=release["descriptor_path"],
        _engine_root_for_tests=release["engine_root"],
        _allow_test_trust=True,
    )
    assert status["state"] == "failed_closed"
    assert status["error"]["code"] == expected_code


def test_bootstrap_rejects_candidate_fake_policy_root(tmp_path: Path, monkeypatch):
    release = _trusted_test_engine_release(tmp_path)
    signature_path = Path(release["policy_signature_path"])
    signature = json.loads(signature_path.read_text(encoding="utf-8"))
    signature["root_key_id"] = "test-candidate-fake-root-v1"
    signature_path.write_text(json.dumps(signature, sort_keys=True), encoding="utf-8")
    _reset_bootstrap(monkeypatch)
    status = bridge.bootstrap_production_qwen_base_clone_runtime(
        registration_path=release["descriptor_path"],
        _engine_root_for_tests=release["engine_root"],
        _allow_test_trust=True,
    )
    assert status["state"] == "failed_closed"
    assert status["error"]["code"] == "TRUST_ROOT_UNKNOWN"


@pytest.mark.parametrize("changed", ["policy", "registry"])
def test_new_generation_reverifies_policy_and_registry_bytes(
    tmp_path: Path,
    monkeypatch,
    changed: str,
):
    registry_path = None
    if changed == "registry":
        registry_path = tmp_path / "test-release-trust-roots.json"
        registry_path.write_bytes(release_trust_policy.RELEASE_TRUST_ROOTS_PATH.read_bytes())
    release = _trusted_test_engine_release(tmp_path / "release", registry_path=registry_path)
    captured: dict[str, object] = {}

    class Supervisor(FakeSupervisor):
        def __init__(self, plan):
            super().__init__()
            self.plan = plan

        def status(self):
            return {"runtime_id": self.plan.runtime_id, "state": "stopped", "owned": False}

        def shutdown(self):
            return {"stopped": False, "reason": "no_owned_process"}

    def supervisor_factory(plan, *, catalog, before_launch):
        assert catalog.owns(plan)
        captured["before_launch"] = before_launch
        return Supervisor(plan)

    monkeypatch.setattr(bridge, "register_qwen_base_clone_runtime", lambda **_kwargs: None)
    monkeypatch.setattr(bridge, "_PRODUCTION_RUNTIMES", [])
    bridge.create_and_register_production_qwen_base_clone_runtime(
        capability=release["capability"],
        cache_root=_cache_root(tmp_path / "cache"),
        supervisor_factory=supervisor_factory,
        _allow_test_trust=True,
    )
    if changed == "policy":
        policy_path = Path(release["policy_path"])
        policy_path.write_bytes(policy_path.read_bytes() + b"\n")
        expected = "TRUST_POLICY_SIGNATURE_INVALID"
    else:
        assert registry_path is not None
        # The checked-in registry is already serialized with the same
        # sorted/indented JSON shape, so reserializing it can be a no-op.
        # Change the signed bytes without changing the parsed document to
        # prove that byte-level binding is rechecked before every launch.
        registry_path.write_bytes(registry_path.read_bytes() + b"\n")
        expected = "TRUST_BINDING_CHANGED"
    with pytest.raises(LocalVoiceRuntimeError) as blocked:
        captured["before_launch"]()
    assert blocked.value.code == "VOICE_ENGINE_RELEASE_TRUST_REVERIFY_FAILED"
    assert blocked.value.details["trust_error"] == expected


def test_application_lifespan_records_and_logs_owned_runtime_shutdown_failures(
    monkeypatch, caplog
):
    from app import main as main_module

    monkeypatch.setattr(main_module.db, "init_db", lambda: None)
    monkeypatch.setattr(main_module, "init_orm", lambda: None)
    monkeypatch.setattr(main_module, "cleanup_expired_previews", lambda: None)
    monkeypatch.setattr(
        main_module,
        "bootstrap_production_qwen_base_clone_runtime",
        lambda: {"state": "disabled"},
    )
    failure = {"runtime_id": bridge.RUNTIME_ID, "error": "fixture-owned-stop-failed"}
    monkeypatch.setattr(
        main_module,
        "shutdown_production_qwen_base_clone_runtimes",
        lambda: {"stopped": [], "failures": [failure], "owned_only": True},
    )
    fake_app = SimpleNamespace(state=SimpleNamespace())

    async def exercise_lifespan():
        async with main_module.lifespan(fake_app):
            pass

    with caplog.at_level("ERROR", logger=main_module.__name__):
        asyncio.run(exercise_lifespan())
    assert fake_app.state.qwen_voice_shutdown["failures"] == [failure]
    assert fake_app.state.qwen_voice_shutdown_error == {
        "code": "VOICE_RUNTIME_SHUTDOWN_INCOMPLETE",
        "failures": [failure],
    }
    assert "owned-runtime failure" in caplog.text

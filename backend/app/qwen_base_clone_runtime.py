"""Trusted bridge from a verified Qwen engine pack to its loopback child.

This module deliberately has no discovery-by-command or manifest launch path.
Only an opaque capability returned by ``verify_qwen_base_clone_engine_release``
can reach the production factory.  Application-owned constants select the
portable Python executable, runner, loopback port and arguments.

The child receives verified reference WAV bytes during ``prepare``.  It never
accepts a voice-pack filesystem path.  Every request is body-bound to the
currently owned child with a fresh nonce/HMAC issued by the process supervisor.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import stat
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol

from .local_voice_runtime_supervisor import (
    LOOPBACK_HOST,
    LocalVoiceRuntimeError,
    LocalVoiceRuntimeSupervisor,
    RuntimeCatalogEntry,
    TrustedRuntimeCatalog,
    create_production_local_voice_runtime_supervisor,
)
from .qwen_engine_launch_attestation import acquire_qwen_engine_launch_attestation
from .release_trust_policy import (
    ReleaseTrustError,
    parse_strict_json,
    reverify_engine_release_binding,
    secure_read_release_file,
    verify_release_trust_chain,
)
from .teacher_voice_pack import (
    QWEN_BASE_CLONE_ENGINE_KIND,
    QWEN_BASE_CLONE_PREPARE_SCHEMA,
    QWEN_BASE_CLONE_SYNTHESIS_SCHEMA,
    QwenBaseClonePackBinding,
    QwenBaseClonePrepareReceipt,
    QwenBaseClonePrepareRequest,
    QwenBaseCloneReference,
    QwenBaseCloneRuntimeAudio,
    QwenBaseCloneSynthesisRequest,
    TeacherVoicePackError,
    VerifiedQwenEngineRegistrationCapability,
    register_qwen_base_clone_runtime,
    unregister_qwen_base_clone_runtime,
    verify_qwen_base_clone_engine_release,
)
from .windows_safe_relative import (
    WindowsSafeRelativePathError,
    validate_windows_safe_relative,
    windows_casefold_key,
)


SERVICE_ID = "mentorflow.qwen-base-clone-service.v1"
RUNTIME_ID = "mentorflow.qwen3-tts-0.6b-base-runtime.v1"
APP_RUNTIME_CATALOG_ID = "mentorflow.qwen3-tts-0.6b-base.win-x64.v1"
PORTABLE_PYTHON_RELATIVE = "runtime/python/python.exe"
RUNNER_RELATIVE = "runtime/runner/qwen_base_clone_server.py"
MODEL_RELATIVE = "runtime/models/qwen3-tts-12hz-0.6b-base"
DEFAULT_PORT = 8892
DEFAULT_DEVICE = "cuda:0"
DEFAULT_MINIMUM_NVIDIA_DRIVER = "555.00"
MAX_TEXT_CHARACTERS = 800
MAX_REPLY_KIND_CHARACTERS = 128
MAX_REFERENCE_AUDIO_BYTES = 12 * 1024 * 1024
MAX_PREPARE_REQUEST_BYTES = 36 * 1024 * 1024
MAX_SYNTHESIS_REQUEST_BYTES = 256 * 1024
MAX_JSON_RECEIPT_BYTES = 18 * 1024 * 1024
MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BASE64_BYTES = 4 * ((MAX_AUDIO_BYTES + 2) // 3)
_HEX64 = frozenset("0123456789abcdef")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_SAFE_TOKEN_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENGINE_REGISTRATION_SCHEMA = "mentorflow.qwen-engine-registration.v1"
DEFAULT_ENGINE_REGISTRATION_PATH = (
    PROJECT_ROOT / "backend" / "config" / "voice" / "qwen_engine_registration.v1.json"
)
DEFAULT_ENGINE_ROOT = PROJECT_ROOT / "data" / "voice" / "engines"
_ENGINE_REGISTRATION_FIELDS = {
    "schema",
    "enabled",
    "package_id",
    "version",
    "leaf_public_key",
    "leaf_trust_receipt",
    "trust_policy",
    "trust_policy_signature",
    "engine_catalog",
    "engine_archive",
    "engine_package",
}


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in _HEX64 for character in text)


def _strict_object(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_PROTOCOL_INVALID",
            f"{label} fields do not match the fixed protocol",
        )
    return dict(value)


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_REFERENCE_INVALID",
            "could not inspect a verified reference audio path",
            details={"error": f"{type(exc).__name__}:{exc}"},
        ) from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(metadata.st_mode)


def _read_verified_reference(reference: QwenBaseCloneReference) -> bytes:
    path = Path(reference.audio_path)
    if _is_reparse_point(path) or not path.is_file():
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_REFERENCE_INVALID",
            "verified reference audio is missing or uses a reparse point",
        )
    if not 44 <= int(reference.audio_bytes) <= MAX_REFERENCE_AUDIO_BYTES:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_REFERENCE_INVALID",
            "verified reference audio size is outside the runtime limit",
        )
    try:
        with path.open("rb") as handle:
            audio = handle.read(MAX_REFERENCE_AUDIO_BYTES + 1)
    except OSError as exc:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_REFERENCE_INVALID",
            "verified reference audio could not be read",
            details={"error": f"{type(exc).__name__}:{exc}"},
        ) from exc
    if (
        len(audio) != reference.audio_bytes
        or len(audio) > MAX_REFERENCE_AUDIO_BYTES
        or not hmac.compare_digest(_sha256_bytes(audio), reference.audio_sha256)
        or len(audio) < 44
        or audio[:4] != b"RIFF"
        or audio[8:12] != b"WAVE"
    ):
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_REFERENCE_MISMATCH",
            "reference bytes no longer match the verified voice-pack binding",
        )
    if hashlib.sha256(reference.transcript.encode("utf-8")).hexdigest() != reference.transcript_sha256:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_REFERENCE_MISMATCH",
            "reference transcript no longer matches the verified voice-pack binding",
        )
    return audio


def _engine_payload(binding: QwenBaseClonePackBinding) -> dict[str, Any]:
    return {
        "kind": binding.engine.kind,
        "catalog_id": binding.engine.catalog_id,
        "version": binding.engine.version,
        "manifest_sha256": binding.engine.manifest_sha256,
    }


def _reference_payload(
    reference: QwenBaseCloneReference,
    *,
    include_audio: bool,
) -> dict[str, Any]:
    payload = {
        "style": reference.style,
        "relative_path": reference.relative_path,
        "audio_bytes": reference.audio_bytes,
        "audio_sha256": reference.audio_sha256,
        "transcript": reference.transcript,
        "transcript_sha256": reference.transcript_sha256,
        "language": reference.language,
    }
    if include_audio:
        audio = _read_verified_reference(reference)
        encoded_length = 4 * ((len(audio) + 2) // 3)
        if encoded_length > MAX_AUDIO_BASE64_BYTES:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REFERENCE_INVALID",
                "reference audio exceeds the encoded transport limit",
            )
        payload["audio_base64"] = base64.b64encode(audio).decode("ascii")
    return payload


def _binding_payload(
    binding: QwenBaseClonePackBinding,
    *,
    include_audio: bool,
) -> dict[str, Any]:
    return {
        "pack_id": binding.pack_id,
        "version": binding.version,
        "manifest_sha256": binding.manifest_sha256,
        "engine": _engine_payload(binding),
        "references": [
            _reference_payload(reference, include_audio=include_audio)
            for reference in binding.references
        ],
        "references_sha256": binding.references_sha256,
        "binding_sha256": binding.binding_sha256,
    }


class LoopbackTransport(Protocol):
    def post_json(
        self,
        path: str,
        body: bytes,
        *,
        headers: Mapping[str, str],
        max_request_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class StrictLoopbackJsonTransport:
    """Bounded JSON transport with no environment proxy and no redirects."""

    def __init__(self, *, host: str, port: int) -> None:
        if host != LOOPBACK_HOST or not 1024 <= int(port) <= 65535:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_NON_LOOPBACK",
                "Qwen runtime transport must target 127.0.0.1 on an application port",
            )
        self._origin = f"http://{LOOPBACK_HOST}:{int(port)}"
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RejectRedirects(),
        )

    def post_json(
        self,
        path: str,
        body: bytes,
        *,
        headers: Mapping[str, str],
        max_request_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        if path not in {"/v1/prepare", "/v1/synthesize"}:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_INVALID",
                "Qwen runtime path is not application-authorized",
            )
        if not body or len(body) > max_request_bytes:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_TOO_LARGE",
                "Qwen runtime request body is empty or exceeds its endpoint limit",
            )
        url = self._origin + path
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "Connection": "close",
            **{str(key): str(value) for key, value in headers.items()},
        }
        request = urllib.request.Request(
            url,
            data=body,
            headers=request_headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=float(timeout_seconds)) as response:
                if response.geturl() != url:
                    raise LocalVoiceRuntimeError(
                        "VOICE_RUNTIME_REDIRECT_FORBIDDEN",
                        "Qwen runtime redirected a loopback request",
                    )
                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise LocalVoiceRuntimeError(
                        "VOICE_RUNTIME_PROTOCOL_INVALID",
                        "Qwen runtime response is not JSON",
                    )
                raw = response.read(max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_REJECTED",
                "Qwen runtime rejected the authenticated request",
                details={"status": int(exc.code)},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_UNAVAILABLE",
                "Qwen runtime loopback request failed",
                details={"error": f"{type(exc).__name__}:{exc}"},
            ) from exc
        if len(raw) > max_response_bytes:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_RESPONSE_TOO_LARGE",
                "Qwen runtime response exceeds the endpoint limit",
            )
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PROTOCOL_INVALID",
                "Qwen runtime response is not valid UTF-8 JSON",
            ) from exc
        if not isinstance(value, Mapping):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PROTOCOL_INVALID",
                "Qwen runtime response must be an object",
            )
        return value


_PREPARE_RECEIPT_FIELDS = {
    "schema",
    "ready",
    "engine_kind",
    "engine_catalog_id",
    "engine_version",
    "engine_manifest_sha256",
    "pack_id",
    "pack_version",
    "pack_manifest_sha256",
    "binding_sha256",
    "references_sha256",
}
_SYNTHESIS_RECEIPT_FIELDS = {
    "schema",
    "media_type",
    "engine_kind",
    "engine_catalog_id",
    "engine_version",
    "engine_manifest_sha256",
    "pack_id",
    "pack_version",
    "pack_manifest_sha256",
    "binding_sha256",
    "style",
    "reference_audio_sha256",
    "reference_transcript_sha256",
    "request_text_sha256",
    "reply_kind",
    "request_sha256",
    "audio_sha256",
    "audio_bytes",
    "profile",
    "cache",
}


class QwenBaseCloneLoopbackRuntime:
    """Typed app-side implementation of the fixed child protocol."""

    def __init__(
        self,
        *,
        supervisor: LocalVoiceRuntimeSupervisor,
        transport: LoopbackTransport | None = None,
        capability: VerifiedQwenEngineRegistrationCapability | None = None,
    ) -> None:
        if supervisor.plan.host != LOOPBACK_HOST:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_NON_LOOPBACK",
                "Qwen runtime supervisor is not bound to 127.0.0.1",
            )
        self.supervisor = supervisor
        self._capability = capability
        self._transport = transport or StrictLoopbackJsonTransport(
            host=supervisor.plan.host,
            port=supervisor.plan.port,
        )
        self._singleflight = asyncio.Lock()

    async def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        max_request_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        body = _canonical_json_bytes(payload)
        if len(body) > max_request_bytes:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_TOO_LARGE",
                "Qwen runtime request exceeds its endpoint limit",
            )
        async with self.supervisor.synthesis_lease_async():
            headers = self.supervisor.authorize_loopback_request(
                method="POST",
                path=path,
                content_length=len(body),
                body_sha256=_sha256_bytes(body),
            )
            return await asyncio.to_thread(
                self._transport.post_json,
                path,
                body,
                headers=headers,
                max_request_bytes=max_request_bytes,
                max_response_bytes=max_response_bytes,
                timeout_seconds=timeout_seconds,
            )

    async def prepare(self, request: QwenBaseClonePrepareRequest) -> QwenBaseClonePrepareReceipt:
        if (
            request.schema != QWEN_BASE_CLONE_PREPARE_SCHEMA
            or request.binding_sha256 != request.binding.binding_sha256
            or request.references_sha256 != request.binding.references_sha256
        ):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_INVALID",
                "prepare request does not match its typed pack binding",
            )
        payload = {
            "schema": request.schema,
            "binding": _binding_payload(request.binding, include_audio=True),
        }
        async with self._singleflight:
            response = await self._post(
                "/v1/prepare",
                payload,
                max_request_bytes=MAX_PREPARE_REQUEST_BYTES,
                max_response_bytes=256 * 1024,
                timeout_seconds=300.0,
            )
        receipt = _strict_object(response, _PREPARE_RECEIPT_FIELDS, "prepare receipt")
        return QwenBaseClonePrepareReceipt(
            schema=str(receipt["schema"]),
            ready=receipt["ready"] is True,
            engine_kind=str(receipt["engine_kind"]),
            engine_catalog_id=str(receipt["engine_catalog_id"]),
            engine_version=str(receipt["engine_version"]),
            engine_manifest_sha256=str(receipt["engine_manifest_sha256"]),
            pack_id=str(receipt["pack_id"]),
            pack_version=str(receipt["pack_version"]),
            pack_manifest_sha256=str(receipt["pack_manifest_sha256"]),
            binding_sha256=str(receipt["binding_sha256"]),
            references_sha256=str(receipt["references_sha256"]),
        )

    async def synthesize(self, request: QwenBaseCloneSynthesisRequest) -> QwenBaseCloneRuntimeAudio:
        text = str(request.text)
        reply_kind = str(request.reply_kind)
        if not text.strip() or len(text) > MAX_TEXT_CHARACTERS:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_TEXT_INVALID",
                "synthesis text is empty or exceeds the runtime limit",
            )
        if len(reply_kind) > MAX_REPLY_KIND_CHARACTERS:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_INVALID",
                "reply_kind exceeds the runtime limit",
            )
        payload = {
            "schema": request.schema,
            "binding": _binding_payload(request.binding, include_audio=False),
            "reference": _reference_payload(request.reference, include_audio=False),
            "binding_sha256": request.binding_sha256,
            "engine_kind": request.engine_kind,
            "engine_catalog_id": request.engine_catalog_id,
            "engine_version": request.engine_version,
            "engine_manifest_sha256": request.engine_manifest_sha256,
            "pack_id": request.pack_id,
            "pack_version": request.pack_version,
            "pack_manifest_sha256": request.pack_manifest_sha256,
            "text": text,
            "text_sha256": request.text_sha256,
            "style": request.style,
            "reference_audio_sha256": request.reference_audio_sha256,
            "reference_transcript_sha256": request.reference_transcript_sha256,
            "reply_kind": reply_kind,
            "request_sha256": request.request_sha256,
        }
        async with self._singleflight:
            response = await self._post(
                "/v1/synthesize",
                payload,
                max_request_bytes=MAX_SYNTHESIS_REQUEST_BYTES,
                max_response_bytes=MAX_JSON_RECEIPT_BYTES,
                timeout_seconds=300.0,
            )
        response_object = _strict_object(
            response,
            {"schema", "receipt", "audio_base64"},
            "synthesis response",
        )
        if response_object["schema"] != QWEN_BASE_CLONE_SYNTHESIS_SCHEMA:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PROTOCOL_INVALID",
                "synthesis response schema is invalid",
            )
        receipt = _strict_object(
            response_object["receipt"],
            _SYNTHESIS_RECEIPT_FIELDS,
            "synthesis receipt",
        )
        audio_bytes = receipt["audio_bytes"]
        encoded_audio = response_object["audio_base64"]
        if (
            isinstance(audio_bytes, bool)
            or not isinstance(audio_bytes, int)
            or not 44 <= audio_bytes <= MAX_AUDIO_BYTES
            or not isinstance(encoded_audio, str)
            or len(encoded_audio) > MAX_AUDIO_BASE64_BYTES
            or len(encoded_audio) != 4 * ((audio_bytes + 2) // 3)
        ):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PROTOCOL_INVALID",
                "synthesis audio exceeds or conflicts with its encoded transport limit",
            )
        try:
            audio = base64.b64decode(
                encoded_audio.encode("ascii"),
                validate=True,
            )
        except (UnicodeEncodeError, ValueError) as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PROTOCOL_INVALID",
                "synthesis audio is not valid canonical base64",
            ) from exc
        audio_sha256 = str(receipt["audio_sha256"])
        if (
            len(audio) != audio_bytes
            or not _is_sha256(audio_sha256)
            or not hmac.compare_digest(_sha256_bytes(audio), audio_sha256)
        ):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PROTOCOL_INVALID",
                "synthesis audio does not match its receipt",
            )
        return QwenBaseCloneRuntimeAudio(
            schema=str(receipt["schema"]),
            audio=audio,
            media_type=str(receipt["media_type"]),
            engine_kind=str(receipt["engine_kind"]),
            engine_catalog_id=str(receipt["engine_catalog_id"]),
            engine_version=str(receipt["engine_version"]),
            engine_manifest_sha256=str(receipt["engine_manifest_sha256"]),
            pack_id=str(receipt["pack_id"]),
            pack_version=str(receipt["pack_version"]),
            pack_manifest_sha256=str(receipt["pack_manifest_sha256"]),
            binding_sha256=str(receipt["binding_sha256"]),
            style=str(receipt["style"]),
            reference_audio_sha256=str(receipt["reference_audio_sha256"]),
            reference_transcript_sha256=str(receipt["reference_transcript_sha256"]),
            request_text_sha256=str(receipt["request_text_sha256"]),
            reply_kind=str(receipt["reply_kind"]),
            request_sha256=str(receipt["request_sha256"]),
            audio_sha256=audio_sha256,
            audio_bytes=audio_bytes,
            profile=str(receipt["profile"]),
            cache=str(receipt["cache"]),
        )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_engine_path(root: Path, relative: str, *, directory: bool = False) -> Path:
    posix = PurePosixPath(relative)
    path = root.joinpath(*posix.parts)
    if not path.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_ASSETS_MISSING",
            "required Qwen runtime asset escapes the verified engine root",
        )
    present = path.is_dir() if directory else path.is_file()
    if not present or _is_reparse_point(path):
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_ASSETS_MISSING",
            f"required Qwen runtime asset is missing: {relative}",
        )
    return path


_PRODUCTION_RUNTIME_LOCK = threading.RLock()
_PRODUCTION_RUNTIMES: list[QwenBaseCloneLoopbackRuntime] = []
_BOOTSTRAP_LOCK = threading.RLock()
_BOOTSTRAP_RUNTIME: QwenBaseCloneLoopbackRuntime | None = None
_BOOTSTRAP_STATUS: dict[str, Any] = {
    "schema": "mentorflow.qwen-production-bootstrap-status.v1",
    "state": "not_attempted",
    "registered": False,
    "starts_model": False,
}


def create_and_register_production_qwen_base_clone_runtime(
    *,
    capability: VerifiedQwenEngineRegistrationCapability,
    port: int = DEFAULT_PORT,
    device: str = DEFAULT_DEVICE,
    minimum_nvidia_driver: str = DEFAULT_MINIMUM_NVIDIA_DRIVER,
    cache_root: Path | None = None,
    supervisor_factory: Callable[..., LocalVoiceRuntimeSupervisor] = create_production_local_voice_runtime_supervisor,
    _allow_test_trust: bool = False,
) -> QwenBaseCloneLoopbackRuntime:
    """Register a lazy runtime from an already verified engine capability.

    This function does not start the child or load the model.  Registration
    fails before constructing a supervisor if the signed engine package does
    not contain the fixed application-owned executable/runner/model layout.
    """
    if (
        not isinstance(capability, VerifiedQwenEngineRegistrationCapability)
        or not capability.is_valid()
        or capability.dependency.kind != QWEN_BASE_CLONE_ENGINE_KIND
    ):
        raise LocalVoiceRuntimeError(
            "VOICE_ENGINE_CAPABILITY_REQUIRED",
            "a verified Qwen 0.6B Base engine capability is required",
        )
    release_binding = capability.release_trust_binding
    if release_binding is None or not release_binding.is_valid():
        raise LocalVoiceRuntimeError(
            "VOICE_ENGINE_RELEASE_TRUST_REQUIRED",
            "production registration requires a fixed-root release trust receipt",
        )
    if release_binding.trust.test_only and not _allow_test_trust:
        raise LocalVoiceRuntimeError(
            "VOICE_ENGINE_TEST_TRUST_PRODUCTION_FORBIDDEN",
            "production registration cannot use a test root, policy, or leaf",
        )
    if not release_binding.trust.test_only and not capability.is_production_trusted():
        raise LocalVoiceRuntimeError(
            "VOICE_ENGINE_RELEASE_TRUST_REQUIRED",
            "production registration capability trust is incomplete",
        )
    package_root = Path(capability.package_root).resolve(strict=True)
    executable = _required_engine_path(package_root, PORTABLE_PYTHON_RELATIVE)
    runner = _required_engine_path(package_root, RUNNER_RELATIVE)
    _required_engine_path(package_root, MODEL_RELATIVE, directory=True)
    selected_cache_root = Path(cache_root) if cache_root is not None else (
        package_root.parent / "_runtime-cache" / capability.dependency.catalog_id / capability.dependency.version
    )
    selected_cache_root = Path(os.path.abspath(selected_cache_root))
    if (
        selected_cache_root.parent == selected_cache_root
        or selected_cache_root.resolve(strict=False).is_relative_to(package_root)
    ):
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_CACHE_ROOT_INVALID",
            "runtime cache must remain outside the immutable signed engine package",
        )
    if str(device) not in {"cuda:0"}:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_DEVICE_INVALID",
            "the production Qwen runtime currently supports only cuda:0",
        )
    environment = {
        "MENTORFLOW_VOICE_RUNTIME_ROOT": str(package_root),
        "MENTORFLOW_VOICE_HOST": LOOPBACK_HOST,
        "MENTORFLOW_VOICE_PORT": str(int(port)),
        "MENTORFLOW_VOICE_DEVICE": str(device),
        "MENTORFLOW_VOICE_MIN_NVIDIA_DRIVER": str(minimum_nvidia_driver),
        "MENTORFLOW_VOICE_CACHE_ROOT": str(selected_cache_root),
        "HF_HOME": str(selected_cache_root / "huggingface"),
        "HF_HUB_CACHE": str(selected_cache_root / "huggingface" / "hub"),
        "TORCH_HOME": str(selected_cache_root / "torch"),
        "XDG_CACHE_HOME": str(selected_cache_root),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    catalog = TrustedRuntimeCatalog(
        [
            RuntimeCatalogEntry(
                catalog_id=APP_RUNTIME_CATALOG_ID,
                runtime_id=RUNTIME_ID,
                service_id=SERVICE_ID,
                executable_relative=PORTABLE_PYTHON_RELATIVE,
                arguments=("-I", "-B", RUNNER_RELATIVE),
                working_directory_relative=".",
                environment=environment,
                executable_sha256=_hash_file(executable),
                required_file_sha256={RUNNER_RELATIVE: _hash_file(runner)},
            )
        ]
    )
    plan = catalog.issue_plan(
        APP_RUNTIME_CATALOG_ID,
        runtime_root=package_root,
        port=int(port),
        startup_timeout_seconds=180.0,
        probe_timeout_seconds=1.5,
        idle_ttl_seconds=300.0,
        reaper_interval_seconds=5.0,
    )
    def verify_generation_and_acquire_attestation():
        try:
            reverify_engine_release_binding(release_binding)
        except ReleaseTrustError as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_ENGINE_RELEASE_TRUST_REVERIFY_FAILED",
                "release root, policy, revocation, validity, leaf, catalog or archive revalidation failed",
                details={"trust_error": exc.code},
            ) from exc
        return acquire_qwen_engine_launch_attestation(capability)

    supervisor = supervisor_factory(
        plan,
        catalog=catalog,
        before_launch=verify_generation_and_acquire_attestation,
    )
    runtime = QwenBaseCloneLoopbackRuntime(
        supervisor=supervisor,
        capability=capability,
    )
    register_qwen_base_clone_runtime(capability=capability, runtime=runtime)
    with _PRODUCTION_RUNTIME_LOCK:
        _PRODUCTION_RUNTIMES.append(runtime)
    return runtime


def _safe_bootstrap_token(value: Any, field: str) -> str:
    token = str(value or "")
    if (
        not 1 <= len(token) <= 80
        or token[0] not in "abcdefghijklmnopqrstuvwxyz0123456789"
        or any(character not in _SAFE_TOKEN_CHARACTERS for character in token)
    ):
        raise LocalVoiceRuntimeError(
            "VOICE_ENGINE_REGISTRATION_CONFIG_INVALID",
            f"{field} must be a stable lowercase token",
        )
    return token


def _registration_relative_path(release_parent: Path, value: Any, field: str) -> Path:
    try:
        relative = validate_windows_safe_relative(value)
    except WindowsSafeRelativePathError as exc:
        raise LocalVoiceRuntimeError(
            "VOICE_ENGINE_REGISTRATION_CONFIG_INVALID",
            f"{field} must be an exact Windows-safe relative POSIX path",
        ) from exc
    return release_parent.joinpath(*relative.parts)


def bootstrap_production_qwen_base_clone_runtime(
    *,
    registration_path: Path | None = None,
    _engine_root_for_tests: Path | None = None,
    _allow_test_trust: bool = False,
    _registry_path_for_tests: Path | None = None,
    _now_for_tests: datetime | None = None,
) -> dict[str, Any]:
    """Verify and register an installed release without starting its child.

    The descriptor may select only Windows-safe release artifacts below the
    application-owned engine root. It cannot carry a root/public-key value,
    URL, executable, command, arguments, host, port, or engine-root override.
    Environment configuration can select the descriptor path only.

    Underscored test parameters are in-process fixture boundaries. Main app
    startup never supplies them; production therefore cannot load test roots,
    a candidate registry, or a candidate engine root.
    """
    global _BOOTSTRAP_RUNTIME, _BOOTSTRAP_STATUS
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAP_RUNTIME is not None:
            return dict(_BOOTSTRAP_STATUS)
        configured_path = registration_path
        if configured_path is None:
            configured = str(os.getenv("MENTORFLOW_QWEN_ENGINE_REGISTRATION") or "").strip()
            configured_path = Path(configured) if configured else DEFAULT_ENGINE_REGISTRATION_PATH
        configured_path = Path(os.path.abspath(configured_path))
        if not configured_path.is_file():
            _BOOTSTRAP_STATUS = {
                "schema": "mentorflow.qwen-production-bootstrap-status.v1",
                "state": "engine_release_not_configured",
                "registered": False,
                "starts_model": False,
            }
            return dict(_BOOTSTRAP_STATUS)
        try:
            if _allow_test_trust and _engine_root_for_tests is None:
                raise LocalVoiceRuntimeError(
                    "VOICE_ENGINE_TEST_TRUST_PRODUCTION_FORBIDDEN",
                    "test trust requires an explicit in-process test engine root",
                )
            if _registry_path_for_tests is not None and not _allow_test_trust:
                raise LocalVoiceRuntimeError(
                    "VOICE_ENGINE_TRUST_ROOT_OVERRIDE_FORBIDDEN",
                    "a test registry cannot be supplied to production bootstrap",
                )
            raw = secure_read_release_file(
                configured_path,
                label="engine registration descriptor",
                maximum_bytes=64 * 1024,
            )
            value = parse_strict_json(
                raw,
                code="VOICE_ENGINE_REGISTRATION_CONFIG_INVALID",
                label="engine registration descriptor",
            )
            descriptor = _strict_object(
                value,
                _ENGINE_REGISTRATION_FIELDS,
                "engine registration descriptor",
            )
            if descriptor["schema"] != ENGINE_REGISTRATION_SCHEMA or not isinstance(descriptor["enabled"], bool):
                raise LocalVoiceRuntimeError(
                    "VOICE_ENGINE_REGISTRATION_CONFIG_INVALID",
                    "engine registration descriptor schema or enabled flag is invalid",
                )
            if descriptor["enabled"] is not True:
                _BOOTSTRAP_STATUS = {
                    "schema": "mentorflow.qwen-production-bootstrap-status.v1",
                    "state": "disabled",
                    "registered": False,
                    "starts_model": False,
                }
                return dict(_BOOTSTRAP_STATUS)
            package_id = _safe_bootstrap_token(descriptor["package_id"], "package_id")
            version = _safe_bootstrap_token(descriptor["version"], "version")
            selected_root = _engine_root_for_tests if _engine_root_for_tests is not None else DEFAULT_ENGINE_ROOT
            selected_root = Path(os.path.abspath(selected_root))
            if selected_root.parent == selected_root:
                raise LocalVoiceRuntimeError(
                    "VOICE_ENGINE_REGISTRATION_CONFIG_INVALID",
                    "engine root cannot be a filesystem root",
                )
            release_parent = selected_root / package_id
            relative_values = {
                field: validate_windows_safe_relative(descriptor[field])
                for field in (
                    "leaf_public_key",
                    "leaf_trust_receipt",
                    "trust_policy",
                    "trust_policy_signature",
                    "engine_catalog",
                    "engine_archive",
                    "engine_package",
                )
            }
            folded = [windows_casefold_key(value) for value in relative_values.values()]
            if len(set(folded)) != len(folded):
                raise LocalVoiceRuntimeError(
                    "VOICE_ENGINE_REGISTRATION_CONFIG_INVALID",
                    "engine registration artifact paths collide under Windows case folding",
                )
            public_path = _registration_relative_path(release_parent, descriptor["leaf_public_key"], "leaf_public_key")
            receipt_path = _registration_relative_path(release_parent, descriptor["leaf_trust_receipt"], "leaf_trust_receipt")
            policy_path = _registration_relative_path(release_parent, descriptor["trust_policy"], "trust_policy")
            policy_signature_path = _registration_relative_path(
                release_parent,
                descriptor["trust_policy_signature"],
                "trust_policy_signature",
            )
            catalog_path = _registration_relative_path(release_parent, descriptor["engine_catalog"], "engine_catalog")
            archive_path = _registration_relative_path(release_parent, descriptor["engine_archive"], "engine_archive")
            package_root = _registration_relative_path(release_parent, descriptor["engine_package"], "engine_package")
            raw_catalog = secure_read_release_file(
                catalog_path,
                label="installed Qwen engine catalog",
                maximum_bytes=1024 * 1024,
            )
            catalog = parse_strict_json(
                raw_catalog,
                code="VOICE_ENGINE_CATALOG_INVALID",
                label="installed Qwen engine catalog",
            )
            manifest_path = package_root / "engine-manifest.json"
            raw_manifest = secure_read_release_file(
                manifest_path,
                label="installed Qwen engine manifest",
                maximum_bytes=1024 * 1024,
            )
            manifest_value = parse_strict_json(
                raw_manifest,
                code="VOICE_ENGINE_MANIFEST_INVALID",
                label="installed Qwen engine manifest",
            )
            release_trust = verify_release_trust_chain(
                manifest_name="engine-manifest.json",
                manifest_value=manifest_value,
                public_key_path=public_path,
                trust_receipt_path=receipt_path,
                trust_policy_path=policy_path,
                trust_policy_signature_path=policy_signature_path,
                _allow_test_roots=_allow_test_trust,
                _registry_path_for_tests=_registry_path_for_tests,
                _now_for_tests=_now_for_tests,
            )
            capability = verify_qwen_base_clone_engine_release(
                catalog=catalog,
                package_id=package_id,
                version=version,
                archive_path=archive_path,
                package_root=package_root,
                release_trust=release_trust,
                catalog_sha256=_sha256_bytes(raw_catalog),
                catalog_bytes=len(raw_catalog),
                catalog_path=catalog_path,
            )
            runtime = create_and_register_production_qwen_base_clone_runtime(
                capability=capability,
                _allow_test_trust=_allow_test_trust,
            )
        except (
            LocalVoiceRuntimeError,
            TeacherVoicePackError,
            ReleaseTrustError,
            WindowsSafeRelativePathError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            code = getattr(exc, "code", "VOICE_ENGINE_REGISTRATION_FAILED")
            _BOOTSTRAP_STATUS = {
                "schema": "mentorflow.qwen-production-bootstrap-status.v1",
                "state": "failed_closed",
                "registered": False,
                "starts_model": False,
                "error": {"code": str(code), "type": type(exc).__name__},
            }
            return dict(_BOOTSTRAP_STATUS)
        _BOOTSTRAP_RUNTIME = runtime
        _BOOTSTRAP_STATUS = {
            "schema": "mentorflow.qwen-production-bootstrap-status.v1",
            "state": "registered",
            "registered": True,
            "starts_model": False,
            "engine": {
                "kind": capability.dependency.kind,
                "catalog_id": capability.dependency.catalog_id,
                "version": capability.dependency.version,
                "manifest_sha256": capability.dependency.manifest_sha256,
            },
            "release_trust": capability.release_trust_binding.receipt,
        }
        return dict(_BOOTSTRAP_STATUS)


def production_qwen_base_clone_bootstrap_status() -> dict[str, Any]:
    with _BOOTSTRAP_LOCK:
        return dict(_BOOTSTRAP_STATUS)


def production_qwen_base_clone_runtime_status() -> dict[str, Any]:
    """Read only: never probes, starts or loads a model."""
    with _PRODUCTION_RUNTIME_LOCK:
        runtimes = tuple(_PRODUCTION_RUNTIMES)
    return {
        "schema": "mentorflow.qwen-production-runtime-registry.v1",
        "registered": len(runtimes),
        "runtimes": [runtime.supervisor.status() for runtime in runtimes],
        "starts_model": False,
    }


def shutdown_production_qwen_base_clone_runtimes() -> dict[str, Any]:
    """Stop only child process trees retained by our production supervisors."""
    global _BOOTSTRAP_RUNTIME
    with _PRODUCTION_RUNTIME_LOCK:
        runtimes = tuple(reversed(_PRODUCTION_RUNTIMES))
    stopped: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    completed: list[QwenBaseCloneLoopbackRuntime] = []
    for runtime in runtimes:
        try:
            stopped.append(runtime.supervisor.shutdown())
            if runtime._capability is not None:
                unregister_qwen_base_clone_runtime(
                    capability=runtime._capability,
                    runtime=runtime,
                )
            completed.append(runtime)
        except BaseException as exc:
            failures.append(
                {
                    "runtime_id": runtime.supervisor.plan.runtime_id,
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
    if completed:
        with _PRODUCTION_RUNTIME_LOCK:
            for runtime in completed:
                if runtime in _PRODUCTION_RUNTIMES:
                    _PRODUCTION_RUNTIMES.remove(runtime)
        with _BOOTSTRAP_LOCK:
            if _BOOTSTRAP_RUNTIME in completed:
                _BOOTSTRAP_RUNTIME = None
    return {
        "schema": "mentorflow.qwen-production-runtime-shutdown.v1",
        "stopped": stopped,
        "failures": failures,
        "owned_only": True,
    }

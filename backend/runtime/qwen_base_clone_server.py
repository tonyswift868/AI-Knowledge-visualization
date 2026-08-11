"""Pack-relative Qwen3-TTS 0.6B Base loopback service.

The production release copies this file to ``runtime/runner``.  It uses only
the standard library until a verified prepare request first needs the model.
No endpoint accepts a filesystem path, URL, command, model selector or port.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import hmac
import importlib.abc
import importlib.util
import io
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import wave
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PurePosixPath
from socketserver import ThreadingMixIn
from typing import Any, Callable, Mapping, Protocol


HEALTH_SCHEMA = "mentorflow.local-voice-runtime-health.v2"
PREPARE_SCHEMA = "mentorflow.qwen-base-clone-prepare.v1"
SYNTHESIS_SCHEMA = "mentorflow.qwen-base-clone-synthesis.v1"
SERVICE_ID = "mentorflow.qwen-base-clone-service.v1"
RUNTIME_ID = "mentorflow.qwen3-tts-0.6b-base-runtime.v1"
ENGINE_KIND = "qwen3-tts-0.6b-base"
INSTANCE_SECRET_ENV = "MENTORFLOW_LOCAL_VOICE_INSTANCE_SECRET"
HEALTH_NONCE_HEADER = "X-MentorFlow-Runtime-Nonce"
REQUEST_NONCE_HEADER = "X-MentorFlow-Request-Nonce"
REQUEST_HEADER_PROOF_HEADER = "X-MentorFlow-Request-Header-Proof"
REQUEST_PROOF_HEADER = "X-MentorFlow-Request-Proof"
LOOPBACK_HOST = "127.0.0.1"
MODEL_RELATIVE = "runtime/models/qwen3-tts-12hz-0.6b-base"
DEPENDENCY_LOCK_RELATIVE = "metadata/dependency-lock.json"
SOURCE_OVERLAY_RELATIVE = "metadata/source-overlays/qwen-12hz-only.json"
QWEN_SITE_PACKAGES_RELATIVE = "runtime/python/Lib/site-packages"
QWEN_WHEEL_SHA256 = "11a290d8dabc7ef91a90c54478c8ab19b3edb1d85c0882313721892bdc4af15d"
QWEN_12HZ_MODEL_TYPE = "qwen3_tts_tokenizer_12hz"
QWEN_MODEL_CONFIG_SHA256 = "2e714c787c8edb98b05432685cddb634add2de4d4e645f653d68251ef72ba011"
QWEN_SPEECH_CONFIG_SHA256 = "ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167"
QWEN_OVERLAY_SCHEMA = "mentorflow.qwen-12hz-source-overlay.v1"
NUMBA_THREADING_LAYER_ENV = "NUMBA_THREADING_LAYER"
NUMBA_THREADING_LAYER_REQUIRED = "workqueue"
NUMBA_TBBPOOL_MODULE = "numba.np.ufunc.tbbpool"
QWEN_OVERLAY_INPUTS = {
    "qwen_tts/core/__init__.py": "1b380d9de843b6d585d938c339d066136567ca7125412674234204af4386679e",
    "qwen_tts/inference/qwen3_tts_tokenizer.py": "ac2d855022a1bd21d33ab7b267ec952eef71f81d3f8d969a306139ff1a929515",
}
MAX_TEXT_CHARACTERS = 800
MAX_REPLY_KIND_CHARACTERS = 128
MAX_REFERENCE_AUDIO_BYTES = 12 * 1024 * 1024
MAX_PREPARE_BODY_BYTES = 36 * 1024 * 1024
MAX_SYNTHESIS_BODY_BYTES = 256 * 1024
MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BASE64_BYTES = 4 * ((MAX_AUDIO_BYTES + 2) // 3)
MAX_AUDIO_CACHE_ENTRIES = 64
MAX_AUDIO_CACHE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_WORKERS = 8
DEFAULT_REQUEST_QUEUE_SIZE = 16
DEFAULT_HEADER_TIMEOUT_SECONDS = 5.0
DEFAULT_BODY_TIMEOUT_SECONDS = 10.0
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_DRIVER_VERSION = re.compile(r"^\d{3,4}\.\d{1,3}(?:\.\d{1,3})?$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,512}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_ENGINE_FIELDS = {"kind", "catalog_id", "version", "manifest_sha256"}
_BINDING_FIELDS = {
    "pack_id",
    "version",
    "manifest_sha256",
    "engine",
    "references",
    "references_sha256",
    "binding_sha256",
}
_REFERENCE_FIELDS = {
    "style",
    "relative_path",
    "audio_bytes",
    "audio_sha256",
    "transcript",
    "transcript_sha256",
    "language",
}
_SYNTHESIS_FIELDS = {
    "schema",
    "binding",
    "reference",
    "binding_sha256",
    "engine_kind",
    "engine_catalog_id",
    "engine_version",
    "engine_manifest_sha256",
    "pack_id",
    "pack_version",
    "pack_manifest_sha256",
    "text",
    "text_sha256",
    "style",
    "reference_audio_sha256",
    "reference_transcript_sha256",
    "reply_kind",
    "request_sha256",
}


class ProtocolError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = int(status)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _enforce_numba_workqueue() -> dict[str, Any]:
    """Fail closed unless the portable runtime is locked to Numba workqueue.

    Numba ships an optional ``tbbpool`` extension whose PE import references
    ``tbb12.dll``.  The strict 12Hz pack intentionally does not ship that DLL;
    setting the threading layer before any product import makes workqueue the
    deterministic non-TBB backend.  Re-checking after imports prevents a host
    process from preloading the TBB extension around the gate.
    """

    configured = os.environ.get(NUMBA_THREADING_LAYER_ENV)
    if configured is not None and configured.casefold() != NUMBA_THREADING_LAYER_REQUIRED:
        raise RuntimeError(
            f"{NUMBA_THREADING_LAYER_ENV} must be {NUMBA_THREADING_LAYER_REQUIRED}, got {configured!r}"
        )
    os.environ[NUMBA_THREADING_LAYER_ENV] = NUMBA_THREADING_LAYER_REQUIRED
    tbbpool_loaded = NUMBA_TBBPOOL_MODULE in sys.modules or any(
        name.startswith(NUMBA_TBBPOOL_MODULE + ".") for name in sys.modules
    )
    if tbbpool_loaded:
        raise RuntimeError("Numba TBB pool was loaded in the strict 12Hz runtime")
    active = None
    numba_module = sys.modules.get("numba")
    if numba_module is not None:
        config = getattr(numba_module, "config", None)
        active = getattr(config, "THREADING_LAYER", None)
        if active not in {None, NUMBA_THREADING_LAYER_REQUIRED}:
            raise RuntimeError(
                f"loaded Numba threading layer is not {NUMBA_THREADING_LAYER_REQUIRED}: {active!r}"
            )
    return {
        "configured": NUMBA_THREADING_LAYER_REQUIRED,
        "active_config": active,
        "tbbpool_loaded": False,
        "tbb": "optional-not-shipped",
    }


def _strict_object(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ProtocolError("PROTOCOL_FIELDS_INVALID", f"{field} fields are invalid")
    return dict(value)


def _safe_id(value: Any, field: str) -> str:
    text = str(value or "")
    if not _SAFE_ID.fullmatch(text):
        raise ProtocolError("PROTOCOL_ID_INVALID", f"{field} is invalid")
    return text


def _sha256(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HEX64.fullmatch(text):
        raise ProtocolError("PROTOCOL_DIGEST_INVALID", f"{field} is invalid")
    return text


def _safe_relative_metadata(value: Any) -> str:
    text = str(value or "")
    if (
        not text
        or len(text) > 512
        or chr(92) in text
        or ":" in text
        or text.startswith("/")
        or any(ord(character) < 32 for character in text)
    ):
        raise ProtocolError("PROTOCOL_PATH_INVALID", "reference relative_path is invalid")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ProtocolError("PROTOCOL_PATH_INVALID", "reference relative_path is invalid")
    return path.as_posix()


def _is_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(metadata.st_mode)


def _validate_wav(audio: bytes, field: str) -> None:
    if not 44 <= len(audio) <= MAX_AUDIO_BYTES or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise ProtocolError("AUDIO_INVALID", f"{field} is not a bounded WAV")
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            if (
                reader.getnchannels() not in {1, 2}
                or reader.getsampwidth() not in {1, 2, 3, 4}
                or not 8_000 <= reader.getframerate() <= 96_000
                or reader.getnframes() <= 0
                or reader.getcomptype() != "NONE"
            ):
                raise ProtocolError("AUDIO_INVALID", f"{field} PCM parameters are invalid")
    except (wave.Error, EOFError) as exc:
        raise ProtocolError("AUDIO_INVALID", f"{field} WAV structure is invalid") from exc


def _reference_identity(reference: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "style": reference["style"],
        "relative_path": reference["relative_path"],
        "audio_bytes": reference["audio_bytes"],
        "audio_sha256": reference["audio_sha256"],
        "transcript_sha256": reference["transcript_sha256"],
        "language": reference["language"],
    }


def _validate_reference(
    value: Any,
    *,
    include_audio: bool,
) -> tuple[dict[str, Any], bytes | None]:
    expected = set(_REFERENCE_FIELDS)
    if include_audio:
        expected.add("audio_base64")
    reference = _strict_object(value, expected, "reference")
    reference["style"] = str(reference["style"] or "")
    if reference["style"] not in {"lively", "relaxed"}:
        raise ProtocolError("STYLE_INVALID", "reference style is invalid")
    reference["relative_path"] = _safe_relative_metadata(reference["relative_path"])
    size = reference["audio_bytes"]
    if not isinstance(size, int) or not 44 <= size <= MAX_REFERENCE_AUDIO_BYTES:
        raise ProtocolError("REFERENCE_INVALID", "reference audio_bytes is invalid")
    reference["audio_sha256"] = _sha256(reference["audio_sha256"], "reference.audio_sha256")
    transcript = str(reference["transcript"] or "").strip()
    if not transcript or len(transcript) > 4000:
        raise ProtocolError("REFERENCE_INVALID", "reference transcript is invalid")
    reference["transcript"] = transcript
    reference["transcript_sha256"] = _sha256(
        reference["transcript_sha256"],
        "reference.transcript_sha256",
    )
    if not hmac.compare_digest(_text_sha256(transcript), reference["transcript_sha256"]):
        raise ProtocolError("REFERENCE_MISMATCH", "reference transcript digest does not match")
    if reference["language"] != "zh":
        raise ProtocolError("REFERENCE_INVALID", "reference language must be zh")
    audio: bytes | None = None
    if include_audio:
        encoded_audio = reference.pop("audio_base64")
        expected_encoded_bytes = 4 * ((size + 2) // 3)
        if (
            not isinstance(encoded_audio, str)
            or len(encoded_audio) != expected_encoded_bytes
            or len(encoded_audio) > MAX_AUDIO_BASE64_BYTES
        ):
            raise ProtocolError("REFERENCE_INVALID", "reference audio_base64 exceeds its exact limit")
        try:
            audio = base64.b64decode(encoded_audio.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError) as exc:
            raise ProtocolError("REFERENCE_INVALID", "reference audio_base64 is invalid") from exc
        if len(audio) != size or not hmac.compare_digest(hashlib.sha256(audio).hexdigest(), reference["audio_sha256"]):
            raise ProtocolError("REFERENCE_MISMATCH", "reference audio digest does not match")
        _validate_wav(audio, "reference audio")
    return reference, audio


def _validate_binding(
    value: Any,
    *,
    include_audio: bool,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    binding = _strict_object(value, _BINDING_FIELDS, "binding")
    binding["pack_id"] = _safe_id(binding["pack_id"], "binding.pack_id")
    binding["version"] = _safe_id(binding["version"], "binding.version")
    binding["manifest_sha256"] = _sha256(binding["manifest_sha256"], "binding.manifest_sha256")
    engine = _strict_object(binding["engine"], _ENGINE_FIELDS, "binding.engine")
    if engine["kind"] != ENGINE_KIND:
        raise ProtocolError("ENGINE_INVALID", "binding engine kind is invalid")
    engine["catalog_id"] = _safe_id(engine["catalog_id"], "binding.engine.catalog_id")
    engine["version"] = _safe_id(engine["version"], "binding.engine.version")
    engine["manifest_sha256"] = _sha256(engine["manifest_sha256"], "binding.engine.manifest_sha256")
    binding["engine"] = engine
    raw_references = binding["references"]
    if not isinstance(raw_references, list) or len(raw_references) != 2:
        raise ProtocolError("REFERENCE_INVALID", "binding must contain exactly two references")
    references: list[dict[str, Any]] = []
    audio_by_style: dict[str, bytes] = {}
    for raw_reference in raw_references:
        reference, audio = _validate_reference(raw_reference, include_audio=include_audio)
        references.append(reference)
        if audio is not None:
            audio_by_style[reference["style"]] = audio
    if [reference["style"] for reference in references] != ["lively", "relaxed"]:
        raise ProtocolError("REFERENCE_INVALID", "references must be ordered lively then relaxed")
    if references[0]["audio_sha256"] == references[1]["audio_sha256"]:
        raise ProtocolError("REFERENCE_INVALID", "style references must be distinct")
    binding["references"] = references
    expected_references_sha256 = _canonical_sha256(
        [_reference_identity(reference) for reference in references]
    )
    binding["references_sha256"] = _sha256(binding["references_sha256"], "binding.references_sha256")
    if not hmac.compare_digest(binding["references_sha256"], expected_references_sha256):
        raise ProtocolError("BINDING_MISMATCH", "references_sha256 does not match")
    expected_binding_sha256 = _canonical_sha256(
        {
            "engine": engine,
            "pack": {
                "pack_id": binding["pack_id"],
                "version": binding["version"],
                "manifest_sha256": binding["manifest_sha256"],
            },
            "references_sha256": expected_references_sha256,
        }
    )
    binding["binding_sha256"] = _sha256(binding["binding_sha256"], "binding.binding_sha256")
    if not hmac.compare_digest(binding["binding_sha256"], expected_binding_sha256):
        raise ProtocolError("BINDING_MISMATCH", "binding_sha256 does not match")
    return binding, audio_by_style


def _health_proof(instance_key: str, nonce: str, *, pid: int, ready: bool) -> str:
    message = "\n".join(
        (nonce, SERVICE_ID, RUNTIME_ID, str(int(pid)), "1" if ready else "0")
    ).encode("utf-8")
    return hmac.new(instance_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _request_proof(
    instance_key: str,
    *,
    nonce: str,
    method: str,
    path: str,
    body_sha256: str,
    pid: int,
) -> str:
    message = "\n".join(
        (
            "mentorflow.local-voice-runtime-request.v1",
            nonce,
            method.upper(),
            path,
            body_sha256,
            SERVICE_ID,
            RUNTIME_ID,
            str(int(pid)),
        )
    ).encode("utf-8")
    return hmac.new(instance_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _request_header_proof(
    instance_key: str,
    *,
    nonce: str,
    method: str,
    path: str,
    content_length: int,
    pid: int,
) -> str:
    message = "\n".join(
        (
            "mentorflow.local-voice-runtime-request-header.v1",
            nonce,
            method.upper(),
            path,
            str(int(content_length)),
            SERVICE_ID,
            RUNTIME_ID,
            str(int(pid)),
        )
    ).encode("utf-8")
    return hmac.new(instance_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


class RequestAuthenticator:
    def __init__(self, instance_key: str, *, pid: int | None = None) -> None:
        if len(instance_key) < 16:
            raise RuntimeError("runtime instance credential is missing")
        self._instance_key = instance_key
        self._pid = int(pid if pid is not None else os.getpid())
        self._guard = threading.Lock()
        # The credential is unique to this child generation, so retaining every
        # nonce for the process lifetime is bounded by that generation and
        # prevents an old authorized request from becoming replayable again.
        self._seen_requests: set[str] = set()

    def verify_headers(
        self,
        *,
        nonce: str,
        header_proof: str,
        method: str,
        path: str,
        content_length: int,
    ) -> None:
        if (
            not _NONCE.fullmatch(nonce)
            or not _HEX64.fullmatch(header_proof)
            or isinstance(content_length, bool)
            or not isinstance(content_length, int)
            or content_length <= 0
        ):
            raise ProtocolError("REQUEST_AUTH_INVALID", "runtime request authorization is invalid", status=401)
        expected = _request_header_proof(
            self._instance_key,
            nonce=nonce,
            method=method,
            path=path,
            content_length=content_length,
            pid=self._pid,
        )
        if not hmac.compare_digest(header_proof, expected):
            raise ProtocolError("REQUEST_AUTH_INVALID", "runtime request authorization is invalid", status=401)
        with self._guard:
            if nonce in self._seen_requests:
                raise ProtocolError("REQUEST_REPLAYED", "runtime request nonce was already used", status=409)
            # Reserve before reading the body. Failed, stalled and malformed
            # bodies burn the nonce instead of permitting a retry/replay.
            self._seen_requests.add(nonce)

    def verify_body(
        self,
        *,
        nonce: str,
        proof: str,
        method: str,
        path: str,
        body_sha256: str,
    ) -> None:
        if not _NONCE.fullmatch(nonce) or not _HEX64.fullmatch(proof) or not _HEX64.fullmatch(body_sha256):
            raise ProtocolError("REQUEST_AUTH_INVALID", "runtime request authorization is invalid", status=401)
        with self._guard:
            if nonce not in self._seen_requests:
                raise ProtocolError("REQUEST_AUTH_INVALID", "request nonce was not header-authorized", status=401)
        expected = _request_proof(
            self._instance_key,
            nonce=nonce,
            method=method,
            path=path,
            body_sha256=body_sha256,
            pid=self._pid,
        )
        if not hmac.compare_digest(proof, expected):
            raise ProtocolError("REQUEST_AUTH_INVALID", "runtime request authorization is invalid", status=401)

    def health(self, nonce: str, *, ready: bool) -> dict[str, Any]:
        if not _NONCE.fullmatch(nonce):
            raise ProtocolError("HEALTH_NONCE_INVALID", "health nonce is invalid", status=400)
        return {
            "schema": HEALTH_SCHEMA,
            "runtime_id": RUNTIME_ID,
            "service_id": SERVICE_ID,
            "pid": self._pid,
            "listener_pid": self._pid,
            "ready": bool(ready),
            "proof": _health_proof(self._instance_key, nonce, pid=self._pid, ready=ready),
        }


class ModelBackend(Protocol):
    def create_prompt(self, *, audio_path: Path, transcript: str, language: str) -> Any: ...

    def synthesize(self, *, prompt: Any, text: str, language: str) -> bytes: ...


def _version_tuple(value: str) -> tuple[int, ...]:
    if not _DRIVER_VERSION.fullmatch(value):
        raise RuntimeError("NVIDIA driver version is invalid")
    return tuple(int(part) for part in value.split("."))


def _query_nvidia_driver() -> str:
    candidates = []
    windows_root = str(os.environ.get("WINDIR") or os.environ.get("SYSTEMROOT") or "").strip()
    program_files = str(os.environ.get("PROGRAMFILES") or "").strip()
    if windows_root:
        candidates.append(Path(windows_root) / "System32" / "nvidia-smi.exe")
    if program_files:
        candidates.append(Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe")
    executable = next((candidate for candidate in candidates if candidate.is_file() and not _is_reparse(candidate)), None)
    if executable is None:
        raise RuntimeError("NVIDIA driver query tool is unavailable")
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    completed = subprocess.run(
        [str(executable), "--query-gpu=driver_version", "--format=csv,noheader"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=10.0,
        check=True,
        shell=False,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    versions = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not versions or any(not _DRIVER_VERSION.fullmatch(version) for version in versions):
        raise RuntimeError("NVIDIA driver query returned an invalid version")
    return min(versions, key=_version_tuple)


_CORE_V1_IMPORTS = (
    "from .tokenizer_25hz.configuration_qwen3_tts_tokenizer_v1 import Qwen3TTSTokenizerV1Config\n",
    "from .tokenizer_25hz.modeling_qwen3_tts_tokenizer_v1 import Qwen3TTSTokenizerV1Model\n",
)
_TOKENIZER_V1_IMPORTS = (
    "    Qwen3TTSTokenizerV1Config,\n",
    "    Qwen3TTSTokenizerV1Model,\n",
)
_TOKENIZER_V1_REGISTRATION = (
    "        AutoConfig.register(\"qwen3_tts_tokenizer_25hz\", Qwen3TTSTokenizerV1Config)\n"
    "        AutoModel.register(Qwen3TTSTokenizerV1Config, Qwen3TTSTokenizerV1Model)\n\n"
)
_QWEN_OVERLAY_OPERATIONS = {
    "qwen_tts/core/__init__.py": ["remove-v1-imports"],
    "qwen_tts/inference/qwen3_tts_tokenizer.py": [
        "remove-v1-imports",
        "remove-v1-registrations",
        "require-12hz-config-before-load",
        "require-12hz-model-after-load",
        "require-12hz-encode-decode",
    ],
}
_QWEN_OVERLAY_MODULES = {
    "qwen_tts.core": "qwen_tts/core/__init__.py",
    "qwen_tts.inference.qwen3_tts_tokenizer": "qwen_tts/inference/qwen3_tts_tokenizer.py",
}
_QWEN_FORBIDDEN_IMPORT_PREFIXES = ("qwen_tts.core.tokenizer_25hz", "sox")
_qwen_overlay_state: dict[str, Any] | None = None


def _replace_overlay_once(source: str, old: str, new: str, operation: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"Qwen overlay input drifted at {operation}")
    return source.replace(old, new, 1)


def _transform_qwen_source(relative: str, original: bytes) -> tuple[bytes, bytes]:
    expected = QWEN_OVERLAY_INPUTS.get(relative)
    if expected is None or hashlib.sha256(original).hexdigest() != expected:
        raise RuntimeError(f"Qwen overlay input hash mismatch: {relative}")
    try:
        source = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"Qwen overlay input is not UTF-8: {relative}") from exc
    if relative == "qwen_tts/core/__init__.py":
        for line in _CORE_V1_IMPORTS:
            source = _replace_overlay_once(source, line, "", "remove-v1-imports")
    elif relative == "qwen_tts/inference/qwen3_tts_tokenizer.py":
        source = _replace_overlay_once(
            source,
            "import base64\n",
            "import base64\nimport hashlib\nimport json\nimport os\n",
            "add-12hz-gate-imports",
        )
        for line in _TOKENIZER_V1_IMPORTS:
            source = _replace_overlay_once(source, line, "", "remove-v1-imports")
        source = _replace_overlay_once(
            source,
            _TOKENIZER_V1_REGISTRATION,
            "",
            "remove-v1-registrations",
        )
        source = _replace_overlay_once(
            source,
            "        inst = cls()\n\n",
            "        inst = cls()\n"
            "        config_path = os.path.join(os.fspath(pretrained_model_name_or_path), \"config.json\")\n"
            "        try:\n"
            "            with open(config_path, \"rb\") as config_handle:\n"
            "                config_bytes = config_handle.read()\n"
            "            speech_config = json.loads(config_bytes.decode(\"utf-8\"))\n"
            "        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:\n"
            "            raise RuntimeError(\"12Hz speech tokenizer config is unavailable or invalid\") from exc\n"
            f"        if hashlib.sha256(config_bytes).hexdigest() != \"{QWEN_SPEECH_CONFIG_SHA256}\":\n"
            "            raise RuntimeError(\"12Hz speech tokenizer config hash mismatch\")\n"
            f"        if speech_config.get(\"model_type\") != \"{QWEN_12HZ_MODEL_TYPE}\":\n"
            "            raise RuntimeError(\"only the locked 12Hz speech tokenizer is supported\")\n\n",
            "require-12hz-config-before-load",
        )
        source = _replace_overlay_once(
            source,
            "        inst.config = inst.model.config\n\n",
            "        inst.config = inst.model.config\n"
            f"        if inst.model.get_model_type() != \"{QWEN_12HZ_MODEL_TYPE}\":\n"
            "            raise RuntimeError(\"loaded speech tokenizer is not the locked 12Hz model\")\n\n",
            "require-12hz-model-after-load",
        )
        source = _replace_overlay_once(
            source,
            "        wavs = self._normalize_audio_inputs(audios, sr=sr)\n",
            f"        if self.model.get_model_type() != \"{QWEN_12HZ_MODEL_TYPE}\":\n"
            "            raise RuntimeError(\"encode is restricted to the locked 12Hz speech tokenizer\")\n"
            "        wavs = self._normalize_audio_inputs(audios, sr=sr)\n",
            "require-12hz-encode",
        )
        source = _replace_overlay_once(
            source,
            "        model_type = self.model.get_model_type()\n\n",
            "        model_type = self.model.get_model_type()\n"
            f"        if model_type != \"{QWEN_12HZ_MODEL_TYPE}\":\n"
            "            raise RuntimeError(\"decode is restricted to the locked 12Hz speech tokenizer\")\n\n",
            "require-12hz-decode",
        )
    else:
        raise RuntimeError(f"unsupported Qwen overlay source: {relative}")
    transformed = source.encode("utf-8")
    patch = "".join(
        difflib.unified_diff(
            original.decode("utf-8").splitlines(keepends=True),
            source.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            lineterm="\n",
        )
    ).encode("utf-8")
    if not patch:
        raise RuntimeError(f"Qwen overlay unexpectedly produced an empty patch: {relative}")
    return transformed, patch


def build_qwen_12hz_overlay_contract(site_packages_root: Path) -> dict[str, Any]:
    root = Path(site_packages_root).resolve(strict=True)
    qwen_root = root / "qwen_tts"
    if not qwen_root.is_dir() or _is_reparse(qwen_root):
        raise RuntimeError("locked qwen_tts package root is missing or unsafe")
    transforms = []
    for module, relative in _QWEN_OVERLAY_MODULES.items():
        source_path = root.joinpath(*PurePosixPath(relative).parts)
        if not source_path.is_file() or _is_reparse(source_path):
            raise RuntimeError(f"Qwen overlay source is missing or unsafe: {relative}")
        original = source_path.read_bytes()
        transformed, patch = _transform_qwen_source(relative, original)
        transforms.append(
            {
                "module": module,
                "path": relative,
                "input_sha256": hashlib.sha256(original).hexdigest(),
                "output_sha256": hashlib.sha256(transformed).hexdigest(),
                "patch_sha256": hashlib.sha256(patch).hexdigest(),
                "operations": _QWEN_OVERLAY_OPERATIONS[relative],
            }
        )
    transforms.sort(key=lambda item: item["path"])
    return {
        "schema": QWEN_OVERLAY_SCHEMA,
        "policy": "qwen3-tts-12hz-only-no-sox",
        "application": "verified-in-memory-import-loader",
        "upstream_wheel": {
            "name": "qwen-tts",
            "version": "0.1.1",
            "sha256": QWEN_WHEEL_SHA256,
        },
        "transforms": transforms,
        "model_identity": {
            "main_config": {
                "path": "config.json",
                "sha256": QWEN_MODEL_CONFIG_SHA256,
                "required_fields": {
                    "model_type": "qwen3_tts",
                    "tokenizer_type": QWEN_12HZ_MODEL_TYPE,
                    "tts_model_size": "0b6",
                    "tts_model_type": "base",
                },
            },
            "speech_tokenizer_config": {
                "path": "speech_tokenizer/config.json",
                "sha256": QWEN_SPEECH_CONFIG_SHA256,
                "required_fields": {"model_type": QWEN_12HZ_MODEL_TYPE},
            },
        },
        "forbidden_imports": ["qwen_tts.core.tokenizer_25hz", "sox"],
        "upstream_wheel_bytes_modified": False,
    }


def _load_exact_json(path: Path, *, label: str) -> tuple[bytes, dict[str, Any]]:
    if not path.is_file() or _is_reparse(path):
        raise RuntimeError(f"{label} is missing or unsafe")
    content = path.read_bytes()
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return content, payload


def _validate_qwen_12hz_model_identity(pack_root: Path) -> Path:
    model_path = pack_root.joinpath(*PurePosixPath(MODEL_RELATIVE).parts)
    if not model_path.is_dir() or _is_reparse(model_path):
        raise RuntimeError("pack-relative Qwen model is missing or unsafe")
    checks = (
        (
            "config.json",
            QWEN_MODEL_CONFIG_SHA256,
            {
                "model_type": "qwen3_tts",
                "tokenizer_type": QWEN_12HZ_MODEL_TYPE,
                "tts_model_size": "0b6",
                "tts_model_type": "base",
            },
        ),
        (
            "speech_tokenizer/config.json",
            QWEN_SPEECH_CONFIG_SHA256,
            {"model_type": QWEN_12HZ_MODEL_TYPE},
        ),
    )
    for relative, expected_hash, required_fields in checks:
        content, payload = _load_exact_json(
            model_path.joinpath(*PurePosixPath(relative).parts),
            label=f"Qwen model {relative}",
        )
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise RuntimeError(f"Qwen model {relative} hash mismatch")
        if any(payload.get(field) != expected for field, expected in required_fields.items()):
            raise RuntimeError(f"Qwen model {relative} is not the locked 12Hz Base 0.6B identity")
    return model_path


class _Qwen12HzOverlayFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, site_packages: Path, contract: dict[str, Any]) -> None:
        self.site_packages = site_packages
        transforms = {item["module"]: item for item in contract["transforms"]}
        self.modules: dict[str, dict[str, Any]] = {}
        for module, relative in _QWEN_OVERLAY_MODULES.items():
            source_path = site_packages.joinpath(*PurePosixPath(relative).parts)
            original = source_path.read_bytes()
            transformed, patch = _transform_qwen_source(relative, original)
            declaration = transforms.get(module)
            if (
                declaration is None
                or declaration["input_sha256"] != hashlib.sha256(original).hexdigest()
                or declaration["output_sha256"] != hashlib.sha256(transformed).hexdigest()
                or declaration["patch_sha256"] != hashlib.sha256(patch).hexdigest()
            ):
                raise RuntimeError(f"Qwen overlay contract does not match transformed source: {relative}")
            self.modules[module] = {
                "source": transformed,
                "origin": source_path,
                "package": relative.endswith("/__init__.py"),
            }

    def find_spec(self, fullname: str, path: Any = None, target: Any = None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in _QWEN_FORBIDDEN_IMPORT_PREFIXES):
            raise ImportError(f"forbidden Qwen 25Hz/SoX import: {fullname}")
        item = self.modules.get(fullname)
        if item is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            self,
            origin=str(item["origin"]),
            is_package=bool(item["package"]),
        )

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        item = self.modules[module.__name__]
        module.__file__ = str(item["origin"])
        if item["package"]:
            module.__path__ = [str(Path(item["origin"]).parent)]
        code = compile(item["source"], str(item["origin"]), "exec", dont_inherit=True)
        exec(code, module.__dict__)


def install_qwen_12hz_only_runtime(pack_root: Path) -> dict[str, Any]:
    global _qwen_overlay_state
    numba_runtime = _enforce_numba_workqueue()
    root = Path(pack_root).resolve(strict=True)
    _validate_qwen_12hz_model_identity(root)
    if _qwen_overlay_state is not None:
        if _qwen_overlay_state["pack_root"] != root:
            raise RuntimeError("Qwen overlay is already bound to another pack root")
        return dict(_qwen_overlay_state)
    forbidden_loaded = sorted(
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in _QWEN_FORBIDDEN_IMPORT_PREFIXES)
    )
    already_loaded_qwen = sorted(name for name in sys.modules if name == "qwen_tts" or name.startswith("qwen_tts."))
    if forbidden_loaded or already_loaded_qwen:
        raise RuntimeError(
            f"Qwen overlay must install before Qwen/SoX imports; forbidden={forbidden_loaded}, qwen={already_loaded_qwen}"
        )
    lock_path = root.joinpath(*PurePosixPath(DEPENDENCY_LOCK_RELATIVE).parts)
    _lock_content, lock = _load_exact_json(lock_path, label="runtime dependency lock")
    packages = lock.get("packages")
    qwen_entries = [
        item
        for item in packages
        if isinstance(item, dict) and item.get("name") == "qwen-tts"
    ] if isinstance(packages, list) else []
    if (
        lock.get("schema") != "mentorflow.qwen-runtime-dependency-lock.v1"
        or len(qwen_entries) != 1
        or qwen_entries[0].get("version") != "0.1.1"
        or qwen_entries[0].get("wheel_sha256") != QWEN_WHEEL_SHA256
    ):
        raise RuntimeError("runtime dependency lock does not bind the trusted qwen-tts wheel")
    site_packages = root.joinpath(*PurePosixPath(QWEN_SITE_PACKAGES_RELATIVE).parts)
    expected_contract = build_qwen_12hz_overlay_contract(site_packages)
    contract_path = root.joinpath(*PurePosixPath(SOURCE_OVERLAY_RELATIVE).parts)
    contract_content, contract = _load_exact_json(contract_path, label="Qwen 12Hz source overlay contract")
    if contract != expected_contract:
        raise RuntimeError("Qwen 12Hz source overlay contract does not match source bytes")
    finder = _Qwen12HzOverlayFinder(site_packages, contract)
    sys.meta_path.insert(0, finder)
    _qwen_overlay_state = {
        "status": "installed",
        "pack_root": root,
        "contract_sha256": hashlib.sha256(contract_content).hexdigest(),
        "wheel_sha256": QWEN_WHEEL_SHA256,
        "input_sha256": dict(QWEN_OVERLAY_INPUTS),
        "output_sha256": {
            item["path"]: item["output_sha256"] for item in contract["transforms"]
        },
        "patch_sha256": {
            item["path"]: item["patch_sha256"] for item in contract["transforms"]
        },
        "numba_threading_layer": numba_runtime["configured"],
        "numba_active_config": numba_runtime["active_config"],
        "tbbpool_loaded": numba_runtime["tbbpool_loaded"],
        "tbb": numba_runtime["tbb"],
        "sox_loaded": "sox" in sys.modules,
        "tokenizer_25hz_loaded": any(
            name == "qwen_tts.core.tokenizer_25hz" or name.startswith("qwen_tts.core.tokenizer_25hz.")
            for name in sys.modules
        ),
    }
    return dict(_qwen_overlay_state)


class QwenModelBackend:
    def __init__(self, *, pack_root: Path, device: str, minimum_driver: str) -> None:
        self.pack_root = pack_root
        self.device = device
        self.minimum_driver = minimum_driver
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        current_driver = _query_nvidia_driver()
        if _version_tuple(current_driver) < _version_tuple(self.minimum_driver):
            raise RuntimeError("NVIDIA driver does not meet the signed runtime gate")
        model_path = _validate_qwen_12hz_model_identity(self.pack_root)
        overlay = install_qwen_12hz_only_runtime(self.pack_root)
        if overlay["sox_loaded"] or overlay["tokenizer_25hz_loaded"]:
            raise RuntimeError("Qwen 25Hz/SoX modules were loaded before model initialization")
        import torch
        from qwen_tts import Qwen3TTSModel

        numba_runtime = _enforce_numba_workqueue()
        if numba_runtime["tbbpool_loaded"]:
            raise RuntimeError("Numba TBB pool loaded on the Qwen product import path")

        if "sox" in sys.modules or any(
            name == "qwen_tts.core.tokenizer_25hz" or name.startswith("qwen_tts.core.tokenizer_25hz.")
            for name in sys.modules
        ):
            raise RuntimeError("Qwen import crossed the locked 12Hz-only boundary")
        if self.device != "cuda:0" or not torch.cuda.is_available():
            raise RuntimeError("the configured CUDA device is unavailable")
        model = Qwen3TTSModel.from_pretrained(
            str(model_path),
            device_map=self.device,
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
            local_files_only=True,
        )
        speech_tokenizer = getattr(getattr(model, "model", None), "speech_tokenizer", None)
        get_model_type = getattr(speech_tokenizer, "get_model_type", None)
        if not callable(get_model_type) or get_model_type() != QWEN_12HZ_MODEL_TYPE:
            raise RuntimeError("loaded Qwen model is not bound to the locked 12Hz speech tokenizer")
        if "sox" in sys.modules or any(
            name == "qwen_tts.core.tokenizer_25hz" or name.startswith("qwen_tts.core.tokenizer_25hz.")
            for name in sys.modules
        ):
            raise RuntimeError("Qwen model loading imported forbidden 25Hz/SoX modules")
        self._model = model
        return model

    def create_prompt(self, *, audio_path: Path, transcript: str, language: str) -> Any:
        if language != "zh":
            raise RuntimeError("only verified Chinese references are supported")
        return self._load().create_voice_clone_prompt(
            ref_audio=str(audio_path),
            ref_text=transcript,
            x_vector_only_mode=False,
        )

    def synthesize(self, *, prompt: Any, text: str, language: str) -> bytes:
        if language != "zh":
            raise RuntimeError("only Chinese synthesis is supported")
        wavs, sample_rate = self._load().generate_voice_clone(
            text=text,
            language="Chinese",
            voice_clone_prompt=prompt,
            non_streaming_mode=True,
        )
        if len(wavs) != 1:
            raise RuntimeError("Qwen runtime returned an unexpected waveform count")
        import soundfile as sound_file

        buffer = io.BytesIO()
        sound_file.write(buffer, wavs[0], int(sample_rate), format="WAV", subtype="PCM_16")
        audio = buffer.getvalue()
        _validate_wav(audio, "synthesized audio")
        return audio


def _ensure_cache_directory(path: Path, *, boundary: Path) -> Path:
    candidate = path.resolve(strict=False)
    root = boundary.resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise RuntimeError("runtime cache path escapes its root")
    boundary.mkdir(parents=True, exist_ok=True)
    current = boundary
    if _is_reparse(current):
        raise RuntimeError("runtime cache root cannot be a reparse point")
    for part in candidate.relative_to(root).parts:
        current = current / part
        current.mkdir(exist_ok=True)
        if _is_reparse(current) or not current.is_dir():
            raise RuntimeError("runtime cache directory is unsafe")
    return candidate


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".stage-", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class QwenBaseCloneRuntimeState:
    """Single model, two prompt caches and one synthesis single-flight lock."""

    def __init__(self, *, backend: ModelBackend, cache_root: Path) -> None:
        self.backend = backend
        self.cache_root = Path(cache_root).resolve(strict=False)
        self._guard = threading.RLock()
        self._prepared_binding: dict[str, Any] | None = None
        self._prompts: dict[str, Any] = {}
        self._audio_cache: OrderedDict[str, bytes] = OrderedDict()
        self._audio_cache_bytes = 0

    @staticmethod
    def _receipt(binding: Mapping[str, Any]) -> dict[str, Any]:
        engine = binding["engine"]
        return {
            "schema": PREPARE_SCHEMA,
            "ready": True,
            "engine_kind": engine["kind"],
            "engine_catalog_id": engine["catalog_id"],
            "engine_version": engine["version"],
            "engine_manifest_sha256": engine["manifest_sha256"],
            "pack_id": binding["pack_id"],
            "pack_version": binding["version"],
            "pack_manifest_sha256": binding["manifest_sha256"],
            "binding_sha256": binding["binding_sha256"],
            "references_sha256": binding["references_sha256"],
        }

    def prepare(self, payload: Any) -> dict[str, Any]:
        request = _strict_object(payload, {"schema", "binding"}, "prepare request")
        if request["schema"] != PREPARE_SCHEMA:
            raise ProtocolError("SCHEMA_INVALID", "prepare schema is invalid")
        binding, audio_by_style = _validate_binding(request["binding"], include_audio=True)
        with self._guard:
            if self._prepared_binding == binding and set(self._prompts) == {"lively", "relaxed"}:
                return self._receipt(binding)
            reference_root = _ensure_cache_directory(
                self.cache_root / "references" / binding["binding_sha256"],
                boundary=self.cache_root,
            )
            prepared: dict[str, Any] = {}
            for reference in binding["references"]:
                style = reference["style"]
                audio = audio_by_style[style]
                reference_path = reference_root / f"{style}-{reference['audio_sha256']}.wav"
                if reference_path.is_file() and not _is_reparse(reference_path):
                    existing = reference_path.read_bytes()
                    if not hmac.compare_digest(hashlib.sha256(existing).hexdigest(), reference["audio_sha256"]):
                        raise RuntimeError("cached reference audio was modified")
                else:
                    if reference_path.exists():
                        raise RuntimeError("cached reference path is unsafe")
                    _atomic_write(reference_path, audio)
                prepared[style] = self.backend.create_prompt(
                    audio_path=reference_path,
                    transcript=reference["transcript"],
                    language=reference["language"],
                )
            self._prepared_binding = binding
            self._prompts = prepared
            self._audio_cache.clear()
            self._audio_cache_bytes = 0
            return self._receipt(binding)

    def _validate_synthesis(self, payload: Any) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        request = _strict_object(payload, _SYNTHESIS_FIELDS, "synthesis request")
        if request["schema"] != SYNTHESIS_SCHEMA:
            raise ProtocolError("SCHEMA_INVALID", "synthesis schema is invalid")
        binding, audio_by_style = _validate_binding(request["binding"], include_audio=False)
        if audio_by_style:
            raise ProtocolError("PROTOCOL_FIELDS_INVALID", "synthesis binding cannot carry audio")
        reference, audio = _validate_reference(request["reference"], include_audio=False)
        if audio is not None:
            raise ProtocolError("PROTOCOL_FIELDS_INVALID", "synthesis reference cannot carry audio")
        text = str(request["text"] or "")
        reply_kind = str(request["reply_kind"] or "")
        if not text.strip() or len(text) > MAX_TEXT_CHARACTERS:
            raise ProtocolError("TEXT_INVALID", "synthesis text is empty or too long")
        if len(reply_kind) > MAX_REPLY_KIND_CHARACTERS:
            raise ProtocolError("REQUEST_INVALID", "reply_kind is too long")
        style = str(request["style"] or "")
        expected_reference = next((item for item in binding["references"] if item["style"] == style), None)
        if expected_reference is None or reference != expected_reference:
            raise ProtocolError("BINDING_MISMATCH", "synthesis reference does not match its style")
        engine = binding["engine"]
        comparisons = {
            "binding_sha256": binding["binding_sha256"],
            "engine_kind": engine["kind"],
            "engine_catalog_id": engine["catalog_id"],
            "engine_version": engine["version"],
            "engine_manifest_sha256": engine["manifest_sha256"],
            "pack_id": binding["pack_id"],
            "pack_version": binding["version"],
            "pack_manifest_sha256": binding["manifest_sha256"],
            "text_sha256": _text_sha256(text),
            "style": style,
            "reference_audio_sha256": reference["audio_sha256"],
            "reference_transcript_sha256": reference["transcript_sha256"],
            "reply_kind": reply_kind,
        }
        for field, expected in comparisons.items():
            if request[field] != expected:
                raise ProtocolError("BINDING_MISMATCH", f"synthesis {field} does not match")
        request_identity = {
            "binding_sha256": binding["binding_sha256"],
            "engine_kind": engine["kind"],
            "engine_catalog_id": engine["catalog_id"],
            "engine_version": engine["version"],
            "engine_manifest_sha256": engine["manifest_sha256"],
            "pack_id": binding["pack_id"],
            "pack_version": binding["version"],
            "pack_manifest_sha256": binding["manifest_sha256"],
            "style": style,
            "reference_audio_sha256": reference["audio_sha256"],
            "reference_transcript_sha256": reference["transcript_sha256"],
            "text_sha256": comparisons["text_sha256"],
            "reply_kind": reply_kind,
        }
        request_sha256 = _canonical_sha256(request_identity)
        if request["request_sha256"] != request_sha256:
            raise ProtocolError("BINDING_MISMATCH", "request_sha256 does not match")
        return request, binding, text, reply_kind

    def synthesize(self, payload: Any) -> dict[str, Any]:
        request, binding, text, reply_kind = self._validate_synthesis(payload)
        with self._guard:
            if self._prepared_binding != binding or request["style"] not in self._prompts:
                raise ProtocolError("RUNTIME_NOT_PREPARED", "this exact pack binding is not prepared", status=409)
            cache_state = "miss"
            cache_key = request["request_sha256"]
            cached = self._audio_cache.get(cache_key)
            if cached is not None:
                audio = cached
                _validate_wav(audio, "cached synthesis")
                self._audio_cache.move_to_end(cache_key)
                cache_state = "hit"
            else:
                audio = self.backend.synthesize(
                    prompt=self._prompts[request["style"]],
                    text=text,
                    language="zh",
                )
                _validate_wav(audio, "synthesized audio")
                encoded_length = 4 * ((len(audio) + 2) // 3)
                if encoded_length > MAX_AUDIO_BASE64_BYTES:
                    raise ProtocolError(
                        "AUDIO_RESPONSE_TOO_LARGE",
                        "synthesized audio exceeds the encoded response limit",
                        status=503,
                    )
                self._audio_cache[cache_key] = audio
                self._audio_cache_bytes += len(audio)
                while (
                    len(self._audio_cache) > MAX_AUDIO_CACHE_ENTRIES
                    or self._audio_cache_bytes > MAX_AUDIO_CACHE_BYTES
                ):
                    _evicted_key, evicted_audio = self._audio_cache.popitem(last=False)
                    self._audio_cache_bytes -= len(evicted_audio)
            encoded_length = 4 * ((len(audio) + 2) // 3)
            if encoded_length > MAX_AUDIO_BASE64_BYTES:
                raise ProtocolError(
                    "AUDIO_RESPONSE_TOO_LARGE",
                    "synthesis audio exceeds the encoded response limit",
                    status=503,
                )
            audio_sha256 = hashlib.sha256(audio).hexdigest()
            engine = binding["engine"]
            receipt = {
                "schema": SYNTHESIS_SCHEMA,
                "media_type": "audio/wav",
                "engine_kind": engine["kind"],
                "engine_catalog_id": engine["catalog_id"],
                "engine_version": engine["version"],
                "engine_manifest_sha256": engine["manifest_sha256"],
                "pack_id": binding["pack_id"],
                "pack_version": binding["version"],
                "pack_manifest_sha256": binding["manifest_sha256"],
                "binding_sha256": binding["binding_sha256"],
                "style": request["style"],
                "reference_audio_sha256": request["reference_audio_sha256"],
                "reference_transcript_sha256": request["reference_transcript_sha256"],
                "request_text_sha256": request["text_sha256"],
                "reply_kind": reply_kind,
                "request_sha256": request["request_sha256"],
                "audio_sha256": audio_sha256,
                "audio_bytes": len(audio),
                "profile": request["style"],
                "cache": cache_state,
            }
            encoded_audio = base64.b64encode(audio).decode("ascii")
            if len(encoded_audio) != encoded_length:
                raise ProtocolError("AUDIO_RESPONSE_INVALID", "encoded synthesis audio length changed", status=503)
            return {
                "schema": SYNTHESIS_SCHEMA,
                "receipt": receipt,
                "audio_base64": encoded_audio,
            }


class _ThreadedLoopbackServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler: type[BaseHTTPRequestHandler],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        request_queue_size: int = DEFAULT_REQUEST_QUEUE_SIZE,
        header_timeout_seconds: float = DEFAULT_HEADER_TIMEOUT_SECONDS,
        body_timeout_seconds: float = DEFAULT_BODY_TIMEOUT_SECONDS,
    ) -> None:
        if not 1 <= int(max_workers) <= 64:
            raise ValueError("max_workers must be between 1 and 64")
        if not 1 <= int(request_queue_size) <= 128:
            raise ValueError("request_queue_size must be between 1 and 128")
        if not 0.05 <= float(header_timeout_seconds) <= 30.0:
            raise ValueError("header timeout is outside the supported range")
        if not 0.05 <= float(body_timeout_seconds) <= 300.0:
            raise ValueError("body timeout is outside the supported range")
        self.request_queue_size = int(request_queue_size)
        self.header_timeout_seconds = float(header_timeout_seconds)
        self.body_timeout_seconds = float(body_timeout_seconds)
        self._worker_slots = threading.BoundedSemaphore(int(max_workers))
        self._worker_guard = threading.Lock()
        self._active_requests = 0
        super().__init__(server_address, request_handler)

    @property
    def active_request_count(self) -> int:
        with self._worker_guard:
            return self._active_requests

    def get_request(self) -> tuple[socket.socket, Any]:
        request, client_address = super().get_request()
        return request, client_address

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._worker_slots.acquire(blocking=False):
            try:
                # Drain only bytes already queued by the kernel. On Windows,
                # closing a socket with unread request bytes can turn the
                # overload response into a TCP reset before the client sees
                # the 503. This loop is non-blocking and byte-bounded.
                request.setblocking(False)
                remaining = 64 * 1024
                while remaining > 0:
                    try:
                        chunk = request.recv(min(remaining, 4096))
                    except (BlockingIOError, InterruptedError):
                        break
                    if not chunk:
                        break
                    remaining -= len(chunk)
                request.settimeout(0.25)
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\n"
                    b"Cache-Control: no-store\r\n"
                    b"Connection: close\r\n\r\n"
                )
                request.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            finally:
                request.close()
            return
        with self._worker_guard:
            self._active_requests += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._worker_guard:
                self._active_requests -= 1
            self._worker_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._worker_guard:
                self._active_requests -= 1
            self._worker_slots.release()


class RuntimeRequestHandler(BaseHTTPRequestHandler):
    server_version = "MentorFlowVoice/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        self._deadline_guard = threading.Lock()
        self._deadline_timer: threading.Timer | None = None
        self._deadline_token = 0
        self._expired_stage: str | None = None
        super().setup()
        self._arm_absolute_deadline(
            self.server.header_timeout_seconds,  # type: ignore[attr-defined]
            "headers",
        )

    def _arm_absolute_deadline(self, seconds: float, stage: str) -> None:
        self._cancel_absolute_deadline()
        with self._deadline_guard:
            self._deadline_token += 1
            token = self._deadline_token
            timer = threading.Timer(
                float(seconds),
                self._expire_absolute_deadline,
                args=(token, stage),
            )
            timer.daemon = True
            self._deadline_timer = timer
        timer.start()

    def _expire_absolute_deadline(self, token: int, stage: str) -> None:
        with self._deadline_guard:
            if token != self._deadline_token or self._deadline_timer is None:
                return
            self._deadline_timer = None
            self._expired_stage = stage
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _cancel_absolute_deadline(self) -> None:
        timer: threading.Timer | None
        with self._deadline_guard:
            self._deadline_token += 1
            timer = self._deadline_timer
            self._deadline_timer = None
        if timer is not None:
            timer.cancel()
            if timer is not threading.current_thread():
                timer.join()

    def parse_request(self) -> bool:
        try:
            return super().parse_request()
        finally:
            # Header parsing has either completed or failed. Trickle traffic
            # cannot extend this deadline because the Timer is wall-clock based.
            self._cancel_absolute_deadline()

    def finish(self) -> None:
        try:
            self._cancel_absolute_deadline()
        finally:
            super().finish()

    @property
    def runtime_state(self) -> QwenBaseCloneRuntimeState:
        return self.server.runtime_state  # type: ignore[attr-defined]

    @property
    def authenticator(self) -> RequestAuthenticator:
        return self.server.authenticator  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: Any) -> None:
        return None

    def _send_json(self, status: int, value: Any) -> None:
        body = _canonical_json_bytes(value)
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            # A timed-out or cancelled local client is not a server failure and
            # must not amplify a slow-connection attack into traceback spam.
            pass
        finally:
            self.close_connection = True

    def _send_error(self, exc: ProtocolError) -> None:
        self._send_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})

    def _one_header(self, name: str) -> str:
        values = self.headers.get_all(name, failobj=[]) or []
        if len(values) != 1:
            raise ProtocolError("HEADER_INVALID", f"exactly one {name} header is required", status=400)
        return str(values[0]).strip()

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path != "/health":
                raise ProtocolError("NOT_FOUND", "endpoint not found", status=404)
            nonce = self._one_header(HEALTH_NONCE_HEADER)
            self._send_json(HTTPStatus.OK, self.authenticator.health(nonce, ready=True))
        except ProtocolError as exc:
            self._send_error(exc)

    def _validated_content_length(self, maximum: int) -> int:
        if str(self.headers.get("Transfer-Encoding") or "").strip():
            raise ProtocolError("BODY_INVALID", "transfer encoding is not supported", status=400)
        if str(self.headers.get("Content-Encoding") or "").strip():
            raise ProtocolError("BODY_INVALID", "content encoding is not supported", status=400)
        content_type = self._one_header("Content-Type").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ProtocolError("BODY_INVALID", "content type must be application/json", status=415)
        raw_length = self._one_header("Content-Length")
        if not raw_length.isdecimal():
            raise ProtocolError("BODY_INVALID", "content length is invalid", status=400)
        length = int(raw_length)
        if not 1 <= length <= maximum:
            raise ProtocolError("BODY_TOO_LARGE", "request body exceeds its endpoint limit", status=413)
        return length

    def _read_body(self, length: int) -> bytes:
        body = self.rfile.read(length)
        if self._expired_stage == "body":
            self.close_connection = True
            raise ProtocolError("BODY_READ_TIMEOUT", "request body read timed out", status=408)
        if len(body) != length:
            raise ProtocolError("BODY_INVALID", "request body length does not match", status=400)
        return body

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/v1/prepare":
                maximum = MAX_PREPARE_BODY_BYTES
            elif self.path == "/v1/synthesize":
                maximum = MAX_SYNTHESIS_BODY_BYTES
            else:
                raise ProtocolError("NOT_FOUND", "endpoint not found", status=404)
            content_length = self._validated_content_length(maximum)
            nonce = self._one_header(REQUEST_NONCE_HEADER)
            self.authenticator.verify_headers(
                nonce=nonce,
                header_proof=self._one_header(REQUEST_HEADER_PROOF_HEADER),
                method="POST",
                path=self.path,
                content_length=content_length,
            )
            body_proof = self._one_header(REQUEST_PROOF_HEADER)
            self._arm_absolute_deadline(
                self.server.body_timeout_seconds,  # type: ignore[attr-defined]
                "body",
            )
            try:
                body = self._read_body(content_length)
            finally:
                self._cancel_absolute_deadline()
            self.authenticator.verify_body(
                nonce=nonce,
                proof=body_proof,
                method="POST",
                path=self.path,
                body_sha256=hashlib.sha256(body).hexdigest(),
            )
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolError("JSON_INVALID", "request body is not valid UTF-8 JSON", status=400) from exc
            if self.path == "/v1/prepare":
                response = self.runtime_state.prepare(payload)
            else:
                response = self.runtime_state.synthesize(payload)
            self._send_json(HTTPStatus.OK, response)
        except ProtocolError as exc:
            self._send_error(exc)
        except Exception:
            self._send_error(
                ProtocolError(
                    "RUNTIME_OPERATION_FAILED",
                    "the local Qwen runtime could not complete the request",
                    status=503,
                )
            )


def _runtime_configuration() -> tuple[Path, str, int, str, str, Path, str]:
    # Remove the credential from the inherited environment immediately. The
    # authenticator retains only its process-local copy for this generation.
    instance_key = str(os.environ.pop(INSTANCE_SECRET_ENV, "") or "")
    actual_root = Path(__file__).resolve().parents[2]
    raw_root = str(os.environ.get("MENTORFLOW_VOICE_RUNTIME_ROOT") or "").strip()
    if not raw_root:
        raise RuntimeError("runtime root is missing")
    configured_root = Path(raw_root).resolve(strict=False)
    host = str(os.environ.get("MENTORFLOW_VOICE_HOST") or "")
    raw_port = str(os.environ.get("MENTORFLOW_VOICE_PORT") or "")
    device = str(os.environ.get("MENTORFLOW_VOICE_DEVICE") or "")
    minimum_driver = str(os.environ.get("MENTORFLOW_VOICE_MIN_NVIDIA_DRIVER") or "")
    raw_cache_root = str(os.environ.get("MENTORFLOW_VOICE_CACHE_ROOT") or "")
    if configured_root != actual_root or host != LOOPBACK_HOST:
        raise RuntimeError("runtime root or host does not match the signed pack layout")
    if not raw_port.isdecimal() or not 1024 <= int(raw_port) <= 65535:
        raise RuntimeError("runtime port is invalid")
    if device != "cuda:0" or not _DRIVER_VERSION.fullmatch(minimum_driver):
        raise RuntimeError("runtime device or driver gate is invalid")
    if not raw_cache_root:
        raise RuntimeError("runtime cache root is missing")
    cache_root = Path(raw_cache_root).resolve(strict=False)
    if cache_root.parent == cache_root or cache_root.is_relative_to(actual_root):
        raise RuntimeError("runtime cache must remain outside the signed pack")
    if len(instance_key) < 16:
        raise RuntimeError("runtime instance credential is missing")
    return actual_root, host, int(raw_port), device, minimum_driver, cache_root, instance_key


def create_server(
    *,
    pack_root: Path,
    host: str,
    port: int,
    cache_root: Path,
    instance_key: str,
    backend: ModelBackend,
    max_workers: int = DEFAULT_MAX_WORKERS,
    request_queue_size: int = DEFAULT_REQUEST_QUEUE_SIZE,
    header_timeout_seconds: float = DEFAULT_HEADER_TIMEOUT_SECONDS,
    body_timeout_seconds: float = DEFAULT_BODY_TIMEOUT_SECONDS,
) -> HTTPServer:
    if host != LOOPBACK_HOST:
        raise RuntimeError("runtime server must bind to 127.0.0.1")
    server = _ThreadedLoopbackServer(
        (host, int(port)),
        RuntimeRequestHandler,
        max_workers=max_workers,
        request_queue_size=request_queue_size,
        header_timeout_seconds=header_timeout_seconds,
        body_timeout_seconds=body_timeout_seconds,
    )
    server.runtime_state = QwenBaseCloneRuntimeState(backend=backend, cache_root=cache_root)  # type: ignore[attr-defined]
    server.authenticator = RequestAuthenticator(instance_key)  # type: ignore[attr-defined]
    return server


def main() -> int:
    pack_root, host, port, device, minimum_driver, cache_root, instance_key = _runtime_configuration()
    backend = QwenModelBackend(
        pack_root=pack_root,
        device=device,
        minimum_driver=minimum_driver,
    )
    server = create_server(
        pack_root=pack_root,
        host=host,
        port=port,
        cache_root=cache_root,
        instance_key=instance_key,
        backend=backend,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

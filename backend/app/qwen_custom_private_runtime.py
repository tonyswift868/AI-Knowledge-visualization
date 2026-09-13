"""Persistent lazy runtime for the non-exportable custom voice prerelease."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import threading
from pathlib import Path
from .runtime_resources import project_resource_root
from typing import Any, Mapping

from .local_voice_runtime_supervisor import (
    LocalVoiceRuntimeError,
    LocalVoiceRuntimeSupervisor,
    RuntimeCatalogEntry,
    TrustedRuntimeCatalog,
    create_production_local_voice_runtime_supervisor,
)
from .qwen_base_clone_runtime import StrictLoopbackJsonTransport
from .private_voice_release_registration import (
    PrivateVoiceReleaseRegistrationError,
    resolve_app_private_release,
)


REGISTRATION_ENV = "MENTORFLOW_PRIVATE_QWEN_RUNTIME_REGISTRATION"
REGISTRATION_SCHEMA = "mentorflow.private-qwen-runtime-registration.v1"
COMPONENT_SCHEMA = "mentorflow.private-qwen-runtime-component.v1"
PORTABLE_RECEIPT_SCHEMA = "mentorflow.private-portable-qwen-runtime-receipt.v1"
RUNTIME_ID = "mentorflow.qwen3-tts-custom-private-runtime.v1"
SERVICE_ID = "mentorflow.qwen-custom-private-service.v1"
CATALOG_ID = "mentorflow.private-qwen-runtime"
RUNNER = "runtime/runner/qwen_custom_private_server.py"
BASE_RUNNER = "runtime/runner/qwen_base_clone_server.py"
PYTHON = "runtime/python/python.exe"
DEPENDENCY_LOCK = "metadata/dependency-lock.json"
OVERLAY_CONTRACT = "metadata/source-overlays/qwen-12hz-only.json"
_LOCK = threading.RLock()
_RUNTIME: "PrivateCustomLoopbackRuntime | None" = None
_BINDING: tuple[str, str, str, str] | None = None
_REGISTRATION_CACHE_KEY: tuple[Any, ...] | None = None
_REGISTRATION_CACHE: dict[str, Any] | None = None


class PrivateQwenRuntimeError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_reparse(path: Path, field: str) -> None:
    if os.name != "nt":
        return
    absolute = path.absolute()
    drive = absolute.drive
    if not drive or str(absolute).startswith("\\\\"):
        raise PrivateQwenRuntimeError(f"{field} must be on a local drive")
    import ctypes
    if ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") != 3:
        raise PrivateQwenRuntimeError(f"{field} must be on a fixed local drive")
    current = Path(f"{drive}\\")
    for part in absolute.parts[1:]:
        current = current / part
        if not current.exists():
            break
        attributes = current.lstat().st_file_attributes
        if attributes & 0x400:
            raise PrivateQwenRuntimeError(f"{field} contains a reparse point")


def _plain_file(path: Path, field: str) -> Path:
    _reject_reparse(path, field)
    value = path.resolve(strict=True)
    if not value.is_file() or int(value.stat().st_nlink) != 1:
        raise PrivateQwenRuntimeError(f"{field} is not a plain file")
    return value


def _plain_dir(path: Path, field: str) -> Path:
    _reject_reparse(path, field)
    value = path.resolve(strict=True)
    if not value.is_dir():
        raise PrivateQwenRuntimeError(f"{field} is not a directory")
    return value


def _runtime_tree(root: Path) -> dict[str, Any]:
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "PRIVATE_RUNTIME_COMPONENT.json":
            continue
        if not path.is_file() or int(path.stat().st_nlink) != 1:
            raise PrivateQwenRuntimeError(f"runtime contains non-plain file: {relative}")
        records.append({"path": relative, "bytes": path.stat().st_size, "sha256": _sha(path)})
    digest = hashlib.sha256(b"".join(f"{item['path']}\0{item['bytes']}\0{item['sha256']}\n".encode() for item in records)).hexdigest()
    return {"file_count": len(records), "total_bytes": sum(item["bytes"] for item in records), "tree_sha256": digest}


def _registration_path() -> Path:
    raw_path = str(os.getenv(REGISTRATION_ENV) or "").strip()
    if raw_path:
        return Path(raw_path)
    catalog_path = str(os.getenv("MENTORFLOW_VOICE_PACK_CATALOG") or "").strip()
    if catalog_path:
        return Path(catalog_path).resolve(strict=False).parent / "private-runtime-registration.json"
    pack_root = str(os.getenv("MENTORFLOW_VOICE_PACK_ROOT") or "").strip()
    if pack_root:
        return Path(pack_root).resolve(strict=False) / "private-runtime-registration.json"
    try:
        private_release = resolve_app_private_release()
    except PrivateVoiceReleaseRegistrationError as exc:
        raise PrivateQwenRuntimeError("app private voice release registration is invalid") from exc
    if private_release is not None:
        return private_release["runtime_registration_path"]
    return project_resource_root() / "data" / "voice_packs" / "private-runtime-registration.json"


def probe_registration() -> dict[str, Any]:
    """Passive small-file probe; full runtime tree verification stays click-lazy."""
    path = _plain_file(_registration_path(), "private runtime registration")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateQwenRuntimeError("private runtime registration is invalid") from exc
    expected = {"schema", "enabled", "registration_id", "runtime_root", "component_sha256", "component_signature", "signing_key_id", "public_key", "cache_root", "port"}
    if not isinstance(value, dict) or set(value) != expected or value.get("schema") != REGISTRATION_SCHEMA or value.get("enabled") is not True:
        raise PrivateQwenRuntimeError("private runtime registration fields are invalid")
    runtime_root = _plain_dir(Path(str(value["runtime_root"])), "private runtime component")
    component_path = _plain_file(runtime_root / "PRIVATE_RUNTIME_COMPONENT.json", "private runtime component receipt")
    raw_component = component_path.read_bytes()
    if hashlib.sha256(raw_component).hexdigest() != value.get("component_sha256"):
        raise PrivateQwenRuntimeError("private runtime component receipt hash mismatch")
    try:
        public_key = base64.b64decode(str(value["public_key"]), validate=True)
        signature = base64.b64decode(str(value["component_signature"]), validate=True)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, raw_component)
    except Exception as exc:
        raise PrivateQwenRuntimeError("private runtime component signature is invalid") from exc
    component = json.loads(raw_component.decode("utf-8"))
    if (
        value.get("signing_key_id") != "tsubaki-private-local-prerelease-key"
        or component.get("schema") != COMPONENT_SCHEMA
        or component.get("scope") != "private_local_only_nonexportable"
        or component.get("registration_id") != value.get("registration_id")
    ):
        raise PrivateQwenRuntimeError("private runtime component semantics are invalid")
    return {
        "state": "stopped",
        "starts_model": False,
        "owned": False,
        "verified_scope": "registration_and_signed_component_only",
        "payload_tree_verification": "deferred_until_first_synthesis",
        "idle_ttl_seconds": 300.0,
    }


def load_registration() -> dict[str, Any]:
    global _REGISTRATION_CACHE_KEY, _REGISTRATION_CACHE
    path = _plain_file(_registration_path(), "private runtime registration")
    path_stat = path.stat()
    registration_sha256 = _sha(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = {"schema", "enabled", "registration_id", "runtime_root", "component_sha256", "component_signature", "signing_key_id", "public_key", "cache_root", "port"}
    if not isinstance(value, dict) or set(value) != expected or value["schema"] != REGISTRATION_SCHEMA or value["enabled"] is not True:
        raise PrivateQwenRuntimeError("private runtime registration fields are invalid")
    runtime_root = _plain_dir(Path(str(value["runtime_root"])), "private runtime component")
    component_path = _plain_file(runtime_root / "PRIVATE_RUNTIME_COMPONENT.json", "private runtime component receipt")
    component_stat = component_path.stat()
    component_sha256 = _sha(component_path)
    if component_sha256 != value["component_sha256"]:
        raise PrivateQwenRuntimeError("private runtime component receipt hash mismatch")
    try:
        public_key = base64.b64decode(str(value["public_key"]), validate=True)
        component_signature = base64.b64decode(str(value["component_signature"]), validate=True)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        if len(public_key) != 32 or len(component_signature) != 64:
            raise ValueError("invalid Ed25519 material size")
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            component_signature, component_path.read_bytes()
        )
    except Exception as exc:
        raise PrivateQwenRuntimeError("private runtime component signature is invalid") from exc
    if value["signing_key_id"] != "tsubaki-private-local-prerelease-key":
        raise PrivateQwenRuntimeError("private runtime signing key identity is invalid")
    cache_key = (
        str(path), path_stat.st_size, path_stat.st_mtime_ns, registration_sha256,
        str(runtime_root), component_stat.st_size, component_stat.st_mtime_ns,
        component_sha256,
    )
    with _LOCK:
        if _REGISTRATION_CACHE_KEY == cache_key and _REGISTRATION_CACHE is not None:
            return dict(_REGISTRATION_CACHE)
    component = json.loads(component_path.read_text(encoding="utf-8"))
    required_component = {"schema", "registration_id", "scope", "portable_receipt_path", "portable_receipt_sha256", "python_sha256", "runner_sha256", "base_runner_sha256", "tree"}
    if not isinstance(component, dict) or set(component) != required_component or component["schema"] != COMPONENT_SCHEMA or component["scope"] != "private_local_only_nonexportable" or component["registration_id"] != value["registration_id"]:
        raise PrivateQwenRuntimeError("private runtime component semantics are invalid")
    for relative, field in ((PYTHON, "python_sha256"), (RUNNER, "runner_sha256"), (BASE_RUNNER, "base_runner_sha256")):
        if _sha(_plain_file(runtime_root.joinpath(*relative.split("/")), relative)) != component[field]:
            raise PrivateQwenRuntimeError(f"private runtime component digest mismatch: {relative}")
    portable_relative = str(component["portable_receipt_path"])
    if portable_relative != "metadata/PRIVATE_PORTABLE_RUNTIME_RECEIPT.json":
        raise PrivateQwenRuntimeError("private portable receipt path is invalid")
    portable_path = _plain_file(runtime_root.joinpath(*portable_relative.split("/")), "private portable runtime receipt")
    if _sha(portable_path) != component["portable_receipt_sha256"]:
        raise PrivateQwenRuntimeError("private portable receipt digest mismatch")
    portable = json.loads(portable_path.read_text(encoding="utf-8"))
    expected_portable = {
        "schema", "runtime_kind", "python_version", "python_sha256",
        "dependency_lock_sha256", "overlay_contract_sha256", "offline_only",
    }
    if (
        not isinstance(portable, dict)
        or set(portable) != expected_portable
        or portable["schema"] != PORTABLE_RECEIPT_SCHEMA
        or portable["runtime_kind"] != "qwen3-tts-12hz-portable-private"
        or portable["python_version"] != "3.12.10"
        or portable["offline_only"] is not True
        or portable["python_sha256"] != component["python_sha256"]
        or _sha(_plain_file(runtime_root.joinpath(*DEPENDENCY_LOCK.split("/")), DEPENDENCY_LOCK)) != portable["dependency_lock_sha256"]
        or _sha(_plain_file(runtime_root.joinpath(*OVERLAY_CONTRACT.split("/")), OVERLAY_CONTRACT)) != portable["overlay_contract_sha256"]
    ):
        raise PrivateQwenRuntimeError("private portable runtime receipt semantics are invalid")
    if _runtime_tree(runtime_root) != component["tree"]:
        raise PrivateQwenRuntimeError("private runtime component full tree mismatch")
    cache_root = Path(str(value["cache_root"])).resolve(strict=False)
    if cache_root.parent == cache_root or cache_root.is_relative_to(runtime_root) or runtime_root.is_relative_to(cache_root):
        raise PrivateQwenRuntimeError("private runtime cache root is invalid")
    port = value["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise PrivateQwenRuntimeError("private runtime port is invalid")
    result = {**value, "runtime_root": str(runtime_root), "cache_root": str(cache_root), "component": component}
    with _LOCK:
        _REGISTRATION_CACHE_KEY = cache_key
        _REGISTRATION_CACHE = dict(result)
    return result


class PrivateCustomLoopbackRuntime:
    def __init__(self, supervisor: LocalVoiceRuntimeSupervisor) -> None:
        self.supervisor = supervisor
        self.transport = StrictLoopbackJsonTransport(host=supervisor.plan.host, port=supervisor.plan.port)
        self.singleflight = asyncio.Lock()

    async def _post(self, path: str, payload: Mapping[str, Any], *, timeout: float, maximum: int) -> Mapping[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        async with self.supervisor.synthesis_lease_async():
            headers = self.supervisor.authorize_loopback_request(method="POST", path=path, content_length=len(body), body_sha256=hashlib.sha256(body).hexdigest())
            return await asyncio.to_thread(self.transport.post_json, path, body, headers=headers, max_request_bytes=256 * 1024, max_response_bytes=maximum, timeout_seconds=timeout)

    async def prepare(self, binding: Mapping[str, Any]) -> Mapping[str, Any]:
        async with self.singleflight:
            return await self._post("/v1/prepare", {"schema": "mentorflow.qwen-custom-private-prepare.v1", "binding": dict(binding)}, timeout=30.0, maximum=128 * 1024)

    async def synthesize(self, binding: Mapping[str, Any], *, text: str, style: str, reply_kind: str) -> tuple[bytes, Mapping[str, Any]]:
        async with self.singleflight:
            response = await self._post("/v1/synthesize", {"schema": "mentorflow.qwen-custom-private-synthesis.v1", "binding": dict(binding), "text": text, "style": style, "reply_kind": reply_kind}, timeout=300.0, maximum=16 * 1024 * 1024)
        if set(response) != {"schema", "receipt", "audio_base64"} or response["schema"] != "mentorflow.qwen-custom-private-synthesis.v1":
            raise PrivateQwenRuntimeError("private runtime response schema invalid")
        receipt = response["receipt"]
        audio = base64.b64decode(str(response["audio_base64"]).encode("ascii"), validate=True)
        if not isinstance(receipt, Mapping) or receipt.get("audio_sha256") != hashlib.sha256(audio).hexdigest() or receipt.get("loaded_model_sha256") != binding["model_sha256"]:
            raise PrivateQwenRuntimeError("private runtime audio/model binding mismatch")
        return audio, receipt


def binding_for(pack: Mapping[str, Any], installed_root: Path, manifest_sha256: str) -> dict[str, Any]:
    model_item = next(item for item in pack["files"] if item["path"] == "model/model.safetensors")
    return {
        "pack_id": pack["pack_id"], "pack_version": pack["version"],
        "pack_manifest_sha256": manifest_sha256,
        "model_root": str((installed_root / "model").resolve(strict=True)),
        "model_sha256": model_item["sha256"], "speaker": "tsubaki_teacher", "speaker_slot": 3000,
        "scope": "private_local_only_nonexportable",
    }


def get_or_create_runtime(pack: Mapping[str, Any], installed_root: Path, manifest_sha256: str) -> tuple[PrivateCustomLoopbackRuntime, dict[str, Any]]:
    global _RUNTIME, _BINDING
    binding = binding_for(pack, installed_root, manifest_sha256)
    identity = (str(pack["pack_id"]), str(pack["version"]), manifest_sha256, binding["model_sha256"])
    with _LOCK:
        if _RUNTIME is not None and _BINDING == identity:
            return _RUNTIME, binding
        if _RUNTIME is not None and _BINDING != identity:
            _RUNTIME.supervisor.shutdown()
            _RUNTIME = None
            _BINDING = None
        if _RUNTIME is None:
            registration = load_registration()
            root = Path(registration["runtime_root"])
            cache = Path(registration["cache_root"])
            environment = {
                "MENTORFLOW_VOICE_RUNTIME_ROOT": str(root), "MENTORFLOW_VOICE_HOST": "127.0.0.1",
                "MENTORFLOW_VOICE_PORT": str(registration["port"]), "MENTORFLOW_VOICE_CACHE_ROOT": str(cache),
                "MENTORFLOW_VOICE_DEVICE": "cuda:0", "MENTORFLOW_VOICE_MIN_NVIDIA_DRIVER": "555.00",
                "MENTORFLOW_PRIVATE_MODEL_ROOT": binding["model_root"], "MENTORFLOW_PRIVATE_MODEL_SHA256": binding["model_sha256"],
                "HF_HOME": str(cache / "huggingface"), "HF_HUB_CACHE": str(cache / "huggingface" / "hub"),
                "TORCH_HOME": str(cache / "torch"), "XDG_CACHE_HOME": str(cache), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "CUDA_VISIBLE_DEVICES": "0", "NUMBA_THREADING_LAYER": "workqueue",
            }
            entry = RuntimeCatalogEntry(catalog_id=CATALOG_ID, runtime_id=RUNTIME_ID, service_id=SERVICE_ID, executable_relative=PYTHON, arguments=("-I", "-B", RUNNER), working_directory_relative=".", environment=environment, executable_sha256=registration["component"]["python_sha256"], required_file_sha256={RUNNER: registration["component"]["runner_sha256"], BASE_RUNNER: registration["component"]["base_runner_sha256"]})
            catalog = TrustedRuntimeCatalog([entry])
            plan = catalog.issue_plan(CATALOG_ID, runtime_root=root, port=registration["port"], startup_timeout_seconds=180.0, probe_timeout_seconds=1.5, idle_ttl_seconds=300.0, reaper_interval_seconds=5.0)
            supervisor = create_production_local_voice_runtime_supervisor(plan, catalog=catalog)
            _RUNTIME = PrivateCustomLoopbackRuntime(supervisor)
            _BINDING = identity
        return _RUNTIME, binding


def runtime_status() -> dict[str, Any]:
    with _LOCK:
        if _RUNTIME is not None:
            return _RUNTIME.supervisor.status()
    try:
        return probe_registration()
    except Exception as exc:
        return {"state": "unavailable", "starts_model": False, "reason": type(exc).__name__}


def shutdown_private_qwen_runtime() -> dict[str, Any]:
    global _RUNTIME, _BINDING
    with _LOCK:
        runtime = _RUNTIME
        _RUNTIME = None
        _BINDING = None
    return runtime.supervisor.shutdown() if runtime is not None else {"state": "stopped", "owned": False}

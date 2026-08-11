#!/usr/bin/env python3
"""Run the explicit, offline Qwen Base Clone release acceptance contract.

The real backend is deliberately gated behind fully supplied local release
paths and a fixed-root trust chain.  Unit tests inject a fake backend and tiny
PCM WAVs; importing this module never builds a pack, loads Qwen, touches CUDA,
or starts a listener.

Only the exact runtime supervisor created by this harness may be stopped.  No
PID-, process-name-, or port-based termination is implemented here.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import io
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import wave
import zipfile
from contextlib import AbstractContextManager
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for _path in (PROJECT_ROOT, BACKEND_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from scripts import build_qwen3_tts_runtime_pack as pack_builder
from backend.app.immutable_evidence import ImmutableEvidenceError, immutable_write


REPORT_SCHEMA = "mentorflow.qwen-base-clone-real-e2e-report.v1"
AUDIO_SCOPE_SCHEMA = "mentorflow.qwen-base-clone-e2e-audio-scope.v1"
PROTECTED_PORTS = (8000, 8892, 9880)
RANDOM_PORT_MINIMUM = 49152
RANDOM_PORT_MAXIMUM = 65535


class HarnessError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class E2EConfig:
    engine_build_spec: Path
    engine_output_root: Path
    engine_pack_root: Path
    engine_archive: Path
    engine_catalog: Path
    package_id: str
    version: str
    public_key: Path
    trust_receipt: Path
    trust_policy: Path
    trust_policy_signature: Path
    voice_pack_root: Path
    voice_catalog: Path
    voice_pack_id: str
    voice_pack_version: str
    cache_root: Path
    evidence_root: Path
    port: int
    text: str
    style: str
    reply_kind: str
    test_trust_chain: bool = False
    temporary_test_reference: bool = False

    def validated(self) -> "E2EConfig":
        if self.port in PROTECTED_PORTS or not RANDOM_PORT_MINIMUM <= int(self.port) <= RANDOM_PORT_MAXIMUM:
            raise HarnessError(
                "E2E_PORT_INVALID",
                f"port must be an explicit unused dynamic port in {RANDOM_PORT_MINIMUM}-{RANDOM_PORT_MAXIMUM}",
            )
        for name, value in (
            ("package_id", self.package_id),
            ("version", self.version),
            ("voice_pack_id", self.voice_pack_id),
            ("voice_pack_version", self.voice_pack_version),
        ):
            if not value or len(value) > 80:
                raise HarnessError("E2E_IDENTITY_INVALID", f"{name} is required")
        if not self.text.strip():
            raise HarnessError("E2E_TEXT_INVALID", "synthesis text must be non-empty")
        if self.style not in {"lively", "relaxed"}:
            raise HarnessError("E2E_STYLE_INVALID", "style must be lively or relaxed")
        if self.temporary_test_reference and not self.test_trust_chain:
            raise HarnessError(
                "E2E_TEST_BOUNDARY_INVALID",
                "temporary test references require the explicit internal test trust chain",
            )
        if self.test_trust_chain:
            identities = (
                (self.package_id, self.version),
                (self.voice_pack_id, self.voice_pack_version),
            )
            if any(not item_id.startswith("test-") or "test" not in item_version.casefold() for item_id, item_version in identities):
                raise HarnessError(
                    "E2E_TEST_BOUNDARY_INVALID",
                    "test trust requires test-* package IDs and versions containing 'test'",
                )
        for name, path in (
            ("engine_output_root", self.engine_output_root),
            ("voice_pack_root", self.voice_pack_root),
            ("cache_root", self.cache_root),
            ("evidence_root", self.evidence_root),
        ):
            absolute = Path(os.path.abspath(path))
            if absolute.parent == absolute:
                raise HarnessError("E2E_PATH_INVALID", f"{name} cannot be a filesystem root")
        return self


@dataclass(frozen=True)
class AudioRun:
    audio: bytes
    media_type: str
    profile: str
    cache: str
    scope_receipt: Mapping[str, Any]


class E2EBackend(Protocol):
    def configure(self, config: E2EConfig) -> Mapping[str, Any]: ...

    def prepare(self) -> Mapping[str, Any]: ...

    def synthesize(self, *, text: str, style: str, reply_kind: str) -> AudioRun: ...

    def runtime_status(self) -> Mapping[str, Any]: ...

    def restart_owned(self) -> Mapping[str, Any]: ...

    def cleanup_owned(self) -> Mapping[str, Any]: ...


class PortInspector(Protocol):
    def snapshot(self, ports: tuple[int, ...]) -> Mapping[int, tuple[int, ...]]: ...

    def assert_process_loopback_only(self, pid: int) -> None: ...


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _strict_json_value(value: Any, *, field: str = "value") -> Any:
    """Losslessly project adapter values onto an explicit JSON data model."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "encoding": "base64",
            "bytes": len(raw),
            "sha256": _sha256_bytes(raw),
            "data": base64.b64encode(raw).decode("ascii"),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _strict_json_value(getattr(value, item.name), field=f"{field}.{item.name}")
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise HarnessError("E2E_JSON_CONTRACT_INVALID", f"{field} contains a non-string or empty key")
            result[key] = _strict_json_value(item, field=f"{field}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [
            _strict_json_value(item, field=f"{field}[{index}]")
            for index, item in enumerate(value)
        ]
    raise HarnessError(
        "E2E_JSON_CONTRACT_INVALID",
        f"{field} contains unsupported value type {type(value).__name__}",
    )


def _strict_json_mapping(value: Any, *, field: str) -> dict[str, Any]:
    converted = _strict_json_value(value, field=field)
    if not isinstance(converted, dict):
        raise HarnessError("E2E_JSON_CONTRACT_INVALID", f"{field} must serialize to a JSON object")
    # Prove the result now instead of discovering a dataclass/Path leak at the
    # final e2e-report write.
    json.dumps(converted, ensure_ascii=False, sort_keys=True)
    return converted


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return pack_builder.sha256_file(path)


def _plain_file(path: Path, *, code: str) -> Path:
    path = Path(os.path.abspath(path))
    try:
        pack_builder._ensure_plain_file(path, code=code)
    except pack_builder.BuildError as exc:
        raise HarnessError(exc.code, str(exc)) from exc
    return path


def _read_json(path: Path, *, code: str) -> dict[str, Any]:
    path = _plain_file(path, code=code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError(code, f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HarnessError(code, f"{path} must contain a JSON object")
    return value


def _immutable_write(path: Path, content: bytes) -> None:
    path = Path(os.path.abspath(path))
    if path.parent == path:
        raise HarnessError("E2E_EVIDENCE_PATH_INVALID", "evidence path cannot be a filesystem root")
    try:
        immutable_write(path, content)
    except ImmutableEvidenceError as exc:
        codes = {
            "IMMUTABLE_TARGET_EXISTS": "E2E_EVIDENCE_EXISTS",
            "IMMUTABLE_TARGET_INVALID": "E2E_EVIDENCE_INVALID",
            "IMMUTABLE_REPARSE_FORBIDDEN": "E2E_EVIDENCE_REPARSE",
        }
        raise HarnessError(
            codes.get(exc.code, exc.code),
            str(exc),
            details={
                "temporary_path": str(exc.temporary_path) if exc.temporary_path else None,
            },
        ) from exc


def _validate_pcm_wav(audio: bytes) -> dict[str, Any]:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            channels = int(wav_file.getnchannels())
            sample_width = int(wav_file.getsampwidth())
            sample_rate = int(wav_file.getframerate())
            frame_count = int(wav_file.getnframes())
            compression = wav_file.getcomptype()
            frames = wav_file.readframes(frame_count + 1)
    except (EOFError, OSError, ValueError, wave.Error) as exc:
        raise HarnessError("E2E_AUDIO_INVALID", f"audio is not a complete PCM WAV: {exc}") from exc
    if (
        channels != 1
        or sample_width != 2
        or not 16000 <= sample_rate <= 48000
        or frame_count <= 0
        or compression != "NONE"
        or len(frames) != frame_count * channels * sample_width
    ):
        raise HarnessError("E2E_AUDIO_INVALID", "audio must be complete mono 16-bit PCM WAV at 16-48 kHz")
    return {
        "bytes": len(audio),
        "sha256": _sha256_bytes(audio),
        "channels": channels,
        "sample_width": sample_width,
        "sample_rate": sample_rate,
        "frames": frame_count,
    }


def _loopback_host(host: Any) -> bool:
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


class LoopbackOnlyNetworkGuard(AbstractContextManager["LoopbackOnlyNetworkGuard"]):
    """Block parent-process outbound sockets while allowing numeric loopback.

    The separately launched runner is also forced to Hugging Face/Transformers
    offline mode and ``local_files_only=True`` by the signed runtime stack.
    This guard intentionally makes no firewall claim; conclusive hostile-code
    egress isolation still belongs to the acceptance host/VM boundary.
    """

    def __init__(self) -> None:
        self._original_connect = socket.socket.connect
        self._original_connect_ex = socket.socket.connect_ex
        self._original_create_connection = socket.create_connection

    @staticmethod
    def _check(sock: socket.socket | None, address: Any) -> None:
        if sock is not None and getattr(sock, "family", None) == getattr(socket, "AF_UNIX", object()):
            return
        if not isinstance(address, tuple) or len(address) < 2 or not _loopback_host(address[0]):
            raise HarnessError(
                "E2E_OFFLINE_GUARD_BLOCKED",
                "only numeric loopback socket connections are allowed during the E2E",
            )

    def __enter__(self) -> "LoopbackOnlyNetworkGuard":
        original_connect = self._original_connect
        original_connect_ex = self._original_connect_ex
        original_create = self._original_create_connection

        def guarded_connect(sock: socket.socket, address: Any):  # type: ignore[no-untyped-def]
            self._check(sock, address)
            return original_connect(sock, address)

        def guarded_connect_ex(sock: socket.socket, address: Any):  # type: ignore[no-untyped-def]
            self._check(sock, address)
            return original_connect_ex(sock, address)

        def guarded_create_connection(address: Any, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            self._check(None, address)
            return original_create(address, *args, **kwargs)

        socket.socket.connect = guarded_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
        socket.create_connection = guarded_create_connection  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        socket.socket.connect = self._original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = self._original_connect_ex  # type: ignore[method-assign]
        socket.create_connection = self._original_create_connection  # type: ignore[assignment]
        return None


class WindowsPowerShellPortInspector:
    """Read-only listener/connection ownership checks through PowerShell 7."""

    def __init__(self, executable: str | None = None) -> None:
        selected = executable or shutil.which("pwsh.exe") or shutil.which("pwsh")
        if not selected:
            raise HarnessError("E2E_POWERSHELL7_REQUIRED", "PowerShell 7 is required for PID/port evidence")
        self.executable = selected

    def _run_json(self, script: str) -> Any:
        result = subprocess.run(
            [self.executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if result.returncode != 0:
            raise HarnessError(
                "E2E_PORT_INSPECTION_FAILED",
                "PowerShell port inspection failed",
                details={"returncode": result.returncode, "stderr": result.stderr[-1000:]},
            )
        text = result.stdout.strip()
        return json.loads(text) if text else []

    def snapshot(self, ports: tuple[int, ...]) -> Mapping[int, tuple[int, ...]]:
        safe_ports = tuple(sorted({int(port) for port in ports}))
        if any(not 1 <= port <= 65535 for port in safe_ports):
            raise HarnessError("E2E_PORT_INVALID", "port snapshot contains an invalid port")
        rendered = ",".join(str(port) for port in safe_ports)
        script = (
            f"$ports=@({rendered}); "
            "$rows=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
            "Where-Object { $ports -contains [int]$_.LocalPort } | "
            "Select-Object @{n='port';e={[int]$_.LocalPort}},@{n='pid';e={[int]$_.OwningProcess}}); "
            "$rows | ConvertTo-Json -Compress"
        )
        raw = self._run_json(script)
        rows = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        grouped = {port: set() for port in safe_ports}
        for row in rows:
            if isinstance(row, Mapping) and int(row.get("port", 0)) in grouped:
                grouped[int(row["port"])].add(int(row.get("pid", 0)))
        return {port: tuple(sorted(pid for pid in pids if pid > 0)) for port, pids in grouped.items()}

    def assert_process_loopback_only(self, pid: int) -> None:
        if int(pid) <= 0:
            raise HarnessError("E2E_RUNTIME_PID_INVALID", "runtime PID is missing")
        script = (
            f"$rows=@(Get-NetTCPConnection -OwningProcess {int(pid)} -ErrorAction SilentlyContinue | "
            "Where-Object { $_.State -notin @('Listen','Bound','Closed') } | "
            "Select-Object State,RemoteAddress,RemotePort); $rows | ConvertTo-Json -Compress"
        )
        raw = self._run_json(script)
        rows = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        forbidden = []
        for row in rows:
            remote = str(row.get("RemoteAddress") or "") if isinstance(row, Mapping) else ""
            if remote in {"", "0.0.0.0", "::", "*"}:
                continue
            try:
                allowed = ipaddress.ip_address(remote.split("%", 1)[0]).is_loopback
            except ValueError:
                allowed = False
            if not allowed:
                forbidden.append(dict(row))
        if forbidden:
            raise HarnessError(
                "E2E_NON_LOOPBACK_CONNECTION",
                "runtime child has a non-loopback connection",
                details={"connections": forbidden},
            )


def _verify_manifest_trust(
    config: E2EConfig,
    *,
    manifest_name: str,
    manifest_value: Mapping[str, Any],
):
    from app.release_trust_policy import ReleaseTrustError, verify_release_trust_chain

    try:
        return verify_release_trust_chain(
            manifest_name=manifest_name,
            manifest_value=manifest_value,
            public_key_path=config.public_key,
            trust_receipt_path=config.trust_receipt,
            trust_policy_path=config.trust_policy,
            trust_policy_signature_path=config.trust_policy_signature,
            operation="verify",
            _allow_test_roots=config.test_trust_chain,
        )
    except ReleaseTrustError as exc:
        raise HarnessError(exc.code, str(exc)) from exc


def _decode_catalog_key(value: Any) -> bytes:
    text = str(value or "").strip()
    try:
        raw = bytes.fromhex(text) if text and all(character in "0123456789abcdefABCDEF" for character in text) else base64.b64decode(text, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise HarnessError("E2E_VOICE_CATALOG_INVALID", "voice catalog public key is invalid") from exc
    if len(raw) != 32:
        raise HarnessError("E2E_VOICE_CATALOG_INVALID", "voice catalog public key must be Ed25519 raw bytes")
    return raw


class RealQwenE2EBackend:
    """The real release/build/install/register/runtime adapter implementation."""

    _VOICE_ENVIRONMENT = (
        "MENTORFLOW_VOICE_PACK_ROOT",
        "MENTORFLOW_VOICE_PACK_CATALOG",
        "MENTORFLOW_VOICE_PACK_PUBLIC_KEYS",
        "MENTORFLOW_VOICE_PACK_HTTPS_ALLOWED_HOSTS",
    )

    def __init__(self) -> None:
        self.config: E2EConfig | None = None
        self.runtime = None
        self.capability = None
        self._active: tuple[dict[str, Any], Path, str] | None = None
        self._saved_environment: dict[str, str | None] = {}
        self._release_scope: dict[str, Any] | None = None

    def _set_voice_environment(self, config: E2EConfig) -> None:
        if self._saved_environment:
            raise HarnessError("E2E_BACKEND_STATE_INVALID", "voice environment was already configured")
        self._saved_environment = {key: os.environ.get(key) for key in self._VOICE_ENVIRONMENT}
        os.environ["MENTORFLOW_VOICE_PACK_ROOT"] = str(Path(os.path.abspath(config.voice_pack_root)))
        os.environ["MENTORFLOW_VOICE_PACK_CATALOG"] = str(Path(os.path.abspath(config.voice_catalog)))
        os.environ.pop("MENTORFLOW_VOICE_PACK_PUBLIC_KEYS", None)
        os.environ.pop("MENTORFLOW_VOICE_PACK_HTTPS_ALLOWED_HOSTS", None)

    def _restore_voice_environment(self) -> None:
        for key, value in self._saved_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._saved_environment.clear()

    @staticmethod
    def _voice_manifest_and_key(
        config: E2EConfig, engine_trust: Any
    ) -> tuple[dict[str, Any], Any, dict[str, Any]]:
        catalog = _read_json(config.voice_catalog, code="E2E_VOICE_CATALOG_INVALID")
        entries = catalog.get("packs")
        if not isinstance(entries, list):
            raise HarnessError("E2E_VOICE_CATALOG_INVALID", "voice catalog packs must be a list")
        matches = [
            item
            for item in entries
            if isinstance(item, Mapping)
            and item.get("pack_id") == config.voice_pack_id
            and item.get("version") == config.voice_pack_version
        ]
        if len(matches) != 1:
            raise HarnessError("E2E_VOICE_PACK_NOT_FOUND", "voice pack is not uniquely present in the catalog")
        entry = dict(matches[0])
        source = entry.get("source")
        if not isinstance(source, Mapping) or source.get("kind") != "bundled":
            raise HarnessError("E2E_OFFLINE_SOURCE_REQUIRED", "real E2E accepts only bundled voice archives")
        raw_relative = str(source.get("archive_path") or "")
        try:
            relative = pack_builder._safe_relative(raw_relative, field="voice_catalog.source.archive_path")
        except pack_builder.BuildError as exc:
            raise HarnessError(exc.code, str(exc)) from exc
        pure = PurePosixPath(relative)
        if not pure.parts or pure.parts[0] != "bundled":
            raise HarnessError("E2E_OFFLINE_SOURCE_REQUIRED", "bundled archive must stay under catalog/bundled")
        archive_path = config.voice_catalog.parent.joinpath(*pure.parts)
        _plain_file(archive_path, code="E2E_VOICE_ARCHIVE_MISSING")
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                info = archive.getinfo("voice-pack.json")
                if info.file_size <= 0 or info.file_size > 1024 * 1024:
                    raise HarnessError("E2E_VOICE_ARCHIVE_INVALID", "voice manifest size is invalid")
                manifest = json.loads(archive.read(info).decode("utf-8"))
        except (OSError, KeyError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            raise HarnessError("E2E_VOICE_ARCHIVE_INVALID", f"cannot read bundled voice manifest: {exc}") from exc
        if not isinstance(manifest, dict):
            raise HarnessError("E2E_VOICE_ARCHIVE_INVALID", "voice manifest must be an object")
        voice_trust = _verify_manifest_trust(config, manifest_name="voice-pack.json", manifest_value=manifest)
        if (
            voice_trust.leaf_key_id != engine_trust.leaf_key_id
            or voice_trust.root_key_id != engine_trust.root_key_id
            or voice_trust.policy_sha256 != engine_trust.policy_sha256
        ):
            raise HarnessError("E2E_TRUST_CHAIN_MISMATCH", "engine and voice releases do not share the fixed trust chain")
        signing_key_id = str(entry.get("signing_key_id") or "")
        keys = catalog.get("trusted_keys")
        key_matches = [
            item for item in keys or [] if isinstance(item, Mapping) and item.get("key_id") == signing_key_id
        ]
        if len(key_matches) != 1 or signing_key_id != voice_trust.leaf_key_id:
            raise HarnessError("E2E_VOICE_CATALOG_INVALID", "voice catalog signing key is not the verified leaf")
        if _decode_catalog_key(key_matches[0].get("public_key")) != voice_trust.raw_public_key:
            raise HarnessError("E2E_VOICE_CATALOG_INVALID", "voice catalog public key differs from fixed trust")
        return manifest, voice_trust, {
            "catalog": {
                "path": str(config.voice_catalog.absolute()),
                "bytes": config.voice_catalog.stat().st_size,
                "sha256": _sha256_file(config.voice_catalog),
            },
            "archive": {
                "path": str(archive_path.absolute()),
                "bytes": archive_path.stat().st_size,
                "sha256": _sha256_file(archive_path),
            },
        }

    def configure(self, config: E2EConfig) -> Mapping[str, Any]:
        from app import qwen_base_clone_runtime as runtime_bridge
        from app import teacher_voice_pack as voice_pack

        self.config = config
        plan = pack_builder.plan_from_spec(config.engine_build_spec)
        if plan["pack_id"] != config.package_id or plan["version"] != config.version:
            raise HarnessError("E2E_ENGINE_IDENTITY_MISMATCH", "build spec identity differs from CLI identity")
        build = pack_builder.build_from_plan(plan, config.engine_output_root)
        actual_paths = {
            "pack_root": Path(build["pack_root"]).absolute(),
            "archive_path": Path(build["archive_path"]).absolute(),
            "catalog_path": Path(build["catalog_path"]).absolute(),
        }
        expected_paths = {
            "pack_root": Path(config.engine_pack_root).absolute(),
            "archive_path": Path(config.engine_archive).absolute(),
            "catalog_path": Path(config.engine_catalog).absolute(),
        }
        if actual_paths != expected_paths:
            raise HarnessError(
                "E2E_ENGINE_OUTPUT_PATH_MISMATCH",
                "builder outputs differ from the explicitly supplied install paths",
                details={
                    "actual": {key: str(value) for key, value in actual_paths.items()},
                    "expected": {key: str(value) for key, value in expected_paths.items()},
                },
            )
        catalog = _read_json(config.engine_catalog, code="E2E_ENGINE_CATALOG_INVALID")
        manifest = _read_json(config.engine_pack_root / "engine-manifest.json", code="E2E_ENGINE_MANIFEST_INVALID")
        engine_trust = _verify_manifest_trust(
            config,
            manifest_name="engine-manifest.json",
            manifest_value=manifest,
        )
        public_key = pack_builder._load_public_key(_plain_file(config.public_key, code="E2E_PUBLIC_KEY_MISSING"))
        builder_verify = pack_builder.verify_catalog_release(
            catalog_path=config.engine_catalog,
            package_id=config.package_id,
            version=config.version,
            archive_path=config.engine_archive,
            pack_root=config.engine_pack_root,
            public_key=public_key,
            public_key_sha256=engine_trust.leaf_public_key_sha256,
        )
        catalog_sha256 = _sha256_file(config.engine_catalog)
        self.capability = voice_pack.verify_qwen_base_clone_engine_release(
            catalog=catalog,
            package_id=config.package_id,
            version=config.version,
            archive_path=config.engine_archive,
            package_root=config.engine_pack_root,
            release_trust=engine_trust,
            catalog_sha256=catalog_sha256,
            catalog_bytes=config.engine_catalog.stat().st_size,
            catalog_path=config.engine_catalog,
        )
        _voice_manifest, voice_trust, voice_release = self._voice_manifest_and_key(config, engine_trust)
        self.runtime = runtime_bridge.create_and_register_production_qwen_base_clone_runtime(
            capability=self.capability,
            port=config.port,
            cache_root=config.cache_root,
            _allow_test_trust=config.test_trust_chain,
        )
        self._set_voice_environment(config)
        install = voice_pack.install_voice_pack(config.voice_pack_id, config.voice_pack_version)
        activate = voice_pack.activate_voice_pack(config.voice_pack_id, config.voice_pack_version)
        self._active = voice_pack._active_manifest(verify_files=True)
        if self._active is None:
            raise HarnessError("E2E_VOICE_ACTIVATION_FAILED", "voice pack did not become active")
        _active_manifest, active_root, active_manifest_sha256 = self._active
        self._release_scope = {
            "engine": {
                "package_id": config.package_id,
                "version": config.version,
                "manifest": {
                    "path": str((config.engine_pack_root / "engine-manifest.json").absolute()),
                    "bytes": (config.engine_pack_root / "engine-manifest.json").stat().st_size,
                    "sha256": _sha256_file(config.engine_pack_root / "engine-manifest.json"),
                },
                "catalog": {
                    "path": str(config.engine_catalog.absolute()),
                    "bytes": config.engine_catalog.stat().st_size,
                    "sha256": catalog_sha256,
                },
                "archive": {
                    "path": str(config.engine_archive.absolute()),
                    "bytes": config.engine_archive.stat().st_size,
                    "sha256": _sha256_file(config.engine_archive),
                },
            },
            "voice": {
                "pack_id": config.voice_pack_id,
                "version": config.voice_pack_version,
                "manifest": {
                    "path": str((active_root / "voice-pack.json").absolute()),
                    "bytes": (active_root / "voice-pack.json").stat().st_size,
                    "sha256": active_manifest_sha256,
                },
                "catalog": voice_release["catalog"],
                "archive": voice_release["archive"],
            },
        }
        return {
            "build": {
                "archive_sha256": build["archive_sha256"],
                "manifest_sha256": build["manifest_sha256"],
                "builder_verify": builder_verify["status"],
            },
            "trust": {
                "test_only": engine_trust.test_only,
                "root_key_id": engine_trust.root_key_id,
                "policy_sha256": engine_trust.policy_sha256,
                "engine_leaf_key_id": engine_trust.leaf_key_id,
                "voice_leaf_key_id": voice_trust.leaf_key_id,
            },
            "install": {"status": install["job"]["status"]},
            "activate": {"status": activate["job"]["status"]},
            "register": {"starts_model": False, "port": config.port},
        }

    def _adapter_context(self):
        from app import teacher_voice_pack as voice_pack

        if self._active is None:
            raise HarnessError("E2E_BACKEND_STATE_INVALID", "voice pack is not active")
        manifest, installed_root, manifest_sha256 = self._active
        adapter = voice_pack._RUNTIME_ADAPTERS[voice_pack.QWEN_BASE_CLONE_ADAPTER_ID]
        return voice_pack, adapter, manifest, installed_root, manifest_sha256

    def prepare(self) -> Mapping[str, Any]:
        voice_pack, adapter, manifest, installed_root, manifest_sha256 = self._adapter_context()
        value = asyncio.run(
            adapter.prepare(
                manifest,
                installed_root=installed_root,
                manifest_sha256=manifest_sha256,
            )
        )
        result = _strict_json_mapping(value, field="real_backend.prepare")
        if (
            set(result)
            != {"schema", "prepared", "cached", "binding", "request", "runtime_receipt"}
            or result.get("schema") != voice_pack.QWEN_BASE_CLONE_PREPARE_RESULT_SCHEMA
            or result.get("prepared") is not True
        ):
            raise HarnessError("E2E_PREPARE_CONTRACT_INVALID", "real adapter prepare result schema is not exact")
        return result

    def synthesize(self, *, text: str, style: str, reply_kind: str) -> AudioRun:
        voice_pack, adapter, manifest, installed_root, _manifest_sha256 = self._adapter_context()
        result = asyncio.run(
            adapter.synthesize(
                manifest,
                text=text,
                style=style,
                reply_kind=reply_kind,
                installed_root=installed_root,
            )
        )
        if self._release_scope is None:
            raise HarnessError("E2E_BACKEND_STATE_INVALID", "release scope was not configured")
        adapter_scope = _strict_json_mapping(result.scope_receipt, field="real_backend.audio_scope")
        if adapter_scope.get("schema") != voice_pack.QWEN_BASE_CLONE_AUDIO_SCOPE_SCHEMA:
            raise HarnessError("E2E_AUDIO_SCOPE_INVALID", "real adapter omitted its exact audio scope receipt")
        return AudioRun(
            bytes(result.audio),
            str(result.media_type),
            str(result.profile),
            str(result.cache),
            {
                "schema": AUDIO_SCOPE_SCHEMA,
                "release": copy.deepcopy(self._release_scope),
                "adapter": adapter_scope,
            },
        )

    def runtime_status(self) -> Mapping[str, Any]:
        if self.runtime is None:
            return {"owned": False, "owned_pid": None, "generation": None}
        return self.runtime.supervisor.status()

    def restart_owned(self) -> Mapping[str, Any]:
        if self.runtime is None:
            raise HarnessError("E2E_BACKEND_STATE_INVALID", "runtime was not registered")
        return self.runtime.supervisor.stop_owned(reason="e2e_restart")

    def cleanup_owned(self) -> Mapping[str, Any]:
        failures: list[dict[str, str]] = []
        stopped: list[Mapping[str, Any]] = []
        supervisor_stopped = self.runtime is None
        try:
            if self.runtime is not None:
                from app import qwen_base_clone_runtime as runtime_bridge
                from app import teacher_voice_pack as voice_pack

                try:
                    stopped.append(self.runtime.supervisor.shutdown())
                    supervisor_stopped = True
                except BaseException as exc:
                    failures.append({"phase": "supervisor", "error": f"{type(exc).__name__}:{exc}"})
                if supervisor_stopped:
                    try:
                        if self.capability is not None:
                            voice_pack.unregister_qwen_base_clone_runtime(
                                capability=self.capability,
                                runtime=self.runtime,
                            )
                    except BaseException as exc:
                        failures.append({"phase": "registry", "error": f"{type(exc).__name__}:{exc}"})
                    try:
                        with runtime_bridge._PRODUCTION_RUNTIME_LOCK:
                            if self.runtime in runtime_bridge._PRODUCTION_RUNTIMES:
                                runtime_bridge._PRODUCTION_RUNTIMES.remove(self.runtime)
                    except BaseException as exc:
                        failures.append({"phase": "owner-list", "error": f"{type(exc).__name__}:{exc}"})
        finally:
            self._restore_voice_environment()
        if supervisor_stopped:
            self.runtime = None
        return {
            "schema": "mentorflow.qwen-e2e-owned-cleanup.v1",
            "owned_only": True,
            "stopped": stopped,
            "failures": failures,
        }


def _owned_runtime_evidence(
    backend: E2EBackend,
    inspector: PortInspector,
    config: E2EConfig,
) -> dict[str, Any]:
    status = dict(backend.runtime_status())
    pid = status.get("owned_pid")
    generation = status.get("generation")
    if status.get("owned") is not True or not isinstance(pid, int) or pid <= 0 or not generation:
        raise HarnessError("E2E_RUNTIME_OWNERSHIP_MISSING", "runtime is not owned after prepare")
    listeners = inspector.snapshot((config.port,)).get(config.port, ())
    if tuple(listeners) != (pid,):
        raise HarnessError(
            "E2E_RUNTIME_LISTENER_MISMATCH",
            "selected port is not owned exclusively by the supervised child",
            details={"expected_pid": pid, "listener_pids": list(listeners)},
        )
    inspector.assert_process_loopback_only(pid)
    return {
        "owned_pid": pid,
        "generation": generation,
        "state": status.get("state"),
        "origin": status.get("origin"),
    }


def _validate_release_evidence_pin(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"path", "bytes", "sha256"}:
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{field} evidence fields are not exact")
    path = _plain_file(Path(str(value["path"])), code="E2E_AUDIO_SCOPE_INVALID")
    if path.stat().st_size != value["bytes"] or _sha256_file(path) != value["sha256"]:
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{field} evidence bytes/hash changed")
    return dict(value)


def _complete_audio_scope_receipt(
    *,
    config: E2EConfig,
    phase: str,
    run: AudioRun,
    audio: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    scope = _strict_json_mapping(run.scope_receipt, field=f"audio.{phase}.scope")
    if set(scope) != {"schema", "release", "adapter"} or scope.get("schema") != AUDIO_SCOPE_SCHEMA:
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{phase} scope envelope is not exact")
    release = scope.get("release")
    if not isinstance(release, dict) or set(release) != {"engine", "voice"}:
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{phase} release scope is missing")
    identities = {
        "engine": ("package_id", config.package_id, "version", config.version),
        "voice": ("pack_id", config.voice_pack_id, "version", config.voice_pack_version),
    }
    normalized_release: dict[str, Any] = {}
    for kind, identity in identities.items():
        value = release.get(kind)
        id_field, expected_id, version_field, expected_version = identity
        if (
            not isinstance(value, dict)
            or set(value) != {id_field, version_field, "manifest", "catalog", "archive"}
            or value.get(id_field) != expected_id
            or value.get(version_field) != expected_version
        ):
            raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{phase} {kind} release identity is invalid")
        normalized_release[kind] = {
            id_field: expected_id,
            version_field: expected_version,
            "manifest": _validate_release_evidence_pin(value["manifest"], field=f"{phase}.{kind}.manifest"),
            "catalog": _validate_release_evidence_pin(value["catalog"], field=f"{phase}.{kind}.catalog"),
            "archive": _validate_release_evidence_pin(value["archive"], field=f"{phase}.{kind}.archive"),
        }

    adapter = scope.get("adapter")
    if (
        not isinstance(adapter, dict)
        or set(adapter) != {"schema", "binding", "request", "runtime_result", "audio"}
        or adapter.get("schema") != "mentorflow.qwen-base-clone-audio-scope.v1"
    ):
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{phase} adapter scope is not exact")
    binding = adapter.get("binding")
    request = adapter.get("request")
    result = adapter.get("runtime_result")
    adapter_audio = adapter.get("audio")
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {"schema", "pack", "engine", "references", "references_sha256", "binding_sha256"}
        or binding.get("schema") != "mentorflow.qwen-base-clone-pack-binding.v1"
        or not isinstance(request, dict)
        or set(request)
        != {
            "schema",
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
            "reference",
            "reference_audio_sha256",
            "reference_transcript_sha256",
            "reply_kind",
            "request_sha256",
        }
        or not isinstance(result, dict)
        or not isinstance(adapter_audio, dict)
    ):
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{phase} binding/request/result scope is incomplete")
    expected_result_fields = {
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
    if set(result) != expected_result_fields or set(adapter_audio) != {
        "bytes", "sha256", "media_type", "profile", "cache"
    }:
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{phase} runtime/audio result fields are not exact")
    pack = binding.get("pack")
    engine = binding.get("engine")
    references = binding.get("references")
    reference = request.get("reference")
    if (
        not isinstance(pack, dict)
        or set(pack) != {"pack_id", "version", "manifest_sha256"}
        or not isinstance(engine, dict)
        or set(engine) != {"kind", "catalog_id", "version", "manifest_sha256"}
        or not isinstance(references, list)
        or not references
        or not isinstance(reference, dict)
        or set(reference)
        != {
            "style",
            "relative_path",
            "audio_path",
            "audio_bytes",
            "audio_sha256",
            "transcript",
            "transcript_sha256",
            "language",
        }
    ):
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{phase} binding/reference fields are invalid")
    matching_references = [item for item in references if isinstance(item, dict) and item == reference]
    raw_audio = bytes(run.audio)
    audio_sha256 = _sha256_bytes(raw_audio)
    text_sha256 = _sha256_bytes(config.text.encode("utf-8"))
    if (
        pack.get("pack_id") != config.voice_pack_id
        or pack.get("version") != config.voice_pack_version
        or pack.get("manifest_sha256") != normalized_release["voice"]["manifest"]["sha256"]
        or engine.get("catalog_id") != config.package_id
        or engine.get("version") != config.version
        or engine.get("manifest_sha256") != normalized_release["engine"]["manifest"]["sha256"]
        or request.get("binding_sha256") != binding.get("binding_sha256")
        or request.get("text") != config.text
        or request.get("text_sha256") != text_sha256
        or request.get("style") != config.style
        or request.get("reply_kind") != config.reply_kind
        or len(matching_references) != 1
        or request.get("reference_audio_sha256") != reference.get("audio_sha256")
        or request.get("reference_transcript_sha256") != reference.get("transcript_sha256")
        or any(result.get(field) != request.get(field) for field in (
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
            "reply_kind",
            "request_sha256",
        ))
        or result.get("request_text_sha256") != text_sha256
        or result.get("audio_sha256") != audio_sha256
        or result.get("audio_bytes") != len(raw_audio)
        or result.get("profile") != run.profile
        or result.get("cache") != run.cache
        or adapter_audio
        != {
            "bytes": len(raw_audio),
            "sha256": audio_sha256,
            "media_type": "audio/wav",
            "profile": run.profile,
            "cache": run.cache,
        }
        or audio.get("sha256") != audio_sha256
        or audio.get("bytes") != len(raw_audio)
    ):
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"{phase} scope does not bind the complete release/request/audio")
    return {
        "schema": AUDIO_SCOPE_SCHEMA,
        "phase": phase,
        "release": normalized_release,
        "binding": binding,
        "request": request,
        "runtime_result": result,
        "runtime": dict(runtime),
        "profile": run.profile,
        "cache": run.cache,
        "audio": {
            **dict(audio),
            "media_type": "audio/wav",
        },
    }


def _persist_audio_scope(
    *,
    config: E2EConfig,
    phase: str,
    run: AudioRun,
    audio: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    value = _complete_audio_scope_receipt(
        config=config,
        phase=phase,
        run=run,
        audio=audio,
        runtime=runtime,
    )
    content = _json_bytes(value)
    path = config.evidence_root / f"{phase}.scope.json"
    _immutable_write(path, content)
    return {
        "path": str(path.absolute()),
        "bytes": len(content),
        "sha256": _sha256_bytes(content),
        "schema": AUDIO_SCOPE_SCHEMA,
        "phase": phase,
        "runtime_generation": runtime.get("generation"),
        "audio_sha256": audio.get("sha256"),
    }


def _validate_report_scope_pins(report: Mapping[str, Any]) -> None:
    audio = report.get("audio")
    if not isinstance(audio, Mapping):
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", "report audio section is invalid")
    if report.get("status") == "passed" and set(audio) != {"cold", "restarted", "repeat"}:
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", "passed report requires all three audio scope receipts")
    generations: dict[str, Any] = {}
    for phase, value in audio.items():
        if not isinstance(value, Mapping):
            raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"report audio.{phase} is invalid")
        declaration = value.get("scope_receipt")
        if not isinstance(declaration, Mapping) or set(declaration) != {
            "path",
            "bytes",
            "sha256",
            "schema",
            "phase",
            "runtime_generation",
            "audio_sha256",
        }:
            raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"report audio.{phase} scope pin is not exact")
        path = _plain_file(Path(str(declaration["path"])), code="E2E_AUDIO_SCOPE_INVALID")
        raw = path.read_bytes()
        try:
            receipt = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"audio.{phase} scope receipt is not JSON") from exc
        if (
            declaration.get("bytes") != len(raw)
            or declaration.get("sha256") != _sha256_bytes(raw)
            or declaration.get("schema") != AUDIO_SCOPE_SCHEMA
            or declaration.get("phase") != phase
            or declaration.get("runtime_generation") != value.get("runtime", {}).get("generation")
            or declaration.get("audio_sha256") != value.get("sha256")
            or not isinstance(receipt, dict)
            or receipt.get("schema") != AUDIO_SCOPE_SCHEMA
            or receipt.get("phase") != phase
            or receipt.get("runtime", {}).get("generation") != declaration.get("runtime_generation")
            or receipt.get("audio", {}).get("sha256") != declaration.get("audio_sha256")
        ):
            raise HarnessError("E2E_AUDIO_SCOPE_INVALID", f"report audio.{phase} does not bind its scope receipt exactly")
        generations[str(phase)] = declaration.get("runtime_generation")
    if report.get("status") == "passed" and (
        generations["cold"] == generations["restarted"]
        or generations["restarted"] != generations["repeat"]
    ):
        raise HarnessError("E2E_AUDIO_SCOPE_INVALID", "restart/cache scope generations are not distinct and stable")


def run_e2e(
    config: E2EConfig,
    *,
    backend: E2EBackend,
    inspector: PortInspector,
    network_guard: AbstractContextManager[Any] | None = None,
) -> dict[str, Any]:
    config = config.validated()
    protected_before = dict(inspector.snapshot(PROTECTED_PORTS))
    selected_before = tuple(inspector.snapshot((config.port,)).get(config.port, ()))
    if selected_before:
        raise HarnessError(
            "E2E_PORT_IN_USE",
            "selected random port already has a listener; no process was stopped",
            details={"port": config.port, "listener_pids": list(selected_before)},
        )
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": "running",
        "test_only": config.test_trust_chain,
        "temporary_test_reference": config.temporary_test_reference,
        "port": config.port,
        "protected_ports": {str(key): list(value) for key, value in protected_before.items()},
        "offline_guard": {
            "parent_socket_policy": "numeric-loopback-only",
            "child_environment": ["HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "local_files_only=True"],
            "host_firewall_claimed": False,
        },
        "phases": {},
        "audio": {},
    }
    primary_error: BaseException | None = None
    cleanup: Mapping[str, Any] = {"owned_only": True, "failures": []}
    guard = network_guard or LoopbackOnlyNetworkGuard()
    guard.__enter__()
    try:
        report["phases"]["build_verify_install_register"] = _strict_json_mapping(
            backend.configure(config), field="backend.configure"
        )

        report["phases"]["cold_prepare"] = _strict_json_mapping(
            backend.prepare(), field="backend.cold_prepare"
        )
        cold_runtime = _owned_runtime_evidence(backend, inspector, config)
        cold = backend.synthesize(text=config.text, style=config.style, reply_kind=config.reply_kind)
        cold_audio = _validate_pcm_wav(cold.audio)
        if cold.media_type.split(";", 1)[0].strip().casefold() not in {"audio/wav", "audio/x-wav", "audio/wave"}:
            raise HarnessError("E2E_AUDIO_INVALID", "cold synthesis media type is not WAV")
        if cold.cache != "miss":
            raise HarnessError("E2E_COLD_START_CACHE_INVALID", "cold synthesis must report a cache miss")
        _immutable_write(config.evidence_root / "cold.wav", cold.audio)
        cold_scope = _persist_audio_scope(
            config=config,
            phase="cold",
            run=cold,
            audio=cold_audio,
            runtime=cold_runtime,
        )
        cold_audio.update(
            {"profile": cold.profile, "cache": cold.cache, "runtime": cold_runtime, "scope_receipt": cold_scope}
        )
        report["audio"]["cold"] = cold_audio

        restart = dict(backend.restart_owned())
        if restart.get("stopped") is not True or restart.get("external_processes_terminated") is not False:
            raise HarnessError("E2E_OWNED_RESTART_FAILED", "owned restart was not confirmed")
        if inspector.snapshot((config.port,)).get(config.port, ()):
            raise HarnessError("E2E_OWNED_RESTART_FAILED", "runtime port remained open after owned stop")
        report["phases"]["owned_restart"] = restart

        report["phases"]["restart_prepare"] = _strict_json_mapping(
            backend.prepare(), field="backend.restart_prepare"
        )
        restarted_runtime = _owned_runtime_evidence(backend, inspector, config)
        if (
            restarted_runtime["owned_pid"] == cold_runtime["owned_pid"]
            or restarted_runtime["generation"] == cold_runtime["generation"]
        ):
            raise HarnessError("E2E_RESTART_IDENTITY_REUSED", "restart did not produce a fresh PID and generation")
        restarted = backend.synthesize(text=config.text, style=config.style, reply_kind=config.reply_kind)
        restarted_audio = _validate_pcm_wav(restarted.audio)
        if restarted.cache != "miss":
            raise HarnessError("E2E_RESTART_CACHE_INVALID", "first synthesis after owned restart must be a cache miss")
        _immutable_write(config.evidence_root / "restarted.wav", restarted.audio)
        restarted_scope = _persist_audio_scope(
            config=config,
            phase="restarted",
            run=restarted,
            audio=restarted_audio,
            runtime=restarted_runtime,
        )
        restarted_audio.update(
            {
                "profile": restarted.profile,
                "cache": restarted.cache,
                "runtime": restarted_runtime,
                "scope_receipt": restarted_scope,
            }
        )
        report["audio"]["restarted"] = restarted_audio

        repeated = backend.synthesize(text=config.text, style=config.style, reply_kind=config.reply_kind)
        repeated_audio = _validate_pcm_wav(repeated.audio)
        if repeated.cache != "hit" or repeated.audio != restarted.audio:
            raise HarnessError(
                "E2E_CACHE_ACCEPTANCE_FAILED",
                "same-generation repeat must be an exact audio cache hit",
            )
        repeated_runtime = _owned_runtime_evidence(backend, inspector, config)
        if (
            repeated_runtime["owned_pid"] != restarted_runtime["owned_pid"]
            or repeated_runtime["generation"] != restarted_runtime["generation"]
        ):
            raise HarnessError(
                "E2E_CACHE_GENERATION_MISMATCH",
                "same-generation cache hit crossed a runtime PID or generation",
            )
        _immutable_write(config.evidence_root / "repeat.wav", repeated.audio)
        repeated_scope = _persist_audio_scope(
            config=config,
            phase="repeat",
            run=repeated,
            audio=repeated_audio,
            runtime=repeated_runtime,
        )
        repeated_audio.update(
            {
                "profile": repeated.profile,
                "cache": repeated.cache,
                "runtime": repeated_runtime,
                "scope_receipt": repeated_scope,
            }
        )
        report["audio"]["repeat"] = repeated_audio
        report["phases"]["cache_repeat"] = {"status": "passed", "cache": repeated.cache}
        report["status"] = "passed"
    except BaseException as exc:
        primary_error = exc
        report["status"] = "failed"
        report["error"] = {
            "code": getattr(exc, "code", "E2E_FAILED"),
            "type": type(exc).__name__,
            "message": str(exc),
            "details": getattr(exc, "details", {}),
        }
    finally:
        try:
            cleanup = dict(backend.cleanup_owned())
        except BaseException as exc:
            cleanup = {
                "owned_only": True,
                "failures": [{"phase": "cleanup", "error": f"{type(exc).__name__}:{exc}"}],
            }
            if primary_error is None:
                primary_error = exc
                report["status"] = "failed"
        try:
            guard.__exit__(None, None, None)
        except BaseException as exc:
            if primary_error is None:
                primary_error = exc
                report["status"] = "failed"
        report["cleanup"] = cleanup

    selected_after = tuple(inspector.snapshot((config.port,)).get(config.port, ()))
    protected_after = dict(inspector.snapshot(PROTECTED_PORTS))
    protection_errors = []
    if selected_after:
        protection_errors.append(f"selected port still open by {list(selected_after)}")
    if protected_after != protected_before:
        protection_errors.append("protected listener PID snapshot changed")
    if cleanup.get("owned_only") is not True or cleanup.get("failures"):
        protection_errors.append("owned cleanup was not clean")
    report["protected_ports_after"] = {str(key): list(value) for key, value in protected_after.items()}
    if protection_errors:
        report["status"] = "failed"
        report["cleanup_gate"] = {"status": "failed", "errors": protection_errors}
        if primary_error is None:
            primary_error = HarnessError("E2E_CLEANUP_GATE_FAILED", "; ".join(protection_errors))
    else:
        report["cleanup_gate"] = {"status": "passed"}
    try:
        _validate_report_scope_pins(report)
    except BaseException as exc:
        report["status"] = "failed"
        report["scope_gate"] = {
            "status": "failed",
            "code": getattr(exc, "code", "E2E_AUDIO_SCOPE_INVALID"),
            "message": str(exc),
        }
        if primary_error is None:
            primary_error = exc
    else:
        report["scope_gate"] = {"status": "passed"}
    _immutable_write(config.evidence_root / "e2e-report.json", _json_bytes(report))
    if primary_error is not None:
        if isinstance(primary_error, HarnessError):
            raise primary_error
        raise HarnessError("E2E_FAILED", str(primary_error)) from primary_error
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the explicit real Qwen Base Clone release E2E.")
    parser.add_argument("--engine-build-spec", type=Path, required=True)
    parser.add_argument("--engine-output-root", type=Path, required=True)
    parser.add_argument("--engine-pack-root", type=Path, required=True)
    parser.add_argument("--engine-archive", type=Path, required=True)
    parser.add_argument("--engine-catalog", type=Path, required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--trust-receipt", type=Path, required=True)
    parser.add_argument("--trust-policy", type=Path, required=True)
    parser.add_argument("--trust-policy-signature", type=Path, required=True)
    parser.add_argument("--voice-pack-root", type=Path, required=True)
    parser.add_argument("--voice-catalog", type=Path, required=True)
    parser.add_argument("--voice-pack-id", required=True)
    parser.add_argument("--voice-pack-version", required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--style", choices=("lively", "relaxed"), required=True)
    parser.add_argument("--reply-kind", default="e2e")
    parser.add_argument(
        "--test-trust-chain",
        action="store_true",
        help="explicitly allow only the fixed internal test root/policy/leaf chain",
    )
    parser.add_argument(
        "--temporary-test-reference",
        action="store_true",
        help="mark an unselected A-E reference as temporary test evidence; never a final voice",
    )
    return parser


def _config(args: argparse.Namespace) -> E2EConfig:
    return E2EConfig(
        engine_build_spec=args.engine_build_spec,
        engine_output_root=args.engine_output_root,
        engine_pack_root=args.engine_pack_root,
        engine_archive=args.engine_archive,
        engine_catalog=args.engine_catalog,
        package_id=args.package_id,
        version=args.version,
        public_key=args.public_key,
        trust_receipt=args.trust_receipt,
        trust_policy=args.trust_policy,
        trust_policy_signature=args.trust_policy_signature,
        voice_pack_root=args.voice_pack_root,
        voice_catalog=args.voice_catalog,
        voice_pack_id=args.voice_pack_id,
        voice_pack_version=args.voice_pack_version,
        cache_root=args.cache_root,
        evidence_root=args.evidence_root,
        port=args.port,
        text=args.text,
        style=args.style,
        reply_kind=args.reply_kind,
        test_trust_chain=args.test_trust_chain,
        temporary_test_reference=args.temporary_test_reference,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_e2e(
            _config(args),
            backend=RealQwenE2EBackend(),
            inspector=WindowsPowerShellPortInspector(),
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except HarnessError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

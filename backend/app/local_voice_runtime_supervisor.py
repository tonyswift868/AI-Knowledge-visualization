"""Fail-closed lifecycle supervision for bundled local voice runtimes.

Voice-pack manifests may select a built-in adapter, but they must never supply
an executable, arguments, environment, or port.  Trusted application code
first creates a :class:`TrustedRuntimeCatalog`, then issues an attested plan
from one of its fixed entries.  The supervisor accepts only a plan issued by
that same catalog.

The child health endpoint proves liveness with a fresh nonce on every probe:

``HMAC(instance_secret, nonce|service_id|runtime_id|pid|ready)``

The per-launch secret is inherited through the child environment and is never
returned by health.  A separate injected verifier must confirm that the PID
reported by health is the operating-system listener for the configured port.
On Windows an injected Job Object (or equivalent process-tree owner) is also
mandatory.  Missing ownership capabilities fail closed before launch.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping, Protocol


RUNTIME_HEALTH_SCHEMA = "mentorflow.local-voice-runtime-health.v2"
INSTANCE_SECRET_ENV = "MENTORFLOW_LOCAL_VOICE_INSTANCE_SECRET"
NONCE_HEADER = "X-MentorFlow-Runtime-Nonce"
REQUEST_NONCE_HEADER = "X-MentorFlow-Request-Nonce"
REQUEST_HEADER_PROOF_HEADER = "X-MentorFlow-Request-Header-Proof"
REQUEST_PROOF_HEADER = "X-MentorFlow-Request-Proof"
LOOPBACK_HOST = "127.0.0.1"
MAX_AUTHORIZED_REQUEST_BODY_BYTES = 36 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FORBIDDEN_EXECUTABLE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".js",
    ".ps1",
    ".py",
    ".pyw",
    ".sh",
    ".vbs",
}
_SCRUBBED_PYTHON_ENV = {
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "VIRTUAL_ENV",
}
_BASE_CHILD_ENV_ALLOWLIST = {
    "APPDATA",
    "COMSPEC",
    "CUDA_PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}
_CATALOG_ENV_ALLOWLIST = {
    "HF_HOME",
    "HF_HUB_CACHE",
    "HF_HUB_OFFLINE",
    "KMP_DUPLICATE_LIB_OK",
    "TORCH_HOME",
    "TRANSFORMERS_OFFLINE",
    "XDG_CACHE_HOME",
    "MENTORFLOW_VOICE_CACHE_ROOT",
    "MENTORFLOW_VOICE_DEVICE",
    "MENTORFLOW_VOICE_HOST",
    "MENTORFLOW_VOICE_MIN_NVIDIA_DRIVER",
    "MENTORFLOW_VOICE_PORT",
    "MENTORFLOW_VOICE_RUNTIME_ROOT",
}


class LocalVoiceRuntimeError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


class RuntimeProbeUnavailable(RuntimeError):
    """A transient loopback connection failure while a child is starting."""


class OwnedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class RuntimeLauncher(Protocol):
    def launch(
        self,
        plan: "LocalVoiceRuntimePlan",
        *,
        environment: Mapping[str, str],
    ) -> OwnedProcess: ...


class RuntimeProcessTreeOwner(Protocol):
    """Own a complete launched process tree, e.g. with a Windows Job Object."""

    def launch(
        self,
        launcher: RuntimeLauncher,
        plan: "LocalVoiceRuntimePlan",
        *,
        environment: Mapping[str, str],
        pre_resume: Callable[[], None] | None = None,
    ) -> OwnedProcess: ...

    def owns_pid(self, process: OwnedProcess, pid: int) -> bool: ...

    def terminate(self, process: OwnedProcess, *, timeout_seconds: float) -> bool: ...

    def release(self, process: OwnedProcess) -> None: ...


class RuntimeIdentityProbe(Protocol):
    def probe(
        self,
        plan: "LocalVoiceRuntimePlan",
        *,
        nonce: str,
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class ListenerOwnershipVerifier(Protocol):
    """Verify through the OS that one PID owns the configured listening port."""

    def verify(self, *, host: str, port: int, listener_pid: int) -> bool: ...


class RuntimeLaunchAttestationLease(Protocol):
    """Generation-scoped evidence retained from verification through exit."""

    def seal_for_launch(self) -> None: ...

    def assert_valid(self) -> None: ...

    def release(self) -> None: ...


def _safe_relative(
    value: str,
    field_name: str,
    *,
    allow_root: bool = False,
) -> PurePosixPath:
    text = str(value or "").strip()
    if allow_root and text == ".":
        return PurePosixPath(".")
    if not text or "\\" in text or ":" in text or text.startswith("/"):
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_CATALOG_INVALID",
            f"{field_name} must be a relative POSIX path",
        )
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_CATALOG_INVALID",
            f"{field_name} escapes the runtime root",
        )
    return path


@dataclass(frozen=True)
class RuntimeCatalogEntry:
    """A fixed command definition authored by trusted application code."""

    catalog_id: str
    runtime_id: str
    service_id: str
    executable_relative: str
    arguments: tuple[str, ...]
    working_directory_relative: str
    environment: Mapping[str, str] = field(default_factory=dict)
    executable_sha256: str | None = None
    required_file_sha256: Mapping[str, str] = field(default_factory=dict)

    def validated(self) -> "RuntimeCatalogEntry":
        for name, value in (
            ("catalog_id", self.catalog_id),
            ("runtime_id", self.runtime_id),
            ("service_id", self.service_id),
        ):
            if not str(value or "").strip():
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_CATALOG_INVALID",
                    f"{name} is required",
                )
        executable = _safe_relative(self.executable_relative, "executable_relative")
        working_directory = _safe_relative(
            self.working_directory_relative,
            "working_directory_relative",
            allow_root=True,
        )
        if Path(executable.name).suffix.casefold() in _FORBIDDEN_EXECUTABLE_SUFFIXES:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_SCRIPT_EXECUTABLE_FORBIDDEN",
                "catalog executables must be native binaries, not scripts",
            )
        arguments = tuple(map(str, self.arguments))
        if any("\x00" in argument for argument in arguments):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_CATALOG_INVALID",
                "runtime arguments may not contain NUL bytes",
            )
        environment = {str(key): str(value) for key, value in self.environment.items()}
        for key, value in environment.items():
            normalized = key.strip().upper()
            if (
                not key.strip()
                or "=" in key
                or "\x00" in key
                or "\x00" in value
                or normalized in _SCRUBBED_PYTHON_ENV
                or normalized == INSTANCE_SECRET_ENV
                or normalized not in _CATALOG_ENV_ALLOWLIST
            ):
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_ENVIRONMENT_FORBIDDEN",
                    f"catalog environment key is forbidden: {key}",
                )
        digest = str(self.executable_sha256 or "").strip().lower()
        if not digest:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_EXECUTABLE_HASH_REQUIRED",
                "trusted runtime catalog entries must pin executable_sha256",
            )
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_EXECUTABLE_HASH_INVALID",
                "executable_sha256 must be a lowercase SHA-256 digest",
            )
        required_files: dict[str, str] = {}
        for raw_relative, raw_digest in self.required_file_sha256.items():
            relative = _safe_relative(str(raw_relative), "required_file_sha256 path").as_posix()
            pinned = str(raw_digest or "").strip().lower()
            if len(pinned) != 64 or any(character not in "0123456789abcdef" for character in pinned):
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_REQUIRED_FILE_HASH_INVALID",
                    f"required runtime file hash is invalid: {relative}",
                )
            if relative.casefold() in {item.casefold() for item in required_files}:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_CATALOG_INVALID",
                    "required runtime file paths must be unique on Windows",
                )
            required_files[relative] = pinned
        return replace(
            self,
            catalog_id=str(self.catalog_id).strip(),
            runtime_id=str(self.runtime_id).strip(),
            service_id=str(self.service_id).strip(),
            executable_relative=executable.as_posix(),
            arguments=arguments,
            working_directory_relative=working_directory.as_posix(),
            environment=environment,
            executable_sha256=digest,
            required_file_sha256=required_files,
        )


@dataclass(frozen=True)
class LocalVoiceRuntimePlan:
    runtime_id: str
    service_id: str
    runtime_root: Path
    executable: Path
    arguments: tuple[str, ...]
    working_directory: Path
    port: int
    host: str = LOOPBACK_HOST
    environment: Mapping[str, str] = field(default_factory=dict)
    executable_sha256: str | None = None
    required_file_sha256: Mapping[str, str] = field(default_factory=dict)
    startup_timeout_seconds: float = 90.0
    probe_timeout_seconds: float = 1.5
    idle_ttl_seconds: float = 300.0
    reaper_interval_seconds: float = 5.0
    _catalog_token: object | None = field(default=None, repr=False, compare=False)
    _catalog_attestation: str = field(default="", repr=False, compare=False)


class TrustedRuntimeCatalog:
    """Issue plans whose commands are fixed by application-owned entries."""

    def __init__(self, entries: list[RuntimeCatalogEntry] | tuple[RuntimeCatalogEntry, ...]) -> None:
        self._token = object()
        self._attestation_key = secrets.token_bytes(32)
        self._entries: dict[str, RuntimeCatalogEntry] = {}
        for raw_entry in entries:
            entry = raw_entry.validated()
            if entry.catalog_id in self._entries:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_CATALOG_INVALID",
                    f"duplicate runtime catalog entry: {entry.catalog_id}",
                )
            self._entries[entry.catalog_id] = entry

    def issue_plan(
        self,
        catalog_id: str,
        *,
        runtime_root: Path,
        port: int,
        startup_timeout_seconds: float = 90.0,
        probe_timeout_seconds: float = 1.5,
        idle_ttl_seconds: float = 300.0,
        reaper_interval_seconds: float = 5.0,
    ) -> LocalVoiceRuntimePlan:
        try:
            entry = self._entries[str(catalog_id)]
        except KeyError as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_CATALOG_ENTRY_UNKNOWN",
                "runtime is not present in the trusted application catalog",
            ) from exc
        root = Path(os.path.abspath(runtime_root.expanduser()))
        executable_relative = PurePosixPath(entry.executable_relative)
        working_relative = PurePosixPath(entry.working_directory_relative)
        plan = LocalVoiceRuntimePlan(
            runtime_id=entry.runtime_id,
            service_id=entry.service_id,
            runtime_root=root,
            executable=root.joinpath(*executable_relative.parts),
            arguments=entry.arguments,
            working_directory=root.joinpath(*working_relative.parts),
            port=int(port),
            environment=dict(entry.environment),
            executable_sha256=entry.executable_sha256,
            required_file_sha256=dict(entry.required_file_sha256),
            startup_timeout_seconds=startup_timeout_seconds,
            probe_timeout_seconds=probe_timeout_seconds,
            idle_ttl_seconds=idle_ttl_seconds,
            reaper_interval_seconds=reaper_interval_seconds,
            _catalog_token=self._token,
        )
        validated = _validate_plan(plan)
        return replace(
            validated,
            _catalog_attestation=self._sign(validated),
        )

    def owns(self, plan: LocalVoiceRuntimePlan) -> bool:
        return bool(
            plan._catalog_token is self._token
            and plan._catalog_attestation
            and hmac.compare_digest(plan._catalog_attestation, self._sign(plan))
        )

    def _sign(self, plan: LocalVoiceRuntimePlan) -> str:
        payload = json.dumps(
            {
                "runtime_id": plan.runtime_id,
                "service_id": plan.service_id,
                "runtime_root": str(plan.runtime_root),
                "executable": str(plan.executable),
                "arguments": list(plan.arguments),
                "working_directory": str(plan.working_directory),
                "host": plan.host,
                "port": int(plan.port),
                "environment": sorted(
                    (str(key), str(value)) for key, value in plan.environment.items()
                ),
                "executable_sha256": plan.executable_sha256,
                "required_file_sha256": sorted(
                    (str(key), str(value)) for key, value in plan.required_file_sha256.items()
                ),
                "startup_timeout_seconds": float(plan.startup_timeout_seconds),
                "probe_timeout_seconds": float(plan.probe_timeout_seconds),
                "idle_ttl_seconds": float(plan.idle_ttl_seconds),
                "reaper_interval_seconds": float(plan.reaper_interval_seconds),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._attestation_key, payload, hashlib.sha256).hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_REPARSE_CHECK_FAILED",
            "could not inspect runtime path metadata",
            details={"path": str(path), "error": f"{type(exc).__name__}:{exc}"},
        ) from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(metadata.st_mode)


def _assert_no_reparse(path: Path, boundary: Path) -> None:
    candidate = Path(os.path.abspath(path))
    root = Path(os.path.abspath(boundary))
    if not candidate.is_relative_to(root):
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_PATH_INVALID",
            "runtime path escapes the configured runtime root",
        )
    while True:
        if _is_reparse_point(candidate):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REPARSE_FORBIDDEN",
                "runtime executable and working directory may not use symlinks or reparse points",
                details={"path": str(candidate)},
            )
        if candidate == root:
            break
        candidate = candidate.parent


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_plan(plan: LocalVoiceRuntimePlan) -> LocalVoiceRuntimePlan:
    if plan.host != LOOPBACK_HOST:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_NON_LOOPBACK",
            "local voice runtimes must bind to 127.0.0.1",
        )
    if not 1024 <= int(plan.port) <= 65535:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_PORT_INVALID",
            "local voice runtime port must be between 1024 and 65535",
        )
    for value, minimum, maximum, label in (
        (plan.startup_timeout_seconds, 0.1, 300.0, "startup timeout"),
        (plan.probe_timeout_seconds, 0.05, 10.0, "probe timeout"),
        (plan.idle_ttl_seconds, 1.0, 86_400.0, "idle TTL"),
        (plan.reaper_interval_seconds, 0.1, 60.0, "reaper interval"),
    ):
        if not minimum <= float(value) <= maximum:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PLAN_INVALID",
                f"{label} is outside the supported range",
            )
    root = Path(os.path.abspath(plan.runtime_root.expanduser()))
    executable = Path(os.path.abspath(plan.executable.expanduser()))
    working_directory = Path(os.path.abspath(plan.working_directory.expanduser()))
    if root.parent == root:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_ROOT_INVALID",
            "runtime root cannot be a filesystem root",
        )
    for path in (executable, working_directory):
        if not path.is_relative_to(root):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PATH_INVALID",
                "runtime executable and working directory must remain inside runtime root",
            )
    _assert_no_reparse(root, root)
    _assert_no_reparse(executable, root)
    _assert_no_reparse(working_directory, root)
    resolved_root = root.resolve(strict=False)
    for path in (executable, working_directory):
        if not path.resolve(strict=False).is_relative_to(resolved_root):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PATH_INVALID",
                "resolved runtime path escapes the configured runtime root",
            )
    if not root.is_dir() or not executable.is_file() or not working_directory.is_dir():
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_ASSETS_MISSING",
            "runtime root, executable, or working directory is missing",
        )
    if executable.suffix.casefold() in _FORBIDDEN_EXECUTABLE_SUFFIXES:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_SCRIPT_EXECUTABLE_FORBIDDEN",
            "runtime executable must be a native binary, not a script",
        )
    if plan.executable_sha256 and not hmac.compare_digest(
        _hash_file(executable),
        plan.executable_sha256,
    ):
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_EXECUTABLE_HASH_MISMATCH",
            "runtime executable does not match the trusted catalog digest",
        )
    required_files: dict[str, str] = {}
    for raw_relative, raw_digest in plan.required_file_sha256.items():
        relative = _safe_relative(str(raw_relative), "required_file_sha256 path")
        path = root.joinpath(*relative.parts)
        _assert_no_reparse(path, root)
        digest = str(raw_digest or "").strip().lower()
        if (
            not path.is_file()
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not hmac.compare_digest(_hash_file(path), digest)
        ):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUIRED_FILE_MISMATCH",
                f"required runtime file does not match the trusted catalog: {relative.as_posix()}",
            )
        required_files[relative.as_posix()] = digest
    return replace(
        plan,
        runtime_root=root,
        executable=executable,
        working_directory=working_directory,
        arguments=tuple(map(str, plan.arguments)),
        environment={str(key): str(value) for key, value in plan.environment.items()},
        required_file_sha256=required_files,
    )


class SubprocessRuntimeLauncher:
    def launch(
        self,
        plan: LocalVoiceRuntimePlan,
        *,
        environment: Mapping[str, str],
    ) -> OwnedProcess:
        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        return subprocess.Popen(
            [str(plan.executable), *plan.arguments],
            cwd=str(plan.working_directory),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )


class DirectProcessOwner:
    """Direct-child ownership for non-Windows platforms only."""

    def launch(
        self,
        launcher: RuntimeLauncher,
        plan: LocalVoiceRuntimePlan,
        *,
        environment: Mapping[str, str],
        pre_resume: Callable[[], None] | None = None,
    ) -> OwnedProcess:
        if pre_resume is not None:
            pre_resume()
        return launcher.launch(plan, environment=environment)

    def owns_pid(self, process: OwnedProcess, pid: int) -> bool:
        return int(process.pid) == int(pid) and process.poll() is None

    def terminate(self, process: OwnedProcess, *, timeout_seconds: float) -> bool:
        try:
            if process.poll() is not None:
                return True
        except BaseException:
            # A poll failure is not proof of exit. Still apply the retained
            # direct-process termination strategy.
            pass
        process.terminate()
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout_seconds)
        try:
            return process.poll() is not None
        except BaseException:
            return False

    def release(self, process: OwnedProcess) -> None:
        return None


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class HttpRuntimeIdentityProbe:
    def probe(
        self,
        plan: LocalVoiceRuntimePlan,
        *,
        nonce: str,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        request = urllib.request.Request(
            f"http://{plan.host}:{int(plan.port)}/health",
            headers={"Accept": "application/json", NONCE_HEADER: nonce},
            method="GET",
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RejectRedirects(),
        )
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(256 * 1024)
        except urllib.error.HTTPError as exc:
            if 500 <= int(exc.code) <= 599:
                raise RuntimeProbeUnavailable(f"health_http_{exc.code}") from exc
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_HEALTH_REJECTED",
                "voice runtime health endpoint rejected the ownership probe",
                details={"status": int(exc.code)},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeProbeUnavailable(type(exc).__name__) from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_HEALTH_MALFORMED",
                "voice runtime health response is not valid UTF-8 JSON",
            ) from exc
        if not isinstance(value, Mapping):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_HEALTH_MALFORMED",
                "voice runtime health response must be an object",
            )
        return value


def loopback_port_in_use(host: str, port: int) -> bool:
    if host != LOOPBACK_HOST:
        return True
    try:
        with socket.create_connection((host, int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def _hmac_message(
    *,
    nonce: str,
    service_id: str,
    runtime_id: str,
    pid: int,
    ready: bool,
) -> bytes:
    return "\n".join(
        (nonce, service_id, runtime_id, str(int(pid)), "1" if ready else "0")
    ).encode("utf-8")


def _hmac_proof(
    secret: str,
    *,
    nonce: str,
    service_id: str,
    runtime_id: str,
    pid: int,
    ready: bool,
) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        _hmac_message(
            nonce=nonce,
            service_id=service_id,
            runtime_id=runtime_id,
            pid=pid,
            ready=ready,
        ),
        hashlib.sha256,
    ).hexdigest()


def _runtime_request_proof(
    secret: str,
    *,
    nonce: str,
    method: str,
    path: str,
    body_sha256: str,
    service_id: str,
    runtime_id: str,
    pid: int,
) -> str:
    """Bind one loopback request to the currently owned child instance."""
    payload = "\n".join(
        (
            "mentorflow.local-voice-runtime-request.v1",
            nonce,
            method.upper(),
            path,
            body_sha256,
            service_id,
            runtime_id,
            str(int(pid)),
        )
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _runtime_request_header_proof(
    secret: str,
    *,
    nonce: str,
    method: str,
    path: str,
    content_length: int,
    service_id: str,
    runtime_id: str,
    pid: int,
) -> str:
    """Authorize request metadata before an untrusted body is read."""
    payload = "\n".join(
        (
            "mentorflow.local-voice-runtime-request-header.v1",
            nonce,
            method.upper(),
            path,
            str(int(content_length)),
            service_id,
            runtime_id,
            str(int(pid)),
        )
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


@dataclass
class _OwnedRuntime:
    process: OwnedProcess
    instance_secret: str
    generation: int
    started_at: float
    launch_attestation: RuntimeLaunchAttestationLease | None = None


@dataclass(frozen=True)
class _LeaseToken:
    generation: int


class LocalVoiceRuntimeSupervisor:
    def __init__(
        self,
        plan: LocalVoiceRuntimePlan,
        *,
        catalog: TrustedRuntimeCatalog,
        launcher: RuntimeLauncher | None = None,
        identity_probe: RuntimeIdentityProbe | None = None,
        listener_verifier: ListenerOwnershipVerifier | None = None,
        process_tree_owner: RuntimeProcessTreeOwner | None = None,
        port_in_use: Callable[[str, int], bool] = loopback_port_in_use,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        secret_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        nonce_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
        before_launch: Callable[[], RuntimeLaunchAttestationLease | None] | None = None,
        platform_name: str = os.name,
    ) -> None:
        if not catalog.owns(plan):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PLAN_UNTRUSTED",
                "runtime plan was not issued by the supplied trusted catalog",
            )
        self.plan = _validate_plan(plan)
        self._catalog = catalog
        self._launcher = launcher or SubprocessRuntimeLauncher()
        self._identity_probe = identity_probe or HttpRuntimeIdentityProbe()
        self._listener_verifier = listener_verifier
        self._platform_name = platform_name
        self._process_tree_owner = (
            process_tree_owner
            if process_tree_owner is not None
            else (DirectProcessOwner() if platform_name != "nt" else None)
        )
        self._port_in_use = port_in_use
        self._clock = clock
        self._sleep = sleep
        self._secret_factory = secret_factory
        self._nonce_factory = nonce_factory
        self._before_launch = before_launch
        self._operation_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._owned: _OwnedRuntime | None = None
        self._generation = 0
        self._lease_counts: dict[int, int] = {}
        self._last_used_at: float | None = None
        self._state = "stopped"
        self._stop_epoch = 0
        self._shutdown_requested = False
        self._last_error: dict[str, Any] | None = None
        self._reaper_stop = threading.Event()
        self._reaper_thread: threading.Thread | None = None

    @staticmethod
    def _require_attestation_contract(value: Any) -> RuntimeLaunchAttestationLease | None:
        if value is None:
            return None
        if not all(callable(getattr(value, member, None)) for member in ("seal_for_launch", "assert_valid", "release")):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_ATTESTATION_INVALID",
                "before_launch returned an invalid launch-attestation lease",
            )
        return value

    def _assert_owned_attestation(self, owned: _OwnedRuntime) -> None:
        lease = owned.launch_attestation
        if lease is None:
            return
        try:
            lease.assert_valid()
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            error = LocalVoiceRuntimeError(
                "VOICE_RUNTIME_ATTESTATION_INVALID",
                "the retained child-generation launch attestation is no longer valid",
                details={
                    "pid": int(owned.process.pid),
                    "upstream_code": str(getattr(exc, "code", "")),
                    "error": f"{type(exc).__name__}:{exc}",
                },
            )
            self._remember_failure(error.code, error)
            raise error from exc

    def _release_owned_attestation(self, owned: _OwnedRuntime) -> None:
        lease = owned.launch_attestation
        if lease is None:
            return
        try:
            lease.release()
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            error = LocalVoiceRuntimeError(
                "VOICE_RUNTIME_ATTESTATION_RELEASE_FAILED",
                "the exited child generation's launch-attestation lease could not be released",
                details={"pid": int(owned.process.pid), "error": f"{type(exc).__name__}:{exc}"},
            )
            self._remember_failure(error.code, error)
            raise error from exc
        owned.launch_attestation = None

    @staticmethod
    def _release_pending_attestation(lease: RuntimeLaunchAttestationLease | None) -> None:
        if lease is None:
            return
        try:
            lease.release()
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_ATTESTATION_RELEASE_FAILED",
                "an unused launch-attestation lease could not be released",
                details={"error": f"{type(exc).__name__}:{exc}"},
            ) from exc

    def _poll_owned(
        self,
        owned: _OwnedRuntime,
        *,
        failure_code: str = "VOICE_RUNTIME_POLL_FAILED",
    ) -> tuple[bool, int | None]:
        """Return ``(known, exit_code)`` without ever discarding the handle."""
        try:
            return True, owned.process.poll()
        except BaseException as exc:
            self._remember_failure(failure_code, exc)
            return False, None

    def _current_lease_count(self) -> int:
        owned = self._owned
        return self._lease_counts.get(owned.generation, 0) if owned else 0

    def _release_exited_owner(self, owned: _OwnedRuntime) -> None:
        # Release generation attestation first.  If its watcher cleanup needs
        # a retry, the retained process/Job handle must remain authoritative so
        # a later status/stop call can still observe the exited child and retry.
        # Once this succeeds, an owner-release failure is independently
        # retryable because launch_attestation has already been cleared.
        self._release_owned_attestation(owned)
        owner = self._process_tree_owner
        release = getattr(owner, "release", None)
        if not callable(release):
            error = LocalVoiceRuntimeError(
                "VOICE_RUNTIME_OWNER_RELEASE_FAILED",
                "the process-tree owner cannot release its retained handles",
                details={"pid": int(owned.process.pid)},
            )
            self._remember_failure(error.code, error)
            raise error
        try:
            release(owned.process)
        except BaseException as exc:
            error = LocalVoiceRuntimeError(
                "VOICE_RUNTIME_OWNER_RELEASE_FAILED",
                "the exited runtime's retained ownership handles could not be released",
                details={
                    "pid": int(owned.process.pid),
                    "error": f"{type(exc).__name__}:{exc}",
                },
            )
            self._remember_failure(error.code, error)
            raise error from exc

    def _passive_clear_exited(self) -> str:
        with self._state_lock:
            owned = self._owned
            if owned is None:
                return "absent"
            known, exit_code = self._poll_owned(owned)
            if not known:
                return "unknown"
            if exit_code is not None:
                try:
                    self._release_exited_owner(owned)
                except LocalVoiceRuntimeError:
                    return "unknown"
                self._lease_counts.pop(owned.generation, None)
                self._owned = None
                self._state = "stopped"
                return "exited"
            return "running"

    def status(self) -> dict[str, Any]:
        process_state = self._passive_clear_exited()
        with self._state_lock:
            owned = self._owned
            return {
                "schema": "mentorflow.local-voice-supervisor-status.v2",
                "runtime_id": self.plan.runtime_id,
                "service_id": self.plan.service_id,
                "state": self._state,
                "owned": owned is not None,
                "process_state": process_state,
                "owned_pid": int(owned.process.pid) if owned is not None else None,
                "generation": owned.generation if owned else None,
                "active_leases": self._current_lease_count(),
                "last_used_at": self._last_used_at,
                "idle_ttl_seconds": self.plan.idle_ttl_seconds,
                "origin": f"http://{self.plan.host}:{self.plan.port}",
                "listener_verifier_configured": self._listener_verifier is not None,
                "process_tree_owner_configured": self._process_tree_owner is not None,
                "last_error": dict(self._last_error) if self._last_error else None,
                "starts_model": False,
            }

    def _require_ownership_capabilities(self) -> None:
        if self._listener_verifier is None:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_LISTENER_VERIFIER_REQUIRED",
                "an OS listener-PID verifier is required before starting a local voice runtime",
            )
        if self._process_tree_owner is None:
            code = (
                "VOICE_RUNTIME_WINDOWS_JOB_OWNER_REQUIRED"
                if self._platform_name == "nt"
                else "VOICE_RUNTIME_PROCESS_TREE_OWNER_REQUIRED"
            )
            raise LocalVoiceRuntimeError(
                code,
                "a process-tree ownership controller is required before launch",
            )

    def _build_child_environment(self, secret: str) -> dict[str, str]:
        # Do not forward the backend's provider credentials or arbitrary
        # Python injection variables into a local model process. Preserve only
        # the minimum OS process environment plus explicitly catalog-allowed
        # offline/cache settings.
        environment = {
            str(key): str(value)
            for key, value in os.environ.items()
            if key.upper() in _BASE_CHILD_ENV_ALLOWLIST
        }
        environment.update({str(key): str(value) for key, value in self.plan.environment.items()})
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        environment[INSTANCE_SECRET_ENV] = secret
        return environment

    def _probe_owned(self, owned: _OwnedRuntime) -> Mapping[str, Any]:
        self._assert_owned_attestation(owned)
        nonce = self._nonce_factory()
        if not nonce:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_NONCE_FAILED",
                "could not create a fresh runtime probe nonce",
            )
        health = self._identity_probe.probe(
            self.plan,
            nonce=nonce,
            timeout_seconds=self.plan.probe_timeout_seconds,
        )
        if any(
            forbidden in health
            for forbidden in ("instance_secret", "instance_token", "instance_token_sha256")
        ):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_SECRET_DISCLOSURE",
                "health response disclosed a forbidden static credential",
            )
        try:
            pid = int(health.get("pid"))
            listener_pid = int(health.get("listener_pid"))
        except (TypeError, ValueError) as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_IDENTITY_MISMATCH",
                "health response omitted a valid listener PID",
            ) from exc
        ready = health.get("ready") is True
        proof = str(health.get("proof") or "")
        expected = _hmac_proof(
            owned.instance_secret,
            nonce=nonce,
            service_id=str(health.get("service_id") or ""),
            runtime_id=str(health.get("runtime_id") or ""),
            pid=pid,
            ready=ready,
        )
        identity_matches = bool(
            health.get("schema") == RUNTIME_HEALTH_SCHEMA
            and health.get("runtime_id") == self.plan.runtime_id
            and health.get("service_id") == self.plan.service_id
            and pid == listener_pid
            and ready
            and len(proof) == 64
            and hmac.compare_digest(proof, expected)
        )
        owner = self._process_tree_owner
        listener = self._listener_verifier
        if (
            not identity_matches
            or owner is None
            or listener is None
            or not owner.owns_pid(owned.process, listener_pid)
            or not listener.verify(
                host=self.plan.host,
                port=self.plan.port,
                listener_pid=listener_pid,
            )
        ):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_IDENTITY_MISMATCH",
                "runtime HMAC, process-tree ownership, or OS listener ownership did not match",
                details={"reported_pid": pid, "listener_pid": listener_pid},
            )
        return health

    def _remember_failure(self, code: str, exc: BaseException) -> None:
        with self._state_lock:
            self._state = "failed"
            self._last_error = {
                "code": code,
                "error": f"{type(exc).__name__}:{exc}",
            }

    def _terminate_record(self, owned: _OwnedRuntime) -> bool:
        owner = self._process_tree_owner
        initial_known, initial_exit_code = self._poll_owned(
            owned,
            failure_code="VOICE_RUNTIME_TERMINATION_POLL_FAILED",
        )
        if initial_known and initial_exit_code is not None:
            self._release_exited_owner(owned)
            return True
        if owner is None:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_TERMINATION_UNCONFIRMED",
                "no process-tree owner is available to terminate the runtime",
            )
        termination_error: BaseException | None = None
        confirmed = False
        try:
            confirmed = bool(owner.terminate(owned.process, timeout_seconds=5.0))
        except BaseException as exc:  # preserve the handle and re-check below
            termination_error = exc
        final_known, final_exit_code = self._poll_owned(
            owned,
            failure_code="VOICE_RUNTIME_TERMINATION_POLL_FAILED",
        )
        exited = final_known and final_exit_code is not None
        # The retained process handle is the authoritative final check. A
        # controller may raise after closing a Job Object even though Windows
        # has already completed tree termination; conversely a successful
        # return without observed exit is not sufficient.
        if not exited:
            error = LocalVoiceRuntimeError(
                "VOICE_RUNTIME_TERMINATION_UNCONFIRMED",
                "owned process-tree termination could not be confirmed",
                details={
                    "pid": int(owned.process.pid),
                    "controller_confirmed": confirmed,
                    "initial_poll_known": initial_known,
                    "final_poll_known": final_known,
                    "controller_error": (
                        f"{type(termination_error).__name__}:{termination_error}"
                        if termination_error
                        else ""
                    ),
                },
            )
            self._remember_failure(error.code, error)
            raise error
        self._release_exited_owner(owned)
        return True

    def ensure_running(self) -> dict[str, Any]:
        """Single-flight lazy start with complete rollback on every failure."""
        with self._operation_lock:
            if self._shutdown_requested:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_SHUTDOWN",
                    "application shutdown has begun; new runtime starts are rejected",
                )
            process_state = self._passive_clear_exited()
            with self._state_lock:
                current = self._owned
                if self._state == "stopping":
                    raise LocalVoiceRuntimeError(
                        "VOICE_RUNTIME_STOPPING",
                        "voice runtime is stopping",
                    )
            if current is not None and process_state == "unknown":
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_POLL_FAILED",
                    "owned runtime state is unknown; refusing to launch a replacement",
                    details={"pid": int(current.process.pid)},
                )
            if current is not None and process_state == "running":
                try:
                    health = self._probe_owned(current)  # type: ignore[arg-type]
                except BaseException as exc:
                    self._remember_failure("VOICE_RUNTIME_IDENTITY_LOST", exc)
                    if isinstance(exc, LocalVoiceRuntimeError):
                        raise
                    raise LocalVoiceRuntimeError(
                        "VOICE_RUNTIME_IDENTITY_LOST",
                        "owned runtime failed its fresh identity probe",
                    ) from exc
                with self._state_lock:
                    self._state = "busy" if self._current_lease_count() else "ready"
                return {
                    "ok": True,
                    "started": False,
                    "pid": int(current.process.pid),  # type: ignore[union-attr]
                    "generation": current.generation,  # type: ignore[union-attr]
                    "health": dict(health),
                }

            self._require_ownership_capabilities()
            # Revalidate immediately before launch. This rejects replaced
            # executables, new reparse points, and digest changes since plan issue.
            if not self._catalog.owns(self.plan):
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_PLAN_UNTRUSTED",
                    "runtime plan changed after trusted catalog issue",
                )
            self.plan = _validate_plan(self.plan)
            if self._port_in_use(self.plan.host, self.plan.port):
                error = LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_PORT_OCCUPIED",
                    "configured port is occupied; MentorFlow will not adopt or stop its listener",
                    details={"host": self.plan.host, "port": self.plan.port},
                )
                self._remember_failure(error.code, error)
                raise error

            # This hook is application-owned, never manifest-controlled. It is
            # called only after the operation lock has established that a new
            # child generation is required, and immediately before launch.
            launch_attestation: RuntimeLaunchAttestationLease | None = None
            if self._before_launch is not None:
                candidate_attestation: Any = None
                try:
                    candidate_attestation = self._before_launch()
                    launch_attestation = self._require_attestation_contract(candidate_attestation)
                except BaseException as exc:
                    if not isinstance(exc, Exception):
                        raise
                    cleanup_error = ""
                    candidate_release = getattr(candidate_attestation, "release", None)
                    if callable(candidate_release):
                        try:
                            candidate_release()
                        except BaseException as cleanup_exc:
                            cleanup_error = f"{type(cleanup_exc).__name__}:{cleanup_exc}"
                    error = LocalVoiceRuntimeError(
                        "VOICE_RUNTIME_PRELAUNCH_VERIFY_FAILED",
                        "application-owned runtime verification failed before launch",
                        details={
                            "upstream_code": str(getattr(exc, "code", "")),
                            "error": f"{type(exc).__name__}:{exc}",
                            "cleanup_error": cleanup_error,
                        },
                    )
                    self._remember_failure(error.code, error)
                    raise error from exc

            try:
                secret = self._secret_factory()
                if not secret:
                    raise LocalVoiceRuntimeError(
                        "VOICE_RUNTIME_SECRET_FAILED",
                        "could not create a per-launch ownership secret",
                    )
                environment = self._build_child_environment(secret)
            except BaseException:
                self._release_pending_attestation(launch_attestation)
                raise

            def attest_suspended_child_before_resume() -> None:
                if launch_attestation is None:
                    return
                launch_attestation.seal_for_launch()
                launch_attestation.assert_valid()

            with self._state_lock:
                self._state = "starting"
                self._last_error = None

            owner = self._process_tree_owner
            assert owner is not None  # guarded by _require_ownership_capabilities
            process: OwnedProcess | None = None
            owned: _OwnedRuntime | None = None
            startup_succeeded = False
            try:
                process = owner.launch(
                    self._launcher,
                    self.plan,
                    environment=environment,
                    pre_resume=attest_suspended_child_before_resume,
                )
                with self._state_lock:
                    self._generation += 1
                    generation = self._generation
                    owned = _OwnedRuntime(
                        process=process,
                        instance_secret=secret,
                        generation=generation,
                        # Persist ownership before invoking clock, health,
                        # reaper, or any other caller-supplied operation.
                        started_at=0.0,
                        launch_attestation=launch_attestation,
                    )
                    launch_attestation = None
                    self._owned = owned
                owned.started_at = self._clock()

                deadline = self._clock() + float(self.plan.startup_timeout_seconds)
                last_unavailable: BaseException | None = None
                while self._clock() < deadline:
                    poll_known, exit_code = self._poll_owned(
                        owned,
                        failure_code="VOICE_RUNTIME_STARTUP_POLL_FAILED",
                    )
                    if not poll_known:
                        raise LocalVoiceRuntimeError(
                            "VOICE_RUNTIME_STARTUP_POLL_FAILED",
                            "could not determine whether the launched runtime is alive",
                            details={"pid": int(process.pid)},
                        )
                    if exit_code is not None:
                        raise LocalVoiceRuntimeError(
                            "VOICE_RUNTIME_EXITED_EARLY",
                            "bundled runtime exited before becoming ready",
                            details={"pid": int(process.pid), "exit_code": int(exit_code)},
                        )
                    try:
                        health = self._probe_owned(owned)
                    except RuntimeProbeUnavailable as exc:
                        last_unavailable = exc
                        self._sleep(min(0.2, max(0.01, deadline - self._clock())))
                        continue
                    except BaseException as exc:
                        if isinstance(exc, LocalVoiceRuntimeError):
                            raise
                        raise LocalVoiceRuntimeError(
                            "VOICE_RUNTIME_PROBE_FAILED",
                            "unexpected ownership probe failure",
                            details={"error": f"{type(exc).__name__}:{exc}"},
                        ) from exc
                    now = self._clock()
                    result = {
                        "ok": True,
                        "started": True,
                        "pid": int(process.pid),
                        "generation": generation,
                        "health": dict(health),
                    }
                    with self._state_lock:
                        self._last_used_at = now
                        self._state = "ready"
                    self.start_reaper()
                    # Commit startup only after every mandatory step,
                    # including reaper creation, has completed.
                    startup_succeeded = True
                    return result
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_START_TIMEOUT",
                    "bundled runtime did not become ready before timeout",
                    details={
                        "last_probe_error": (
                            f"{type(last_unavailable).__name__}:{last_unavailable}"
                            if last_unavailable
                            else ""
                        )
                    },
                )
            except BaseException as exc:
                code = exc.code if isinstance(exc, LocalVoiceRuntimeError) else "VOICE_RUNTIME_START_FAILED"
                self._remember_failure(code, exc)
                if isinstance(exc, LocalVoiceRuntimeError) or not isinstance(exc, Exception):
                    raise
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_START_FAILED",
                    "unexpected failure after acquiring the runtime process handle",
                    details={"error": f"{type(exc).__name__}:{exc}"},
                ) from exc
            finally:
                if process is not None and not startup_succeeded:
                    if owned is None:
                        # Defensive guard for an exception between launch
                        # return and the normal ownership-record assignment.
                        with self._state_lock:
                            self._generation += 1
                            owned = _OwnedRuntime(
                                process=process,
                                instance_secret=secret,
                                generation=self._generation,
                                started_at=0.0,
                                launch_attestation=launch_attestation,
                            )
                            launch_attestation = None
                            self._owned = owned
                    termination_completed = False
                    try:
                        self._terminate_record(owned)
                        termination_completed = True
                    finally:
                        with self._state_lock:
                            # Keep a live/unconfirmed handle so a later
                            # explicit shutdown can retry through the owner.
                            if termination_completed and self._owned is owned:
                                self._lease_counts.pop(owned.generation, None)
                                self._owned = None
                            elif self._owned is None:
                                self._owned = owned
                                self._state = "failed"
                elif launch_attestation is not None:
                    self._release_pending_attestation(launch_attestation)
                    launch_attestation = None

    async def ensure_running_async(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.ensure_running)

    def authorize_loopback_request(
        self,
        *,
        method: str,
        path: str,
        content_length: int,
        body_sha256: str,
    ) -> dict[str, str]:
        """Create a nonce-bound HMAC for a request to the owned child.

        The instance secret never leaves the supervisor.  The child rejects
        replayed nonces, so another local process cannot reuse an observed
        authorization header for a different request body or process instance.
        Callers must hold a synthesis lease while sending the request.
        """
        normalized_method = str(method or "").strip().upper()
        normalized_path = str(path or "").strip()
        normalized_digest = str(body_sha256 or "").strip().lower()
        if (
            isinstance(content_length, bool)
            or not isinstance(content_length, int)
            or not 1 <= content_length <= MAX_AUTHORIZED_REQUEST_BODY_BYTES
        ):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_INVALID",
                "request content length is invalid",
            )
        if normalized_method not in {"POST"}:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_INVALID",
                "only POST requests may be authorized for the voice child",
            )
        if normalized_path not in {"/v1/prepare", "/v1/synthesize"}:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_INVALID",
                "the requested child endpoint is not application-authorized",
            )
        if len(normalized_digest) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_digest
        ):
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_REQUEST_INVALID",
                "request body SHA-256 is invalid",
            )
        with self._state_lock:
            owned = self._owned
            if owned is None or self._state == "stopping" or self._shutdown_requested:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_NOT_RUNNING",
                    "no owned voice runtime is available to authorize the request",
                )
            known, exit_code = self._poll_owned(owned)
            if not known or exit_code is not None:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_NOT_RUNNING",
                    "the owned voice runtime is not alive",
                )
            self._assert_owned_attestation(owned)
            nonce = self._nonce_factory()
            if not nonce:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_NONCE_FAILED",
                    "could not create a fresh runtime request nonce",
                )
            proof = _runtime_request_proof(
                owned.instance_secret,
                nonce=nonce,
                method=normalized_method,
                path=normalized_path,
                body_sha256=normalized_digest,
                service_id=self.plan.service_id,
                runtime_id=self.plan.runtime_id,
                pid=int(owned.process.pid),
            )
            header_proof = _runtime_request_header_proof(
                owned.instance_secret,
                nonce=nonce,
                method=normalized_method,
                path=normalized_path,
                content_length=content_length,
                service_id=self.plan.service_id,
                runtime_id=self.plan.runtime_id,
                pid=int(owned.process.pid),
            )
        return {
            REQUEST_NONCE_HEADER: nonce,
            REQUEST_HEADER_PROOF_HEADER: header_proof,
            REQUEST_PROOF_HEADER: proof,
        }

    def _acquire_synthesis_lease(self) -> _LeaseToken:
        with self._state_lock:
            observed_stop_epoch = self._stop_epoch
            if self._state == "stopping" or self._shutdown_requested:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_STOPPING",
                    "new synthesis leases are rejected while stopping",
                )
        with self._operation_lock:
            with self._state_lock:
                if self._stop_epoch != observed_stop_epoch or self._state == "stopping" or self._shutdown_requested:
                    raise LocalVoiceRuntimeError(
                        "VOICE_RUNTIME_STOPPING",
                        "runtime stopped while the synthesis request was waiting",
                    )
            self.ensure_running()
            with self._state_lock:
                owned = self._owned
                if owned is None or self._state == "stopping":
                    raise LocalVoiceRuntimeError(
                        "VOICE_RUNTIME_NOT_RUNNING",
                        "voice runtime is unavailable for synthesis",
                    )
                poll_known, exit_code = self._poll_owned(owned)
                if not poll_known or exit_code is not None:
                    raise LocalVoiceRuntimeError(
                        "VOICE_RUNTIME_POLL_FAILED" if not poll_known else "VOICE_RUNTIME_NOT_RUNNING",
                        "voice runtime process state is unavailable for synthesis",
                    )
                generation = owned.generation
                self._lease_counts[generation] = self._lease_counts.get(generation, 0) + 1
                self._last_used_at = self._clock()
                self._state = "busy"
                return _LeaseToken(generation=generation)

    def _release_synthesis_lease(self, token: _LeaseToken) -> None:
        with self._state_lock:
            owned = self._owned
            # A finally block from a force-stopped generation cannot release
            # or change any lease accounting for its replacement.
            if owned is None or owned.generation != token.generation:
                return
            count = self._lease_counts.get(token.generation, 0)
            if count <= 0:
                return
            if count == 1:
                self._lease_counts.pop(token.generation, None)
            else:
                self._lease_counts[token.generation] = count - 1
            self._last_used_at = self._clock()
            poll_known, exit_code = self._poll_owned(owned)
            if self._state != "stopping" and poll_known and exit_code is None:
                self._state = "busy" if self._current_lease_count() else "ready"

    @contextmanager
    def synthesis_lease(self) -> Iterator[None]:
        token = self._acquire_synthesis_lease()
        try:
            yield
        finally:
            self._release_synthesis_lease(token)

    @asynccontextmanager
    async def synthesis_lease_async(self):
        token = await asyncio.to_thread(self._acquire_synthesis_lease)
        try:
            yield
        finally:
            self._release_synthesis_lease(token)

    def reap_if_idle(self) -> dict[str, Any]:
        with self._operation_lock:
            process_state = self._passive_clear_exited()
            with self._state_lock:
                owned = self._owned
                if owned is None or process_state in {"absent", "exited"}:
                    return {"stopped": False, "reason": "not_running"}
                if process_state == "unknown":
                    return {"stopped": False, "reason": "poll_failed"}
                if self._current_lease_count():
                    return {"stopped": False, "reason": "active_synthesis"}
                last_used = self._last_used_at
                if last_used is None or self._clock() - last_used < self.plan.idle_ttl_seconds:
                    return {"stopped": False, "reason": "ttl_not_reached"}
            return self.stop_owned(reason="idle_ttl")

    def stop_owned(self, *, reason: str = "shutdown", force: bool = False) -> dict[str, Any]:
        with self._operation_lock:
            with self._state_lock:
                owned = self._owned
                if owned is None:
                    self._owned = None
                    self._state = "stopped"
                    return {"stopped": False, "reason": "no_owned_process"}
                poll_known, exit_code = self._poll_owned(
                    owned,
                    failure_code="VOICE_RUNTIME_TERMINATION_POLL_FAILED",
                )
                if poll_known and exit_code is not None:
                    self._release_exited_owner(owned)
                    self._lease_counts.pop(owned.generation, None)
                    self._owned = None
                    self._state = "stopped"
                    return {"stopped": False, "reason": "no_owned_process"}
                if self._current_lease_count() and not force:
                    return {"stopped": False, "reason": "active_synthesis"}
                self._stop_epoch += 1
                self._state = "stopping"
            try:
                self._terminate_record(owned)
            except BaseException as exc:
                # _terminate_record already records failure and deliberately
                # leaves the handle available for a later retry.
                if isinstance(exc, LocalVoiceRuntimeError):
                    self._remember_failure(exc.code, exc)
                    raise
                error = LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_TERMINATION_UNCONFIRMED",
                    "unexpected process-tree termination failure",
                    details={"pid": int(owned.process.pid), "error": f"{type(exc).__name__}:{exc}"},
                )
                self._remember_failure(error.code, error)
                raise error from exc
            with self._state_lock:
                if self._owned is owned:
                    self._owned = None
                self._lease_counts.pop(owned.generation, None)
                self._state = "stopped"
            return {
                "stopped": True,
                "reason": reason,
                "owned_pid": int(owned.process.pid),
                "generation": owned.generation,
                "external_processes_terminated": False,
            }

    def start_reaper(self) -> None:
        with self._state_lock:
            if self._reaper_thread is not None and self._reaper_thread.is_alive():
                return
            self._reaper_stop.clear()
            thread = threading.Thread(
                target=self._reaper_loop,
                name=f"voice-runtime-reaper-{self.plan.runtime_id}",
                daemon=True,
            )
            self._reaper_thread = thread
            thread.start()

    def _reaper_loop(self) -> None:
        while not self._reaper_stop.wait(self.plan.reaper_interval_seconds):
            try:
                self.reap_if_idle()
            except BaseException as exc:
                self._remember_failure("VOICE_RUNTIME_REAPER_FAILED", exc)

    def shutdown(self) -> dict[str, Any]:
        with self._state_lock:
            self._shutdown_requested = True
            self._stop_epoch += 1
        self._reaper_stop.set()
        thread = self._reaper_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=min(2.0, self.plan.reaper_interval_seconds + 0.1))
        return self.stop_owned(reason="application_shutdown", force=True)


def create_production_local_voice_runtime_supervisor(
    plan: LocalVoiceRuntimePlan,
    *,
    catalog: TrustedRuntimeCatalog,
    identity_probe: RuntimeIdentityProbe | None = None,
    port_in_use: Callable[[str, int], bool] = loopback_port_in_use,
    before_launch: Callable[[], RuntimeLaunchAttestationLease | None] | None = None,
    platform_name: str = os.name,
) -> LocalVoiceRuntimeSupervisor:
    """Build the supported production supervisor with native Windows ownership.

    This factory is intentionally not wired into the application or voice-pack
    routes here.  A caller must explicitly select an attested plan, then opt in
    to this Windows-only runtime boundary.
    """
    if platform_name != "nt":
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_PLATFORM_UNSUPPORTED",
            "bundled local voice runtime supervision is currently supported only on Windows",
        )
    from .windows_voice_runtime_ownership import (
        create_production_windows_runtime_ownership,
    )

    dependencies = create_production_windows_runtime_ownership(
        platform_name=platform_name,
    )
    return LocalVoiceRuntimeSupervisor(
        plan,
        catalog=catalog,
        launcher=dependencies.launcher,
        identity_probe=identity_probe,
        listener_verifier=dependencies.listener_verifier,
        process_tree_owner=dependencies.process_tree_owner,
        port_in_use=port_in_use,
        before_launch=before_launch,
        platform_name=platform_name,
    )


def runtime_health_payload(
    *,
    runtime_id: str,
    service_id: str,
    nonce: str,
    ready: bool,
    listener_pid: int | None = None,
) -> dict[str, Any]:
    """Build a nonce-bound child response without returning the secret."""
    secret = str(os.getenv(INSTANCE_SECRET_ENV) or "")
    pid = int(listener_pid if listener_pid is not None else os.getpid())
    proof = (
        _hmac_proof(
            secret,
            nonce=nonce,
            service_id=service_id,
            runtime_id=runtime_id,
            pid=pid,
            ready=bool(ready),
        )
        if secret and nonce
        else ""
    )
    return {
        "schema": RUNTIME_HEALTH_SCHEMA,
        "runtime_id": runtime_id,
        "service_id": service_id,
        "pid": pid,
        "listener_pid": pid,
        "ready": bool(ready),
        "proof": proof,
    }

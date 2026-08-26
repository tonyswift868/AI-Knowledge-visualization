"""Replaceable host adapters for the staging verification runtime."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .models import (
    CommandRecord,
    EnterpriseStagingEvidenceBundle,
    VerificationStatus,
    command_fingerprint,
    output_fingerprint,
    sanitize_evidence,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class CommandExecution:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    started_at: str
    finished_at: str
    duration_ms: int


class CommandRunner(Protocol):
    def which(self, executable: str) -> str | None: ...

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 60,
    ) -> CommandExecution: ...


class SystemCommandRunner:
    def which(self, executable: str) -> str | None:
        return shutil.which(executable)

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 60,
    ) -> CommandExecution:
        command = tuple(str(item) for item in argv)
        if not command:
            raise ValueError("command must not be empty")
        started_at = utc_now()
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                check=False,
                env={**os.environ, **dict(env or {})},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, int(timeout_seconds)),
            )
            exit_code = int(completed.returncode)
            stdout = completed.stdout
            stderr = completed.stderr
        except FileNotFoundError:
            exit_code, stdout, stderr = 127, "", "COMMAND_NOT_FOUND"
        except subprocess.TimeoutExpired:
            exit_code, stdout, stderr = 124, "", "COMMAND_TIMEOUT"
        return CommandExecution(
            argv=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at,
            finished_at=utc_now(),
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: str
    headers: Mapping[str, str]
    error_code: str | None = None


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        timeout_seconds: float = 5.0,
    ) -> HttpResponse: ...


class UrllibHttpClient:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        timeout_seconds: float = 5.0,
    ) -> HttpResponse:
        payload = None
        request_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        if json_body is not None:
            payload = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(
            str(url),
            data=payload,
            headers=request_headers,
            method=str(method).upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=max(0.1, timeout_seconds)) as response:
                body = response.read(2 * 1024 * 1024).decode("utf-8", "replace")
                return HttpResponse(
                    int(response.status),
                    body,
                    {str(key): str(value) for key, value in response.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            body = exc.read(2 * 1024 * 1024).decode("utf-8", "replace")
            return HttpResponse(int(exc.code), body, {}, "HTTP_ERROR")
        except (urllib.error.URLError, TimeoutError, OSError):
            return HttpResponse(0, "", {}, "ENDPOINT_UNREACHABLE")


class TcpProber(Protocol):
    def reachable(self, host: str, port: int, timeout_seconds: float = 2.0) -> bool: ...


class SocketTcpProber:
    def reachable(self, host: str, port: int, timeout_seconds: float = 2.0) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=max(0.1, timeout_seconds)):
                return True
        except OSError:
            return False


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


def record_command(
    bundle: EnterpriseStagingEvidenceBundle,
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = 60,
    capture_version: bool = False,
) -> CommandExecution:
    execution = runner.run(argv, env=env, timeout_seconds=timeout_seconds)
    command_id = f"command-{len(bundle.commands) + 1:03d}"
    version_summary = None
    if capture_version and execution.exit_code == 0:
        first_line = next(
            (line.strip() for line in execution.stdout.splitlines() if line.strip()),
            "",
        )[:240]
        version_summary = sanitize_evidence(first_line) if first_line else None
    record = CommandRecord(
        command_id=command_id,
        executable=Path(str(argv[0])).name[:128],
        argv_sha256=command_fingerprint(argv),
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        duration_ms=execution.duration_ms,
        exit_code=execution.exit_code,
        status=(VerificationStatus.PASS if execution.exit_code == 0 else VerificationStatus.FAIL),
        output_sha256=output_fingerprint(execution.stdout, execution.stderr),
        version_summary=version_summary if isinstance(version_summary, str) else None,
    )
    bundle.commands.append(record)
    return execution


__all__ = [
    "Clock",
    "CommandExecution",
    "CommandRunner",
    "HttpClient",
    "HttpResponse",
    "SocketTcpProber",
    "SystemClock",
    "SystemCommandRunner",
    "TcpProber",
    "UrllibHttpClient",
    "record_command",
]

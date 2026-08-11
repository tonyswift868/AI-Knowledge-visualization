"""Native Windows ownership primitives for local voice runtime processes.

This module deliberately uses retained process and Job Object handles.  It
never discovers or terminates a process by port, command line, or executable
name.  TCP-table inspection is verification-only: the listener PID must match
the exact child PID already returned by ``subprocess.Popen``.
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

from ctypes import wintypes

from .local_voice_runtime_supervisor import (
    LOOPBACK_HOST,
    LocalVoiceRuntimeError,
    LocalVoiceRuntimePlan,
    OwnedProcess,
    RuntimeLauncher,
)


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_NO_WINDOW = 0x08000000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
STILL_ACTIVE = 259
AF_INET = 2
TCP_TABLE_OWNER_PID_LISTENER = 3
NO_ERROR = 0
ERROR_INSUFFICIENT_BUFFER = 122
_MAX_TCP_TABLE_BYTES = 16 * 1024 * 1024


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


@dataclass(frozen=True)
class WindowsTcpListener:
    local_address: str
    local_port: int
    owning_pid: int


@dataclass
class WindowsJobAttributeList:
    buffer: object
    job_handles: object
    pointer: int
    deleted: bool = False


@dataclass
class SuspendedWindowsChild:
    process: OwnedProcess
    thread_handle: int


class WindowsRuntimeApi(Protocol):
    """Narrow injectable surface around the required Win32 calls."""

    def create_job_object(self) -> int: ...

    def set_kill_on_job_close(self, job_handle: int) -> None: ...

    def close_handle(self, handle: int) -> None: ...

    def create_job_attribute_list(self, job_handle: int) -> WindowsJobAttributeList: ...

    def delete_job_attribute_list(self, attributes: WindowsJobAttributeList) -> None: ...

    def create_process_suspended(
        self,
        attributes: WindowsJobAttributeList,
        plan: LocalVoiceRuntimePlan,
        environment: Mapping[str, str],
    ) -> SuspendedWindowsChild: ...

    def resume_thread(self, thread_handle: int) -> None: ...

    def ipv4_tcp_listeners(self) -> Sequence[WindowsTcpListener]: ...


def _system_error(operation: str, code: int) -> OSError:
    try:
        message = ctypes.FormatError(int(code)).strip()
    except BaseException:
        message = "Windows system error"
    return OSError(int(code), f"{operation} failed: {message}")


def _handle_value(raw_handle: object) -> int:
    if raw_handle is None:
        return 0
    if isinstance(raw_handle, int):
        return int(raw_handle)
    value = getattr(raw_handle, "value", None)
    if value is not None:
        return int(value or 0)
    try:
        return int(raw_handle)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class CtypesWindowsRuntimeApi:
    """Production ctypes adapter for Job Objects and the IPv4 owner-PID table."""

    def __init__(self, *, platform_name: str = os.name) -> None:
        if platform_name != "nt":
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_PLATFORM_UNSUPPORTED",
                "native local voice runtime ownership is supported only on Windows",
            )
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_WINDOWS_API_UNAVAILABLE",
                "required Windows process-ownership APIs are unavailable",
                details={"error": f"{type(exc).__name__}:{exc}"},
            ) from exc

        self._create_job_object = kernel32.CreateJobObjectW
        self._create_job_object.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self._create_job_object.restype = wintypes.HANDLE

        self._set_information_job_object = kernel32.SetInformationJobObject
        self._set_information_job_object.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        self._set_information_job_object.restype = wintypes.BOOL

        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = (wintypes.HANDLE,)
        self._close_handle.restype = wintypes.BOOL

        self._initialize_proc_thread_attribute_list = (
            kernel32.InitializeProcThreadAttributeList
        )
        self._initialize_proc_thread_attribute_list.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        )
        self._initialize_proc_thread_attribute_list.restype = wintypes.BOOL

        self._update_proc_thread_attribute = kernel32.UpdateProcThreadAttribute
        self._update_proc_thread_attribute.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
        )
        self._update_proc_thread_attribute.restype = wintypes.BOOL

        self._delete_proc_thread_attribute_list = (
            kernel32.DeleteProcThreadAttributeList
        )
        self._delete_proc_thread_attribute_list.argtypes = (ctypes.c_void_p,)
        self._delete_proc_thread_attribute_list.restype = None

        self._create_process = kernel32.CreateProcessW
        self._create_process.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOEXW),
            ctypes.POINTER(_PROCESS_INFORMATION),
        )
        self._create_process.restype = wintypes.BOOL

        self._resume_thread = kernel32.ResumeThread
        self._resume_thread.argtypes = (wintypes.HANDLE,)
        self._resume_thread.restype = wintypes.DWORD

        self._terminate_process = kernel32.TerminateProcess
        self._terminate_process.argtypes = (wintypes.HANDLE, wintypes.UINT)
        self._terminate_process.restype = wintypes.BOOL

        self._wait_for_single_object = kernel32.WaitForSingleObject
        self._wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._wait_for_single_object.restype = wintypes.DWORD

        self._get_exit_code_process = kernel32.GetExitCodeProcess
        self._get_exit_code_process.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._get_exit_code_process.restype = wintypes.BOOL

        self._get_extended_tcp_table = iphlpapi.GetExtendedTcpTable
        self._get_extended_tcp_table.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.ULONG),
            wintypes.BOOL,
            wintypes.ULONG,
            ctypes.c_int,
            wintypes.ULONG,
        )
        self._get_extended_tcp_table.restype = wintypes.ULONG

    @staticmethod
    def _last_error(operation: str) -> OSError:
        return _system_error(operation, int(ctypes.get_last_error() or 1))

    def create_job_object(self) -> int:
        handle = _handle_value(self._create_job_object(None, None))
        if not handle:
            raise self._last_error("CreateJobObjectW")
        return handle

    def set_kill_on_job_close(self, job_handle: int) -> None:
        information = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        succeeded = self._set_information_job_object(
            wintypes.HANDLE(int(job_handle)),
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        if not succeeded:
            raise self._last_error("SetInformationJobObject")

    def close_handle(self, handle: int) -> None:
        if not self._close_handle(wintypes.HANDLE(int(handle))):
            raise self._last_error("CloseHandle")

    def create_job_attribute_list(self, job_handle: int) -> WindowsJobAttributeList:
        required = ctypes.c_size_t(0)
        ctypes.set_last_error(0)
        initialized = self._initialize_proc_thread_attribute_list(
            None,
            1,
            0,
            ctypes.byref(required),
        )
        first_error = int(ctypes.get_last_error())
        if initialized or first_error != ERROR_INSUFFICIENT_BUFFER or required.value <= 0:
            raise _system_error(
                "InitializeProcThreadAttributeList(size)",
                first_error or 1,
            )
        if int(required.value) > 1024 * 1024:
            raise ValueError("process attribute list requested an unsafe buffer size")

        buffer = ctypes.create_string_buffer(int(required.value))
        pointer = int(ctypes.cast(buffer, ctypes.c_void_p).value or 0)
        if not pointer or not self._initialize_proc_thread_attribute_list(
            ctypes.c_void_p(pointer),
            1,
            0,
            ctypes.byref(required),
        ):
            raise self._last_error("InitializeProcThreadAttributeList(data)")

        job_handles = (wintypes.HANDLE * 1)(wintypes.HANDLE(int(job_handle)))
        attributes = WindowsJobAttributeList(
            buffer=buffer,
            job_handles=job_handles,
            pointer=pointer,
        )
        if not self._update_proc_thread_attribute(
            ctypes.c_void_p(pointer),
            0,
            PROC_THREAD_ATTRIBUTE_JOB_LIST,
            ctypes.cast(job_handles, ctypes.c_void_p),
            ctypes.sizeof(job_handles),
            None,
            None,
        ):
            error = self._last_error("UpdateProcThreadAttribute(JOB_LIST)")
            self.delete_job_attribute_list(attributes)
            raise error
        return attributes

    def delete_job_attribute_list(self, attributes: WindowsJobAttributeList) -> None:
        if attributes.deleted:
            return
        if attributes.pointer:
            self._delete_proc_thread_attribute_list(
                ctypes.c_void_p(int(attributes.pointer))
            )
        attributes.deleted = True

    @staticmethod
    def _environment_block(environment: Mapping[str, str]) -> ctypes.Array[ctypes.c_wchar]:
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_key, raw_value in environment.items():
            key = str(raw_key)
            value = str(raw_value)
            if not key or "=" in key or "\x00" in key or "\x00" in value:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_ENVIRONMENT_INVALID",
                    "child environment contains an invalid key or value",
                )
            normalized = key.casefold()
            if normalized in seen:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_ENVIRONMENT_INVALID",
                    "child environment contains duplicate case-insensitive keys",
                )
            seen.add(normalized)
            entries.append((key, value))
        entries.sort(key=lambda item: item[0].casefold())
        # create_unicode_buffer contributes the second terminating NUL.
        text = "\x00".join(f"{key}={value}" for key, value in entries) + "\x00"
        return ctypes.create_unicode_buffer(text)

    def create_process_suspended(
        self,
        attributes: WindowsJobAttributeList,
        plan: LocalVoiceRuntimePlan,
        environment: Mapping[str, str],
    ) -> SuspendedWindowsChild:
        if attributes.deleted or not attributes.pointer:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_WINDOWS_ATTRIBUTE_INVALID",
                "the creation-time Job attribute list is unavailable",
            )
        arguments = [str(plan.executable), *map(str, plan.arguments)]
        command_line = subprocess.list2cmdline(arguments)
        if len(command_line) + 1 > 32_767:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_COMMAND_LINE_TOO_LONG",
                "the local voice runtime command line exceeds the Windows limit",
            )
        mutable_command_line = ctypes.create_unicode_buffer(command_line)
        environment_block = self._environment_block(environment)
        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        startup.lpAttributeList = int(attributes.pointer)
        process_information = _PROCESS_INFORMATION()
        flags = (
            CREATE_SUSPENDED
            | CREATE_UNICODE_ENVIRONMENT
            | EXTENDED_STARTUPINFO_PRESENT
            | CREATE_NO_WINDOW
        )
        created = self._create_process(
            str(plan.executable),
            mutable_command_line,
            None,
            None,
            False,
            flags,
            environment_block,
            str(plan.working_directory),
            ctypes.byref(startup),
            ctypes.byref(process_information),
        )
        if not created:
            raise self._last_error("CreateProcessW")

        process_handle = _handle_value(process_information.hProcess)
        thread_handle = _handle_value(process_information.hThread)
        pid = int(process_information.dwProcessId)
        if not process_handle or not thread_handle or pid <= 0:
            if process_handle:
                try:
                    self._terminate_native_process(process_handle, 1)
                finally:
                    self.close_handle(process_handle)
            if thread_handle:
                self.close_handle(thread_handle)
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_WINDOWS_CREATE_FAILED",
                "CreateProcessW returned incomplete retained handles",
            )
        process = WindowsNativeOwnedProcess(self, pid=pid, process_handle=process_handle)
        return SuspendedWindowsChild(process=process, thread_handle=thread_handle)

    def resume_thread(self, thread_handle: int) -> None:
        previous_suspend_count = int(
            self._resume_thread(wintypes.HANDLE(int(thread_handle)))
        )
        if previous_suspend_count == 0xFFFFFFFF:
            raise self._last_error("ResumeThread")

    def _terminate_native_process(self, process_handle: int, exit_code: int) -> None:
        if not self._terminate_process(
            wintypes.HANDLE(int(process_handle)),
            int(exit_code),
        ):
            error_code = int(ctypes.get_last_error())
            # ERROR_ACCESS_DENIED commonly means the process already exited;
            # the retained handle wait below remains authoritative.
            if error_code != 5:
                raise _system_error("TerminateProcess", error_code or 1)

    def terminate_native_process(self, process_handle: int, exit_code: int = 1) -> None:
        self._terminate_native_process(process_handle, exit_code)

    def wait_native_process(
        self,
        process_handle: int,
        timeout_seconds: float | None,
    ) -> int | None:
        if timeout_seconds is None:
            milliseconds = INFINITE
        else:
            milliseconds = min(
                INFINITE - 1,
                max(0, int(float(timeout_seconds) * 1000 + 0.999)),
            )
        result = int(
            self._wait_for_single_object(
                wintypes.HANDLE(int(process_handle)),
                milliseconds,
            )
        )
        if result == WAIT_TIMEOUT:
            return None
        if result == WAIT_FAILED:
            raise self._last_error("WaitForSingleObject")
        if result != WAIT_OBJECT_0:
            raise _system_error("WaitForSingleObject", result)
        exit_code = wintypes.DWORD(0)
        if not self._get_exit_code_process(
            wintypes.HANDLE(int(process_handle)),
            ctypes.byref(exit_code),
        ):
            raise self._last_error("GetExitCodeProcess")
        return int(exit_code.value)

    @staticmethod
    def _parse_tcp_table(buffer: ctypes.Array[ctypes.c_char], used_bytes: int) -> list[WindowsTcpListener]:
        header_size = ctypes.sizeof(wintypes.DWORD)
        row_size = ctypes.sizeof(_MIB_TCPROW_OWNER_PID)
        if int(used_bytes) < header_size:
            raise ValueError("TCP owner-PID table is shorter than its header")
        entry_count = int(wintypes.DWORD.from_buffer_copy(buffer.raw[:header_size]).value)
        maximum_rows = (int(used_bytes) - header_size) // row_size
        if entry_count > maximum_rows:
            raise ValueError("TCP owner-PID table entry count exceeds its buffer")

        listeners: list[WindowsTcpListener] = []
        for index in range(entry_count):
            offset = header_size + index * row_size
            row = _MIB_TCPROW_OWNER_PID.from_buffer_copy(
                buffer.raw[offset : offset + row_size]
            )
            address_bytes = int(row.dwLocalAddr).to_bytes(4, byteorder="little")
            listeners.append(
                WindowsTcpListener(
                    local_address=socket.inet_ntoa(address_bytes),
                    local_port=socket.ntohs(int(row.dwLocalPort) & 0xFFFF),
                    owning_pid=int(row.dwOwningPid),
                )
            )
        return listeners

    def ipv4_tcp_listeners(self) -> list[WindowsTcpListener]:
        size = wintypes.ULONG(0)
        result = int(
            self._get_extended_tcp_table(
                None,
                ctypes.byref(size),
                False,
                AF_INET,
                TCP_TABLE_OWNER_PID_LISTENER,
                0,
            )
        )
        if result == NO_ERROR and int(size.value) == 0:
            return []
        if result != ERROR_INSUFFICIENT_BUFFER:
            raise _system_error("GetExtendedTcpTable(size)", result)

        for _attempt in range(3):
            requested = int(size.value)
            if requested < ctypes.sizeof(wintypes.DWORD) or requested > _MAX_TCP_TABLE_BYTES:
                raise ValueError("GetExtendedTcpTable returned an unsafe buffer size")
            buffer = ctypes.create_string_buffer(requested)
            available = wintypes.ULONG(requested)
            result = int(
                self._get_extended_tcp_table(
                    buffer,
                    ctypes.byref(available),
                    False,
                    AF_INET,
                    TCP_TABLE_OWNER_PID_LISTENER,
                    0,
                )
            )
            if result == ERROR_INSUFFICIENT_BUFFER:
                size.value = available.value
                continue
            if result != NO_ERROR:
                raise _system_error("GetExtendedTcpTable(data)", result)
            used_bytes = min(requested, int(available.value or requested))
            return self._parse_tcp_table(buffer, used_bytes)
        raise _system_error("GetExtendedTcpTable(resize)", ERROR_INSUFFICIENT_BUFFER)


class WindowsNativeOwnedProcess:
    """A minimal Popen-compatible wrapper around one retained process handle."""

    def __init__(
        self,
        api: CtypesWindowsRuntimeApi,
        *,
        pid: int,
        process_handle: int,
    ) -> None:
        self.pid = int(pid)
        self._handle = int(process_handle)
        self._api = api
        self._lock = threading.RLock()
        self._closed = False
        self._return_code: int | None = None

    def _open_handle(self) -> int:
        with self._lock:
            if self._closed or self._handle <= 0:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_PROCESS_HANDLE_CLOSED",
                    "the retained local voice process handle is already closed",
                    details={"pid": self.pid},
                )
            return self._handle

    def poll(self) -> int | None:
        with self._lock:
            if self._return_code is not None:
                return self._return_code
            handle = self._open_handle()
            exit_code = self._api.wait_native_process(handle, 0.0)
            if exit_code is not None:
                self._return_code = int(exit_code)
            return self._return_code

    def wait(self, timeout: float | None = None) -> int:
        with self._lock:
            if self._return_code is not None:
                return self._return_code
            handle = self._open_handle()
            exit_code = self._api.wait_native_process(handle, timeout)
            if exit_code is None:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            self._return_code = int(exit_code)
            return self._return_code

    def terminate(self) -> None:
        self.kill()

    def kill(self) -> None:
        with self._lock:
            if self._return_code is not None:
                return
            self._api.terminate_native_process(self._open_handle(), 1)

    def close_native_handle(self) -> None:
        with self._lock:
            if self._closed:
                return
            handle = self._handle
            self._api.close_handle(handle)
            self._closed = True
            self._handle = 0


class WindowsAtomicRuntimeLauncher:
    """Creation-time Job association; no child instruction runs unowned."""

    def __init__(self, api: WindowsRuntimeApi) -> None:
        self._api = api

    def launch(
        self,
        plan: LocalVoiceRuntimePlan,
        *,
        environment: Mapping[str, str],
    ) -> OwnedProcess:
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_ATOMIC_JOB_CONTEXT_REQUIRED",
            "Windows runtime creation requires a preconfigured Job Object",
        )

    @staticmethod
    def _close_process_handle(process: OwnedProcess) -> None:
        close_native_handle = getattr(process, "close_native_handle", None)
        if callable(close_native_handle):
            close_native_handle()

    def launch_in_job(
        self,
        job_handle: int,
        plan: LocalVoiceRuntimePlan,
        *,
        environment: Mapping[str, str],
        pre_resume: Callable[[], None] | None = None,
    ) -> OwnedProcess:
        attributes: WindowsJobAttributeList | None = None
        child: SuspendedWindowsChild | None = None
        thread_open = False
        phase = "attribute"
        cleanup_errors: list[str] = []
        try:
            attributes = self._api.create_job_attribute_list(job_handle)
            phase = "create"
            child = self._api.create_process_suspended(attributes, plan, environment)
            thread_open = True
            # The Job list has already been consumed by CreateProcessW.  It is
            # safe to delete before resuming the still-suspended main thread.
            self._api.delete_job_attribute_list(attributes)
            attributes = None
            phase = "pre_resume"
            if pre_resume is not None:
                pre_resume()
            phase = "resume"
            self._api.resume_thread(child.thread_handle)
            phase = "thread_handle_close"
            self._api.close_handle(child.thread_handle)
            thread_open = False
            return child.process
        except BaseException as exc:
            if attributes is not None:
                try:
                    self._api.delete_job_attribute_list(attributes)
                except BaseException as cleanup_exc:
                    cleanup_errors.append(
                        f"DeleteAttributeList:{type(cleanup_exc).__name__}:{cleanup_exc}"
                    )
            if child is not None:
                try:
                    child.process.kill()
                except BaseException as cleanup_exc:
                    cleanup_errors.append(
                        f"TerminateProcess:{type(cleanup_exc).__name__}:{cleanup_exc}"
                    )
                try:
                    child.process.wait(timeout=5.0)
                except BaseException as cleanup_exc:
                    cleanup_errors.append(
                        f"WaitProcess:{type(cleanup_exc).__name__}:{cleanup_exc}"
                    )
                if thread_open:
                    try:
                        self._api.close_handle(child.thread_handle)
                    except BaseException as cleanup_exc:
                        cleanup_errors.append(
                            f"CloseThread:{type(cleanup_exc).__name__}:{cleanup_exc}"
                        )
                try:
                    self._close_process_handle(child.process)
                except BaseException as cleanup_exc:
                    cleanup_errors.append(
                        f"CloseProcess:{type(cleanup_exc).__name__}:{cleanup_exc}"
                    )
            code = {
                "attribute": "VOICE_RUNTIME_WINDOWS_ATTRIBUTE_SETUP_FAILED",
                "create": "VOICE_RUNTIME_WINDOWS_CREATE_FAILED",
                "pre_resume": "VOICE_RUNTIME_PRELAUNCH_VERIFY_FAILED",
                "resume": "VOICE_RUNTIME_WINDOWS_RESUME_FAILED",
                "thread_handle_close": "VOICE_RUNTIME_WINDOWS_THREAD_CLOSE_FAILED",
            }[phase]
            if isinstance(exc, LocalVoiceRuntimeError) and exc.code.startswith(
                "VOICE_RUNTIME_WINDOWS_"
            ):
                code = exc.code
            raise LocalVoiceRuntimeError(
                code,
                "atomic Windows local voice runtime creation failed closed",
                details={
                    "phase": phase,
                    "error": f"{type(exc).__name__}:{exc}",
                    "cleanup_errors": cleanup_errors,
                },
            ) from exc


@dataclass
class _JobRecord:
    process: OwnedProcess
    pid: int
    job_handle: int


class WindowsJobObjectProcessTreeOwner:
    """Own exact launched process trees through kill-on-close Job Objects."""

    def __init__(self, api: WindowsRuntimeApi) -> None:
        self._api = api
        self._records: dict[int, _JobRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _record_key(process: OwnedProcess) -> int:
        return id(process)

    @staticmethod
    def _kill_exact_child(process: OwnedProcess, *, timeout_seconds: float = 5.0) -> None:
        try:
            if process.poll() is not None:
                return
        except BaseException:
            pass
        process.kill()
        process.wait(timeout=timeout_seconds)

    def launch(
        self,
        launcher: RuntimeLauncher,
        plan: LocalVoiceRuntimePlan,
        *,
        environment: Mapping[str, str],
        pre_resume: Callable[[], None] | None = None,
    ) -> OwnedProcess:
        job_handle = 0
        process: OwnedProcess | None = None
        atomic_child_returned = False
        cleanup_errors: list[str] = []
        try:
            atomic_launch = getattr(launcher, "launch_in_job", None)
            if not callable(atomic_launch):
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_ATOMIC_LAUNCHER_REQUIRED",
                    "Windows runtime launch must use creation-time Job association",
                )
            job_handle = int(self._api.create_job_object())
            if job_handle <= 0:
                raise RuntimeError("CreateJobObject returned an invalid handle")
            self._api.set_kill_on_job_close(job_handle)
            process = atomic_launch(
                job_handle,
                plan,
                environment=environment,
                pre_resume=pre_resume,
            )
            atomic_child_returned = True
            record = _JobRecord(
                process=process,
                pid=int(process.pid),
                job_handle=job_handle,
            )
            with self._lock:
                key = self._record_key(process)
                if key in self._records:
                    raise RuntimeError("duplicate retained process object")
                self._records[key] = record
            return process
        except BaseException as exc:
            if job_handle:
                try:
                    # A returned child was associated at CreateProcessW time;
                    # closing the only retained Job handle kills its tree.
                    self._api.close_handle(job_handle)
                except BaseException as cleanup_exc:
                    cleanup_errors.append(f"CloseHandle:{type(cleanup_exc).__name__}:{cleanup_exc}")
            if process is not None:
                try:
                    self._kill_exact_child(process)
                except BaseException as cleanup_exc:
                    cleanup_errors.append(
                        f"KillExactChild:{type(cleanup_exc).__name__}:{cleanup_exc}"
                    )
                close_native_handle = getattr(process, "close_native_handle", None)
                if callable(close_native_handle):
                    try:
                        close_native_handle()
                    except BaseException as cleanup_exc:
                        cleanup_errors.append(
                            f"CloseProcess:{type(cleanup_exc).__name__}:{cleanup_exc}"
                        )
            if isinstance(exc, LocalVoiceRuntimeError):
                original = exc
            else:
                original = LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_WINDOWS_JOB_SETUP_FAILED",
                    "could not place the launched child in its kill-on-close Job Object",
                    details={
                        "atomic_child_returned": atomic_child_returned,
                        "error": f"{type(exc).__name__}:{exc}",
                        "cleanup_errors": cleanup_errors,
                    },
                )
            if cleanup_errors and isinstance(exc, LocalVoiceRuntimeError):
                raise LocalVoiceRuntimeError(
                    original.code,
                    original.message,
                    details={**original.details, "cleanup_errors": cleanup_errors},
                ) from exc
            raise original from exc

    def owns_pid(self, process: OwnedProcess, pid: int) -> bool:
        with self._lock:
            record = self._records.get(self._record_key(process))
            if (
                record is None
                or record.process is not process
                or record.job_handle <= 0
                or record.pid != int(pid)
                or int(process.pid) != int(pid)
            ):
                return False
        try:
            return process.poll() is None
        except BaseException:
            return False

    def _close_job(self, process: OwnedProcess) -> bool:
        key = self._record_key(process)
        with self._lock:
            record = self._records.get(key)
            if record is None or record.process is not process:
                return False
            if record.job_handle <= 0:
                return True
            try:
                self._api.close_handle(record.job_handle)
            except BaseException as exc:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_WINDOWS_JOB_CLOSE_FAILED",
                    "the owned runtime Job Object handle could not be closed",
                    details={
                        "pid": record.pid,
                        "error": f"{type(exc).__name__}:{exc}",
                    },
                ) from exc
            record.job_handle = 0
            return True

    def release(self, process: OwnedProcess) -> None:
        """Release a Job handle after the exact child has already exited."""
        try:
            exit_code = process.poll()
        except BaseException as exc:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_WINDOWS_JOB_RELEASE_FAILED",
                "could not confirm child exit before releasing its Job Object",
                details={"pid": int(process.pid)},
            ) from exc
        if exit_code is None:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_WINDOWS_JOB_RELEASE_FAILED",
                "refusing to release a Job Object while its child is still running",
                details={"pid": int(process.pid)},
            )
        self._close_job(process)
        close_native_handle = getattr(process, "close_native_handle", None)
        if callable(close_native_handle):
            try:
                close_native_handle()
            except BaseException as exc:
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_PROCESS_HANDLE_CLOSE_FAILED",
                    "the exited local voice process handle could not be closed",
                    details={
                        "pid": int(process.pid),
                        "error": f"{type(exc).__name__}:{exc}",
                    },
                ) from exc
        with self._lock:
            record = self._records.get(self._record_key(process))
            if record is not None and record.process is process:
                self._records.pop(self._record_key(process), None)

    def terminate(self, process: OwnedProcess, *, timeout_seconds: float) -> bool:
        closed = self._close_job(process)
        if not closed:
            try:
                return process.poll() is not None
            except BaseException:
                return False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            # The Job close is authoritative for the tree.  Retain the exact
            # Popen handle as a final direct-child fallback; never look up a PID.
            self._kill_exact_child(process, timeout_seconds=timeout_seconds)
        try:
            return process.poll() is not None
        except BaseException:
            return False

    def close(self) -> None:
        """Close every still-owned Job handle; safe to call repeatedly."""
        with self._lock:
            processes = [record.process for record in self._records.values()]
        failures: list[str] = []
        for process in processes:
            try:
                self._close_job(process)
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    self._kill_exact_child(process)
                self.release(process)
            except BaseException as exc:
                failures.append(f"pid={int(process.pid)}:{type(exc).__name__}:{exc}")
        if failures:
            raise LocalVoiceRuntimeError(
                "VOICE_RUNTIME_WINDOWS_JOB_CLOSE_FAILED",
                "one or more owned Job Object handles could not be closed",
                details={"failures": failures},
            )


class WindowsTcpListenerOwnershipVerifier:
    """Verify exact loopback listener ownership via GetExtendedTcpTable."""

    def __init__(
        self,
        api: WindowsRuntimeApi,
        *,
        attempts: int = 3,
        retry_delay_seconds: float = 0.04,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= int(attempts) <= 10:
            raise ValueError("attempts must be between 1 and 10")
        if not 0.0 <= float(retry_delay_seconds) <= 1.0:
            raise ValueError("retry_delay_seconds must be between 0 and 1")
        self._api = api
        self._attempts = int(attempts)
        self._retry_delay_seconds = float(retry_delay_seconds)
        self._sleep = sleep

    def verify(self, *, host: str, port: int, listener_pid: int) -> bool:
        if host != LOOPBACK_HOST or not 1 <= int(port) <= 65535 or int(listener_pid) <= 0:
            return False
        for attempt in range(self._attempts):
            try:
                rows = self._api.ipv4_tcp_listeners()
            except BaseException as exc:
                if isinstance(exc, LocalVoiceRuntimeError):
                    raise
                raise LocalVoiceRuntimeError(
                    "VOICE_RUNTIME_TCP_TABLE_QUERY_FAILED",
                    "Windows could not verify the local voice listener owner",
                    details={"error": f"{type(exc).__name__}:{exc}"},
                ) from exc
            matching_pids = {
                int(row.owning_pid)
                for row in rows
                if row.local_address == LOOPBACK_HOST and int(row.local_port) == int(port)
            }
            # A duplicate or ambiguous listener set is not accepted either.
            if matching_pids == {int(listener_pid)}:
                return True
            if attempt + 1 < self._attempts and self._retry_delay_seconds:
                self._sleep(self._retry_delay_seconds)
        return False


@dataclass(frozen=True)
class WindowsRuntimeOwnershipDependencies:
    process_tree_owner: WindowsJobObjectProcessTreeOwner
    listener_verifier: WindowsTcpListenerOwnershipVerifier
    launcher: WindowsAtomicRuntimeLauncher


def create_production_windows_runtime_ownership(
    *,
    platform_name: str = os.name,
    api: WindowsRuntimeApi | None = None,
) -> WindowsRuntimeOwnershipDependencies:
    """Create production ownership dependencies, explicitly Windows-only."""
    if platform_name != "nt":
        raise LocalVoiceRuntimeError(
            "VOICE_RUNTIME_PLATFORM_UNSUPPORTED",
            "native local voice runtime ownership is supported only on Windows",
        )
    resolved_api = api or CtypesWindowsRuntimeApi(platform_name=platform_name)
    return WindowsRuntimeOwnershipDependencies(
        process_tree_owner=WindowsJobObjectProcessTreeOwner(resolved_api),
        listener_verifier=WindowsTcpListenerOwnershipVerifier(resolved_api),
        launcher=WindowsAtomicRuntimeLauncher(resolved_api),
    )

"""Generation-scoped launch attestation for a verified Qwen engine tree.

On Windows every listed file is opened once without FILE_SHARE_WRITE or
FILE_SHARE_DELETE, then its FileId, size and SHA-256 are read from that same
retained handle.  Listed directory handles are retained too.  A recursive
ReadDirectoryChangesW monitor detects name, directory, data, attribute and
security changes; the supervisor performs an exact inventory seal only after
the child exists suspended in its Job Object and immediately before resume.

This boundary blocks ordinary later opens that request write/delete access and
detects directory additions before the suspended-child gate.  It deliberately
does not claim protection from an adversarial process running as the same
security principal, including one that can precisely race directory additions,
inject into this process, duplicate/close its handles, change ownership/DACL,
or kill the monitor; privileged and kernel attackers are outside the boundary
too.  Directory notifications are detection, not a filesystem transaction;
the pre-resume inventory and isolated child protocol are required in addition
to the monitor.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import threading
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .local_voice_runtime_supervisor import LocalVoiceRuntimeError
from .teacher_voice_pack import VerifiedQwenEngineRegistrationCapability


_REPARSE_ATTRIBUTE = 0x00000400
_DIRECTORY_ATTRIBUTE = 0x00000010
_GENERIC_READ = 0x80000000
_FILE_LIST_DIRECTORY = 0x00000001
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_OVERLAPPED = 0x40000000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_WATCH_FILTER = (
    0x00000001  # FILE_NOTIFY_CHANGE_FILE_NAME
    | 0x00000002  # FILE_NOTIFY_CHANGE_DIR_NAME
    | 0x00000004  # FILE_NOTIFY_CHANGE_ATTRIBUTES
    | 0x00000008  # FILE_NOTIFY_CHANGE_SIZE
    | 0x00000010  # FILE_NOTIFY_CHANGE_LAST_WRITE
    | 0x00000040  # FILE_NOTIFY_CHANGE_CREATION
    | 0x00000100  # FILE_NOTIFY_CHANGE_SECURITY
)


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


@dataclass(frozen=True)
class _Identity:
    volume_serial: int
    file_id: int
    size: int
    links: int
    attributes: int


@dataclass(frozen=True)
class _WindowsHeldObject:
    handle: int
    identity: _Identity
    directory: bool


if os.name == "nt":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _KERNEL32.ReadFile.restype = wintypes.BOOL
    _KERNEL32.ReadDirectoryChangesW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _KERNEL32.ReadDirectoryChangesW.restype = wintypes.BOOL
    _KERNEL32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    _KERNEL32.CreateEventW.restype = wintypes.HANDLE
    _KERNEL32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_Overlapped),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    _KERNEL32.GetOverlappedResult.restype = wintypes.BOOL
    _KERNEL32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _KERNEL32.CancelIoEx.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
else:  # pragma: no cover - production package kind is win-x64.
    _KERNEL32 = None


def _failure(code: str, message: str, *, details: dict[str, Any] | None = None) -> LocalVoiceRuntimeError:
    return LocalVoiceRuntimeError(code, message, details=details)


def _is_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise _failure(
            "VOICE_ENGINE_LAUNCH_ATTESTATION_FAILED",
            "could not inspect an engine package path during launch attestation",
            details={"error": f"{type(exc).__name__}:{exc}"},
        ) from exc
    return stat.S_ISLNK(metadata.st_mode) or bool(
        int(getattr(metadata, "st_file_attributes", 0) or 0) & _REPARSE_ATTRIBUTE
    )


def _inventory(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        if _is_reparse(directory):
            raise _failure(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_REPARSE",
                "engine package directories cannot be reparse points",
            )
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            raise _failure(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_FAILED",
                "could not enumerate the engine package for launch",
                details={"error": f"{type(exc).__name__}:{exc}"},
            ) from exc
        for child in children:
            if _is_reparse(child):
                raise _failure(
                    "VOICE_ENGINE_LAUNCH_ATTESTATION_REPARSE",
                    "engine package entries cannot be reparse points",
                )
            relative = child.relative_to(root).as_posix()
            try:
                metadata = os.lstat(child)
            except OSError as exc:
                raise _failure(
                    "VOICE_ENGINE_LAUNCH_ATTESTATION_FAILED",
                    "could not inspect an engine package entry",
                    details={"error": f"{type(exc).__name__}:{exc}"},
                ) from exc
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(relative)
                pending.append(child)
            elif stat.S_ISREG(metadata.st_mode):
                if int(getattr(metadata, "st_nlink", 1) or 1) != 1:
                    raise _failure(
                        "VOICE_ENGINE_LAUNCH_ATTESTATION_HARDLINK",
                        "engine package files cannot be hardlinks",
                    )
                files.add(relative)
            else:
                raise _failure(
                    "VOICE_ENGINE_LAUNCH_ATTESTATION_TYPE",
                    "engine package entries must be regular files or directories",
                )
    return files, directories


def _win_handle_value(value: object) -> int:
    if isinstance(value, int):
        return value
    return int(getattr(value, "value", 0) or 0)


def _win_close(handle: int) -> None:
    if _KERNEL32 is not None and handle not in {0, _INVALID_HANDLE_VALUE}:
        _KERNEL32.CloseHandle(wintypes.HANDLE(int(handle)))


def _win_open(path: Path, *, directory: bool, overlapped: bool = False) -> int:
    assert _KERNEL32 is not None
    desired = (_FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES) if directory else (_GENERIC_READ | _FILE_READ_ATTRIBUTES)
    flags = (
        _FILE_FLAG_OPEN_REPARSE_POINT
        | (_FILE_FLAG_BACKUP_SEMANTICS if directory else 0)
        | (_FILE_FLAG_OVERLAPPED if overlapped else 0)
    )
    ctypes.set_last_error(0)
    handle = _win_handle_value(
        _KERNEL32.CreateFileW(
            str(path),
            desired,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            flags,
            None,
        )
    )
    if handle in {0, _INVALID_HANDLE_VALUE}:
        error = int(ctypes.get_last_error())
        raise _failure(
            "VOICE_ENGINE_LAUNCH_ATTESTATION_LOCK_FAILED",
            "an engine package object could not be locked read-only for the child generation",
            details={"winerror": error},
        )
    return handle


def _win_identity(handle: int, *, directory: bool) -> _Identity:
    assert _KERNEL32 is not None
    value = _ByHandleFileInformation()
    if not _KERNEL32.GetFileInformationByHandle(wintypes.HANDLE(int(handle)), ctypes.byref(value)):
        raise _failure(
            "VOICE_ENGINE_LAUNCH_ATTESTATION_IDENTITY_FAILED",
            "could not obtain engine package FileId evidence",
            details={"winerror": int(ctypes.get_last_error())},
        )
    attributes = int(value.dwFileAttributes)
    actual_directory = bool(attributes & _DIRECTORY_ATTRIBUTE)
    if actual_directory != directory or bool(attributes & _REPARSE_ATTRIBUTE):
        raise _failure(
            "VOICE_ENGINE_LAUNCH_ATTESTATION_TYPE",
            "engine package handle type or reparse status is invalid",
        )
    links = int(value.nNumberOfLinks)
    if not directory and links != 1:
        raise _failure(
            "VOICE_ENGINE_LAUNCH_ATTESTATION_HARDLINK",
            "engine package files cannot be hardlinks",
        )
    return _Identity(
        volume_serial=int(value.dwVolumeSerialNumber),
        file_id=(int(value.nFileIndexHigh) << 32) | int(value.nFileIndexLow),
        size=(int(value.nFileSizeHigh) << 32) | int(value.nFileSizeLow),
        links=links,
        attributes=attributes,
    )


def _win_hash(handle: int, expected_size: int) -> str:
    assert _KERNEL32 is not None
    digest = hashlib.sha256()
    total = 0
    while True:
        buffer = ctypes.create_string_buffer(1024 * 1024)
        read = wintypes.DWORD(0)
        if not _KERNEL32.ReadFile(
            wintypes.HANDLE(int(handle)),
            buffer,
            len(buffer),
            ctypes.byref(read),
            None,
        ):
            raise _failure(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_READ_FAILED",
                "could not hash an engine package file from its retained handle",
                details={"winerror": int(ctypes.get_last_error())},
            )
        count = int(read.value)
        if count == 0:
            break
        total += count
        if total > expected_size:
            raise _failure(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_MISMATCH",
                "engine package file grew during launch attestation",
            )
        digest.update(buffer.raw[:count])
    if total != expected_size:
        raise _failure(
            "VOICE_ENGINE_LAUNCH_ATTESTATION_MISMATCH",
            "engine package file size changed during launch attestation",
        )
    return digest.hexdigest()


def _win_queue_directory_watch(
    handle: int,
    buffer: Any,
    overlapped: _Overlapped,
) -> tuple[bool, int]:
    assert _KERNEL32 is not None
    ctypes.set_last_error(0)
    queued = bool(
        _KERNEL32.ReadDirectoryChangesW(
            wintypes.HANDLE(int(handle)),
            buffer,
            len(buffer),
            True,
            _WATCH_FILTER,
            None,
            ctypes.byref(overlapped),
            None,
        )
    )
    return queued, int(ctypes.get_last_error())


class QwenEngineLaunchAttestationLease:
    """A one-generation retained handle set plus directory-change monitor."""

    threat_boundary = (
        "blocks ordinary listed-file write/delete/replacement opens and detects package-tree changes; "
        "excludes adversarial same-principal directory races, process injection/handle tampering, "
        "ACL/ownership changes, supervisor termination and kernel compromise"
    )

    def __init__(self, capability: VerifiedQwenEngineRegistrationCapability) -> None:
        if not isinstance(capability, VerifiedQwenEngineRegistrationCapability) or not capability.is_valid():
            raise _failure(
                "VOICE_ENGINE_CAPABILITY_REQUIRED",
                "a complete verified Qwen engine capability is required for launch attestation",
            )
        self._root = Path(capability.package_root).resolve(strict=True)
        self._expected_files = {
            relative: (int(size), str(digest))
            for relative, size, digest in capability._verified_files
        }
        self._expected_directories = set(capability._verified_directories)
        self._guard = threading.RLock()
        self._release_lock = threading.Lock()
        self._invalid_reason = ""
        self._released = False
        self._release_started = False
        self._release_in_progress = False
        self._sealed = False
        self._windows_objects: list[_WindowsHeldObject] = []
        self._portable_fds: list[tuple[int, tuple[int, int, int, int], bool]] = []
        self._watch_handle = 0
        self._watch_event_handle = 0
        self._watch_buffer: Any | None = None
        self._watch_overlapped: _Overlapped | None = None
        self._watch_thread: threading.Thread | None = None
        self._watch_ready = threading.Event()
        self._watch_stop = threading.Event()
        try:
            self._assert_inventory_exact()
            if os.name == "nt":
                self._acquire_windows()
            else:  # pragma: no cover - production package kind is win-x64.
                self._acquire_portable()
            self._assert_inventory_exact()
            self.assert_valid()
        except BaseException as original:
            try:
                self.release()
            except BaseException as cleanup:
                if isinstance(original, LocalVoiceRuntimeError):
                    original.details["cleanup_error"] = f"{type(cleanup).__name__}:{cleanup}"
                    raise original
                raise _failure(
                    "VOICE_ENGINE_LAUNCH_ATTESTATION_CLEANUP_FAILED",
                    "launch attestation failed and its partial native state could not be released",
                    details={
                        "original_error": f"{type(original).__name__}:{original}",
                        "cleanup_error": f"{type(cleanup).__name__}:{cleanup}",
                    },
                ) from original
            raise

    def _assert_inventory_exact(self) -> None:
        actual_files, actual_directories = _inventory(self._root)
        expected_files = set(self._expected_files)
        if actual_files != expected_files or actual_directories != self._expected_directories:
            raise _failure(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_INVENTORY_CHANGED",
                "engine package files or directories changed during launch attestation",
                details={
                    "missing_files": sorted(expected_files - actual_files),
                    "unlisted_files": sorted(actual_files - expected_files),
                    "missing_directories": sorted(self._expected_directories - actual_directories),
                    "unlisted_directories": sorted(actual_directories - self._expected_directories),
                },
            )

    def _hold_windows_directory(self, path: Path) -> _WindowsHeldObject:
        handle = _win_open(path, directory=True)
        try:
            record = _WindowsHeldObject(handle, _win_identity(handle, directory=True), True)
        except BaseException:
            _win_close(handle)
            raise
        self._windows_objects.append(record)
        return record

    def _acquire_windows(self) -> None:
        self._hold_windows_directory(self._root)
        # A synchronous ReadDirectoryChangesW call serializes other operations
        # issued through the same handle.  Keep monitoring on its own retained
        # handle so FileId assertions on the root handle can never wait behind
        # the watcher.
        self._watch_handle = _win_open(self._root, directory=True, overlapped=True)
        self._start_windows_watcher()
        for relative in sorted(self._expected_directories):
            self._hold_windows_directory(self._root.joinpath(*relative.split("/")))
        for relative, (expected_size, expected_digest) in sorted(self._expected_files.items()):
            path = self._root.joinpath(*relative.split("/"))
            handle = _win_open(path, directory=False)
            try:
                identity = _win_identity(handle, directory=False)
                if identity.size != expected_size:
                    raise _failure(
                        "VOICE_ENGINE_LAUNCH_ATTESTATION_MISMATCH",
                        "engine package file size does not match its verified capability",
                    )
                actual_digest = _win_hash(handle, expected_size)
                after = _win_identity(handle, directory=False)
                if after != identity or actual_digest != expected_digest:
                    raise _failure(
                        "VOICE_ENGINE_LAUNCH_ATTESTATION_MISMATCH",
                        "engine package FileId, size or hash changed during handle-bound verification",
                    )
                self._windows_objects.append(_WindowsHeldObject(handle, identity, False))
            except BaseException:
                _win_close(handle)
                raise

    def _start_windows_watcher(self) -> None:
        assert _KERNEL32 is not None and self._watch_handle
        watch_handle = self._watch_handle
        buffer = ctypes.create_string_buffer(64 * 1024)
        event_handle = _win_handle_value(_KERNEL32.CreateEventW(None, True, False, None))
        if event_handle in {0, _INVALID_HANDLE_VALUE}:
            raise _failure(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_MONITOR_FAILED",
                "could not create the engine package monitor completion event",
                details={"winerror": int(ctypes.get_last_error())},
            )
        overlapped = _Overlapped()
        overlapped.hEvent = wintypes.HANDLE(int(event_handle))
        # These objects must outlive the pending overlapped request. They are
        # stored before the dedicated thread queues I/O and are cleared only
        # after GetOverlappedResult confirms completion/cancel.
        self._watch_buffer = buffer
        self._watch_overlapped = overlapped
        self._watch_event_handle = event_handle

        def watch() -> None:
            returned = wintypes.DWORD(0)
            try:
                queued, queue_error = _win_queue_directory_watch(
                    watch_handle,
                    buffer,
                    overlapped,
                )
                if not queued:
                    with self._guard:
                        self._invalid_reason = f"directory monitor queue failed:{queue_error}"
                    self._watch_ready.set()
                    return
                # Win32 defines nonzero asynchronous return as successfully
                # queued. Only this point may satisfy the readiness gate.
                self._watch_ready.set()
                ctypes.set_last_error(0)
                succeeded = bool(
                    _KERNEL32.GetOverlappedResult(
                        wintypes.HANDLE(int(watch_handle)),
                        ctypes.byref(overlapped),
                        ctypes.byref(returned),
                        True,
                    )
                )
                error = int(ctypes.get_last_error())
            except BaseException as exc:
                if self._watch_stop.is_set():
                    return
                with self._guard:
                    self._invalid_reason = (
                        "directory monitor raised:"
                        f"{type(exc).__name__}:{exc}"
                    )
                self._watch_ready.set()
                return
            if self._watch_stop.is_set():
                return
            with self._guard:
                if not succeeded:
                    self._invalid_reason = f"directory monitor failed:{error}"
                else:
                    self._invalid_reason = "engine package directory or security metadata changed"

        thread = threading.Thread(
            target=watch,
            name="qwen-engine-launch-attestation-watch",
            daemon=True,
        )
        self._watch_thread = thread
        try:
            thread.start()
        except BaseException as exc:
            _win_close(event_handle)
            self._watch_buffer = None
            self._watch_overlapped = None
            self._watch_event_handle = 0
            self._watch_thread = None
            raise _failure(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_MONITOR_FAILED",
                "the engine package directory monitor thread could not start",
                details={"error": f"{type(exc).__name__}:{exc}"},
            ) from exc

        if not self._watch_ready.wait(timeout=2.0):
            raise _failure(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_MONITOR_FAILED",
                "engine package directory monitor did not queue in time",
            )
        with self._guard:
            monitor_error = self._invalid_reason
        if monitor_error:
            raise _failure(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_MONITOR_FAILED",
                "engine package directory monitor did not become active",
                details={"reason": monitor_error},
            )

    def _acquire_portable(self) -> None:
        # Test/development fallback. POSIX advisory handles do not provide the
        # Windows share-deny guarantee; assert_valid therefore re-enumerates.
        paths: list[tuple[Path, bool]] = [(self._root, True)]
        paths.extend(
            (self._root.joinpath(*relative.split("/")), True)
            for relative in sorted(self._expected_directories)
        )
        paths.extend(
            (self._root.joinpath(*relative.split("/")), False)
            for relative in sorted(self._expected_files)
        )
        for path, directory in paths:
            flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
            if directory:
                flags |= int(getattr(os, "O_DIRECTORY", 0))
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            identity = (int(metadata.st_dev), int(metadata.st_ino), int(metadata.st_size), int(metadata.st_nlink))
            if not directory:
                relative = path.relative_to(self._root).as_posix()
                expected_size, expected_digest = self._expected_files[relative]
                with os.fdopen(os.dup(descriptor), "rb") as handle:
                    digest_builder = hashlib.sha256()
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest_builder.update(chunk)
                    digest = digest_builder.hexdigest()
                if int(metadata.st_nlink) != 1 or int(metadata.st_size) != expected_size or digest != expected_digest:
                    os.close(descriptor)
                    raise _failure(
                        "VOICE_ENGINE_LAUNCH_ATTESTATION_MISMATCH",
                        "engine package file does not match its verified capability",
                    )
            self._portable_fds.append((descriptor, identity, directory))

    def seal_for_launch(self) -> None:
        with self._guard:
            if self._release_started:
                raise _failure(
                    "VOICE_ENGINE_LAUNCH_ATTESTATION_RELEASED",
                    "launch attestation was already released",
                )
        # This exact inventory is intentionally performed in the suspended
        # child pre-resume gate, not merely before CreateProcessW.
        self._assert_inventory_exact()
        self.assert_valid()
        with self._guard:
            self._sealed = True

    def assert_valid(self) -> None:
        with self._guard:
            if self._release_started:
                raise _failure(
                    "VOICE_ENGINE_LAUNCH_ATTESTATION_RELEASED",
                    "launch attestation was already released",
                )
            if self._invalid_reason:
                raise _failure(
                    "VOICE_ENGINE_LAUNCH_ATTESTATION_CHANGED",
                    "engine package changed while its child-generation lease was retained",
                    details={"reason": self._invalid_reason},
                )
            windows_objects = tuple(self._windows_objects)
            portable_fds = tuple(self._portable_fds)
            watch_handle = self._watch_handle
            watch_overlapped = self._watch_overlapped
            watch_thread = self._watch_thread
            sealed = self._sealed
        if os.name == "nt":
            if (
                not watch_handle
                or watch_overlapped is None
                or watch_thread is None
                or not watch_thread.is_alive()
            ):
                raise _failure(
                    "VOICE_ENGINE_LAUNCH_ATTESTATION_MONITOR_FAILED",
                    "the retained engine package directory monitor is not active",
                )
            for record in windows_objects:
                if _win_identity(record.handle, directory=record.directory) != record.identity:
                    raise _failure(
                        "VOICE_ENGINE_LAUNCH_ATTESTATION_CHANGED",
                        "a retained engine package FileId changed",
                    )
        else:  # pragma: no cover - production package kind is win-x64.
            for descriptor, identity, _directory in portable_fds:
                metadata = os.fstat(descriptor)
                current = (int(metadata.st_dev), int(metadata.st_ino), int(metadata.st_size), int(metadata.st_nlink))
                if current != identity:
                    raise _failure(
                        "VOICE_ENGINE_LAUNCH_ATTESTATION_CHANGED",
                        "a retained engine package identity changed",
                    )
            if sealed:
                self._assert_inventory_exact()

    def release(self) -> None:
        # Serialize release so no caller can double-close a native handle.
        # Protection ends as soon as release starts, but a timed-out watcher
        # join leaves cleanup retryable: OVERLAPPED/buffer/event/handle stay
        # retained until the request has actually completed or been canceled.
        with self._release_lock:
            with self._guard:
                if self._released:
                    return
                self._release_started = True
                self._release_in_progress = True
                self._watch_stop.set()
                watch_handle = self._watch_handle
                watch_overlapped = self._watch_overlapped
                watch_event_handle = self._watch_event_handle
                windows_objects = tuple(self._windows_objects)
                self._windows_objects.clear()
                portable_fds = tuple(self._portable_fds)
                self._portable_fds.clear()
                thread = self._watch_thread
            if watch_handle and watch_overlapped is not None and _KERNEL32 is not None:
                _KERNEL32.CancelIoEx(
                    wintypes.HANDLE(int(watch_handle)),
                    ctypes.byref(watch_overlapped),
                )
            for record in reversed(windows_objects):
                _win_close(record.handle)
            for descriptor, _identity, _directory in reversed(portable_fds):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if thread is threading.current_thread():
                with self._guard:
                    self._release_in_progress = False
                raise _failure(
                    "VOICE_ENGINE_LAUNCH_ATTESTATION_RELEASE_FAILED",
                    "the engine package monitor cannot release itself",
                )
            if thread is not None:
                thread.join(timeout=5.0)
                if thread.is_alive():
                    with self._guard:
                        self._release_in_progress = False
                    raise _failure(
                        "VOICE_ENGINE_LAUNCH_ATTESTATION_RELEASE_FAILED",
                        "engine package directory monitor did not stop",
                    )
            _win_close(watch_event_handle)
            _win_close(watch_handle)
            with self._guard:
                self._watch_handle = 0
                self._watch_event_handle = 0
                self._watch_buffer = None
                self._watch_overlapped = None
                self._watch_thread = None
                self._released = True
                self._release_in_progress = False


def acquire_qwen_engine_launch_attestation(
    capability: VerifiedQwenEngineRegistrationCapability,
) -> QwenEngineLaunchAttestationLease:
    return QwenEngineLaunchAttestationLease(capability)

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


FORBIDDEN = {
    "windows",
    "system32",
    "program files",
    "programdata",
    "appdata",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "secrets",
    "credentials",
}
FORBIDDEN_FILES = {".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials", "secrets"}
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
_WINDOWS_INVALID_HANDLE = ctypes.c_void_p(-1).value


class PathSecurityError(ValueError):
    """Raised when a path cannot be proven to be the same safe filesystem entry."""


@dataclass(frozen=True)
class FileIdentity:
    """Stable identity captured from a no-follow file handle."""

    st_dev: int
    st_ino: int
    st_mode: int
    st_nlink: int
    st_size: int
    st_mtime_ns: int
    st_ctime_ns: int
    file_attributes: int = 0

    @classmethod
    def from_stat(cls, metadata) -> "FileIdentity":
        return cls(
            st_dev=int(getattr(metadata, "st_dev", 0) or 0),
            st_ino=int(getattr(metadata, "st_ino", 0) or 0),
            st_mode=int(getattr(metadata, "st_mode", 0) or 0),
            st_nlink=int(getattr(metadata, "st_nlink", 1) or 1),
            st_size=int(getattr(metadata, "st_size", 0) or 0),
            st_mtime_ns=int(getattr(metadata, "st_mtime_ns", 0) or 0),
            st_ctime_ns=int(getattr(metadata, "st_ctime_ns", 0) or 0),
            file_attributes=int(getattr(metadata, "st_file_attributes", 0) or 0),
        )

    def same_entry(self, other: "FileIdentity") -> bool:
        return (
            self.st_dev == other.st_dev
            and self.st_ino == other.st_ino
            and stat.S_IFMT(self.st_mode) == stat.S_IFMT(other.st_mode)
            and self.st_nlink == other.st_nlink
            and self.file_attributes == other.file_attributes
        )

    def unchanged(self, other: "FileIdentity") -> bool:
        return self.same_entry(other) and self.st_size == other.st_size and self.st_mtime_ns == other.st_mtime_ns

    def as_dict(self) -> dict[str, int]:
        return {
            "st_dev": self.st_dev,
            "st_ino": self.st_ino,
            "st_mode": self.st_mode,
            "st_nlink": self.st_nlink,
            "st_size": self.st_size,
            "st_mtime_ns": self.st_mtime_ns,
            "st_ctime_ns": self.st_ctime_ns,
            "file_attributes": self.file_attributes,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FileIdentity | None":
        if not isinstance(value, dict):
            return None
        try:
            return cls(
                st_dev=int(value["st_dev"]),
                st_ino=int(value["st_ino"]),
                st_mode=int(value["st_mode"]),
                st_nlink=int(value["st_nlink"]),
                st_size=int(value["st_size"]),
                st_mtime_ns=int(value["st_mtime_ns"]),
                st_ctime_ns=int(value.get("st_ctime_ns", 0)),
                file_attributes=int(value.get("file_attributes", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None


def forbidden(name: str) -> bool:
    low = str(name).lower()
    return low in FORBIDDEN or low.startswith(".") or low in FORBIDDEN_FILES or low.endswith((".key", ".pem", ".p12"))


def lexical_absolute(raw: str | os.PathLike[str]) -> Path:
    """Return an absolute path without resolving symlinks/reparse points."""

    try:
        return Path(os.path.abspath(os.fspath(raw)))
    except (OSError, TypeError, ValueError) as exc:
        raise PathSecurityError("路径无法读取") from exc


def _is_reparse(metadata) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        int(getattr(metadata, "st_file_attributes", 0) or 0) & _WINDOWS_REPARSE_POINT
    )


def _validate_metadata(metadata, *, kind: str, path: Path) -> FileIdentity:
    if _is_reparse(metadata):
        raise PathSecurityError("拒绝 symlink/junction")
    mode = int(getattr(metadata, "st_mode", 0) or 0)
    if kind == "dir":
        if not stat.S_ISDIR(mode):
            raise PathSecurityError("路径不是目录")
    elif kind == "file":
        if not stat.S_ISREG(mode) or int(getattr(metadata, "st_nlink", 1) or 1) != 1:
            raise PathSecurityError("拒绝硬链接或特殊文件")
    elif kind == "entry":
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise PathSecurityError("拒绝特殊文件")
    else:
        raise PathSecurityError("路径类型无效")
    return FileIdentity.from_stat(metadata)


def _components(path: Path) -> list[Path]:
    absolute = lexical_absolute(path)
    if not absolute.anchor:
        raise PathSecurityError("路径无法读取")
    current = Path(absolute.anchor)
    output = [current]
    for part in absolute.parts[1:]:
        current = current / part
        output.append(current)
    return output


def validate_no_follow_path(path: str | os.PathLike[str], kind: str = "file", *, allow_missing: bool = False) -> Path:
    """Validate all existing lexical ancestors and the final entry with lstat."""

    candidate = lexical_absolute(path)
    components = _components(candidate)
    for index, component in enumerate(components):
        try:
            metadata = os.lstat(component)
        except FileNotFoundError as exc:
            if allow_missing and index == len(components) - 1:
                return candidate
            raise PathSecurityError("路径无法读取") from exc
        except OSError as exc:
            raise PathSecurityError("路径无法读取") from exc
        _validate_metadata(metadata, kind="dir" if index < len(components) - 1 else kind, path=component)
    return candidate


def capture_file_identity(path: str | os.PathLike[str], *, allow_missing: bool = False) -> FileIdentity | None:
    candidate = validate_no_follow_path(path, "file", allow_missing=allow_missing)
    if allow_missing:
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PathSecurityError("文件无法读取") from exc
    try:
        return _validate_metadata(os.lstat(candidate), kind="file", path=candidate)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise PathSecurityError("文件无法读取")
    except OSError as exc:
        raise PathSecurityError("文件无法读取") from exc


def _identity_from_fd(fd: int, *, path: Path) -> FileIdentity:
    try:
        metadata = os.fstat(fd)
    except OSError as exc:
        raise PathSecurityError("文件无法读取") from exc
    return _validate_metadata(metadata, kind="file", path=path)


def _verify_fd_path(fd: int, path: Path) -> None:
    """Reject a POSIX ancestor redirect discovered from the opened descriptor."""

    if os.name == "nt":
        return
    proc_link = Path(f"/proc/self/fd/{fd}")
    try:
        actual = os.readlink(proc_link)
    except (FileNotFoundError, OSError):
        return
    if actual.endswith(" (deleted)"):
        actual = actual[: -len(" (deleted)")]
    if os.path.normcase(os.path.abspath(actual)) != os.path.normcase(os.path.abspath(str(path))):
        raise PathSecurityError("拒绝 symlink/junction")


def _windows_open(path: Path, *, write: bool = False, exclusive: bool = False) -> int:
    """Open a final Windows path with reparse-point and delete-share protection."""

    if os.name != "nt":
        raise RuntimeError("Windows opener used on a non-Windows host")
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    CREATE_NEW = 1
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_FLAG_POSIX_SEMANTICS = 0x01000000
    access = GENERIC_READ | (GENERIC_WRITE if write else 0)
    disposition = CREATE_NEW if exclusive else OPEN_EXISTING
    handle = create_file(
        str(path),
        access,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        disposition,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_POSIX_SEMANTICS,
        None,
    )
    if handle in (None, _WINDOWS_INVALID_HANDLE):
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error) if error else "CreateFileW failed", str(path))
    # Ancestors can be replaced between the lexical lstat pass and CreateFileW.
    # Compare the handle's resolved final name before handing it to a caller;
    # a junction/reparse redirect therefore fails before any read or write.
    try:
        get_final_name = kernel32.GetFinalPathNameByHandleW
        get_final_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final_name.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_final_name(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise OSError(ctypes.get_last_error() or 1, "cannot verify Windows file identity", str(path))
        actual = buffer.value
        if actual.startswith("\\\\?\\"):
            actual = actual[4:]
        expected = os.path.abspath(str(path))
        if os.path.normcase(actual) != os.path.normcase(expected):
            raise PathSecurityError("拒绝 symlink/junction")
    except Exception:
        kernel32.CloseHandle(handle)
        raise
    flags = os.O_BINARY | (os.O_WRONLY if write else os.O_RDONLY)
    try:
        return msvcrt.open_osfhandle(int(handle), flags)
    except Exception:
        kernel32.CloseHandle(handle)
        raise


def open_verified_file(
    path: str | os.PathLike[str],
    mode: str = "rb",
    *,
    expected: FileIdentity | None = None,
    exclusive: bool = False,
) -> tuple[BinaryIO, FileIdentity]:
    """Open a regular file without following the final link and pin its identity."""

    if "b" not in mode or mode not in {"rb", "wb"}:
        raise ValueError("open_verified_file only supports binary read/write")
    write = mode == "wb"
    candidate = lexical_absolute(path)
    validate_no_follow_path(candidate, "file", allow_missing=exclusive)
    flags = (os.O_WRONLY if write else os.O_RDONLY) | getattr(os, "O_BINARY", 0)
    if exclusive:
        flags |= os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    if not write:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        if os.name == "nt":
            fd = _windows_open(candidate, write=write, exclusive=exclusive)
        else:
            fd = os.open(candidate, flags, 0o600)
    except FileExistsError as exc:
        raise PathSecurityError("目标文件已存在") from exc
    except OSError as exc:
        raise PathSecurityError("文件无法读取") from exc
    try:
        identity = _identity_from_fd(fd, path=candidate)
        _verify_fd_path(fd, candidate)
        if expected is not None and not expected.unchanged(identity):
            raise PathSecurityError("文件已发生变化，请重新预览")
        return os.fdopen(fd, mode, closefd=True), identity
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def read_verified_bytes(
    path: str | os.PathLike[str],
    *,
    expected: FileIdentity | None = None,
    max_bytes: int | None = None,
) -> tuple[bytes, FileIdentity]:
    """Read bytes from one pinned handle and fail if the handle identity changes."""

    handle, identity = open_verified_file(path, "rb", expected=expected)
    digest = bytearray()
    try:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.extend(block)
            if max_bytes is not None and len(digest) > max_bytes:
                raise PathSecurityError("文件超过大小限制")
        after = FileIdentity.from_stat(os.fstat(handle.fileno()))
        if not identity.unchanged(after):
            raise PathSecurityError("文件已发生变化，请重新预览")
    except OSError as exc:
        raise PathSecurityError("文件无法读取") from exc
    finally:
        handle.close()
    return bytes(digest), identity


def snapshot_verified_file(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    expected: FileIdentity | None = None,
    max_bytes: int | None = None,
) -> tuple[FileIdentity, str]:
    """Copy a verified source handle into an exclusive owned destination."""

    source_handle, source_identity = open_verified_file(source, "rb", expected=expected)
    destination_path = lexical_absolute(destination)
    validate_no_follow_path(destination_path.parent, "dir")
    try:
        destination_handle, destination_identity = open_verified_file(destination_path, "wb", exclusive=True)
    except Exception:
        source_handle.close()
        raise
    total = 0
    digest = hashlib.sha256()
    try:
        while True:
            block = source_handle.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if max_bytes is not None and total > max_bytes:
                raise PathSecurityError("文件超过大小限制")
            digest.update(block)
            destination_handle.write(block)
        source_after = FileIdentity.from_stat(os.fstat(source_handle.fileno()))
        if not source_identity.unchanged(source_after):
            raise PathSecurityError("文件已发生变化，请重新预览")
        destination_handle.flush()
        destination_after = FileIdentity.from_stat(os.fstat(destination_handle.fileno()))
        if destination_after.st_size != total or destination_after.st_nlink != 1:
            raise PathSecurityError("快照文件无法验证")
    except OSError as exc:
        raise PathSecurityError("文件无法读取") from exc
    finally:
        source_handle.close()
        destination_handle.close()
    try:
        verified, identity = open_verified_file(destination_path, "rb", expected=destination_after)
        verified.close()
    except Exception:
        try:
            os.unlink(destination_path)
        except OSError:
            pass
        raise
    return identity, digest.hexdigest()


def remove_verified_file(
    path: str | os.PathLike[str],
    *,
    expected: FileIdentity | None = None,
    missing_ok: bool = True,
) -> None:
    """Remove only a verified regular file; links, hardlinks, and replacements fail closed."""

    candidate = lexical_absolute(path)
    try:
        handle, identity = open_verified_file(candidate, "rb", expected=expected)
    except PathSecurityError:
        if missing_ok:
            try:
                os.lstat(candidate)
            except FileNotFoundError:
                return
        raise
    try:
        current = FileIdentity.from_stat(os.fstat(handle.fileno()))
        if not identity.unchanged(current):
            raise PathSecurityError("文件已发生变化，请重新预览")
    finally:
        handle.close()
    try:
        os.unlink(candidate)
    except FileNotFoundError:
        if not missing_ok:
            raise PathSecurityError("文件无法删除")
    except OSError as exc:
        raise PathSecurityError("文件无法删除") from exc


def datetime_iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_allowed_root(raw):
    root = lexical_absolute(raw)
    try:
        metadata = os.lstat(root)
    except FileNotFoundError as exc:
        raise ValueError("允许的根目录不存在或不是目录") from exc
    except OSError as exc:
        raise ValueError("允许的根目录无法读取") from exc
    try:
        _validate_metadata(metadata, kind="dir", path=root)
    except PathSecurityError as exc:
        if str(exc) == "拒绝 symlink/junction":
            raise ValueError("拒绝 symlink/junction") from exc
        raise ValueError("允许的根目录不存在或不是目录") from exc
    if root.parent == root or root.anchor == root.as_posix():
        raise ValueError("拒绝盘符根目录")
    parts_lower = {p.lower() for p in root.parts}
    is_temp = "temp" in parts_lower and "appdata" in parts_lower
    home = lexical_absolute(Path.home())
    if (root == home or home in root.parents) and not is_temp:
        raise ValueError("拒绝整个用户主目录")
    if any(forbidden(p) for p in root.parts if p.lower() != "appdata") or (
        "appdata" in parts_lower and "temp" not in parts_lower
    ):
        raise ValueError("拒绝系统、依赖或敏感目录")
    validate_no_follow_path(root, "dir")
    return root


def walk_safe_files(candidate, root, supported, max_files=50, max_bytes=5_000_000, max_total=50_000_000, max_depth=5):
    candidate = lexical_absolute(candidate)
    root = validate_allowed_root(root)
    if not _inside(candidate, root):
        raise ValueError("路径必须位于允许根目录内")
    try:
        candidate_meta = os.lstat(candidate)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("本地路径无法读取") from exc
    if stat.S_ISLNK(candidate_meta.st_mode) or _is_reparse(candidate_meta):
        raise ValueError("拒绝 symlink/junction")
    if not (stat.S_ISDIR(candidate_meta.st_mode) or stat.S_ISREG(candidate_meta.st_mode)):
        raise ValueError("本地路径无法读取")
    accepted: list[dict] = []
    rejected: list[dict] = []
    total = 0

    def reject(path: Path, reason: str) -> None:
        rejected.append({"path": str(path), "reason": reason})

    def walk(folder: Path, depth: int) -> None:
        nonlocal total
        if depth > max_depth:
            reject(folder, "超过目录深度限制")
            return
        try:
            validate_no_follow_path(folder, "dir")
        except PathSecurityError:
            reject(folder, "拒绝 symlink/junction")
            return
        try:
            entries = list(os.scandir(folder))
        except OSError:
            reject(folder, "文件无法读取")
            return
        for entry in entries:
            p = lexical_absolute(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError:
                reject(p, "文件无法读取")
                continue
            if _is_reparse(metadata):
                reject(p, "拒绝 symlink/junction")
                continue
            if forbidden(entry.name):
                reject(p, "敏感或隐藏文件/目录")
                continue
            mode = int(getattr(metadata, "st_mode", 0) or 0)
            if stat.S_ISDIR(mode):
                walk(p, depth + 1)
                continue
            if not stat.S_ISREG(mode):
                reject(p, "拒绝特殊文件")
                continue
            if Path(entry.name).suffix.lower() not in supported:
                reject(p, "不支持的文件类型")
                continue
            if int(getattr(metadata, "st_nlink", 1) or 1) != 1:
                reject(p, "拒绝硬链接或特殊文件")
                continue
            size = int(getattr(metadata, "st_size", 0) or 0)
            if size > max_bytes:
                reject(p, "超过单文件大小限制")
                continue
            if len(accepted) >= max_files or total + size > max_total:
                reject(p, "超过数量或总大小限制")
                continue
            try:
                data, opened_identity = read_verified_bytes(p, max_bytes=max_bytes)
            except PathSecurityError:
                reject(p, "文件已发生变化，请重新预览")
                continue
            if opened_identity.st_size != size:
                reject(p, "文件已发生变化，请重新预览")
                continue
            accepted.append(
                {
                    "id": "file_" + os.urandom(7).hex(),
                    "path": str(p),
                    "relative_path": str(p.relative_to(root)),
                    "size": len(data),
                    "mtime": datetime_iso(opened_identity.st_mtime_ns / 1_000_000_000),
                    "hash": hashlib.sha256(data).hexdigest(),
                    "identity": opened_identity.as_dict(),
                }
            )
            total += len(data)

    if stat.S_ISREG(candidate_meta.st_mode):
        walk(candidate.parent, max_depth)
        accepted[:] = [item for item in accepted if item["path"] == str(candidate)]
    else:
        walk(candidate, 0)
    return accepted, rejected

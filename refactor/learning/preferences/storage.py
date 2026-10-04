"""Account-scoped, atomic local JSON storage; no legacy business runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import threading


class StorageError(ValueError):
    def __init__(self, message="无法读取或保存当前账号的设置。"):
        super().__init__(message)


def account_directory(account_id: str, data_root) -> Path:
    if not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", account_id):
        raise StorageError("账号标识无效。")
    # Reject Windows device names as well as path traversal on every platform.
    if account_id.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
        raise StorageError("账号标识无效。")
    root = Path(data_root).expanduser().resolve()
    path = root / "learning" / "accounts" / account_id
    for child in (root / "learning", root / "learning/accounts", path):
        if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
            raise StorageError("账号设置目录不能是链接。")
    return path


_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


class AccountJSON:
    def __init__(self, account_id: str, data_root, filename: str, *, max_bytes=262144):
        self.account_id = account_id
        self.data_root = Path(data_root).expanduser().resolve()
        self.path = account_directory(account_id, data_root) / filename
        self.max_bytes = max_bytes
        with _locks_guard:
            self.lock = _locks.setdefault(os.path.normcase(str(self.path)), threading.RLock())

    def _check(self):
        account_directory(self.account_id, self.data_root)
        if self.path.is_symlink():
            raise StorageError("设置文件不能是链接。")

    def read(self, default):
        with self.lock:
            self._check()
            try:
                if not self.path.exists():
                    return json.loads(json.dumps(default))
                if self.path.stat().st_size > self.max_bytes:
                    raise StorageError()
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise StorageError()
                return value
            except (OSError, ValueError):
                raise StorageError() from None

    def write(self, value):
        try:
            json.dumps(value, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            value = _scrub(value)  # lone surrogates from a third-party text layer (BUG-43)
        except (TypeError, ValueError):
            pass  # reported by the normal path below
        with self.lock:
            self._check()
            temporary = None
            try:
                payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
                if len(payload) > self.max_bytes:
                    raise StorageError()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._check()
                with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".settings-", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except (OSError, ValueError):
                raise StorageError("设置保存失败，原配置未被覆盖。") from None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)


def _scrub(value):
    """Replace unencodable lone surrogates so one bad excerpt cannot block a save."""
    if isinstance(value, str):
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, dict):
        return {_scrub(key): _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value

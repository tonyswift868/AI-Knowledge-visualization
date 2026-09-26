"""Small tool-record store, never a plan database or an old-account importer."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import RLock
import uuid

from ..file_io import _native_path


class ToolError(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value)
            or value.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}):
        raise ToolError("账号或记录标识无效。")
    return value


_locks = {}
_locks_guard = RLock()


def path_lock(path):
    with _locks_guard:
        return _locks.setdefault(str(path.resolve()).casefold(), RLock())


def atomic_json(path, value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if len(data) > 8 * 1024 * 1024:
        raise ToolError("记录超过保存容量，请建立新活动。")
    native_path = _native_path(path)
    native_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = native_path.with_name(native_path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, native_path)
    except OSError as exc:
        raise ToolError("保存失败，请检查数据目录的可写权限和磁盘空间；原记录未覆盖。") from exc
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    native_path = _native_path(path)
    try:
        if native_path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("oversized")
        return json.loads(native_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"无法读取保存记录 {path.name}；请检查文件，未重置记录。") from exc


def _is_reparse(path):
    native_path = _native_path(path)
    return native_path.is_symlink() or (hasattr(native_path, "is_junction") and native_path.is_junction())


def validate_activity(row, account_id, kind, record_id):
    def require(condition):
        if not condition:
            raise ValueError("invalid activity record")

    try:
        require(isinstance(row, dict) and row["schema"] == "mf-tools-activity.v1")
        require(row["account_id"] == account_id and row["kind"] == kind and row["id"] == record_id)
        require(type(row["revision"]) is int and row["revision"] > 0)
        require(all(isinstance(row[key], str) and row[key].strip() for key in ("title", "source", "source_type", "created_at", "updated_at")))
        items, attempts, cursor = row["items"], row["attempts"], row["cursor"]
        require(isinstance(items, list) and 1 <= len(items) <= 10)
        require(all(isinstance(item, dict) and all(isinstance(item[key], str) and item[key].strip()
                    for key in ("prompt", "expected_answer", "hint", "explanation")) for item in items))
        require(type(cursor) is int and 0 <= cursor <= len(items))
        require(row["status"] == ("completed" if cursor == len(items) else "in_progress"))
        require(isinstance(row["hinted_indices"], list))
        require(all(type(i) is int and 0 <= i < len(items) for i in row["hinted_indices"]))
        require(isinstance(attempts, list))
        for attempt in attempts:
            require(isinstance(attempt, dict) and type(attempt["index"]) is int and 0 <= attempt["index"] < len(items))
            require(type(attempt["correct"]) is bool and type(attempt["hint_used"]) is bool)
            require(all(isinstance(attempt[key], str) and attempt[key].strip()
                        for key in ("id", "answer", "feedback", "outcome", "evaluated_by", "submitted_at")))
        if kind == "review":
            require([a["index"] for a in attempts] == list(range(cursor)))
        else:
            expected = 0
            for attempt in attempts:
                require(attempt["index"] == expected)
                expected += int(attempt["correct"])
            require(expected == cursor)
        binding, sync = row["binding"], row["plan_sync"]
        if binding is None:
            require(sync is None)
        else:
            require(isinstance(binding, dict) and all(isinstance(binding[key], str) and binding[key]
                                                     for key in ("plan_key", "plan_id", "task_id")))
            require(isinstance(sync, dict) and sync["state"] in {"pending", "saved"})
            require(sync["payload"]["status"] in {"pending", "in_progress", "completed"})
            require(sync["payload"]["result"]["activity_id"] == record_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("活动记录格式或账号归属不符，未使用空记录替代。") from exc


class ToolStore:
    def __init__(self, data_root, account_id):
        self.account_id = identifier(account_id)
        base = Path(data_root)
        if not base.is_absolute():
            raise ToolError("data_root 必须是绝对路径。")
        self.root = base.resolve() / "learning" / "accounts" / self.account_id / "tools"
        for part in (self.root, *self.root.parents):
            if part == base.resolve():
                break
            if _is_reparse(part):
                raise ToolError("工具记录目录不能链接到其他账号目录。")
        self.lock = path_lock(self.root)

    def open(self):
        with self.lock:
            owner = self.root / "owner.json"
            if _native_path(owner).exists():
                if read_json(owner) != {"schema": "mf-tools-owner.v1", "account_id": self.account_id}:
                    raise ToolError("工具记录的账号归属不符，已停止访问。")
            else:
                atomic_json(owner, {"schema": "mf-tools-owner.v1", "account_id": self.account_id})

    def path(self, kind, record_id):
        if kind not in {"review", "expedition"}:
            raise ToolError("未知工具类型。")
        path = self.root / kind / (identifier(record_id) + ".json")
        if _is_reparse(path) or _is_reparse(path.parent):
            raise ToolError("记录路径不能链接到其他目录。")
        return path

    def get(self, kind, record_id):
        with self.lock:
            self.open()
            row = read_json(self.path(kind, record_id))
            validate_activity(row, self.account_id, kind, record_id)
            return row

    def list(self, kind):
        with self.lock:
            self.open()
            directory = _native_path(self.path(kind, "probe").parent)
            return sorted((self.get(kind, path.stem) for path in directory.glob("*.json")),
                          key=lambda row: row["updated_at"], reverse=True)

    def save(self, row, expected_revision=None):
        with self.lock:
            self.open()
            path = self.path(row["kind"], row["id"])
            if expected_revision is None:
                if _native_path(path).exists():
                    raise ToolError("活动已存在，请重新读取。")
            elif self.get(row["kind"], row["id"])["revision"] != expected_revision:
                raise ToolError("活动已在其他页面更新，请重新读取后再试。")
            updated = deepcopy(row)
            updated.update(schema="mf-tools-activity.v1", account_id=self.account_id,
                           revision=(expected_revision or 0) + 1, updated_at=now())
            validate_activity(updated, self.account_id, row["kind"], row["id"])
            atomic_json(path, updated)
            return updated

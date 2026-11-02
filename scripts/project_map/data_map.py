#!/usr/bin/env python3
"""Generate or query a read-only metadata map (standard library only).

Examples (run from any directory)::

    python scripts/project_map/data_map.py
    python scripts/project_map/data_map.py --out D:/scratch/project-map
    python scripts/project_map/data_map.py --app-root D:/PreviewApp --data-root D:/PreviewData
    python scripts/project_map/data_map.py --query calls --limit 4
    python scripts/project_map/data_map.py --out D:/scratch/project-map --query course.sqlite3

The repository is inferred from this script. Default output is its
.local/project-map/; --query streams the existing Markdown map and returns
bounded excerpts without rescanning data or reading a large JSON document.
Data roots and --local-root can be overridden. Links, credentials and user
content are excluded; SQLite is immutable/read-only schema only, JSON is
metadata-name allowlisted and only retains top-level keys.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Any, TextIO, Iterable
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOTS = [Path(r"D:\MentorFlowApp"), Path(r"D:\MentorFlowData")]
LOCAL_ROOT = REPO_ROOT / '.local'
MAIN_REPO = REPO_ROOT
SENSITIVE_WORDS = ("token", "key", "secret", "password")
SENSITIVE_DIRS = {"release-keys", "server", "credentials", "credential", ".secrets"}
CONTENT_WORDS = ("material", "draft", "lesson", "note", "question", "knowledge", "course", "study", "plan", "library", "upload", "document", "chat", "model", "classroom", "progress")
SKIP_JSON_WORDS = CONTENT_WORDS + ("learning", "history", "transcript", "conversation", "answer", "report")
JSON_METADATA_ALLOWLIST = {"settings.json", "preferences.json", "config.json", "manifest.json", "version.json", "app-settings.json", "account-settings.json"}
REPARSE_FLAG = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_reparse(path: Path) -> bool:
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & REPARSE_FLAG)
    except OSError:
        return True


def _safe_name(name: str) -> bool:
    low = name.casefold()
    return low != "auth.json" and not any(word in low for word in SENSITIVE_WORDS)


def _excluded_name(name: str) -> bool:
    low = name.casefold()
    return low == "auth.json" or any(word in low for word in SENSITIVE_WORDS)


def _kind(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix:
        return suffix[1:] or "[无扩展名]"
    return "[无扩展名]"


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _stream_json_top_keys(path: Path) -> list[str]:
    """Read JSON tokens incrementally, retaining only root object keys.

    String values are consumed without decoding or retaining them. Content-like
    JSON is filtered by caller before this function is invoked.
    """
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("JSON exceeds 1 MiB metadata limit")
    with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as f:
        parser = _JsonKeyScanner(f)
        return parser.top_keys()


class _JsonKeyScanner:
    def __init__(self, stream: TextIO):
        self.f = stream
        self.ch = ""

    def _next(self) -> str:
        self.ch = self.f.read(1)
        return self.ch

    def _ws(self) -> None:
        while self.ch and self.ch in " \t\r\n":
            self._next()

    def _string(self, capture: bool) -> str:
        # self.ch is the opening quote
        out: list[str] = []
        self._next()
        while self.ch:
            c = self.ch
            if c == '"':
                self._next()
                return "".join(out) if capture else ""
            if c == "\\":
                self._next()
                if not self.ch:
                    raise ValueError("unterminated JSON escape")
                esc = self.ch
                if capture:
                    if esc == "u":
                        digits = ""
                        for _ in range(4):
                            self._next()
                            if not self.ch:
                                raise ValueError("unterminated unicode escape")
                            digits += self.ch
                        out.append(chr(int(digits, 16)))
                        self._next()
                        continue
                    out.append({"\"": '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}.get(esc, esc))
                elif esc == "u":
                    for _ in range(4):
                        self._next()
                        if not self.ch:
                            raise ValueError("unterminated unicode escape")
                self._next()
                continue
            if capture:
                out.append(c)
            self._next()
        raise ValueError("unterminated JSON string")

    def _value(self, depth: int = 0) -> None:
        if depth > 100:
            raise ValueError("JSON nesting exceeds metadata scan limit")
        self._ws()
        if not self.ch:
            raise ValueError("unexpected JSON end")
        if self.ch == '"':
            self._string(False)
            return
        if self.ch in "[{":
            close = "]" if self.ch == "[" else "}"
            self._next()
            self._ws()
            if self.ch == close:
                self._next()
                return
            while self.ch:
                if self.ch == '"':
                    self._string(False)
                    self._ws()
                    if self.ch == ":":
                        self._next()
                        self._value(depth + 1)
                else:
                    self._value(depth + 1)
                self._ws()
                if self.ch in ",:":
                    self._next()
                    self._ws()
                    continue
                if self.ch == close:
                    self._next()
                    return
                raise ValueError("invalid JSON structure")
            raise ValueError("unterminated JSON container")
        while self.ch and self.ch not in ",]} \t\r\n":
            self._next()

    def top_keys(self) -> list[str]:
        self._next()
        self._ws()
        if self.ch != "{":
            return []
        self._next()
        self._ws()
        keys: list[str] = []
        if self.ch == "}":
            return keys
        while self.ch:
            if self.ch != '"':
                raise ValueError("invalid JSON object key")
            keys.append(self._string(True))
            self._ws()
            if self.ch != ":":
                raise ValueError("missing JSON colon")
            self._next()
            self._value()
            self._ws()
            if self.ch == "}":
                return keys
            if self.ch != ",":
                raise ValueError("invalid JSON object separator")
            self._next()
            self._ws()
        raise ValueError("unterminated JSON object")


def _sqlite_schema(path: Path) -> dict[str, Any]:
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True, timeout=1)
    try:
        con.execute("PRAGMA query_only=ON")
        objects = con.execute(
            "SELECT type, name, tbl_name FROM sqlite_master "
            "WHERE (type='table' AND name NOT LIKE 'sqlite_%') OR type='index' ORDER BY type,name"
        ).fetchall()
        tables: list[dict[str, Any]] = []
        indexes: list[dict[str, Any]] = []
        for obj_type, name, table in objects:
            if obj_type == "table":
                cols = con.execute("SELECT name, type FROM pragma_table_info(?)", (name,)).fetchall()
                tables.append({"name": name, "columns": [{"name": row[0], "type": row[1]} for row in cols]})
            else:
                ix = con.execute("SELECT name FROM pragma_index_info(?)", (name,)).fetchall()
                indexes.append({"name": name, "table": table, "columns": [row[0] for row in ix if row[0] is not None]})
        return {"tables": tables, "indexes": indexes}
    finally:
        con.close()


def _empty_stats() -> dict[str, Any]:
    return {"file_count": 0, "size_bytes": 0, "types": {}}


def _add_file(stats: dict[str, Any], path: Path, size: int) -> None:
    kind = _kind(path)
    stats["file_count"] += 1
    stats["size_bytes"] += size
    cell = stats["types"].setdefault(kind, {"count": 0, "size_bytes": 0})
    cell["count"] += 1
    cell["size_bytes"] += size


def _merge_stats(a: dict[str, Any], b: dict[str, Any]) -> None:
    a["file_count"] += b["file_count"]
    a["size_bytes"] += b["size_bytes"]
    for kind, val in b["types"].items():
        cell = a["types"].setdefault(kind, {"count": 0, "size_bytes": 0})
        cell["count"] += val["count"]
        cell["size_bytes"] += val["size_bytes"]


def _make_node(path: Path, root: Path, account_ids: list[str], exclusions: list[dict[str, str]], findings: list[dict[str, Any]], files: list[dict[str, Any]]) -> dict[str, Any]:
    direct = _empty_stats()
    children: list[dict[str, Any]] = []
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.casefold())
    except OSError:
        return {"name": _mask_name(path.name, account_ids), "stats": direct, "children": [], "scan_status": "不可读取"}
    account_root = path.name.casefold() == "accounts"
    account_dirs: list[Path] = []
    for ent in entries:
        child = Path(ent.path)
        name = ent.name
        if _excluded_name(name):
            exclusions.append({"path": "已排除", "status": "已排除"})
            continue
        if name.casefold() in SENSITIVE_DIRS:
            exclusions.append({"path": "已排除", "status": "已排除"})
            continue
        try:
            is_linkish = ent.is_symlink() or _is_reparse(child)
            if is_linkish:
                exclusions.append({"path": _relative_mask(child, root, account_ids), "status": "已排除（链接/重解析点）"})
                continue
            if ent.is_dir(follow_symlinks=False):
                if not _within(child, root):
                    exclusions.append({"path": _relative_mask(child, root, account_ids), "status": "已排除（根目录外）"})
                    continue
                if account_root:
                    account_dirs.append(child)
                else:
                    children.append(_make_node(child, root, account_ids, exclusions, findings, files))
            elif ent.is_file(follow_symlinks=False):
                size = ent.stat(follow_symlinks=False).st_size
                _add_file(direct, child, size)
                _inspect_metadata_file(child, root, size, account_ids, exclusions, findings)
                if _include_file_metadata(child, root):
                    files.append({"path": _relative_mask(child, root, account_ids), "name": _mask_name(name, account_ids), "type": _kind(child), "size_bytes": size})
        except OSError:
            continue
    if account_root and account_dirs:
        merged: dict[str, dict[str, Any]] = {}
        samples: list[str] = []
        for account_dir in account_dirs:
            account_ids.append(account_dir.name)
            if len(samples) < 5:
                samples.append(_mask_account(account_dir.name))
            account_node = _make_node(account_dir, root, account_ids, exclusions, findings, files)
            _merge_template(merged, account_node, "")
        children.append({"name": "<账号模板，合并>", "account_count": len(account_dirs), "masked_samples": samples, "templates": [merged[k] for k in sorted(merged)]})
    total = {"file_count": direct["file_count"], "size_bytes": direct["size_bytes"], "types": json.loads(json.dumps(direct["types"]))}
    for child in children:
        if "stats" in child:
            _merge_stats(total, child["stats"])
        elif child.get("name") == "<账号模板，合并>":
            for tmpl in child["templates"]:
                if tmpl["path_template"] != ".":
                    continue
                _merge_stats(total, tmpl["stats"])
    return {"name": _mask_name(path.name, account_ids), "direct": direct, "stats": total, "children": children}


def _merge_template(out: dict[str, dict[str, Any]], node: dict[str, Any], rel: str) -> None:
    key = rel or "."
    item = out.setdefault(key, {"path_template": key, "stats": _empty_stats(), "child_direct": _empty_stats()})
    _merge_stats(item["child_direct"], node.get("direct", _empty_stats()))
    for child in node.get("children", []):
        if "stats" in child:
            _merge_template(out, child, (rel + "/" + child["name"]).strip("/"))
    _merge_stats(item["stats"], node.get("stats", _empty_stats()))


def _inspect_metadata_file(path: Path, root: Path, size: int, account_ids: list[str], exclusions: list[dict[str, str]], findings: list[dict[str, Any]]) -> None:
    suffix = path.suffix.casefold()
    rel = _relative_mask(path, root, account_ids)
    low = path.name.casefold()
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        try:
            schema = _sqlite_schema(path)
            wal = Path(str(path) + "-wal")
            schema["wal_uncheckpointed_present"] = wal.exists() and wal.stat().st_size > 0
            findings.append({"path": rel, "kind": "sqlite_schema", "schema": _mask_object(schema, account_ids)})
        except (sqlite3.Error, OSError, ValueError) as exc:
            findings.append({"path": rel, "kind": "sqlite_schema", "status": "无法读取结构", "error_type": type(exc).__name__})
    elif suffix == ".json":
        parent_parts = [part.casefold() for part in path.relative_to(root).parts[:-1]]
        if low not in JSON_METADATA_ALLOWLIST or any(any(word in part for word in SKIP_JSON_WORDS) for part in parent_parts):
            exclusions.append({"path": "内容型或非白名单 JSON（已排除）", "status": "已排除，未打开"})
            return
        try:
            findings.append({"path": rel, "kind": "json_top_level_keys", "keys": [_mask_name(k, account_ids) for k in _stream_json_top_keys(path)]})
        except (OSError, UnicodeError, ValueError):
            findings.append({"path": rel, "kind": "json_top_level_keys", "status": "未能安全提取顶层键"})


def _mask_account(name: str) -> str:
    return name[:4] + "***" if name else "***"


def _include_file_metadata(path: Path, root: Path) -> bool:
    """Keep structural/operational names while suppressing content filenames."""
    rel_parts = [p.casefold() for p in path.relative_to(root).parts
                 if p.casefold() not in {"accounts"}]
    if any(not _safe_name(part) for part in rel_parts):
        return False
    suffix = path.suffix.casefold()
    if suffix in {'.db', '.sqlite', '.sqlite3', '.exe', '.dll', '.pyd', '.bin', '.dat',
                  '.onnx', '.model', '.pt', '.pth', '.safetensors', '.pak', '.rcc', '.wasm', '.blob'}:
        return True
    if path.name.casefold().endswith(('-wal', '-shm')) or '.log' in path.name.casefold():
        return True
    if any(part in {'models', 'model', 'logs'} for part in rel_parts[:-1]):
        return True
    filename_block_words = tuple(word for word in CONTENT_WORDS if word != "model")
    if any(any(word in part for word in filename_block_words) for part in rel_parts):
        return False
    if any(not _safe_name(part) for part in rel_parts):
        return False
    lowname = path.name.casefold()
    if ".log" in lowname:
        return True
    return path.suffix.casefold() in {
        ".db", ".sqlite", ".sqlite3", ".json", ".log", ".exe", ".dll",
        ".pyd", ".bin", ".dat", ".model", ".onnx", ".pt", ".pth", ".safetensors",
        ".pak", ".rcc", ".blob", ".wasm", ".zip", ".cab", ".msi", ".lib", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    }


def _mask_name(name: str, account_ids: list[str]) -> str:
    out = name
    for account_id in sorted(set(account_ids), key=len, reverse=True):
        if account_id:
            out = re.sub(re.escape(account_id), _mask_account(account_id), out, flags=re.IGNORECASE)
    return out


def _mask_object(value: Any, account_ids: list[str]) -> Any:
    if isinstance(value, str):
        return _mask_name(value, account_ids)
    if isinstance(value, list):
        return [_mask_object(v, account_ids) for v in value]
    if isinstance(value, dict):
        return {k: _mask_object(v, account_ids) for k, v in value.items()}
    return value


def _account_template_path(path: str, account_ids: list[str]) -> str:
    parts = path.replace("\\", "/").split("/")
    known = {x.casefold() for x in account_ids} | {_mask_account(x).casefold() for x in account_ids}
    for index, part in enumerate(parts):
        parent = parts[index - 1].casefold() if index else ""
        if parent == "accounts" or (parent == "connections" and part.casefold() in known):
            parts[index] = "<账号模板>"
    return "/".join(parts)


def _aggregate_account_records(records: list[dict[str, Any]], account_ids: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        normalized = _account_template_path(record.get("path", ""), account_ids)
        if normalized == record.get("path", ""):
            grouped.setdefault("single:" + json.dumps(record, ensure_ascii=False, sort_keys=True), {**record, "instance_count": 1})
            continue
        key_record = {k: v for k, v in record.items() if k not in {"path", "instance_count"}}
        signature = normalized + "|" + json.dumps(key_record, ensure_ascii=False, sort_keys=True)
        if signature not in grouped:
            grouped[signature] = {**record, "path": normalized, "instance_count": 0}
        grouped[signature]["instance_count"] += 1
    return sorted(grouped.values(), key=lambda x: (x.get("path", "").casefold(), json.dumps(x, ensure_ascii=False, sort_keys=True)))


def _relative_mask(path: Path, root: Path, account_ids: list[str]) -> str:
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = path.name
    parts = Path(rel).parts
    if "accounts" in [part.casefold() for part in parts]:
        ix = [part.casefold() for part in parts].index("accounts")
        if ix + 1 < len(parts):
            p = list(parts)
            p[ix + 1] = _mask_account(p[ix + 1])
            rel = str(Path(*p))
    return _mask_name(rel, account_ids)


def _purpose(name: str) -> str:
    low = name.casefold()
    if low in SENSITIVE_DIRS or _excluded_name(name):
        return "已排除"
    if low in {"release-keys", "server"}:
        return "已排除"
    if "test-tmp" in low:
        return "临时测试产物"
    if "cache" in low or "cache" in Path(name).parts:
        return "缓存"
    if low in {"logs", "log"}:
        return "日志目录（内容不读取）"
    if low in {"models", "model"}:
        return "模型目录（内容不读取）"
    if low in {"accounts", "account"}:
        return "按账号隔离的数据目录"
    purposes = {
        'tasks': '员工任务文件（不深入）', 'receipts': '员工回执（不深入）',
        'node': 'Node.js 工具运行时（按目录名推断）', 'web-deps': '前端依赖（按目录名推断）',
        'java': 'Java 工具运行时（按目录名推断）', 'interview': '学习/面试输出（不读取）',
        'automations': '自动任务配置（不读取）', 'backups': '备份目录（不读取）'}
    return purposes.get(low, '用途未确认；仅列目录名，不深入')


def _local_first_layer(path: Path) -> dict[str, Any]:
    result = {"path": str(path), "entries": []}
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.casefold())
    except OSError:
        result["status"] = "不可读取"
        return result
    for ent in entries:
        if ent.name.casefold() in SENSITIVE_DIRS or _excluded_name(ent.name):
            result["entries"].append({"name": "已排除", "purpose": "已排除，不进入"})
            continue
        result["entries"].append({"name": ent.name, "purpose": _purpose(ent.name)})
    return result


def _main_head() -> str | None:
    try:
        p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(MAIN_REPO), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


_SENSITIVE = re.compile(r"token|key|secret|password", re.I)
_GENERIC = {
    "data", "id", "name", "type", "path", "file", "files", "account",
    "accounts", "learning", "material", "materials", "store", "db",
    "sqlite", "json", "log", "logs", "cache", "index", "metadata",
}
_PERSISTENT_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}
_API = re.compile(
    r"(?P<api>write_text|write_bytes|unlink|replace|mkdir|rmdir|makedirs|remove|dump|dumps|read_text|read_bytes|load|loads|connect|glob|iterdir|walk|exists|stat|fetchone|fetchmany|fetchall)\s*\(|"
    r"(?P<open>open)\s*\([^\n]*[, ]['\"](?P<mode>[rw])[a-z+]*['\"]|"
    r"(?P<execute>\.execute)\s*\(", re.I)
_SQL = re.compile(r"\b(SELECT\b.*\bFROM\b|INSERT\s+INTO\b|UPDATE\b|DELETE\s+FROM\b|CREATE\s+(?:(?:VIRTUAL\s+)?TABLE|(?:UNIQUE\s+)?INDEX)\b|DROP\s+(?:(?:VIRTUAL\s+)?TABLE|(?:UNIQUE\s+)?INDEX)\b|ALTER\s+TABLE\b|REPLACE\s+INTO\b)", re.I)
_SQL_START = re.compile(r"\b(SELECT\b|INSERT\s+INTO\b|UPDATE\b|DELETE\s+FROM\b|CREATE\s+(?:(?:VIRTUAL\s+)?TABLE|(?:UNIQUE\s+)?INDEX)\b|DROP\s+(?:(?:VIRTUAL\s+)?TABLE|(?:UNIQUE\s+)?INDEX)\b|ALTER\s+TABLE\b|REPLACE\s+INTO\b)", re.I)
_DEFINE = re.compile(r"^\s*(?:class|def|async\s+def)\s+", re.I)
_REF = re.compile(r"\b(?:str|Path|PurePath|os\.path)\b|['\"](?:[^'\"]*/)?[^'\"]+\.(?:json|db|sqlite|sqlite3|log|txt|md|csv)\b", re.I)


def _clean_strings(value: Any, parent_key: str = "") -> Iterable[tuple[str, str]]:
    """Yield only filenames and database table identifiers from inventory JSON."""
    if isinstance(value, dict):
        for key, child in value.items():
            k = str(key).lower()
            if isinstance(child, str):
                if k in {"path", "relative_path", "file_path", "filepath"} and Path(child).suffix.lower() in _PERSISTENT_SUFFIXES:
                    yield "path", child
                elif k in {"filename", "file_name", "basename", "name"} and parent_key in {"files", "file_inventory"}:
                    yield "file", child
                elif k in {"table", "table_name"}:
                    yield "table", child
                elif k == "name" and parent_key in {"tables", "table_names"}:
                    yield "table", child
                elif k in {"tables", "table_names", "files", "file_inventory"}:
                    yield from _clean_strings(child, k)
            else:
                yield from _clean_strings(child, k)
    elif isinstance(value, list):
        for item in value:
            yield from _clean_strings(item, parent_key)
    elif isinstance(value, str) and parent_key in {"tables", "table_names"}:
        yield "table", value
    elif isinstance(value, str) and parent_key in {"files", "file_inventory"}:
        yield "file", value


def _terms(report: Any) -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for kind, raw in _clean_strings(report):
        text = raw.replace("\\", "/").strip(" /")
        # Never use account identifiers as search terms. File basenames and
        # schema table identifiers are stable; parent folders are too broad.
        if kind == "path":
            raw_parts = [p for p in text.split("/") if p]
            if raw_parts:
                basename = raw_parts[-1]
                if Path(basename).suffix.lower() in _PERSISTENT_SUFFIXES and not re.fullmatch(r"[0-9a-f-]{16,}(?:\.[^.]+)?", basename, re.I):
                    found.add(("file", basename))
        elif text:
            found.add((kind, text))
    return sorted(
        (k, t) for k, t in found
        if (k == "table" or (len(t) >= 4 and not _SENSITIVE.search(t) and t.casefold() != "auth.json"))
        and (k == "table" or t.lower() not in _GENERIC)
    )


def _operation(line: str, function_name: str, sql_context: str = "") -> tuple[str, str, int]:
    """Return role, allowlisted API/SQL marker and evidence priority."""
    code = line.partition("#")[0]
    if code.strip():
        match = _API.search(code)
        if match:
            api = match.group("api")
            if api:
                api = api.casefold()
            elif match.group("open"):
                api = f"open('{match.group('mode').casefold()}')"
            else:
                api = "execute"
            sql = _SQL.search(code)
            if api == "execute" and sql:
                action = sql.group(1).split()[0].upper()
                if action in {"INSERT", "UPDATE", "DELETE", "CREATE", "REPLACE", "DROP", "ALTER"}:
                    return "写入调用", api, 3
                return "读取调用", api, 3
            if api == "execute":
                if sql_context in {"INSERT", "UPDATE", "DELETE", "CREATE", "REPLACE", "DROP", "ALTER"}:
                    return "写入调用", api, 3
                if sql_context == "SELECT":
                    return "读取调用", api, 3
                return "数据库调用（方向未确定）", api, 1
            if api in {'dumps', 'loads'}:
                return '序列化（非文件I/O）', api, 1
            if api in {'dump', 'load'}:
                return '流序列化（目标待核实）', api, 1
            if api in {'replace', 'remove'}:
                return '同名调用（未证明文件I/O）', api, 1
            if api == "connect":
                return "数据库连接（非读写）", api, 1
            if api in {"stat", "exists", "glob", "iterdir", "walk"}:
                return "文件系统元数据（非正文读取）", api, 1
            if api == "open('r')":
                return "打开读模式（未证明正文读取）", api, 1
            if api in {"write_text", "write_bytes", "unlink", "replace", "mkdir", "rmdir", "makedirs", "remove", "dump", "dumps", "open('w')"}:
                return "写入调用", api, 3
            return "读取调用", api, 3
        sql = _SQL.search(code)
        if sql:
            action = sql.group(1).split()[0].upper()
            if action in {"INSERT", "UPDATE", "DELETE", "CREATE", "REPLACE", "DROP", "ALTER"}:
                return "写入SQL（静态语句）", action, 2
            return "读取SQL（静态语句）", action, 2
        if sql_context:
            if sql_context in {"INSERT", "UPDATE", "DELETE", "CREATE", "REPLACE", "DROP", "ALTER"}:
                return "写入SQL（静态语句）", sql_context, 2
            return "读取SQL（静态语句）", sql_context, 2
    if _DEFINE.search(line):
        return "定义（非I/O证据）", "", 0
    return "引用（仅命中，未证明I/O）", "", 0


def _owner_is_reparse(path: Path) -> bool:
    """Avoid following Windows junctions and any other reparse points."""
    try:
        if path.is_symlink():
            return True
        if hasattr(os.path, "isjunction") and os.path.isjunction(path):
            return True
        import stat
        return bool(path.lstat().st_file_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return True


def _source_references(report: Any, source_root: str | os.PathLike[str], *, max_hits_per_term: int = 8) -> dict[str, Any]:
    """Return compact source evidence for inventory names/tables.

    `report` is the already-built inventory dict. Results intentionally
    contain no source text, account IDs, or values from user data.
    """
    if not isinstance(report, dict):
        raise TypeError('source association requires an in-memory metadata inventory')
    root = Path(source_root)
    terms = _terms(report)
    by_term: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {t: [] for t in terms}
    if not root.is_dir() or _owner_is_reparse(root):
        return {"evidence": [], "unmatched": [{"kind": k, "term": t} for k, t in terms], "note": "source root unavailable"}

    pattern = re.compile("|".join(re.escape(term) for _, term in sorted(terms, key=lambda x: len(x[1]), reverse=True)), re.I) if terms else None
    term_index: dict[str, list[tuple[str, str]]] = {}
    for item in terms:
        term_index.setdefault(item[1].casefold(), []).append(item)
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            d for d in dirs
            if not _SENSITIVE.search(d) and d not in {"__pycache__", ".git", 'dist', 'node_modules', '.venv', 'build', 'target'}
            and not _owner_is_reparse(current_path / d)
        ]
        for filename in files:
            if not filename.endswith((".py", '.pyw', '.java', '.ts', '.tsx', '.sql', '.js')) or filename.endswith('.min.js') or _SENSITIVE.search(filename) or _owner_is_reparse(current_path / filename):
                continue
            path = Path(current) / filename
            # Source files are scanned incrementally; no source line is retained
            # in the output. Skip unreadable/invalid files without widening scope.
            try:
                function_name = ""
                triple_quote = None
                sql_context = ""
                with path.open("r", encoding="utf-8", errors="replace") as src:
                    for lineno, line in enumerate(src, 1):
                        if not pattern:
                            continue
                        if _DEFINE.search(line):
                            fn = re.search(r"\b(?:def|async\s+def)\s+([A-Za-z_]\w*)", line)
                            if fn:
                                function_name = fn.group(1)
                        code_line = line.partition("#")[0]
                        is_comment = not code_line.strip() or (triple_quote is not None and code_line.lstrip().startswith("--"))
                        if is_comment:
                            code_line = ""
                        was_in_triple = triple_quote is not None
                        opened_here = False
                        closes_here = False
                        if not is_comment:
                            if not was_in_triple:
                                if "\"\"\"" in code_line:
                                    triple_quote = '\"\"\"'
                                    opened_here = True
                                elif "'''" in code_line:
                                    triple_quote = "'''"
                                    opened_here = True
                            sql_match = _SQL_START.search(code_line)
                            if sql_match and (was_in_triple or opened_here):
                                sql_context = sql_match.group(1).split()[0].upper()
                            line_sql_context = sql_context if (was_in_triple or opened_here) else ""
                            if was_in_triple and triple_quote in code_line:
                                closes_here = True
                            elif opened_here and code_line.count(triple_quote) >= 2:
                                closes_here = True
                        else:
                            line_sql_context = ""
                        matched_terms = set()
                        for match in pattern.finditer(code_line):
                            for item in term_index.get(match.group(0).casefold(), ()):
                                # Database identifiers require whole-token
                                # boundaries; plans does not match plans_ready.
                                if item[0] == "table" and not re.search(
                                    rf"(?<![A-Za-z0-9_]){re.escape(item[1])}(?![A-Za-z0-9_])", code_line, re.I
                                ):
                                    continue
                                matched_terms.add(item)
                        for item in matched_terms:
                            role, api, score = _operation(code_line, function_name, line_sql_context)
                            candidate = {
                                "source": path.relative_to(root).as_posix(),
                                "line": lineno,
                                "symbol": "" if _SENSITIVE.search(function_name) else function_name,
                                "role": role,
                                "api": api,
                            }
                            hits = by_term[item]
                            if not any(h[1]["source"] == candidate["source"] and h[1]["line"] == lineno for h in hits):
                                hits.append((score, candidate))
                                hits.sort(key=lambda h: (-h[0], h[1]["source"], h[1]["line"]))
                                del hits[max_hits_per_term:]
                        if closes_here:
                            triple_quote = None
                            sql_context = ""
            except (OSError, UnicodeError, ValueError):
                continue
    evidence = []
    unmatched = []
    for (kind, term), hits in by_term.items():
        if hits:
            evidence.append({"kind": kind, "term": term, "hits": [h[1] for h in hits]})
        else:
            unmatched.append({"kind": kind, "term": term})
    return {
        "evidence": evidence,
        "unmatched": unmatched,
        "note": "扫描py/pyw/java/ts/tsx/sql/js源文件，跳过构建及依赖目录。字符串命中仅为静态候选；同名表不保证属于同一本地库，SQL字串不代表已运行。函数名按近邻def近似；读写角色需结合API与路径核实，未知明确标注。",
    }



def enrich_owners(report: dict[str, Any], source_root: Path) -> dict[str, Any]:
    report['owner_mappings'] = _source_references(report, source_root)
    report['owner_mappings']['source_root'] = str(source_root)
    report['owner_mappings']['generated_at'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    report['owner_mappings']['source_head'] = _main_head()
    return report

def _collapse_known_accounts(node, ids):
    known = {value.casefold() for value in ids} | {_mask_account(value).casefold() for value in ids}
    children = node.get('children', [])
    for child in children:
        _collapse_known_accounts(child, ids)
    matches = [child for child in children if child.get('name', '').casefold() in known and 'stats' in child]
    if not matches:
        return
    merged = {}
    for child in matches:
        _merge_template(merged, child, '')
    node['children'] = [child for child in children if child not in matches] + [{
        'name': '<账号模板，合并>', 'account_count': len(matches),
        'masked_samples': [_mask_account(child['name']) for child in matches[:5]],
        'templates': [merged[key] for key in sorted(merged)]}]


def scan(roots: list[Path], local_root: Path) -> dict[str, Any]:
    exclusions: list[dict[str, str]] = []
    findings: list[dict[str, Any]] = []
    scanned: list[dict[str, Any]] = []
    all_files: list[dict[str, Any]] = []
    account_ids: list[str] = []
    for root in roots:
        if not root.exists():
            scanned.append({"path": str(root), "status": "不存在"})
            continue
        if _is_reparse(root):
            scanned.append({"path": str(root), "status": "已排除（根为链接/重解析点）"})
            continue
        before = (len(exclusions), len(findings), len(all_files))
        node = _make_node(root, root, account_ids, exclusions, findings, all_files)
        for collection, offset in zip((exclusions, findings, all_files), before):
            for item in collection[offset:]:
                item['root'] = str(root)
        node["path"] = str(root)
        scanned.append(node)
    for node in scanned:
        _collapse_known_accounts(node, account_ids)
    report = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "main_head": _main_head(),
        "roots": scanned,
        "files": _aggregate_account_records(all_files, account_ids),
        "local_first_layer": _local_first_layer(local_root),
        "metadata_findings": _aggregate_account_records(findings, account_ids),
        "exclusions": exclusions,
        "owner_mappings": [],
        "privacy": {
            "sqlite": "sqlite_master、表字段和索引元数据；不查数据行；只读 immutable 连接",
            "json": "仅显式元数据 basename 白名单(settings/preferences/config/manifest/version)提取顶层键；其他 JSON 与内容目录内 JSON 均未打开；键外值只流式跳过且不解码留存；每个文件上限 1 MiB",
            "logs_models_binaries": "仅文件类型、数量、大小；不读取内容",
            "reparse_points": "跳过，不跟随链接",
        },
    }
    # IDs discovered under accounts/ may also appear in earlier-sorted paths
    # such as connections/<id>; scrub the finished metadata map after discovery.
    return _mask_object(report, account_ids)


def _markdown(report: dict[str, Any]) -> str:
    lines = ["# MentorFlow 数据目录地图", "", f"- 生成时间：{report['generated_at']}", f"- 主仓库提交：{report.get('main_head') or '不可用'}", "", "## 数据目录", ""]

    def emit(node: dict[str, Any], depth: int = 0) -> None:
        pad = "  " * depth
        if "stats" not in node:
            lines.append(f"{pad}- {node.get('path')}: {node.get('status')}")
            return
        st = node["stats"]
        kinds = ", ".join(f"{k} {v['count']} 个/{v['size_bytes']} B" for k, v in sorted(st["types"].items())) or "无文件"
        lines.append(f"{pad}- {node.get('name', node.get('path'))}：{st['file_count']} 个文件，{st['size_bytes']} B；{kinds}")
        for child in node.get("children", []):
            if child.get("name") == "<账号模板，合并>":
                lines.append(f"{pad}  - 每个账号一份，共 {child['account_count']} 个；掩码样例：{', '.join(child['masked_samples']) or '无'}")
                for tmpl in child["templates"]:
                    ts = tmpl["stats"]
                    tk = ", ".join(f"{k} {v['count']} 个/{v['size_bytes']} B" for k, v in sorted(ts["types"].items())) or "无文件"
                    lines.append(f"{pad}    - 模板 {tmpl['path_template']}：{ts['file_count']} 个文件，{ts['size_bytes']} B；{tk}")
            else:
                emit(child, depth + 1)

    for root in report["roots"]:
        emit(root)
    lines += ["", "## .local 第一层", ""]
    for ent in report.get("local_first_layer", {}).get("entries", []):
        lines.append(f"- {ent['name']}：{ent['purpose']}")
    lines += ["", "## SQLite / JSON 元数据", ""]
    for item in report.get("metadata_findings", []):
        if item["kind"] == "sqlite_schema":
            lines.append(f"- `{item.get('root', '')}/{item['path']}` SQLite（{item.get('instance_count', 1)} 份）：")
            if item.get("schema") is not None:
                for table in item["schema"]["tables"]:
                    lines.append(f"  - 表 `{table['name']}`：" + ", ".join(f"`{c['name']}` {c['type']}" for c in table["columns"]))
                for idx in item["schema"]["indexes"]:
                    lines.append(f"  - 索引 `{idx['name']}` ({idx['table']})：" + ", ".join(idx["columns"]))
                if item["schema"].get("wal_uncheckpointed_present"):
                    lines.append("  - 存在非空 WAL；immutable 结构快照不读取 WAL，可能未包含尚未检查点的结构变化。")
            else:
                lines.append(f"  - {item.get('status', '结构不可用')}")
        else:
            if "keys" in item:
                lines.append(f"- `{item.get('root', '')}/{item['path']}` JSON 顶层键：{', '.join(item['keys']) or '无'}")
            else:
                lines.append(f"- `{item['path']}` JSON：{item.get('status', '已跳过')}")
    lines += ["", "## 文件名元数据（日志、模型、二进制及结构化文件；未读取内容）", ""]
    for item in report.get("files", []):
        lines.append(f"- `{item.get('root', '')}/{item['path']}`：{item['type']}，每份{item['size_bytes']} B，{item.get('instance_count', 1)} 份")
    lines += ["", "## 谁写谁读（静态证据）", ""]
    mapping = report.get('owner_mappings') or {}
    lines.append(mapping.get('note', ''))
    for evidence in mapping.get('evidence', []):
        lines.append(f"- {evidence['kind']} `{evidence['term']}`：")
        for hit in evidence['hits']:
            location = str(MAIN_REPO / 'refactor' / hit['source']).replace(chr(92), '/')
            lines.append(f"  - [{hit['source']}:{hit['line']}]({location}:{hit['line']})：{hit['role']}；{hit.get('api', '')}；函数 {hit.get('symbol', '') or '未确定'}")
    lines += ['', '未命中：' + ', '.join(x['term'] for x in mapping.get('unmatched', [])),
              '', '统计仅包括未排除项；正文文件只统计类型/大小，未打开。存在WAL时仅主库结构快照，非跨文件事务快照。',
              '', '## 排除项', '']
    lines.extend(f"- {x['path']}：{x['status']}" for x in report.get("exclusions", []))
    return "\n".join(lines) + "\n"


def _output_dir(value: Path) -> Path:
    if value.exists() and _is_reparse(value):
        raise ValueError('输出目录不得为链接')
    for name in ('data_map.json', 'data_map.md'):
        target = value / name
        if target.is_symlink() or (target.exists() and _is_reparse(target)):
            raise ValueError('输出文件不得为链接')
        if target.exists() and target.stat(follow_symlinks=False).st_nlink > 1:
            raise ValueError('输出文件不得为硬链接')
    return value.resolve()


def query_map(path: Path, query: str, *, limit: int = 5, max_bytes: int = 12000) -> str:
    """Stream bounded excerpts from an already-generated safe Markdown map.

    Match a case-insensitive keyword, path or table name. Include nearby lines
    for context, a section and line number. Never load the complete map; the
    output ceiling includes UTF-8 headers and a truncation notice.
    """
    from collections import deque
    if not query.strip() or not 1 <= limit <= 50 or not 512 <= max_bytes <= 65536:
        raise ValueError('查询不能为空；limit须为1–50，max-bytes须为512–65536')
    if path.is_symlink() or _is_reparse(path):
        raise ValueError('查询文件不得为链接/重解析点')
    needle = query.replace('\\', '/').casefold()
    lines = [f'# 数据地图查询：{query[:40]}', '匹配当前已生成地图；未重扫用户数据。']
    size = len(('\n'.join(lines) + '\n').encode('utf-8'))
    reserve = len('\n…输出已截断；可缩小关键词或增加 --limit/--max-bytes。\n'.encode('utf-8'))
    previous = deque(maxlen=2)
    section = ''
    hits = 0
    following = 0
    truncated = False
    emitted = set()

    def append(text: str) -> bool:
        nonlocal size
        # A directory's file-type summary itself can be a very long line.
        offset = max(0, text.replace('\\', '/').casefold().find(needle) - 220) if len(text) > 900 else 0
        shortened = ('…' if offset else '') + text[offset:offset + 900] + ('…' if len(text) > offset + 900 else '')
        cost = len((shortened + '\n').encode('utf-8'))
        if size + cost > max_bytes - reserve:
            return False
        lines.append(shortened)
        size += cost
        return True

    with path.open('r', encoding='utf-8') as stream:
        for number, raw in enumerate(stream, 1):
            line = raw.rstrip('\r\n')
            if number <= 4 and ('生成时间' in line or '主仓库提交' in line):
                append(line)
            if line.startswith('## '):
                section = line[3:]
            matched = needle in line.replace('\\', '/').casefold()
            if matched and hits < limit:
                hits += 1
                if not append(f'\n匹配 {hits}｜{section}｜第 {number} 行'):
                    truncated = True
                    break
                for index, prior in previous:
                    if index not in emitted:
                        if not append(prior):
                            truncated = True
                            break
                        emitted.add(index)
                if truncated:
                    break
                following = 6
            if following and number not in emitted:
                if not append(line):
                    truncated = True
                    break
                emitted.add(number)
                following -= 1
            previous.append((number, line))
            if hits >= limit and following == 0:
                truncated = True
                break
    if not hits:
        append('没有匹配；请换用文件名、表名或路径中的关键词。')
    if truncated:
        lines.append('…输出已截断；可缩小关键词或增加 --limit/--max-bytes。')
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    # Windows Python may otherwise emit cp936 into Codex/dots' UTF-8 pipe.
    if hasattr(sys.stdout, 'reconfigure') and sys.stdout.encoding and sys.stdout.encoding.lower() not in {'utf-8', 'utf8'}:
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / '.local/project-map', help="输出/查询目录，默认仓库.local/project-map")
    ap.add_argument('--app-root', type=Path, default=DEFAULT_ROOTS[0], help='安装版数据根（默认D:/MentorFlowApp）')
    ap.add_argument('--data-root', type=Path, default=DEFAULT_ROOTS[1], help='用户数据根（默认D:/MentorFlowData）')
    ap.add_argument('--local-root', type=Path, default=LOCAL_ROOT, help='只列第一层的.local，默认当前仓库.local')
    ap.add_argument('--query', help='只查询已有地图：关键词、路径或表名；不重扫数据')
    ap.add_argument('--limit', type=int, default=5, help='查询最多匹配数，1–50（默认5）')
    ap.add_argument('--max-bytes', type=int, default=12000, help='查询UTF-8输出上限，512–65536（默认12000）')
    args = ap.parse_args(argv)
    try:
        out = _output_dir(args.out)
        roots = [args.app_root.absolute(), args.data_root.absolute()]
        if any(_within(out, root) for root in roots):
            raise ValueError('输出不得位于被扫描的用户数据根内')
        if args.query is not None:
            print(query_map(out / 'data_map.md', args.query, limit=args.limit, max_bytes=args.max_bytes), end='')
            return 0
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    started = time.perf_counter()
    report = scan(roots, args.local_root)
    report = enrich_owners(report, MAIN_REPO / "refactor")
    report["duration_seconds"] = round(time.perf_counter() - started, 3)
    out.mkdir(parents=True, exist_ok=True)
    stem = "data_map"
    (out / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / f"{stem}.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"markdown": str(out / f'{stem}.md'), "json": str(out / f'{stem}.json'), "duration_seconds": report["duration_seconds"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

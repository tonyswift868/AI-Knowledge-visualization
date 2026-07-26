from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import uuid
import csv
import io
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from .provider_runtime import generation_meta, provider_for_session, require_provider_session
from .provider_service import ProviderError


router = APIRouter(prefix="/api/active-teaching", tags=["active-teaching"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_ROOT = Path(
    os.getenv(
        "MENTORFLOW_ACTIVE_UPLOAD_ROOT",
        str(PROJECT_ROOT / "data" / "active_teaching" / "uploads"),
    )
).expanduser().resolve()
SUPPORTED_SUFFIXES = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".pdf", ".docx",
    ".csv", ".json", ".yaml", ".yml", ".xml", ".rtf",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h",
    ".go", ".rs", ".sql", ".sh", ".ps1",
    ".pptx", ".xlsx", ".epub",
}
CONFUSION_MARKERS = ("不懂", "不理解", "为什么", "再解释", "没明白", "听不懂", "看不懂")
UNDERSTOOD_MARKERS = ("懂了", "明白了", "理解了", "会了", "可以继续")
MAX_MATERIAL_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_text(value: str) -> str:
    return _hash_bytes(value.encode("utf-8", errors="ignore"))


def _normalise(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _db_path() -> Path:
    from . import orm

    return Path(orm.ORM_DB)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    _init_schema(connection)
    return connection


def _init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS active_materials (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            status TEXT NOT NULL,
            parse_error TEXT NOT NULL DEFAULT '',
            page_count INTEGER NOT NULL DEFAULT 0,
            parser_name TEXT NOT NULL,
            UNIQUE(workspace_id, sha256)
        );
        CREATE TABLE IF NOT EXISTS active_chunks (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            material_id TEXT NOT NULL REFERENCES active_materials(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            page_start INTEGER,
            page_end INTEGER,
            paragraph_start INTEGER NOT NULL,
            paragraph_end INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            heading TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            text TEXT NOT NULL,
            UNIQUE(material_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS active_receipts (
            workspace_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_knowledge_points (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            material_id TEXT NOT NULL REFERENCES active_materials(id) ON DELETE CASCADE,
            normalized_name TEXT NOT NULL,
            name TEXT NOT NULL,
            explanation TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            estimated_task_minutes INTEGER NOT NULL,
            mastery REAL NOT NULL DEFAULT 0,
            acceptance_method TEXT NOT NULL,
            source_evidence TEXT NOT NULL,
            isolated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(workspace_id, normalized_name)
        );
        CREATE TABLE IF NOT EXISTS active_dependencies (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            prerequisite_id TEXT NOT NULL REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            dependent_id TEXT NOT NULL REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            evidence TEXT NOT NULL,
            UNIQUE(workspace_id, prerequisite_id, dependent_id)
        );
        CREATE TABLE IF NOT EXISTS active_knowledge_conflicts (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            topic TEXT NOT NULL,
            evidence_a TEXT NOT NULL,
            evidence_b TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unresolved'
        );
        CREATE TABLE IF NOT EXISTS active_plans (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            receipt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            feasible INTEGER NOT NULL,
            total_estimated_task_minutes INTEGER NOT NULL,
            unscheduled_task_minutes INTEGER NOT NULL,
            warning TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS active_plan_tasks (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES active_plans(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            day_date TEXT NOT NULL,
            day_index INTEGER NOT NULL,
            task_order INTEGER NOT NULL,
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id),
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            source_evidence TEXT NOT NULL,
            acceptance_condition TEXT NOT NULL,
            estimated_task_minutes INTEGER NOT NULL,
            completed_task_minutes INTEGER NOT NULL DEFAULT 0,
            actual_elapsed_minutes REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'planned',
            task_type TEXT NOT NULL DEFAULT 'initial_learning',
            learning_phase TEXT NOT NULL DEFAULT 'integrated_initial',
            point_sequence INTEGER NOT NULL DEFAULT 0,
            phase_part_index INTEGER NOT NULL DEFAULT 1,
            phase_part_count INTEGER NOT NULL DEFAULT 1,
            linked_review_item_id TEXT NOT NULL DEFAULT '',
            review_cycle INTEGER NOT NULL DEFAULT 0,
            origin_task_id TEXT NOT NULL DEFAULT '',
            not_before_date TEXT,
            trigger_reason TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS active_teaching_sessions (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            plan_id TEXT NOT NULL REFERENCES active_plans(id),
            state TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            actual_elapsed_minutes REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS active_teaching_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            action TEXT NOT NULL,
            content TEXT NOT NULL,
            citations TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_mastery_evidence (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id),
            evidence_type TEXT NOT NULL,
            score REAL NOT NULL,
            passed INTEGER NOT NULL,
            evidence TEXT NOT NULL,
            citations TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_task_time_events (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE,
            task_id TEXT NOT NULL REFERENCES active_plan_tasks(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            elapsed_minutes REAL NOT NULL,
            activity_kind TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_review_items (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            plan_id TEXT NOT NULL DEFAULT '',
            knowledge_point_id TEXT NOT NULL REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            source_task_id TEXT NOT NULL REFERENCES active_plan_tasks(id) ON DELETE CASCADE,
            source_mastery_evidence_id TEXT NOT NULL,
            due_date TEXT NOT NULL,
            interval_step INTEGER NOT NULL DEFAULT 0,
            interval_days INTEGER NOT NULL DEFAULT 1,
            review_total_minutes INTEGER NOT NULL DEFAULT 0,
            estimated_task_minutes INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            success_streak INTEGER NOT NULL DEFAULT 0,
            last_score REAL,
            last_passed INTEGER,
            last_attempted_at TEXT,
            scheduled_task_id TEXT NOT NULL DEFAULT '',
            scheduled_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(session_id, knowledge_point_id)
        );
        CREATE TABLE IF NOT EXISTS active_review_attempts (
            id TEXT PRIMARY KEY,
            review_item_id TEXT NOT NULL REFERENCES active_review_items(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            plan_task_id TEXT NOT NULL DEFAULT '',
            mastery_evidence_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            score REAL NOT NULL,
            passed INTEGER NOT NULL,
            assistance_level TEXT NOT NULL DEFAULT 'none',
            evaluator_kind TEXT NOT NULL,
            estimated_task_minutes INTEGER NOT NULL,
            completed_task_minutes INTEGER NOT NULL DEFAULT 0,
            actual_elapsed_minutes REAL NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL,
            reviewed_on TEXT NOT NULL,
            next_interval_days INTEGER,
            next_due_date TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(review_item_id, attempt_no)
        );
        CREATE TABLE IF NOT EXISTS active_replan_history (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES active_plans(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES active_teaching_sessions(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            trigger_name TEXT NOT NULL,
            reason TEXT NOT NULL,
            affected_from TEXT NOT NULL,
            before_snapshot TEXT NOT NULL,
            after_snapshot TEXT NOT NULL,
            moved_task_ids TEXT NOT NULL,
            unscheduled_minutes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    existing_task_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_plan_tasks)").fetchall()
    }
    for column, ddl in (
        ("task_type", "TEXT NOT NULL DEFAULT 'initial_learning'"),
        ("learning_phase", "TEXT NOT NULL DEFAULT 'integrated_initial'"),
        ("point_sequence", "INTEGER NOT NULL DEFAULT 0"),
        ("phase_part_index", "INTEGER NOT NULL DEFAULT 1"),
        ("phase_part_count", "INTEGER NOT NULL DEFAULT 1"),
        ("linked_review_item_id", "TEXT NOT NULL DEFAULT ''"),
        ("review_cycle", "INTEGER NOT NULL DEFAULT 0"),
        ("origin_task_id", "TEXT NOT NULL DEFAULT ''"),
        ("not_before_date", "TEXT"),
        ("trigger_reason", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in existing_task_columns:
            connection.execute(
                f"ALTER TABLE active_plan_tasks ADD COLUMN {column} {ddl}"
            )
    existing_review_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_review_items)").fetchall()
    }
    for column, ddl in (
        ("source_mastery_evidence_id", "TEXT NOT NULL DEFAULT ''"),
        ("interval_step", "INTEGER NOT NULL DEFAULT 0"),
        ("review_total_minutes", "INTEGER NOT NULL DEFAULT 0"),
        ("scheduled_task_id", "TEXT NOT NULL DEFAULT ''"),
        ("scheduled_date", "TEXT"),
        ("plan_id", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in existing_review_columns:
            connection.execute(
                f"ALTER TABLE active_review_items ADD COLUMN {column} {ddl}"
            )
    existing_review_attempt_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_review_attempts)").fetchall()
    }
    if "plan_task_id" not in existing_review_attempt_columns:
        connection.execute(
            "ALTER TABLE active_review_attempts ADD COLUMN plan_task_id TEXT NOT NULL DEFAULT ''"
        )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_active_plan_tasks_schedule
           ON active_plan_tasks(plan_id,status,day_date)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_active_plan_tasks_review
           ON active_plan_tasks(linked_review_item_id,status)"""
    )
    existing_replan_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(active_replan_history)").fetchall()
    }
    for column, ddl in (
        ("plan_id", "TEXT NOT NULL DEFAULT ''"),
        ("session_id", "TEXT NOT NULL DEFAULT ''"),
        ("trigger", "TEXT NOT NULL DEFAULT ''"),
        ("trigger_name", "TEXT NOT NULL DEFAULT ''"),
        ("reason", "TEXT NOT NULL DEFAULT ''"),
        ("affected_from", "TEXT NOT NULL DEFAULT ''"),
        ("before_state", "TEXT NOT NULL DEFAULT '[]'"),
        ("after_state", "TEXT NOT NULL DEFAULT '[]'"),
        ("before_snapshot", "TEXT NOT NULL DEFAULT '[]'"),
        ("after_snapshot", "TEXT NOT NULL DEFAULT '[]'"),
        ("moved_task_ids", "TEXT NOT NULL DEFAULT '[]'"),
        ("unscheduled_minutes", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in existing_replan_columns:
            connection.execute(
                f"ALTER TABLE active_replan_history ADD COLUMN {column} {ddl}"
            )
    connection.commit()


def _error(code: str, message: str, status: int = 400, **details: Any) -> None:
    raise HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "details": details}},
    )


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._ -]+", "_", Path(value).name).strip(" .")
    return cleaned[:180] or "material"


def _split_markdown(text: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
    if not matches:
        matches = [re.match(r"", text)]
    parts: list[dict[str, Any]] = []
    paragraph_offsets = [match.start() for match in re.finditer(r"(?m)(?:^|\n)\s*\S", text)]
    for index, match in enumerate(matches):
        start = match.start() if match else 0
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if not content:
            continue
        heading = match.group(2).strip() if match and match.lastindex else ""
        level = len(match.group(1)) if match and match.lastindex else 1
        paragraph_start = 1 + sum(1 for offset in paragraph_offsets if offset < start)
        paragraph_end = max(paragraph_start, sum(1 for offset in paragraph_offsets if offset < end))
        parts.append(
            {
                "text": content,
                "heading": heading,
                "heading_level": level,
                "page_start": None,
                "page_end": None,
                "paragraph_start": paragraph_start,
                "paragraph_end": paragraph_end,
                "char_start": start,
                "char_end": end,
            }
        )
    return parts


def _split_plain(text: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", text, flags=re.S))
    parts = []
    for index, match in enumerate(matches, 1):
        value = match.group(0).strip()
        if value:
            parts.append(
                {
                    "text": value,
                    "heading": value.splitlines()[0][:120],
                    "heading_level": 3,
                    "page_start": None,
                    "page_end": None,
                    "paragraph_start": index,
                    "paragraph_end": index,
                    "char_start": match.start(),
                    "char_end": match.end(),
                }
            )
    return parts


def _read_text_with_fallback(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    failures = []
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            failures.append(f"{encoding}:{exc.start}")
    raise ValueError("无法识别文本编码（已尝试 UTF-8、UTF-16、GB18030）")


def _validate_text_quality(text: str) -> None:
    if not text.strip():
        raise ValueError("文件中没有可解析的正文")
    if "\x00" in text:
        raise ValueError("文本包含 NUL 字节，文件内容与文本格式不匹配")
    sample = text[:200_000]
    control_count = sum(
        1
        for char in sample
        if ord(char) < 32 and char not in {"\n", "\r", "\t", "\f"}
    )
    if control_count / max(1, len(sample)) > 0.01:
        raise ValueError("文本控制字符比例异常，无法确认正文质量")
    if sample.count("\ufffd") / max(1, len(sample)) > 0.001:
        raise ValueError("文本包含过多解码替换字符，无法可靠解析")


def _validate_structured_text(text: str, suffix: str) -> None:
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 语法错误（第 {exc.lineno} 行第 {exc.colno} 列）") from exc
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml

            yaml.safe_load(text)
        except Exception as exc:
            raise ValueError(f"YAML 语法错误：{exc}") from exc
    elif suffix == ".xml":
        from xml.etree import ElementTree

        try:
            ElementTree.fromstring(text)
        except ElementTree.ParseError as exc:
            raise ValueError(f"XML 语法错误：{exc}") from exc
    elif suffix == ".csv":
        try:
            rows = list(csv.reader(io.StringIO(text)))
        except csv.Error as exc:
            raise ValueError(f"CSV 语法错误：{exc}") from exc
        if not rows or not any(any(cell.strip() for cell in row) for row in rows):
            raise ValueError("CSV 中没有可解析的数据")
    elif suffix == ".rtf" and not text.lstrip().startswith(r"{\rtf"):
        raise ValueError("RTF 文件缺少有效的 RTF 文档头")


def _parse_zip_xml_text(path: Path, kind: str) -> tuple[list[dict[str, Any]], int, str]:
    import zipfile
    from xml.etree import ElementTree

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError(
                f"压缩文档条目过多（{len(infos)} > {MAX_ARCHIVE_ENTRIES}）"
            )
        uncompressed = sum(max(0, int(info.file_size)) for info in infos)
        if uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError(
                "压缩文档解压后超过安全预算 "
                f"（{uncompressed} > {MAX_ARCHIVE_UNCOMPRESSED_BYTES} 字节）"
            )
        names = archive.namelist()
        if kind == "pptx":
            selected = sorted(
                (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=lambda value: int(re.search(r"\d+", Path(value).stem).group()),
            )
        elif kind == "xlsx":
            selected = sorted(
                name for name in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
        else:
            selected = sorted(
                name for name in names
                if name.casefold().endswith((".xhtml", ".html", ".htm"))
            )
        parts: list[dict[str, Any]] = []
        char_cursor = 0
        paragraph = 1
        for page_number, name in enumerate(selected, 1):
            raw = archive.read(name)
            if kind == "epub":
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(raw, "html.parser")
                values = [
                    item.get_text(" ", strip=True)
                    for item in soup.select("h1,h2,h3,h4,h5,h6,p,li")
                    if item.get_text(" ", strip=True)
                ]
            else:
                root = ElementTree.fromstring(raw)
                values = [
                    str(node.text).strip()
                    for node in root.iter()
                    if node.text and str(node.text).strip()
                ]
            text = "\n".join(values).strip()
            if not text:
                continue
            parts.append(
                {
                    "text": text,
                    "heading": values[0][:120] if values else Path(name).stem,
                    "heading_level": 2,
                    "page_start": page_number,
                    "page_end": page_number,
                    "paragraph_start": paragraph,
                    "paragraph_end": paragraph + max(0, len(values) - 1),
                    "char_start": char_cursor,
                    "char_end": char_cursor + len(text),
                }
            )
            char_cursor += len(text) + 2
            paragraph += max(1, len(values))
        return parts, len(selected), f"stdlib-{kind}-xml"


def _parse_file(path: Path) -> tuple[list[dict[str, Any]], int, str]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的文件格式：{suffix or '无扩展名'}")
    text_suffixes = {
        ".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".xml", ".rtf",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h",
        ".go", ".rs", ".sql", ".sh", ".ps1",
    }
    if suffix in text_suffixes:
        text, encoding = _read_text_with_fallback(path)
        _validate_text_quality(text)
        _validate_structured_text(text, suffix)
        parts = _split_markdown(text) if suffix in {".md", ".markdown"} else _split_plain(text)
        return parts, 1, f"{encoding}-{suffix.lstrip('.')}"
    if suffix in {".html", ".htm"}:
        from bs4 import BeautifulSoup

        text, encoding = _read_text_with_fallback(path)
        soup = BeautifulSoup(text, "html.parser")
        text = "\n\n".join(item.get_text(" ", strip=True) for item in soup.select("h1,h2,h3,h4,h5,h6,p,li"))
        return _split_plain(text), 1, f"beautifulsoup-{encoding}"
    if suffix == ".docx":
        from docx import Document

        paragraphs = [paragraph.text.strip() for paragraph in Document(path).paragraphs if paragraph.text.strip()]
        text = "\n\n".join(paragraphs)
        return _split_plain(text), 1, "python-docx"
    if suffix == ".pdf":
        import fitz

        document = fitz.open(path)
        parts: list[dict[str, Any]] = []
        char_cursor = 0
        paragraph_cursor = 1
        for page_number, page in enumerate(document, 1):
            blocks = [item[4].strip() for item in page.get_text("blocks") if str(item[4]).strip()]
            for block in blocks:
                parts.append(
                    {
                        "text": block,
                        "heading": block.splitlines()[0][:120],
                        "heading_level": 3,
                        "page_start": page_number,
                        "page_end": page_number,
                        "paragraph_start": paragraph_cursor,
                        "paragraph_end": paragraph_cursor,
                        "char_start": char_cursor,
                        "char_end": char_cursor + len(block),
                    }
                )
                char_cursor += len(block) + 2
                paragraph_cursor += 1
        return parts, len(document), "pymupdf"
    if suffix in {".pptx", ".xlsx", ".epub"}:
        return _parse_zip_xml_text(path, suffix.lstrip("."))
    raise ValueError(f"不支持的文件格式：{suffix}")


def _evidence(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "material_id": row["material_id"],
        "chunk_id": row["id"],
        "source_file": row["original_filename"] if "original_filename" in keys else None,
        "heading": row["heading"] if "heading" in keys else "",
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "paragraph_start": row["paragraph_start"],
        "paragraph_end": row["paragraph_end"],
        "char_start": row["char_start"],
        "char_end": row["char_end"],
        "content_hash": row["content_hash"],
        "quote": row["text"][:360],
    }


def _public_material(connection: sqlite3.Connection, material_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM active_materials WHERE id=?", (material_id,)).fetchone()
    if not row:
        _error("MATERIAL_NOT_FOUND", "学习资料不存在", 404)
    output = dict(row)
    output["chunks"] = [
        dict(item)
        for item in connection.execute(
            "SELECT * FROM active_chunks WHERE material_id=? ORDER BY ordinal", (material_id,)
        ).fetchall()
    ]
    output["chunk_count"] = len(output["chunks"])
    return output


@router.post("/workspaces/{workspace_id}/materials")
async def upload_material(workspace_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    filename = _safe_filename(file.filename or "material")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        _error(
            "UNSUPPORTED_FILE_TYPE",
            f"不支持的文件格式：{suffix or '无扩展名'}；支持 {', '.join(sorted(SUPPORTED_SUFFIXES))}",
            415,
        )
    payload = await file.read()
    if len(payload) > MAX_MATERIAL_BYTES:
        _error(
            "MATERIAL_TOO_LARGE",
            f"资料超过单文件安全上限 {MAX_MATERIAL_BYTES // (1024 * 1024)} MiB",
            413,
            size_bytes=len(payload),
            max_size_bytes=MAX_MATERIAL_BYTES,
        )
    sha256 = _hash_bytes(payload)
    with _connect() as connection:
        duplicate = connection.execute(
            "SELECT id FROM active_materials WHERE workspace_id=? AND sha256=?",
            (workspace_id, sha256),
        ).fetchone()
        if duplicate:
            result = _public_material(connection, duplicate["id"])
            if result["status"] != "ready":
                _error(
                    "MATERIAL_PARSE_FAILED",
                    f"相同资料此前解析失败：{result['parse_error']}",
                    422,
                    material_id=result["id"],
                    deduplicated=True,
                )
            result["deduplicated"] = True
            return result
        material_id = _id("material")
        target_dir = UPLOAD_ROOT / re.sub(r"[^0-9A-Za-z._-]", "_", workspace_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{sha256[:16]}-{filename}"
        target.write_bytes(payload)
        try:
            parts, page_count, parser_name = _parse_file(target)
            if not parts:
                raise ValueError("文件中没有可解析的正文")
        except Exception as exc:
            connection.execute(
                """INSERT INTO active_materials
                   (id,workspace_id,original_filename,media_type,size_bytes,sha256,uploaded_at,stored_path,status,parse_error,page_count,parser_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    material_id,
                    workspace_id,
                    filename,
                    file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    len(payload),
                    sha256,
                    _now(),
                    str(target),
                    "failed",
                    str(exc),
                    0,
                    "failed",
                ),
            )
            connection.commit()
            _error("MATERIAL_PARSE_FAILED", f"资料解析失败：{exc}", 422, material_id=material_id)
        connection.execute(
            """INSERT INTO active_materials
               (id,workspace_id,original_filename,media_type,size_bytes,sha256,uploaded_at,stored_path,status,parse_error,page_count,parser_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                material_id,
                workspace_id,
                filename,
                file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                len(payload),
                sha256,
                _now(),
                str(target),
                "ready",
                "",
                page_count,
                parser_name,
            ),
        )
        for ordinal, part in enumerate(parts, 1):
            text = part["text"].strip()
            connection.execute(
                """INSERT INTO active_chunks
                   (id,workspace_id,material_id,ordinal,page_start,page_end,paragraph_start,paragraph_end,char_start,char_end,heading,content_hash,text)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _id("chunk"),
                    workspace_id,
                    material_id,
                    ordinal,
                    part.get("page_start"),
                    part.get("page_end"),
                    int(part.get("paragraph_start") or ordinal),
                    int(part.get("paragraph_end") or ordinal),
                    int(part.get("char_start") or 0),
                    int(part.get("char_end") or len(text)),
                    str(part.get("heading") or "")[:300],
                    _hash_text(text),
                    text,
                ),
            )
        connection.commit()
        result = _public_material(connection, material_id)
        result["deduplicated"] = False
        return result


@router.get("/workspaces/{workspace_id}/materials")
def list_materials(workspace_id: str) -> list[dict[str, Any]]:
    with _connect() as connection:
        return [
            _public_material(connection, row["id"])
            for row in connection.execute(
                "SELECT id FROM active_materials WHERE workspace_id=? ORDER BY uploaded_at", (workspace_id,)
            ).fetchall()
        ]


def _validate_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    required = (
        "learning_goal",
        "current_level",
        "deadline",
        "study_weekdays",
        "preferred_learning_style",
        "material_ids",
    )
    missing = [field for field in required if payload.get(field) in (None, "", [])]
    if missing:
        _error("RECEIPT_FIELDS_REQUIRED", "学习回执缺少必填字段", 422, missing=missing)
    try:
        deadline = date.fromisoformat(str(payload["deadline"]))
    except ValueError:
        _error("INVALID_DEADLINE", "截止日期必须是 YYYY-MM-DD", 422)
    if deadline < date.today():
        _error("DEADLINE_IN_PAST", "截止日期不能早于今天", 422)
    try:
        start_date = date.fromisoformat(str(payload.get("start_date") or date.today().isoformat()))
    except ValueError:
        _error("INVALID_START_DATE", "开始日期必须是 YYYY-MM-DD", 422)
    if start_date > deadline:
        _error("INVALID_DATE_RANGE", "开始日期不能晚于截止日期", 422)
    minutes = int(payload.get("daily_task_minutes") or 30)
    if not 10 <= minutes <= 720:
        _error("INVALID_DAILY_TASK_MINUTES", "每日任务分钟预算必须在 10 到 720 之间", 422)
    weekdays = sorted({int(item) for item in payload["study_weekdays"]})
    if not weekdays or any(item < 0 or item > 6 for item in weekdays):
        _error("INVALID_STUDY_WEEKDAYS", "每周可学习日期必须使用 0 到 6", 422)
    fixed_rest_weekdays = sorted({int(item) for item in payload.get("fixed_rest_weekdays", [])})
    if any(item < 0 or item > 6 for item in fixed_rest_weekdays):
        _error("INVALID_REST_WEEKDAYS", "固定休息星期必须使用 0 到 6", 422)
    if set(fixed_rest_weekdays) & set(weekdays):
        _error("WEEKDAY_AVAILABILITY_CONFLICT", "同一星期不能同时设为学习日和固定休息日", 422)
    unavailable_dates = sorted({str(item) for item in payload.get("unavailable_dates", [])})
    for item in unavailable_dates:
        try:
            unavailable_date = date.fromisoformat(item)
        except ValueError:
            _error("INVALID_UNAVAILABLE_DATE", "额外不可学习日期必须是 YYYY-MM-DD", 422, value=item)
        if unavailable_date < start_date or unavailable_date > deadline:
            _error("UNAVAILABLE_DATE_OUTSIDE_RANGE", "额外不可学习日期必须位于学习周期内", 422, value=item)
    special_date_minutes: dict[str, int] = {}
    for raw_day, raw_minutes in dict(payload.get("special_date_minutes") or {}).items():
        try:
            special_day = date.fromisoformat(str(raw_day))
        except ValueError:
            _error("INVALID_SPECIAL_DATE", "特殊日期必须是 YYYY-MM-DD", 422, value=raw_day)
        if special_day < start_date or special_day > deadline:
            _error("SPECIAL_DATE_OUTSIDE_RANGE", "特殊日期必须位于学习周期内", 422, value=raw_day)
        special_minutes = int(raw_minutes)
        if not 0 <= special_minutes <= 720:
            _error("INVALID_SPECIAL_DATE_MINUTES", "特殊日期分钟数必须在 0 到 720 之间", 422)
        special_date_minutes[special_day.isoformat()] = special_minutes
    raw_period_ratio = payload.get("initial_learning_period_ratio")
    initial_learning_period_ratio: float | None = None
    if raw_period_ratio is not None:
        try:
            initial_learning_period_ratio = float(raw_period_ratio)
        except (TypeError, ValueError):
            _error("INVALID_LEARNING_PERIOD_RATIO", "首学周期比例必须是 0 到 1 之间的数字", 422)
        if not 0.1 <= initial_learning_period_ratio <= 0.9:
            _error("INVALID_LEARNING_PERIOD_RATIO", "首学周期比例必须在 0.1 到 0.9 之间", 422)
    return {
        **payload,
        "start_date": start_date.isoformat(),
        "deadline": deadline.isoformat(),
        "daily_task_minutes": minutes,
        "allocation_mode": (
            "system" if str(payload.get("allocation_mode") or "").casefold() == "system"
            else "manual"
        ),
        "study_weekdays": weekdays,
        "fixed_rest_weekdays": fixed_rest_weekdays or sorted(set(range(7)) - set(weekdays)),
        "unavailable_dates": unavailable_dates,
        "special_date_minutes": special_date_minutes,
        "initial_learning_period_ratio": initial_learning_period_ratio,
        "current_foundation": str(payload.get("current_foundation") or payload["current_level"]),
        "target_level": str(payload.get("target_level") or "complete_mastery"),
        "notes": str(payload.get("notes") or ""),
        "teacher_persona": str(payload.get("teacher_persona") or "")[:2000],
        "learner_interests": str(payload.get("learner_interests") or "")[:1000],
        "allow_schedule_suggestions": bool(payload.get("allow_schedule_suggestions", True)),
        "material_ids": list(dict.fromkeys(str(item) for item in payload["material_ids"])),
        "updated_at": _now(),
    }


@router.put("/workspaces/{workspace_id}/receipt")
def save_receipt(workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
    receipt = _validate_receipt(body)
    with _connect() as connection:
        known = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM active_materials WHERE workspace_id=? AND status='ready'", (workspace_id,)
            ).fetchall()
        }
        missing = sorted(set(receipt["material_ids"]) - known)
        if missing:
            _error("RECEIPT_MATERIAL_NOT_FOUND", "回执引用了不存在或解析失败的资料", 422, material_ids=missing)
        if receipt["allocation_mode"] == "system":
            study_dates = []
            cursor = date.fromisoformat(receipt["start_date"])
            deadline = date.fromisoformat(receipt["deadline"])
            unavailable = set(receipt["unavailable_dates"])
            while cursor <= deadline:
                if cursor.weekday() in set(receipt["study_weekdays"]) and cursor.isoformat() not in unavailable:
                    study_dates.append(cursor)
                cursor += timedelta(days=1)
            if not study_dates:
                _error("NO_STUDY_DATES", "截止日期前没有可用学习日", 422)
            placeholders = ",".join("?" for _ in receipt["material_ids"])
            chunk_count = int(connection.execute(
                f"""SELECT COUNT(*) FROM active_chunks
                    WHERE workspace_id=? AND material_id IN ({placeholders})""",
                [workspace_id, *receipt["material_ids"]],
            ).fetchone()[0])
            estimated_total = max(90, chunk_count * 8 + 120)
            recommended = max(20, min(480, math.ceil(estimated_total * 1.2 / len(study_dates) / 5) * 5))
            receipt["daily_task_minutes"] = recommended
            receipt["allocation"] = {
                "mode": "system",
                "estimated_total_minutes": estimated_total,
                "buffer_rate": 0.2,
                "available_study_days": len(study_dates),
                "recommended_minutes_per_day": recommended,
                "reason": "根据所选资料、截止日期和真实可学习日自动分配；用户无需猜测每日分钟。",
            }
        connection.execute(
            """INSERT INTO active_receipts(workspace_id,payload,updated_at) VALUES (?,?,?)
               ON CONFLICT(workspace_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
            (workspace_id, json.dumps(receipt, ensure_ascii=False), receipt["updated_at"]),
        )
        connection.commit()
    return receipt


@router.get("/workspaces/{workspace_id}/receipt")
def get_receipt(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not row:
            _error("RECEIPT_NOT_FOUND", "学习回执尚未填写", 404)
        return json.loads(row["payload"])


def _extract_field(text: str, labels: Iterable[str]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?i)(?:^|[。\n])\s*(?:{label_pattern})\s*[：:]\s*([^。\n]+)",
        text,
    )
    return match.group(1).strip() if match else ""


def _difficulty_minutes(difficulty: str, text: str) -> int:
    explicit = _extract_field(text, ("预计任务分钟", "estimated_task_minutes"))
    if explicit:
        number = re.search(r"\d+", explicit)
        if number:
            return max(10, min(240, int(number.group())))
    value = difficulty.casefold()
    if "hard" in value or "困难" in value:
        return 85
    if "easy" in value or "简单" in value:
        return 40
    return 60


def _cycle_nodes(edges: list[tuple[str, str]]) -> set[str]:
    outgoing: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for source, target in edges:
        outgoing[source].add(target)
        nodes.update((source, target))
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: set[str] = set()
    stack: list[str] = []

    def walk(node: str) -> None:
        if node in visiting:
            cycles.add(node)
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for target in outgoing[node]:
            if target in visiting:
                cycles.update(stack[stack.index(target) :])
            else:
                walk(target)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in nodes:
        walk(node)
    return cycles


@router.post("/workspaces/{workspace_id}/knowledge-graph/generate")
def generate_knowledge_graph(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not receipt_row:
            _error("RECEIPT_REQUIRED", "请先填写学习回执", 409)
        receipt = json.loads(receipt_row["payload"])
        placeholders = ",".join("?" for _ in receipt["material_ids"])
        chunks = connection.execute(
            f"""SELECT * FROM active_chunks
                WHERE workspace_id=? AND material_id IN ({placeholders})
                ORDER BY material_id, ordinal""",
            (workspace_id, *receipt["material_ids"]),
        ).fetchall()
        if not chunks:
            _error("KNOWLEDGE_SOURCE_REQUIRED", "所选资料没有可追溯正文", 409)
        old_plans = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM active_plans WHERE workspace_id=?", (workspace_id,)
            ).fetchall()
        ]
        for plan_id in old_plans:
            session_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM active_teaching_sessions WHERE plan_id=?", (plan_id,)
                ).fetchall()
            ]
            for session_id in session_ids:
                connection.execute(
                    "DELETE FROM active_replan_history WHERE session_id=?", (session_id,)
                )
                connection.execute(
                    "DELETE FROM active_task_time_events WHERE session_id=?", (session_id,)
                )
                connection.execute(
                    "DELETE FROM active_mastery_evidence WHERE session_id=?", (session_id,)
                )
                connection.execute(
                    "DELETE FROM active_teaching_messages WHERE session_id=?", (session_id,)
                )
            connection.execute("DELETE FROM active_teaching_sessions WHERE plan_id=?", (plan_id,))
            connection.execute("DELETE FROM active_plan_tasks WHERE plan_id=?", (plan_id,))
        connection.execute("DELETE FROM active_plans WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_knowledge_conflicts WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_dependencies WHERE workspace_id=?", (workspace_id,))
        connection.execute("DELETE FROM active_knowledge_points WHERE workspace_id=?", (workspace_id,))
        created: list[dict[str, Any]] = []
        aliases: dict[str, str] = {}
        duplicate_count = 0
        candidate_dependencies: list[tuple[str, str, dict[str, Any]]] = []
        for chunk in chunks:
            raw_name = re.sub(r"^\s*\d+[.)、]\s*", "", str(chunk["heading"] or "")).strip()
            if not raw_name:
                raw_name = chunk["text"].splitlines()[0].lstrip("# ").strip()
            if not raw_name:
                continue
            normalized = _normalise(raw_name)
            if not normalized:
                continue
            difficulty = _extract_field(chunk["text"], ("难度", "训练难度", "difficulty")) or "medium"
            explanation = _extract_field(chunk["text"], ("知识点", "解释", "explanation"))
            if not explanation:
                explanation = re.sub(r"(?m)^#{1,6}\s+.*$", "", chunk["text"]).strip()[:600]
            if not explanation:
                explanation = f"依据资料章节“{raw_name}”进行讲解与练习。"
            acceptance = _extract_field(chunk["text"], ("验收", "验收方式", "acceptance"))
            if not acceptance:
                acceptance = (
                    "能不用照抄资料解释该知识点，给出一个具体应用或例子，"
                    "并按资料中的事实或步骤完成一次可观察检验。"
                )
            evidence = _evidence(chunk)
            if normalized in aliases:
                duplicate_count += 1
                point_id = aliases[normalized]
                existing = connection.execute(
                    "SELECT explanation,source_evidence FROM active_knowledge_points WHERE id=?",
                    (point_id,),
                ).fetchone()
                existing_evidence = json.loads(existing["source_evidence"])
                connection.execute(
                    "UPDATE active_knowledge_points SET source_evidence=? WHERE id=?",
                    (json.dumps([*existing_evidence, evidence], ensure_ascii=False), point_id),
                )
                if _normalise(existing["explanation"]) != _normalise(explanation):
                    connection.execute(
                        """INSERT INTO active_knowledge_conflicts
                           (id,workspace_id,knowledge_point_id,topic,evidence_a,evidence_b,summary,status)
                           VALUES (?,?,?,?,?,?,?,'unresolved')""",
                        (
                            _id("conflict"),
                            workspace_id,
                            point_id,
                            raw_name[:240],
                            json.dumps(existing_evidence[0], ensure_ascii=False),
                            json.dumps(evidence, ensure_ascii=False),
                            "多个上传资料对同一知识点给出了不同表述，教学前需要指出并核对。",
                        ),
                    )
                continue
            point_id = f"kp_{hashlib.sha256((workspace_id + ':' + normalized).encode()).hexdigest()[:20]}"
            connection.execute(
                """INSERT INTO active_knowledge_points
                   (id,workspace_id,material_id,normalized_name,name,explanation,difficulty,estimated_task_minutes,mastery,acceptance_method,source_evidence,isolated,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    point_id,
                    workspace_id,
                    chunk["material_id"],
                    normalized,
                    raw_name[:240],
                    explanation,
                    difficulty,
                    _difficulty_minutes(difficulty, chunk["text"]),
                    0,
                    acceptance,
                    json.dumps([evidence], ensure_ascii=False),
                    0,
                    _now(),
                ),
            )
            aliases[normalized] = point_id
            synonyms = _extract_field(chunk["text"], ("同义词", "aliases", "synonyms"))
            for synonym in re.split(r"[,，、;；]", synonyms):
                key = _normalise(synonym)
                if key:
                    aliases[key] = point_id
            created.append(
                {
                    "id": point_id,
                    "name": raw_name[:240],
                    "explanation": explanation,
                    "difficulty": difficulty,
                    "estimated_task_minutes": _difficulty_minutes(difficulty, chunk["text"]),
                    "current_mastery": 0,
                    "acceptance_method": acceptance,
                    "material_id": chunk["material_id"],
                    "source_evidence": [evidence],
                    "prerequisites": [],
                    "successors": [],
                    "isolated": False,
                }
            )
            explicit = _extract_field(chunk["text"], ("前置", "前置知识", "prerequisites"))
            for item in re.split(r"[,，、;；]", explicit):
                key = _normalise(item)
                if key:
                    candidate_dependencies.append((key, point_id, evidence))
        if not created:
            _error("KNOWLEDGE_EXTRACTION_EMPTY", "资料正文无法生成有来源的知识点", 422)
        id_set = {item["id"] for item in created}
        name_by_id = {item["id"]: item["name"] for item in created}
        edges: list[tuple[str, str, dict[str, Any]]] = []
        for source_key, target, evidence in candidate_dependencies:
            source = source_key if source_key in id_set else aliases.get(source_key)
            if source and source != target:
                edges.append((source, target, evidence))
        cycles = _cycle_nodes([(source, target) for source, target, _ in edges])
        if cycles:
            connection.rollback()
            _error(
                "KNOWLEDGE_DEPENDENCY_CYCLE",
                "检测到循环依赖，知识图谱未保存",
                422,
                nodes=sorted(name_by_id.get(item, item) for item in cycles),
            )
        unique_edges: set[tuple[str, str]] = set()
        for source, target, evidence in edges:
            if (source, target) in unique_edges:
                continue
            unique_edges.add((source, target))
            connection.execute(
                """INSERT INTO active_dependencies(id,workspace_id,prerequisite_id,dependent_id,evidence)
                   VALUES (?,?,?,?,?)""",
                (_id("dep"), workspace_id, source, target, json.dumps(evidence, ensure_ascii=False)),
            )
        connected = {item for edge in unique_edges for item in edge}
        for point in created:
            point["isolated"] = point["id"] not in connected and len(created) > 1
            connection.execute(
                "UPDATE active_knowledge_points SET isolated=? WHERE id=?",
                (int(point["isolated"]), point["id"]),
            )
        connection.commit()
        return _graph(connection, workspace_id) | {
            "quality": {
                "duplicate_count": duplicate_count,
                "synonym_merge_count": duplicate_count,
                "cycle_count": 0,
                "unsourced_blocked_count": 0,
                "isolated_count": sum(1 for item in created if item["isolated"]),
                "duration_outlier_count": sum(
                    1 for item in created if not 10 <= item["estimated_task_minutes"] <= 240
                ),
            }
        }


def _graph(connection: sqlite3.Connection, workspace_id: str) -> dict[str, Any]:
    point_rows = connection.execute(
        "SELECT * FROM active_knowledge_points WHERE workspace_id=? ORDER BY created_at,id", (workspace_id,)
    ).fetchall()
    edge_rows = connection.execute(
        "SELECT * FROM active_dependencies WHERE workspace_id=? ORDER BY id", (workspace_id,)
    ).fetchall()
    prerequisites: dict[str, list[str]] = defaultdict(list)
    successors: dict[str, list[str]] = defaultdict(list)
    edges = []
    for row in edge_rows:
        prerequisites[row["dependent_id"]].append(row["prerequisite_id"])
        successors[row["prerequisite_id"]].append(row["dependent_id"])
        edges.append(
            {
                "id": row["id"],
                "prerequisite_id": row["prerequisite_id"],
                "dependent_id": row["dependent_id"],
                "relation": "prerequisite",
                "source_evidence": json.loads(row["evidence"]),
            }
        )
    points = []
    for row in point_rows:
        points.append(
            {
                "id": row["id"],
                "name": row["name"],
                "explanation": row["explanation"],
                "source_evidence": json.loads(row["source_evidence"]),
                "prerequisites": prerequisites[row["id"]],
                "successors": successors[row["id"]],
                "difficulty": row["difficulty"],
                "estimated_task_minutes": row["estimated_task_minutes"],
                "current_mastery": row["mastery"],
                "acceptance_method": row["acceptance_method"],
                "material_id": row["material_id"],
                "isolated": bool(row["isolated"]),
            }
        )
    conflicts = [
        {
            **dict(row),
            "evidence_a": json.loads(row["evidence_a"]),
            "evidence_b": json.loads(row["evidence_b"]),
        }
        for row in connection.execute(
            "SELECT * FROM active_knowledge_conflicts WHERE workspace_id=? ORDER BY id",
            (workspace_id,),
        ).fetchall()
    ]
    return {
        "workspace_id": workspace_id,
        "knowledge_points": points,
        "edges": edges,
        "conflicts": conflicts,
    }


@router.get("/workspaces/{workspace_id}/knowledge-graph")
def get_knowledge_graph(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        result = _graph(connection, workspace_id)
        if not result["knowledge_points"]:
            _error("KNOWLEDGE_GRAPH_NOT_FOUND", "知识图谱尚未生成", 404)
        return result


def _topological_points(
    points: list[sqlite3.Row], dependencies: list[sqlite3.Row]
) -> list[sqlite3.Row]:
    by_id = {row["id"]: row for row in points}
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {row["id"]: 0 for row in points}
    for edge in dependencies:
        if edge["prerequisite_id"] in by_id and edge["dependent_id"] in by_id:
            outgoing[edge["prerequisite_id"]].append(edge["dependent_id"])
            indegree[edge["dependent_id"]] += 1
    def source_order(row: sqlite3.Row) -> tuple[int, int, str]:
        try:
            evidence = json.loads(row["source_evidence"])
        except (KeyError, TypeError, json.JSONDecodeError):
            evidence = []
        positions = [
            (
                int(item.get("paragraph") or item.get("paragraph_start") or 0),
                int(item.get("char_start") or 0),
            )
            for item in evidence
            if isinstance(item, dict)
        ]
        paragraph, char_start = min(positions, default=(0, 0))
        return paragraph, char_start, str(row["name"])
    ready = [
        node_id
        for node_id, value in indegree.items()
        if value == 0
    ]
    ready.sort(key=lambda item: source_order(by_id[item]))
    ordered: list[sqlite3.Row] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=lambda item: source_order(by_id[item]))
    if len(ordered) != len(points):
        _error("KNOWLEDGE_DEPENDENCY_CYCLE", "知识依赖存在循环，无法生成计划", 422)
    return ordered


def _study_dates(deadline: date, weekdays: list[int]) -> list[date]:
    result = []
    cursor = date.today()
    while cursor <= deadline:
        if cursor.weekday() in weekdays:
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


@router.post("/workspaces/{workspace_id}/plans/generate")
def generate_plan(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not receipt_row:
            _error("RECEIPT_REQUIRED", "请先填写学习回执", 409)
        receipt = json.loads(receipt_row["payload"])
        material_ids = list(receipt.get("material_ids") or [])
        placeholders = ",".join("?" for _ in material_ids)
        points = connection.execute(
            f"""SELECT * FROM active_knowledge_points
                WHERE workspace_id=? AND material_id IN ({placeholders})""",
            [workspace_id, *material_ids],
        ).fetchall()
        if not points:
            _error("KNOWLEDGE_GRAPH_REQUIRED", "请先生成知识图谱", 409)
        dependencies = connection.execute(
            "SELECT * FROM active_dependencies WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
        ordered = _topological_points(points, dependencies)
        dates = _study_dates(date.fromisoformat(receipt["deadline"]), receipt["study_weekdays"])
        if not dates:
            _error("NO_STUDY_DATES", "截止日期前没有符合回执设置的学习日", 422)
        budget = int(receipt["daily_task_minutes"])
        plan_id = _id("active_plan")
        task_specs = []
        for point in ordered:
            minutes = int(point["estimated_task_minutes"])
            split_count = max(1, (minutes + min(120, budget) - 1) // min(120, budget))
            remaining = minutes
            for split_index in range(split_count):
                part_minutes = min(remaining, min(120, budget))
                remaining -= part_minutes
                suffix = f"（{split_index + 1}/{split_count}）" if split_count > 1 else ""
                task_specs.append((point, suffix, part_minutes))
        day_index = 0
        used = 0
        scheduled: list[dict[str, Any]] = []
        unscheduled = 0
        task_order = 0
        for point, suffix, minutes in task_specs:
            if used and used + minutes > budget:
                day_index += 1
                used = 0
                task_order = 0
            if day_index >= len(dates):
                unscheduled += minutes
                continue
            task_order += 1
            used += minutes
            evidence = json.loads(point["source_evidence"])
            task = {
                "id": _id("active_task"),
                "plan_id": plan_id,
                "workspace_id": workspace_id,
                "day_date": dates[day_index].isoformat(),
                "day_index": day_index,
                "task_order": task_order,
                "knowledge_point_id": point["id"],
                "title": f"{point['name']}{suffix}",
                "objective": f"围绕“{point['name']}”完成讲解、示例、理解检测和独立验收。",
                "source_evidence": evidence,
                "acceptance_condition": point["acceptance_method"],
                "estimated_task_minutes": minutes,
                "completed_task_minutes": 0,
                "actual_elapsed_minutes": 0.0,
                "status": "planned",
            }
            scheduled.append(task)
        total = sum(int(point["estimated_task_minutes"]) for point in ordered)
        warning = (
            f"截止日期前容量不足，仍有 {unscheduled} 任务分钟未排入；请延长截止日期、增加学习日或缩小范围。"
            if unscheduled
            else ""
        )
        connection.execute(
            """INSERT INTO active_plans
               (id,workspace_id,receipt,created_at,feasible,total_estimated_task_minutes,unscheduled_task_minutes,warning)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                plan_id,
                workspace_id,
                json.dumps(receipt, ensure_ascii=False),
                _now(),
                int(unscheduled == 0),
                total,
                unscheduled,
                warning,
            ),
        )
        for task in scheduled:
            connection.execute(
                """INSERT INTO active_plan_tasks
                   (id,plan_id,workspace_id,day_date,day_index,task_order,knowledge_point_id,title,objective,source_evidence,acceptance_condition,estimated_task_minutes,completed_task_minutes,actual_elapsed_minutes,status)
                   VALUES (:id,:plan_id,:workspace_id,:day_date,:day_index,:task_order,:knowledge_point_id,:title,:objective,:source_evidence,:acceptance_condition,:estimated_task_minutes,:completed_task_minutes,:actual_elapsed_minutes,:status)""",
                {**task, "source_evidence": json.dumps(task["source_evidence"], ensure_ascii=False)},
            )
        connection.commit()
        return _plan(connection, plan_id)


def _plan(connection: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM active_plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        _error("ACTIVE_PLAN_NOT_FOUND", "主动教学计划不存在", 404)
    tasks = [
        dict(item)
        for item in connection.execute(
            "SELECT * FROM active_plan_tasks WHERE plan_id=? ORDER BY day_index,task_order", (plan_id,)
        ).fetchall()
    ]
    for task in tasks:
        task["source_evidence"] = json.loads(task["source_evidence"])
    days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        days[task["day_date"]].append(task)
    receipt = json.loads(row["receipt"])
    level = str(
        receipt.get("current_foundation")
        or receipt.get("current_level")
        or "beginner"
    ).casefold()
    elapsed_multipliers = {
        "zero_foundation": (1.3, 1.8),
        "beginner": (1.15, 1.6),
        "intermediate": (0.9, 1.3),
        "advanced": (0.75, 1.1),
    }.get(level, (1.0, 1.5))
    initial_minutes = sum(
        int(item["estimated_task_minutes"])
        for item in tasks
        if item["task_type"] == "initial_learning"
    )
    projected_review_minutes = max(
        0, int(row["total_estimated_task_minutes"]) - initial_minutes
    )
    total_mastery_minutes = int(row["total_estimated_task_minutes"])
    result = {
        **dict(row),
        "receipt": receipt,
        "daily_task_minutes_budget": receipt["daily_task_minutes"],
        "total_mastery_task_minutes": total_mastery_minutes,
        "initial_learning_task_minutes": initial_minutes,
        "projected_review_task_minutes": projected_review_minutes,
        "initial_learning_ratio": round(
            initial_minutes / max(1, total_mastery_minutes), 4
        ),
        "projected_review_ratio": round(
            projected_review_minutes / max(1, total_mastery_minutes), 4
        ),
        "expected_actual_elapsed_minutes_range": {
            "min": round(total_mastery_minutes * elapsed_multipliers[0]),
            "max": round(total_mastery_minutes * elapsed_multipliers[1]),
            "basis": (
                f"current_foundation={level}; "
                f"task_minutes×{elapsed_multipliers[0]:.2f}–{elapsed_multipliers[1]:.2f}"
            ),
        },
        "materialized_review_task_minutes": sum(
            int(item["estimated_task_minutes"])
            for item in tasks
            if item["task_type"] == "review"
        ),
        "review_scheduling_policy": (
            "复习分钟计入完全掌握容量预测；只有最终独立应用阶段通过后才进入复习队列，"
            "初始计划不为未学知识创建复习任务。"
        ),
        "tasks": tasks,
        "days": [
            {
                "date": day,
                "estimated_task_minutes": sum(item["estimated_task_minutes"] for item in items),
                "completed_task_minutes": sum(item["completed_task_minutes"] for item in items),
                "actual_elapsed_minutes": round(sum(item["actual_elapsed_minutes"] for item in items), 3),
                "tasks": items,
            }
            for day, items in sorted(days.items())
        ],
    }
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_plan_calendars'"
    ).fetchone():
        calendar_row = connection.execute(
            "SELECT calendar,available_capacity_minutes FROM active_plan_calendars WHERE plan_id=?",
            (plan_id,),
        ).fetchone()
        if calendar_row:
            result["calendar"] = json.loads(calendar_row["calendar"])
            for day in result["calendar"]:
                planned = int(day.get("planned_task_minutes") or 0)
                day["expected_actual_elapsed_minutes_range"] = {
                    "min": round(planned * elapsed_multipliers[0]),
                    "max": round(planned * elapsed_multipliers[1]),
                }
            result["available_capacity_minutes"] = calendar_row["available_capacity_minutes"]
    calendar = result.get("calendar") or []
    result["initial_learning_period_ratio"] = receipt.get(
        "initial_learning_period_ratio"
    )
    result["phase_boundary"] = {
        "initial_learning_dates": [
            day["date"] for day in calendar if day.get("study_phase") == "initial_learning"
        ],
        "review_dates": [
            day["date"] for day in calendar if day.get("study_phase") == "review"
        ],
    }
    return result


@router.get("/workspaces/{workspace_id}/plans/current")
def current_plan(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT id FROM active_plans WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if not row:
            _error("ACTIVE_PLAN_NOT_FOUND", "主动教学计划尚未生成", 404)
        return _plan(connection, row["id"])


def _citations_for_point(connection: sqlite3.Connection, point_id: str) -> list[dict[str, Any]]:
    row = connection.execute(
        "SELECT source_evidence FROM active_knowledge_points WHERE id=?", (point_id,)
    ).fetchone()
    return json.loads(row["source_evidence"]) if row else []


def _task_rows(connection: sqlite3.Connection, plan_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """SELECT * FROM active_plan_tasks
           WHERE plan_id=? AND task_type='initial_learning'
           ORDER BY point_sequence,day_index,task_order,id""",
        (plan_id,),
    ).fetchall()


def _review_total_minutes_for_point(
    connection: sqlite3.Connection, point: sqlite3.Row
) -> int:
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_point_details'"
    ).fetchone():
        detail = connection.execute(
            "SELECT mastery_estimate FROM active_point_details WHERE knowledge_point_id=?",
            (point["id"],),
        ).fetchone()
        if detail:
            try:
                minutes = int(json.loads(detail["mastery_estimate"]).get("review_minutes") or 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                minutes = 0
            if minutes > 0:
                return max(5, min(120, minutes))
    return max(5, min(60, round(int(point["estimated_task_minutes"]) * 0.2)))


def _review_occurrence_minutes(total_minutes: int, cycle: int) -> int:
    """Split the complete +1/+3/+7 review budget without multiplying it by three."""
    total = max(3, int(total_minutes))
    cycle_index = max(1, min(3, int(cycle))) - 1
    base, remainder = divmod(total, 3)
    return base + (1 if cycle_index < remainder else 0)


def _task_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "day_date": row["day_date"],
        "status": row["status"],
        "task_type": row["task_type"],
        "learning_phase": row["learning_phase"],
        "linked_review_item_id": row["linked_review_item_id"],
        "origin_task_id": row["origin_task_id"],
        "not_before_date": row["not_before_date"],
        "estimated_task_minutes": row["estimated_task_minutes"],
        "completed_task_minutes": row["completed_task_minutes"],
        "actual_elapsed_minutes": row["actual_elapsed_minutes"],
    }


def _refresh_plan_calendar(
    connection: sqlite3.Connection,
    plan_id: str,
    receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rebuild the persisted calendar from authoritative plan-task rows."""
    from .generic_knowledge import calendar_capacities

    calendar = [
        {
            **item,
            "planned_task_minutes": 0,
            "completed_task_minutes": 0,
            "actual_elapsed_minutes": 0.0,
            "tasks": [],
        }
        for item in calendar_capacities(receipt)
    ]
    by_date = {item["date"]: item for item in calendar}
    rows = connection.execute(
        """SELECT * FROM active_plan_tasks
           WHERE plan_id=? ORDER BY day_index,task_order,id""",
        (plan_id,),
    ).fetchall()
    for row in rows:
        if row["status"] == "unscheduled" or row["day_date"] not in by_date:
            continue
        day = by_date[row["day_date"]]
        payload = dict(row)
        payload["source_evidence"] = json.loads(payload["source_evidence"])
        day["tasks"].append(payload)
        day["planned_task_minutes"] += int(row["estimated_task_minutes"])
        day["completed_task_minutes"] += int(row["completed_task_minutes"])
        day["actual_elapsed_minutes"] = round(
            float(day["actual_elapsed_minutes"]) + float(row["actual_elapsed_minutes"]),
            3,
        )
    review_days = [
        day
        for day in calendar
        if day.get("study_phase") == "review" and day["capacity_minutes"] > 0
    ]
    if review_days:
        points = connection.execute(
            """SELECT point.*
               FROM active_knowledge_points AS point
               JOIN (
                 SELECT knowledge_point_id, MIN(point_sequence) AS sequence
                 FROM active_plan_tasks
                 WHERE plan_id=? AND task_type='initial_learning'
                 GROUP BY knowledge_point_id
               ) AS planned ON planned.knowledge_point_id=point.id
               ORDER BY planned.sequence,point.id""",
            (plan_id,),
        ).fetchall()
        for index, point in enumerate(points):
            day = review_days[index % len(review_days)]
            item = {
                "knowledge_point_id": point["id"],
                "title": point["name"],
                "projected_review_task_minutes": _review_total_minutes_for_point(
                    connection, point
                ),
                "status": "awaiting_initial_mastery",
            }
            day.setdefault("projected_review_items", []).append(item)
            day["projected_review_task_minutes"] = int(
                day.get("projected_review_task_minutes") or 0
            ) + int(item["projected_review_task_minutes"])
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_plan_calendars'"
    ).fetchone():
        connection.execute(
            """INSERT INTO active_plan_calendars
               (plan_id,workspace_id,calendar,available_capacity_minutes,created_at)
               VALUES (
                 ?,
                 (SELECT workspace_id FROM active_plans WHERE id=?),
                 ?,?,
                 ?
               )
               ON CONFLICT(plan_id) DO UPDATE SET
                 calendar=excluded.calendar,
                 available_capacity_minutes=excluded.available_capacity_minutes""",
            (
                plan_id,
                plan_id,
                json.dumps(calendar, ensure_ascii=False),
                sum(int(item["capacity_minutes"]) for item in calendar),
                _now(),
            ),
        )
    return calendar


def _reschedule_plan_work(
    connection: sqlite3.Connection,
    *,
    plan_id: str,
    receipt: dict[str, Any],
    affected_from: date,
) -> tuple[list[str], int, list[dict[str, Any]]]:
    """Pack unfinished main work and materialized auxiliary work into one calendar.

    Completed/attempted work is fixed and consumes capacity. Review work may never
    be placed before its due date. Main-route tasks keep their dependency order.
    """
    from .generic_knowledge import calendar_capacities

    capacities = calendar_capacities(receipt)
    eligible = [
        item for item in capacities if item["date"] >= affected_from.isoformat()
    ]
    remaining = {item["date"]: int(item["capacity_minutes"]) for item in eligible}
    day_index = {item["date"]: index for index, item in enumerate(capacities)}
    all_rows = connection.execute(
        """SELECT * FROM active_plan_tasks
           WHERE plan_id=? ORDER BY day_index,task_order,id""",
        (plan_id,),
    ).fetchall()
    movable_statuses = {"planned", "unscheduled"}
    pending = [row for row in all_rows if row["status"] in movable_statuses]
    fixed = [row for row in all_rows if row["status"] not in movable_statuses]
    order_by_day: dict[str, int] = defaultdict(int)
    for row in fixed:
        day_value = str(row["day_date"])
        if day_value not in remaining or row["status"] == "cancelled":
            continue
        remaining[day_value] = max(
            0, remaining[day_value] - int(row["estimated_task_minutes"])
        )
        order_by_day[day_value] = max(order_by_day[day_value], int(row["task_order"]))

    phase_rank = {
        "understand": 0,
        "recall": 1,
        "guided_practice": 2,
        "independent_application": 3,
    }
    main_queue = sorted(
        (row for row in pending if row["task_type"] == "initial_learning"),
        key=lambda row: (
            int(row["point_sequence"]),
            phase_rank.get(str(row["learning_phase"]), 99),
            int(row["phase_part_index"]),
            int(row["day_index"]),
            int(row["task_order"]),
            row["id"],
        ),
    )
    review_queue = sorted(
        (row for row in pending if row["task_type"] == "review"),
        key=lambda row: (
            str(row["not_before_date"] or row["day_date"]),
            int(row["review_cycle"]),
            row["id"],
        ),
    )
    remediation_queue = sorted(
        (row for row in pending if row["task_type"] == "remediation"),
        key=lambda row: (
            str(row["not_before_date"] or affected_from.isoformat()),
            int(row["day_index"]),
            int(row["task_order"]),
            row["id"],
        ),
    )
    other_queue = sorted(
        (
            row
            for row in pending
            if row["task_type"] not in {"initial_learning", "review", "remediation"}
        ),
        key=lambda row: (int(row["day_index"]), int(row["task_order"]), row["id"]),
    )
    previous = {row["id"]: (row["day_date"], row["status"]) for row in pending}
    assigned: set[str] = set()
    moved: list[str] = []

    def place(row: sqlite3.Row, day_value: str) -> None:
        order_by_day[day_value] += 1
        connection.execute(
            """UPDATE active_plan_tasks
               SET day_date=?,day_index=?,task_order=?,status='planned'
               WHERE id=?""",
            (
                day_value,
                day_index[day_value],
                order_by_day[day_value],
                row["id"],
            ),
        )
        remaining[day_value] -= int(row["estimated_task_minutes"])
        assigned.add(row["id"])
        if previous[row["id"]] != (day_value, "planned"):
            moved.append(row["id"])

    for day in eligible:
        day_value = day["date"]
        while remaining[day_value] > 0:
            due_reviews = [
                row
                for row in review_queue
                if row["id"] not in assigned
                and str(row["not_before_date"] or row["day_date"]) <= day_value
                and int(row["estimated_task_minutes"]) <= remaining[day_value]
            ]
            due_remediation = [
                row
                for row in remediation_queue
                if row["id"] not in assigned
                and str(row["not_before_date"] or affected_from.isoformat()) <= day_value
                and int(row["estimated_task_minutes"]) <= remaining[day_value]
            ]
            if due_remediation:
                place(due_remediation[0], day_value)
                continue
            if due_reviews:
                place(due_reviews[0], day_value)
                continue
            main = next((row for row in main_queue if row["id"] not in assigned), None)
            if main is not None:
                not_before = str(main["not_before_date"] or affected_from.isoformat())
                if (
                    not_before <= day_value
                    and int(main["estimated_task_minutes"]) <= remaining[day_value]
                ):
                    place(main, day_value)
                    continue
            other = next(
                (
                    row
                    for row in other_queue
                    if row["id"] not in assigned
                    and str(row["not_before_date"] or affected_from.isoformat()) <= day_value
                    and int(row["estimated_task_minutes"]) <= remaining[day_value]
                ),
                None,
            )
            if other is not None:
                place(other, day_value)
                continue
            break

    unscheduled = 0
    for row in pending:
        if row["id"] in assigned:
            continue
        connection.execute(
            """UPDATE active_plan_tasks
               SET status='unscheduled',day_index=999999,task_order=999999
               WHERE id=?""",
            (row["id"],),
        )
        if previous[row["id"]][1] != "unscheduled":
            moved.append(row["id"])
        unscheduled += int(row["estimated_task_minutes"])
    connection.execute(
        """UPDATE active_review_items
           SET scheduled_date=(
             SELECT CASE WHEN task.status='unscheduled' THEN NULL ELSE task.day_date END
             FROM active_plan_tasks AS task
             WHERE task.id=active_review_items.scheduled_task_id
           ),
           updated_at=?
           WHERE plan_id=? AND scheduled_task_id<>''""",
        (_now(), plan_id),
    )
    calendar = _refresh_plan_calendar(connection, plan_id, receipt)
    return list(dict.fromkeys(moved)), unscheduled, calendar


def _materialize_review_occurrence(
    connection: sqlite3.Connection,
    *,
    session: sqlite3.Row,
    review: sqlite3.Row,
    point: sqlite3.Row,
    due_date: str,
    cycle: int,
    trigger_reason: str,
) -> str:
    """Create one auditable plan task for one review occurrence."""
    plan_row = connection.execute(
        "SELECT receipt FROM active_plans WHERE id=?", (session["plan_id"],)
    ).fetchone()
    if plan_row:
        from .generic_knowledge import calendar_capacities

        review_dates = [
            day["date"]
            for day in calendar_capacities(json.loads(plan_row["receipt"]))
            if day.get("study_phase") == "review" and day["capacity_minutes"] > 0
        ]
        if review_dates and due_date < review_dates[0]:
            due_date = review_dates[0]
    current_task_id = str(review["scheduled_task_id"] or "")
    if current_task_id:
        current = connection.execute(
            "SELECT id,status FROM active_plan_tasks WHERE id=?",
            (current_task_id,),
        ).fetchone()
        if current and current["status"] in {"planned", "in_progress", "unscheduled"}:
            return current["id"]
    total_minutes = int(review["review_total_minutes"] or 0)
    if total_minutes <= 0:
        total_minutes = _review_total_minutes_for_point(connection, point)
    occurrence_minutes = _review_occurrence_minutes(total_minutes, cycle)
    task_id = _id("active_review_task")
    connection.execute(
        """INSERT INTO active_plan_tasks
           (id,plan_id,workspace_id,day_date,day_index,task_order,
            knowledge_point_id,title,objective,source_evidence,
            acceptance_condition,estimated_task_minutes,
            completed_task_minutes,actual_elapsed_minutes,status,
            task_type,learning_phase,point_sequence,phase_part_index,phase_part_count,
            linked_review_item_id,review_cycle,origin_task_id,not_before_date,
            trigger_reason)
           VALUES (?,?,?,?,999999,999999,?,?,?,?,?,?,0,0,'unscheduled',
                   'review','review',0,1,1,?,?,?,?,?)""",
        (
            task_id,
            session["plan_id"],
            session["workspace_id"],
            due_date,
            point["id"],
            f"{point['name']} · 第 {cycle} 次间隔复习",
            (
                "在不依赖答案或提示的情况下完成延迟复测；"
                "只有独立通过才增加复习掌握证据。"
            ),
            point["source_evidence"],
            point["acceptance_method"],
            occurrence_minutes,
            review["id"],
            cycle,
            review["source_task_id"],
            due_date,
            trigger_reason,
        ),
    )
    connection.execute(
        """UPDATE active_review_items
           SET plan_id=?,scheduled_task_id=?,scheduled_date=NULL,
               estimated_task_minutes=?,review_total_minutes=?,updated_at=?
           WHERE id=?""",
        (
            session["plan_id"],
            task_id,
            occurrence_minutes,
            total_minutes,
            _now(),
            review["id"],
        ),
    )
    return task_id


def _estimate_remediation_task_minutes(
    task: sqlite3.Row, point: sqlite3.Row, failure_count: int
) -> int:
    difficulty = str(point["difficulty"] or "medium").casefold()
    factor = 0.25 if difficulty in {"easy", "简单"} else 0.35
    if difficulty in {"hard", "困难"}:
        factor = 0.45
    estimate = round(int(task["estimated_task_minutes"]) * factor) + min(
        20, max(0, failure_count) * 5
    )
    return max(10, min(60, estimate))


def _create_remediation_task(
    connection: sqlite3.Connection,
    *,
    session: sqlite3.Row,
    state: dict[str, Any],
    task: sqlite3.Row,
    point: sqlite3.Row,
    trigger_reason: str,
) -> dict[str, Any]:
    stack = state.get("remediation_stack") or []
    if stack:
        active_id = str(stack[-1].get("remediation_task_id") or "")
        if active_id:
            existing = connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=?", (active_id,)
            ).fetchone()
            if existing and existing["status"] in {
                "planned",
                "in_progress",
                "unscheduled",
            }:
                return dict(existing)
    failure_count = sum(
        1
        for item in state.get("unresolved_confusions") or []
        if item.get("knowledge_point_id") == point["id"]
    )
    minutes = _estimate_remediation_task_minutes(task, point, failure_count)
    today = date.today().isoformat()
    remediation_id = _id("active_remediation_task")
    connection.execute(
        """INSERT INTO active_plan_tasks
           (id,plan_id,workspace_id,day_date,day_index,task_order,
            knowledge_point_id,title,objective,source_evidence,
            acceptance_condition,estimated_task_minutes,
            completed_task_minutes,actual_elapsed_minutes,status,
            task_type,learning_phase,point_sequence,phase_part_index,phase_part_count,
            linked_review_item_id,review_cycle,origin_task_id,not_before_date,
            trigger_reason)
           VALUES (?,?,?,?,999999,999999,?,?,?,?,?,?,0,0,'planned',
                   'remediation','remediation',0,1,1,'',0,?,?,?)""",
        (
            remediation_id,
            session["plan_id"],
            session["workspace_id"],
            today,
            point["id"],
            f"{point['name']} · 临时深化教学",
            "定位当前缺失的前置知识，降低解释层级，完成更简单示例并重新检测理解。",
            point["source_evidence"],
            point["acceptance_method"],
            minutes,
            task["id"],
            today,
            trigger_reason,
        ),
    )
    plan = connection.execute(
        "SELECT * FROM active_plans WHERE id=?", (session["plan_id"],)
    ).fetchone()
    _replan_pending_in_transaction(
        connection,
        session=session,
        plan=plan,
        receipt=json.loads(plan["receipt"]),
        affected_from=date.today(),
        trigger_name="remediation_inserted",
        reason=f"知识点“{point['name']}”触发临时深化教学。",
        state=state,
    )
    connection.execute(
        """UPDATE active_plan_tasks
           SET status='in_progress' WHERE id=?""",
        (remediation_id,),
    )
    return dict(
        connection.execute(
            "SELECT * FROM active_plan_tasks WHERE id=?", (remediation_id,)
        ).fetchone()
    )


def _review_items(
    connection: sqlite3.Connection, session_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT review.*,point.name AS knowledge_point_name,
                  point.acceptance_method,point.source_evidence
           FROM active_review_items AS review
           JOIN active_knowledge_points AS point ON point.id=review.knowledge_point_id
           WHERE review.session_id=?
           ORDER BY CASE review.status WHEN 'in_progress' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                    review.due_date,review.created_at,review.id""",
        (session_id,),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["source_evidence"] = json.loads(item["source_evidence"])
        if item["last_passed"] is not None:
            item["last_passed"] = bool(item["last_passed"])
        items.append(item)
    return items


def _sync_pending_reviews(
    connection: sqlite3.Connection, session_id: str, state: dict[str, Any]
) -> None:
    state["pending_reviews"] = [
        {
            "id": item["id"],
            "knowledge_point_id": item["knowledge_point_id"],
            "knowledge_point_name": item["knowledge_point_name"],
            "source_task_id": item["source_task_id"],
            "due_date": item["due_date"],
            "scheduled_date": item["scheduled_date"],
            "scheduled_task_id": item["scheduled_task_id"],
            "interval_days": item["interval_days"],
            "review_total_minutes": item["review_total_minutes"],
            "estimated_task_minutes": item["estimated_task_minutes"],
            "status": item["status"],
            "attempt_count": item["attempt_count"],
            "success_streak": item["success_streak"],
            "last_score": item["last_score"],
            "last_passed": item["last_passed"],
        }
        for item in _review_items(connection, session_id)
        if item["status"] in {"pending", "in_progress"}
    ]


def _public_session(connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
    messages = []
    for message in connection.execute(
        "SELECT * FROM active_teaching_messages WHERE session_id=? ORDER BY created_at,id",
        (session_id,),
    ).fetchall():
        item = dict(message)
        item["citations"] = json.loads(item["citations"])
        messages.append(item)
    state = json.loads(row["state"])
    _sync_pending_reviews(connection, session_id, state)
    incomplete_initial = int(
        connection.execute(
            """SELECT COUNT(*) FROM active_plan_tasks
               WHERE plan_id=? AND task_type='initial_learning'
                 AND status<>'completed'""",
            (row["plan_id"],),
        ).fetchone()[0]
    )
    incomplete_initial_points = int(
        connection.execute(
            """SELECT COUNT(DISTINCT knowledge_point_id)
               FROM active_plan_tasks
               WHERE plan_id=? AND task_type='initial_learning'
                 AND status<>'completed'""",
            (row["plan_id"],),
        ).fetchone()[0]
    )
    state["formal_review_gate"] = {
        "unlocked": incomplete_initial == 0,
        "incomplete_initial_task_count": incomplete_initial,
        "incomplete_initial_knowledge_point_count": incomplete_initial_points,
        "daily_consolidation_counts_as_formal_review": False,
        "reason": (
            "all_initial_learning_completed"
            if incomplete_initial == 0
            else "complete_first_pass_before_formal_review"
        ),
    }
    return {
        "session_id": row["id"],
        "workspace_id": row["workspace_id"],
        "plan_id": row["plan_id"],
        "state": state,
        "messages": messages,
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "actual_elapsed_minutes": round(float(row["actual_elapsed_minutes"]), 3),
    }


def _record_message(
    connection: sqlite3.Connection,
    session_id: str,
    role: str,
    action: str,
    content: str,
    citations: list[dict[str, Any]],
) -> None:
    connection.execute(
        """INSERT INTO active_teaching_messages(id,session_id,role,action,content,citations,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (_id("teaching_message"), session_id, role, action, content, json.dumps(citations, ensure_ascii=False), _now()),
    )


async def _render_teacher_content(
    *,
    draft: str,
    action: str,
    citations: list[dict[str, Any]],
    provider_session_id: str | None,
    state: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Let a verified real provider improve delivery without controlling state or facts."""
    if not provider_session_id:
        return draft, {
            "mode": "system_guided",
            "provider": None,
            "fallback_used": False,
            "scope": "state_and_source_grounded_template",
        }
    try:
        provider_session = require_provider_session(provider_session_id)
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    if provider_session.provider == "fake":
        _error(
            "REAL_PROVIDER_REQUIRED",
            "主动教学表达需要已验证的真实 Provider；Fake Provider 不能冒充 AI 教师。",
            428,
        )
    evidence = [
        {
            "source_file": item.get("source_file"),
            "page": item.get("page"),
            "paragraph": item.get("paragraph"),
            "quote": str(item.get("quote") or "")[:1200],
        }
        for item in citations
    ]
    prompt = {
        "fixed_action": action,
        "fixed_route_position": {
            "route_index": state.get("route_index"),
            "route_length": state.get("route_length"),
            "teaching_step": state.get("current_teaching_step"),
            "next_action": state.get("next_action"),
        },
        "source_grounded_draft": draft,
        "teacher_persona": state.get("teacher_persona") or "",
        "learner_context": state.get("learner_context") or {},
        "persistent_memory": state.get("teacher_memory") or {},
        "allowed_evidence": evidence,
        "rules": [
            "Rewrite the draft as concise, supportive Chinese teaching language.",
            "Do not change the action, route, task completion, mastery, timing, or next action.",
            "Do not add factual claims beyond the draft and allowed evidence.",
            "For a zero-foundation learner, explain with one vivid everyday analogy, clearly label it as an analogy, then map it back to the cited source.",
            "Follow the user's teacher persona when it does not conflict with evidence or safety.",
            "Use persistent memory to avoid repeating failed explanations and to acknowledge prior questions.",
            "Keep uncertainty statements exactly uncertain; never fabricate missing evidence.",
            "Return JSON only with one string field named content.",
        ],
    }
    try:
        payload = await provider_for_session(provider_session).json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are only the teaching-expression layer. The application owns "
                        "facts, citations, teaching state, grading, timing and routing."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        )
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    rendered = str(payload.get("content") or "").strip() if isinstance(payload, dict) else ""
    if not rendered:
        _error(
            "PROVIDER_TEACHING_RESPONSE_INVALID",
            "Provider 未返回有效的教学表达内容。",
            502,
        )
    return rendered, generation_meta(provider_session) | {
        "scope": "expression_only",
        "fixed_action": action,
    }


def _merge_citations(
    primary: list[dict[str, Any]], fallback: list[dict[str, Any]], limit: int = 5
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*primary, *fallback]:
        key = str(item.get("chunk_id") or item.get("content_hash") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _opening(connection: sqlite3.Connection, task: sqlite3.Row, receipt: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    point = connection.execute(
        "SELECT * FROM active_knowledge_points WHERE id=?", (task["knowledge_point_id"],)
    ).fetchone()
    stored_citations = json.loads(point["source_evidence"])
    retrieved = _search(
        connection,
        task["workspace_id"],
        f"{point['name']} {point['explanation']} {point['acceptance_method']}",
        plan_id=task["plan_id"],
    )
    citations = _merge_citations(retrieved, stored_citations)
    zero_foundation = str((receipt or {}).get("current_foundation") or "").casefold() == "zero_foundation"
    interests = str((receipt or {}).get("learner_interests") or "").strip()
    familiar_scene = interests.split("、")[0] if interests else "日常任务"
    analogy_text = (
        f"先把“{point['name']}”放进你熟悉的“{familiar_scene}”场景里："
        "先找出对象、条件、动作和可观察结果，再逐项映射回资料中的原概念。"
        "这个类比只帮助理解结构，不会替资料补写事实；资料没有说明的结论，我会明确标成待验证。"
    )
    analogy = (
        f"\n\n生活类比（帮助理解，不是资料原文）：{analogy_text}"
        if zero_foundation else ""
    )
    content = (
        f"今天先学习“{point['name']}”。我会主动按“讲解→示例→检查理解→独立验收”推进。"
        f"\n\n讲解：{point['explanation']}"
        f"{analogy}"
        f"\n\n本任务预计 {task['estimated_task_minutes']} 任务分钟；验收条件：{point['acceptance_method']}"
    )
    return content, citations


def _daily_route_summary(
    connection: sqlite3.Connection, plan_id: str, day_date: str
) -> dict[str, Any]:
    rows = connection.execute(
        """SELECT id,knowledge_point_id,title,estimated_task_minutes,status
           FROM active_plan_tasks
           WHERE plan_id=? AND day_date=? AND task_type='initial_learning'
           ORDER BY task_order,id""",
        (plan_id, day_date),
    ).fetchall()
    point_ids = list(dict.fromkeys(str(row["knowledge_point_id"]) for row in rows))
    return {
        "date": day_date,
        "task_count": len(rows),
        "knowledge_point_count": len(point_ids),
        "knowledge_point_ids": point_ids,
        "task_minutes": sum(int(row["estimated_task_minutes"]) for row in rows),
        "teaching_order": [
            "knowledge_briefing",
            "grounded_explanation",
            "guided_example",
            "learner_practice",
            "assessment_or_remediation",
        ],
        "tasks": [dict(row) for row in rows],
    }


def _daily_briefing(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    plan_id: str,
    day_date: str,
) -> tuple[str, list[dict[str, Any]]]:
    summary = _daily_route_summary(connection, plan_id, day_date)
    point_ids = summary["knowledge_point_ids"]
    points = [
        connection.execute(
            "SELECT * FROM active_knowledge_points WHERE id=?", (point_id,)
        ).fetchone()
        for point_id in point_ids
    ]
    points = [point for point in points if point is not None]
    names = [str(point["name"]) for point in points]
    query = " ".join(names[:12])
    citations = _search(connection, workspace_id, query, plan_id=plan_id) if query else []
    for point in points[:5]:
        citations = _merge_citations(
            citations, json.loads(point["source_evidence"])
        )
    preview = "、".join(names[:6])
    if len(names) > 6:
        preview += f"等 {len(names)} 个知识点"
    content = (
        f"今天不是直接让你做题。我们先建立完成今日任务需要的知识框架：{preview}。"
        f"\n\n今日计划约 {summary['task_minutes']} 个标准任务分钟。"
        "我会先检索资料讲清概念和先修关系，再示范最小例子，然后让你完成一个小练习；"
        "我会根据你的回答决定补讲、降低难度或继续，而不是照着资料念。"
    )
    return content, citations


def _grade_understanding(message: str, point: sqlite3.Row) -> tuple[float, list[str]]:
    """Provide a domain-neutral precheck, never a mastery verdict.

    A long answer or a click on "understood" is not independently verified
    evidence.  This precheck can detect whether the answer is substantive and
    grounded in the current point, but deliberately stays below the completion
    threshold until a real rubric evaluator or a domain verifier is available.
    """
    reference = " ".join(
        str(value or "")
        for value in (point["name"], point["explanation"], point["acceptance_method"])
    )
    reference_terms: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", reference):
        lowered = token.casefold()
        if re.fullmatch(r"[\u4e00-\u9fff]+", lowered):
            reference_terms.update(
                lowered[index : index + 2] for index in range(max(0, len(lowered) - 1))
            )
        else:
            reference_terms.add(lowered)
    lowered_message = message.casefold()
    grounded_hits = sum(1 for term in reference_terms if term in lowered_message)
    checks = {
        "substantive_answer": len(message.strip()) >= 30,
        "explains_relationship_or_steps": bool(
            re.search(r"因为|所以|关系|步骤|首先|然后|意味着|because|therefore|first|then", message, re.I)
        ),
        "example_or_application": bool(
            re.search(r"例如|比如|场景|应用|假设|example|apply", message, re.I)
        ),
        "grounded_in_current_point": grounded_hits >= 2,
    }
    passed_checks = [name for name, passed in checks.items() if passed]
    score = min(0.69, 0.35 + 0.08 * len(passed_checks))
    return round(score, 2), passed_checks


async def _evaluate_understanding(
    *,
    message: str,
    point: sqlite3.Row,
    citations: list[dict[str, Any]],
    provider_session_id: str | None,
) -> tuple[float, bool, list[str], dict[str, Any]]:
    precheck_score, precheck_checks = _grade_understanding(message, point)
    if not provider_session_id:
        return precheck_score, False, precheck_checks, {
            "mode": "unverified_without_real_provider",
            "reason": "文本长度或用户自报不能作为可验证掌握证据",
        }
    try:
        provider_session = require_provider_session(provider_session_id)
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    if provider_session.provider == "fake":
        _error(
            "REAL_PROVIDER_REQUIRED",
            "理解验收需要已验证的真实 Provider 或领域验证器；Fake Provider 不能给出掌握结论。",
            428,
        )
    rubric = str(point["acceptance_method"] or "").strip()
    prompt = {
        "knowledge_point": {
            "name": point["name"],
            "explanation": point["explanation"],
            "acceptance_method": rubric,
        },
        "learner_answer": message,
        "source_evidence": [
            {
                "chunk_id": item.get("chunk_id"),
                "quote": str(item.get("quote") or "")[:1200],
            }
            for item in citations
        ],
        "rules": [
            "Judge only against the supplied acceptance method and source evidence.",
            "Do not reward the learner merely for claiming to understand.",
            "Score 0 to 1. criteria_met is true only when the answer demonstrates the observable acceptance condition.",
            "Return JSON only: score, criteria_met, rationale.",
        ],
    }
    try:
        payload = await provider_for_session(provider_session).json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a source-grounded rubric evaluator. You do not control "
                        "course state and you cannot mark a task complete."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        )
    except ProviderError as exc:
        _error(exc.code, exc.message, exc.status, **exc.details)
    try:
        provider_score = max(0.0, min(1.0, float(payload.get("score"))))
    except (AttributeError, TypeError, ValueError):
        _error("PROVIDER_ASSESSMENT_INVALID", "Provider 未返回有效的理解评分。", 502)
    criteria_met = bool(payload.get("criteria_met"))
    required_prechecks = {
        "substantive_answer",
        "grounded_in_current_point",
    }
    passed = (
        criteria_met
        and provider_score >= 0.8
        and required_prechecks.issubset(precheck_checks)
    )
    return round(provider_score, 2), passed, precheck_checks, generation_meta(
        provider_session
    ) | {
        "scope": "grounded_rubric_evaluation",
        "criteria_met": criteria_met,
        "rationale": str(payload.get("rationale") or "")[:1000],
        "system_threshold": 0.8,
        "required_prechecks": sorted(required_prechecks),
        "prechecks_satisfied": required_prechecks.issubset(precheck_checks),
    }


@router.post("/workspaces/{workspace_id}/sessions/start")
async def start_session(
    workspace_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    with _connect() as connection:
        plan_row = connection.execute(
            "SELECT id,receipt FROM active_plans WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        if not plan_row:
            _error("ACTIVE_PLAN_REQUIRED", "请先生成主动教学计划", 409)
        existing = connection.execute(
            """SELECT id FROM active_teaching_sessions
               WHERE plan_id=? ORDER BY started_at DESC LIMIT 1""",
            (plan_row["id"],),
        ).fetchone()
        if existing:
            return _public_session(connection, existing["id"])
        tasks = _task_rows(connection, plan_row["id"])
        if not tasks:
            _error("ACTIVE_PLAN_EMPTY", "计划没有可执行任务", 409)
        receipt = json.loads(plan_row["receipt"])
        first = tasks[0]
        state = {
            "current_learning_goal": receipt["learning_goal"],
            "current_knowledge_point_id": first["knowledge_point_id"],
            "current_teaching_step": "daily_briefing",
            "today_remaining_task_minutes": receipt["daily_task_minutes"],
            "completed_tasks": [],
            "pending_reviews": [],
            "unresolved_confusions": [],
            "remediation_stack": [],
            "route_index": 0,
            "route_length": len(tasks),
            "active_task_id": first["id"],
            "mastery_evidence": [],
            "next_action": "explain_current_point",
            "estimated_task_minutes": first["estimated_task_minutes"],
            "completed_task_minutes": 0,
            "actual_elapsed_minutes": 0.0,
            "teacher_persona": receipt.get("teacher_persona") or "",
            "learner_context": {
                "foundation": receipt.get("current_foundation") or receipt.get("current_level"),
                "interests": receipt.get("learner_interests") or "",
                "preferred_learning_style": receipt.get("preferred_learning_style") or "",
            },
            "teacher_memory": {
                "plan_id": plan_row["id"],
                "material_ids": list(receipt.get("material_ids") or []),
                "learner_questions": [],
                "unresolved_points": [],
                "explanations_that_helped": [],
                "last_interaction": "",
            },
            "autonomy": {
                "mode": "teacher_led_until_learner_input",
                "awaiting_learner": False,
                "reason": "daily_knowledge_briefing",
            },
            "today_plan": _daily_route_summary(
                connection, plan_row["id"], first["day_date"]
            ),
        }
        session_id = _id("active_session")
        started = _now()
        connection.execute(
            """INSERT INTO active_teaching_sessions
               (id,workspace_id,plan_id,state,started_at,updated_at,actual_elapsed_minutes)
               VALUES (?,?,?,?,?,?,?)""",
            (session_id, workspace_id, plan_row["id"], json.dumps(state, ensure_ascii=False), started, started, 0.0),
        )
        opening, citations = _daily_briefing(
            connection,
            workspace_id=workspace_id,
            plan_id=plan_row["id"],
            day_date=first["day_date"],
        )
        opening, teacher_generation = await _render_teacher_content(
            draft=opening,
            action="daily_briefing",
            citations=citations,
            provider_session_id=str(body.get("provider_session_id") or "").strip() or None,
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        if teacher_generation.get("mode") == "real":
            state["provider_session_id"] = teacher_generation.get("provider_session_id")
        connection.execute(
            "UPDATE active_teaching_sessions SET state=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), session_id),
        )
        _record_message(
            connection, session_id, "teacher", "daily_briefing", opening, citations
        )
        state["teacher_memory"]["last_interaction"] = "daily_briefing"
        connection.execute(
            "UPDATE active_teaching_sessions SET state=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), session_id),
        )
        connection.commit()
        return _public_session(connection, session_id)


@router.post("/sessions/{session_id}/teach-next")
async def teach_next(
    session_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Advance teacher-led delivery until learner evidence is required.

    This endpoint never invents a learner response and never completes a task.
    It lets the teacher proactively deliver the explanation/example and then
    stop at an explicit learner check.
    """
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        state = json.loads(session["state"])
        tasks = _task_rows(connection, session["plan_id"])
        route_index = min(int(state["route_index"]), len(tasks) - 1)
        task = tasks[route_index]
        point = connection.execute(
            "SELECT * FROM active_knowledge_points WHERE id=?",
            (task["knowledge_point_id"],),
        ).fetchone()
        citations = _merge_citations(
            _search(
                connection,
                session["workspace_id"],
                f"{point['name']} {point['explanation']} {point['acceptance_method']}",
                plan_id=session["plan_id"],
            ),
            json.loads(point["source_evidence"]),
        )
        step = str(state.get("current_teaching_step") or "explain")
        if step == "daily_briefing":
            action = "explain"
            plan_receipt = json.loads(connection.execute(
                "SELECT receipt FROM active_plans WHERE id=?", (session["plan_id"],)
            ).fetchone()["receipt"])
            content, citations = _opening(connection, task, plan_receipt)
            state["current_teaching_step"] = "explain"
            state["next_action"] = "show_example"
            awaiting = False
        elif step == "explain":
            action = "invite_questions"
            content = (
                f"“{point['name']}”的核心已经讲完。先不要急着做题。"
                "\n\n你可以直接问：为什么要这样做、哪一步最难、能不能换个例子、"
                "边界条件是什么，或者把你卡住的原话贴出来。"
                "\n\n我会结合当前资料重新检索并回答；你确认没有疑问后，再说“开始练习”。"
            )
            state["current_teaching_step"] = "question_invitation"
            state["next_action"] = "answer_question_or_start_practice"
            awaiting = True
        elif step in {"example", "remediation_example"}:
            action = "check_understanding"
            content = (
                f"现在轮到一个很小的动作：请用自己的话说出“{point['name']}”的核心思路，"
                f"再说明你准备怎样满足验收条件：{point['acceptance_method']}"
                "\n\n不用一次答得完美；答错后我会自动暂停主路线、降低解释层级再教一次。"
            )
            state["current_teaching_step"] = "check_understanding"
            state["next_action"] = "evaluate_answer"
            awaiting = True
        elif step == "remediation_explain":
            action = "remediation_example"
            content = (
                f"我们把“{point['name']}”再降一级，只练一个事实：{point['explanation'][:240]}"
                "\n\n我会在下一步给你一个最小复述题，答错不会跳过，也不会丢失原路线。"
            )
            state["current_teaching_step"] = "remediation_example"
            state["next_action"] = "remediation_recheck"
            awaiting = False
        else:
            state["autonomy"] = {
                "mode": "teacher_led_until_learner_input",
                "awaiting_learner": True,
                "reason": "learner_evidence_required",
            }
            connection.execute(
                "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
                (json.dumps(state, ensure_ascii=False), _now(), session_id),
            )
            connection.commit()
            return _public_session(connection, session_id)
        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action=action,
            citations=citations,
            provider_session_id=(
                str(body.get("provider_session_id") or "").strip()
                or state.get("provider_session_id")
            ),
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        state["autonomy"] = {
            "mode": "teacher_led_until_learner_input",
            "awaiting_learner": awaiting,
            "reason": "learner_evidence_required" if awaiting else "teacher_continues",
        }
        state["last_retrieval"] = {
            "mode": "local_hybrid_v1",
            "query_scope": "current_knowledge_point",
            "citation_count": len(citations),
        }
        teacher_memory = dict(state.get("teacher_memory") or {})
        teacher_memory["last_interaction"] = action
        teacher_memory["recent_turns"] = (
            list(teacher_memory.get("recent_turns") or [])
            + [{"role": "teacher", "action": action, "content": content[:600]}]
        )[-12:]
        state["teacher_memory"] = teacher_memory
        _record_message(connection, session_id, "teacher", action, content, citations)
        connection.execute(
            "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
        connection.commit()
        return _public_session(connection, session_id)


def _retrieval_terms(value: str) -> set[str]:
    terms: set[str] = set()
    for item in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", value):
        lowered = item.casefold()
        if re.fullmatch(r"[\u4e00-\u9fff]+", lowered) and len(lowered) > 2:
            terms.update(lowered[index : index + 2] for index in range(len(lowered) - 1))
        elif len(lowered) > 1:
            terms.add(lowered)
    return terms


def _character_ngrams(value: str, size: int = 2) -> Counter[str]:
    normalized = _normalise(value)
    if not normalized:
        return Counter()
    if len(normalized) <= size:
        return Counter({normalized: 1})
    return Counter(normalized[index : index + size] for index in range(len(normalized) - size + 1))


def _cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    numerator = sum(left[key] * right[key] for key in shared)
    denominator = math.sqrt(sum(value * value for value in left.values())) * math.sqrt(
        sum(value * value for value in right.values())
    )
    return numerator / denominator if denominator else 0.0


def _plan_material_ids(
    connection: sqlite3.Connection, workspace_id: str, plan_id: str | None
) -> list[str]:
    if not plan_id:
        return []
    plan_row = connection.execute(
        "SELECT receipt FROM active_plans WHERE id=? AND workspace_id=?",
        (plan_id, workspace_id),
    ).fetchone()
    if not plan_row:
        return []
    return list(
        dict.fromkeys(
            str(item) for item in (json.loads(plan_row["receipt"]).get("material_ids") or [])
        )
    )


def _search(
    connection: sqlite3.Connection,
    workspace_id: str,
    query: str,
    *,
    plan_id: str | None = None,
) -> list[dict[str, Any]]:
    """Local, workspace-isolated hybrid retrieval with exact chunk citations.

    The index intentionally has no network or model dependency: it combines
    token coverage, Chinese character n-gram similarity, heading boosts and
    source diversity.  It is more tolerant of phrasing changes than the old
    substring gate while keeping every answer traceable to stored chunks.
    """
    terms = _retrieval_terms(query)
    query_ngrams = _character_ngrams(query)
    if not terms:
        return []
    scored: list[tuple[float, sqlite3.Row, list[str]]] = []
    params: list[Any] = [workspace_id]
    material_clause = ""
    if plan_id:
        material_ids = _plan_material_ids(connection, workspace_id, plan_id)
        if not material_ids:
            return []
        material_clause = f" AND chunk.material_id IN ({','.join('?' for _ in material_ids)})"
        params.extend(material_ids)
    for row in connection.execute(
        f"""SELECT chunk.*, material.original_filename
           FROM active_chunks AS chunk
           JOIN active_materials AS material ON material.id=chunk.material_id
           WHERE chunk.workspace_id=? AND material.status='ready'{material_clause}""",
        params,
    ).fetchall():
        text_terms = _retrieval_terms(f"{row['heading']} {row['text']}")
        matched = sorted(terms & text_terms)
        coverage = len(matched) / max(1, len(terms))
        ngram_similarity = _cosine_similarity(query_ngrams, _character_ngrams(row["text"]))
        heading_overlap = len(terms & _retrieval_terms(row["heading"])) / max(1, len(terms))
        phrase_bonus = 0.18 if _normalise(query) and _normalise(query) in _normalise(row["text"]) else 0.0
        score = coverage * 0.58 + ngram_similarity * 0.24 + heading_overlap * 0.18 + phrase_bonus
        if matched and (coverage >= 0.12 or ngram_similarity >= 0.08):
            scored.append((score, row, matched))
    scored.sort(key=lambda item: (-item[0], item[1]["ordinal"], item[1]["id"]))

    # Avoid five adjacent chunks from one file crowding out corroborating sources.
    selected: list[tuple[float, sqlite3.Row, list[str]]] = []
    per_material: Counter[str] = Counter()
    for item in scored:
        material_id = item[1]["material_id"]
        if per_material[material_id] >= 3:
            continue
        selected.append(item)
        per_material[material_id] += 1
        if len(selected) == 5:
            break
    return [
        _evidence(row)
        | {
            "score": round(score, 6),
            "matched_terms": matched[:12],
            "retrieval_mode": "local_hybrid_v1",
        }
        for score, row, matched in selected
    ]


def _knowledge_base_status(
    connection: sqlite3.Connection,
    workspace_id: str,
    material_ids: list[str] | None = None,
) -> dict[str, Any]:
    material_ids = list(dict.fromkeys(str(item) for item in (material_ids or [])))
    material_clause = ""
    params: list[Any] = [workspace_id]
    if material_ids:
        material_clause = f" AND material.id IN ({','.join('?' for _ in material_ids)})"
        params.extend(material_ids)
    row = connection.execute(
        f"""SELECT COUNT(DISTINCT material.id) AS material_count,
                  COUNT(chunk.id) AS chunk_count,
                  COALESCE(SUM(LENGTH(chunk.text)), 0) AS indexed_characters
           FROM active_materials AS material
           LEFT JOIN active_chunks AS chunk ON chunk.material_id=material.id
           WHERE material.workspace_id=? AND material.status='ready'{material_clause}""",
        params,
    ).fetchone()
    chunk_count = int(row["chunk_count"] or 0)
    return {
        "status": "ready" if chunk_count else "empty",
        "retrieval_mode": "local_hybrid_v1",
        "network_required": False,
        "workspace_isolated": True,
        "material_count": int(row["material_count"] or 0),
        "chunk_count": chunk_count,
        "indexed_characters": int(row["indexed_characters"] or 0),
    }


def _matching_conflicts(
    connection: sqlite3.Connection,
    workspace_id: str,
    query: str,
    *,
    plan_id: str | None = None,
) -> list[dict[str, Any]]:
    normalised_query = _normalise(query)
    plan_materials = set(_plan_material_ids(connection, workspace_id, plan_id))
    output = []
    for row in connection.execute(
        "SELECT * FROM active_knowledge_conflicts WHERE workspace_id=? AND status='unresolved'",
        (workspace_id,),
    ).fetchall():
        evidence_a = json.loads(row["evidence_a"])
        evidence_b = json.loads(row["evidence_b"])
        if plan_id and (
            str(evidence_a.get("material_id") or "") not in plan_materials
            or str(evidence_b.get("material_id") or "") not in plan_materials
        ):
            continue
        if _normalise(row["topic"]) in normalised_query or normalised_query in _normalise(row["topic"]):
            output.append(
                {
                    **dict(row),
                    "evidence_a": evidence_a,
                    "evidence_b": evidence_b,
                }
            )
    return output


@router.post("/sessions/{session_id}/advance")
async def advance_session(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    message = str(body.get("message") or "").strip()
    if not message:
        _error("TEACHING_MESSAGE_REQUIRED", "请输入学习反馈或回答", 422)
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        state = json.loads(session["state"])
        requested_provider_session_id = (
            str(body.get("provider_session_id") or "").strip() or None
        )
        bound_provider_session_id = state.get("provider_session_id")
        if (
            requested_provider_session_id
            and bound_provider_session_id
            and requested_provider_session_id != bound_provider_session_id
        ):
            _error(
                "TEACHING_PROVIDER_SESSION_MISMATCH",
                "该教学会话已绑定另一条 Provider 会话；不能在一次学习证据链中静默更换评分来源。",
                409,
            )
        if requested_provider_session_id and not bound_provider_session_id:
            state["provider_session_id"] = requested_provider_session_id
        tasks = _task_rows(connection, session["plan_id"])
        if not tasks:
            _error("ACTIVE_PLAN_EMPTY", "计划没有可执行任务", 409)
        route_index = min(int(state["route_index"]), len(tasks) - 1)
        task = tasks[route_index]
        point = connection.execute(
            "SELECT * FROM active_knowledge_points WHERE id=?", (task["knowledge_point_id"],)
        ).fetchone()
        stored_citations = json.loads(point["source_evidence"])
        citations = _merge_citations(
            _search(
                connection,
                session["workspace_id"],
                f"{point['name']} {point['explanation']} {point['acceptance_method']}",
                plan_id=session["plan_id"],
            ),
            stored_citations,
        )
        state["last_retrieval"] = {
            "mode": "local_hybrid_v1",
            "query_scope": "current_knowledge_point",
            "citation_count": len(citations),
        }
        active_remediation = (
            (state.get("remediation_stack") or [])[-1]
            if state.get("remediation_stack")
            else {}
        )
        timing_task_id = str(active_remediation.get("remediation_task_id") or task["id"])
        # Actual learning time must be an explicit active interval reported by
        # the client/activity tracker. Time since the last message may include
        # refreshes, sleep or an abandoned tab and must not masquerade as study.
        try:
            elapsed = max(0.0, min(60.0, float(body.get("actual_elapsed_minutes") or 0)))
        except (TypeError, ValueError):
            _error("INVALID_ACTUAL_ELAPSED_MINUTES", "实际学习分钟必须是非负数字", 422)
        if elapsed > 0:
            activity_event_id = str(body.get("activity_event_id") or "").strip()
            if not activity_event_id:
                _error(
                    "ACTIVITY_EVENT_ID_REQUIRED",
                    "记录实际学习分钟时必须提供 activity_event_id，以防请求重放导致重复计时。",
                    422,
                )
            inserted = connection.execute(
                """INSERT OR IGNORE INTO active_task_time_events
                   (id,event_id,task_id,session_id,elapsed_minutes,activity_kind,recorded_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    _id("time_event"),
                    activity_event_id,
                    timing_task_id,
                    session_id,
                    elapsed,
                    str(body.get("activity_kind") or "active_learning")[:80],
                    _now(),
                ),
            ).rowcount
            if inserted == 0:
                elapsed = 0.0
        state["actual_elapsed_minutes"] = round(float(state.get("actual_elapsed_minutes", 0)) + elapsed, 3)
        connection.execute(
            "UPDATE active_plan_tasks SET actual_elapsed_minutes=actual_elapsed_minutes+? WHERE id=?",
            (elapsed, timing_task_id),
        )
        _record_message(connection, session_id, "user", "feedback", message, [])
        teacher_memory = dict(state.get("teacher_memory") or {})
        recent_turns = list(teacher_memory.get("recent_turns") or [])
        if message:
            recent_turns.append({"role": "learner", "content": message[:600]})
        teacher_memory["recent_turns"] = recent_turns[-12:]
        teacher_memory["last_learner_message"] = message[:600]
        state["teacher_memory"] = teacher_memory

        lower = message.casefold()
        is_confused = any(marker in lower for marker in CONFUSION_MARKERS)
        is_understood = bool(body.get("understood")) or any(marker in lower for marker in UNDERSTOOD_MARKERS)
        is_remediation = bool(state.get("remediation_stack"))
        is_question_window = state.get("current_teaching_step") == "question_invitation"
        wants_practice = is_question_window and (
            bool(body.get("continue_to_practice"))
            or lower.strip() in {"继续", "下一步"}
            or any(
                marker in lower
                for marker in (
                    "开始练习",
                    "继续练习",
                    "没有问题",
                    "没问题",
                    "没有疑问",
                    "可以做题",
                    "可以继续",
                    "懂了",
                    "明白了",
                    "进入练习",
                )
            )
        )
        action = "continue"
        is_explicit_question = (
            (("?" in message or "？" in message) or (is_question_window and not wants_practice))
            and not is_understood
            and not is_remediation
            and (not is_confused or is_question_window)
        )
        question_hits = _search(
            connection, session["workspace_id"], message, plan_id=session["plan_id"]
        ) if is_explicit_question else []
        question_conflicts = (
            _matching_conflicts(
                connection,
                session["workspace_id"],
                message,
                plan_id=session["plan_id"],
            )
            if is_explicit_question
            else []
        )
        if question_conflicts:
            action = "source_conflict"
            citations = [
                question_conflicts[0]["evidence_a"],
                question_conflicts[0]["evidence_b"],
            ]
            content = (
                f"资料存在冲突：{question_conflicts[0]['summary']}"
                "我会保留两份原文证据，在冲突解决前不选择其中一项作为确定事实。"
                "\n\n你可以继续追问；确认清楚后说“开始练习”。"
            )
            state["current_teaching_step"] = "question_invitation"
            state["next_action"] = "answer_question_or_start_practice"
            state["autonomy"] = {
                "mode": "teacher_led_question_dialogue",
                "awaiting_learner": True,
                "reason": "follow_up_question_invited",
            }
        elif is_explicit_question and not question_hits:
            action = "insufficient_evidence"
            citations = []
            content = (
                "资料中无法确认。当前上传资料没有足够证据支持这个问题，我不会猜测或编造。"
                "你可以换一种问法、补充资料，或者继续追问我能从现有资料确认的部分；"
                "确认清楚后说“开始练习”。"
            )
            state["current_teaching_step"] = "question_invitation"
            state["next_action"] = "answer_question_or_start_practice"
            state["autonomy"] = {
                "mode": "teacher_led_question_dialogue",
                "awaiting_learner": True,
                "reason": "follow_up_question_invited",
            }
        elif is_explicit_question:
            action = "grounded_answer"
            citations = question_hits
            content = (
                f"你问得很好。先把它和当前知识点连起来：{point['explanation'][:240]}"
                f"\n\n资料中直接相关的依据是：{question_hits[0]['quote'][:320]}"
                "\n\n我只会把上面的原文当作已确认事实；如果你的问题要求的结论没有出现在证据里，"
                "我会把它标成待验证，并带你补资料、设计验证或换一种可由现有资料回答的问法。"
                "\n\n如果这还没有解决你卡住的那一步，请继续追问；"
                "你确认清楚后再说“开始练习”，我不会机械跳到下一步。"
            )
            state["current_teaching_step"] = "question_invitation"
            state["next_action"] = "answer_question_or_start_practice"
            state["autonomy"] = {
                "mode": "teacher_led_question_dialogue",
                "awaiting_learner": True,
                "reason": "follow_up_question_invited",
            }
        elif is_question_window and wants_practice:
            action = "guided_practice"
            state["current_teaching_step"] = "check_understanding"
            state["next_action"] = "evaluate_answer"
            state["autonomy"] = {
                "mode": "teacher_led_until_learner_input",
                "awaiting_learner": True,
                "reason": "learner_practice_required",
            }
            content = (
                f"好，现在进入“{point['name']}”的引导练习。"
                f"\n\n先按这个核心开始：{point['explanation'][:260]}"
                f"\n\n请你用自己的话说明思路，给出一个具体应用或关键步骤，"
                f"并完成验收条件：{point['acceptance_method']}。"
                "遇到任何一步不会，可以立刻提问，我会暂停验收并继续答疑。"
            )
        elif is_confused and not is_remediation:
            state["unresolved_confusions"].append(
                {"knowledge_point_id": point["id"], "message": message, "created_at": _now()}
            )
            remediation_task = _create_remediation_task(
                connection,
                session=session,
                state=state,
                task=task,
                point=point,
                trigger_reason="learner_confused",
            )
            state["remediation_stack"].append(
                {
                    "route_index": route_index,
                    "teaching_step": state["current_teaching_step"],
                    "remediation_task_id": remediation_task["id"],
                    "estimated_task_minutes": remediation_task[
                        "estimated_task_minutes"
                    ],
                    "origin_task_id": task["id"],
                    "trigger_reason": "learner_confused",
                }
            )
            state["current_teaching_step"] = "remediation_explain"
            state["next_action"] = "remediation_example"
            state["estimated_task_minutes"] = remediation_task[
                "estimated_task_minutes"
            ]
            action = "remediation_explain"
            content = (
                f"已暂停主路线并保存位置（第 {route_index + 1}/{len(tasks)} 项）。"
                f"\n\n分层补讲：先把“{point['name']}”缩小成三件事：它是什么、材料说明了哪些关键关系或步骤、"
                f"怎样用当前验收条件证明自己会了。当前验收条件是：{point['acceptance_method']}"
            )
        elif is_remediation and is_understood:
            saved = state["remediation_stack"].pop()
            remediation_task_id = str(saved.get("remediation_task_id") or "")
            if remediation_task_id:
                remediation_task = connection.execute(
                    "SELECT * FROM active_plan_tasks WHERE id=?",
                    (remediation_task_id,),
                ).fetchone()
                if remediation_task:
                    connection.execute(
                        """UPDATE active_plan_tasks
                           SET status='completed',
                               completed_task_minutes=estimated_task_minutes
                           WHERE id=?""",
                        (remediation_task_id,),
                    )
                    if remediation_task_id not in state["completed_tasks"]:
                        state["completed_tasks"].append(remediation_task_id)
                        state["completed_task_minutes"] = int(
                            state.get("completed_task_minutes", 0)
                        ) + int(remediation_task["estimated_task_minutes"])
                        state["today_remaining_task_minutes"] = max(
                            0,
                            int(state.get("today_remaining_task_minutes", 0))
                            - int(remediation_task["estimated_task_minutes"]),
                        )
            state["current_teaching_step"] = "check_understanding"
            state["next_action"] = "check_understanding"
            state["estimated_task_minutes"] = task["estimated_task_minutes"]
            state["unresolved_confusions"] = [
                item for item in state["unresolved_confusions"] if item["knowledge_point_id"] != point["id"]
            ]
            action = "resume_main_route"
            content = (
                f"补救教学完成，已恢复主路线原位置（第 {saved['route_index'] + 1}/{len(tasks)} 项）。"
                f"\n\n现在做一次最小理解检查：请不用照抄资料，用自己的话解释“{point['name']}”，"
                f"并说明你会如何满足这个验收条件：{point['acceptance_method']}"
            )
            plan_row = connection.execute(
                "SELECT * FROM active_plans WHERE id=?",
                (session["plan_id"],),
            ).fetchone()
            _refresh_plan_calendar(
                connection, session["plan_id"], json.loads(plan_row["receipt"])
            )
        elif is_remediation:
            state["current_teaching_step"] = "remediation_example"
            state["next_action"] = "remediation_recheck"
            action = "remediation_example"
            content = (
                f"更简单的例子：只保留资料中关于“{point['name']}”的最小事实——{point['explanation'][:240]}"
                f"\n\n请先举一个符合这个事实的具体场景，再说明它怎样满足验收条件：{point['acceptance_method']}"
                "。如果仍不理解，我会继续降低解释层级或标记需要额外资料。"
            )
        elif state["current_teaching_step"] == "explain":
            state["current_teaching_step"] = "question_invitation"
            state["next_action"] = "answer_question_or_start_practice"
            state["autonomy"] = {
                "mode": "teacher_led_question_dialogue",
                "awaiting_learner": True,
                "reason": "question_invited_before_practice",
            }
            action = "invite_questions"
            content = (
                f"“{point['name']}”先讲到这里，我不会马上让你做题。"
                "\n\n请告诉我哪一步还不清楚；你可以问为什么、要求换例子或追问边界条件。"
                "我会检索资料回答。确认没有疑问后说“开始练习”。"
            )
        elif state["current_teaching_step"] == "example":
            state["current_teaching_step"] = "check_understanding"
            state["next_action"] = "evaluate_answer"
            action = "check_understanding"
            content = (
                f"理解检查：请不用照抄资料，用自己的话解释“{point['name']}”，"
                f"给出一个具体应用或例子，并完成这个验收条件：{point['acceptance_method']}"
            )
        elif state["current_teaching_step"] == "check_understanding" and is_understood:
            learning_phase = str(task["learning_phase"] or "integrated_initial")
            phase_part_index = int(task["phase_part_index"] or 1)
            phase_part_count = int(task["phase_part_count"] or 1)
            completes_independent_phase = (
                learning_phase in {"integrated_initial", "independent_application"}
                and phase_part_index >= phase_part_count
            )
            score, passed, grading_checks, evaluation = await _evaluate_understanding(
                message=message,
                point=point,
                citations=citations,
                provider_session_id=requested_provider_session_id,
            )
            mastery_evidence_id = _id("mastery")
            connection.execute(
                """INSERT INTO active_mastery_evidence
                   (id,session_id,knowledge_point_id,evidence_type,score,passed,evidence,citations,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    mastery_evidence_id,
                    session_id,
                    point["id"],
                    "provider_grounded_rubric" if evaluation.get("mode") == "real" else "unverified_recall",
                    score,
                    1 if passed else 0,
                    json.dumps(
                        {
                            "learner_answer": message,
                            "task_id": task["id"],
                            "learning_phase": learning_phase,
                            "phase_part_index": phase_part_index,
                            "phase_part_count": phase_part_count,
                            "grading_checks": grading_checks,
                            "evaluation": evaluation,
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(citations, ensure_ascii=False),
                    _now(),
                ),
            )
            state["mastery_evidence"].append(
                {
                    "knowledge_point_id": point["id"],
                    "score": score,
                    "passed": passed,
                    "evidence": message,
                    "task_id": task["id"],
                    "learning_phase": learning_phase,
                    "grading_checks": grading_checks,
                    "evaluation": evaluation,
                }
            )
            if passed:
                if completes_independent_phase:
                    connection.execute(
                        "UPDATE active_knowledge_points SET mastery=MAX(mastery,?) WHERE id=?",
                        (score, point["id"]),
                    )
                connection.execute(
                    """UPDATE active_plan_tasks SET status='completed',
                       completed_task_minutes=estimated_task_minutes WHERE id=?""",
                    (task["id"],),
                )
                state["completed_tasks"].append(task["id"])
                state["completed_task_minutes"] = int(state.get("completed_task_minutes", 0)) + int(
                    task["estimated_task_minutes"]
                )
                state["today_remaining_task_minutes"] = max(
                    0,
                    int(state["today_remaining_task_minutes"]) - int(task["estimated_task_minutes"]),
                )
                if completes_independent_phase:
                    review_due = (date.today() + timedelta(days=1)).isoformat()
                    plan_receipt = connection.execute(
                        "SELECT receipt FROM active_plans WHERE id=?",
                        (session["plan_id"],),
                    ).fetchone()
                    if plan_receipt:
                        from .generic_knowledge import calendar_capacities

                        review_dates = [
                            day["date"]
                            for day in calendar_capacities(
                                json.loads(plan_receipt["receipt"])
                            )
                            if day.get("study_phase") == "review"
                            and day["capacity_minutes"] > 0
                        ]
                        if review_dates and review_due < review_dates[0]:
                            review_due = review_dates[0]
                    now = _now()
                    review_total_minutes = _review_total_minutes_for_point(
                        connection, point
                    )
                    first_review_minutes = _review_occurrence_minutes(
                        review_total_minutes, 1
                    )
                    connection.execute(
                        """INSERT INTO active_review_items
                           (id,session_id,workspace_id,plan_id,knowledge_point_id,source_task_id,
                            source_mastery_evidence_id,due_date,interval_step,interval_days,
                            review_total_minutes,estimated_task_minutes,status,
                            attempt_count,success_streak,last_score,last_passed,
                            last_attempted_at,created_at,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,NULL,NULL,NULL,?,?)
                           ON CONFLICT(session_id,knowledge_point_id) DO UPDATE SET
                             plan_id=excluded.plan_id,
                             source_task_id=excluded.source_task_id,
                             source_mastery_evidence_id=excluded.source_mastery_evidence_id,
                             due_date=excluded.due_date,
                             interval_step=0,
                             interval_days=1,
                             review_total_minutes=excluded.review_total_minutes,
                             estimated_task_minutes=excluded.estimated_task_minutes,
                             status='pending',
                             attempt_count=0,
                             success_streak=0,
                             last_score=NULL,
                             last_passed=NULL,
                             last_attempted_at=NULL,
                             updated_at=excluded.updated_at""",
                        (
                            _id("review"),
                            session_id,
                            session["workspace_id"],
                            session["plan_id"],
                            point["id"],
                            task["id"],
                            mastery_evidence_id,
                            review_due,
                            0,
                            1,
                            review_total_minutes,
                            first_review_minutes,
                            "pending",
                            now,
                            now,
                        ),
                    )
                    review_row = connection.execute(
                        """SELECT * FROM active_review_items
                           WHERE session_id=? AND knowledge_point_id=?""",
                        (session_id, point["id"]),
                    ).fetchone()
                    _materialize_review_occurrence(
                        connection,
                        session=session,
                        review=review_row,
                        point=point,
                        due_date=review_due,
                        cycle=1,
                        trigger_reason="independent_application_passed",
                    )
                    plan_row = connection.execute(
                        "SELECT * FROM active_plans WHERE id=?",
                        (session["plan_id"],),
                    ).fetchone()
                    _replan_pending_in_transaction(
                        connection,
                        session=session,
                        plan=plan_row,
                        receipt=json.loads(plan_row["receipt"]),
                        affected_from=date.today(),
                        trigger_name="review_enqueued",
                        reason=f"知识点“{point['name']}”独立通过，加入 +1 日复习。",
                        state=state,
                    )
                    _sync_pending_reviews(connection, session_id, state)
                if route_index + 1 < len(tasks):
                    route_index += 1
                    next_task = tasks[route_index]
                    next_point = connection.execute(
                        "SELECT * FROM active_knowledge_points WHERE id=?",
                        (next_task["knowledge_point_id"],),
                    ).fetchone()
                    state["route_index"] = route_index
                    state["active_task_id"] = next_task["id"]
                    state["current_knowledge_point_id"] = next_point["id"]
                    state["current_teaching_step"] = "explain"
                    state["next_action"] = "show_example"
                    state["estimated_task_minutes"] = next_task["estimated_task_minutes"]
                    action = "advance_route"
                    citations = json.loads(next_point["source_evidence"])
                    if completes_independent_phase:
                        progress_message = (
                            f"独立应用阶段通过（{score:.0%}），知识点掌握度已更新，"
                            f"并安排 {review_due} 的首次复习。"
                        )
                    else:
                        progress_message = (
                            f"“{learning_phase}”阶段通过（{score:.0%}）；"
                            "该阶段任务分钟已完成，但尚未把整个知识点标记为掌握。"
                        )
                    content = (
                        f"{progress_message}"
                        f"\n\n回到整体路线，下一项是“{next_point['name']}”：{next_point['explanation']}"
                    )
                else:
                    state["route_index"] = len(tasks)
                    state["current_teaching_step"] = "completed"
                    state["next_action"] = "review"
                    action = "complete_route"
                    content = "当前计划路线已完成；下一步进入基于掌握证据的间隔复习。"
            else:
                state["unresolved_confusions"].append(
                    {
                        "knowledge_point_id": point["id"],
                        "message": message,
                        "created_at": _now(),
                        "reason": "failed_assessment",
                    }
                )
                remediation_task = _create_remediation_task(
                    connection,
                    session=session,
                    state=state,
                    task=task,
                    point=point,
                    trigger_reason="failed_assessment",
                )
                state["current_teaching_step"] = "remediation_explain"
                state["remediation_stack"].append(
                    {
                        "route_index": route_index,
                        "teaching_step": "check_understanding",
                        "remediation_task_id": remediation_task["id"],
                        "estimated_task_minutes": remediation_task[
                            "estimated_task_minutes"
                        ],
                        "origin_task_id": task["id"],
                        "trigger_reason": "failed_assessment",
                    }
                )
                state["estimated_task_minutes"] = remediation_task[
                    "estimated_task_minutes"
                ]
                state["next_action"] = "remediation_example"
                action = "remediation_explain"
                if evaluation.get("mode") == "unverified_without_real_provider":
                    content = (
                        "这次回答已保存，但当前没有真实 Provider 或领域验证器，不能把自报理解当作掌握通过。"
                        "主路线已暂停；连接真实 Provider 后可按资料与验收条件评分，或继续补讲。"
                    )
                else:
                    content = (
                        "本次理解检查未达到系统验收阈值，主路线已暂停；"
                        f"评估说明：{evaluation.get('rationale') or '需要补足验收条件中的可观察证据。'}"
                    )
        else:
            hits = _search(
                connection, session["workspace_id"], message, plan_id=session["plan_id"]
            )
            if not hits:
                action = "insufficient_evidence"
                citations = []
                content = "资料中无法确认。当前上传资料没有足够证据支持这个问题，我不会猜测或编造。"
            else:
                citations = hits
                action = "grounded_answer"
                content = f"根据上传资料，可确认的相关片段如下：{hits[0]['quote'][:240]}"

        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action=action,
            citations=citations,
            provider_session_id=requested_provider_session_id,
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        teacher_memory = dict(state.get("teacher_memory") or {})
        teacher_memory["last_interaction"] = action
        if is_explicit_question and message:
            questions = list(teacher_memory.get("learner_questions") or [])
            questions.append(message[:400])
            teacher_memory["learner_questions"] = questions[-20:]
        if is_confused:
            unresolved = list(teacher_memory.get("unresolved_points") or [])
            unresolved.append(str(point["name"]))
            teacher_memory["unresolved_points"] = list(dict.fromkeys(unresolved))[-20:]
        teacher_memory["recent_turns"] = (
            list(teacher_memory.get("recent_turns") or [])
            + [{"role": "teacher", "action": action, "content": content[:600]}]
        )[-12:]
        state["teacher_memory"] = teacher_memory
        _record_message(connection, session_id, "teacher", action, content, citations)
        connection.execute(
            """UPDATE active_teaching_sessions
               SET state=?,updated_at=?,actual_elapsed_minutes=actual_elapsed_minutes+? WHERE id=?""",
            (json.dumps(state, ensure_ascii=False), _now(), elapsed, session_id),
        )
        connection.commit()
        return _public_session(connection, session_id)


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    with _connect() as connection:
        return _public_session(connection, session_id)


@router.post("/sessions/{session_id}/focus")
def focus_session_task(
    session_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Move an existing plan-scoped teacher to a selected calendar task.

    The session, memory and retrieval scope stay bound to the same plan.  A
    task from another plan is therefore rejected instead of silently changing
    the teacher's knowledge base.
    """
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        _error("ACTIVE_TASK_REQUIRED", "请选择要进入的日历任务", 422)
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        tasks = _task_rows(connection, session["plan_id"])
        route_index = next(
            (index for index, task in enumerate(tasks) if task["id"] == task_id),
            None,
        )
        if route_index is None:
            _error(
                "ACTIVE_TASK_OUTSIDE_SESSION_PLAN",
                "所选任务不属于当前教师会话的计划，不能混用另一任务的知识库或记忆。",
                409,
            )
        task = tasks[route_index]
        state = json.loads(session["state"])
        state["route_index"] = route_index
        state["route_length"] = len(tasks)
        state["current_knowledge_point_id"] = task["knowledge_point_id"]
        state["current_teaching_step"] = "daily_briefing"
        state["next_action"] = "explain_current_point"
        state["estimated_task_minutes"] = task["estimated_task_minutes"]
        state["active_task_id"] = task["id"]
        state["today_remaining_task_minutes"] = int(
            connection.execute(
                """SELECT COALESCE(SUM(estimated_task_minutes-completed_task_minutes),0)
                   FROM active_plan_tasks
                   WHERE plan_id=? AND day_date=? AND status<>'completed'""",
                (session["plan_id"], task["day_date"]),
            ).fetchone()[0]
        )
        state["today_plan"] = _daily_route_summary(
            connection, session["plan_id"], task["day_date"]
        )
        state["autonomy"] = {
            "mode": "teacher_led_until_learner_input",
            "awaiting_learner": False,
            "reason": "calendar_task_selected",
        }
        teacher_memory = dict(state.get("teacher_memory") or {})
        teacher_memory["last_interaction"] = "calendar_task_selected"
        state["teacher_memory"] = teacher_memory
        connection.execute(
            """UPDATE active_teaching_sessions
               SET state=?,updated_at=? WHERE id=?""",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
        connection.commit()
        return _public_session(connection, session_id)


def _review_as_of(value: Any) -> date:
    try:
        return date.fromisoformat(str(value or date.today().isoformat()))
    except ValueError:
        _error("INVALID_REVIEW_DATE", "复习日期必须是有效 ISO 日期", 422)


@router.get("/sessions/{session_id}/reviews")
def list_session_reviews(
    session_id: str, status: str = "", as_of: str = ""
) -> dict[str, Any]:
    with _connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone():
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        items = _review_items(connection, session_id)
        if status:
            allowed = {"pending", "due", "in_progress", "completed"}
            if status not in allowed:
                _error("INVALID_REVIEW_STATUS", "复习状态无效", 422)
            if status == "due":
                day = _review_as_of(as_of)
                items = [
                    item
                    for item in items
                    if item["status"] in {"pending", "in_progress"}
                    and date.fromisoformat(item["due_date"]) <= day
                ]
            else:
                items = [item for item in items if item["status"] == status]
        return {"session_id": session_id, "items": items}


@router.post("/sessions/{session_id}/reviews/{review_id}/start")
async def start_review(
    session_id: str,
    review_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    as_of = _review_as_of(body.get("as_of"))
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        incomplete_initial = connection.execute(
            """SELECT COUNT(*) FROM active_plan_tasks
               WHERE plan_id=? AND task_type='initial_learning'
                 AND status<>'completed'""",
            (session["plan_id"],),
        ).fetchone()[0]
        if incomplete_initial:
            incomplete_initial_points = connection.execute(
                """SELECT COUNT(DISTINCT knowledge_point_id)
                   FROM active_plan_tasks
                   WHERE plan_id=? AND task_type='initial_learning'
                     AND status<>'completed'""",
                (session["plan_id"],),
            ).fetchone()[0]
            _error(
                "INITIAL_LEARNING_NOT_COMPLETE",
                "正式复习只能在第一遍全部完成后开始；当天首学后的巩固不计入正式复习阶段。",
                409,
                incomplete_initial_task_count=int(incomplete_initial),
                incomplete_initial_knowledge_point_count=int(
                    incomplete_initial_points
                ),
            )
        review = connection.execute(
            """SELECT review.*,point.name AS knowledge_point_name,
                      point.acceptance_method,point.source_evidence
               FROM active_review_items AS review
               JOIN active_knowledge_points AS point
                 ON point.id=review.knowledge_point_id
               WHERE review.id=? AND review.session_id=?""",
            (review_id, session_id),
        ).fetchone()
        if not review:
            _error("REVIEW_NOT_FOUND", "复习任务不存在", 404)
        if review["status"] == "completed":
            _error("REVIEW_ALREADY_COMPLETED", "该间隔复习链已经完成", 409)
        if date.fromisoformat(review["due_date"]) > as_of:
            _error(
                "REVIEW_NOT_DUE",
                "该复习尚未到期，未学过或未到期内容不会提前进入今日复习。",
                409,
                due_date=review["due_date"],
            )
        scheduled_task_id = str(review["scheduled_task_id"] or "")
        scheduled_task = (
            connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=? AND linked_review_item_id=?",
                (scheduled_task_id, review_id),
            ).fetchone()
            if scheduled_task_id
            else None
        )
        if not scheduled_task:
            _error(
                "REVIEW_NOT_SCHEDULED",
                "复习队列存在，但尚未生成可执行的日历任务。",
                409,
            )
        if scheduled_task["status"] == "unscheduled":
            _error(
                "REVIEW_OUTSIDE_CAPACITY",
                "该复习尚未能排入截止日期前的可用容量。",
                409,
                due_date=review["due_date"],
            )
        scheduled_for = date.fromisoformat(
            str(review["scheduled_date"] or scheduled_task["day_date"])
        )
        if scheduled_for > as_of:
            _error(
                "REVIEW_NOT_SCHEDULED_TODAY",
                "该复习已到期，但根据日历容量安排在稍后的学习日。",
                409,
                scheduled_date=scheduled_for.isoformat(),
            )
        state = json.loads(session["state"])
        active_review = state.get("active_review")
        if active_review and active_review.get("id") != review_id:
            _error("OTHER_REVIEW_IN_PROGRESS", "请先完成当前复习任务", 409)
        provider_session_id = str(body.get("provider_session_id") or "").strip() or None
        bound_provider_session_id = state.get("provider_session_id")
        if provider_session_id and bound_provider_session_id and provider_session_id != bound_provider_session_id:
            _error(
                "TEACHING_PROVIDER_SESSION_MISMATCH",
                "该教学会话已绑定另一条 Provider 会话。",
                409,
            )
        if provider_session_id and not bound_provider_session_id:
            state["provider_session_id"] = provider_session_id
        state.setdefault(
            "review_return",
            {
                "route_index": state.get("route_index"),
                "current_knowledge_point_id": state.get("current_knowledge_point_id"),
                "current_teaching_step": state.get("current_teaching_step"),
                "next_action": state.get("next_action"),
                "estimated_task_minutes": state.get("estimated_task_minutes"),
            },
        )
        state["active_review"] = {
            "id": review_id,
            "plan_task_id": scheduled_task_id,
            "knowledge_point_id": review["knowledge_point_id"],
            "due_date": review["due_date"],
            "interval_days": review["interval_days"],
            "interval_step": review["interval_step"],
        }
        state["current_knowledge_point_id"] = review["knowledge_point_id"]
        state["current_teaching_step"] = "review_recall"
        state["next_action"] = "review_check"
        state["estimated_task_minutes"] = review["estimated_task_minutes"]
        connection.execute(
            "UPDATE active_review_items SET status='in_progress',updated_at=? WHERE id=?",
            (_now(), review_id),
        )
        connection.execute(
            "UPDATE active_plan_tasks SET status='in_progress' WHERE id=?",
            (scheduled_task_id,),
        )
        _sync_pending_reviews(connection, session_id, state)
        citations = json.loads(review["source_evidence"])
        content = (
            f"到期复习：“{review['knowledge_point_name']}”。请先不看答案，用自己的话回忆核心关系或步骤，"
            f"再完成这项验收：{review['acceptance_method']}。"
            f"本轮预计 {review['estimated_task_minutes']} 个标准任务分钟。"
        )
        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action="review_recall",
            citations=citations,
            provider_session_id=provider_session_id,
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        _record_message(connection, session_id, "teacher", "review_recall", content, citations)
        connection.execute(
            "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
        connection.commit()
        return {
            "review": next(
                item for item in _review_items(connection, session_id) if item["id"] == review_id
            ),
            "session": _public_session(connection, session_id),
        }


@router.post("/sessions/{session_id}/reviews/{review_id}/submit")
async def submit_review(
    session_id: str, review_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    message = str(body.get("message") or "").strip()
    if not message:
        _error("REVIEW_ANSWER_REQUIRED", "请输入复习回答", 422)
    assistance_level = str(body.get("assistance_level") or "none").strip()
    if assistance_level not in {"none", "hinted", "guided"}:
        _error("INVALID_ASSISTANCE_LEVEL", "assistance_level 必须是 none、hinted 或 guided", 422)
    reviewed_on = _review_as_of(body.get("as_of"))
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        state = json.loads(session["state"])
        if (state.get("active_review") or {}).get("id") != review_id:
            _error("REVIEW_NOT_STARTED", "请先启动该复习任务", 409)
        review = connection.execute(
            "SELECT * FROM active_review_items WHERE id=? AND session_id=?",
            (review_id, session_id),
        ).fetchone()
        if not review:
            _error("REVIEW_NOT_FOUND", "复习任务不存在", 404)
        review_task_id = str(review["scheduled_task_id"] or "")
        review_task = (
            connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=? AND linked_review_item_id=?",
                (review_task_id, review_id),
            ).fetchone()
            if review_task_id
            else None
        )
        if not review_task:
            _error("REVIEW_NOT_SCHEDULED", "当前复习没有对应的计划任务。", 409)
        if str(review["scheduled_date"] or review_task["day_date"]) > reviewed_on.isoformat():
            _error(
                "REVIEW_NOT_SCHEDULED_TODAY",
                "当前复习轮次尚未到排定日期。",
                409,
                scheduled_date=review["scheduled_date"] or review_task["day_date"],
            )
        point = connection.execute(
            "SELECT * FROM active_knowledge_points WHERE id=?",
            (review["knowledge_point_id"],),
        ).fetchone()
        provider_session_id = str(body.get("provider_session_id") or "").strip() or None
        bound_provider_session_id = state.get("provider_session_id")
        if provider_session_id and bound_provider_session_id and provider_session_id != bound_provider_session_id:
            _error(
                "TEACHING_PROVIDER_SESSION_MISMATCH",
                "该教学会话已绑定另一条 Provider 会话。",
                409,
            )
        if bound_provider_session_id and not provider_session_id:
            provider_session_id = bound_provider_session_id
        try:
            elapsed = max(0.0, min(60.0, float(body.get("actual_elapsed_minutes") or 0)))
        except (TypeError, ValueError):
            _error("INVALID_ACTUAL_ELAPSED_MINUTES", "实际学习分钟必须是非负数字", 422)
        if elapsed > 0:
            activity_event_id = str(body.get("activity_event_id") or "").strip()
            if not activity_event_id:
                _error(
                    "ACTIVITY_EVENT_ID_REQUIRED",
                    "记录实际学习分钟时必须提供 activity_event_id。",
                    422,
                )
            inserted = connection.execute(
                """INSERT OR IGNORE INTO active_task_time_events
                   (id,event_id,task_id,session_id,elapsed_minutes,activity_kind,recorded_at)
                   VALUES (?,?,?,?,?,'review',?)""",
                (
                    _id("time_event"),
                    activity_event_id,
                    review_task_id,
                    session_id,
                    elapsed,
                    _now(),
                ),
            ).rowcount
            if inserted == 0:
                elapsed = 0.0
        citations = json.loads(point["source_evidence"])
        score, independently_passed, checks, evaluation = await _evaluate_understanding(
            message=message,
            point=point,
            citations=citations,
            provider_session_id=provider_session_id,
        )
        passed = independently_passed and assistance_level == "none"
        evidence_id = _id("mastery")
        attempt_no = int(review["attempt_count"]) + 1
        evidence_payload = {
            "learner_answer": message,
            "review_item_id": review_id,
            "attempt_no": attempt_no,
            "assistance_level": assistance_level,
            "grading_checks": checks,
            "evaluation": evaluation,
        }
        connection.execute(
            """INSERT INTO active_mastery_evidence
               (id,session_id,knowledge_point_id,evidence_type,score,passed,evidence,citations,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                evidence_id,
                session_id,
                point["id"],
                "delayed_independent_review"
                if evaluation.get("mode") == "real"
                else "unverified_delayed_review",
                score,
                int(passed),
                json.dumps(evidence_payload, ensure_ascii=False),
                json.dumps(citations, ensure_ascii=False),
                _now(),
            ),
        )
        previous_step = int(review["interval_step"])
        success_streak = int(review["success_streak"])
        if passed:
            next_step = previous_step + 1
            success_streak += 1
            if next_step >= 3:
                next_interval = None
                next_due = None
                next_status = "completed"
            else:
                next_interval = (3, 7)[next_step - 1]
                next_due = (reviewed_on + timedelta(days=next_interval)).isoformat()
                next_status = "pending"
            completed_minutes = int(review["estimated_task_minutes"])
            connection.execute(
                "UPDATE active_knowledge_points SET mastery=MAX(mastery,?) WHERE id=?",
                (score, point["id"]),
            )
        else:
            next_step = previous_step
            success_streak = 0
            next_interval = 1
            next_due = (reviewed_on + timedelta(days=1)).isoformat()
            next_status = "pending"
            completed_minutes = 0
        connection.execute(
            """INSERT INTO active_review_attempts
               (id,review_item_id,session_id,plan_task_id,mastery_evidence_id,attempt_no,score,passed,
                assistance_level,evaluator_kind,estimated_task_minutes,completed_task_minutes,
                actual_elapsed_minutes,outcome,reviewed_on,next_interval_days,next_due_date,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _id("review_attempt"),
                review_id,
                session_id,
                review_task_id,
                evidence_id,
                attempt_no,
                score,
                int(passed),
                assistance_level,
                str(evaluation.get("mode") or "unknown"),
                review["estimated_task_minutes"],
                completed_minutes,
                elapsed,
                "passed" if passed else "failed",
                reviewed_on.isoformat(),
                next_interval,
                next_due,
                _now(),
            ),
        )
        connection.execute(
            """UPDATE active_plan_tasks
               SET status=?,completed_task_minutes=?,
                   actual_elapsed_minutes=actual_elapsed_minutes+?
               WHERE id=?""",
            (
                "completed" if passed else "attempted_failed",
                completed_minutes,
                elapsed,
                review_task_id,
            ),
        )
        connection.execute(
            """UPDATE active_review_items
               SET due_date=?,interval_step=?,interval_days=?,status=?,attempt_count=?,
                   success_streak=?,last_score=?,last_passed=?,last_attempted_at=?,updated_at=?
               WHERE id=?""",
            (
                next_due or review["due_date"],
                next_step,
                next_interval or int(review["interval_days"]),
                next_status,
                attempt_no,
                success_streak,
                score,
                int(passed),
                _now(),
                _now(),
                review_id,
            ),
        )
        if next_status == "pending":
            updated_review = connection.execute(
                "SELECT * FROM active_review_items WHERE id=?",
                (review_id,),
            ).fetchone()
            next_cycle = next_step + 1
            next_review_task_id = _materialize_review_occurrence(
                connection,
                session=session,
                review=updated_review,
                point=point,
                due_date=str(next_due),
                cycle=next_cycle,
                trigger_reason="review_passed" if passed else "review_retry",
            )
            plan_row = connection.execute(
                "SELECT * FROM active_plans WHERE id=?",
                (session["plan_id"],),
            ).fetchone()
            _replan_pending_in_transaction(
                connection,
                session=session,
                plan=plan_row,
                receipt=json.loads(plan_row["receipt"]),
                affected_from=reviewed_on,
                trigger_name="review_rescheduled" if passed else "review_retry",
                reason=(
                    f"知识点“{point['name']}”进入下一次延迟复测。"
                    if passed
                    else f"知识点“{point['name']}”未独立通过，增加次日重试。"
                ),
                state=state,
            )
            if not passed:
                state.setdefault("active_review", {})["plan_task_id"] = next_review_task_id
        else:
            connection.execute(
                """UPDATE active_review_items
                   SET scheduled_task_id='',scheduled_date=NULL,updated_at=?
                   WHERE id=?""",
                (_now(), review_id),
            )
            plan_row = connection.execute(
                "SELECT * FROM active_plans WHERE id=?",
                (session["plan_id"],),
            ).fetchone()
            _refresh_plan_calendar(
                connection, session["plan_id"], json.loads(plan_row["receipt"])
            )
        state["actual_elapsed_minutes"] = round(
            float(state.get("actual_elapsed_minutes", 0)) + elapsed, 3
        )
        state["mastery_evidence"].append(
            {
                "knowledge_point_id": point["id"],
                "score": score,
                "passed": passed,
                "evidence": message,
                "review_item_id": review_id,
                "assistance_level": assistance_level,
                "evaluation": evaluation,
            }
        )
        if passed:
            restored = state.pop("review_return", {})
            state.pop("active_review", None)
            for key, value in restored.items():
                state[key] = value
            action = "review_completed" if next_status == "completed" else "review_rescheduled"
            if next_status == "completed":
                content = (
                    f"延迟独立复测通过（{score:.0%}），1/3/7 复习链完成。"
                    "已恢复原学习路线。"
                )
            else:
                content = (
                    f"延迟独立复测通过（{score:.0%}），下一次复习安排在 {next_due}。"
                    "已恢复原学习路线。"
                )
        else:
            state["current_teaching_step"] = "review_remediation"
            state["next_action"] = "review_retry"
            action = "review_remediation"
            if assistance_level != "none":
                content = (
                    "本轮使用了提示或引导，因此不能记为独立掌握；"
                    f"已把下一次独立复测提前到 {next_due}。"
                )
            else:
                content = (
                    "本轮复习未达到独立验收门槛，掌握度没有提高；"
                    f"已把下一次复习提前到 {next_due}，并保留当前复习位置用于补救。"
                )
        _sync_pending_reviews(connection, session_id, state)
        _record_message(connection, session_id, "user", "review_answer", message, [])
        content, teacher_generation = await _render_teacher_content(
            draft=content,
            action=action,
            citations=citations,
            provider_session_id=provider_session_id,
            state=state,
        )
        state["teacher_generation"] = teacher_generation
        _record_message(connection, session_id, "teacher", action, content, citations)
        connection.execute(
            """UPDATE active_teaching_sessions
               SET state=?,updated_at=?,actual_elapsed_minutes=actual_elapsed_minutes+?
               WHERE id=?""",
            (json.dumps(state, ensure_ascii=False), _now(), elapsed, session_id),
        )
        connection.commit()
        return {
            "passed": passed,
            "review": next(
                item for item in _review_items(connection, session_id) if item["id"] == review_id
            ),
            "attempt": dict(
                connection.execute(
                    "SELECT * FROM active_review_attempts WHERE mastery_evidence_id=?",
                    (evidence_id,),
                ).fetchone()
            ),
            "session": _public_session(connection, session_id),
        }


def _remaining_projected_review_minutes(
    connection: sqlite3.Connection, plan: sqlite3.Row
) -> int:
    initial_minutes = int(
        connection.execute(
            """SELECT COALESCE(SUM(estimated_task_minutes),0)
               FROM active_plan_tasks
               WHERE plan_id=? AND task_type='initial_learning'""",
            (plan["id"],),
        ).fetchone()[0]
    )
    baseline = max(0, int(plan["total_estimated_task_minutes"]) - initial_minutes)
    materialized_baseline = 0
    rows = connection.execute(
        """SELECT review.id,review.review_total_minutes,
                  COALESCE(MAX(task.review_cycle),0) AS max_cycle
           FROM active_review_items AS review
           LEFT JOIN active_plan_tasks AS task
             ON task.linked_review_item_id=review.id
           WHERE review.plan_id=?
           GROUP BY review.id,review.review_total_minutes""",
        (plan["id"],),
    ).fetchall()
    for row in rows:
        total = int(row["review_total_minutes"] or 0)
        for cycle in range(1, min(3, int(row["max_cycle"] or 0)) + 1):
            materialized_baseline += _review_occurrence_minutes(total, cycle)
    return max(0, baseline - materialized_baseline)


def _replan_pending_in_transaction(
    connection: sqlite3.Connection,
    *,
    session: sqlite3.Row,
    plan: sqlite3.Row,
    receipt: dict[str, Any],
    affected_from: date,
    trigger_name: str,
    reason: str,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Authoritative shared replan used by manual, review and remediation flows."""
    from .generic_knowledge import calendar_capacities

    before_rows = connection.execute(
        """SELECT * FROM active_plan_tasks WHERE plan_id=?
           ORDER BY day_index,task_order,id""",
        (plan["id"],),
    ).fetchall()
    before_snapshot = [_task_snapshot(row) for row in before_rows]
    moved, directly_unscheduled, calendar = _reschedule_plan_work(
        connection,
        plan_id=plan["id"],
        receipt=receipt,
        affected_from=affected_from,
    )
    after_rows = connection.execute(
        """SELECT * FROM active_plan_tasks WHERE plan_id=?
           ORDER BY day_index,task_order,id""",
        (plan["id"],),
    ).fetchall()
    after_snapshot = [_task_snapshot(row) for row in after_rows]
    eligible_dates = {
        item["date"]: int(item["capacity_minutes"])
        for item in calendar_capacities(receipt)
        if item["date"] >= affected_from.isoformat()
    }
    fixed_minutes = sum(
        int(row["estimated_task_minutes"])
        for row in after_rows
        if row["day_date"] in eligible_dates
        and row["status"] not in {"planned", "unscheduled", "cancelled"}
    )
    pending_minutes = sum(
        int(row["estimated_task_minutes"])
        for row in after_rows
        if row["status"] in {"planned", "unscheduled"}
    )
    future_review_minutes = _remaining_projected_review_minutes(connection, plan)
    available_for_pending = max(0, sum(eligible_dates.values()) - fixed_minutes)
    quantity_shortfall = max(
        0, pending_minutes + future_review_minutes - available_for_pending
    )
    unscheduled = max(directly_unscheduled, quantity_shortfall)
    warning = (
        f"重排后仍有 {unscheduled} 个标准任务分钟无法在截止日期前容纳。"
        if unscheduled
        else ""
    )
    connection.execute(
        """UPDATE active_plans
           SET receipt=?,feasible=?,unscheduled_task_minutes=?,warning=?
           WHERE id=?""",
        (
            json.dumps(receipt, ensure_ascii=False),
            int(unscheduled == 0),
            unscheduled,
            warning,
            plan["id"],
        ),
    )
    history_id = _id("replan")
    before_json = json.dumps(before_snapshot, ensure_ascii=False)
    after_json = json.dumps(after_snapshot, ensure_ascii=False)
    connection.execute(
        """INSERT INTO active_replan_history
           (id,plan_id,session_id,workspace_id,trigger,trigger_name,reason,affected_from,
            before_state,after_state,before_snapshot,after_snapshot,moved_task_ids,
            unscheduled_minutes,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            history_id,
            plan["id"],
            session["id"],
            session["workspace_id"],
            trigger_name,
            trigger_name,
            reason[:1000],
            affected_from.isoformat(),
            before_json,
            after_json,
            before_json,
            after_json,
            json.dumps(moved, ensure_ascii=False),
            unscheduled,
            _now(),
        ),
    )
    history = {
        "id": history_id,
        "trigger": trigger_name,
        "reason": reason,
        "affected_from": affected_from.isoformat(),
        "moved_task_ids": moved,
        "unscheduled_task_minutes": unscheduled,
    }
    if state is not None:
        state.setdefault("replan_history", []).append(history)
        state["today_remaining_task_minutes"] = next(
            (
                max(
                    0,
                    int(item["capacity_minutes"])
                    - sum(
                        int(task["estimated_task_minutes"])
                        for task in item["tasks"]
                        if task["status"] == "completed"
                    ),
                )
                for item in calendar
                if item["date"] == affected_from.isoformat()
            ),
            0,
        )
    return {
        "history": history,
        "moved_task_ids": moved,
        "unscheduled_task_minutes": unscheduled,
        "calendar": calendar,
        "remaining_projected_review_minutes": future_review_minutes,
    }


@router.post("/sessions/{session_id}/replan")
def replan_session(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Roll unfinished standard-task work forward without changing elapsed time."""
    with _connect() as connection:
        session = connection.execute(
            "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        plan = connection.execute(
            "SELECT * FROM active_plans WHERE id=?", (session["plan_id"],)
        ).fetchone()
        if not plan:
            _error("ACTIVE_PLAN_NOT_FOUND", "主动教学计划不存在", 404)
        receipt = json.loads(plan["receipt"])
        try:
            affected_from = date.fromisoformat(
                str(body.get("affected_from") or date.today().isoformat())
            )
            deadline = date.fromisoformat(receipt["deadline"])
        except (TypeError, ValueError):
            _error("INVALID_REPLAN_DATE", "重排日期必须是有效 ISO 日期", 422)
        if affected_from > deadline:
            _error("REPLAN_AFTER_DEADLINE", "重排起点晚于截止日期", 422)

        added_unavailable = {
            str(item).strip()
            for item in body.get("additional_unavailable_dates") or []
            if str(item).strip()
        }
        for item in added_unavailable:
            try:
                date.fromisoformat(item)
            except ValueError:
                _error("INVALID_UNAVAILABLE_DATE", f"无效不可学习日期：{item}", 422)
        receipt["unavailable_dates"] = sorted(
            set(receipt.get("unavailable_dates") or []) | added_unavailable
        )
        if connection.execute(
            "SELECT 1 FROM active_receipts WHERE workspace_id=?",
            (session["workspace_id"],),
        ).fetchone():
            connection.execute(
                "UPDATE active_receipts SET payload=?,updated_at=? WHERE workspace_id=?",
                (json.dumps(receipt, ensure_ascii=False), _now(), session["workspace_id"]),
            )
        trigger_name = str(body.get("trigger") or "manual")[:80]
        state = json.loads(session["state"])
        outcome = _replan_pending_in_transaction(
            connection,
            session=session,
            plan=plan,
            receipt=receipt,
            affected_from=affected_from,
            trigger_name=trigger_name,
            reason=str(body.get("reason") or "滚动未完成任务"),
            state=state,
        )
        connection.execute(
            "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
        connection.commit()
        return {
            "history_id": outcome["history"]["id"],
            "affected_from": affected_from.isoformat(),
            "moved_task_ids": outcome["moved_task_ids"],
            "unscheduled_task_minutes": outcome["unscheduled_task_minutes"],
            "estimated_task_minutes_changed": False,
            "actual_elapsed_minutes_changed": False,
            "plan": _plan(connection, plan["id"]),
            "session": _public_session(connection, session_id),
        }


@router.get("/sessions/{session_id}/replan-history")
def replan_history(session_id: str) -> dict[str, Any]:
    with _connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM active_teaching_sessions WHERE id=?", (session_id,)
        ).fetchone():
            _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
        rows = connection.execute(
            """SELECT * FROM active_replan_history
               WHERE session_id=? ORDER BY created_at,id""",
            (session_id,),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            for key in ("before_snapshot", "after_snapshot", "moved_task_ids"):
                item[key] = json.loads(item[key])
            items.append(item)
        return {"session_id": session_id, "items": items}


def _completion_report(
    connection: sqlite3.Connection, session_id: str
) -> dict[str, Any]:
    session = connection.execute(
        "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not session:
        _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
    plan = connection.execute(
        "SELECT * FROM active_plans WHERE id=?", (session["plan_id"],)
    ).fetchone()
    state = json.loads(session["state"])
    tasks = connection.execute(
        """SELECT * FROM active_plan_tasks WHERE plan_id=?
           ORDER BY day_index,task_order,id""",
        (session["plan_id"],),
    ).fetchall()
    by_type: dict[str, dict[str, Any]] = {}
    for task in tasks:
        bucket = by_type.setdefault(
            task["task_type"],
            {
                "task_count": 0,
                "completed_count": 0,
                "planned_task_minutes": 0,
                "completed_task_minutes": 0,
                "actual_elapsed_minutes": 0.0,
            },
        )
        bucket["task_count"] += 1
        bucket["completed_count"] += int(task["status"] == "completed")
        bucket["planned_task_minutes"] += int(task["estimated_task_minutes"])
        bucket["completed_task_minutes"] += int(task["completed_task_minutes"])
        bucket["actual_elapsed_minutes"] = round(
            float(bucket["actual_elapsed_minutes"])
            + float(task["actual_elapsed_minutes"]),
            3,
        )
    planned_minutes = sum(int(task["estimated_task_minutes"]) for task in tasks)
    completed_minutes = sum(int(task["completed_task_minutes"]) for task in tasks)
    actual_minutes = round(
        sum(float(task["actual_elapsed_minutes"]) for task in tasks), 3
    )
    initial = [task for task in tasks if task["task_type"] == "initial_learning"]
    review_rows = connection.execute(
        "SELECT * FROM active_review_items WHERE session_id=? ORDER BY due_date,id",
        (session_id,),
    ).fetchall()
    evidence_rows = connection.execute(
        """SELECT evidence_type,score,passed,knowledge_point_id,created_at
           FROM active_mastery_evidence WHERE session_id=? ORDER BY created_at,id""",
        (session_id,),
    ).fetchall()
    independent_evidence = [
        row
        for row in evidence_rows
        if row["passed"]
        and row["evidence_type"]
        in {"provider_grounded_rubric", "delayed_independent_review", "code_hidden_tests"}
    ]
    conflicts = connection.execute(
        """SELECT COUNT(*) FROM active_knowledge_conflicts
           WHERE workspace_id=? AND status='unresolved'""",
        (session["workspace_id"],),
    ).fetchone()[0]
    mistakes: list[dict[str, Any]] = []
    code_summary = {
        "problem_count": 0,
        "submission_count": 0,
        "independently_verified_problem_count": 0,
    }
    if connection.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='active_practice_code_submissions'"""
    ).fetchone():
        problems = connection.execute(
            """SELECT id,title FROM active_practice_problem_specs
               WHERE workspace_id=? ORDER BY created_at,id""",
            (session["workspace_id"],),
        ).fetchall()
        code_summary["problem_count"] = len(problems)
        for problem in problems:
            submissions = connection.execute(
                """SELECT * FROM active_practice_code_submissions
                   WHERE session_id=? AND problem_id=? ORDER BY created_at,id""",
                (session_id, problem["id"]),
            ).fetchall()
            code_summary["submission_count"] += len(submissions)
            independent_accept = next(
                (
                    row
                    for row in reversed(submissions)
                    if row["mode"] == "submit"
                    and row["status"] == "accepted"
                    and int(row["hint_level"]) == 0
                ),
                None,
            )
            if independent_accept:
                code_summary["independently_verified_problem_count"] += 1
            for row in submissions:
                if row["status"] == "accepted":
                    continue
                mistakes.append(
                    {
                        "submission_id": row["id"],
                        "problem_id": problem["id"],
                        "problem_title": problem["title"],
                        "status": row["status"],
                        "passed_count": row["passed_count"],
                        "total_count": row["total_count"],
                        "hint_level": row["hint_level"],
                        "created_at": row["created_at"],
                        "resolved": bool(
                            independent_accept
                            and independent_accept["created_at"] >= row["created_at"]
                        ),
                    }
                )
    gates = {
        "all_initial_tasks_completed": bool(initial)
        and all(task["status"] == "completed" for task in initial),
        "all_materialized_reviews_completed": bool(review_rows)
        and all(row["status"] == "completed" for row in review_rows),
        "no_unresolved_confusions": not state.get("unresolved_confusions"),
        "no_unresolved_source_conflicts": int(conflicts) == 0,
        "no_unscheduled_minutes": int(plan["unscheduled_task_minutes"]) == 0,
        "all_code_problems_independently_verified": (
            code_summary["problem_count"] == 0
            or code_summary["independently_verified_problem_count"]
            == code_summary["problem_count"]
        ),
        "has_independent_mastery_evidence": bool(independent_evidence),
    }
    verified_complete = all(gates.values())
    return {
        "session_id": session_id,
        "workspace_id": session["workspace_id"],
        "learning_goal": state.get("current_learning_goal"),
        "status": (
            "verified_complete"
            if verified_complete
            else "blocked_by_capacity"
            if int(plan["unscheduled_task_minutes"]) > 0
            else "in_progress"
        ),
        "verified_complete": verified_complete,
        "gates": gates,
        "time": {
            "planned_task_minutes": planned_minutes,
            "completed_task_minutes": completed_minutes,
            "actual_elapsed_minutes": actual_minutes,
            "variance_minutes": round(actual_minutes - completed_minutes, 3),
            "variance_reason": (
                "实际耗时与完成的标准任务分钟分开记录；差异可能来自学习速度、补救或中断。"
            ),
        },
        "tasks_by_type": by_type,
        "mastery": {
            "evidence_count": len(evidence_rows),
            "independent_pass_count": len(independent_evidence),
            "review_chain_count": len(review_rows),
            "completed_review_chain_count": sum(
                row["status"] == "completed" for row in review_rows
            ),
        },
        "code": code_summary,
        "mistake_queue": mistakes[-50:],
        "unresolved_confusions": state.get("unresolved_confusions") or [],
        "generated_at": _now(),
    }


@router.get("/sessions/{session_id}/completion-report")
def completion_report(session_id: str) -> dict[str, Any]:
    with _connect() as connection:
        return _completion_report(connection, session_id)


@router.post("/workspaces/{workspace_id}/retrieve")
def retrieve(workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
    query = str(body.get("query") or "").strip()
    plan_id = str(body.get("plan_id") or "").strip() or None
    if not query:
        _error("QUERY_REQUIRED", "检索问题不能为空", 422)
    with _connect() as connection:
        plan_material_ids = _plan_material_ids(connection, workspace_id, plan_id)
        if plan_id and not plan_material_ids:
            _error("ACTIVE_PLAN_NOT_FOUND", "当前工作区中不存在该主动教学计划", 404)
        conflicts = _matching_conflicts(
            connection, workspace_id, query, plan_id=plan_id
        )
        if conflicts:
            return {
                "status": "conflict",
                "answer": f"资料存在冲突：{conflicts[0]['summary']}",
                "citations": [conflicts[0]["evidence_a"], conflicts[0]["evidence_b"]],
                "conflicts": conflicts,
                "retrieval": _knowledge_base_status(
                    connection, workspace_id, plan_material_ids
                ),
            }
        hits = _search(connection, workspace_id, query, plan_id=plan_id)
        if not hits:
            return {
                "status": "insufficient",
                "answer": "资料中无法确认",
                "citations": [],
                "retrieval": _knowledge_base_status(
                    connection, workspace_id, plan_material_ids
                ),
            }
        return {
            "status": "grounded",
            "answer": f"资料中可确认：{hits[0]['quote'][:300]}",
            "citations": hits,
            "retrieval": _knowledge_base_status(
                connection, workspace_id, plan_material_ids
            ),
        }


@router.get("/workspaces/{workspace_id}/snapshot")
def workspace_snapshot(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        materials = [
            _public_material(connection, row["id"])
            for row in connection.execute(
                "SELECT id FROM active_materials WHERE workspace_id=? ORDER BY uploaded_at", (workspace_id,)
            ).fetchall()
        ]
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        plan_row = connection.execute(
            "SELECT id FROM active_plans WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        session_row = (
            connection.execute(
                "SELECT id FROM active_teaching_sessions WHERE plan_id=? ORDER BY started_at DESC LIMIT 1",
                (plan_row["id"],),
            ).fetchone()
            if plan_row
            else None
        )
        knowledge_graph = _graph(connection, workspace_id)
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='active_point_details'"
        ).fetchone():
            from .generic_knowledge_api import public_generic_graph

            knowledge_graph = public_generic_graph(connection, workspace_id)
        plan_material_ids = (
            _plan_material_ids(connection, workspace_id, plan_row["id"])
            if plan_row
            else []
        )
        return {
            "workspace_id": workspace_id,
            "local_knowledge_base": {
                **_knowledge_base_status(
                    connection, workspace_id, plan_material_ids
                ),
                "scope": "current_plan" if plan_row else "workspace",
                "plan_id": plan_row["id"] if plan_row else None,
                "material_ids": plan_material_ids,
            },
            "materials": materials,
            "receipt": json.loads(receipt_row["payload"]) if receipt_row else None,
            "knowledge_graph": knowledge_graph,
            "plan": _plan(connection, plan_row["id"]) if plan_row else None,
            "session": _public_session(connection, session_row["id"]) if session_row else None,
            "completion_report": (
                _completion_report(connection, session_row["id"]) if session_row else None
            ),
        }


@router.get("/workspaces")
def list_learning_workspaces() -> dict[str, Any]:
    """Return the durable task index for the unified learning workspace."""
    with _connect() as connection:
        workspace_ids = [
            row["workspace_id"]
            for row in connection.execute(
                """SELECT workspace_id FROM active_materials
                   UNION SELECT workspace_id FROM active_receipts
                   UNION SELECT workspace_id FROM active_plans
                   ORDER BY workspace_id"""
            ).fetchall()
        ]
        items: list[dict[str, Any]] = []
        for workspace_id in workspace_ids:
            receipt_row = connection.execute(
                "SELECT payload,updated_at FROM active_receipts WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
            receipt = json.loads(receipt_row["payload"]) if receipt_row else {}
            plan = connection.execute(
                """SELECT * FROM active_plans
                   WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1""",
                (workspace_id,),
            ).fetchone()
            session = (
                connection.execute(
                    """SELECT id,updated_at FROM active_teaching_sessions
                       WHERE plan_id=? ORDER BY updated_at DESC LIMIT 1""",
                    (plan["id"],),
                ).fetchone()
                if plan
                else None
            )
            counts = connection.execute(
                """SELECT COUNT(*) AS material_count,
                          COALESCE(SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END),0)
                              AS ready_material_count
                   FROM active_materials WHERE workspace_id=?""",
                (workspace_id,),
            ).fetchone()
            point_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM active_knowledge_points WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchone()[0]
            )
            task_counts = {"total": 0, "completed": 0}
            next_task = None
            if plan:
                task_row = connection.execute(
                    """SELECT id,title,day_date,estimated_task_minutes,status,
                              task_type,learning_phase
                       FROM active_plan_tasks
                       WHERE plan_id=? AND status<>'completed'
                       ORDER BY day_date,task_order,id LIMIT 1""",
                    (plan["id"],),
                ).fetchone()
                if task_row:
                    next_task = dict(task_row)
                count_row = connection.execute(
                    """SELECT COUNT(*) AS total,
                              COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0)
                                  AS completed
                       FROM active_plan_tasks WHERE plan_id=?""",
                    (plan["id"],),
                ).fetchone()
                task_counts = {
                    "total": int(count_row["total"]),
                    "completed": int(count_row["completed"]),
                }
            updated_candidates = [
                str(value)
                for value in (
                    receipt_row["updated_at"] if receipt_row else "",
                    plan["created_at"] if plan else "",
                    session["updated_at"] if session else "",
                )
                if value
            ]
            items.append(
                {
                    "workspace_id": workspace_id,
                    "title": str(
                        receipt.get("learning_goal")
                        or (next_task["title"] if next_task else workspace_id)
                    ),
                    "status": (
                        "learning"
                        if session
                        else "planned"
                        if plan
                        else "setting_up"
                    ),
                    "material_count": int(counts["material_count"]),
                    "ready_material_count": int(counts["ready_material_count"]),
                    "knowledge_point_count": point_count,
                    "plan_id": plan["id"] if plan else None,
                    "plan_feasible": bool(plan["feasible"]) if plan else None,
                    "session_id": session["id"] if session else None,
                    "task_count": task_counts["total"],
                    "completed_task_count": task_counts["completed"],
                    "next_task": next_task,
                    "updated_at": max(updated_candidates) if updated_candidates else "",
                }
            )
        items.sort(key=lambda item: (item["updated_at"], item["workspace_id"]), reverse=True)
        return {"items": items}

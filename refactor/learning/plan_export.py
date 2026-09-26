"""Selected-current-plan ZIP export, split from account_portability.py.

Retains canonical JSON, SHA256SUMS, staged writing and manifest verification.
Replaces whole-vault export with a single plan, allowlisted task fields and its
verified originals. index.html is self-contained and needs no app/login/network.
This module never deletes an account or calls any account lifecycle endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from datetime import datetime, timezone
from enum import Enum
import hashlib
import html
import json
import os
from pathlib import Path
import re
from threading import Event, RLock
from uuid import uuid4
from urllib.parse import quote
import zipfile

from .file_io import _native_path, assert_no_reparse_ancestors, canonical_io_path, open_resource_source
from .plan_models import PlanCatalog, PlanError, TASK_FIELDS, plan_details
from .plan_resources import PlanResourceResolver
from .presentation import DETAIL_LABELS, human_status


class ExportStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    MISSING_ORIGINALS = "MISSING_ORIGINALS"
    NO_CURRENT_PLAN = "NO_CURRENT_PLAN"


@dataclass(frozen=True)
class ExportResult:
    status: ExportStatus
    code: str
    message: str
    output_path: Path | None = None
    plan_id: str = ""
    account_id: str = ""
    missing: tuple[str, ...] = ()

    @property
    def succeeded(self):
        return self.status is ExportStatus.SUCCEEDED


class ExportCancellation(Event):
    """Serialize UI cancellation with atomic publication, preserving its result.

    The lock covers only the final rename and result recording, never copying,
    hashing or network requests. A close arriving after publication reports the
    saved artifact instead of incorrectly calling it cancelled.
    """
    def __init__(self):
        super().__init__()
        self.publication_lock = RLock()
        self.published_result = None

    def set(self):
        with self.publication_lock:
            super().set()


def _json_bytes(value: object) -> bytes:
    """Canonical serializer extracted from account_portability._json_bytes."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _member_name(index, filename):
    # Stable ordinal prefix prevents collisions and Windows reserved basenames.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")[:100] or "original"
    return f"materials/{index:03d}-{name}"


def _offline_html(plan, materials, observed_at):
    escape = lambda value: html.escape(str(value if value is not None else ""), quote=True)
    rows = []
    for task in plan["tasks"]:
        values = [task.get("day_date") or task.get("date") or "—", task.get("title") or task.get("name") or "未命名任务", task.get("objective") or "", task.get("acceptance_condition") or "", task.get("estimated_task_minutes", task.get("planned_task_minutes", "—")), human_status(task.get("status"))]
        rows.append("<tr>" + "".join("<td>" + escape(v) + "</td>" for v in values) + "</tr>")
    links = "".join(f'<li><a href="{escape(quote(item["member"], safe="/"))}">{escape(item["filename"])}</a> · {item["size_bytes"]} 字节</li>' for item in materials)
    details = "<dl>" + "".join("<dt>" + escape(DETAIL_LABELS.get(key, key)) + "</dt><dd>" + escape(value) + "</dd>" for key, value in plan["details"].items()) + "</dl>"
    return ('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>MentorFlow 当前计划</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#302632;background:#fff9fc}table{border-collapse:collapse;width:100%;background:white}td,th{border:1px solid #ddd;padding:10px;text-align:left;vertical-align:top;white-space:pre-wrap}h1{color:#9b3965}a{color:#9b3965}</style>
<h1>''' + escape(plan["title"]) + "</h1><p>学习任务：" + escape(plan["workspace_title"]) + " · 状态：" + escape(plan["status"]) + "</p><p>记录读取时间：" + escape(observed_at) + "。这是导出时的当前计划快照，仅包含该计划及下列关联资料。解压整个 ZIP 后打开本页即可离线查看，无需登录。</p>" + details + "<table><thead><tr><th>日期</th><th>任务</th><th>目标</th><th>完成条件</th><th>预计分钟</th><th>状态</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table><h2>关联资料原件</h2><ul>" + (links or "<li>当前计划未关联资料。</li>") + "</ul><p>完整结构：plan.json。完整性清单：manifest.json、SHA256SUMS。</p></html>").encode("utf-8")


def export_current_plan(*, catalog: PlanCatalog, plan_key: str, resolver: PlanResourceResolver,
                        destination: Path | None, cleanup_roots: tuple[Path, ...],
                        cancelled: Event | None = None, require_current=None) -> ExportResult:
    """Synchronous worker API; invoke off the GUI thread.

    destination=None or cancelled Event -> CANCELLED. cleanup_roots must include
    every directory the account layer might remove (plus this resolver's roots,
    enforced internally). Success means originals and ZIP ledger verified and a
    complete file published without overwriting. It is NOT consent to deletion.
    """
    account_id = catalog.scope.account_id
    plan = next((p for p in catalog.plans if p.key == plan_key), None)
    plan_id = plan.plan_id if plan else ""
    def result(status, code, message, **kwargs):
        return ExportResult(status, code, message, plan_id=plan_id, account_id=account_id, **kwargs)
    def check():
        if cancelled is not None and cancelled.is_set():
            raise PlanError("CANCELLED", "导出已取消；未发布部分文件。")
        resolver.require_current()
        if require_current:
            require_current()
    temporary = None
    published = False
    try:
        check()
        if destination is None:
            return result(ExportStatus.CANCELLED, "CANCELLED", "已取消选择保存位置。")
        if not catalog.plans:
            if catalog.notices:
                return result(ExportStatus.FAILED, "PLAN_SOURCE_INCOMPLETE", "账号历史计划尚未完整恢复，不能报告导出成功。")
            return result(ExportStatus.NO_CURRENT_PLAN, "NO_CURRENT_PLAN", "暂无当前计划；未生成空导出包。")
        if plan is None:
            return result(ExportStatus.FAILED, "PLAN_SELECTION_REQUIRED", "请先选择需要保存的当前计划。")
        if resolver.scope != catalog.scope:
            raise PlanError("SCOPE_MISMATCH", "导出计划与原件解析器不属于同一账号空间。")
        if plan.issues:
            return result(ExportStatus.FAILED, "PLAN_INCOMPLETE", "\n".join(plan.issues))
        output = canonical_io_path(destination)
        if output.suffix.lower() != ".zip":
            raise PlanError("DESTINATION_INVALID", "请选择 .zip 文件作为保存位置。")
        if not cleanup_roots:
            raise PlanError("CLEANUP_SCOPE_REQUIRED", "尚未提供账号清理目录范围，无法安全选择导出位置。")
        forbidden = [canonical_io_path(path) for path in cleanup_roots] + [resolver.root.parent]
        if resolver.legacy_root:
            forbidden.append(resolver.legacy_root.parent.parent)
        if any(output == root or output.is_relative_to(root) for root in forbidden):
            raise PlanError("DESTINATION_IN_ACCOUNT", "保存位置位于账号受管目录或后续清理范围内，请另选位置。")
        assert_no_reparse_ancestors(output, Path(output.anchor))
        if _native_path(output).exists():
            raise PlanError("DESTINATION_EXISTS", "目标文件已存在，请选择新文件名；原文件未覆盖。")
        if not _native_path(output.parent).is_dir():
            raise PlanError("DESTINATION_INVALID", "保存目录不存在，请选择已有目录。")
        originals = []
        for resource in plan.resources:
            check()
            originals.append(resolver.resolve(resource, cancelled=cancelled))
        missing = tuple(item.resource.filename for item in originals if item.status == "missing")
        errors = [item for item in originals if item.status == "error"]
        if errors:
            return result(ExportStatus.FAILED, "ORIGINAL_INVALID", "\n".join(item.resource.filename + "：" + item.message for item in errors))
        if missing:
            return result(ExportStatus.MISSING_ORIGINALS, "MISSING_ORIGINALS", "关联原件缺失，未创建导出包；请补齐、取消或返回账号页明确改选不保存。", missing=missing)
        safe_plan = {key: getattr(plan, key) for key in ("plan_id", "workspace_id", "title", "workspace_title", "status", "source")}
        safe_plan["details"] = plan_details(plan.details)
        safe_plan["tasks"] = [{key: task[key] for key in TASK_FIELDS if key in task} for task in plan.tasks]
        for output_task, task in zip(safe_plan["tasks"], plan.tasks):
            for field in ("source_ids", "source_chunk_ids"):
                refs = task.get(field)
                if isinstance(refs, list) and all(isinstance(v, str) for v in refs):
                    output_task[field] = refs
        # Explicit reference fields only; never export arbitrary runtime or
        # account metadata stored alongside a plan.
        safe_plan["sources"] = []
        for source in plan.details.get("sources", []):
            if isinstance(source, dict):
                source_copy = {k: source[k] for k in ("id", "url", "final_url", "title", "content_sha256", "truncated") if k in source}
                source_copy["chunks"] = [{k: c[k] for k in ("id", "text") if k in c}
                    for c in source.get("chunks", []) if isinstance(c, dict)]
                safe_plan["sources"].append(source_copy)
        material_manifest = [dict(resource_id=item.resource.resource_id, filename=item.resource.filename, media_type=item.resource.media_type, size_bytes=item.resource.size_bytes, sha256=item.resource.sha256, member=_member_name(index, item.resource.filename)) for index, item in enumerate(originals, 1)]
        safe_plan["materials"] = material_manifest
        safe_plan["source_authority"] = catalog.authority
        safe_plan["server_confirmed_at" if catalog.authority == "SERVER_CONFIRMED" else "local_read_at"] = catalog.observed_at
        payloads = {"plan.json": _json_bytes(safe_plan), "index.html": _offline_html(safe_plan, material_manifest, catalog.observed_at)}
        temporary = output.with_name(output.name + "." + uuid4().hex + ".part")
        ledger = {name: _sha256_bytes(raw) for name, raw in payloads.items()}
        with _native_path(temporary).open("xb") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, raw in payloads.items():
                    check()
                    archive.writestr(name, raw)
                for original, manifest in zip(originals, material_manifest):
                    check()
                    digest, count = hashlib.sha256(), 0
                    with open_resource_source(resolver._safe_original(original.path)) as (_, source):
                        with archive.open(manifest["member"], "w", force_zip64=True) as target:
                            while block := source.read(1024 * 1024):
                                check()
                                count += len(block)
                                if count > original.resource.size_bytes:
                                    raise PlanError("ORIGINAL_CHANGED", "原件在导出期间发生变化；未发布导出包。")
                                digest.update(block)
                                target.write(block)
                    if count != original.resource.size_bytes or digest.hexdigest() != original.resource.sha256:
                        raise PlanError("ORIGINAL_CHANGED", "原件在导出期间发生变化；未发布导出包。")
                    ledger[manifest["member"]] = digest.hexdigest()
                manifest = {"schema": "mentorflow.current-plan-export.v1", "created_at": datetime.now(timezone.utc).isoformat(), "snapshot_revision": catalog.snapshot_revision, "plan_id": plan.plan_id, "members": [{"path": key, "sha256": value} for key, value in sorted(ledger.items())], "exclusions": ["credentials", "other_plans", "other_accounts", "models", "absolute_local_paths"]}
                manifest_bytes = _json_bytes(manifest)
                archive.writestr("manifest.json", manifest_bytes)
                ledger["manifest.json"] = _sha256_bytes(manifest_bytes)
                archive.writestr("SHA256SUMS", "".join(f"{digest}  {name}\n" for name, digest in sorted(ledger.items())).encode())
            handle.flush()
            os.fsync(handle.fileno())
        # Verify the completed archive from disk, not just bytes queued to write.
        with zipfile.ZipFile(_native_path(temporary)) as archive:
            if set(archive.namelist()) != set(ledger) | {"SHA256SUMS"}:
                raise PlanError("VERIFY_FAILED", "导出包成员核验失败。")
            for name, expected in ledger.items():
                digest = hashlib.sha256()
                with archive.open(name) as source:
                    while block := source.read(1024 * 1024):
                        check()
                        digest.update(block)
                if digest.hexdigest() != expected:
                    raise PlanError("VERIFY_FAILED", "导出包完整性核验失败。")
        gate = cancelled.publication_lock if isinstance(cancelled, ExportCancellation) else nullcontext()
        with gate:
            check()
            assert_no_reparse_ancestors(output, Path(output.anchor))
            # Windows rename refuses an existing destination, including a file
            # that appeared after the initial check. Never overwrite it.
            if os.name == "nt":
                os.rename(_native_path(temporary), _native_path(output))
            else:
                os.link(temporary, output)
                temporary.unlink()
            published = True
            outcome = result(ExportStatus.SUCCEEDED, "EXPORTED", "当前计划和关联原件已完整导出。解压后打开 index.html 可离线查看。", output_path=output)
            if isinstance(cancelled, ExportCancellation):
                cancelled.published_result = outcome
            return outcome
    except PlanError as exc:
        return result(ExportStatus.CANCELLED if exc.code == "CANCELLED" else ExportStatus.FAILED, exc.code, str(exc))
    except Exception:
        return result(ExportStatus.FAILED, "EXPORT_FAILED", "导出失败；请检查目录权限、磁盘空间及原件状态。未报告保存成功。")
    finally:
        if temporary is not None and not published:
            try:
                _native_path(temporary).unlink(missing_ok=True)
            except OSError:
                return result(ExportStatus.FAILED, "PARTIAL_CLEANUP_FAILED", f"导出未完成，临时文件清理失败：{temporary}。未生成可用导出包。")

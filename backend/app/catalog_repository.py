from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import db
from .story_pack import StoryPackError, load_story_pack
from .services import uid


ROOT = Path(__file__).resolve().parents[2]
CATALOG_ROOT = ROOT / "content" / "narrative_catalog" / "v2"
CATALOG_DB = CATALOG_ROOT / "narrative_catalog_v2.sqlite"
MANIFEST_PATH = CATALOG_ROOT / "catalog_manifest.json"
INTEGRITY_PATH = CATALOG_ROOT / "integrity_report.json"
OVERLAY_PATH = CATALOG_ROOT / "builtin_catalog_overlay.json"


class CatalogError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, status_code: int = 400):
        self.code, self.message, self.details, self.status_code = code, message, details or {}, status_code
        super().__init__(message)


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else (value if value is not None else fallback)
    except (TypeError, ValueError):
        return fallback


def _connect() -> sqlite3.Connection:
    if not CATALOG_DB.is_file():
        raise CatalogError("CATALOG_NOT_INSTALLED", "10,000条剧情目录尚未安装", status_code=503)
    connection = sqlite3.connect(f"file:{CATALOG_DB.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _manifest() -> dict[str, Any]:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError("CATALOG_MANIFEST_INVALID", "目录 manifest 无法读取", status_code=503) from exc


@lru_cache(maxsize=1)
def _overlay() -> dict[str, Any]:
    if not OVERLAY_PATH.exists():
        return {}
    try:
        item = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
        return {item.get("catalog_id"): item} if item.get("catalog_id") else {}
    except (OSError, ValueError):
        return {}


def health() -> dict[str, Any]:
    manifest = _manifest()
    integrity = json.loads(INTEGRITY_PATH.read_text(encoding="utf-8")) if INTEGRITY_PATH.exists() else {}
    with _connect() as connection:
        total = connection.execute("SELECT COUNT(*) FROM catalog_records").fetchone()[0]
        curated = connection.execute("SELECT COUNT(*) FROM catalog_records WHERE record_tier='curated_dna'").fetchone()[0]
        candidate = connection.execute("SELECT COUNT(*) FROM catalog_records WHERE record_tier='candidate_index'").fetchone()[0]
        fts = connection.execute("SELECT COUNT(*) FROM catalog_fts WHERE catalog_fts MATCH ?", ("Visual",)).fetchone()[0]
    checks = {"version": manifest.get("version") == "2.0.0", "total": total == 10000 and manifest.get("total_records") == 10000, "curated": curated == 41, "candidate": candidate == 9959, "unique": len(integrity.get("checks", {})) == 0 or integrity.get("checks", {}).get("no_duplicate_ids") is True, "fts": fts >= 0}
    return {"ready": all(checks.values()), "catalog_id": manifest.get("catalog_id"), "version": manifest.get("version"), "counts": {"total": total, "curated": curated, "candidate": candidate}, "checks": checks, "integrity_report": integrity}


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    payload = _json(item.pop("payload_json", "{}"), {})
    for key in ("narrative_signals", "genres", "tags", "source_urls", "content_flags"):
        if key in item:
            item[key] = _json(item[key], [])
    item.update(payload if isinstance(payload, dict) else {})
    item["narrative_signals"] = _json(item.get("narrative_signals"), [])
    item["genres"] = _json(item.get("genres"), [])
    item["tags"] = _json(item.get("tags"), [])
    overlay = _overlay().get(item.get("catalog_id"), {})
    if overlay:
        item.update(overlay)
    return item


def get_record(catalog_id: str) -> dict[str, Any]:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM catalog_records WHERE catalog_id=?", (catalog_id,)).fetchone()
    if not row:
        overlay = _overlay().get(catalog_id)
        if overlay:
            return overlay
        raise CatalogError("CATALOG_RECORD_NOT_FOUND", "目录记录不存在", {"catalog_id": catalog_id}, 404)
    return _row_payload(row)


def _safe_filter(record: dict[str, Any], body: dict[str, Any]) -> bool:
    blocked = {str(item).lower() for item in body.get("exclude_content_flags", []) or []}
    flags = {str(item).lower() for item in record.get("content_flags", []) or []}
    return not blocked.intersection(flags)


def _fts_query(value: str) -> str:
    """Turn user/profile text into a safe FTS5 phrase query.

    FTS5 treats punctuation and operator-like input as query syntax. Quoting
    each bounded term keeps matching deterministic for both Chinese text and
    ordinary English profile fields without exposing raw MATCH expressions.
    """
    terms = re.findall(r"[\w\u3400-\u9fff]+", value, flags=re.UNICODE)
    return " AND ".join(f'"{term.replace(chr(34), "")}"*' for term in terms[:24])


def search(body: dict[str, Any]) -> dict[str, Any]:
    query = str(body.get("query") or "").strip()
    limit = max(1, min(int(body.get("limit", 20)), 100))
    tiers = body.get("record_tiers") or ["curated_dna", "candidate_index"]
    signals = {str(item).lower() for item in body.get("narrative_signals", []) or []}
    sql = "SELECT r.* FROM catalog_records r"
    params: list[Any] = []
    if query:
        sql += " JOIN catalog_fts f ON f.catalog_id=r.catalog_id WHERE catalog_fts MATCH ?"
        params.append(_fts_query(query))
    else:
        sql += " WHERE 1=1"
    sql += " AND r.record_tier IN (" + ",".join("?" for _ in tiers) + ")"
    params.extend(tiers)
    sql += " ORDER BY CASE WHEN r.record_tier='curated_dna' THEN 0 ELSE 1 END, COALESCE(r.curation_priority,0) DESC LIMIT ?"
    params.append(limit)
    with _connect() as connection:
        records = [_row_payload(row) for row in connection.execute(sql, params).fetchall()]
    filtered = [record for record in records if _safe_filter(record, body)]
    if signals:
        filtered.sort(key=lambda record: len(signals.intersection({str(item).lower() for item in record.get("narrative_signals", [])})), reverse=True)
    return {"query": query, "limit": limit, "results": filtered[:limit], "source": "offline_sqlite_fts"}


def _diversity_key(record: dict[str, Any]) -> tuple[str, str, str]:
    dna = record.get("dna_card") or {}
    return (str(dna.get("player_position") or record.get("player_position") or ""), str(dna.get("relationship_engine") or record.get("relationship_engine") or ""), str((record.get("narrative_signals") or [""])[0]))


def match(body: dict[str, Any]) -> dict[str, Any]:
    profile = body.get("task_profile") or body
    query = " ".join(str(profile.get(key, "")) for key in ("goal", "goal_statement", "task_title", "expected_output", "learning_style"))
    candidates = search({"query": query, "limit": 100, "exclude_content_flags": profile.get("exclude_content_flags", [])}).get("results", []) if query.strip() else search({"limit": 100, "exclude_content_flags": profile.get("exclude_content_flags", [])}).get("results", [])
    builtin = [record for record in _overlay().values() if record.get("story_pack_status") == "ready"]
    candidates = builtin + [record for record in candidates if record.get("catalog_id") not in {item.get("catalog_id") for item in builtin}]
    candidates.sort(key=lambda record: (0 if record.get("story_pack_status") == "ready" else (0 if record.get("record_tier") == "curated_dna" else 1), -(float(record.get("curation_priority") or 0)), -float(record.get("quality_score") or 0)))
    selected: list[dict[str, Any]] = []
    keys: set[tuple[str, str, str]] = set()
    for record in candidates:
        key = _diversity_key(record)
        if key in keys and len(selected) < 2:
            continue
        keys.add(key)
        record["why_fit"] = "优先匹配已策展的叙事结构，并根据任务目标、关系引擎和周期给出适配理由。" if record.get("record_tier") == "curated_dna" else "这是待策展候选，当前仅依据目录元数据匹配，不能视为完整剧情分析。"
        record["story_pack_status"] = story_pack_status(record["catalog_id"])["story_pack_status"]
        selected.append(record)
        if len(selected) == 3:
            break
    return {"results": selected, "count": len(selected), "retrieval_budget": {"fts_max": 100, "rule_rank_max": 20, "display_max": 3}}


def story_pack_status(catalog_id: str) -> dict[str, Any]:
    overlay = _overlay().get(catalog_id)
    pack_id = (overlay or {}).get("story_pack_id") or (catalog_id if catalog_id.startswith("builtin:") else "")
    if pack_id.startswith("builtin:"):
        pack_id = pack_id.split(":", 1)[1]
    if pack_id:
        try:
            pack = load_story_pack(pack_id)
            return {"catalog_id": catalog_id, "story_pack_id": pack_id, "story_pack_status": "ready", "version": pack["manifest"].get("version"), "title": pack["manifest"].get("title")}
        except StoryPackError:
            pass
    return {"catalog_id": catalog_id, "story_pack_id": pack_id or None, "story_pack_status": "PACK_REQUIRED"}


def request_curation(catalog_id: str, workspace_id: str, task_profile: dict[str, Any]) -> dict[str, Any]:
    get_record(catalog_id)
    request_id = uid("curation")
    now = db.now()
    with db.connection() as connection:
        connection.execute("INSERT INTO catalog_curation_requests (id,catalog_id,workspace_id,requested_task_profile,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (request_id, catalog_id, workspace_id or "", json.dumps(task_profile, ensure_ascii=False), "queued", now, now))
    return {"request_id": request_id, "catalog_id": catalog_id, "status": "queued", "message": "已创建策展请求；当前不会联网或自行补写剧情。"}
def _all_records() -> list[dict[str, Any]]:
    """Load the complete offline catalog before applying semantic ranking."""
    with _connect() as connection:
        return [_row_payload(row) for row in connection.execute("SELECT * FROM catalog_records").fetchall()]


def _profile_mode(profile: dict[str, Any]) -> str:
    recommendation = profile.get("coupling_recommendation") or {}
    return str(recommendation.get("mode") or profile.get("coupling_mode") or "parallel").lower()


def _profile_duration(profile: dict[str, Any]) -> int:
    value = profile.get("duration_days") or profile.get("duration") or 14
    if isinstance(value, (list, tuple)):
        value = value[0] if value else 14
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 14


def _learning_fit(record: dict[str, Any]) -> dict[str, Any]:
    dna = record.get("dna_card") or {}
    fit = dna.get("learning_fit") or record.get("learning_fit") or {}
    return fit if isinstance(fit, dict) else {}


def _narrative_dimensions(record: dict[str, Any]) -> dict[str, str]:
    dna = record.get("dna_card") or {}
    families = dna.get("narrative_families") or record.get("narrative_signals") or ["adaptive_route"]
    families = [str(item) for item in families]
    signals = set(families)
    relationship = str(dna.get("relationship_engine") or record.get("relationship_engine") or "角色关系随学习进度变化")
    interaction = str(dna.get("player_position") or record.get("interaction_style") or "选择与反馈")
    emotion = str(dna.get("emotional_promise") or record.get("emotional_tone") or "逐步建立投入感")
    time_mechanic = "时间循环/复习轮次" if "knowledge_accumulation_loop" in signals or "false_ending_true_route" in signals else "现实日期推进" if "distant_real_time_companion" in signals or "routine_world_lives_without_you" in signals else "章节里程碑"
    return {"narrative_structure": ", ".join(families[:3]), "relationship_engine": relationship, "interaction_style": interaction, "emotional_tone": emotion, "time_mechanic": time_mechanic}


def _shape_fit(profile: dict[str, Any], record: dict[str, Any]) -> tuple[float, str]:
    shape = str(profile.get("task_shape") or "mixed")
    fit = _learning_fit(record)
    best = " ".join(str(item) for item in fit.get("best_task_types", []) or [])
    labels = {
        "coding_practice": ("编程 算法 题库 技术", "编程练习与题库式学习"), "practical_project": ("项目 交付 技术 应用", "项目交付与实践"),
        "case_analysis": ("法律 证据 案件 论证", "证据论证与案例分析"), "language_acquisition": ("语言 英语 单词 翻译", "语言重复与迁移"),
        "concept_course": ("医学 概念 课程 诊断", "概念课程与解释"), "long_form_creation": ("写作 文章 研究 论文", "长篇创作与修订"),
        "memory_drill": ("记忆 面试 复习 背诵", "间隔复习与主动回忆"), "exam_cram": ("冲刺 考试 短期 每日", "短期冲刺与快速迁移"),
    }
    terms, label = labels.get(shape, ("学习 复习 课程", "综合学习"))
    hit = any(term in best for term in terms.split())
    return (0.96 if hit else 0.42, f"资料属于{label}，该候选的学习适配范围为：{best or '通用学习'}。")


def _duration_fit(profile: dict[str, Any], record: dict[str, Any]) -> tuple[float, str]:
    target = _profile_duration(profile)
    fit = _learning_fit(record)
    ranges = fit.get("duration_days") or record.get("duration_days") or []
    if isinstance(ranges, (int, float)):
        ranges = [ranges]
    try:
        values = [int(item) for item in ranges]
    except (TypeError, ValueError):
        values = []
    if len(values) >= 2:
        low, high = min(values), max(values)
        score = 1.0 if low <= target <= high else max(0.35, 1 - min(abs(target - (low if target < low else high)) / max(high, 1), 0.65))
        label = f"建议周期 {low}–{high} 天"
    elif values:
        score, label = max(0.35, 1 - abs(target - values[0]) / max(target, 1)), f"建议周期约 {values[0]} 天"
    else:
        score, label = 0.55, "建议周期待策展"
    return round(score, 3), label


def _coupling_fit(profile: dict[str, Any], record: dict[str, Any]) -> tuple[float, str]:
    mode = _profile_mode(profile)
    coupling = str(_learning_fit(record).get("coupling") or "")
    hints = {"strong": ("强", "证据", "门禁", "融合"), "weak": ("弱", "权限", "主题", "伴"), "parallel": ("并行", "每日", "契约", "循环")}.get(mode, (mode,))
    hit = any(hint in coupling for hint in hints)
    return (0.94 if hit else 0.48, f"{mode}耦合与候选的融合方式{'匹配' if hit else '部分匹配'}：{coupling or '目录未声明'}。")


def _rank_catalog_record(profile: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    shape_score, shape_reason = _shape_fit(profile, record)
    duration_score, duration_reason = _duration_fit(profile, record)
    coupling_score, coupling_reason = _coupling_fit(profile, record)
    quality = float(record.get("quality_score") or record.get("narrative_score") or 50) / 100
    score = round((shape_score * 0.45 + duration_score * 0.2 + coupling_score * 0.2 + min(1, quality) * 0.15) * 100, 1)
    status = story_pack_status(record["catalog_id"])
    dimensions = _narrative_dimensions(record)
    playable = status["story_pack_status"] == "ready" and bool(status.get("story_pack_id"))
    return {
        "catalog_id": record["catalog_id"], "title": record.get("title") or record["catalog_id"], "record_tier": record.get("record_tier", "candidate_index"),
        "match_score": score, "match_reasons": [shape_reason, duration_reason, coupling_reason], "task_shape_fit": shape_score,
        "duration_fit": duration_score, "coupling_fit": coupling_score, **dimensions, "tags": record.get("tags") or record.get("narrative_signals") or [],
        "pack_status": status["story_pack_status"], "story_pack_status": status["story_pack_status"], "story_pack_id": status.get("story_pack_id"), "playable": playable,
        "curation_status": record.get("curation_status") or "catalog_metadata", "why_fit": shape_reason, "duration_days": (_learning_fit(record).get("duration_days") or record.get("duration_days") or []),
    }


def _match_v2(body: dict[str, Any]) -> dict[str, Any]:
    profile = dict(body.get("task_profile") or body)
    exclude = {str(item).lower() for item in profile.get("exclude_content_flags", []) or []}
    records = _all_records()
    catalog_record_count = len(records)
    records.extend(item for item in _overlay().values() if item.get("catalog_id") not in {record.get("catalog_id") for record in records})
    ranked = []
    for record in records:
        flags = {str(item).lower() for item in record.get("content_flags", []) or []}
        if exclude.intersection(flags):
            continue
        ranked.append(_rank_catalog_record(profile, record))
    ranked.sort(key=lambda item: (-item["match_score"], 0 if item["record_tier"] == "curated_dna" else 1, item["catalog_id"]))
    selected: list[dict[str, Any]] = []
    ready = next((item for item in ranked if item["playable"]), None)
    if ready:
        selected.append(ready)
    for item in ranked:
        if len(selected) >= 3:
            break
        if item["catalog_id"] in {candidate["catalog_id"] for candidate in selected}:
            continue
        if selected and item["record_tier"] not in {"curated_dna", "candidate_index"}:
            continue
        selected.append(item)
    return {"results": selected[:3], "count": len(selected[:3]), "retrieval_budget": {"scanned_records": catalog_record_count, "overlay_records": len(records) - catalog_record_count, "rule_rank_max": len(ranked), "display_max": 3}, "task_profile": {"domain": profile.get("domain"), "task_shape": profile.get("task_shape"), "knowledge_structure": profile.get("knowledge_structure"), "review_need": profile.get("review_need"), "coupling_mode": _profile_mode(profile), "recommended_story_structures": [item["narrative_structure"] for item in selected[:3]]}}


match = _match_v2

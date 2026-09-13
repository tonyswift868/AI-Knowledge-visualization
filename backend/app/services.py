from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import socket
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from .runtime_resources import project_resource_root
from urllib.parse import urlparse

from . import db

SUPPORTED = {".pdf", ".docx", ".md", ".txt", ".html", ".htm"}
FORBIDDEN_PARTS = {".git", "node_modules", ".venv", "venv"}
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, MAX_DEPTH = 50, 5_000_000, 50_000_000, 5
STORY_PRESET_PATH = project_resource_root() / "data" / "presets" / "mentorflow_story_theater_default.json"


def load_story_preset() -> dict:
    try:
        return json.loads(STORY_PRESET_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"world": {}, "characters": []}


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_text(path: Path) -> tuple[str, int]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".html", ".htm"}:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if suffix in {".html", ".htm"}:
            from bs4 import BeautifulSoup
            raw = BeautifulSoup(raw, "html.parser").get_text("\n")
        return raw, 1
    if suffix == ".pdf":
        import fitz
        doc = fitz.open(path)
        return "\n\n".join(page.get_text() for page in doc), len(doc)
    if suffix == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(path).paragraphs), 1
    raise ValueError(f"不支持的文件类型: {suffix}")


def chunk_text(text: str, source_id: str, size: int = 900) -> list[dict]:
    blocks = [x.strip() for x in re.split(r"\n{2,}", text) if x.strip()]
    if not blocks:
        blocks = [text.strip()]
    chunks: list[dict] = []
    buf = ""
    ordinal = 1
    for block in blocks:
        if len(buf) + len(block) > size and buf:
            chunks.append(make_chunk(source_id, ordinal, buf))
            ordinal += 1
            buf = ""
        buf += ("\n\n" if buf else "") + block
    if buf:
        chunks.append(make_chunk(source_id, ordinal, buf))
    return chunks


def make_chunk(source_id: str, ordinal: int, text: str) -> dict:
    heading = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), "")
    return {"id": uid("chunk"), "source_id": source_id, "ordinal": ordinal, "page": ordinal, "heading": heading,
            "text": text, "normalized_text": normalize(text), "content_hash": digest(text)}


def safe_path(raw: str, allowed_root: str) -> Path:
    candidate = Path(raw).expanduser().resolve()
    root = Path(allowed_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("允许的本地根目录不存在或不是目录")
    if root.name.lower() in {"windows", "system32", ".git", "node_modules", ".venv"}:
        raise ValueError("拒绝使用系统目录或依赖目录作为允许根目录")
    try:
        relative_parts = candidate.relative_to(root).parts
    except ValueError as exc:
        raise ValueError("路径必须位于用户明确提供的允许目录内") from exc
    if any(part.lower() in FORBIDDEN_PARTS for part in relative_parts):
        raise ValueError("拒绝读取系统目录、隐藏依赖目录或密钥相关目录")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("路径必须位于用户明确提供的允许目录内") from exc
    return candidate


def inspect_local(raw: str, allowed_root: str) -> dict:
    candidate = safe_path(raw, allowed_root)
    files = [candidate] if candidate.is_file() else [p for p in candidate.rglob("*") if p.is_file()]
    accepted, rejected, total = [], [], 0
    for p in files:
        try:
            rel_depth = len(p.relative_to(candidate if candidate.is_dir() else candidate.parent).parts)
            if rel_depth > MAX_DEPTH:
                rejected.append({"path": str(p), "reason": "超过目录深度限制"}); continue
            if p.suffix.lower() not in SUPPORTED:
                rejected.append({"path": str(p), "reason": "不支持的文件类型"}); continue
            size = p.stat().st_size
            if size > MAX_FILE_BYTES:
                rejected.append({"path": str(p), "reason": "超过单文件大小限制"}); continue
            if len(accepted) >= MAX_FILES or total + size > MAX_TOTAL_BYTES:
                rejected.append({"path": str(p), "reason": "超过导入数量或总大小限制"}); continue
            accepted.append({"path": str(p), "name": p.name, "size": size}); total += size
        except OSError as exc:
            rejected.append({"path": str(p), "reason": str(exc)})
    return {"root": str(candidate), "files": accepted, "rejected": rejected, "total_bytes": total}


def create_source(workspace_id: str, title: str, source_type: str, text: str, **meta) -> dict:
    source_id, created = uid("source"), db.now()
    if any(s.get("content_hash")==digest(text) for s in db.rows("sources", "workspace_id", workspace_id)):
        raise ValueError("DUPLICATE_SOURCE: 当前学习空间已有相同内容的资料")
    source = {"id": source_id, "workspace_id": workspace_id, "title": title, "source_type": source_type,
              "original_filename": meta.get("original_filename", ""), "original_url": meta.get("original_url", ""),
              "local_path": meta.get("local_path", ""), "trust_level": meta.get("trust_level", "personal_notes"),
              "content_hash": digest(text), "status": "ready", "parse_error": "", "page_count": meta.get("page_count", 1),
              "text": text, "created_at": created, "updated_at": created,
              "archive_preview_id": meta.get("archive_preview_id", ""), "archive_relative_path": meta.get("archive_relative_path", ""),
              "parent_archive_name": meta.get("parent_archive_name", ""), "language": meta.get("language", "")}
    from .ingestion_secure import chunks as secure_make_chunks
    inferred_heading=next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")),"")
    parts_for_index=meta.get("parts") or [{"text":text,"page_start":None,"page_end":None,"heading":inferred_heading}]
    secure_items=secure_make_chunks(parts_for_index,source_id)
    with db.connection() as conn:
        conn.execute("INSERT INTO sources (id,workspace_id,title,source_type,original_filename,original_url,local_path,trust_level,content_hash,status,parse_error,page_count,text,created_at,updated_at,archive_preview_id,archive_relative_path,parent_archive_name,language) VALUES (:id,:workspace_id,:title,:source_type,:original_filename,:original_url,:local_path,:trust_level,:content_hash,:status,:parse_error,:page_count,:text,:created_at,:updated_at,:archive_preview_id,:archive_relative_path,:parent_archive_name,:language)", source)
        if secure_items:
            legacy_chunks=[{"id":x["id"],"source_id":source_id,"ordinal":x["ordinal"],"page":x["page_start"],"heading":x["heading"],"text":x["text"],"normalized_text":x["normalized_text"],"content_hash":x["content_hash"]} for x in secure_items]
        else: legacy_chunks=chunk_text(text, source_id)
        for chunk in legacy_chunks: conn.execute("INSERT INTO chunks VALUES (:id,:source_id,:ordinal,:page,:heading,:text,:normalized_text,:content_hash)", chunk)
    try:
        from .orm import SessionLocal, WorkspaceORM, SourceORM, ChunkORM, init_orm
        from .ingestion_secure import parse_file, chunks as secure_make_chunks
        init_orm()
        with SessionLocal() as session:
            if not session.get(WorkspaceORM, workspace_id):
                session.add(WorkspaceORM(id=workspace_id,title=workspace_id,description=""))
            orm_source=SourceORM(id=source_id,workspace_id=workspace_id,title=title,source_type=source_type,trust_level=meta.get("trust_level","personal_notes"),content_hash=digest(text),status="ready",parse_error="",original_url=meta.get("original_url",""),local_path=meta.get("local_path",""),page_count=meta.get("page_count",0),archive_preview_id=meta.get("archive_preview_id",""),archive_relative_path=meta.get("archive_relative_path",""),parent_archive_name=meta.get("parent_archive_name",""),language=meta.get("language",""))
            session.add(orm_source)
            for item in secure_items:
                session.add(ChunkORM(id=item["id"],source_id=source_id,ordinal=item["ordinal"],page_start=item["page_start"],page_end=item["page_end"],heading=item["heading"],text=item["text"],normalized_text=item["normalized_text"],content_hash=item["content_hash"]))
            session.commit()
    except Exception:
        # Legacy storage remains the compatibility source; ORM indexing can be retried safely.
        pass
    source.pop("text", None)
    source["chunk_count"] = len(db.rows("chunks", "source_id", source_id))
    return source


def source_detail(source_id: str) -> dict | None:
    source = db.row("sources", "id", source_id)
    if not source: return None
    source.pop("text", None)
    source["chunks"] = db.rows("chunks", "source_id", source_id)
    for chunk in source["chunks"]:
        chunk["page_start"]=chunk.get("page"); chunk["page_end"]=chunk.get("page")
    source["chunk_count"] = len(source["chunks"])
    return source


def available_days(start: date, deadline: date, days_per_week: int, unavailable: set[str], preferred_weekdays: list[int] | None = None) -> list[date]:
    result, current = [], start
    allowed=set(preferred_weekdays or range(7))
    if not preferred_weekdays and days_per_week<7: allowed=set(range(days_per_week))
    while current <= deadline:
        if current.isoformat() not in unavailable and current.weekday() in allowed:
            result.append(current)
        current += timedelta(days=1)
    return result


def analyze(workspace_id: str, goal: dict) -> dict:
    sources = db.rows("sources", "workspace_id", workspace_id)
    chunks = sum(len(db.rows("chunks", "source_id", s["id"])) for s in sources)
    today = date.today()
    unavailable = set(goal.get("unavailable_dates", []))
    start = date.fromisoformat(goal.get("start_date") or today.isoformat())
    days = available_days(start, date.fromisoformat(goal["deadline"]), goal["days_per_week"], unavailable, goal.get("preferred_weekdays"))
    estimated = max(90, chunks * 8 + 120)
    buffer_rate = 0.20
    buffered_estimated = int((estimated * (1 + buffer_rate)) + 0.999)
    study_day_count = max(1, len(days))
    base_daily = int((estimated / study_day_count) + 0.999)
    recommended = max(20, min(480, math.ceil(buffered_estimated / study_day_count / 5) * 5))
    available = study_day_count * recommended
    clarity = min(1, max(.25, len(goal["goal_statement"]) / 80))
    corpus=" ".join(c.get("text","") for s in sources for c in db.rows("chunks","source_id",s["id"]))
    keywords=[x for x in re.findall(r"[\w\u4e00-\u9fff]+",goal["goal_statement"].lower()) if len(x)>1]
    matched=sum(1 for x in keywords if x in corpus.lower())
    sufficiency=min(1,((chunks/8)*.45)+((matched/max(1,len(keywords)))*.55)) if chunks else 0
    at_risk=available < buffered_estimated
    risk = "high" if at_risk else "low" if sufficiency >= .5 else "medium"
    result = {"goal_clarity": round(clarity, 2), "source_sufficiency": round(sufficiency, 2),
              "estimated_total_minutes": estimated, "buffer_rate": buffer_rate, "buffer_minutes": buffered_estimated - estimated,
              "available_study_days": len(days), "base_minutes_per_day": base_daily, "recommended_minutes_per_day": recommended,
              "available_total_minutes": available, "risk_level": risk, "at_risk": at_risk, "source_coverage": round(sufficiency,2),
              "allocation_method": "资料量估算 + 20% 复习/拖延/切换损耗",
              "risks": ([] if available >= buffered_estimated else [f"按当前截止日期仍缺少 {buffered_estimated - available} 分钟，建议延长截止日期或缩小范围"]) + ([] if chunks else ["尚未导入可引用的学习资料"]),
              "missing_materials": [] if chunks >= 4 else ["建议补充至少 4 个可引用知识片段"],
              "recommendations": ["保留截止日前的复习和最终任务缓冲"],
              "suggested_success_criteria": ["完成最终任务并提交可核验成果"], "user_confirmed": False, "created_at": db.now()}
    result["options"] = ([{"type":"extend_deadline","label":"延长截止日期"},{"type":"reduce_scope","label":"缩小目标范围"},{"type":"increase_daily_minutes","label":"增加每日投入"}] if at_risk else [])
    result["allocation_preview"] = {"first_pass_ratio": 0.4, "first_pass_days": max(1, math.ceil(len(days) * 0.4)) if days else 1, "review_strategy": "rolling_spaced_review", "note": "首轮先建立概念和示例，后续日期自动滚动安排回忆、混合复习和迁移。"}
    aid = uid("assessment")
    with db.connection() as conn:
        conn.execute("INSERT INTO assessments VALUES (?,?,?,?,?)", (aid, workspace_id, json.dumps(result, ensure_ascii=False), 0, result["created_at"]))
    try:
        from .orm import SessionLocal, FeasibilityORM, init_orm
        init_orm()
        with SessionLocal() as session:
            session.add(FeasibilityORM(id=aid,workspace_id=workspace_id,payload=result,confirmed=False)); session.commit()
    except Exception: pass
    result["id"] = aid
    return result


def scene_for_task(title: str, task_type: str, ordinal: int) -> dict:
    preset = load_story_preset()
    characters = preset.get("characters") or [{"id": "lumen", "name": "导师", "title": "学习伙伴", "role": "陪你完成今天的回合", "color": "#8ed9bd", "avatar": "✦"}]
    character = characters[(ordinal - 1) % len(characters)]
    if task_type == "final_task":
        setting = "模拟面试现场：面试官给你一道题，你需要先澄清问题，再做出可验证的解法。"
        objective = "完成一次独立表达，并让导师检查你的关键判断。"
        choices = [
            {"id": "clarify", "label": "先澄清题意", "detail": "像真实面试一样确认输入、边界和目标。", "prompt": f"进入模拟面试。我先澄清「{title}」的题意，请你扮演面试官，只给我必要信息。"},
            {"id": "model", "label": "先画解法模型", "detail": "把问题拆成数据结构、状态和步骤。", "prompt": f"进入模拟面试。我先为「{title}」画解法模型，请用追问引导我，不要直接给答案。"},
            {"id": "explain", "label": "直接开始讲解", "detail": "马上说出你的思路，导师会指出漏洞。", "prompt": f"进入模拟面试。我直接讲解「{title}」的解法，请按面试标准追问并指出漏洞。"},
        ]
    elif task_type == "review":
        setting = "复盘室：你面前是今天的错题和一个需要重新解释的关键判断。"
        objective = "找出一个薄弱点，并用新的例子重新讲清楚。"
        choices = [
            {"id": "recall", "label": "先回忆关键步骤", "detail": "不看资料，先复述你记得的流程。", "prompt": f"开始复盘「{title}」。请先问我三个递进问题，帮我找出记忆断点。"},
            {"id": "contrast", "label": "比较两个容易混淆的解法", "detail": "通过对比发现什么时候该换方法。", "prompt": f"开始复盘「{title}」。请给我两个容易混淆的解法，让我判断它们的适用边界。"},
            {"id": "teach", "label": "把它讲给新人听", "detail": "用最简单的语言重建概念。", "prompt": f"开始复盘「{title}」。请让我像教新人一样解释它，并在关键处追问。"},
        ]
    else:
        setting = f"任务场景 {ordinal}：你正在把「{title}」转化成一次可以完成和验证的行动。"
        objective = "先做出一个关键选择，再通过一个小挑战证明你真的理解了。"
        choices = [
            {"id": "observe", "label": "先观察案例", "detail": "从资料里的具体例子找出规律。", "prompt": f"我选择观察案例。请围绕「{title}」从当前资料中给我一个案例，并让我找出关键规律。"},
            {"id": "explain", "label": "先说出我的理解", "detail": "不抄资料，先让导师听到你的模型。", "prompt": f"我选择先解释。请让我用自己的话说明「{title}」，然后根据资料证据追问我。"},
            {"id": "challenge", "label": "先接受小挑战", "detail": "马上做一个最小练习，再补齐概念。", "prompt": f"我选择先接受挑战。请给我一个关于「{title}」的最小练习，不要直接给答案。"},
        ]
    return {"type": "branching_learning_scene", "episode": f"EP.{ordinal:02d}", "title": f"第 {ordinal} 幕 · {title}", "setting": setting, "objective": objective, "location": preset.get("world", {}).get("title", "夜航学习剧场"), "knowledge_focus": title, "character": character, "choices": choices}


def _build_knowledge_map(lessons: list[dict], chunks: list[dict]) -> dict:
    """Return a deterministic, evidence-linked map for the teaching UI."""
    nodes = []
    edges = []
    for index, lesson in enumerate(lessons):
        refs = lesson.get("blocks", [{}])[0].get("evidence_refs", [])
        nodes.append({"id": lesson["id"], "label": lesson["title"], "kind": "lesson", "ordinal": index + 1, "source_refs": refs})
        if index:
            edges.append({"from": lessons[index - 1]["id"], "to": lesson["id"], "relation": "prerequisite"})
    return {"version": 1, "nodes": nodes, "edges": edges, "source_chunk_count": len(chunks), "generated_at": db.now()}


def build_plan(workspace_id: str, goal: dict, assessment: dict) -> dict:
    sources = db.rows("sources", "workspace_id", workspace_id)
    chunks = []
    for source in sources:
        chunks.extend(db.rows("chunks", "source_id", source["id"]))
    chunks = chunks[:12]
    version = 1
    existing = db.rows("plans", "workspace_id", workspace_id)
    if existing:
        version = max(json.loads(p["payload"]).get("version", 0) for p in existing) + 1
    plan_id = uid("plan")
    module_count = max(1, min(4, (len(chunks) + 2) // 3))
    milestones, lessons = [], []
    for m in range(module_count):
        milestone_id, module_id = uid("milestone"), uid("module")
        milestone = {"id": milestone_id, "ordinal": m + 1, "title": ["建立地图", "刻意练习", "综合应用", "最终交付"][min(m, 3)],
                     "objective": f"掌握与目标相关的第 {m+1} 个能力层级", "status": "not_started", "modules": []}
        module = {"id": module_id, "ordinal": 1, "title": f"模块 {m+1}", "objective": "从证据出发形成可执行能力", "lessons": []}
        for j in range(2 if m < module_count - 1 else 1):
            lesson_id = uid("lesson")
            evidence = chunks[(m * 2 + j) % len(chunks)] if chunks else None
            title = (evidence.get("heading") or f"核心练习 {m+1}.{j+1}") if evidence else f"核心练习 {m+1}.{j+1}"
            lesson = {"id": lesson_id, "ordinal": j + 1, "title": title[:80], "objective": "解释关键概念并完成一次主动回忆", "lesson_kind": "guided_practice", "estimated_minutes": min(45, max(20, goal["minutes_per_day"])), "completion_criteria": "用自己的话回答检查问题并完成练习", "mastery_threshold": .75, "status": "not_started", "blocks": []}
            for k, block_type in enumerate(["explain", "example", "question", "practice", "checkpoint"]):
                block_text = {"explain": f"本节聚焦：{title}。先建立概念地图，再连接到目标。", "example": "观察资料中的一个具体例子，指出它支持的结论。", "question": "请用自己的话说明：这个知识点为什么重要？", "practice": "完成一个最小练习，并记录你做出的关键判断。", "checkpoint": "检查：你能否在不看资料时复述核心步骤？"}[block_type]
                block = {"id": uid("block"), "lesson_id": lesson_id, "ordinal": k + 1, "block_type": block_type, "content": block_text, "expected_response": "清晰、可核验的回答或产出", "completion_rule": "用户提交回答后由规则与 Tutor 共同检查", "evaluator_rubric": {"evidence": True, "clarity": True}, "evidence_refs": ([{"source_id": evidence["source_id"], "chunk_id": evidence["id"], "source_title": next((s["title"] for s in sources if s["id"] == evidence["source_id"]), "资料"), "page_start": evidence.get("page"), "page_end": evidence.get("page"), "quote": evidence["text"][:180]}] if evidence else []), "estimated_minutes": max(4, lesson["estimated_minutes"] // 5)}
                if evidence: block["content"] += "\n\nEvidence anchor: " + evidence["text"][:180]
                lesson["blocks"].append(block)
            module["lessons"].append(lesson); lessons.append(lesson)
        milestone["modules"].append(module); milestones.append(milestone)
    plan_start = date.fromisoformat(goal.get("start_date") or date.today().isoformat())
    days = available_days(plan_start, date.fromisoformat(goal["deadline"]), goal["days_per_week"], set(goal.get("unavailable_dates", [])), goal.get("preferred_weekdays"))
    study_days = []
    task_index = 0
    for idx, day in enumerate(days):
        tasks = []
        if idx == len(days) - 2:
            tasks.append({"id": uid("task"), "study_day_id": "", "lesson_id": "", "title": "复习队列：回顾薄弱知识点", "task_type": "review", "estimated_minutes": min(30, goal["minutes_per_day"]), "priority": "high", "completion_criteria": "完成错题复述", "scene": scene_for_task("薄弱知识点复盘", "review", idx + 1), "status": "not_started"})
        elif idx == len(days) - 1:
            tasks.append({"id": uid("task"), "study_day_id": "", "lesson_id": "", "title": "最终任务：提交可核验成果", "task_type": "final_task", "estimated_minutes": min(45, goal["minutes_per_day"]), "priority": "high", "completion_criteria": goal["expected_output"], "scene": scene_for_task(goal["expected_output"], "final_task", idx + 1), "status": "not_started"})
        elif task_index < len(lessons):
            lesson = lessons[task_index]
            tasks.append({"id": uid("task"), "study_day_id": "", "lesson_id": lesson["id"], "title": f"学习：{lesson['title']}", "task_type": "lesson", "estimated_minutes": lesson["estimated_minutes"], "priority": "high" if idx < 3 else "normal", "completion_criteria": lesson["completion_criteria"], "scene": scene_for_task(lesson["title"], "lesson", idx + 1), "status": "not_started"})
            task_index += 1
        else:
            practice_title = f"剧情支线：{goal['goal_statement'][:24]} · 主动回忆"
            tasks.append({"id": uid("task"), "study_day_id": "", "lesson_id": "", "title": practice_title, "task_type": "practice", "estimated_minutes": min(30, goal["minutes_per_day"]), "priority": "normal", "completion_criteria": "用自己的话复述一个关键点，并给出一个新例子", "scene": scene_for_task(practice_title, "practice", idx + 1), "status": "not_started"})
        if tasks: study_days.append({"id": uid("day"), "date": day.isoformat(), "planned_minutes": goal["minutes_per_day"], "carry_over_minutes": 0, "status": "planned", "tasks": tasks})
    # Replace the legacy one-pass ordering with an explicit first-pass and
    # rolling-review schedule. Keeping this transformation here preserves the
    # existing lesson/block generation and ORM compatibility.
    total_days = len(study_days)
    first_pass_days = max(1, min(total_days, math.ceil(total_days * 0.4))) if total_days else 1
    final_day_index = total_days - 1 if total_days >= 3 else None
    first_pass_end = min(first_pass_days, final_day_index if final_day_index is not None else total_days)
    review_strategies = ["spaced_recall", "mixed_review", "transfer_test"]
    task_index = 0
    for idx, day_payload in enumerate(study_days):
        if final_day_index is not None and idx == final_day_index:
            task = {"id": uid("task"), "study_day_id": day_payload["id"], "lesson_id": "", "title": "Final assessment · submit a verifiable result", "task_type": "final_task", "estimated_minutes": min(45, goal["minutes_per_day"]), "priority": "high", "completion_criteria": goal["expected_output"], "scene": scene_for_task(goal["expected_output"], "final_task", idx + 1), "status": "not_started", "learning_pass": "final_assessment"}
            day_payload["tasks"] = [task]
            continue
        if idx < first_pass_end and task_index < len(lessons):
            lesson = lessons[task_index]
            day_payload["tasks"] = [{"id": uid("task"), "study_day_id": day_payload["id"], "lesson_id": lesson["id"], "title": f"Learn: {lesson['title']}", "task_type": "lesson", "estimated_minutes": lesson["estimated_minutes"], "priority": "high" if idx < 3 else "normal", "completion_criteria": lesson["completion_criteria"], "scene": scene_for_task(lesson["title"], "lesson", idx + 1), "status": "not_started", "learning_pass": "first_pass"}]
            task_index += 1
        elif idx >= first_pass_end:
            strategy = review_strategies[(idx - first_pass_end) % len(review_strategies)]
            review_lesson = lessons[(idx - first_pass_end) % len(lessons)] if lessons else None
            title = f"Rolling review · {strategy} · {(review_lesson or {}).get('title', goal['goal_statement'][:20])}"
            day_payload["tasks"] = [{"id": uid("task"), "study_day_id": day_payload["id"], "lesson_id": (review_lesson or {}).get("id", ""), "title": title, "task_type": "review", "review_strategy": strategy, "estimated_minutes": min(30, goal["minutes_per_day"]), "priority": "high", "completion_criteria": "Recall the key steps, then transfer them to a new example.", "scene": scene_for_task(title, "review", idx + 1), "status": "not_started", "learning_pass": "rolling_review"}]
    plan = {"id": plan_id, "workspace_id": workspace_id, "version": version, "title": f"{goal['goal_statement'][:30]} · 个性化路线", "goal": goal["goal_statement"], "start_date": goal.get("start_date") or plan_start.isoformat(), "deadline": goal["deadline"], "status": "draft", "rationale": "先建立知识地图，再进行主动回忆和综合产出；最后两天保留复习与交付缓冲。", "risk_summary": assessment["risks"], "source_coverage": len(chunks), "source_coverage_rate": assessment.get("source_sufficiency", 0), "milestones": milestones, "study_days": study_days, "success_criteria": goal.get("success_criteria") or ["完成最终任务并提交可核验成果"], "created_at": db.now()}
    with db.connection() as conn:
        conn.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?)", (plan_id, workspace_id, version, json.dumps(plan, ensure_ascii=False), "draft", plan["created_at"], ""))
        for day in study_days:
            conn.execute("INSERT INTO study_days VALUES (?,?,?,?)", (day["id"], plan_id, day["date"], json.dumps(day, ensure_ascii=False)))
            for task in day["tasks"]:
                task["study_day_id"] = day["id"]
                conn.execute("INSERT INTO tasks VALUES (?,?,?,?)", (task["id"], day["id"], task["lesson_id"], json.dumps(task, ensure_ascii=False)))
    plan["allocation"] = {"first_pass_ratio": 0.4, "first_pass_days": first_pass_end, "first_pass_minutes": sum(int(day.get("planned_minutes", 0)) for day in study_days[:first_pass_end]), "review_days": max(0, total_days - first_pass_end - (1 if final_day_index is not None else 0)), "review_minutes": sum(int(day.get("planned_minutes", 0)) for day in study_days[first_pass_end:(final_day_index if final_day_index is not None else total_days)]), "final_assessment_days": 1 if final_day_index is not None else 0, "review_strategy": "rolling_spaced_review"}
    plan["knowledge_map"] = _build_knowledge_map(lessons, chunks)
    plan["rationale"] = "首轮学习约占可用学习日的 40%，之后按间隔回忆、混合复习和迁移练习滚动巩固，最后保留一次综合验收。"
    with db.connection() as conn:
        conn.execute("UPDATE plans SET payload=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), plan_id))
    unscheduled_lessons=lessons[task_index:]
    plan["capacity_status"]="insufficient" if unscheduled_lessons else "sufficient"
    plan["unscheduled_minutes"]=sum(x["estimated_minutes"] for x in unscheduled_lessons)
    plan["unscheduled_lessons"]=[{"id":x["id"],"title":x["title"]} for x in unscheduled_lessons]
    plan["options"]=assessment.get("options",[])
    with db.connection() as conn:
        conn.execute("UPDATE plans SET payload=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), plan_id))
    try:
        from .orm import SessionLocal, PlanORM, MilestoneORM, ModuleORM, LessonORM, BlockORM, DayORM, TaskORM, ProgressORM, init_orm
        init_orm()
        with SessionLocal() as session:
            orm_plan=PlanORM(id=plan_id,workspace_id=workspace_id,version=version,title=plan["title"],status="draft",deadline=date.fromisoformat(plan["deadline"]),capacity_status=plan["capacity_status"],scheduled_minutes=sum(d["planned_minutes"] for d in study_days),unscheduled_minutes=plan["unscheduled_minutes"],diff={})
            session.add(orm_plan)
            for milestone in milestones:
                orm_milestone=MilestoneORM(id=milestone["id"],plan_id=plan_id,ordinal=milestone["ordinal"],title=milestone["title"],objective=milestone["objective"]); session.add(orm_milestone)
                for module in milestone["modules"]:
                    orm_module=ModuleORM(id=module["id"],milestone_id=milestone["id"],ordinal=module["ordinal"],title=module["title"],objective=module["objective"]); session.add(orm_module)
                    for lesson in module["lessons"]:
                        orm_lesson=LessonORM(id=lesson["id"],module_id=module["id"],ordinal=lesson["ordinal"],title=lesson["title"],objective=lesson["objective"],estimated_minutes=lesson["estimated_minutes"],completion_criteria=lesson["completion_criteria"]); session.add(orm_lesson); session.add(ProgressORM(id=f"progress_{lesson['id']}",workspace_id=workspace_id,lesson_id=lesson["id"],mastery=0,attempts=0,independent_attempts=0,hint_attempts=0))
                        for block in lesson["blocks"]:
                            session.add(BlockORM(id=block["id"],lesson_id=lesson["id"],ordinal=block["ordinal"],block_type=block["block_type"],content=block["content"],rubric=block.get("evaluator_rubric",{}),evidence_chunk_ids=[x.get("chunk_id") for x in block.get("evidence_refs",[])]))
            for day in study_days:
                orm_day=DayORM(id=day["id"],plan_id=plan_id,day=date.fromisoformat(day["date"]),planned_minutes=day["planned_minutes"]); session.add(orm_day)
                for task in day["tasks"]:
                    session.add(TaskORM(id=task["id"],day_id=day["id"],lesson_id=task.get("lesson_id") or None,title=task["title"],task_type=task["task_type"],estimated_minutes=task["estimated_minutes"],status=task.get("status","not_started")))
            session.commit()
    except Exception:
        pass
    return plan


def validate_citations(workspace_id: str, citations: list[dict]) -> tuple[list[dict], list[str]]:
    valid, warnings = [], []
    sources = {s["id"]: s for s in db.rows("sources", "workspace_id", workspace_id)}
    for c in citations:
        chunk = db.row("chunks", "id", c.get("chunk_id", ""))
        page_ok=bool(chunk) and ((c.get("page") == chunk.get("page")) if "page" in c else (c.get("page_start") in {None,chunk.get("page")}))
        if not chunk or chunk["source_id"] not in sources or c.get("source_id") != chunk["source_id"] or not page_ok or normalize(c.get("quote", "")) not in chunk["normalized_text"]:
            warnings.append("检测到无效引用，已从回答中移除")
        else: valid.append(c)
    return valid, warnings


def _teaching_scaffold(lesson: dict, persona_prompt: str = "", phase: str = "explanation") -> dict:
    title = str(lesson.get("title") or "当前知识点")
    if phase == "explanation":
        return {"step": "explain", "label": "先建立直觉", "analogy": f"把「{title}」想成整理一只抽屉：先分区，再决定每件东西放哪儿。", "example": f"我会先用资料里的一个小例子拆解「{title}」，标出输入、关键判断和结果。", "prompt": "先不用答题，只告诉我：这个类比里哪一步最像当前知识点？"}
    if phase == "worked_example":
        return {"step": "worked_example", "label": "老师示范一遍", "analogy": "像一起看导航，不要求你马上独立开车。", "example": f"我先演示「{title}」的最小例子，再把最后一个判断留给你。", "prompt": "请指出示例中最关键的一个判断，并说说为什么。"}
    if phase in {"guided_practice", "faded_guidance"}:
        return {"step": "guided_practice", "label": "带着你做", "analogy": "像扶着自行车后座逐渐松手，卡住时再扶一下。", "example": f"围绕「{title}」完成一个更小的变体：先写目标，再写第一步。", "prompt": "先写出目标和第一步，剩下的我帮你检查。"}
    if phase == "spaced_recall":
        return {"step": "spaced_recall", "label": "先从记忆里找回来", "analogy": "像把暂时放回抽屉的工具重新摸出来，不看说明书也先试着说说用途。", "example": f"关于「{title}」，先回忆关键步骤，再对照资料补缺口。", "prompt": "先说出你还记得的一个关键步骤，不要求完整。"}
    if phase == "mixed_review":
        return {"step": "mixed_review", "label": "混合旧新知识", "analogy": "像把两道看起来相似的菜放在一起辨味道，重点是说清楚什么时候用哪一种。", "example": f"把「{title}」和最近学过的另一个概念放在一起比较。", "prompt": "说出两者最容易混淆的地方，以及一个区分办法。"}
    if phase == "transfer_test":
        return {"step": "transfer_test", "label": "迁移到新场景", "analogy": "换一条路也能到目的地，才算真的会了，不是只记住原路线。", "example": f"把「{title}」用到一个资料中没有原样出现的新例子。", "prompt": "先描述新场景和你会保留的关键判断。"}
    if phase == "final_assessment":
        return {"step": "final_assessment", "label": "最后综合验收", "analogy": "像把工具箱带到真实现场，先检查条件，再选择工具。", "example": f"综合使用「{title}」完成最终产出，先写计划再动手。", "prompt": "先列出你的判断依据和执行步骤，答案可以稍后提交。"}
    return {"step": "independent", "label": "独立尝试", "analogy": "现在轮到你自己走一小段，我只在必要时递提示。", "example": f"请独立完成「{title}」的变体，并附上一个可验证的理由。", "prompt": "提交你的思路、步骤和证据；不确定也可以先交草稿。"}


def tutor(workspace_id: str, lesson_id: str, message: str, session_id: str | None = None, phase: str = "explanation", teacher: dict | None = None) -> dict:
    from .prompt_loader import load_prompt
    tutor_prompt = load_prompt("tutor.md")
    persona_prompt = str((db.row("workspaces", "id", workspace_id) or {}).get("persona_prompt") or "")
    plan_rows = db.rows("plans", "workspace_id", workspace_id)
    plan = json.loads(plan_rows[-1]["payload"]) if plan_rows else {}
    lesson = next((l for m in plan.get("milestones", []) for mod in m["modules"] for l in mod["lessons"] if l["id"] == lesson_id), None)
    if not lesson:
        return {"message": "当前课程尚未找到，请先从今日任务进入。", "status": "insufficient", "confidence": .2, "citations": [], "warnings": ["没有当前课程证据"]}
    try:
        from .retrieval_service import RetrievalService
        hits=RetrievalService().search(workspace_id,message,top_k=4)
    except Exception:
        hits=[]
    if not hits or max((x.get("score",0) for x in hits),default=0)<=0:
        scaffold = _teaching_scaffold(lesson, persona_prompt, phase)
        scaffold["persona_instructions"] = persona_prompt[:200]
        if teacher:
            scaffold["character_hook"] = f"{teacher.get('name', '今日导师')}的口头禅：{teacher.get('catchphrase') or teacher.get('specialty') or teacher.get('role') or '把复杂问题拆成小步骤'}。"
            scaffold["example"] = f"{scaffold['character_hook']}\n{scaffold['example']}"
        return {"message": f"我先不要求你答题。把「{lesson['title']}」想成一个日常动作：{scaffold['analogy']}\n\n{scaffold['example']}\n\n{scaffold['prompt']}（资料证据还不够，我会先陪你建立直觉。）", "status": "insufficient", "confidence": .15, "citations": [], "suggested_next_action": {"type": "scaffold", "text": scaffold["prompt"]}, "scaffold": scaffold, "persona_applied": bool(persona_prompt), "progress_update": {"lesson_id": lesson_id, "mastery_delta": 0}, "warnings": ["当前问题还没有命中可引用证据"]}
        return {"message":"当前问题在已导入资料中没有足够证据，我不会把无关资料冒充答案。你可以补充资料或缩小问题。","status":"insufficient","confidence":.15,"citations":[],"suggested_next_action":{"type":"question","text":"请提出一个能在当前资料中核验的问题。"},"progress_update":{"lesson_id":lesson_id,"mastery_delta":0},"warnings":["检索没有找到相关证据"]}
    citations=[{"source_id":x["source_id"],"chunk_id":x["chunk_id"],"source_title":x["source_title"],"page_start":x.get("page_start"),"page_end":x.get("page_end"),"heading":x.get("heading",""),"quote":x["quote"]} for x in hits]
    from .grounding_service import critic as grounding_critic
    critic_result = grounding_critic(workspace_id, citations, {x.get("chunk_id") for x in citations})
    valid = citations if critic_result["passed"] else []
    warnings = [] if critic_result["passed"] else critic_result["errors"]
    status = "grounded" if valid else "insufficient"
    response = {"message": f"{message[:120]}\n\n我们先把问题拆成一个可验证动作：请结合「{lesson['title']}」用自己的话回答课程检查问题。回答后我会按证据、清晰度和是否能独立完成来反馈。", "status": status, "confidence": .86 if valid else .35, "citations": valid, "suggested_next_action": {"type": "question", "text": "请用自己的话解释关键概念，并给出一个例子。"}, "progress_update": {"lesson_id": lesson_id, "mastery_delta": .05}, "warnings": warnings, "prompt_loaded": bool(tutor_prompt)}
    scaffold = _teaching_scaffold(lesson, persona_prompt, phase)
    scaffold["persona_instructions"] = persona_prompt[:200]
    if teacher:
        scaffold["character_hook"] = f"{teacher.get('name', '今日导师')}的口头禅：{teacher.get('catchphrase') or teacher.get('specialty') or teacher.get('role') or '把复杂问题拆成小步骤'}。"
        scaffold["example"] = f"{scaffold['character_hook']}\n{scaffold['example']}"
    if valid:
        evidence_quote = str(valid[0].get("quote") or "").strip()[:180]
        if evidence_quote:
            scaffold["evidence_example"] = evidence_quote
            scaffold["example"] = f"资料里的小例子：{evidence_quote}\n{scaffold['example']}"
    response["scaffold"] = scaffold
    response["persona_applied"] = bool(persona_prompt)
    response["suggested_next_action"] = {"type": "scaffold", "text": scaffold["prompt"]}
    response["message"] = f"{message[:120]}\n\n先别急着答题，我先把「{lesson['title']}」讲成一个直觉：{scaffold['analogy']}\n\n{scaffold['example']}\n\n{scaffold['prompt']}（我会按资料证据陪你一步步来，别想偷跳关，嘿。）"
    return response

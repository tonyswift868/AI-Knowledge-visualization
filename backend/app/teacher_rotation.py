from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .orm import TeacherMemoryORM
from .teacher_avatar_registry import list_models as list_teacher_avatar_models


# Adult teaching personas. Visual identity is deliberately absent here: a
# licensed local/online avatar registry binds VRM/Live2D models independently.
TEACHER_ROSTER: tuple[dict[str, Any], ...] = (
    {
        "id": "mio_kisaragi",
        "name": "米欧老师",
        "gender": "woman",
        "adult_age": 32,
        "title": "概念与结构导师",
        "specialty": "把复杂概念拆成可验证的小步骤",
        "role": "温柔地追问证据，并陪你把想法说完整",
        "tone": "calm_structured",
        "color": "#b69cff",
        "adult_only": True,
        "story_mode": False,
    },
    {
        "id": "lin_qinghe",
        "name": "林清禾老师",
        "gender": "woman",
        "adult_age": 29,
        "title": "实践与迁移导师",
        "specialty": "用例子和反例检查是否真正理解",
        "role": "先接住你的问题，再带回今天的课程目标",
        "tone": "warm_practical",
        "color": "#78d6b0",
        "adult_only": True,
        "story_mode": False,
    },
    {
        "id": "su_ning",
        "name": "苏宁老师",
        "gender": "woman",
        "adult_age": 35,
        "title": "记忆与复习导师",
        "specialty": "主动回忆、间隔复习与错因整理",
        "role": "记住你学过什么，让不同老师能接着教",
        "tone": "gentle_reflective",
        "color": "#f0b879",
        "adult_only": True,
        "story_mode": False,
    },
    {
        "id": "qiao_yan",
        "name": "乔妍老师",
        "gender": "woman",
        "adult_age": 31,
        "title": "推理与表达导师",
        "specialty": "澄清前提、组织论证与准确表达",
        "role": "用一个小问题帮助你发现推理缺口",
        "tone": "precise_kind",
        "color": "#79b9ee",
        "adult_only": True,
        "story_mode": False,
    },
)

CATCHPHRASES = {
    "mio_kisaragi": "复杂不是敌人，没拆开的复杂才是。",
    "lin_qinghe": "先拿生活里的例子试跑一遍。",
    "su_ning": "今天忘了没关系，明天让它自己回来。",
    "qiao_yan": "先问边界，再谈答案。",
}


def teacher_for_day(workspace_id: str, on_date: date | None = None, avatar_ids: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    """Rotate the teaching persona and optional registry avatar independently."""
    day = on_date or date.today()
    seed = int(hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:8], 16)
    teacher = copy.deepcopy(TEACHER_ROSTER[(seed + day.toordinal()) % len(TEACHER_ROSTER)])
    teacher["catchphrase"] = CATCHPHRASES.get(teacher.get("id"), "先把复杂问题拆成一小步。")
    registry_items = list_teacher_avatar_models().get("items", [])
    registry_by_id = {str(item.get("model_id")): item for item in registry_items}
    available_avatars = [str(item).strip() for item in (avatar_ids or registry_by_id.keys()) if str(item).strip()]
    teacher["avatar_id"] = available_avatars[(seed * 7 + day.toordinal()) % len(available_avatars)] if available_avatars else None
    avatar = registry_by_id.get(str(teacher["avatar_id"]))
    if avatar:
        teacher["persona_name"] = teacher["name"]
        teacher["name"] = str(avatar.get("display_name") or teacher["name"])
        teacher["avatar_model_url"] = avatar.get("model_url")
        teacher["avatar_creator"] = avatar.get("creator")
        teacher["avatar_license"] = avatar.get("license_name")
        teacher["avatar_morph"] = avatar.get("morph")
    teacher["avatar_source"] = "avatar_registry"
    teacher["avatar_selection_key"] = f"{workspace_id}:{day.isoformat()}"
    teacher["rotation_date"] = day.isoformat()
    teacher["rotation_policy"] = "daily_workspace_v1"
    return teacher


def _empty_memory() -> dict[str, Any]:
    return {
        "recent_questions": [],
        "recent_focus": [],
        "cited_source_ids": [],
        "cited_chunk_ids": [],
        "weak_points": [],
        "quiz_history": [],
        "pending_quizzes": {},
        "interaction_count": 0,
    }


def memory_row(session: Session, workspace_id: str) -> TeacherMemoryORM:
    row = session.get(TeacherMemoryORM, workspace_id)
    if row is None:
        row = TeacherMemoryORM(workspace_id=workspace_id, memory=_empty_memory())
        session.add(row)
        session.flush()
    return row


def public_memory(memory: dict[str, Any] | None) -> dict[str, Any]:
    value = memory or _empty_memory()
    return {
        "interaction_count": int(value.get("interaction_count", 0)),
        "recent_focus": list(value.get("recent_focus", []))[-5:],
        "weak_points": list(value.get("weak_points", []))[-5:],
        "known_source_count": len(set(value.get("cited_source_ids", []))),
        "completed_quiz_count": len(value.get("quiz_history", [])),
    }


def _bounded_unique(items: list[Any], incoming: list[Any], limit: int) -> list[Any]:
    result: list[Any] = []
    for item in [*items, *incoming]:
        if item not in result:
            result.append(item)
    return result[-limit:]


def build_quiz(run_id: str, citations: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not citations:
        return None, None
    evidence = citations[0]
    quote = str(evidence.get("quote") or "").strip()
    if not quote:
        return None, None
    supported = quote[:180]
    quiz_id = f"quiz_{uuid.uuid4().hex[:20]}"
    public = {
        "quiz_id": quiz_id,
        "kind": "single_choice",
        "question": "下面哪一项是当前课程资料直接支持的内容？",
        "options": [
            {"id": "supported", "text": supported},
            {"id": "opposite", "text": "资料明确表达了与上述片段相反的结论。"},
            {"id": "absent", "text": "资料中完全没有涉及这个主题。"},
        ],
        "citation": copy.deepcopy(evidence),
    }
    private = {**copy.deepcopy(public), "run_id": run_id, "correct_option_id": "supported"}
    return public, private


def enrich_tutor_result(
    session: Session,
    *,
    workspace_id: str,
    run_id: str,
    lesson_title: str,
    teacher: dict[str, Any],
    user_message: str,
    result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Attach persona/memory/quiz without changing validated citation fields."""
    row = memory_row(session, workspace_id)
    memory = copy.deepcopy(row.memory or _empty_memory())
    citations = list(result.get("citations") or [])
    grounded = result.get("status") == "grounded" and bool(citations)
    has_scaffold = bool(result.get("scaffold"))
    if grounded:
        original = str(result.get("message") or "")
        result["message"] = f"我是今天轮值的{teacher['name']}。我们慢慢来。\n\n{original}"
    elif not has_scaffold:
        result["message"] = (
            f"我是今天轮值的{teacher['name']}。这个问题很有意思，不过当前导入的课程资料还不足以支持直接回答。"
            f"我们先回到「{lesson_title or '当前课程'}」：你愿意先说说自己已经确认的一点吗？"
        )
        result["status"] = "off_topic_redirect"
        result["citations"] = []
        result["confidence"] = min(float(result.get("confidence", 0.15)), 0.2)
        result["suggested_next_action"] = {"type": "question", "text": f"用一句话复述「{lesson_title or '当前课程'}」的一个关键点。"}

    else:
        # Preserve a first-turn explanation when retrieval is still empty.
        result["message"] = f"鎴戞槸浠婂ぉ杞€肩殑{teacher['name']}銆傚厛涓嶈鎬ュ洖绛旓紝鎴戜滑鍏堝缓涓€涓洿瑙夈€俓n\n{result.get('message', '')}"

    public_quiz, private_quiz = build_quiz(run_id, list(result.get("citations") or []))
    if result.get("suppress_quiz"):
        public_quiz, private_quiz = None, None
    if private_quiz:
        pending = dict(memory.get("pending_quizzes") or {})
        pending[private_quiz["quiz_id"]] = private_quiz
        memory["pending_quizzes"] = dict(list(pending.items())[-20:])

    focus = [lesson_title] if lesson_title else []
    focus += [str(item.get("heading") or item.get("source_title") or "") for item in citations]
    memory["recent_questions"] = [*list(memory.get("recent_questions", [])), user_message[:240]][-12:]
    memory["recent_focus"] = _bounded_unique(list(memory.get("recent_focus", [])), [x for x in focus if x], 12)
    memory["cited_source_ids"] = _bounded_unique(list(memory.get("cited_source_ids", [])), [x.get("source_id") for x in citations if x.get("source_id")], 80)
    memory["cited_chunk_ids"] = _bounded_unique(list(memory.get("cited_chunk_ids", [])), [x.get("chunk_id") for x in citations if x.get("chunk_id")], 160)
    if not grounded and lesson_title:
        memory["weak_points"] = _bounded_unique(list(memory.get("weak_points", [])), [lesson_title], 12)
    memory["interaction_count"] = int(memory.get("interaction_count", 0)) + 1
    memory["last_teacher_id"] = teacher["id"]
    memory["updated_at"] = datetime.now(timezone.utc).isoformat()
    row.memory = memory
    row.revision += 1
    row.updated_at = datetime.now(timezone.utc)

    result["teacher"] = copy.deepcopy(teacher)
    result["memory_snapshot"] = public_memory(memory)
    result["quick_quiz"] = public_quiz
    return result, public_memory(memory)


class QuizError(ValueError):
    pass


def grade_quiz(session: Session, *, workspace_id: str, run_id: str, quiz_id: str, option_id: str) -> dict[str, Any]:
    row = memory_row(session, workspace_id)
    memory = copy.deepcopy(row.memory or _empty_memory())
    pending = dict(memory.get("pending_quizzes") or {})
    quiz = pending.get(quiz_id)
    if not quiz or quiz.get("run_id") != run_id:
        raise QuizError("QUIZ_NOT_FOUND")
    correct = option_id == quiz["correct_option_id"]
    pending.pop(quiz_id, None)
    memory["pending_quizzes"] = pending
    memory["quiz_history"] = [
        *list(memory.get("quiz_history", [])),
        {"quiz_id": quiz_id, "run_id": run_id, "correct": correct, "answered_at": datetime.now(timezone.utc).isoformat()},
    ][-40:]
    row.memory = memory
    row.revision += 1
    row.updated_at = datetime.now(timezone.utc)
    return {
        "quiz_id": quiz_id,
        "correct": correct,
        "correct_option_id": quiz["correct_option_id"],
        "feedback": "答对了，这一项可以由资料片段直接核验。" if correct else "再看一眼引用片段：要选资料直接表达、而不是推测出来的内容。",
        "citation": quiz["citation"],
        "memory_snapshot": public_memory(memory),
    }

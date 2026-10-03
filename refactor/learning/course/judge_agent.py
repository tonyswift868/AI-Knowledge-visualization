"""Shared agent JSON validation and persisted host verdicts. No AI grading."""
from dataclasses import dataclass
import json
import re
from .planner_agents import AgentFormatError


@dataclass(frozen=True)
class Verdict:
    kind: str
    correct: bool | None
    wrong_points: tuple[str, ...]
    reason: str
    uncertain: bool = False
    checks: tuple[dict, ...] = ()
    review: dict | None = None



def _text(value, name, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise AgentFormatError(f"{name}: 文本为空、类型错误或超过长度限制")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise AgentFormatError(f"{name}: 文本含控制字符")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AgentFormatError(f"{name}: 文本含无效 Unicode 字符") from None
    return value



def _identifier(value):
    value = _text(value, "id", 256)
    if value != value.strip() or any(c.isspace() for c in value):
        raise AgentFormatError("id: 标识不能含空白")
    return value



def _ids(value, allowed, *, empty=False):
    if not isinstance(value, list) or len(value) > len(allowed) or (not empty and not value):
        raise AgentFormatError("ids: 标识列表为空或无效")
    result = tuple(_identifier(item) for item in value)
    if len(set(result)) != len(result) or any(item not in allowed for item in result):
        raise AgentFormatError("ids: 标识重复或不在本次输入内")
    return result



def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise AgentFormatError("fields: 字段缺失或包含未约定字段")
    return value



def _json_object(answer):
    """Accept one object (optionally one JSON fence), never pick a nested object.

    In particular, do not silently accept duplicate keys, NaN, a second answer,
    or prose surrounding a rejected answer. Errors never echo model content.
    """
    text = _text(answer, "answer", 256_000).strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.S | re.I)
        if not match:
            raise AgentFormatError("json: JSON 围栏格式无效")
        text = match[1]

    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise AgentFormatError("json: 字段重复")
            obj[key] = value
        return obj

    def constant(_value):
        raise AgentFormatError("json: 不允许非有限数值")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        raise AgentFormatError("json: 需要单个完整 JSON 对象") from None
    if not isinstance(value, dict):
        raise AgentFormatError("json: 顶层必须为对象")
    return value



def _dump(value):
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise AgentFormatError("context: 需要可序列化的 JSON 数据") from None
    if len(text) > 500_000:
        raise AgentFormatError("context: 本次上下文过长")
    return text



def _points(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise AgentFormatError("points: 须提供 1–100 个知识点")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise AgentFormatError("points: 知识点须为对象")
        identifier = _identifier(item.get("id"))
        if identifier in seen:
            raise AgentFormatError("points: 知识点标识重复")
        seen.add(identifier)
        result.append(dict(id=identifier, title=_text(item.get("title"), "title", 500),
                           summary=_text(item.get("summary"), "summary", 8000, empty=True)))
    return result



def _excerpts(value):
    # Shared teacher context accepts records or a chunk_id -> text mapping.
    if isinstance(value, dict):
        value = [{"id": key, "text": text} for key, text in value.items()]
    if not isinstance(value, list) or not 1 <= len(value) <= 500:
        raise AgentFormatError("excerpts: 须提供相关资料原文")
    result = {}
    for item in value:
        if not isinstance(item, dict):
            raise AgentFormatError("excerpts: 片段须为对象")
        identifier = _identifier(item.get("id"))
        if identifier in result:
            raise AgentFormatError("excerpts: 片段标识重复")
        result[identifier] = _text(item.get("text"), "excerpt", 40_000)
    _dump(result)
    return result

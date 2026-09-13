"""Small, task-independent teacher companion chat.

Casual conversation deliberately lives outside learning sessions: it must not
change a plan, mastery state, or the teacher's course memory.  The local reply
path is intentionally synchronous and bounded so the companion still feels
alive when no provider is connected.
"""

import asyncio
import importlib
import json
import os
import re
import stat
import time
from pathlib import Path, PureWindowsPath
from .runtime_resources import backend_resource_root
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from . import db
from .provider_runtime import (
    create_verified_demo_session,
    generation_meta,
    provider_for_session,
    require_provider_session,
)
from .services import uid
from .minimax_teacher_voice import (
    MiniMaxTeacherVoiceError,
    minimax_teacher_voice_status,
    synthesize_minimax_teacher_voice,
)
from .teacher_voice_pack import (
    TeacherVoicePackError,
    error_detail as teacher_voice_pack_error_detail,
    get_teacher_voice_status,
    synthesize_active_teacher_voice,
    stream_active_teacher_voice,
)

router = APIRouter(prefix="/api/casual-chat", tags=["casual-chat"])

_CASUAL_PROMPT_FALLBACK = """你是学习平台里的下课闲聊教师“{{teacher_name}}”。
这是一个独立的角色聊天窗口，只服务于当前角色。
不要修改学习计划、知识点掌握度、复习安排或任何课程状态。
只使用当前角色的教师设定、用户补充设定和本角色当前聊天记录作为上下文；不要读取其他角色的对话。
回复要自然、连贯、简洁，先回应用户，再给一个自然的追问；不要重复开场白，不要冒充已经完成任何操作。

教师设定：
{{teacher_prompt}}

用户补充设定（本轮必须体现）：
{{extra_setting}}
"""


def _casual_prompt_path() -> Path:
    explicit = str(os.getenv("MENTORFLOW_CASUAL_CHAT_PROMPT") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (backend_resource_root() / "config" / "prompts" / "casual_chat_teacher_prompt.md").resolve()


def _casual_prompt_template() -> str:
    path = _casual_prompt_path()
    try:
        template = path.read_text(encoding="utf-8") if path.is_file() else _CASUAL_PROMPT_FALLBACK
    except OSError:
        template = _CASUAL_PROMPT_FALLBACK
    # Keep a malformed or accidentally huge edit from consuming the whole
    # provider context. The file is read per request, so edits take effect
    # without a backend restart.
    return template[:8000]

_LIVELY_REPLY_KINDS = {
    "teaching.encourage",
    "teaching.quiz_success",
    "teaching.celebrate",
    "teaching.transition",
    "after_class.small_talk",
    "after_class.greeting",
}

_MAX_TEACHER_VOICE_BYTES = 12 * 1024 * 1024
_MAX_TEACHER_VOICE_STREAM_CHARS = 2400
_MAX_LEGACY_VOICE_CONFIG_BYTES = 64 * 1024
_LEGACY_QWEN_PROTOCOL = "mentorflow.legacy-qwen3-tts-direct-loopback.v1"
_LEGACY_GPT_SOVITS_PROTOCOL = "mentorflow.legacy-gpt-sovits-direct-loopback.v1"
_LEGACY_QWEN_FIELDS = frozenset(
    {"schema_version", "protocol", "enabled", "api_url", "engine", "release_slot"}
)
_LEGACY_GPT_SOVITS_FIELDS = frozenset(
    {
        "schema_version",
        "protocol",
        "engine",
        "reference_audio_rights_confirmed",
        "api_url",
        "ref_audio_path",
        "prompt_lang",
        "prompt_text",
        "release_slot",
    }
)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_WINDOWS_REPARSE_POINT = 0x400
_LOCAL_WINDOWS_DRIVE_TYPES = frozenset({2, 3, 5, 6})
_WINDOWS_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_WINDOWS_RESERVED_SUPERSCRIPT_DIGITS = {"¹": "1", "²": "2", "³": "3"}


def _teacher_voice_provider() -> str:
    """Use an installed signed pack unless an operator opts into compatibility."""
    return str(os.getenv("MENTORFLOW_TEACHER_VOICE_PROVIDER") or "pack").strip().lower()


def _legacy_local_voice_allowed() -> bool:
    return str(os.getenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE") or "").strip().lower() in _TRUE_VALUES


def _legacy_voice_error(code: str, message: str, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": {
                "code": code,
                "message": message,
                "details": details,
            }
        },
    )


def _require_legacy_local_voice_opt_in() -> None:
    if _legacy_local_voice_allowed():
        return
    raise _legacy_voice_error(
        "VOICE_LEGACY_LOCAL_DISABLED",
        "旧版本地教师语音兼容链默认关闭",
        required_provider="local",
        required_flag="MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE=1",
    )


def _has_reparse_point(path: Path) -> bool:
    current = path
    while True:
        metadata = os.lstat(current)
        if stat.S_ISLNK(metadata.st_mode) or bool(
            int(getattr(metadata, "st_file_attributes", 0) or 0)
            & _WINDOWS_REPARSE_POINT
        ):
            return True
        if current.parent == current:
            return False
        current = current.parent


def _is_local_windows_drive(root: str) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_drive_type = kernel32.GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint
        return int(get_drive_type(root)) in _LOCAL_WINDOWS_DRIVE_TYPES
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _invalid_legacy_config_path(env_name: str, label: str) -> HTTPException:
    return _legacy_voice_error(
        "VOICE_LEGACY_CONFIG_PATH_INVALID",
        f"{label}配置必须使用本地盘符上的显式绝对普通文件路径",
        environment=env_name,
    )


def _canonical_windows_device_basename(component: str) -> str:
    """Return the Win32 device-comparison basename without broad Unicode folding."""
    basename = component.rstrip(" .").split(".", 1)[0].rstrip(" ").upper()
    if len(basename) == 4 and basename[:3] in {"COM", "LPT"}:
        official_digit = _WINDOWS_RESERVED_SUPERSCRIPT_DIGITS.get(basename[3])
        if official_digit is not None:
            return f"{basename[:3]}{official_digit}"
    return basename


def _validated_legacy_config_candidate(raw: str, env_name: str, label: str) -> Path:
    # Validate the operator-provided text before constructing or probing a path.
    # In particular, UNC/device paths must be rejected before any filesystem call
    # so the read-only warm route cannot accidentally issue an SMB request.
    if (
        not raw
        or "~" in raw
        or "\x00" in raw
        or any(ord(character) < 32 for character in raw)
        or any(character in raw for character in ("$", "%", "!"))
        or not _WINDOWS_DRIVE_ABSOLUTE.match(raw)
        or ":" in raw[2:]
    ):
        raise _invalid_legacy_config_path(env_name, label)

    lexical = PureWindowsPath(raw)
    components = lexical.parts[1:]
    for component in components:
        if component in {".", ".."} or component != component.rstrip(" ."):
            raise _invalid_legacy_config_path(env_name, label)
        device_name = _canonical_windows_device_basename(component)
        if device_name in _WINDOWS_RESERVED_DEVICE_NAMES:
            raise _invalid_legacy_config_path(env_name, label)

    drive_root = f"{raw[0].upper()}:\\"
    if not _is_local_windows_drive(drive_root):
        raise _invalid_legacy_config_path(env_name, label)
    return Path(raw)


def _explicit_legacy_config_path(env_name: str, label: str) -> Path | None:
    environment_value = os.getenv(env_name)
    if environment_value is None or environment_value == "":
        return None
    raw = str(environment_value)
    if raw != raw.strip():
        raise _invalid_legacy_config_path(env_name, label)
    candidate = _validated_legacy_config_candidate(raw, env_name, label)
    try:
        candidate_has_reparse_point = _has_reparse_point(candidate)
    except FileNotFoundError as exc:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_NOT_FOUND",
            f"找不到显式指定的{label}配置文件",
            environment=env_name,
        ) from exc
    except OSError as exc:
        raise _invalid_legacy_config_path(env_name, label) from exc
    if candidate_has_reparse_point:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_PATH_INVALID",
            f"{label}配置路径不能经过符号链接或重解析点",
            environment=env_name,
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_NOT_FOUND",
            f"找不到显式指定的{label}配置文件",
            environment=env_name,
        ) from exc
    try:
        resolved_has_reparse_point = _has_reparse_point(resolved)
        metadata = os.lstat(resolved)
    except OSError as exc:
        raise _invalid_legacy_config_path(env_name, label) from exc
    if resolved_has_reparse_point:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_PATH_INVALID",
            f"{label}配置路径不能经过符号链接或重解析点",
            environment=env_name,
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_PATH_INVALID",
            f"{label}配置路径不是普通文件",
            environment=env_name,
        )
    if metadata.st_size <= 0 or metadata.st_size > _MAX_LEGACY_VOICE_CONFIG_BYTES:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_INVALID",
            f"{label}配置文件为空或过大",
        )
    return resolved


def _read_legacy_config(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_LEGACY_VOICE_CONFIG_BYTES + 1)
    except OSError as exc:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_UNREADABLE",
            f"无法读取{label}配置文件",
        ) from exc
    if not raw or len(raw) > _MAX_LEGACY_VOICE_CONFIG_BYTES:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_INVALID",
            f"{label}配置文件为空或过大",
        )
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_INVALID",
            f"{label}配置不是有效 UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_INVALID",
            f"{label}配置必须是 JSON 对象",
        )
    return value


def _require_exact_legacy_schema(
    config: dict[str, Any],
    *,
    label: str,
    protocol: str,
    engine: str,
    fields: frozenset[str],
) -> None:
    if (
        type(config.get("schema_version")) is not int
        or config.get("schema_version") != 1
        or config.get("protocol") != protocol
        or config.get("engine") != engine
    ):
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_PROTOCOL_INVALID",
            f"{label}配置缺少精确的 legacy schema/protocol/engine 标识",
        )
    actual_fields = frozenset(config)
    if actual_fields != fields:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_FIELDS_INVALID",
            f"{label}配置字段集合不符合 legacy 协议",
            missing_fields=sorted(fields - actual_fields),
            unexpected_fields=sorted(actual_fields - fields),
        )


def _legacy_loopback_origin(value: Any, label: str) -> str:
    api_url = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(api_url)
        port = parsed.port
    except ValueError as exc:
        raise _legacy_voice_error(
            "VOICE_LEGACY_RUNTIME_URL_INVALID",
            f"{label}服务地址无效",
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise _legacy_voice_error(
            "VOICE_LEGACY_RUNTIME_URL_INVALID",
            f"{label}服务必须是带显式端口的纯 loopback HTTP origin",
        )
    return api_url


async def _synthesize_edge_teacher_voice(text: str, style: str) -> bytes:
    """Generate one on-demand MP3 without loading a local model.

    ``edge-tts`` is imported lazily so opening MentorFlow never starts or warms
    a voice runtime.  The request happens only after the learner clicks a
    listen button.
    """
    edge_tts = importlib.import_module("edge_tts")
    voice = str(os.getenv("MENTORFLOW_EDGE_TTS_VOICE") or "").strip()
    if not voice:
        raise RuntimeError("MENTORFLOW_EDGE_TTS_VOICE is required for explicit Edge compatibility")
    rate = "+2%" if style == "lively" else "-2%"
    communicator = edge_tts.Communicate(
        text,
        voice=voice,
        rate=rate,
        volume="+0%",
        pitch="+0Hz",
    )
    audio = bytearray()
    async for chunk in communicator.stream():
        if chunk.get("type") != "audio":
            continue
        audio.extend(bytes(chunk.get("data") or b""))
        if len(audio) > _MAX_TEACHER_VOICE_BYTES:
            raise RuntimeError("teacher_voice_audio_too_large")
    if len(audio) < 128:
        raise RuntimeError("teacher_voice_audio_empty")
    return bytes(audio)


def _voice_style_for_request(body: dict[str, Any]) -> str:
    explicit_style = str(body.get("style") or "").strip().lower()
    if explicit_style:
        if explicit_style not in {"lively", "relaxed"}:
            raise HTTPException(status_code=422, detail={"message": "不支持的教师语音风格"})
        return explicit_style
    reply_kind = str(body.get("reply_kind") or "").strip().lower()
    return "lively" if reply_kind in _LIVELY_REPLY_KINDS else "relaxed"


def _qwen_voice_config_path() -> Path | None:
    return _explicit_legacy_config_path("MENTORFLOW_QWEN3_TTS_CONFIG", "旧版 Qwen")


def _qwen_voice_config() -> dict[str, Any] | None:
    path = _qwen_voice_config_path()
    if path is None:
        return None
    config = _read_legacy_config(path, "旧版 Qwen")
    _require_exact_legacy_schema(
        config,
        label="旧版 Qwen",
        protocol=_LEGACY_QWEN_PROTOCOL,
        engine="qwen3-tts",
        fields=_LEGACY_QWEN_FIELDS,
    )
    if type(config.get("enabled")) is not bool:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_INVALID",
            "旧版 Qwen 配置的 enabled 必须是布尔值",
        )
    if config.get("enabled") is not True:
        return None
    release_slot = config.get("release_slot")
    if not isinstance(release_slot, str) or not release_slot.strip():
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_INVALID",
            "旧版 Qwen 配置缺少明确的 release_slot",
        )
    api_url = _legacy_loopback_origin(
        os.getenv("MENTORFLOW_QWEN3_TTS_URL") or config.get("api_url"),
        "旧版 Qwen",
    )
    return {
        "engine": "qwen3-tts",
        "api_url": api_url,
        "release_slot": release_slot.strip(),
    }


def _voice_config_path() -> Path:
    path = _explicit_legacy_config_path("MENTORFLOW_GPT_SOVITS_CONFIG", "旧版 GPT-SoVITS")
    if path is None:
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_REQUIRED",
            "旧版本地教师语音需要显式配置文件",
            environment="MENTORFLOW_GPT_SOVITS_CONFIG",
        )
    return path


def _local_voice_config() -> dict[str, Any]:
    path = _voice_config_path()
    config = _read_legacy_config(path, "旧版 GPT-SoVITS")
    _require_exact_legacy_schema(
        config,
        label="旧版 GPT-SoVITS",
        protocol=_LEGACY_GPT_SOVITS_PROTOCOL,
        engine="gpt-sovits",
        fields=_LEGACY_GPT_SOVITS_FIELDS,
    )
    if config.get("reference_audio_rights_confirmed") is not True:
        raise _legacy_voice_error(
            "VOICE_LEGACY_REFERENCE_NOT_AUTHORIZED",
            "旧版本地教师语音素材尚未通过授权确认",
        )
    string_fields = ("api_url", "ref_audio_path", "prompt_lang", "prompt_text", "release_slot")
    if not all(
        isinstance(config.get(key), str) and bool(config[key].strip())
        for key in string_fields
    ):
        raise _legacy_voice_error(
            "VOICE_LEGACY_CONFIG_INVALID",
            "旧版本地教师语音参考配置不完整",
        )
    api_url = _legacy_loopback_origin(config.get("api_url"), "旧版 GPT-SoVITS")
    return dict(config) | {
        "api_url": api_url,
        "ref_audio_path": config["ref_audio_path"].strip(),
        "prompt_lang": config["prompt_lang"].strip(),
        "prompt_text": config["prompt_text"].strip(),
        "release_slot": config["release_slot"].strip(),
    }


def _selected_voice_config(style: str) -> dict[str, Any]:
    qwen = _qwen_voice_config()
    if qwen is not None:
        return qwen | {"style": style}
    local = _local_voice_config()
    local.setdefault("release_slot", "explicit_legacy_local")
    return local | {"engine": "gpt-sovits", "style": style}


def _voice_fallback_configs(style: str, primary: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the preferred config followed by authorized local fallbacks."""
    configs = [primary]
    seen = {
        (str(primary.get("engine") or "gpt-sovits"), str(primary.get("api_url") or "").rstrip("/"))
    }
    if str(primary.get("engine") or "gpt-sovits") == "qwen3-tts":
        for builder in (_local_voice_config,):
            try:
                candidate = builder()
            except HTTPException:
                candidate = None
            if not candidate:
                continue
            candidate = dict(candidate)
            candidate.setdefault("engine", "gpt-sovits")
            candidate.setdefault("style", style)
            identity = (
                str(candidate.get("engine") or "gpt-sovits"),
                str(candidate.get("api_url") or "").rstrip("/"),
            )
            if identity not in seen:
                seen.add(identity)
                configs.append(candidate)
    return configs


def _ensure_tables() -> None:
    with db.connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS casual_chat_sessions (
              id TEXT PRIMARY KEY,
              workspace_id TEXT DEFAULT '',
              teacher_id TEXT NOT NULL,
              teacher_name TEXT NOT NULL,
              teacher_prompt TEXT DEFAULT '',
              extra_setting TEXT DEFAULT '',
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )


def _session(row: Any) -> dict[str, Any]:
    item = dict(row)
    payload = json.loads(item.pop("payload") or "{}")
    return {
        "id": item["id"],
        "workspace_id": item.get("workspace_id") or "",
        "teacher_id": item["teacher_id"],
        "teacher_name": item["teacher_name"],
        "teacher_prompt": item.get("teacher_prompt") or "",
        "extra_setting": item.get("extra_setting") or "",
        "messages": payload.get("messages", [])[-24:],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _get(session_id: str) -> dict[str, Any] | None:
    row = db.row("casual_chat_sessions", "id", session_id)
    return _session(row) if row else None


def _save(item: dict[str, Any]) -> None:
    now = db.now()
    with db.connection() as connection:
        connection.execute(
            "UPDATE casual_chat_sessions SET payload=?, updated_at=? WHERE id=?",
            (json.dumps({"messages": item.get("messages", [])[-24:]}, ensure_ascii=False), now, item["id"]),
        )


def _local_reply(item: dict[str, Any], text: str) -> str:
    """A pleasant, deterministic answer for instant demo/offline chat."""
    name = item.get("teacher_name") or "老师"
    setting = str(item.get("extra_setting") or "").strip()
    clean = text.strip()
    setting_lower = setting.lower()
    if any(token in setting_lower for token in ("俏皮", "活泼", "调皮", "可爱")):
        tone = "嘿嘿，"
    elif any(token in setting_lower for token in ("慵懒", "女王", "优雅")):
        tone = "懒洋洋地说，"
    elif any(token in setting_lower for token in ("严格", "督促", "直接")):
        tone = "听好了，"
    elif "温柔" in setting_lower or "耐心" in setting_lower:
        tone = "轻声说，"
    else:
        tone = ""
    setting_suffix = "我会按你的补充设定来回应。" if setting else ""
    if any(token in clean for token in ("累", "疲惫", "不想学", "焦虑")):
        return f"{tone}先不用硬撑，{name}陪你缓一口气。{setting_suffix}告诉我现在最想解决的一件小事，我会把它变得轻一点。"
    if any(token in clean for token in ("你好", "嗨", "在吗", "在么")):
        return f"{tone}在呀，我是{name}。{setting_suffix}今天想聊学习、生活，还是先放松一下？"
    if any(token in clean for token in ("谢谢", "感谢")):
        return f"{tone}不客气呀！{setting_suffix}你愿意把想法说出来，就已经是在照顾自己了。"
    prefix = f"按你的设定“{setting}”，" if setting else ""
    return f"{tone}{prefix}我听到了：{clean[:180]}。{setting_suffix}我们可以先从你最在意的那一点聊起；如果你想回到课程，我也会帮你接上进度。"


def _messages_for_prompt(item: dict[str, Any], text: str) -> list[dict[str, str]]:
    template = _casual_prompt_template()
    prompt = str(item.get("teacher_prompt") or "").strip()[:1600]
    setting = str(item.get("extra_setting") or "").strip()[:500]
    system = (
        template.replace("{{teacher_name}}", str(item.get("teacher_name") or "老师"))
        .replace("{{teacher_prompt}}", prompt or "（未提供额外教师设定）")
        .replace("{{extra_setting}}", setting or "（未提供额外补充设定）")
    )
    history = [{"role": "system", "content": system}]

    # A casual chat is independent per teacher, but it is still a real
    # conversation. Reuse only this session's bounded history so a reply can
    # follow the user's immediately previous message without leaking another
    # character's conversation.
    for message in item.get("messages", [])[-12:]:
        role = "assistant" if message.get("role") == "teacher" else "user"
        content = str(message.get("content") or "").strip()
        if content:
            history.append({"role": role, "content": content[:2000]})
    if setting:
        # Keep the setting adjacent to the current question. A single
        # instruction at the top of a long chat history is easy for some
        # providers to underweight or lose during context compression.
        history.append(
            {
                "role": "system",
                "content": (
                    "【本轮必须执行的用户补充设定】"
                    f"{setting}"
                    "\n请在这一次回复的措辞、称呼或节奏中体现它；不要只声明‘已收到设定’。"
                    "该设定只改变闲聊表达方式，不能改变事实、课程状态、权限或安全边界。"
                ),
            }
        )
    history.append({"role": "user", "content": text[:2000]})
    return history


@router.post("/sessions")
def create_casual_session(body: dict[str, Any]):
    _ensure_tables()
    now = db.now()
    session_id = uid("casual")
    teacher_id = str(body.get("teacher_id") or "warm")[:80]
    teacher_name = str(body.get("teacher_name") or "艾莉丝")[:80]
    teacher_prompt = str(body.get("teacher_prompt") or "")[:2000]
    extra_setting = str(body.get("extra_setting") or "")[:500]
    workspace_id = str(body.get("workspace_id") or "")[:80]
    with db.connection() as connection:
        connection.execute(
            "INSERT INTO casual_chat_sessions VALUES (?,?,?,?,?,?,?,?,?)",
            (session_id, workspace_id, teacher_id, teacher_name, teacher_prompt, extra_setting, json.dumps({"messages": []}, ensure_ascii=False), now, now),
        )
    return _get(session_id)


@router.get("/sessions")
def list_casual_sessions(workspace_id: str = "", limit: int = 50):
    """List persisted role conversations for the phone-like message list."""
    _ensure_tables()
    workspace_id = str(workspace_id or "")[:80]
    limit = max(1, min(int(limit or 50), 100))
    with db.connection() as connection:
        rows = connection.execute(
            "SELECT * FROM casual_chat_sessions WHERE workspace_id=? ORDER BY updated_at DESC LIMIT ?",
            (workspace_id, limit),
        ).fetchall()
    return {"items": [_session(row) for row in rows]}


@router.get("/sessions/{session_id}")
def get_casual_session(session_id: str):
    _ensure_tables()
    item = _get(session_id)
    if not item:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail={"message": "闲聊会话不存在"})
    return item


@router.patch("/sessions/{session_id}")
def update_casual_session(session_id: str, body: dict[str, Any]):
    _ensure_tables()
    item = _get(session_id)
    if not item:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail={"message": "闲聊会话不存在"})
    item["teacher_id"] = str(body.get("teacher_id") or item["teacher_id"])[:80]
    item["teacher_name"] = str(body.get("teacher_name") or item["teacher_name"])[:80]
    item["teacher_prompt"] = str(body.get("teacher_prompt") or item["teacher_prompt"])[:2000]
    if "extra_setting" in body:
        # An empty value is an intentional clear, not a request to keep stale
        # session state from a previous setting.
        item["extra_setting"] = str(body.get("extra_setting") or "")[:500]
    with db.connection() as connection:
        connection.execute(
            "UPDATE casual_chat_sessions SET teacher_id=?,teacher_name=?,teacher_prompt=?,extra_setting=?,updated_at=? WHERE id=?",
            (item["teacher_id"], item["teacher_name"], item["teacher_prompt"], item["extra_setting"], db.now(), session_id),
        )
    return _get(session_id)


@router.post("/sessions/{session_id}/messages")
async def send_casual_message(session_id: str, body: dict[str, Any], request: Request):
    _ensure_tables()
    item = _get(session_id)
    if not item:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail={"message": "闲聊会话不存在"})
    text = str(body.get("message") or "").strip()
    if not text:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail={"message": "请输入想聊的内容"})
    text = text[:2000]
    history_used = bool(item.get("messages"))
    started = time.perf_counter()
    provider_session_id = str(body.get("provider_session_id") or request.headers.get("X-Provider-Session-ID") or "").strip()
    session = require_provider_session(provider_session_id) if provider_session_id else create_verified_demo_session()
    reply = ""
    fallback_used = False
    if session.provider == "fake":
        reply = _local_reply(item, text)
    else:
        try:
            reply = (await asyncio.wait_for(provider_for_session(session).text(_messages_for_prompt(item, text), temperature=0.65), timeout=8.0)).strip()
        except Exception:
            # Chat should remain usable if a remote model is slow or briefly unavailable.
            reply = _local_reply(item, text)
            fallback_used = True
    item.setdefault("messages", []).extend([
        {"role": "user", "content": text},
        {"role": "teacher", "content": reply},
    ])
    _save(item)
    return {
        "message": reply,
        "teacher_id": item["teacher_id"],
        "teacher_name": item["teacher_name"],
        "generation_meta": generation_meta(session, latency_ms=max(0, int((time.perf_counter() - started) * 1000))) | {
            "fallback_used": fallback_used,
            "surface": "casual_chat",
            "context_policy": "current_message_plus_role_setting_and_session_context",
            "history_used": history_used,
        },
    }


@router.get("/voice/stream")
async def stream_casual_teacher_voice(
    text: str = "",
    style: str = "",
    reply_kind: str = "",
):
    """Proxy GPT-SoVITS fragments so the browser can start playing early."""
    text = str(text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail={"message": "没有可朗读的内容"})
    if len(text) > _MAX_TEACHER_VOICE_STREAM_CHARS:
        raise HTTPException(
            status_code=422,
            detail={"message": "朗读内容过长，请先收起或分段播放"},
        )
    if _teacher_voice_provider() != "pack":
        raise HTTPException(
            status_code=501,
            detail={"message": "当前教师语音提供方不支持流式朗读"},
        )
    voice_style = _voice_style_for_request({"style": style, "reply_kind": reply_kind})
    try:
        stream, pack = await stream_active_teacher_voice(
            text=text,
            style=voice_style,
            reply_kind=str(reply_kind or "").strip().lower(),
        )
    except TeacherVoicePackError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=teacher_voice_pack_error_detail(exc),
        ) from exc
    return StreamingResponse(
        stream,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-MentorFlow-Voice-Engine": "gpt-sovits",
            "X-MentorFlow-Synthetic-Voice": "true",
            "X-MentorFlow-Voice-Style": voice_style,
            "X-MentorFlow-Voice-Release": f"{pack['pack_id']}@{pack['version']}",
            "X-MentorFlow-Voice-Streaming": "true",
        },
    )


@router.post("/voice/synthesize")
async def synthesize_casual_teacher_voice(body: dict[str, Any]):
    """Return same-origin audio only after an explicit learner request."""
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail={"message": "没有可朗读的内容"})
    if len(text) > 800:
        raise HTTPException(status_code=422, detail={"message": "单次朗读内容过长，请分段播放"})
    style = _voice_style_for_request(body)
    reply_kind = str(body.get("reply_kind") or "").strip().lower()

    provider = _teacher_voice_provider()
    if provider == "minimax":
        try:
            synthesized = await synthesize_minimax_teacher_voice(text, style)
        except MiniMaxTeacherVoiceError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "error": {
                        "code": exc.code,
                        "message": str(exc),
                        "details": {},
                    }
                },
            ) from exc
        return Response(
            content=synthesized.audio,
            media_type=synthesized.media_type,
            headers={
                "Cache-Control": "no-store",
                "X-MentorFlow-Voice-Engine": "minimax",
                "X-MentorFlow-Synthetic-Voice": "true",
                "X-MentorFlow-Voice-Style": style,
                "X-MentorFlow-Voice-Profile": synthesized.profile,
                "X-MentorFlow-Voice-Release": f"minimax:{synthesized.model}",
                "X-MentorFlow-Voice-Cache": "client-memory",
            },
        )
    if provider == "pack":
        try:
            synthesized, pack = await synthesize_active_teacher_voice(
                text=text,
                style=style,
                reply_kind=reply_kind,
            )
        except TeacherVoicePackError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=teacher_voice_pack_error_detail(exc),
            ) from exc
        return Response(
            content=synthesized.audio,
            media_type=synthesized.media_type,
            headers={
                "Cache-Control": "no-store",
                "X-MentorFlow-Voice-Engine": synthesized.engine,
                "X-MentorFlow-Synthetic-Voice": "true",
                "X-MentorFlow-Voice-Style": style,
                "X-MentorFlow-Voice-Profile": synthesized.profile,
                "X-MentorFlow-Voice-Release": f"{pack['pack_id']}@{pack['version']}",
                "X-MentorFlow-Voice-Cache": synthesized.cache,
            },
        )

    if provider in {"edge", "edge-tts"}:
        edge_voice = str(os.getenv("MENTORFLOW_EDGE_TTS_VOICE") or "").strip()
        if not edge_voice:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "VOICE_EDGE_COMPAT_NOT_CONFIGURED",
                        "message": "Edge 兼容模式需要显式配置语音名称",
                        "details": {},
                    }
                },
            )
        try:
            audio = await asyncio.wait_for(
                _synthesize_edge_teacher_voice(text, style),
                timeout=30.0,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "自然中文语音暂时不可用，正在尝试浏览器语音",
                    "engine": "edge-tts",
                    "reason": type(exc).__name__,
                },
            ) from exc
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "private, max-age=300",
                "X-MentorFlow-Voice-Engine": "edge-tts",
                "X-MentorFlow-Synthetic-Voice": "true",
                "X-MentorFlow-Voice-Style": style,
                "X-MentorFlow-Voice-Profile": edge_voice,
                "X-MentorFlow-Voice-Release": "edge-online-v1",
                "X-MentorFlow-Voice-Cache": "client-memory",
            },
        )

    if provider != "local":
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "VOICE_PROVIDER_INVALID",
                    "message": "教师语音提供方配置无效",
                    "details": {"provider": provider},
                }
            },
        )

    _require_legacy_local_voice_opt_in()
    primary = _selected_voice_config(style)
    configs = _voice_fallback_configs(style, primary)
    attempted: list[dict[str, str]] = []

    for config in configs:
        engine = str(config.get("engine") or "gpt-sovits")
        if engine == "qwen3-tts":
            payload = {"text": text, "style": style, "reply_kind": reply_kind or None}
        else:
            payload = {
                "text": text,
                "text_lang": "zh",
                "ref_audio_path": str(config["ref_audio_path"]),
                "prompt_lang": str(config["prompt_lang"]),
                "prompt_text": str(config["prompt_text"]),
                "media_type": "wav",
                "streaming_mode": False,
                "text_split_method": "cut5",
                "speed_factor": 1.0,
            }

        # Compatibility mode contacts only a service that the operator already
        # owns and started. MentorFlow never discovers, starts, warms or stops it.
        try:
            timeout = httpx.Timeout(120.0 if engine == "qwen3-tts" else 35.0, connect=2.0)
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                upstream = await client.post(f"{str(config['api_url']).rstrip('/')}/tts", json=payload)
                upstream.raise_for_status()
            content_type = str(upstream.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            audio = bytes(upstream.content)
            if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"} or len(audio) < 44 or audio[:4] != b"RIFF":
                attempted.append({"engine": engine, "status": "invalid_audio"})
                continue
            return Response(
                content=audio,
                media_type="audio/wav",
                headers={
                    "Cache-Control": "no-store",
                    "X-MentorFlow-Voice-Engine": engine,
                    "X-MentorFlow-Synthetic-Voice": "true",
                    "X-MentorFlow-Voice-Style": style,
                    "X-MentorFlow-Voice-Profile": str(upstream.headers.get("x-mentorflow-voice-profile") or style),
                    "X-MentorFlow-Voice-Release": str(config.get("release_slot") or "unknown"),
                    "X-MentorFlow-Voice-Cache": str(upstream.headers.get("x-mentorflow-voice-cache") or "unknown"),
                },
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError):
            attempted.append({"engine": engine, "status": "unreachable"})
        except httpx.HTTPError as exc:
            attempted.append({"engine": engine, "status": f"http_error:{type(exc).__name__}"})

    raise HTTPException(
        status_code=503,
        detail={
            "message": "本地教师语音暂时不可用，请稍后重试",
            "attempted_engines": attempted,
        },
    )


@router.post("/voice/warm")
async def warm_casual_teacher_voice() -> dict[str, Any]:
    """Report readiness without loading a model or making a TTS request."""
    provider = _teacher_voice_provider()
    if provider == "minimax":
        return minimax_teacher_voice_status()
    if provider == "pack":
        try:
            status = get_teacher_voice_status()
        except TeacherVoicePackError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=teacher_voice_pack_error_detail(exc),
            ) from exc
        active = status.get("active_pack")
        return {
            "ok": active is not None,
            "warmed": False,
            "engine": "voice-pack",
            "active_pack": active,
            "reason": None if active is not None else "VOICE_PACK_NOT_INSTALLED",
        }
    if provider in {"edge", "edge-tts"}:
        edge_voice = str(os.getenv("MENTORFLOW_EDGE_TTS_VOICE") or "").strip()
        if not edge_voice:
            return {
                "ok": False,
                "warmed": False,
                "engine": "edge-tts",
                "reason": "VOICE_EDGE_COMPAT_NOT_CONFIGURED",
            }
        return {
            "ok": True,
            "warmed": False,
            "engine": "edge-tts",
            "voice": edge_voice,
            "reason": "not_required",
        }
    if provider != "local":
        return {
            "ok": False,
            "warmed": False,
            "engine": "unavailable",
            "reason": "VOICE_PROVIDER_INVALID",
        }
    _require_legacy_local_voice_opt_in()
    config = _selected_voice_config("relaxed")
    return {
        "ok": True,
        "warmed": False,
        "engine": str(config.get("engine") or "gpt-sovits"),
        "reason": "legacy_external_runtime_not_started_by_mentorflow",
    }

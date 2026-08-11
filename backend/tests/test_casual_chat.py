from app import casual_chat, db
import httpx
import json
from pathlib import Path

from fastapi import HTTPException
import pytest


def _valid_legacy_gpt_sovits_config(tmp_path: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": casual_chat._LEGACY_GPT_SOVITS_PROTOCOL,
        "engine": "gpt-sovits",
        "reference_audio_rights_confirmed": True,
        "api_url": "http://localhost:49153",
        "ref_audio_path": str(tmp_path / "authorized-reference.wav"),
        "prompt_lang": "zh",
        "prompt_text": "显式授权测试参考文本",
        "release_slot": "explicit-test-gpt-sovits",
    }


def test_casual_chat_is_independent_and_fast(client):
    created = client.post(
        "/api/casual-chat/sessions",
        json={
            "teacher_id": "warm",
            "teacher_name": "艾莉丝",
            "teacher_prompt": "温柔而活泼",
            "extra_setting": "多用生活化比喻",
        },
    )
    assert created.status_code == 200
    session = created.json()
    assert session["messages"] == []

    response = client.post(
        f"/api/casual-chat/sessions/{session['id']}/messages",
        json={"message": "你好，今天有点累"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["teacher_id"] == "warm"
    assert "艾莉丝" in body["message"]
    assert body["generation_meta"]["surface"] == "casual_chat"
    assert body["generation_meta"]["latency_ms"] is not None

    saved = client.get(f"/api/casual-chat/sessions/{session['id']}").json()
    assert [item["role"] for item in saved["messages"]] == ["user", "teacher"]
    with db.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0


def test_casual_chat_history_is_bounded(client):
    session = client.post("/api/casual-chat/sessions", json={"teacher_name": "林老师"}).json()
    for index in range(15):
        result = client.post(
            f"/api/casual-chat/sessions/{session['id']}/messages",
            json={"message": f"第 {index} 句"},
        )
        assert result.status_code == 200
    saved = client.get(f"/api/casual-chat/sessions/{session['id']}").json()
    assert len(saved["messages"]) <= 24
    assert saved["messages"][-1]["role"] == "teacher"


def test_casual_chat_fallback_applies_extra_setting_to_shortcuts():
    item = {"teacher_name": "小熊老师", "extra_setting": "俏皮一点，多用轻松语气"}
    reply = casual_chat._local_reply(item, "你好")
    assert reply.startswith("嘿嘿，")
    assert "补充设定" in reply


def test_casual_chat_provider_prompt_contains_extra_setting_as_actionable_instruction():
    messages = casual_chat._messages_for_prompt(
        {
            "teacher_name": "小熊老师",
            "teacher_prompt": "温柔耐心",
            "extra_setting": "使用俏皮短句，先给结论",
            "messages": [],
        },
        "你好",
    )
    system = messages[0]["content"]
    assert "使用俏皮短句，先给结论" in system
    assert "本轮必须体现" in system
    assert messages[-2]["role"] == "system"
    assert "【本轮必须执行的用户补充设定】使用俏皮短句，先给结论" in messages[-2]["content"]
    assert "不要只声明‘已收到设定’" in messages[-2]["content"]


def test_casual_chat_reads_editable_prompt_template_without_restart(tmp_path, monkeypatch):
    prompt_path = tmp_path / "casual-chat-prompt.md"
    prompt_path.write_text(
        "角色={{teacher_name}}\n设定={{extra_setting}}\n教师={{teacher_prompt}}\n只谈当前闲聊。",
        encoding="utf-8",
    )
    monkeypatch.setenv("MENTORFLOW_CASUAL_CHAT_PROMPT", str(prompt_path))

    first = casual_chat._messages_for_prompt(
        {
            "teacher_name": "甲老师",
            "teacher_prompt": "温柔",
            "extra_setting": "多用短句",
            "messages": [],
        },
        "第一句",
    )
    assert first[0]["content"] == "角色=甲老师\n设定=多用短句\n教师=温柔\n只谈当前闲聊。"

    prompt_path.write_text("已更新：{{teacher_name}}", encoding="utf-8")
    second = casual_chat._messages_for_prompt(
        {"teacher_name": "甲老师", "teacher_prompt": "温柔", "extra_setting": "", "messages": []},
        "第二句",
    )
    assert second[0]["content"] == "已更新：甲老师"


def test_casual_chat_provider_prompt_uses_only_the_current_role_history():
    messages = casual_chat._messages_for_prompt(
        {
            "teacher_name": "小熊老师",
            "teacher_prompt": "温柔耐心",
            "extra_setting": "使用简短俏皮语气",
            "messages": [
                {"role": "user", "content": "上一条私密闲聊不应被带入"},
                {"role": "teacher", "content": "上一条回复也不应被带入"},
            ],
        },
        "只回答当前问题",
    )
    serialized = "\n".join(str(item["content"]) for item in messages)
    assert "上一条私密闲聊不应被带入" in serialized
    assert "上一条回复也不应被带入" in serialized
    assert "只回答当前问题" in serialized
    assert messages[1:3] == [
        {"role": "user", "content": "上一条私密闲聊不应被带入"},
        {"role": "assistant", "content": "上一条回复也不应被带入"},
    ]


def test_casual_chat_lists_persisted_role_sessions_by_workspace(client):
    created = client.post(
        "/api/casual-chat/sessions",
        json={"workspace_id": "ws_phone", "teacher_id": "teacher_a", "teacher_name": "甲老师"},
    ).json()
    client.post(
        f"/api/casual-chat/sessions/{created['id']}/messages",
        json={"message": "保留这句"},
    )
    listed = client.get("/api/casual-chat/sessions?workspace_id=ws_phone").json()
    assert len(listed["items"]) == 1
    assert listed["items"][0]["teacher_id"] == "teacher_a"
    assert listed["items"][0]["messages"][-2:][0]["content"] == "保留这句"


def test_casual_chat_can_clear_an_existing_extra_setting(client):
    created = client.post(
        "/api/casual-chat/sessions",
        json={"teacher_name": "小熊老师", "extra_setting": "先俏皮再解释"},
    ).json()
    updated = client.patch(
        f"/api/casual-chat/sessions/{created['id']}",
        json={"extra_setting": ""},
    )
    assert updated.status_code == 200
    assert updated.json()["extra_setting"] == ""


def test_casual_chat_online_provider_receives_extra_setting_in_system_prompt(client, monkeypatch):
    from app.provider_runtime import create_verified_demo_session

    session = create_verified_demo_session()
    session.provider = "openai_compatible"
    captured: dict[str, object] = {}

    class StubProvider:
        async def text(self, messages, temperature=0.65):
            captured["messages"] = messages
            return "我会按设定回应。"

    monkeypatch.setattr(casual_chat, "require_provider_session", lambda _session_id: session)
    monkeypatch.setattr(casual_chat, "provider_for_session", lambda _session: StubProvider())
    created = client.post(
        "/api/casual-chat/sessions",
        json={"teacher_name": "小熊老师", "extra_setting": "先给结论，再用俏皮短句"},
    ).json()
    response = client.post(
        f"/api/casual-chat/sessions/{created['id']}/messages",
        json={"message": "请开始", "provider_session_id": session.id},
    )
    assert response.status_code == 200
    system = str(captured["messages"][0]["content"])
    assert "先给结论，再用俏皮短句" in system
    assert "本轮必须体现" in system


def test_local_voice_provider_requires_second_legacy_opt_in_before_config(client, monkeypatch):
    selected: list[str] = []
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "local")
    monkeypatch.delenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE", raising=False)
    monkeypatch.setattr(
        casual_chat,
        "_selected_voice_config",
        lambda style: selected.append(style),
    )

    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "不得访问旧服务"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VOICE_LEGACY_LOCAL_DISABLED"
    assert response.json()["error"]["details"]["required_flag"] == (
        "MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE=1"
    )
    assert selected == []


def test_legacy_local_with_gate_still_requires_explicit_config(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "local")
    monkeypatch.setenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE", "1")
    monkeypatch.delenv("MENTORFLOW_QWEN3_TTS_CONFIG", raising=False)
    monkeypatch.delenv("MENTORFLOW_GPT_SOVITS_CONFIG", raising=False)

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("missing explicit config must not contact a runtime")

    monkeypatch.setattr(casual_chat.httpx, "AsyncClient", ForbiddenClient)
    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "仍然不能自动发现相邻项目"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VOICE_LEGACY_CONFIG_REQUIRED"
    assert response.json()["error"]["details"]["environment"] == (
        "MENTORFLOW_GPT_SOVITS_CONFIG"
    )


def test_legacy_qwen_config_is_explicit_protocol_marked_and_loopback_only(tmp_path, monkeypatch):
    bundled = Path(__file__).resolve().parents[1] / "config" / "voice" / "qwen3_tts_runtime.json"
    bundled_value = json.loads(bundled.read_text(encoding="utf-8"))
    assert bundled_value["enabled"] is False
    assert bundled_value["protocol"] == casual_chat._LEGACY_QWEN_PROTOCOL

    monkeypatch.delenv("MENTORFLOW_QWEN3_TTS_CONFIG", raising=False)
    monkeypatch.delenv("MENTORFLOW_QWEN3_TTS_URL", raising=False)
    assert casual_chat._qwen_voice_config() is None

    config_path = tmp_path / "legacy-qwen.json"
    config_path.write_text(
        json.dumps(
            bundled_value
            | {
                "enabled": True,
                "api_url": "http://127.0.0.1:49152",
                "release_slot": "explicit-test-qwen",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MENTORFLOW_QWEN3_TTS_CONFIG", str(config_path))

    assert casual_chat._qwen_voice_config() == {
        "engine": "qwen3-tts",
        "api_url": "http://127.0.0.1:49152",
        "release_slot": "explicit-test-qwen",
    }

    monkeypatch.setenv("MENTORFLOW_QWEN3_TTS_URL", "https://example.invalid:443")
    with pytest.raises(HTTPException) as remote:
        casual_chat._qwen_voice_config()
    assert remote.value.detail["error"]["code"] == "VOICE_LEGACY_RUNTIME_URL_INVALID"

    monkeypatch.delenv("MENTORFLOW_QWEN3_TTS_URL", raising=False)
    config_path.write_text(
        json.dumps(bundled_value | {"enabled": True, "protocol": "unmarked-direct-listener"}),
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as protocol:
        casual_chat._qwen_voice_config()
    assert protocol.value.detail["error"]["code"] == "VOICE_LEGACY_CONFIG_PROTOCOL_INVALID"

    markerless = bundled_value | {
        "enabled": True,
        "api_url": "http://127.0.0.1:49152",
    }
    markerless.pop("protocol")
    config_path.write_text(json.dumps(markerless), encoding="utf-8")
    with pytest.raises(HTTPException) as missing_protocol:
        casual_chat._qwen_voice_config()
    assert missing_protocol.value.detail["error"]["code"] == (
        "VOICE_LEGACY_CONFIG_PROTOCOL_INVALID"
    )

    config_path.write_text(
        json.dumps(
            bundled_value
            | {
                "enabled": True,
                "api_url": "http://127.0.0.1:49152",
                "unexpected_runtime_root": "E:/adjacent-runtime",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as extra_fields:
        casual_chat._qwen_voice_config()
    assert extra_fields.value.detail["error"]["code"] == (
        "VOICE_LEGACY_CONFIG_FIELDS_INVALID"
    )


def test_legacy_config_paths_are_explicit_absolute_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_QWEN3_TTS_CONFIG", "relative/legacy-qwen.json")
    with pytest.raises(HTTPException) as relative:
        casual_chat._qwen_voice_config()
    assert relative.value.detail["error"]["code"] == "VOICE_LEGACY_CONFIG_PATH_INVALID"

    monkeypatch.delenv("MENTORFLOW_QWEN3_TTS_CONFIG", raising=False)
    monkeypatch.delenv("MENTORFLOW_GPT_SOVITS_CONFIG", raising=False)
    with pytest.raises(HTTPException) as missing:
        casual_chat._voice_config_path()
    assert missing.value.detail["error"]["code"] == "VOICE_LEGACY_CONFIG_REQUIRED"

    config_path = tmp_path / "legacy-gpt-sovits.json"
    valid_gpt = _valid_legacy_gpt_sovits_config(tmp_path)
    config_path.write_text(json.dumps(valid_gpt), encoding="utf-8")
    monkeypatch.setenv("MENTORFLOW_GPT_SOVITS_CONFIG", str(config_path))
    parsed = casual_chat._local_voice_config()
    assert parsed["api_url"] == "http://localhost:49153"
    assert parsed["release_slot"] == "explicit-test-gpt-sovits"

    markerless = dict(valid_gpt)
    markerless.pop("protocol")
    config_path.write_text(json.dumps(markerless), encoding="utf-8")
    with pytest.raises(HTTPException) as missing_marker:
        casual_chat._local_voice_config()
    assert missing_marker.value.detail["error"]["code"] == (
        "VOICE_LEGACY_CONFIG_PROTOCOL_INVALID"
    )

    config_path.write_text(
        json.dumps(valid_gpt | {"protocol": "mentorflow.unmarked-gpt-listener.v1"}),
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as wrong_marker:
        casual_chat._local_voice_config()
    assert wrong_marker.value.detail["error"]["code"] == (
        "VOICE_LEGACY_CONFIG_PROTOCOL_INVALID"
    )

    config_path.write_text(
        json.dumps(valid_gpt | {"runtime_root": "E:/adjacent-ai_agent"}),
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as extra_fields:
        casual_chat._local_voice_config()
    assert extra_fields.value.detail["error"]["code"] == (
        "VOICE_LEGACY_CONFIG_FIELDS_INVALID"
    )


@pytest.mark.parametrize(
    ("component", "canonical"),
    [
        ("COM¹.json", "COM1"),
        ("com² .TXT", "COM2"),
        ("CoM³...   ", "COM3"),
        ("LPT¹", "LPT1"),
        ("lpt².log", "LPT2"),
        ("LpT³ .json", "LPT3"),
        ("COM⁴.json", "COM⁴"),
        ("COM₁.json", "COM₁"),
        ("COM¹x.json", "COM¹X"),
        ("XCOM¹.json", "XCOM¹"),
    ],
)
def test_windows_device_basename_canonicalizer_is_exact(component, canonical):
    assert casual_chat._canonical_windows_device_basename(component) == canonical


@pytest.mark.parametrize(
    "unsafe_path",
    [
        r"relative\legacy.json",
        r"~\legacy.json",
        r"C:\~\legacy.json",
        r"C:\%USERPROFILE%\legacy.json",
        r"C:\%TEMP:~0,2%\legacy.json",
        r"C:\$env:TEMP\legacy.json",
        r"C:\${env:TEMP}\legacy.json",
        r" C:\explicit\legacy.json",
        "C:\\explicit\\legacy.json ",
        r"\\audit.invalid\share\legacy.json",
        r"\\?\C:\legacy.json",
        r"\\.\C:\legacy.json",
        r"C:\NUL.json",
        r"C:\NUL .json",
        r"C:\COM¹.json",
        r"C:\com²",
        r"C:\CoM³.TxT",
        r"C:\LPT¹",
        r"C:\lpt².log",
        r"C:\LpT³ .json",
        "C:\\COM¹.json. ",
        "C:\\folder\\lpt²...   ",
    ],
)
def test_legacy_config_rejects_unsafe_raw_path_before_filesystem(
    unsafe_path, monkeypatch
):
    touched: list[str] = []
    monkeypatch.setenv("MENTORFLOW_QWEN3_TTS_CONFIG", unsafe_path)
    monkeypatch.setattr(
        casual_chat,
        "_is_local_windows_drive",
        lambda _root: touched.append("drive") or True,
    )
    monkeypatch.setattr(
        casual_chat,
        "_has_reparse_point",
        lambda _path: touched.append("lstat") or False,
    )
    monkeypatch.setattr(
        casual_chat,
        "_read_legacy_config",
        lambda _path, _label: touched.append("read") or {},
    )

    with pytest.raises(HTTPException) as rejected:
        casual_chat._qwen_voice_config()

    assert rejected.value.detail["error"]["code"] == "VOICE_LEGACY_CONFIG_PATH_INVALID"
    assert touched == []


@pytest.mark.parametrize(
    "unsafe_path",
    [
        r"~\legacy.json",
        r"\\audit.invalid\share\legacy.json",
        r"\\?\C:\legacy.json",
        r"\\.\C:\legacy.json",
        r"C:\COM¹.json",
        r"C:\lpt².log",
        "C:\\CoM³.json. ",
    ],
)
def test_legacy_warm_rejects_unsafe_path_without_filesystem_or_network(
    client, monkeypatch, unsafe_path
):
    touched: list[str] = []
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "local")
    monkeypatch.setenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE", "1")
    monkeypatch.setenv("MENTORFLOW_QWEN3_TTS_CONFIG", unsafe_path)
    monkeypatch.delenv("MENTORFLOW_GPT_SOVITS_CONFIG", raising=False)
    monkeypatch.setattr(
        casual_chat,
        "_is_local_windows_drive",
        lambda _root: touched.append("drive") or True,
    )
    monkeypatch.setattr(
        casual_chat,
        "_has_reparse_point",
        lambda _path: touched.append("lstat") or False,
    )
    monkeypatch.setattr(
        casual_chat,
        "_read_legacy_config",
        lambda _path, _label: touched.append("read") or {},
    )

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            touched.append("network")
            raise AssertionError("unsafe warm path must fail before network")

    monkeypatch.setattr(casual_chat.httpx, "AsyncClient", ForbiddenClient)
    response = client.post("/api/casual-chat/voice/warm")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VOICE_LEGACY_CONFIG_PATH_INVALID"
    assert touched == []


def test_legacy_warm_rejects_mapped_drive_before_file_or_http_access(
    client, monkeypatch
):
    touched: list[str] = []
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "local")
    monkeypatch.setenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE", "1")
    monkeypatch.setenv("MENTORFLOW_QWEN3_TTS_CONFIG", r"Z:\legacy.json")
    monkeypatch.delenv("MENTORFLOW_GPT_SOVITS_CONFIG", raising=False)
    monkeypatch.setattr(
        casual_chat,
        "_is_local_windows_drive",
        lambda _root: touched.append("drive-type") or False,
    )
    monkeypatch.setattr(
        casual_chat,
        "_has_reparse_point",
        lambda _path: touched.append("lstat") or False,
    )
    monkeypatch.setattr(
        casual_chat,
        "_read_legacy_config",
        lambda _path, _label: touched.append("read") or {},
    )

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            touched.append("http")
            raise AssertionError("mapped drive rejection must precede HTTP")

    monkeypatch.setattr(casual_chat.httpx, "AsyncClient", ForbiddenClient)
    response = client.post("/api/casual-chat/voice/warm")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VOICE_LEGACY_CONFIG_PATH_INVALID"
    assert touched == ["drive-type"]


def test_legacy_config_size_and_duplicate_keys_fail_closed(tmp_path, monkeypatch):
    oversized = tmp_path / "oversized-legacy.json"
    oversized.write_bytes(b"x" * (casual_chat._MAX_LEGACY_VOICE_CONFIG_BYTES + 1))
    monkeypatch.setenv("MENTORFLOW_QWEN3_TTS_CONFIG", str(oversized))
    touched: list[str] = []
    monkeypatch.setattr(
        casual_chat,
        "_read_legacy_config",
        lambda _path, _label: touched.append("read") or {},
    )
    with pytest.raises(HTTPException) as too_large:
        casual_chat._qwen_voice_config()
    assert too_large.value.detail["error"]["code"] == "VOICE_LEGACY_CONFIG_INVALID"
    assert touched == []

    duplicate = tmp_path / "duplicate-key-legacy.json"
    duplicate.write_text(
        "{"
        '"schema_version":1,'
        f'"protocol":"{casual_chat._LEGACY_QWEN_PROTOCOL}",'
        f'"protocol":"{casual_chat._LEGACY_QWEN_PROTOCOL}",'
        '"enabled":true,'
        '"api_url":"http://127.0.0.1:49152",'
        '"engine":"qwen3-tts",'
        '"release_slot":"duplicate-key-test"'
        "}",
        encoding="utf-8",
    )
    monkeypatch.undo()
    monkeypatch.setenv("MENTORFLOW_QWEN3_TTS_CONFIG", str(duplicate))
    with pytest.raises(HTTPException) as duplicate_key:
        casual_chat._qwen_voice_config()
    assert duplicate_key.value.detail["error"]["code"] == "VOICE_LEGACY_CONFIG_INVALID"


def test_legacy_voice_warm_is_config_only_and_never_contacts_runtime(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "local")
    monkeypatch.setenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE", "1")
    monkeypatch.setattr(
        casual_chat,
        "_selected_voice_config",
        lambda style: {
            "engine": "qwen3-tts",
            "api_url": "http://127.0.0.1:49152",
            "release_slot": "explicit-test-qwen",
            "style": style,
        },
    )

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("warm must not contact a legacy runtime")

    monkeypatch.setattr(casual_chat.httpx, "AsyncClient", ForbiddenClient)
    response = client.post("/api/casual-chat/voice/warm")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "warmed": False,
        "engine": "qwen3-tts",
        "reason": "legacy_external_runtime_not_started_by_mentorflow",
    }


def test_casual_teacher_voice_proxies_valid_local_gpt_sovits_wav(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "local")
    monkeypatch.setenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE", "1")
    class Upstream:
        status_code = 200
        headers = {"content-type": "audio/wav"}
        content = b"RIFF" + (b"\x00" * 64)

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            assert url == "http://127.0.0.1:9880/tts"
            assert json["text"] == "你好"
            assert json["media_type"] == "wav"
            return Upstream()

    monkeypatch.setattr(casual_chat, "_selected_voice_config", lambda style: {
        "api_url": "http://127.0.0.1:9880",
        "ref_audio_path": "E:/authorized/reference.wav",
        "prompt_lang": "ja",
        "prompt_text": "authorized prompt",
        "release_slot": "test_release",
        "style": style,
    })
    monkeypatch.setattr(casual_chat.httpx, "AsyncClient", FakeClient)

    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "你好", "style": "relaxed"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.headers["x-mentorflow-voice-engine"] == "gpt-sovits"
    assert response.headers["x-mentorflow-voice-style"] == "relaxed"
    assert response.headers["x-mentorflow-voice-release"] == "test_release"
    assert response.content.startswith(b"RIFF")


def test_casual_teacher_voice_streams_pack_fragments(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "pack")

    async def fake_stream(*, text, style, reply_kind):
        assert text == "长文本流式朗读"
        assert style == "relaxed"
        assert reply_kind == "teaching.explain"
        yield b"RIFF" + (b"\x00" * 12)
        yield b"audio-fragment"

    async def fake_prepare(*, text, style, reply_kind):
        return fake_stream(text=text, style=style, reply_kind=reply_kind), {
            "pack_id": "phrolova-b-gpt-sovits",
            "version": "1.0.0",
        }

    monkeypatch.setattr(casual_chat, "stream_active_teacher_voice", fake_prepare)

    response = client.get(
        "/api/casual-chat/voice/stream",
        params={"text": "长文本流式朗读", "reply_kind": "teaching.explain"},
    )

    assert response.status_code == 200
    assert response.content == b"RIFF" + (b"\x00" * 12) + b"audio-fragment"
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.headers["x-mentorflow-voice-streaming"] == "true"


def test_casual_teacher_voice_rejects_unknown_style_without_calling_runtime(client):
    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "你好", "style": "impersonation"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "不支持的教师语音风格"


def test_casual_teacher_voice_routes_reply_kind_to_qwen_style(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "local")
    monkeypatch.setenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE", "1")
    class Upstream:
        status_code = 200
        headers = {
            "content-type": "audio/wav",
            "x-mentorflow-voice-profile": "playful",
            "x-mentorflow-voice-cache": "hit",
        }
        content = b"RIFF" + (b"\x00" * 64)

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            assert url == "http://127.0.0.1:8892/tts"
            assert json == {
                "text": "做对了",
                "style": "lively",
                "reply_kind": "teaching.quiz_success",
            }
            return Upstream()

    monkeypatch.setattr(casual_chat, "_selected_voice_config", lambda style: {
        "engine": "qwen3-tts",
        "api_url": "http://127.0.0.1:8892",
        "release_slot": "test_qwen_release",
        "style": style,
    })
    monkeypatch.setattr(casual_chat.httpx, "AsyncClient", FakeClient)

    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "做对了", "reply_kind": "teaching.quiz_success"},
    )
    assert response.status_code == 200
    assert response.headers["x-mentorflow-voice-engine"] == "qwen3-tts"
    assert response.headers["x-mentorflow-voice-style"] == "lively"
    assert response.headers["x-mentorflow-voice-profile"] == "playful"
    assert response.headers["x-mentorflow-voice-cache"] == "hit"


def test_casual_teacher_voice_falls_back_when_qwen_runtime_is_stopped(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "local")
    monkeypatch.setenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE", "1")
    class Upstream:
        status_code = 200
        headers = {"content-type": "audio/wav"}
        content = b"RIFF" + (b"\x00" * 64)

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            if url.startswith("http://127.0.0.1:8892"):
                raise httpx.ConnectError("qwen runtime stopped")
            assert url == "http://127.0.0.1:9880/tts"
            assert json["prompt_text"] == "authorized prompt"
            return Upstream()

    monkeypatch.setattr(casual_chat, "_selected_voice_config", lambda style: {
        "engine": "qwen3-tts",
        "api_url": "http://127.0.0.1:8892",
        "release_slot": "test_qwen_release",
        "style": style,
    })
    monkeypatch.setattr(casual_chat, "_local_voice_config", lambda: {
        "engine": "gpt-sovits",
        "api_url": "http://127.0.0.1:9880",
        "ref_audio_path": "E:/authorized/reference.wav",
        "prompt_lang": "ja",
        "prompt_text": "authorized prompt",
        "release_slot": "test_local_fallback",
    })
    monkeypatch.setattr(casual_chat.httpx, "AsyncClient", FakeClient)

    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "你好", "reply_kind": "after_class.small_talk"},
    )
    assert response.status_code == 200
    assert response.headers["x-mentorflow-voice-engine"] == "gpt-sovits"
    assert response.headers["x-mentorflow-voice-release"] == "test_local_fallback"
    assert response.content.startswith(b"RIFF")


def test_default_teacher_voice_requires_active_pack_without_edge_or_local_start(client, monkeypatch, tmp_path):
    edge_calls: list[tuple[str, str]] = []

    async def fake_edge(text: str, style: str) -> bytes:
        edge_calls.append((text, style))
        return b"ID3" + (b"\x00" * 256)

    monkeypatch.delenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", raising=False)
    monkeypatch.setenv("MENTORFLOW_ALLOW_LEGACY_LOCAL_VOICE", "1")
    monkeypatch.setenv("MENTORFLOW_VOICE_PACK_ROOT", str(tmp_path / "empty-packs"))
    monkeypatch.setenv(
        "MENTORFLOW_VOICE_PACK_CATALOG",
        str(Path(__file__).resolve().parents[1] / "config" / "voice" / "teacher_voice_pack_catalog.v1.json"),
    )
    monkeypatch.setattr(casual_chat, "_synthesize_edge_teacher_voice", fake_edge)

    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "我们先看环形链表的入口。", "reply_kind": "teaching.explain"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VOICE_PACK_NOT_INSTALLED"
    assert response.json()["error"]["details"]["fallback_policy"] == "off"
    assert edge_calls == []


def test_edge_teacher_voice_remains_explicit_compatibility_only(client, monkeypatch):
    async def explicit_edge(_text: str, _style: str) -> bytes:
        return b"ID3" + (b"\x00" * 256)

    monkeypatch.setenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", "edge-tts")
    monkeypatch.setenv("MENTORFLOW_EDGE_TTS_VOICE", "explicit-test-voice")
    monkeypatch.setattr(casual_chat, "_synthesize_edge_teacher_voice", explicit_edge)

    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "按需朗读"},
    )

    assert response.status_code == 200
    assert response.headers["x-mentorflow-voice-engine"] == "edge-tts"


def test_default_teacher_voice_warm_is_read_only_pack_status(client, monkeypatch, tmp_path):
    monkeypatch.delenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", raising=False)
    monkeypatch.setenv("MENTORFLOW_VOICE_PACK_ROOT", str(tmp_path / "empty-packs"))
    monkeypatch.setenv(
        "MENTORFLOW_VOICE_PACK_CATALOG",
        str(Path(__file__).resolve().parents[1] / "config" / "voice" / "teacher_voice_pack_catalog.v1.json"),
    )

    response = client.post("/api/casual-chat/voice/warm")

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "warmed": False,
        "engine": "voice-pack",
        "active_pack": None,
        "reason": "VOICE_PACK_NOT_INSTALLED",
    }
    assert not (tmp_path / "empty-packs").exists()

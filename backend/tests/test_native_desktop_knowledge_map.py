from __future__ import annotations

import sqlite3
from pathlib import Path

from desktop.knowledge_map_view import layered_positions
from desktop.materials_page import human_size
from desktop.plans_page import flattened_tasks
from desktop.classroom_page import session_transcript, teacher_turn_text
from desktop.facts import PROGRAMMER_ANALOGIES
from desktop.main import _import_development_data_once


def test_layered_positions_follow_prerequisite_order_without_domain_rules() -> None:
    nodes = [{"id": "random-root"}, {"id": "random-middle"}, {"id": "random-leaf"}]
    edges = [
        {"source_id": "random-root", "target_id": "random-middle"},
        {"source_id": "random-middle", "target_id": "random-leaf"},
    ]

    result = layered_positions(nodes, edges)

    assert result["random-root"][0] < result["random-middle"][0] < result["random-leaf"][0]


def test_layered_positions_keep_cycles_and_ignore_dangling_edges() -> None:
    nodes = [{"id": "alpha"}, {"id": "beta"}, {"id": "standalone"}]
    edges = [
        {"source_id": "alpha", "target_id": "beta"},
        {"source_id": "beta", "target_id": "alpha"},
        {"source_id": "missing", "target_id": "standalone"},
    ]

    result = layered_positions(nodes, edges)

    assert set(result) == {"alpha", "beta", "standalone"}
    assert result["standalone"][0] < result["alpha"][0]


def test_native_material_size_formatting_is_stable() -> None:
    assert human_size(0) == "0 B"
    assert human_size(1536) == "1.5 KiB"
    assert human_size("not-a-number") == "—"


def test_native_plan_flattens_preview_and_prefers_execution_state() -> None:
    preview = {"days": [{"date": "2030-01-02", "tasks": [{"id": "draft", "title": "Draft"}]}]}
    assert flattened_tasks(preview)[0]["day_date"] == "2030-01-02"
    active = {**preview, "execution_tasks": [{"id": "live", "status": "planned"}]}
    assert flattened_tasks(active) == [{"id": "live", "status": "planned"}]


def test_native_classroom_renders_structured_turn_as_plain_text() -> None:
    turn = {
        "concept": {"name": "Sentinel Concept"},
        "blocks": [
            {"type": "objective", "content": "Know the boundary."},
            {"type": "worked_example", "content": "Input → Output"},
        ],
        "check": {"prompt": "What changes?"},
        "expected_learner_action": "Explain in one sentence.",
    }
    rendered = teacher_turn_text(turn)
    assert "Sentinel Concept" in rendered
    assert "【学习目标】" in rendered
    assert "What changes?" in rendered
    transcript = session_transcript({"messages": [{"role": "teacher", "teacher_turn": turn}]})
    assert "教师" in transcript and "Input → Output" in transcript


def test_overview_uses_curated_programmer_terms_with_life_analogies() -> None:
    assert len(PROGRAMMER_ANALOGIES) >= 60
    titles = {title for title, _body, _source in PROGRAMMER_ANALOGIES}
    assert {"算法 Algorithm", "缓存 Cache", "Git 提交 Commit", "数据库事务 Transaction"} <= titles
    assert all("知识卡" not in title and "知识卡" not in body for title, body, _source in PROGRAMMER_ANALOGIES)
    assert all("https://" in source for _title, _body, source in PROGRAMMER_ANALOGIES)
    assert len({body for _title, body, _source in PROGRAMMER_ANALOGIES}) == len(PROGRAMMER_ANALOGIES)


def test_native_classroom_voice_pipeline_writes_editable_text_without_microphone(
    tmp_path, monkeypatch
) -> None:
    """Exercise the async WAV-to-text UI path without recording ambient audio."""

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from PySide6.QtWidgets import QApplication

    from desktop.classroom_page import ClassroomPage

    class FakeApi:
        def transcribe_teaching_voice(self, session_id: str, path: Path) -> dict:
            assert session_id == "session-voice-test"
            assert path.suffix == ".wav" and path.stat().st_size > 44
            return {"text": "这是可编辑的实时转写结果"}

    application = QApplication.instance() or QApplication([])
    page = ClassroomPage(FakeApi(), lambda: "")
    page.session_id = "session-voice-test"
    page._voice_sample_rate = 16_000
    page._voice_channels = 1
    page._audio_buffer.extend(b"\x00\x00" * 4_000)
    page._request_voice_transcription(final=True)
    assert page._voice_future is not None
    page._voice_future.result(timeout=5)
    page._poll_voice_transcription()
    application.processEvents()

    assert page.answer.toPlainText() == "这是可编辑的实时转写结果"
    assert "继续编辑" in page.state.text()
    assert page.voice_button.isEnabled()
    assert not list((tmp_path / "temp" / "voice").glob("*.wav"))
    page.close()


def test_distributable_imports_checkout_databases_without_mutating_sources(
    tmp_path, monkeypatch
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    source_data = checkout / "data"
    source_data.mkdir()
    for name, sentinel in (("app.db", "legacy"), ("mentorflow_v2.db", "orm")):
        with sqlite3.connect(source_data / name) as connection:
            connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
            connection.execute("INSERT INTO sentinel(value) VALUES (?)", (sentinel,))
    upload = source_data / "active_teaching" / "uploads" / "ws-a"
    upload.mkdir(parents=True)
    (upload / "evidence.txt").write_text("private evidence", encoding="utf-8")
    fake_executable = checkout / "release" / "distributable" / "MentorFlow" / "MentorFlow.exe"
    fake_executable.parent.mkdir(parents=True)
    fake_executable.touch()
    monkeypatch.setattr("desktop.main.sys.executable", str(fake_executable))

    app_root = tmp_path / "local-app-data" / "MentorFlow"
    target_data = app_root / "data"
    _import_development_data_once(app_root, target_data)

    for name, sentinel in (("app.db", "legacy"), ("mentorflow_v2.db", "orm")):
        with sqlite3.connect(target_data / name) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT value FROM sentinel").fetchone()[0] == sentinel
        with sqlite3.connect(source_data / name) as connection:
            assert connection.execute("SELECT value FROM sentinel").fetchone()[0] == sentinel
    assert (target_data / "active_teaching" / "uploads" / "ws-a" / "evidence.txt").is_file()
    assert (app_root / "development-data-import.json").is_file()

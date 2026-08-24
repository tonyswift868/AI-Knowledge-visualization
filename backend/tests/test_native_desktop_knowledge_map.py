from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from desktop.knowledge_map_view import layered_positions
from desktop.materials_page import human_size
from desktop.plans_page import flattened_tasks
from desktop.classroom_page import (
    session_transcript,
    teacher_presentation_state,
    teacher_turn_text,
)


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


def test_native_knowledge_map_keeps_title_and_summary_items_alive(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QGraphicsTextItem

    from desktop.knowledge_map_view import KnowledgeMapView

    application = QApplication.instance() or QApplication([])
    view = KnowledgeMapView()
    view.resize(800, 500)
    view.set_graph(
        [
            {
                "id": "concept-random",
                "name": "随机概念标题",
                "explanation": "这里应当显示从资料证据提取出的重点说明。",
                "node_type": "concept",
                "layer": "material",
                "status": "confirmed",
            }
        ],
        [],
    )
    application.processEvents()

    texts = [
        item.toPlainText()
        for item in view.scene().items()
        if isinstance(item, QGraphicsTextItem)
    ]
    assert "随机概念标题" in texts
    assert any("资料证据提取" in value for value in texts)


def test_native_map_candidate_checks_persist_and_batch_decision_reloads_once(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton

    from desktop.api import ApiError
    from desktop.main_window import MainWindow

    class FakeApi:
        def __init__(self) -> None:
            self.batch_calls: list[tuple[str, list[str], str, str]] = []
            self.single_calls: list[str] = []
            self.candidates = [
                {"id": "candidate-a", "candidate_type": "node", "payload": {"name": "概念 A"}},
                {"id": "candidate-b", "candidate_type": "edge", "payload": {"name": "关系 B"}},
            ]

        def knowledge_map_candidates(self, _workspace_id: str) -> dict:
            return {"candidates": list(self.candidates)}

        def decide_knowledge_map_candidate(self, _workspace_id: str, candidate_id: str, **_kwargs) -> dict:
            self.single_calls.append(candidate_id)
            return {"revision": "legacy-revision"}

        def decide_knowledge_map_candidates(
            self,
            workspace_id: str,
            candidate_ids: list[str],
            *,
            decision: str,
            reason: str,
        ) -> dict:
            self.batch_calls.append((workspace_id, list(candidate_ids), decision, reason))
            return {
                "revision": 2,
                "revision_id": "revision-2",
                "results": [{"id": candidate_id, "status": decision} for candidate_id in candidate_ids],
                "decided_count": len(candidate_ids),
                "remaining_pending_count": 5,
            }

    application = QApplication.instance() or QApplication([])
    api = FakeApi()
    window = MainWindow.__new__(MainWindow)
    window.api = api
    window.map_workspace = QComboBox()
    window.map_workspace.addItem("任务", "workspace-1")
    window.map_candidates = QListWidget()
    window.map_state = QLabel()
    window.evidence_text = QPlainTextEdit()
    window.candidate_reason = QLineEdit()
    window.map_select_all_button = QPushButton()
    window._map_node_names = {}
    window._map_checked_candidate_ids = set()
    window._map_current_candidate_id = ""
    window.map_candidates.currentItemChanged.connect(window._show_candidate)
    window.map_candidates.itemChanged.connect(window._candidate_check_changed)
    window._refresh_candidates("workspace-1")

    assert window.map_candidates.count() == 2
    window.map_candidates.setCurrentRow(1)
    window._toggle_all_candidates()
    assert window.map_select_all_button.text() == "取消全选"
    assert all(
        window.map_candidates.item(index).checkState() == Qt.CheckState.Checked
        for index in range(window.map_candidates.count())
    )

    # Rebuilding the list keeps both the checked IDs and the current candidate ID.
    window._refresh_candidates("workspace-1")
    assert window._map_current_candidate_id == "candidate-b"
    assert window._map_checked_candidate_ids == {"candidate-a", "candidate-b"}

    reloads: list[bool] = []
    def reload_map() -> None:
        reloads.append(True)
        window.map_state.setText("图谱已刷新")

    window._load_selected_map = reload_map
    window._decide_candidate("confirmed")
    assert api.batch_calls == [("workspace-1", ["candidate-a", "candidate-b"], "confirmed", "")]
    assert api.single_calls == []
    assert reloads == [True]
    assert "图谱已刷新" in window.map_state.text()
    assert "已确认 2 项" in window.map_state.text()
    assert "剩余待审核 5 项" in window.map_state.text()

    for widget in (window.map_candidates, window.map_state, window.evidence_text, window.candidate_reason, window.map_select_all_button):
        widget.deleteLater()
    application.processEvents()


def test_native_map_batch_atomic_error_preserves_checked_selection_and_status(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton

    from desktop.api import ApiError
    from desktop.main_window import MainWindow

    class FakeApi:
        def __init__(self) -> None:
            self.batch_calls: list[list[str]] = []
            self.single_calls: list[str] = []
            self.candidates = [
                {"id": "candidate-a", "candidate_type": "node", "payload": {"name": "概念 A"}},
                {"id": "candidate-b", "candidate_type": "edge", "payload": {"name": "关系 B"}},
            ]

        def knowledge_map_candidates(self, _workspace_id: str) -> dict:
            return {"candidates": list(self.candidates)}

        def decide_knowledge_map_candidate(self, _workspace_id: str, candidate_id: str, **_kwargs) -> dict:
            self.single_calls.append(candidate_id)
            return {"revision": "legacy-revision"}

        def decide_knowledge_map_candidates(self, _workspace_id: str, candidate_ids: list[str], **_kwargs) -> dict:
            self.batch_calls.append(list(candidate_ids))
            raise ApiError(409, "KNOWLEDGE_MAP_CANDIDATE_STALE", "候选项所属 revision 已变化，请刷新后再决定")

    application = QApplication.instance() or QApplication([])
    api = FakeApi()
    window = MainWindow.__new__(MainWindow)
    window.api = api
    window.map_workspace = QComboBox()
    window.map_workspace.addItem("任务", "workspace-1")
    window.map_candidates = QListWidget()
    window.map_state = QLabel("图谱当前状态")
    window.evidence_text = QPlainTextEdit()
    window.candidate_reason = QLineEdit()
    window.map_select_all_button = QPushButton()
    window._map_node_names = {}
    window._map_checked_candidate_ids = set()
    window._map_current_candidate_id = ""
    window.map_candidates.currentItemChanged.connect(window._show_candidate)
    window.map_candidates.itemChanged.connect(window._candidate_check_changed)
    window._refresh_candidates("workspace-1")
    window._toggle_all_candidates()
    checked_before = [
        window.map_candidates.item(index).checkState()
        for index in range(window.map_candidates.count())
    ]

    reloads: list[bool] = []
    window._load_selected_map = lambda: reloads.append(True)
    window._decide_candidate("confirmed")

    assert api.batch_calls == [["candidate-a", "candidate-b"]]
    assert api.single_calls == []
    assert reloads == []
    assert "KNOWLEDGE_MAP_CANDIDATE_STALE" in window.map_state.text()
    assert "已确认" not in window.map_state.text()
    assert [
        window.map_candidates.item(index).checkState()
        for index in range(window.map_candidates.count())
    ] == checked_before == [Qt.CheckState.Checked, Qt.CheckState.Checked]

    for widget in (window.map_candidates, window.map_state, window.evidence_text, window.candidate_reason, window.map_select_all_button):
        widget.deleteLater()
    application.processEvents()


def test_native_material_size_formatting_is_stable() -> None:
    assert human_size(0) == "0 B"
    assert human_size(1536) == "1.5 KiB"
    assert human_size("not-a-number") == "—"


def test_native_plan_flattens_preview_and_prefers_execution_state() -> None:
    preview = {"days": [{"date": "2030-01-02", "tasks": [{"id": "draft", "title": "Draft"}]}]}
    assert flattened_tasks(preview)[0]["day_date"] == "2030-01-02"
    active = {**preview, "execution_tasks": [{"id": "live", "status": "planned"}]}
    assert flattened_tasks(active) == [{"id": "live", "status": "planned"}]
    legacy = {
        "study_days": [
            {"date": "2030-01-03", "tasks": [{"id": "legacy", "title": "Legacy"}]}
        ]
    }
    assert flattened_tasks(legacy)[0]["day_date"] == "2030-01-03"


def test_overview_progress_prefers_knowledge_route_over_execution_task_rows() -> None:
    from desktop.main_window import MainWindow

    assert MainWindow._task_progress(
        {
            "knowledge_point_count": 150,
            "completed_knowledge_point_count": 41,
            "task_count": 648,
            "completed_task_count": 167,
        }
    ) == (41, 150)


def test_overview_calendar_uses_completed_duration_as_effort(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication, QLabel

    from desktop.main_window import MainWindow
    from desktop.plans_page import PlanCalendar

    application = QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    window.overview_calendar = PlanCalendar()
    window.overview_calendar_summary = QLabel()
    window._render_overview_calendar(
        {
            "tasks": [
                {
                    "day_date": "2030-01-03",
                    "status": "completed",
                    "estimated_task_minutes": 25,
                    "completed_task_minutes": 25,
                    "completed_on": "2030-01-05",
                },
                {
                    "day_date": "2030-01-04",
                    "status": "planned",
                    "estimated_task_minutes": 40,
                },
                {
                    "day_date": "2030-01-04",
                    "status": "unscheduled",
                    "estimated_task_minutes": 90,
                },
            ],
            "activity_by_day": {
                "2030-01-05": {
                    "actual_elapsed_minutes": 3.5,
                    "activity_event_count": 1,
                }
            },
        }
    )

    completed = window.overview_calendar.summary_for(
        QDate(2030, 1, 5)
    )
    assert "完成任务时长 25 分钟" in completed
    assert "当天专注 3.5 分钟" in completed
    assert "已安排 0 项" in completed
    pending = window.overview_calendar.summary_for(QDate(2030, 1, 4))
    assert "已安排 1 项" in pending
    assert "待排期 1 项" in pending
    assert "计划 40 分钟" in pending
    assert window.overview_calendar._day_metrics["2030-01-05"]["ratio"] == 1.0
    application.processEvents()


def test_overview_calendar_falls_back_to_preserved_legacy_plan() -> None:
    from desktop.api import ApiError
    from desktop.main_window import MainWindow

    class FakeApi:
        def current_mastery_plan(self, _workspace_id: str) -> dict:
            raise ApiError(404, "ACTIVE_PLAN_NOT_FOUND", "no canonical plan")

        def current_legacy_plan(self, workspace_id: str) -> dict:
            return {"workspace_id": workspace_id, "tasks": [{"day_date": "2030-01-02"}]}

    window = MainWindow.__new__(MainWindow)
    window.api = FakeApi()

    result = window._current_plan_for_overview("legacy-workspace")

    assert result["workspace_id"] == "legacy-workspace"
    assert result["tasks"][0]["day_date"] == "2030-01-02"


def test_provider_model_upgrade_is_scoped_to_deepseek_official_host() -> None:
    from desktop.main_window import MainWindow

    assert (
        MainWindow._normalized_provider_model(
            "https://api.deepseek.com/v1", "deepseek-chat"
        )
        == "deepseek-v4-flash"
    )
    assert (
        MainWindow._normalized_provider_model(
            "https://custom.example/v1", "deepseek-chat"
        )
        == "deepseek-chat"
    )
    assert (
        MainWindow._normalized_provider_model(
            "https://api.deepseek.com", "my-custom-model"
        )
        == "my-custom-model"
    )


def test_embedded_web_target_blank_uses_safe_system_browser(monkeypatch) -> None:
    from PySide6.QtCore import QUrl

    from desktop import web_links

    callbacks = []
    opened: list[str] = []

    class Signal:
        def connect(self, callback):
            callbacks.append(callback)

    class Page:
        newWindowRequested = Signal()

    class Request:
        def __init__(self, url: str):
            self._url = QUrl(url)

        def requestedUrl(self):
            return self._url

    monkeypatch.setattr(
        web_links.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )

    page = Page()
    assert web_links.install_external_link_handler(page) is page
    assert len(callbacks) == 1
    assert callbacks[0](Request("https://example.org/original")) is True
    assert callbacks[0](Request("javascript:alert(1)")) is False
    assert callbacks[0](Request("file:///C:/private.txt")) is False
    assert opened == ["https://example.org/original"]


def test_overview_life_analogy_hides_private_keywords(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from desktop.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    window.settings = QSettings(str(tmp_path / "analogy.ini"), QSettings.Format.IniFormat)
    window.settings.setValue("analogy/keywords", "private-sentinel-keyword")
    window.provider_session_id = "verified-provider"
    window._life_example = {
        "title": "冰箱里的排队小游戏",
        "analogy": "先看谁快过期，再决定今晚先吃谁，省得蔬菜在角落开退休会。",
        "takeaway": "先找对象，再看条件，最后决定下一步。",
    }
    window.offline_fact_title = QLabel()
    window.offline_fact_body = QLabel()
    window.offline_fact_source = QLabel()
    window.fact_source_button = QPushButton()

    window._render_fact()

    rendered = " ".join(
        (
            window.offline_fact_title.text(),
            window.offline_fact_body.text(),
            window.offline_fact_source.text(),
        )
    )
    assert "private-sentinel-keyword" not in rendered
    assert "冰箱里的排队小游戏" in rendered
    assert "顺手记住" not in rendered
    assert "先找对象" in rendered
    assert not window.fact_source_button.isVisible()
    application.processEvents()


def test_overview_life_analogy_first_failure_restores_empty_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from concurrent.futures import Future

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from desktop.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    window.settings = QSettings(str(tmp_path / "analogy-first-failure.ini"), QSettings.Format.IniFormat)
    window.settings.setValue("analogy/keywords", "private-sentinel-keyword")
    window.provider_session_id = "verified-provider"
    window._life_example = {}
    window.offline_fact_title = QLabel("正在加载…")
    window.offline_fact_body = QLabel()
    window.offline_fact_source = QLabel()
    window.fact_source_button = QPushButton()
    window.next_fact_button = QPushButton("加载中…")
    window.next_fact_button.setEnabled(False)
    window.analogy_loading_status = QLabel("正在加载…")
    future: Future = Future()
    future.set_exception(RuntimeError("private provider failure"))
    window._personalized_rank_future = future

    window._poll_personalized_rank()

    assert window.offline_fact_title.text() == "准备一张生活小卡片"
    assert "正在加载" not in window.offline_fact_title.text()
    assert window.analogy_loading_status.text() == ""
    assert not window.analogy_loading_status.isVisible()
    assert window.next_fact_button.isEnabled()
    assert window.next_fact_button.text() == "换一张"
    application.processEvents()


def test_overview_life_analogy_refresh_failure_restores_previous_card(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from concurrent.futures import Future

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from desktop.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    window.settings = QSettings(str(tmp_path / "analogy-refresh-failure.ini"), QSettings.Format.IniFormat)
    window.settings.setValue("analogy/keywords", "private-sentinel-keyword")
    window.provider_session_id = "verified-provider"
    window._life_example = {
        "title": "原来的好卡片",
        "analogy": "这是上一张已经成功生成并保存的生活场景。",
        "takeaway": "上一张的结论仍然完整保留。",
    }
    window.offline_fact_title = QLabel("正在加载…")
    window.offline_fact_body = QLabel()
    window.offline_fact_source = QLabel()
    window.fact_source_button = QPushButton()
    window.next_fact_button = QPushButton("加载中…")
    window.next_fact_button.setEnabled(False)
    window.analogy_loading_status = QLabel("正在加载…")
    future: Future = Future()
    future.set_exception(RuntimeError("private provider failure"))
    window._personalized_rank_future = future

    window._poll_personalized_rank()

    assert window.offline_fact_title.text() == "原来的好卡片"
    assert window.offline_fact_body.text() == "这是上一张已经成功生成并保存的生活场景。"
    assert window.analogy_loading_status.text() == ""
    assert not window.analogy_loading_status.isVisible()
    assert window.next_fact_button.isEnabled()
    assert window.next_fact_button.text() == "换一张"
    application.processEvents()


def test_overview_life_analogy_prefetch_is_consumed_without_network_wait(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from desktop.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    window.settings = QSettings(str(tmp_path / "analogy-prefetch.ini"), QSettings.Format.IniFormat)
    window.settings.setValue("analogy/keywords", "private-sentinel-keyword")
    window.settings.setValue("provider/base_url", "https://api.deepseek.com/v1")
    window.settings.setValue("provider/model", "deepseek-v4-flash")
    window.provider_session_id = "verified-provider"
    window._life_example = {"title": "上一张", "analogy": "上一张内容", "takeaway": "上一张结论"}
    window._analogy_recent_cards = []
    window._analogy_prefetch_future = None
    window._analogy_prefetch_generation = 0
    window._personalized_rank_future = None
    window.offline_fact_title = QLabel()
    window.offline_fact_body = QLabel()
    window.offline_fact_source = QLabel()
    window.fact_source_button = QPushButton()
    window.next_fact_button = QPushButton("换一张")
    window.analogy_loading_status = QLabel()
    scheduled: list[bool] = []
    window._schedule_analogy_prefetch = lambda: scheduled.append(True)
    window._analogy_prefetched = {
        "schema": "mentorflow.life-analogy.prefetch.v4",
        "context": window._analogy_context_key(),
        "result": {
            "card": {
                "title": "群聊里发错的那句话",
                "analogy": "你在手机群聊里发错消息，撤回后重新选对联系人。",
                "takeaway": "失败时退回最近一步，再尝试尚未走过的选择。",
            },
            "memory": {"concept_key": "backtracking", "scene_family": "phone_messages"},
        },
    }

    window._next_fact()
    application.processEvents()

    assert window.offline_fact_title.text() == "群聊里发错的那句话"
    assert "手机群聊" in window.offline_fact_body.text()
    assert window._personalized_rank_future is None
    assert window.next_fact_button.isEnabled()
    assert scheduled == [True]


def test_overview_life_analogy_prefetch_fills_bounded_buffer_and_persists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from concurrent.futures import ThreadPoolExecutor
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QVBoxLayout

    from desktop.main_window import MainWindow

    monkeypatch.setattr("desktop.main_window.QTimer.singleShot", lambda *_args: None)

    application = QApplication.instance() or QApplication([])
    calls: list[int] = []

    class FakeApi:
        def generate_life_analogy(self, _session, _keywords, *, recent_cards, variation_seed):
            calls.append(variation_seed)
            return {
                "card": {
                    "title": "群聊里发错的那句话",
                    "analogy": "你在手机群聊里发错消息，撤回后重新选对联系人。",
                    "takeaway": "失败时退回最近一步，再尝试尚未走过的选择。",
                },
                "memory": {
                    "concept_key": f"backtracking-{variation_seed}",
                    "scene_family": "phone_messages",
                },
            }

    window = MainWindow.__new__(MainWindow)
    window.settings = QSettings(str(tmp_path / "analogy-prefetch-build.ini"), QSettings.Format.IniFormat)
    window.settings.setValue("analogy/keywords", "private-sentinel-keyword")
    window.settings.setValue("provider/base_url", "https://api.deepseek.com/v1")
    window.settings.setValue("provider/model", "deepseek-v4-flash")
    window.provider_session_id = "verified-provider"
    window.api = FakeApi()
    window._analogy_recent_cards = []
    window._analogy_prefetched = {}
    window._analogy_prefetch_queue = []
    window._analogy_prefetch_future = None
    window._analogy_prefetch_futures = set()
    window._analogy_prefetch_generation = 0
    window._analogy_prefetch_retry_count = 0
    window._analogy_click_pending = False
    window._analogy_executor = ThreadPoolExecutor(max_workers=2)
    try:
        window._schedule_analogy_prefetch()
        while window._analogy_prefetch_futures:
            future = next(iter(window._analogy_prefetch_futures))
            future.result(timeout=2)
            window._poll_analogy_prefetch(
                window._analogy_prefetch_generation,
                window._analogy_prefetch_context,
                future,
            )
        restored = window._load_prefetched_life_example()
        window._schedule_analogy_prefetch()
    finally:
        window._analogy_executor.shutdown(wait=True, cancel_futures=True)

    assert restored["result"]["card"]["title"] == "群聊里发错的那句话"
    assert sorted(calls) == [1, 2, 3]
    assert 2 <= len(window._load_prefetched_life_examples()) <= 3
    application.processEvents()


def test_life_analogy_slot_failure_is_bounded_to_one_provider_call() -> None:
    from desktop.main_window import MainWindow

    calls: list[int] = []

    class FakeApi:
        def generate_life_analogy(self, _session, _keywords, *, recent_cards, variation_seed):
            calls.append(variation_seed)
            raise RuntimeError("candidate rejected")

    window = MainWindow.__new__(MainWindow)
    window.api = FakeApi()
    with pytest.raises(RuntimeError, match="candidate rejected"):
        window._generate_life_analogy_resilient("session", "方向", [], 10)
    assert calls == [10]


def test_life_analogy_slot_rejects_malformed_success_without_serial_retry() -> None:
    from desktop.main_window import MainWindow

    calls: list[int] = []

    class FakeApi:
        def generate_life_analogy(self, _session, _keywords, *, recent_cards, variation_seed):
            calls.append(variation_seed)
            return {"state": "ready", "card": None}

    window = MainWindow.__new__(MainWindow)
    window.api = FakeApi()
    with pytest.raises(RuntimeError, match="missing card"):
        window._generate_life_analogy_resilient("session", "方向", [], 10)
    assert calls == [10]


def test_life_analogy_poll_clears_busy_for_payload_without_card(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from concurrent.futures import Future

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from desktop.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    window.settings = QSettings(str(tmp_path / "analogy-malformed.ini"), QSettings.Format.IniFormat)
    window.settings.setValue("analogy/keywords", "方向")
    window.provider_session_id = "verified-provider"
    window._life_example = {}
    window.offline_fact_title = QLabel("正在加载…")
    window.offline_fact_body = QLabel()
    window.offline_fact_source = QLabel()
    window.fact_source_button = QPushButton()
    window.next_fact_button = QPushButton("加载中…")
    window.next_fact_button.setEnabled(False)
    window.analogy_loading_status = QLabel("正在加载…")
    window._analogy_prefetch_retry_count = 0
    window._personalized_rank_future = Future()
    window._personalized_rank_future.set_result({"state": "ready", "card": None})

    window._poll_personalized_rank()

    assert window.analogy_loading_status.text() == ""
    assert not window.analogy_loading_status.isVisible()
    assert window.next_fact_button.isEnabled()
    assert window.next_fact_button.text() == "换一张"
    application.processEvents()


def test_life_analogy_poll_ignores_late_card_after_context_clear(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from concurrent.futures import Future

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from desktop.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    window.settings = QSettings(str(tmp_path / "analogy-stale.ini"), QSettings.Format.IniFormat)
    window.settings.setValue("analogy/keywords", "新方向")
    window.settings.setValue("provider/base_url", "https://new.example/v1")
    window.settings.setValue("provider/model", "new-model")
    window.provider_session_id = "new-provider"
    window._life_example = {
        "title": "上一张",
        "analogy": "上一张内容",
        "takeaway": "上一张结论",
    }
    window.offline_fact_title = QLabel()
    window.offline_fact_body = QLabel()
    window.offline_fact_source = QLabel()
    window.fact_source_button = QPushButton()
    window.next_fact_button = QPushButton("加载中…")
    window.next_fact_button.setEnabled(False)
    window.analogy_loading_status = QLabel("正在加载…")
    window._analogy_prefetch_generation = 0
    window._analogy_prefetch_future = None
    window._analogy_prefetch_context = ""
    window._analogy_prefetch_retry_count = 0
    stale_generation = window._analogy_prefetch_generation
    stale_context = window._analogy_context_key()
    future: Future = Future()
    future.set_result(
        {
            "card": {
                "title": "旧请求",
                "analogy": "旧请求不应覆盖新方向。",
                "takeaway": "旧请求不应覆盖新方向。",
            },
            "memory": {},
        }
    )
    window._personalized_rank_future = future

    window._clear_analogy_prefetch()
    window._poll_personalized_rank(stale_generation, stale_context)

    assert window._life_example["title"] == "上一张"
    assert window.offline_fact_title.text() == ""
    assert window._personalized_rank_future is None
    application.processEvents()


def test_native_task_index_does_not_resurrect_legacy_blank_workspaces() -> None:
    from desktop.main_window import MainWindow

    class FakeApi:
        def active_workspaces(self, *, include_archived: bool = False) -> dict:
            assert include_archived is False
            return {
                "items": [
                    {
                        "workspace_id": "active-plan",
                        "title": "真实学习计划",
                        "archived": False,
                        "updated_at": "2030-01-02T00:00:00Z",
                    }
                ]
            }

        def workspaces(self) -> list[dict]:
            raise AssertionError("canonical success must not merge legacy blank rows")

    window = MainWindow.__new__(MainWindow)
    window.api = FakeApi()

    assert window._workspace_items() == [
        {
            "workspace_id": "active-plan",
            "title": "真实学习计划",
            "archived": False,
            "updated_at": "2030-01-02T00:00:00Z",
        }
    ]


def test_overview_calendar_aggregates_all_visible_plans() -> None:
    from desktop.main_window import MainWindow

    class FakeApi:
        def current_mastery_plan(self, workspace_id: str) -> dict:
            if workspace_id == "new-plan":
                return {
                    "execution_tasks": [
                        {
                            "day_date": "2030-02-02",
                            "status": "planned",
                            "estimated_task_minutes": 10,
                        }
                    ]
                }
            return {
                "execution_tasks": [
                    {
                        "day_date": "2030-01-05",
                        "status": "completed",
                        "estimated_task_minutes": 25,
                    }
                ]
            }

    window = MainWindow.__new__(MainWindow)
    window.api = FakeApi()
    result = window._current_plans_for_overview(["new-plan", "older-plan"])

    assert len(result["plans"]) == 2
    assert flattened_tasks(result["plans"][1])[0]["status"] == "completed"


def test_plan_calendar_ignores_wheel_month_changes() -> None:
    from desktop.plans_page import PlanCalendar

    class FakeWheelEvent:
        ignored = False

        def ignore(self) -> None:
            self.ignored = True

    event = FakeWheelEvent()
    PlanCalendar.wheelEvent(None, event)
    assert event.ignored is True


def test_plan_calendar_forwards_wheel_to_outer_page_without_changing_month(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

    from desktop.plans_page import PlanCalendar

    application = QApplication.instance() or QApplication([])
    scroll = QScrollArea()
    scroll.resize(420, 220)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    calendar = PlanCalendar()
    content_layout.addWidget(calendar)
    content_layout.addSpacing(420)
    scroll.setWidget(content)
    scroll.show()
    application.processEvents()
    scroll.verticalScrollBar().setValue(100)
    before_value = scroll.verticalScrollBar().value()
    before_page = (calendar.yearShown(), calendar.monthShown())
    event = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )

    assert calendar.eventFilter(calendar._calendar_viewport, event) is True
    assert (calendar.yearShown(), calendar.monthShown()) == before_page
    assert scroll.verticalScrollBar().value() < before_value
    assert event.isAccepted()
    scroll.deleteLater()
    application.processEvents()


def test_overview_calendar_and_tutorial_stay_inside_responsive_bounds(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication, QWidget

    from desktop.main_window import MainWindow, SpotlightTutorial

    application = QApplication.instance() or QApplication([])
    for width, height in ((870, 720), (1266, 979)):
        window = MainWindow.__new__(MainWindow)
        window.user = {"display_name": "测试"}
        window._render_fact = lambda: None
        page = window._overview()
        page.resize(width, height)
        page.show()
        application.processEvents()
        viewport = window.overview_scroll.viewport()
        content = window.overview_scroll.widget()
        assert content.width() <= viewport.width()
        calendar = window.overview_calendar
        assert calendar.geometry().right() <= calendar.parentWidget().rect().right()
        page.deleteLater()

    parent = QWidget()
    parent.resize(1100, 720)
    parent.show()
    application.processEvents()
    overlay = SpotlightTutorial(
        parent,
        (("侧边栏", lambda _overlay: QRect(0, 0, 230, 300), "标题", "说明文字。"),),
        lambda: None,
    )
    overlay.setGeometry(parent.rect())
    overlay.show()
    application.processEvents()
    for size in ((1100, 720), (1496, 979)):
        overlay.resize(*size)
        application.processEvents()
        card = overlay.card.geometry()
        assert card.left() >= 0
        assert card.top() >= 0
        assert card.right() < overlay.width()
        assert card.bottom() < overlay.height()
    overlay.deleteLater()
    parent.deleteLater()
    application.processEvents()


def test_long_task_card_shrinks_without_hiding_its_action(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from desktop.main_window import TASK_THEMES, TaskCardWidget

    application = QApplication.instance() or QApplication([])
    card = TaskCardWidget(
        title="完全掌握上传资料，并能用可验证的方式独立应用，并且能独立完成一百五十道算法题",
        progress="进度 135 / 648 项",
        material_text="资料 1 份",
        status="学习中",
        completed=135,
        total=648,
        theme=TASK_THEMES["pink"],
        on_detail=lambda: None,
    )
    card.resize(720, 136)
    card.show()
    application.processEvents()

    detail = next(button for button in card.findChildren(QPushButton) if button.text() == "查看计划")
    assert detail.geometry().right() <= card.rect().right()
    assert detail.isVisible()
    assert card.minimumSizeHint().width() <= 720
    card.deleteLater()
    application.processEvents()


def test_responsive_task_list_refits_existing_card_after_window_resize(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QListWidgetItem, QPushButton

    from desktop.main_window import ResponsiveCardList, TASK_THEMES, TaskCardWidget

    application = QApplication.instance() or QApplication([])
    task_list = ResponsiveCardList()
    task_list.resize(900, 180)
    card = TaskCardWidget(
        title="这是一个足够长、会在窗口缩小时自动换行的学习计划标题",
        progress="进度 135 / 648 项",
        material_text="资料 1 份",
        status="学习中",
        completed=135,
        total=648,
        theme=TASK_THEMES["pink"],
        on_detail=lambda: None,
    )
    item = QListWidgetItem()
    item.setData(Qt.ItemDataRole.UserRole, "responsive-plan")
    task_list.addItem(item)
    task_list.setItemWidget(item, card)
    task_list.show()
    application.processEvents()

    task_list.resize(540, 180)
    application.processEvents()
    task_list.fit_cards_to_viewport()
    application.processEvents()

    detail = next(button for button in card.findChildren(QPushButton) if button.text() == "查看计划")
    assert item.sizeHint().width() <= task_list.viewport().width()
    assert card.width() <= task_list.viewport().width()
    assert detail.geometry().right() <= card.rect().right()
    task_list.deleteLater()
    application.processEvents()


def test_next_fact_click_keeps_button_available_and_consumes_pending_card(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from concurrent.futures import Future
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from desktop.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    window.settings = QSettings(str(tmp_path / "analogy-pending.ini"), QSettings.Format.IniFormat)
    window.settings.setValue("analogy/keywords", "pending-keyword")
    window.settings.setValue("provider/base_url", "https://api.example/v1")
    window.settings.setValue("provider/model", "model")
    window.provider_session_id = "provider-session"
    window._life_example = {"title": "上一张", "analogy": "上一张内容", "takeaway": "上一张结论"}
    window._analogy_recent_cards = []
    window._analogy_prefetch_generation = 0
    window._analogy_prefetch_retry_count = 0
    window._analogy_click_pending = False
    window._analogy_button_feedback = ""
    window._analogy_button_feedback_token = 0
    window.offline_fact_title = QLabel()
    window.offline_fact_body = QLabel()
    window.offline_fact_source = QLabel()
    window.fact_source_button = QPushButton()
    window.next_fact_button = QPushButton("换一张")
    window.analogy_loading_status = QLabel()
    future: Future = Future()
    window._analogy_prefetch_future = future
    window._analogy_prefetch_context = window._analogy_context_key()

    window._next_fact()
    assert window.next_fact_button.isEnabled()
    assert window.next_fact_button.text() == "正在加载…"
    assert window._analogy_click_pending is True

    future.set_result(
        {
            "card": {"title": "下一张", "analogy": "新的生活场景", "takeaway": "新的结论"},
            "memory": {},
        }
    )
    window._poll_analogy_prefetch(0, window._analogy_prefetch_context)
    application.processEvents()

    assert window.offline_fact_title.text() == "下一张"
    assert window.next_fact_button.isEnabled()
    assert window._analogy_prefetched == {}


def test_plans_refresh_honors_explicit_workspace_route(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from concurrent.futures import Future
    from PySide6.QtWidgets import QApplication

    from desktop.plans_page import PlansPage

    class FakeApi:
        def runtime_policy(self) -> dict:
            return {}

    application = QApplication.instance() or QApplication([])
    plans = PlansPage(FakeApi())
    plans._workspace_list_token = 1
    plans._requested_workspace_id = "second"
    future: Future = Future()
    future.set_result(
        [
            {"workspace_id": "first", "title": "第一项"},
            {"workspace_id": "second", "title": "第二项"},
        ]
    )
    plans._workspace_list_future = future
    plans.load_workspace = lambda: None
    plans._poll_workspace_list(1, "first")

    assert plans.workspace.currentData() == "second"
    application.processEvents()
    plans._load_executor.shutdown(wait=True, cancel_futures=True)


def test_task_color_refreshes_current_plan_button_immediately(tmp_path) -> None:
    from PySide6.QtCore import QSettings
    from desktop.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    window.settings = QSettings(str(tmp_path / "colors.ini"), QSettings.Format.IniFormat)
    calls: list[str] = []
    window.plans_page = type("Plans", (), {"_refresh_color_button": lambda _self, value: calls.append(value)})()
    window._refresh_tasks = lambda: None
    window._refresh_overview = lambda: None

    window._set_task_color("workspace-color", "pink")

    assert window.settings.value("task-colors/workspace-color") == "pink"
    assert calls == ["workspace-color"]


def test_plans_empty_table_is_replaced_and_rows_remain_reachable(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from desktop.plans_page import PlansPage

    class FakeApi:
        def runtime_policy(self) -> dict:
            return {}

    application = QApplication.instance() or QApplication([])
    plans = PlansPage(FakeApi())
    assert plans.table.isHidden()
    assert not plans.table_empty_state.isHidden()

    plans._render_plan(
        {
            "tasks": [
                {
                    "day_date": "2030-01-02",
                    "title": "可到达的任务行",
                    "estimated_task_minutes": 15,
                    "status": "planned",
                }
            ]
        }
    )
    assert not plans.table.isHidden()
    assert plans.table_empty_state.isHidden()
    assert plans.table.rowCount() == 1
    assert plans.table.item(0, 1).text() == "可到达的任务行"
    application.processEvents()


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


def test_native_classroom_blocks_actions_during_voice_and_keeps_voice_disabled_while_pending(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from concurrent.futures import Future
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from desktop.classroom_page import ClassroomPage

    class FakeApi:
        calls: list[dict] = []

        def advance_teaching_session(self, session_id: str, **payload) -> dict:
            self.calls.append({"session_id": session_id, **payload})
            return {"session_id": session_id, "state": {}, "messages": []}

    application = QApplication.instance() or QApplication([])
    api = FakeApi()
    page = ClassroomPage(api, lambda: "")
    page.session_id = "session-voice-race"
    page.answer.setPlainText("保留这段草稿")
    try:
        page._voice_recording = True
        page.send_answer()
        assert api.calls == []
        assert page._action_future is None
        assert page.answer.toPlainText() == "保留这段草稿"

        page._voice_recording = False
        page._voice_future = Future()
        page.skip_to_next_point()
        assert api.calls == []
        assert page._action_future is None
        assert page.answer.toPlainText() == "保留这段草稿"

        page._voice_future = None
        pending = Future()
        page._action_future = pending
        page._set_action_pending("send")
        page._voice_base_text = page.answer.toPlainText()
        page.answer.setPlainText("保留这段草稿\n语音补充")
        page._finish_voice_transcription("语音补充")
        assert page.voice_button is not None and not page.voice_button.isEnabled()
        assert page.answer.toPlainText() == "保留这段草稿\n语音补充"

        page._action_future = None
        page._restore_action_controls()
        assert page.voice_button is not None and page.voice_button.isEnabled()

        page.answer.setPlainText("原始回答")
        assert page._start_teaching_action(
            kind="send",
            session_id=page.session_id,
            message="原始回答",
            provider_session_id="",
            attachment_ids=[],
            force_complete=False,
            actual_elapsed_minutes=0,
            activity_event_id="desktop-test",
            workspace_id="",
        )
        page.answer.setPlainText("新转写草稿")
        deadline = time.perf_counter() + 2
        while page._action_future is not None and time.perf_counter() < deadline:
            application.processEvents()
            QTest.qWait(20)
        application.processEvents()
        assert page.answer.toPlainText() == "新转写草稿"
    finally:
        page.close()


def test_native_classroom_reports_bounded_active_time_with_idempotency_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from desktop.classroom_page import ClassroomPage

    class FakeApi:
        captured: dict = {}

        def advance_teaching_session(self, session_id: str, **payload) -> dict:
            self.captured = {"session_id": session_id, **payload}
            return {
                "session_id": session_id,
                "workspace_id": "workspace-time-test",
                "actual_elapsed_minutes": payload["actual_elapsed_minutes"],
                "state": {},
                "messages": [],
            }

        def materials(self, _workspace_id: str) -> list[dict]:
            return []

    application = QApplication.instance() or QApplication([])
    api = FakeApi()
    page = ClassroomPage(api, lambda: "")
    page.session_id = "session-time-test"
    page._active_learning_seconds = 75
    page.answer.setPlainText("这是一次真实课堂交互。")

    try:
        page.send_answer()
        deadline = time.perf_counter() + 2
        while page._action_future is not None and time.perf_counter() < deadline:
            application.processEvents()
            QTest.qWait(20)
        application.processEvents()

        assert api.captured["actual_elapsed_minutes"] == 1.25
        assert str(api.captured["activity_event_id"]).startswith("desktop-")
        assert page._active_learning_seconds == 0
    finally:
        page.close()


def test_native_classroom_free_chat_uses_isolated_payload_without_activity_minutes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from desktop.classroom_page import ClassroomPage

    class FakeApi:
        captured: dict = {}

        def advance_teaching_session(self, session_id: str, **payload) -> dict:
            self.captured = {"session_id": session_id, **payload}
            return {
                "session_id": session_id,
                "workspace_id": "workspace-free-chat",
                "actual_elapsed_minutes": 0,
                "state": {},
                "messages": [],
            }

        def materials(self, _workspace_id: str) -> list[dict]:
            return []

    application = QApplication.instance() or QApplication([])
    api = FakeApi()
    page = ClassroomPage(api, lambda: "")
    page.session_id = "session-free-chat"
    page._active_learning_seconds = 75
    page.free_chat_radio.click()
    page.answer.setPlainText("课堂之外，我想问一个相关问题。")
    try:
        page.send_answer()
        deadline = time.perf_counter() + 2
        while page._action_future is not None and time.perf_counter() < deadline:
            application.processEvents()
            QTest.qWait(20)
        application.processEvents()
        assert api.captured["interaction_mode"] == "free_chat"
        assert "force_complete" not in api.captured
        assert "verify_completion" not in api.captured
        assert "actual_elapsed_minutes" not in api.captured
        assert "activity_event_id" not in api.captured
        assert page._active_learning_seconds == 75
        assert page.interaction_notice is not None
        assert page.interaction_notice.text() == ""
        assert not page.interaction_notice.isVisible()
        assert "想聊什么都可以" in page.answer.placeholderText()
    finally:
        page.close()


def test_native_classroom_immersive_round_trip_preserves_draft_controls_and_radio_state(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from PySide6.QtWidgets import QApplication, QPushButton

    from desktop.classroom_page import ClassroomPage

    class FakeApi:
        def active_workspaces(self, *, include_archived: bool = False) -> dict:
            return {"items": []}

    application = QApplication.instance() or QApplication([])
    page = ClassroomPage(FakeApi(), lambda: "")
    page.show()
    page._view_stack.setCurrentIndex(1)
    page.answer.setPlainText("沉浸模式下也不能丢失的草稿")
    page.free_chat_radio.click()
    application.processEvents()
    try:
        assert page.interaction_mode == "free_chat"
        assert page.free_chat_radio.isChecked()
        assert not page.learning_evidence_radio.isChecked()
        assert page.free_chat_radio.styleSheet() == ""
        classroom_panel = page._view_stack.widget(1)
        assert "QRadioButton::indicator:checked" in classroom_panel.styleSheet()
        assert "#c95f8d" in classroom_panel.styleSheet()
        assert classroom_panel.objectName() == "classroomSessionPanel"
        assert classroom_panel.__class__.__name__ == "ClassroomAtmosphere"
        assert all(widget.isVisible() for widget in page._immersive_top_widgets)

        page.toggle_immersive_mode()
        application.processEvents()
        assert page.immersive_mode
        assert page._immersive_toggle.text() == "退出沉浸模式"
        assert all(not widget.isVisible() for widget in page._immersive_top_widgets)
        assert page.transcript.isVisible()
        assert page.answer.isVisible()
        assert page.answer.toPlainText() == "沉浸模式下也不能丢失的草稿"
        assert page.attach_button.isVisible()
        assert page.voice_button.isVisible()
        assert page.complete_button.isVisible()
        assert page.send_button.isVisible()
        assert page.free_chat_radio.isChecked()
        # The stage is detached and lazy.  There is exactly one stable stage
        # entry point (the upper-right header button), so immersive mode does
        # not introduce a duplicate composer-side toggle.
        assert page._teacher_stage_panel is None
        assert len(page.findChildren(QPushButton, "teacherStageToggleButton")) == 1
        assert not page._teacher_stage_button.isVisible()

        page.toggle_immersive_mode()
        application.processEvents()
        assert not page.immersive_mode
        assert all(widget.isVisible() for widget in page._immersive_top_widgets)
        assert page._teacher_stage_button.isVisible()
        assert page.answer.toPlainText() == "沉浸模式下也不能丢失的草稿"
    finally:
        page.close()


def test_native_classroom_skin_switch_preserves_session_draft_and_updates_stage(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    import desktop.classroom_page as classroom_module

    settings = QSettings(str(tmp_path / "classroom-theme.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(classroom_module, "QSettings", lambda *_args, **_kwargs: settings)

    class FakeApi:
        def active_workspaces(self, *, include_archived: bool = False) -> dict:
            return {"items": []}

    application = QApplication.instance() or QApplication([])
    page = classroom_module.ClassroomPage(FakeApi(), lambda: "")
    page.answer.setPlainText("换皮肤也不能丢的草稿")
    page.current_session = {"session_id": "session-theme", "state": {"status": "learning"}}
    page.transcript.set_session({
        "current_point": {
            "messages": [{"role": "teacher", "content": "主题切换时这条消息不能被重建。"}]
        }
    })
    application.processEvents()
    original_body = page.transcript.findChild(classroom_module.QTextBrowser, "transcriptMessageBody")
    original_bubble = page.transcript.findChild(classroom_module.QFrame, "teacherMessageBubble")
    try:
        assert page.classroom_theme_key == "sakura"
        assert "#c95f8d" in page._classroom_panel.styleSheet()

        page.apply_theme("rainy_window")
        page._ensure_teacher_stage_window()
        application.processEvents()
        assert page.classroom_theme_key == "rainy_window"
        assert settings.value("appearance/classroom_theme") == "rainy_window"
        assert "#78b7d4" in page._classroom_panel.styleSheet()
        assert "#78b7d4" in page._teacher_stage_panel.styleSheet()
        assert page.transcript.findChild(classroom_module.QTextBrowser, "transcriptMessageBody") is original_body
        assert page.transcript.findChild(classroom_module.QFrame, "teacherMessageBubble") is original_bubble
        assert page.answer.toPlainText() == "换皮肤也不能丢的草稿"
        assert page.current_session["session_id"] == "session-theme"

        page._set_action_pending("send")
        assert page._typing_timer.isActive()
        page._advance_typing_indicator()
        assert "教师正在思考" in page._conversation_status.text()
        page._restore_action_controls()
        assert not page._typing_timer.isActive()
        assert page._conversation_status.text() == "●  教师在线"
    finally:
        page.close()


def test_native_teacher_stage_state_follows_generic_classroom_semantics() -> None:
    assert teacher_presentation_state({}) == "idle"
    assert teacher_presentation_state({}, busy=True) == "thinking"
    assert teacher_presentation_state({"state": {"teacher_turn_v1": {"action": "daily_briefing"}}}) == "greeting"
    assert teacher_presentation_state({"state": {"teacher_turn_v1": {"action": "check_understanding"}}}) == "questioning"
    assert teacher_presentation_state({"state": {"teacher_turn_v1": {"action": "remediation_example"}}}) == "encouraging"
    assert teacher_presentation_state({"state": {"teacher_turn_v1": {"action": "advance_route"}}}) == "praising"
    assert teacher_presentation_state({"state": {"teacher_turn_v1": {"action": "source_conflict"}}}) == "confused"
    assert teacher_presentation_state({"state": {"latest_free_chat_turn_v1": {"action": "free_chat_response"}}}) == "talking"
    assert teacher_presentation_state({"state": {"teacher_turn_v1": {"presentation_state": "shy"}}}) == "shy"
    assert teacher_presentation_state({"state": {"teacher_presentation_state": "playful_annoyed"}}) == "playful_annoyed"
    assert teacher_presentation_state({"state": {"teacher_turn_v1": {"state": {"status": "completed"}}}}) == "ending"


def test_native_teacher_stage_is_visible_before_web_runtime_mounts(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import desktop.classroom_page as classroom_module

    class FakeApi:
        def active_workspaces(self, *, include_archived: bool = False) -> dict:
            return {"items": []}

    class FakeStageWindow:
        def __init__(self) -> None:
            self.visible = False
            self.events: list[str] = []

        def isVisible(self) -> bool:
            return self.visible

        def attach_to(self, _host) -> None:
            self.events.append("attach")

        def sync_to_host(self) -> None:
            self.events.append("sync")

        def show(self) -> None:
            self.visible = True
            self.events.append("show")

        def raise_(self) -> None:
            self.events.append("raise")

    QApplication.instance() or QApplication([])
    page = classroom_module.ClassroomPage(FakeApi(), lambda: "", origin="http://127.0.0.1:1")
    stage = FakeStageWindow()
    page._teacher_stage_window = stage
    try:
        page._ensure_teacher_stage_web = lambda: stage.events.append(
            "mount-visible" if stage.visible else "mount-hidden"
        )
        page.open_teacher_stage()
        assert stage.events == ["attach", "sync", "show", "raise", "mount-visible"]
    finally:
        page.close()


def test_native_teacher_action_reviews_persist_only_in_local_development_settings(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QVBoxLayout

    import desktop.classroom_page as classroom_module

    stored: dict[str, object] = {}

    class FakeSettings:
        def __init__(self, *_args) -> None:
            return None

        def value(self, key: str, default=None):
            return stored.get(key, default)

        def setValue(self, key: str, value: object) -> None:
            stored[key] = value

        def sync(self) -> None:
            return None

    class FakeApi:
        def active_workspaces(self, *, include_archived: bool = False) -> dict:
            return {"items": []}

    monkeypatch.setattr(classroom_module, "QSettings", FakeSettings)
    QApplication.instance() or QApplication([])
    page = classroom_module.ClassroomPage(FakeApi(), lambda: "")
    try:
        page._teacher_action_debug_actions = [
            {"id": "BLINK", "label": "眨眼"},
            {"id": "motion:Cry:0", "label": "哭泣"},
        ]
        page._review_teacher_action("BLINK", "excellent")
        page._review_teacher_action("motion:Cry:0", "weak")
        assert [item["id"] for item in page._teacher_action_debug_actions] == ["BLINK"]
        restored = page._load_teacher_action_reviews()
        assert restored == {"BLINK": "excellent", "motion:Cry:0": "weak"}
        assert all("session" not in key and "learning" not in key for key in stored)
        page._review_teacher_action("BLINK", "excellent")
        assert page._load_teacher_action_reviews() == {"motion:Cry:0": "weak"}

        # A later model capability refresh cannot re-add locally rejected
        # actions to the tester.
        page._teacher_action_debug_rows = QVBoxLayout()
        page._teacher_action_debug_rows.addStretch(1)
        page._render_teacher_action_debug_rows(
            {
                "ready": True,
                "actions": [
                    {"id": "BLINK", "label": "眨眼"},
                    {"id": "motion:Cry:0", "label": "哭泣"},
                ],
            }
        )
        assert all(
            item["id"] != "motion:Cry:0"
            for item in page._teacher_action_debug_actions
        )
    finally:
        page.close()


def test_teacher_stage_window_clamps_to_active_screen_when_host_touches_edge(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMainWindow

    from desktop.classroom_page import TeacherStageWindow

    class Owner:
        def _on_teacher_stage_hidden(self) -> None:
            return None

    application = QApplication.instance() or QApplication([])
    host = QMainWindow()
    host.setGeometry(0, 0, 780, 700)
    host.show()
    application.processEvents()
    stage = TeacherStageWindow(Owner())
    stage.attach_to(host)
    stage.sync_to_host()
    try:
        available = host.screen().availableGeometry()
        assert available.contains(stage.frameGeometry().topLeft())
        assert available.contains(stage.frameGeometry().bottomRight())
        assert stage.width() <= 500
        assert stage.height() == min(host.frameGeometry().height(), available.height())
    finally:
        stage.hide()
        host.close()


def test_native_classroom_submit_and_mastery_are_nonblocking_and_fetch_materials_off_thread(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QTextBrowser

    from desktop.classroom_page import ClassroomPage

    class SlowApi:
        calls: list[dict] = []
        material_calls = 0

        def active_workspaces(self, *, include_archived: bool = False) -> dict:
            return {"items": []}

        def advance_teaching_session(self, session_id: str, **payload) -> dict:
            time.sleep(0.2)
            self.calls.append({"session_id": session_id, **payload})
            return {
                "session_id": session_id,
                "workspace_id": "workspace-slow",
                "state": {},
                "messages": [],
            }

        def materials(self, workspace_id: str) -> list[dict]:
            assert workspace_id == "workspace-slow"
            type(self).material_calls += 1
            time.sleep(0.2)
            return [{"id": "material-1", "original_url": "https://example.test/material"}]

    def wait_for_action(page, application) -> None:
        deadline = time.perf_counter() + 3
        while page._action_future is not None and time.perf_counter() < deadline:
            application.processEvents()
            QTest.qWait(20)
        application.processEvents()
        assert page._action_future is None

    application = QApplication.instance() or QApplication([])
    api = SlowApi()
    page = ClassroomPage(api, lambda: "provider-test")
    page.session_id = "session-slow"
    page.current_session = {"workspace_id": "workspace-slow"}
    page.answer.setPlainText("回答不会阻塞界面")
    try:
        started = time.perf_counter()
        page.send_answer()
        assert time.perf_counter() - started < 0.1
        assert page.send_button is not None and not page.send_button.isEnabled()
        assert page.send_button.text() == "正在提交…"
        assert page.answer.isReadOnly()
        assert any(
            "回答不会阻塞界面" in body.toPlainText()
            for body in page.transcript.findChildren(QTextBrowser)
        )
        wait_for_action(page, application)
        assert page.send_button is not None and page.send_button.isEnabled()
        assert page.answer.toPlainText() == ""
        assert SlowApi.material_calls == 1

        started = time.perf_counter()
        page.skip_to_next_point()
        assert time.perf_counter() - started < 0.1
        assert page.complete_button is not None and not page.complete_button.isEnabled()
        assert page.complete_button.text() == "正在进入下一知识点…"
        wait_for_action(page, application)
        assert page.complete_button is not None and page.complete_button.isEnabled()
        assert any(call.get("force_complete") is True for call in api.calls)
        assert SlowApi.material_calls == 2
    finally:
        page.close()


def test_native_classroom_failed_submit_preserves_input_and_restores_controls(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from desktop.api import ApiError
    from desktop.classroom_page import ClassroomPage

    class FailingApi:
        def active_workspaces(self, *, include_archived: bool = False) -> dict:
            return {"items": []}

        def advance_teaching_session(self, _session_id: str, **_payload) -> dict:
            time.sleep(0.15)
            raise ApiError(503, "TEACHER_UNAVAILABLE", "教师服务暂时不可用")

    application = QApplication.instance() or QApplication([])
    page = ClassroomPage(FailingApi(), lambda: "")
    page.session_id = "session-failure"
    page.answer.setPlainText("失败时仍要保留这段回答")
    try:
        page.send_answer()
        assert page.send_button is not None and not page.send_button.isEnabled()
        deadline = time.perf_counter() + 2
        while page._action_future is not None and time.perf_counter() < deadline:
            application.processEvents()
            QTest.qWait(20)
        application.processEvents()
        assert page.answer.toPlainText() == "失败时仍要保留这段回答"
        assert not page.answer.isReadOnly()
        assert page.send_button is not None and page.send_button.isEnabled()
        assert "TEACHER_UNAVAILABLE" in page.state.text()
    finally:
        page.close()


def test_native_classroom_ignores_stale_result_after_exit(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from threading import Event

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QTextBrowser

    from desktop.classroom_page import ClassroomPage

    started = Event()
    release = Event()

    class BlockingApi:
        def active_workspaces(self, *, include_archived: bool = False) -> dict:
            return {"items": []}

        def advance_teaching_session(self, session_id: str, **_payload) -> dict:
            started.set()
            release.wait(2)
            return {"session_id": session_id, "workspace_id": "workspace-stale", "state": {}, "messages": []}

        def materials(self, _workspace_id: str) -> list[dict]:
            return []

    application = QApplication.instance() or QApplication([])
    page = ClassroomPage(BlockingApi(), lambda: "")
    page.session_id = "session-stale"
    page.answer.setPlainText("这条结果不能覆盖退出状态")
    try:
        page.send_answer()
        assert started.wait(1)
        page.exit_classroom()
        assert page.session_id == ""
        transcript_bodies = page.transcript.findChildren(QTextBrowser)
        assert any("已退出本次课堂" in body.toPlainText() for body in transcript_bodies)
        release.set()
        QTest.qWait(250)
        application.processEvents()
        assert page.session_id == ""
        transcript_bodies = page.transcript.findChildren(QTextBrowser)
        assert any("已退出本次课堂" in body.toPlainText() for body in transcript_bodies)
    finally:
        release.set()
        page.close()


def test_native_classroom_message_copy_buttons_copy_plain_teacher_and_learner_text(
    monkeypatch,
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from desktop.classroom_page import ClassroomTranscriptView

    application = QApplication.instance() or QApplication([])
    view = ClassroomTranscriptView()
    view.set_session(
        {
            "current_point": {
                "messages": [
                    {
                        "role": "teacher",
                        "teacher_turn": {
                            "concept": {"name": "复制概念"},
                            "blocks": [{"type": "explanation", "content": "教师纯文本"}],
                        },
                    },
                    {"role": "user", "content": "学习者纯文本"},
                ]
            }
        }
    )
    application.processEvents()
    buttons = view.findChildren(QPushButton, "messageCopyButton")
    assert len(buttons) == 2
    buttons[0].click()
    assert QApplication.clipboard().text() == "当前概念：复制概念\n\n【核心讲解】\n教师纯文本"
    buttons[1].click()
    assert QApplication.clipboard().text() == "学习者纯文本"
    view.close()


def test_native_classroom_message_copy_hides_internal_focus_markers(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from desktop.classroom_page import ClassroomTranscriptView

    application = QApplication.instance() or QApplication([])
    view = ClassroomTranscriptView()
    view.set_session(
        {
            "current_point": {
                "messages": [
                    {
                        "role": "teacher",
                        "teacher_turn": {
                            "blocks": [
                                {
                                    "type": "explanation",
                                    "content": "先记住[[重点:循环不变量]]，再检查[[难点：边界条件]]。",
                                }
                            ]
                        },
                    }
                ]
            }
        }
    )
    application.processEvents()
    button = view.findChild(QPushButton, "messageCopyButton")
    assert button is not None
    button.click()
    copied = QApplication.clipboard().text()
    assert "重点：循环不变量" in copied
    assert "难点：边界条件" in copied
    assert "[[" not in copied and "]]" not in copied
    view.close()


def test_slow_native_actions_return_immediately_and_finish_in_background(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_LOCAL_DATA_MODE", "1")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from desktop.materials_page import MaterialsPage
    from desktop.plans_page import PlansPage
    import desktop.system_page as system_module

    class FakeApi:
        def runtime_policy(self) -> dict:
            return {
                "scheduling": {
                    "minimum_daily_minutes": 1,
                    "maximum_daily_minutes": 720,
                    "default_daily_minutes": 30,
                    "minimum_review_rounds": 1,
                    "maximum_review_rounds": 4,
                    "default_review_rounds": 2,
                }
            }

    application = QApplication.instance() or QApplication([])
    plans = PlansPage(FakeApi())
    started = time.perf_counter()
    assert plans._start_plan_action(
        "save",
        lambda: (time.sleep(0.2), {"allocation": {}})[1],
        "正在保存学习条件…",
    )
    assert time.perf_counter() - started < 0.1
    assert not plans.save_button.isEnabled()

    materials = MaterialsPage(FakeApi())
    started = time.perf_counter()
    assert materials._start_operation(
        "archive",
        lambda: time.sleep(0.2),
        {"archived": True},
    )
    assert time.perf_counter() - started < 0.1

    monkeypatch.setattr(
        system_module,
        "diagnostics_text",
        lambda *_args, **_kwargs: (time.sleep(0.2), "app.db：1.0 MiB\n  完整性：ok\n  外键错误：0")[1],
    )
    started = time.perf_counter()
    settings = system_module.SystemPage({}, "", lambda: None, FakeApi())
    assert time.perf_counter() - started < 0.1
    assert not settings.refresh_button.isEnabled()

    QTest.qWait(350)
    application.processEvents()
    assert plans.save_button.isEnabled()
    assert plans.status.text().startswith("学习条件已保存")
    assert settings.refresh_button.isEnabled()
    assert "完整性：ok" in settings.report.toPlainText()

    plans._action_executor.shutdown(wait=True)
    materials._executor.shutdown(wait=True)
    settings._executor.shutdown(wait=True)


def test_distributable_imports_checkout_databases_without_mutating_sources(
    tmp_path, monkeypatch
) -> None:
    # Importing the desktop entry point configures process-wide runtime flags.
    # Keep that side effect scoped to the one test that needs this helper.
    from desktop.main import _import_development_data_once

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


def test_native_map_progress_uses_persisted_backend_units(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QProgressBar

    from desktop.main_window import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow.__new__(MainWindow)
    window.map_progress = QProgressBar()
    window.map_state = QLabel()

    window._render_map_job_progress(
        {
            "status": "extracting",
            "progress": {
                "schema": "mentorflow.knowledge-map-progress.v1",
                "completed_units": 3,
                "total_units": 8,
                "percent": 37.5,
                "message": "已完成 2/4 个真实提取批次",
            },
        }
    )

    assert window.map_progress.value() == 38
    assert "3/8" in window.map_progress.format()
    assert "提取知识" in window.map_progress.format()
    assert window.map_state.text() == "已完成 2/4 个真实提取批次"
    window.map_progress.deleteLater()
    window.map_state.deleteLater()
    application.processEvents()

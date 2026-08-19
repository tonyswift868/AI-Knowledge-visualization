from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from desktop.knowledge_map_view import layered_positions
from desktop.materials_page import human_size
from desktop.plans_page import flattened_tasks
from desktop.classroom_page import session_transcript, teacher_turn_text


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
    from PySide6.QtWidgets import QApplication

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


def test_native_classroom_reports_bounded_active_time_with_idempotency_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
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

    page.send_answer()
    application.processEvents()

    assert api.captured["actual_elapsed_minutes"] == 1.25
    assert str(api.captured["activity_event_id"]).startswith("desktop-")
    assert page._active_learning_seconds == 0


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

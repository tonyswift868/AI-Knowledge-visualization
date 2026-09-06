from __future__ import annotations

from concurrent.futures import Future
from typing import Callable

import pytest


class FakeTimer:
    """Queue Qt callbacks so each async transition can be asserted directly."""

    def __init__(self) -> None:
        self.callbacks: list[tuple[int, Callable[[], None]]] = []

    def single_shot(self, delay: int, callback: Callable[[], None]) -> None:
        self.callbacks.append((int(delay), callback))

    def run_next(self, delay: int) -> None:
        for index, (scheduled_delay, callback) in enumerate(self.callbacks):
            if scheduled_delay == delay:
                self.callbacks.pop(index)
                callback()
                return
        raise AssertionError(f"no queued callback with delay {delay}")

    def run_all(self, delay: int) -> None:
        while any(scheduled_delay == delay for scheduled_delay, _ in self.callbacks):
            self.run_next(delay)


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def generate_life_analogy(
        self,
        session: str,
        keywords: str,
        *,
        recent_cards: list[dict[str, str]],
        variation_seed: int,
    ) -> dict[str, object]:
        self.calls.append((session, keywords, variation_seed))
        number = len(self.calls)
        return {
            "card": {
                "title": f"Generated card {number}",
                "analogy": f"An everyday scene for card {number}.",
                "takeaway": f"A useful idea for card {number}.",
            },
            "memory": {"scene_family": "everyday"},
        }


class FakeExecutor:
    def __init__(self) -> None:
        self.submissions: list[tuple[Future, Callable[..., object], tuple[object, ...]]] = []

    def submit(self, callback: Callable[..., object], *args: object) -> Future:
        future: Future = Future()
        self.submissions.append((future, callback, args))
        return future

    def resolve(self, future: Future) -> None:
        for candidate, callback, args in self.submissions:
            if candidate is future:
                try:
                    candidate.set_result(callback(*args))
                except Exception as exc:  # pragma: no cover - exercised by fail()
                    candidate.set_exception(exc)
                return
        raise AssertionError("future was not submitted to fake executor")

    def fail(self, future: Future, error: Exception) -> None:
        for candidate, _, _ in self.submissions:
            if candidate is future:
                candidate.set_exception(error)
                return
        raise AssertionError("future was not submitted to fake executor")

    @property
    def pending(self) -> list[Future]:
        return [future for future, _, _ in self.submissions if not future.done()]


def _card(window, title: str) -> dict[str, object]:
    return {
        "schema": "mentorflow.life-analogy.prefetch.v4",
        "context": window._analogy_context_key(),
        "result": {
            "card": {
                "title": title,
                "analogy": f"An everyday scene for {title}.",
                "takeaway": f"A useful idea from {title}.",
            },
            "memory": {"scene_family": "everyday"},
        },
    }


@pytest.fixture()
def qt_app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _window(tmp_path, qt_app):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QLabel, QPushButton

    from desktop.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    from desktop.account_identity import AccountProfile
    window.account_profile = AccountProfile.personal_local()
    window.settings = QSettings(
        str(tmp_path / "life-analogy-prefetch.ini"), QSettings.Format.IniFormat
    )
    window.settings.setValue("analogy/keywords", "general concepts")
    window.settings.setValue("provider/base_url", "https://provider.invalid/v1")
    window.settings.setValue("provider/model", "test-model")
    window.provider_session_id = "session"
    window.api = FakeProvider()
    window._analogy_recent_cards = []
    window._life_example = {
        "title": "Last good card",
        "analogy": "The last good card remains visible.",
        "takeaway": "Keep the useful explanation available.",
    }
    window._analogy_prefetch_queue = []
    window._analogy_prefetched = {}
    window._analogy_card_library = []
    window._analogy_library_cursor = 0
    window._analogy_prefetch_future = None
    window._analogy_prefetch_futures = set()
    window._analogy_prefetch_context = ""
    window._analogy_prefetch_generation = 0
    window._analogy_prefetch_retry_count = 0
    window._analogy_click_pending = False
    window._analogy_button_feedback = ""
    window._analogy_button_feedback_token = 0
    window._analogy_executor = FakeExecutor()
    window.offline_fact_title = QLabel()
    window.offline_fact_body = QLabel()
    window.offline_fact_source = QLabel()
    window.fact_source_button = QPushButton()
    window.next_fact_button = QPushButton("换一张")
    window.analogy_loading_status = QLabel("technical details should stay hidden")
    window._current_fact_url = ""
    window._update_analogy_button_state()
    return window


def _patch_timer(monkeypatch, timer: FakeTimer) -> None:
    from desktop import main_window

    monkeypatch.setattr(
        main_window.QTimer,
        "singleShot",
        staticmethod(timer.single_shot),
    )


def test_initial_hidden_fill_is_bounded_to_three_ready_or_inflight(
    tmp_path, qt_app, monkeypatch
) -> None:
    from desktop.main_window import MainWindow

    timer = FakeTimer()
    _patch_timer(monkeypatch, timer)
    window = _window(tmp_path, qt_app)
    executor: FakeExecutor = window._analogy_executor

    window._schedule_analogy_prefetch()
    assert len(window._analogy_prefetch_queue) + len(executor.pending) <= 3
    assert len(executor.pending) == 3

    while executor.pending:
        future = executor.pending[0]
        executor.resolve(future)
        window._poll_analogy_prefetch(
            window._analogy_prefetch_generation,
            window._analogy_prefetch_context,
            future,
        )
        assert len(window._analogy_prefetch_queue) + len(executor.pending) <= 3
        if any(delay == 0 for delay, _ in timer.callbacks):
            timer.run_next(0)
        assert len(window._analogy_prefetch_queue) + len(executor.pending) <= 3

    assert len(window._analogy_prefetch_queue) == 3
    assert len(executor.pending) == 0
    assert len(window._analogy_prefetch_queue) + len(executor.pending) <= 3


def test_two_rapid_clicks_consume_ready_cards_and_refill_in_background(
    tmp_path, qt_app, monkeypatch
) -> None:
    timer = FakeTimer()
    _patch_timer(monkeypatch, timer)
    window = _window(tmp_path, qt_app)
    window._analogy_prefetch_queue = [_card(window, "Ready one"), _card(window, "Ready two"), _card(window, "Ready three")]
    window._analogy_prefetched = window._analogy_prefetch_queue[0]
    executor: FakeExecutor = window._analogy_executor

    window._next_fact()
    assert window._life_example["title"] == "Ready one"
    window._next_fact()
    assert window._life_example["title"] == "Ready two"
    assert len(window._analogy_prefetch_queue) == 1
    assert executor.submissions == []

    timer.run_all(0)

    assert len(executor.pending) == 2
    assert len(window._analogy_prefetch_queue) + len(executor.pending) == 3
    assert window.next_fact_button.isEnabled()


def test_clicks_beyond_ready_buffer_cycle_generated_library_without_waiting(
    tmp_path, qt_app, monkeypatch
) -> None:
    timer = FakeTimer()
    _patch_timer(monkeypatch, timer)
    window = _window(tmp_path, qt_app)
    window._analogy_prefetch_queue = [
        _card(window, "Ready one"),
        _card(window, "Ready two"),
        _card(window, "Ready three"),
    ]
    window._analogy_prefetched = window._analogy_prefetch_queue[0]

    for expected in ("Ready one", "Ready two", "Ready three", "Ready one"):
        window._next_fact()
        assert window._life_example["title"] == expected
        assert window.next_fact_button.text() != "正在加载…"

    assert len(window._analogy_card_library) == 3
    assert window._analogy_click_pending is False


def test_click_during_pending_generation_shows_loading_and_auto_consumes_when_ready(
    tmp_path, qt_app, monkeypatch
) -> None:
    timer = FakeTimer()
    _patch_timer(monkeypatch, timer)
    window = _window(tmp_path, qt_app)
    executor: FakeExecutor = window._analogy_executor

    window._schedule_analogy_prefetch()
    pending = executor.pending[0]
    window._next_fact()

    assert window._life_example["title"] == "Last good card"
    assert window.next_fact_button.isEnabled()
    assert window.next_fact_button.text() == "正在加载…"
    assert window.analogy_loading_status.text() == ""
    assert not window.analogy_loading_status.isVisible()
    assert window._analogy_click_pending is True

    executor.resolve(pending)
    window._poll_analogy_prefetch(
        window._analogy_prefetch_generation,
        window._analogy_prefetch_context,
        pending,
    )
    timer.run_next(0)

    assert window._life_example["title"] == "Generated card 1"
    assert window._analogy_click_pending is False
    assert window.next_fact_button.isEnabled()
    assert window.analogy_loading_status.text() == ""
    assert not window.analogy_loading_status.isVisible()


def test_failed_slot_preserves_last_good_card_and_retries_without_deadlock(
    tmp_path, qt_app, monkeypatch
) -> None:
    timer = FakeTimer()
    _patch_timer(monkeypatch, timer)
    window = _window(tmp_path, qt_app)
    executor: FakeExecutor = window._analogy_executor

    window._schedule_analogy_prefetch()
    failed, *still_pending = executor.pending
    executor.fail(failed, RuntimeError("provider unavailable"))
    window._poll_analogy_prefetch(
        window._analogy_prefetch_generation,
        window._analogy_prefetch_context,
        failed,
    )

    assert window._life_example["title"] == "Last good card"
    assert window.offline_fact_title.text() == "Last good card"
    assert window.offline_fact_body.text() == "The last good card remains visible."
    assert window.next_fact_button.isEnabled()
    assert window.next_fact_button.text() == "换一张"
    assert window.analogy_loading_status.text() == ""
    assert not window.analogy_loading_status.isVisible()

    timer.run_next(900)
    assert all(future in executor.pending for future in still_pending)
    assert len(executor.pending) == 3
    assert len(window._analogy_prefetch_queue) + len(executor.pending) <= 3


def test_context_change_rejects_stale_completion(tmp_path, qt_app, monkeypatch) -> None:
    timer = FakeTimer()
    _patch_timer(monkeypatch, timer)
    window = _window(tmp_path, qt_app)
    executor: FakeExecutor = window._analogy_executor

    window._schedule_analogy_prefetch()
    stale_generation = window._analogy_prefetch_generation
    stale_context = window._analogy_prefetch_context
    stale_future = executor.pending[0]
    stale_future.set_result(
        {
            "card": {
                "title": "Stale card",
                "analogy": "This completion belongs to the old context.",
                "takeaway": "It must not replace a newer context.",
            },
            "memory": {},
        }
    )

    window.settings.setValue("analogy/keywords", "changed concepts")
    window._schedule_analogy_prefetch()
    window._poll_analogy_prefetch(stale_generation, stale_context, stale_future)

    assert window._life_example["title"] == "Last good card"
    assert window._analogy_prefetch_queue == []
    assert all(
        item.get("result", {}).get("card", {}).get("title") != "Stale card"
        for item in window._analogy_prefetch_queue
    )
    assert window._analogy_prefetch_generation != stale_generation
    assert len(executor.pending) == 3

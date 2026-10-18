import threading
from types import SimpleNamespace

import pytest

from tests.refactor.guide165 import check_guide_import165 as checker
from tests.refactor.guide165.check_guide_import165 import (
    _optional_note_preparation,
    _save_module_headers,
)


def test_166_live_harness_persists_navigation_on_module_headers():
    navigation = [{"id": "m-nav-1", "title": "阅读路线", "refs": ["s1"]}]

    class Store:
        def __init__(self):
            self.rows = None

        def save_outline(self, plan_id, stages, modules):
            self.rows = modules

        def records(self, entity, plan_id):
            assert entity == "modules"
            return self.rows

    store = Store()
    result = _save_module_headers(store, "plan", [{
        "id": "module-1", "title": "第一章", "points": [{"minutes": 12}],
        "navigation": navigation,
    }])

    assert result[0]["navigation"] == navigation
    assert result[0]["point_count"] == 1
    assert result[0]["learning_minutes"] == 12


def test_166_live_harness_optional_notes_timeout_is_reported_and_questions_can_continue():
    class Timeout(Exception):
        code = "MODEL_FIRST_EVENT_TIMEOUT"

    class Gateway:
        def __init__(self):
            self.halted = threading.Event()
            self.halted.set()

    gateway = Gateway()

    def fail_notes(*args):
        raise Timeout("first event timeout")

    notes, error = _optional_note_preparation(gateway, [], [], fail_notes, None)
    assert notes == {}
    assert error["code"] == "MODEL_FIRST_EVENT_TIMEOUT"
    assert not gateway.halted.is_set()

    with pytest.raises(RuntimeError):
        _optional_note_preparation(gateway, [], [], lambda *args: (_ for _ in ()).throw(RuntimeError()), None)


def test_166_live_harness_refuses_to_reuse_output_with_paid_request(tmp_path, monkeypatch):
    model_dir = tmp_path / "evidence" / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "call-00001.json").write_text('{"purpose":"outline"}', encoding="utf-8")

    def live_factory_must_not_run(*args, **kwargs):
        raise AssertionError("live factory must not run for a reused paid output directory")

    monkeypatch.setattr("tests.refactor.simulation.run.live_factory", live_factory_must_not_run)
    args = SimpleNamespace(output=tmp_path, account_id="isolated", preset_root=tmp_path)
    with pytest.raises(RuntimeError, match="已有模型调用记录"):
        checker._run_live(None, args)

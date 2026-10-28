from copy import deepcopy

import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.classroom import register
from tests.refactor.app.test_app_classroom import DAY, PLAN, room


def _legacy_gap(reason="legacy missing"):
    return {"point_id": "p0", "title": "queue definition", "purpose": "question", "reason": reason}


def test_missing219_gaps_imports_legacy_then_reads_database(room):
    classroom, _, store = room
    legacy = classroom._file(PLAN)
    state = classroom._rebuild(classroom._course(PLAN))
    state["question_gaps"] = [_legacy_gap()]
    legacy.write(state)

    actual = classroom._missing_gaps(PLAN)

    position = store.classroom_position(PLAN)
    assert actual == [_legacy_gap()]
    assert position["state"]["question_gaps"] == actual
    assert legacy.path.exists()


def test_missing219_gaps_does_not_overwrite_new_database_position(room):
    classroom, _, store = room
    current = classroom._rebuild(classroom._course(PLAN))
    current["step"] = "teaching"
    current["question_gaps"] = [_legacy_gap("new state")]
    store.save_classroom_position(PLAN, current)
    legacy = classroom._file(PLAN)
    stale = classroom._rebuild(classroom._course(PLAN))
    stale["question_gaps"] = [_legacy_gap("old state")]
    legacy.write(stale)
    before = deepcopy(store.classroom_position(PLAN)["state"])

    assert classroom._missing_gaps(PLAN) == [_legacy_gap("new state"), _legacy_gap("old state")]
    after = store.classroom_position(PLAN)["state"]
    assert {k: v for k, v in after.items() if k != "question_gaps"} == {k: v for k, v in before.items() if k != "question_gaps"}
    assert after["question_gaps"] == [_legacy_gap("new state"), _legacy_gap("old state")]


def test_missing219_gaps_migration_is_idempotent_and_preserves_legacy_file(room):
    classroom, _, store = room
    legacy = classroom._file(PLAN)
    state = classroom._rebuild(classroom._course(PLAN))
    state["question_gaps"] = [_legacy_gap(), _legacy_gap()]
    legacy.write(state)

    first = classroom._missing_gaps(PLAN)
    after_first = store.classroom_position(PLAN)["state"]
    second = classroom._missing_gaps(PLAN)

    assert first == [_legacy_gap()]
    assert second == first
    assert store.classroom_position(PLAN)["state"] == after_first
    assert legacy.path.exists()


def test_missing219_gaps_write_failure_keeps_legacy_evidence(room, monkeypatch):
    classroom, _, store = room
    legacy = classroom._file(PLAN)
    state = classroom._rebuild(classroom._course(PLAN))
    state["question_gaps"] = [_legacy_gap()]
    legacy.write(state)
    original = store.save_classroom_position

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save_classroom_position", fail)
    with pytest.raises(BridgeError):
        classroom._missing_gaps(PLAN)
    monkeypatch.setattr(store, "save_classroom_position", original)
    assert legacy.path.exists()
    assert store.classroom_position(PLAN) is None


def test_missing219_registered_gaps_reads_database_and_never_reopens_legacy_json(room):
    classroom, _, store = room
    current = classroom._rebuild(classroom._course(PLAN))
    current["question_gaps"] = [_legacy_gap("database")]
    store.save_classroom_position(PLAN, current)

    class Bridge:
        def __init__(self):
            self.handlers = {}

        def register(self, name, handler, **_kwargs):
            self.handlers[name] = handler

        def push(self, *_args, **_kwargs):
            pass

    bridge = Bridge()
    register(bridge, lambda: classroom.services, lambda: DAY)
    handler = bridge.handlers["classroom.gaps"]
    params = {"plan_id": PLAN}
    assert handler(params, None) == [_legacy_gap("database")]

    legacy = classroom._file(PLAN)
    stale = classroom._rebuild(classroom._course(PLAN))
    stale["question_gaps"] = [_legacy_gap("legacy after migration")]
    legacy.write(stale)
    assert handler(params, None) == [_legacy_gap("database")]


def test_missing219_gap_import_marker_is_written_once_and_local(room):
    classroom, _, store = room
    legacy = classroom._file(PLAN)
    state = classroom._rebuild(classroom._course(PLAN))
    state["question_gaps"] = [_legacy_gap()]
    legacy.write(state)

    assert not store.classroom_gaps_imported(PLAN)
    assert classroom._missing_gaps(PLAN) == [_legacy_gap()]
    assert store.classroom_gaps_imported(PLAN)
    before = store.classroom_position(PLAN)
    state["question_gaps"] = [_legacy_gap("edited legacy")]
    legacy.write(state)
    assert classroom._missing_gaps(PLAN) == [_legacy_gap()]
    assert store.classroom_position(PLAN) == before


def test_missing219_gap_merge_failure_rolls_back_position_outbox_and_marker(room, monkeypatch):
    classroom, _, store = room
    current = classroom._rebuild(classroom._course(PLAN))
    current['question_gaps'] = [_legacy_gap('database')]
    store.save_classroom_position(PLAN, current)
    legacy = classroom._file(PLAN)
    legacy.write(dict(current, question_gaps=[_legacy_gap('legacy')]))
    original_file = legacy.path.read_bytes()
    before = list(store._db.iterdump())
    save = store.save_classroom_position

    def fail_after_write(*args, **kwargs):
        save(*args, **kwargs)
        raise OSError('disk full before migration marker')

    monkeypatch.setattr(store, 'save_classroom_position', fail_after_write)
    with pytest.raises(OSError, match='disk full'):
        classroom._missing_gaps(PLAN)
    assert list(store._db.iterdump()) == before
    assert not store.classroom_gaps_imported(PLAN)
    assert legacy.path.read_bytes() == original_file
    monkeypatch.setattr(store, 'save_classroom_position', save)
    assert classroom._missing_gaps(PLAN) == [_legacy_gap('database'), _legacy_gap('legacy')]
    assert store.classroom_gaps_imported(PLAN)


@pytest.mark.parametrize('has_legacy_gap', [False, True])
def test_missing219_plan_details_can_read_gaps_before_starting_class(room, has_legacy_gap):
    classroom, _, store = room
    if has_legacy_gap:
        state = classroom._rebuild(classroom._course(PLAN))
        classroom._file(PLAN).write(dict(state, question_gaps=[_legacy_gap()]))
    with store.transaction():
        store._put('plans', PLAN, dict(store.get_plan(PLAN), state='ready'))
    assert classroom._missing_gaps(PLAN) == ([_legacy_gap()] if has_legacy_gap else [])
    assert store.get_plan(PLAN)['state'] == 'ready'

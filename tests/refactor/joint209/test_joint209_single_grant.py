"""209: the explicit one-question grant unlocks final check without an answer."""
from datetime import datetime, timezone
from types import SimpleNamespace

from refactor.learning.app.classroom import Classroom
from refactor.learning.course.store import CourseStore
from scripts.grant_mastery204 import execute, preview
from tests.refactor.integration38.support import context
from tests.refactor.new204 import test_grant_mastery204_tool as fixture204


def test_209_single_missed_question_grant_unlocks_real_classroom_without_retest(tmp_path, monkeypatch):
    plan = 'c' * 32
    monkeypatch.setattr(fixture204, 'PLAN', plan)
    ids = fixture204._seed(tmp_path, m6_count=1, m6_correct=1,
                          m8_count=1, m8_correct=0, new_m6_count=0)
    account = fixture204.ACCOUNT
    qid = ids['m8'][0]
    with CourseStore(account, tmp_path) as store:
        before = {entity: store.records(entity, plan) for entity in
                  ('question_answers', 'credits', 'ledger_events', 'attempt_closed')}
        old_judgements = {row['id']: row for row in store.records('judgements', plan)}
        assert not store.load_mastery(plan).check_ready()

    projected = preview(tmp_path, account, plan, 'm8', [qid], False)
    assert projected['module']['current_state'] == 'failed'
    assert projected['module']['projected_state'] == 'passed'
    result = execute(tmp_path, account, plan, 'm8', [qid], False)
    assert result['module_state'] == 'passed'

    class NoModel:
        def complete(self, *_args, **_kwargs):
            raise AssertionError('No retest, teacher, or paid model is needed')

    with CourseStore(account, tmp_path) as store:
        services = SimpleNamespace(store=store, account_id=account, data_root=tmp_path,
            gateway=NoModel(), settings=SimpleNamespace(load=lambda: {'refresh_time': '04:00'}))
        room = Classroom(services, lambda: fixture204.DAY,
                         now=lambda: datetime(2026, 10, 27, tzinfo=timezone.utc))
        monkeypatch.setattr(room, '_schedule_next_module_safely', lambda *_a, **_k: None)
        monkeypatch.setattr(room, '_profile', lambda *_a, **_k: None)
        entered = room.perform(plan, 'enter', context())
        assert not entered.get('failure')
        assert entered['step'] == 'check_offer' and entered['test_readiness']['check']
        started = room.perform(plan, 'start_check', context())
        assert not started.get('failure') and started['step'] == 'checking'
        assert started['question'] and started['question']['id'] != qid
        assert store.load_mastery(plan).module('m8').state == 'passed'
        for entity, rows in before.items():
            assert store.records(entity, plan) == rows
        after = {row['id']: row for row in store.records('judgements', plan)}
        assert all(after[key] == row for key, row in old_judgements.items())
        extra = [row for key, row in after.items() if key not in old_judgements]
        assert len(extra) == 1 and extra[0]['details']['user_override'] is True

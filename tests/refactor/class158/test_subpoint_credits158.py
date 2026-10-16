"""Subpoint credits survive retries, mastery projection and module settlement."""
from refactor.learning.app.classroom import _Course, TODAY_EVENT
from tests.refactor.conversation37.test_grounded_conversation37 import DAY
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import act, PLAN


def _close(env):
    env.room.cancel_materials()
    env.store.close()


def _live_for(env, point_id):
    return [row for row in env.store.load_ledger(PLAN).to_dict()['credits']
            if row['unit_id'] == point_id]


def _finish_first_concept(env):
    act(env, 'next')                 # first subpoint
    act(env, 'next')                 # record s1
    act(env, 'next')                 # record s2
    return act(env, 'next')          # record s3 and finish the large point


def test_subpoint_credits158_are_incremental_idempotent_and_mark_taught_only_at_total(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(type(env.room), '_materials', staticmethod(lambda: env.materials))
    try:
        act(env, 'next')
        assert not env.store.records('credits', PLAN)
        view = act(env, 'next')
        rows = env.store.records('credits', PLAN)
        assert len(rows) == 1 and rows[0]['id'].startswith('subpoint:')
        assert rows[0]['credit']['minutes'] == 5 and rows[0]['credit']['state'] == 'tentative'
        assert any(event.startswith(TODAY_EVENT) for event in env.stream)
        assert 'p0' not in _Course(env.store, env.store.get_plan(PLAN)).taught()

        # A scene-save retry sees the same stable child id and writes nothing.
        course = env.room._course(PLAN)
        state = env.room._load(course)
        point = course.point('p0')
        state['day'] = '2026-10-04'  # Simulate reopening after a study-day boundary.
        assert env.room._credit_subpoint(course, state, point, point['subpoints'][0]) is False
        assert len(env.store.records('credits', PLAN)) == 1
        assert env.store.records('credits', PLAN)[0]['credit']['day'] == DAY.isoformat()

        act(env, 'next')
        assert sum(row['minutes'] for row in _live_for(env, 'p0')) == 10
        assert 'p0' not in env.store.learning_credit_projection(PLAN)['taught']
        view = act(env, 'next')
        rows = env.store.records('credits', PLAN)
        assert view['step'] == 'test_offer'
        assert len([row for row in rows if row['id'].startswith('subpoint:')]) == 3
        assert not [row for row in rows if row['id']=='learn:p0']
        assert sum(row['credit']['minutes'] for row in rows if row['credit']['unit_id'] == 'p0') == 15
        assert 'p0' in env.store.learning_credit_projection(PLAN)['taught']
        assert 'p0' in _Course(env.store, env.store.get_plan(PLAN)).taught()
    finally:
        _close(env)


def test_subpoint_credits158_resume_old_partial_point_without_duplicate_child_minutes(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(type(env.room), '_materials', staticmethod(lambda: env.materials))
    try:
        act(env, 'next')
        course=env.room._course(PLAN);state=env.room._load(course);point=course.point('p0')
        # A prior version already committed the first child's five minutes.
        env.room._credit_subpoint(course,state,point,point['subpoints'][0])
        act(env, 'next')                 # leave s1 without adding it twice
        act(env, 'next')                 # leave s2
        act(env, 'next')                 # finish s3 and the large point
        rows=[row for row in env.store.records('credits',PLAN) if row['credit']['unit_id']=='p0']
        assert len([row for row in rows if row['id'].startswith('subpoint:')])==3
        difference=[row for row in rows if row['id']=='learn:p0']
        assert difference==[]
        assert sum(row['credit']['minutes'] for row in rows)==15
    finally:
        _close(env)


def test_subpoint_credits158_existing_whole_point_marker_prevents_child_double_count(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(type(env.room), '_materials', staticmethod(lambda: env.materials))
    try:
        act(env, 'next')
        course=env.room._course(PLAN);state=env.room._load(course);point=course.point('p0')
        env.store.add_credit(PLAN,'learn:p0','p0',15,DAY,'tentative')
        assert env.room._credit_subpoint(course,state,point,point['subpoints'][0]) is False
        assert [row['id'] for row in env.store.records('credits',PLAN)]==['learn:p0']
    finally:
        _close(env)


def test_subpoint_credits158_point_done_fills_missing_minutes_from_partial_child_history(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(type(env.room), '_materials', staticmethod(lambda: env.materials))
    try:
        act(env, 'next')
        course=env.room._course(PLAN);state=env.room._load(course);point=course.point('p0')
        # Reproduce a saved halfway state whose first two child submissions are
        # present but whose final child completion was not persisted yet.
        for child in point['subpoints'][:2]:
            env.store.add_credit(PLAN,env.room._subpoint_credit_id(point,child),point['id'],child['minutes'],DAY,'tentative')
        env.room._point_done(course,state)
        rows=[row for row in env.store.records('credits',PLAN) if row['credit']['unit_id']=='p0']
        assert len([row for row in rows if row['id'].startswith('subpoint:')])==2
        difference=[row for row in rows if row['id']=='learn:p0']
        assert len(difference)==1 and difference[0]['credit']['minutes']==5
        assert sum(row['credit']['minutes'] for row in rows)==15
    finally:
        _close(env)


def test_subpoint_credits158_module_pass_confirms_children_without_replacing_ids(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(type(env.room), '_materials', staticmethod(lambda: env.materials))
    try:
        _finish_first_concept(env)
        child_ids={row['id'] for row in env.store.records('credits',PLAN) if row['id'].startswith('subpoint:')}
        act(env, 'start_test', 'module_test')
        act(env, 'answer', [0])
        assert env.store.load_mastery(PLAN).module('m0').state=='passed'
        live=_live_for(env,'p0')
        assert len(live)==3 and sum(row['minutes'] for row in live)==15
        assert all(row['state']=='confirmed' for row in live)
        assert {row['id'] for row in env.store.records('credits',PLAN)
                if row['id'].startswith('subpoint:')}==child_ids
    finally:
        _close(env)


def test_subpoint_credits158_module_failure_revokes_and_successful_retest_restores(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(type(env.room), '_materials', staticmethod(lambda: env.materials))
    try:
        _finish_first_concept(env)
        assert sum(row['minutes'] for row in _live_for(env, 'p0')) == 15
        act(env, 'start_test', 'module_test')
        act(env, 'answer', [1])
        assert env.store.load_mastery(PLAN).module('m0').state == 'failed'
        assert not _live_for(env, 'p0')
        # The immutable submissions remain, so a test-save retry cannot count
        # them as a second lesson or erase their audit history.
        assert sum(row['credit']['minutes'] for row in env.store.records('credits', PLAN)
                   if row['credit']['unit_id'] == 'p0') == 15

        act(env, 'start_test', 'retest')
        act(env, 'answer', [0])
        assert env.store.load_mastery(PLAN).module('m0').state == 'passed'
        live = _live_for(env, 'p0')
        assert sum(row['minutes'] for row in live) == 15
        assert all(row['state'] == 'confirmed' for row in live)
        restored = [row for row in env.store.records('credits', PLAN)
                    if row['credit']['unit_id'] == 'p0' and row['id'].startswith('relearn:concept:')]
        assert len(restored) == 1 and restored[0]['credit']['minutes'] == 15
    finally:
        _close(env)


def test_subpoint_credits158_partial_credit_freezes_parent_minutes_during_reestimate(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(type(env.room), '_materials', staticmethod(lambda: env.materials))
    try:
        point = env.store.records('points', PLAN)[0]
        original_minutes = point['minutes']
        env.store.add_credit(PLAN, 'subpoint:partial-freeze', 'p0', 5, DAY, 'tentative')
        with env.store.transaction():
            plan = env.store.get_plan(PLAN)
            plan['point_minutes_version'] = 0
            env.store._put('plans', PLAN, plan)
        before = env.store.point_minutes_snapshot(PLAN)
        assert not before['ready']
        labels = {row['id']: '难' for row in before['points']}
        assert env.store.save_point_minutes(PLAN, before['fingerprint'], labels, today=DAY)
        after = env.store.records('points', PLAN)
        assert next(row['minutes'] for row in after if row['id'] == 'p0') == original_minutes
    finally:
        _close(env)


def test_subpoint_credits158_no_bank_completion_confirms_children_but_partial_is_not_taught(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(type(env.room), '_materials', staticmethod(lambda: env.materials))
    try:
        env.materials.bank.clear()
        with env.store.transaction():
            module=env.store.records('modules',PLAN)[0]
            module['question_count']=0
            module['material_preparation']={'state':'done'}
            env.store._put('modules',PLAN,module)
        act(env, 'next')
        act(env, 'next')
        assert env.store.learning_credit_projection(PLAN)['recorded'] >= {'p0'}
        assert 'p0' not in env.store.learning_credit_projection(PLAN)['taught']
        act(env, 'next')
        act(env, 'next')
        assert env.store.load_mastery(PLAN).module('m0').state == 'passed'
        point_rows = [row for row in env.store.load_ledger(PLAN).to_dict()['credits']
                      if row['unit_id'] == 'p0']
        assert sum(row['minutes'] for row in point_rows) == 15
        assert all(row['state'] == 'confirmed' for row in point_rows)
        assert 'p0' in env.store.learning_credit_projection(PLAN)['taught']
    finally:
        _close(env)

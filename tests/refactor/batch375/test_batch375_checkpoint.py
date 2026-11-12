"""375: legacy material splitter batch checkpoint and schema upgrade tests."""

from datetime import date, timedelta
import sqlite3

import pytest

from refactor.learning.course import material_questions as mq
from refactor.learning.course import point_materials as pm
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.research import ResearchCancelled
from refactor.learning.course.store import CourseStore, SCHEMA_VERSION


PLAN = 'batch375-plan'


def _candidates():
    rows = []
    for index in range(9):
        stem = f'第 {index + 1} 题：原文填空 ____。'
        rows.append(dict(
            id=f'batch375-candidate-{index}', candidate_type='objective',
            stem=stem, reference=f'{stem}\n答案：答案{index}',
            answer=f'答案{index}', options=[], objective_kind='fill',
            source_id='source', source_title='375 测试资料', source_url='https://example.invalid',
            chunk_ids=['chunk'], block_ids=['chunk'], link_chunk_ids=['chunk'],
        ))
    return rows


def _seed(tmp_path):
    store = CourseStore('batch375', tmp_path)
    today = date(2026, 11, 11)
    store.create_plan(PLAN, goal='批次暂存', total_minutes=20,
                      window=PlanWindow(today, today + timedelta(days=4), 60))
    store.add_sources(PLAN, [dict(
        id='source', title='375 测试资料', adopted=True,
        url='https://example.invalid',
        chunks=[dict(chunk_id='chunk', text='资料原文，供输入指纹使用。', section=['测试'])],
    )])
    store.save_outline(PLAN, [dict(id='stage', title='阶段')],
                       [dict(id='module', stage_id='stage', title='模块', prerequisites=[])])
    store.save_generated_module(
        PLAN, 'module', [dict(id='point', title='旧式知识点', summary='原文题', minutes=10,
                              chunk_ids=['chunk'])], check_minutes=2, today=today)
    return store


def _splitter(calls, *, fail_at=None, failure=None):
    def split(packet, **_kwargs):
        group = packet['candidates']
        calls.append(len(group))
        if fail_at is not None and len(calls) == fail_at:
            raise failure
        return dict(questions=[dict(
            candidate_id=row['id'], kind='objective', stem=row['stem'],
            options=[], answer=row['answer'], objective_kind='fill',
        ) for row in group])
    return split


@pytest.fixture
def candidate_patch(monkeypatch):
    monkeypatch.setattr(mq, 'candidates', lambda _chunks, _sources: _candidates())


@pytest.mark.parametrize('failure', [ResearchCancelled(), RuntimeError('splitter failed')],
                         ids=['cancel', 'error'])
def test_batch375_failed_prefix_is_staged_and_retry_calls_only_last_batch(
        tmp_path, candidate_patch, failure):
    store = _seed(tmp_path)
    try:
        first_calls = []
        with pytest.raises(type(failure)):
            pm.prepare_module(store, PLAN, 'module',
                              dict(question_splitter=_splitter(first_calls, fail_at=3, failure=failure)),
                              lambda *_args: None)
        assert first_calls == [4, 4, 1]
        assert store.records('question_bank', PLAN) == []
        marker = next(row for row in store.records('modules', PLAN) if row['id'] == 'module')
        assert marker.get('question_extraction', {}).get('state') != 'done'
        _, chunks = pm._material_chunks(store, PLAN)
        fingerprint = pm._fingerprint(store.records('points', PLAN), chunks)
        staged = store.question_batch_staging(PLAN, 'module', fingerprint)
        assert set(staged) == {0, 1}
        assert [len(staged[index]['accepted']) for index in (0, 1)] == [4, 4]
        assert all(staged[index]['rejected'] == [] for index in (0, 1))

        retry_calls = []
        result = pm.prepare_module(store, PLAN, 'module',
                                   dict(question_splitter=_splitter(retry_calls)),
                                   lambda *_args: None)
        assert retry_calls == [1]
        assert result['questions'] == 9
        assert len(store.records('question_bank', PLAN)) == 9
        marker = next(row for row in store.records('modules', PLAN) if row['id'] == 'module')
        assert marker['question_extraction']['state'] == 'done'
        assert store.question_batch_staging(PLAN, 'module', fingerprint) == {}
    finally:
        store.close()


def test_batch375_fingerprint_change_discards_old_staging_and_restarts(
        tmp_path, candidate_patch):
    store = _seed(tmp_path)
    try:
        failed_calls = []
        with pytest.raises(RuntimeError):
            pm.prepare_module(store, PLAN, 'module',
                              dict(question_splitter=_splitter(
                                  failed_calls, fail_at=3, failure=RuntimeError('stop'))),
                              lambda *_args: None)
        chunk = store._get('source_chunks', PLAN, 'chunk')
        chunk['text'] += ' 输入发生变化。'
        with store.transaction():
            store._put('source_chunks', PLAN, chunk)

        retry_calls = []
        result = pm.prepare_module(store, PLAN, 'module',
                                   dict(question_splitter=_splitter(retry_calls)),
                                   lambda *_args: None)
        assert retry_calls == [4, 4, 1]
        assert result['questions'] == 9
        assert len(store.records('question_bank', PLAN)) == 9
        _, chunks = pm._material_chunks(store, PLAN)
        fingerprint = pm._fingerprint(store.records('points', PLAN), chunks)
        assert store.question_batch_staging(PLAN, 'module', fingerprint) == {}
    finally:
        store.close()


def test_batch375_success_is_idempotent_and_staging_is_hidden_before_commit(
        tmp_path, candidate_patch):
    store = _seed(tmp_path)
    try:
        calls = []
        result = pm.prepare_module(store, PLAN, 'module',
                                   dict(question_splitter=_splitter(calls)),
                                   lambda *_args: None)
        assert calls == [4, 4, 1] and result['questions'] == 9
        assert len(store.records('question_bank', PLAN)) == 9
        rerun_calls = []
        again = pm.prepare_module(store, PLAN, 'module',
                                  dict(question_splitter=_splitter(rerun_calls)),
                                  lambda *_args: None)
        assert rerun_calls == [] and again == result
    finally:
        store.close()


def test_batch375_old_schema_reopens_and_preserves_plan_progress_and_bank(tmp_path,
                                                                          candidate_patch):
    store = _seed(tmp_path)
    calls = []
    pm.prepare_module(store, PLAN, 'module',
                      dict(question_splitter=_splitter(calls)), lambda *_args: None)
    assert len(store.records('question_bank', PLAN)) == 9
    store.close()

    path = tmp_path / 'learning' / 'accounts' / 'batch375' / 'course.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('UPDATE schema_version SET version=?', (SCHEMA_VERSION - 1,))
        db.commit()

    with CourseStore('batch375', tmp_path) as reopened:
        assert reopened._db.execute('SELECT version FROM schema_version').fetchone()[0] == SCHEMA_VERSION
        assert reopened.get_plan(PLAN)['id'] == PLAN
        assert len(reopened.records('points', PLAN)) == 1
        assert len(reopened.records('question_bank', PLAN)) == 9
        assert reopened.records('practice_results', PLAN) == []

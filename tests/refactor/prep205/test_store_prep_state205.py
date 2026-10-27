import json
from datetime import date, timedelta

import pytest

from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course import point_materials
from refactor.learning.course.store import CourseStore, StoreError, MAX_PAYLOAD


PLAN = '2' * 32
DAY = '2026-10-27'


def _seed(tmp_path):
    store = CourseStore('prep-state205', tmp_path)
    today = date.fromisoformat(DAY)
    store.create_plan(PLAN, goal='备题状态迁移', total_minutes=30,
                      window=PlanWindow(today, today + timedelta(days=3), 60))
    store.add_sources(PLAN, [dict(id='source', kind='url', url='https://example.invalid',
        title='示例资料', adopted=True, chunks=[dict(chunk_id='chunk', text='概念说明。', section=['概念'])])])
    store.save_outline(PLAN, [dict(id='stage', title='阶段')], [
        dict(id='module', stage_id='stage', title='模块', prerequisites=[])])
    points = [dict(id=f'p{i}', title=f'点{i}', summary='掌握原文内容', minutes=3,
                   chunk_ids=['chunk'], structure='concept_v1', goal='掌握原文内容',
                   subpoints=[dict(id=f'p{i}-s{j}', title=f'要点{i}-{j}', minutes=1,
                                   chunk_ids=['chunk'], notes=[]) for j in range(1, 4)])
              for i in range(4)]
    store.save_generated_module(PLAN, 'module', points, check_minutes=2, today=today)
    return store


def _reject(sid, number):
    return dict(subpoint_id=sid, reason=f'拒绝详情{number}：' + ('原文证据，' * 45), details=[])


def test_prep205_large_legacy_aggregate_migrates_to_current_point_markers(tmp_path):
    store = _seed(tmp_path)
    try:
        points = store.records('points', PLAN)
        subpoint_ids = [sub['id'] for point in points for sub in point['subpoints']]
        # This emulates old module aggregation after several resumptions. The
        # unnormalized in-memory envelope is over the shared 256 KiB wire cap.
        rejected = [_reject(subpoint_ids[i % len(subpoint_ids)], i) for i in range(500)]
        assert len(json.dumps(rejected, ensure_ascii=False).encode('utf-8')) > MAX_PAYLOAD
        module = store.records('modules', PLAN)[0]
        module['question_extraction'] = dict(state='done', source_fingerprint='old',
            result=dict(rejected=len(rejected)), rejections=rejected, at='2026-10-27T00:00:00+00:00')

        store._put('modules', PLAN, module)

        saved_module = store.records('modules', PLAN)[0]
        assert saved_module['question_extraction']['rejections'] == []
        assert saved_module['question_extraction']['rejection_count'] == 500
        assert saved_module['question_extraction']['result']['rejected'] == 500
        saved_points = store.records('points', PLAN)
        saved_rows = [row for point in saved_points
                      for row in point.get('subpoint_preparation', {}).get('rejections', [])]
        assert len(saved_rows) == 500
        assert all(row['subpoint_id'] in subpoint_ids for row in saved_rows)
        assert all(len(json.dumps(point, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) < MAX_PAYLOAD
                   for point in saved_points)
        module_record = store._db.execute(
            "SELECT payload FROM _records WHERE entity='modules' AND entity_id=?",
            (f'{len(PLAN)}:{PLAN}module',)).fetchone()[0]
        assert len(module_record.encode('utf-8')) < MAX_PAYLOAD
        assert all(len(row[0].encode('utf-8')) < MAX_PAYLOAD for row in store._db.execute(
            "SELECT payload FROM _records WHERE pending=1 AND deleted=0"))
    finally:
        store.close()


def test_prep205_old_unknown_subpoints_collapse_to_count_and_migration_is_idempotent(tmp_path):
    store = _seed(tmp_path)
    try:
        module = store.records('modules', PLAN)[0]
        current_id = store.records('points', PLAN)[0]['subpoints'][0]['id']
        rejected = [_reject(current_id, 1), _reject('removed-old-subpoint', 2)]
        module['question_extraction'] = dict(state='done', result=dict(rejected=2),
                                             rejections=rejected)
        store._put('modules', PLAN, module)
        first = store.records('modules', PLAN)[0]['question_extraction']
        assert first['rejections'] == []
        assert first['rejection_count'] == 2
        point = next(p for p in store.records('points', PLAN) if p['id'] == 'p0')
        assert [row['subpoint_id'] for row in point['subpoint_preparation']['rejections']] == [current_id]

        store.update_module_preparation_state(PLAN, 'module', 'running', attempts=2)
        point = next(p for p in store.records('points', PLAN) if p['id'] == 'p0')
        assert [row['subpoint_id'] for row in point['subpoint_preparation']['rejections']] == [current_id]
        extraction = store.records('modules', PLAN)[0]['question_extraction']
        assert extraction['rejections'] == [] and extraction['rejection_count'] == 2
        assert extraction['state'] == 'done'
    finally:
        store.close()


def test_prep205_duplicate_legacy_rejection_does_not_dirty_point_record(tmp_path):
    store = _seed(tmp_path)
    try:
        point = store.records('points', PLAN)[0]
        rejection = _reject(point['subpoints'][0]['id'], 17)
        point['subpoint_preparation'] = dict(state='partial', rejections=[rejection])
        with store.transaction():
            store._put('points', PLAN, point)
        wire_id = f'{len(PLAN)}:{PLAN}{point["id"]}'
        before = store._db.execute(
            "SELECT local_revision,payload FROM _records WHERE entity='points' AND entity_id=?",
            (wire_id,)).fetchone()

        module = store.records('modules', PLAN)[0]
        module['question_extraction'] = dict(state='partial', result=dict(rejected=1),
                                             rejections=[rejection])
        store._put('modules', PLAN, module)

        after = store._db.execute(
            "SELECT local_revision,payload FROM _records WHERE entity='points' AND entity_id=?",
            (wire_id,)).fetchone()
        assert (after['local_revision'], after['payload']) == (before['local_revision'], before['payload'])
        assert len(store._get('points', PLAN, point['id'])['subpoint_preparation']['rejections']) == 1
        assert store._get('modules', PLAN, 'module')['question_extraction']['rejections'] == []
    finally:
        store.close()


def test_prep205_question_count_metadata_update_does_not_replan(tmp_path):
    store = _seed(tmp_path)
    try:
        before_module = store.records('modules', PLAN)[0]
        before_plan = store.get_plan(PLAN)
        before_ledger = store.records('ledger_events', PLAN)

        store.save_module_preparation(PLAN, 'module', dict(state='done', points=4),
                                      merge=True, question_count=17)

        after_module = store.records('modules', PLAN)[0]
        assert after_module['question_count'] == 17
        assert after_module['check_minutes'] == before_module['check_minutes']
        assert after_module['material_preparation']['state'] == 'done'
        assert store.records('ledger_events', PLAN) == before_ledger
        assert store.get_plan(PLAN)['total_minutes'] == before_plan['total_minutes']
        with pytest.raises(StoreError):
            store.save_module_preparation(PLAN, 'module', {}, merge=True, question_count=True)
    finally:
        store.close()


def test_prep205_hundred_point_preparation_stays_bounded_and_resumes_without_model_calls(tmp_path):
    store = CourseStore('prep-large205', tmp_path / 'source')
    replica = None
    today = date.fromisoformat(DAY)
    plan_id = '3' * 32
    try:
        store.create_plan(plan_id, goal='大型模块备题', total_minutes=450,
                          window=PlanWindow(today, today + timedelta(days=10), 120))
        store.add_sources(plan_id, [dict(id='source', kind='url', url='https://example.invalid',
            title='概念材料', adopted=True, chunks=[dict(chunk_id='chunk', text='这是用于验证备题的概念说明。', section=['概念'])])])
        store.save_outline(plan_id, [dict(id='stage', title='阶段')], [
            dict(id='large', stage_id='stage', title='150 点模块', prerequisites=[])])
        points = [dict(id=f'p{i:03d}', title=f'大点 {i}', summary=f'掌握第 {i} 个概念',
            minutes=3, chunk_ids=['chunk'], structure='concept_v1', goal=f'掌握第 {i} 个概念',
            subpoints=[dict(id=f'p{i:03d}-s{j}', title=f'要点 {i}-{j}', minutes=1,
                           chunk_ids=['chunk'], notes=[]) for j in range(1, 4)])
            for i in range(150)]
        store.save_generated_module(plan_id, 'large', points, check_minutes=20, today=today)

        calls = []
        def empty_finder(packet, _passages, **_kwargs):
            calls.append(packet['point_id'])
            return dict(subpoints=[dict(id=sub['id'], cloze=[]) for sub in packet['subpoints']])

        result = point_materials.prepare_module(store, plan_id, 'large',
            dict(finder=empty_finder), lambda *_args: None)
        assert result['points'] == 150 and result['questions'] == 0
        assert len(calls) == 150
        module = next(row for row in store.records('modules', plan_id) if row['id'] == 'large')
        extraction = module['question_extraction']
        assert extraction['state'] == 'done' and extraction['result']['points'] == 150
        assert extraction['rejection_count'] == 450 and not extraction.get('rejections')
        saved_points = [point for point in store.records('points', plan_id) if point['module_id'] == 'large']
        assert len(saved_points) == 150
        for point in saved_points:
            marker = point['subpoint_preparation']
            assert marker['state'] == 'done' and len(marker['processed_subpoints']) == 3
            assert all(item['status'] == 'rejected' for item in marker['processed_subpoints'].values())

        def wire_payload_size(entity, identifier):
            record = store._db.execute(
                'SELECT payload FROM _records WHERE entity=? AND entity_id=?',
                (entity, f'{len(plan_id)}:{plan_id}{identifier}')).fetchone()[0]
            return len(record.encode('utf-8'))

        module_size_before = wire_payload_size('modules', 'large')
        point_sizes = [wire_payload_size('points', point['id']) for point in saved_points]
        outbox = store.pending_records(10000)
        assert all(len(json.dumps(record.payload, ensure_ascii=False,
                                  separators=(',', ':')).encode('utf-8')) <= MAX_PAYLOAD
                   for record in outbox)
        assert module_size_before < MAX_PAYLOAD and max(point_sizes) < MAX_PAYLOAD

        # A stale module fingerprint forces the module finalizer to run again;
        # all point checkpoints already match, so the fake finder is not called.
        module = store._get('modules', plan_id, 'large')
        module['question_extraction']['source_fingerprint'] = 'stale-module-fingerprint'
        with store.transaction():
            store._put('modules', plan_id, module)
        point_revisions = {point['id']: store._db.execute(
            "SELECT local_revision FROM _records WHERE entity='points' AND entity_id=?",
            (f'{len(plan_id)}:{plan_id}{point["id"]}',)).fetchone()[0] for point in saved_points}
        point_materials.prepare_module(store, plan_id, 'large', dict(finder=empty_finder), lambda *_args: None)
        assert len(calls) == 150
        module_after = next(row for row in store.records('modules', plan_id) if row['id'] == 'large')
        assert module_after['question_extraction']['state'] == 'done'
        assert module_after['question_extraction']['result']['points'] == 150
        assert wire_payload_size('modules', 'large') == module_size_before
        assert all(store._db.execute(
            "SELECT local_revision FROM _records WHERE entity='points' AND entity_id=?",
            (f'{len(plan_id)}:{plan_id}{point["id"]}',)).fetchone()[0] == revision
            for point_id, revision in point_revisions.items()
            for point in saved_points if point['id'] == point_id)

        replica = CourseStore('prep-large205-replica', tmp_path / 'replica')
        replica.apply_remote([(record, index + 1) for index, record in enumerate(store.pending_records(10000))])
        remote_points = [point for point in replica.records('points', plan_id) if point['module_id'] == 'large']
        remote_module = next(row for row in replica.records('modules', plan_id) if row['id'] == 'large')
        assert len(remote_points) == 150
        assert remote_module['question_extraction']['result']['points'] == 150
        assert all(point['subpoint_preparation']['state'] == 'done' for point in remote_points)
    finally:
        if replica is not None:
            replica.close()
        store.close()

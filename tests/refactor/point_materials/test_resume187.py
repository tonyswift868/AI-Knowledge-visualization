"""187: subpoint checkpoints and input-only fingerprints."""

import pytest

from refactor.learning.course import point_materials as pm
from refactor.learning.connections.errors import ModelError
from tests.refactor.point_materials.test_subpoint114_questions import (
    pointer, reviewer, store as seeded_store,
)


def test_point_materials187_signature_ignores_notes_but_tracks_structure(seeded_store):
    point = seeded_store.records('points', 'p')[0]
    _, chunks = pm._material_chunks(seeded_store, 'p')
    original = pm._fingerprint([point], chunks)
    point['subpoints'][0]['notes'] = [dict(text='deferred generated note', chunk_id='c1')]
    assert pm._fingerprint([point], chunks) == original
    point['subpoints'][0]['title'] += ' 更新'
    assert pm._fingerprint([point], chunks) != original


def test_point_materials187_stale_rejection_marker_does_not_skip_changed_input(seeded_store):
    point = seeded_store.records('points', 'p')[0]
    point['subpoint_preparation'] = dict(state='done', fingerprint='stale', result={},
        processed_subpoints={point['subpoints'][0]['id']: dict(
            subpoint_id=point['subpoints'][0]['id'], status='rejected', input_fingerprint='stale')})
    with seeded_store.transaction():
        seeded_store._put('points', 'p', point)
    calls = []

    def finder(packet, *args, **kwargs):
        calls.append(packet['subpoints'][0]['id'])
        return dict(subpoints=[dict(id=packet['subpoints'][0]['id'], cloze=[])])

    pm.prepare_point(seeded_store, 'p', point, dict(finder=finder, distractor_checker=reviewer))
    assert point['subpoints'][0]['id'] in calls


def test_point_materials187_checker_disconnect_keeps_local_rejections_and_retries_only_pending(seeded_store):
    point = seeded_store.records('points', 'p')[0]
    calls = []
    checks = []

    def finder(packet, passages, **kwargs):
        ids = [s['id'] for s in packet['subpoints']]
        calls.append(ids)
        return dict(subpoints=[dict(id=sid, cloze=[pointer('c2', second=True)] if sid.endswith('-s2') else [])
                               for sid in ids])

    def flaky_checker(packet, **kwargs):
        checks.append([q['id'] for q in packet['questions']])
        if len(checks) == 1:
            raise ModelError('MODEL_CONNECTION_FAILED', 'simulated disconnect')
        return reviewer(packet, **kwargs)

    try:
        pm.prepare_point(seeded_store, 'p', point,
                         dict(finder=finder, distractor_checker=flaky_checker))
    except ModelError as exc:
        assert str(exc) == 'simulated disconnect'
    else:
        marker = seeded_store.records('points', 'p')[0].get('subpoint_preparation', {})
        pytest.fail(f'checker was not reached: finder={calls!r} checks={checks!r} marker={marker!r}')
    marker = seeded_store.records('points', 'p')[0]['subpoint_preparation']
    assert marker['processed_subpoints']['point-s1']['status'] == 'rejected'
    assert marker['processed_subpoints']['point-s3']['status'] == 'rejected'
    assert 'point-s2' not in marker['processed_subpoints']

    pm.prepare_point(seeded_store, 'p', seeded_store.records('points', 'p')[0],
                     dict(finder=finder, distractor_checker=flaky_checker))
    assert calls[0] == ['point-s1', 'point-s2', 'point-s3']
    assert calls[1] == ['point-s2']
    assert len(checks) == 2 and checks[0] == checks[1]
    assert len(seeded_store.records('question_bank', 'p')) == 1


def test_point_materials187_stale_bank_row_does_not_validate_changed_input(seeded_store):
    point = seeded_store.records('points', 'p')[0]
    pm.prepare_point(seeded_store, 'p', point, dict(finder=lambda packet, *a, **k: dict(
        subpoints=[dict(id=s['id'], cloze=[pointer(s['chunk_ids'][0], s['id'].endswith('-s2'))]
                        if s['id'].endswith('-s2') else []) for s in packet['subpoints']]),
        distractor_checker=reviewer))
    original_bank = seeded_store.records('question_bank', 'p')
    assert len(original_bank) == 1

    changed = seeded_store.records('points', 'p')[0]
    changed['subpoints'][1]['title'] += ' 新输入'
    changed.pop('subpoint_preparation', None)
    with seeded_store.transaction():
        seeded_store._put('points', 'p', changed)

    def no_new_candidate(packet, *args, **kwargs):
        return dict(subpoints=[dict(id=s['id'], cloze=[]) for s in packet['subpoints']])

    pm.prepare_point(seeded_store, 'p', changed,
                     dict(finder=no_new_candidate, distractor_checker=reviewer))
    saved = seeded_store.records('points', 'p')[0]['subpoint_preparation']['processed_subpoints']
    assert saved['point-s2']['status'] == 'rejected'
    assert saved['point-s2']['input_fingerprint'] == pm._subpoint_fingerprint(
        changed['subpoints'][1], pm._material_chunks(seeded_store, 'p')[1])
    # Historical question is retained; it cannot make the changed input valid.
    assert seeded_store.records('question_bank', 'p') == original_bank


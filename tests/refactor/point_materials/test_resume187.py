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
    pm.prepare_point(seeded_store, 'p', point, dict(finder=lambda *a, **k: calls.append(1)))
    assert calls == []
    marker=seeded_store.records('points','p')[0]['subpoint_preparation']
    assert marker['state']=='done' and set(marker['processed_subpoints'])=={'point-s1','point-s2','point-s3'}

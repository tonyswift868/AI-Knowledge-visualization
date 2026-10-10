"""Offline verification of the private cache investigation, opt in explicitly.

MENTORFLOW_CACHE97_ROOT points to private SQLite backups; no credential/model
access is made by this test. Normal full-suite runs skip this analysis.
"""
import json
import os
from pathlib import Path

import pytest

from tests.refactor.cache_prefix.investigate_cache97 import (
    aggregate, canonical, documents_first, first_difference, read_calls,
)

pytestmark = pytest.mark.skipif(not os.getenv('MENTORFLOW_CACHE97_ROOT'),
                               reason='explicit private cache analysis directory required')


def test_cache97_measured_denominator_and_unknown_cost():
    row = dict(usage_json=json.dumps(dict(input_tokens=120,cached_input_tokens=100,
        uncached_input_tokens=20,output_tokens=5,raw={})),cost_json=None,state='succeeded')
    result = aggregate([row,dict(row,usage_json=None,state='failed')])
    assert result['calls'] == 2 and result['measured_calls'] == 1
    assert result['hit_rate'] == 100/120
    assert result['unknown_cost_calls'] == 2
    unknown = dict(row,usage_json=json.dumps(dict(input_tokens=120,cached_input_tokens=0,
        uncached_input_tokens=120,output_tokens=5,raw={'_mentorflow':{'unreported_fields':['cached_input_tokens']}})))
    assert aggregate([unknown])['hit_rate'] is None


def test_cache97_permutation_preserves_roles_content_and_exposes_break():
    rows = [dict(role='user',content=s) for s in (
        '【计划与当前模块】\n{}','【对话原文（数据）】\nold',
        '【当前模块原文（数据）】\nlong source','【本步任务】\nold')]
    after = documents_first(rows)
    assert sorted((m['role'],m['content']) for m in after) == sorted((m['role'],m['content']) for m in rows)
    assert rows[1]['content'].startswith('【对话原文')
    assert after[1]['content'].startswith('【当前模块原文')
    assert first_difference('固定ab','固定ac') == 3
    assert canonical(dict(system='s',messages=rows)).startswith('<system>\ns\n</system>')


def test_cache97_actual_snapshots_and_replay_have_evidence():
    root = Path(os.environ['MENTORFLOW_CACHE97_ROOT'])
    manifest = json.loads((root/'snapshot-manifest.json').read_text(encoding='utf-8'))
    rows,audit = read_calls(manifest)
    assert rows and not audit['conflicts']
    assert all(Path(item['copy']).is_relative_to(root) for item in manifest)
    replay = json.loads((root/'reconstructed.json').read_text(encoding='utf-8'))
    assert {'generate','point_links','point_materials'} <= {r['purpose'] for r in replay['rows']}
    assert 'not historical wire' in replay['limitations']

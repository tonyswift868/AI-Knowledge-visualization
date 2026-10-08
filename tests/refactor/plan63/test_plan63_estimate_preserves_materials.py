"""Batch 63: real draft/store, no model for duration changes."""
import copy
from datetime import timedelta
import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.plan_flow import map_ranges, CHARS_PER_POINT_RANGE
from refactor.learning.course.ledger import PlanWindow, estimate_check_minutes
from tests.refactor.plan37.test_grounded_plan37_flow import fixture, adopt_and_map, TODAY


def test_plan63_actual_cap_and_eight_minutes_with_weighted_days():
    r = map_ranges(PlanWindow(TODAY, TODAY, 400), 30, module_counts=[10, 20], question_counts=[10,20])
    assert r['learning'] == 240
    assert r['total'] == [240 + 60 + 26 + 52] * 2
    assert not r['over_cap'] and not r['suggested_end']
    high = map_ranges(PlanWindow(TODAY, TODAY, 300), 30, module_counts=[10, 20], question_counts=[10,20])
    assert high['over_cap'] and high['suggested_cap'] == 380
    assert high['suggested_end'] == (TODAY + timedelta(days=1)).isoformat()


@pytest.mark.parametrize('phase', ['material_preview', 'outline'])
@pytest.mark.parametrize('change', [{'daily_cap': 400}, {'daily_cap': 1930}, {'end': '2026-10-23'}])
def test_plan63_window_changes_preserve_structure_points_sources_and_reopen(fixture, phase, change):
    flow, model, ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    draft['phase'] = phase
    flow.estimate(draft) if phase == 'outline' else None
    original = copy.deepcopy({k: draft[k] for k in ('material_structure', 'outline', 'sources')})
    calls = list(model.calls)
    flow.adjust_window(draft, ctx, change)
    after = flow.load(draft['plan_id'])
    assert after['phase'] == phase and model.calls == calls
    assert after['material_structure'] == original['material_structure']
    assert after['sources'] == original['sources']
    before_map = original['outline']; after_map = after['outline']
    assert {k:v for k,v in before_map.items() if k != 'ranges'} == {k:v for k,v in after_map.items() if k != 'ranges'}
    assert all(after['form'][k] == v for k,v in change.items())
    public = flow.public(after)
    assert public['material_structure']['ranges']['cap'] == after['form']['daily_cap']
    if phase == 'outline':
        assert after['estimate'] and after_map['ranges']['cap'] == after['form']['daily_cap']


def test_plan63_structure_estimate_counts_expanded_blocks_once_and_needs_no_model(fixture):
    flow, model, ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    module = draft['material_structure']['modules'][0]
    module['refs'] *= 2
    calls = list(model.calls)
    modules, r = flow.structure_ranges(draft)
    sections = {s.id:s for s in flow._sections(draft)[1]}
    chars = sum(sections[k].chars for k in set(module['refs']))
    assert modules[0]['chars'] == chars
    import math
    counts = [math.ceil(chars/v) for v in reversed(CHARS_PER_POINT_RANGE)]
    assert modules[0]['estimated_points'] == counts and r['points'] == counts
    assert r['learning'] == [n*8 for n in counts]
    assert r['module_check'] == r['final_check'] == [0,0] and not r['checks_pending']
    assert model.calls == calls and r['input_tokens'][1] >= r['input_tokens'][0] > 0


def test_plan63_invalid_adjustment_does_not_change_draft(fixture):
    flow, _, ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    before = copy.deepcopy(draft)
    with pytest.raises(BridgeError): flow.adjust_window(draft, ctx, {'daily_cap': 9})
    assert draft == before


def test_plan63_legacy_local_map_reestimates_without_discarding_content(fixture):
    flow, _, _, *_ = fixture
    draft = adopt_and_map(fixture)
    module = draft['outline']['stages'][0]['modules'][0]
    module['minutes'] = [15, 15]
    draft['outline']['ranges']['learning'] = 15
    before = copy.deepcopy(draft)
    view = flow.public(draft)
    assert view['outline']['ranges']['learning'] == 3
    assert view['outline']['stages'][0]['modules'][0]['minutes'] == [3,3]
    assert draft == before


def test_plan63_late_preparation_view_after_account_db_closes_does_not_reopen(fixture):
    flow, _, _, *_ = fixture
    draft = adopt_and_map(fixture)
    flow.services.store.close()
    view = flow.public(draft)
    assert view['material_structure'] == draft['material_structure']
    assert view['outline']['ranges']['learning'] == 3


def test_plan63_warning_does_not_block_user_confirming(fixture):
    flow, model, ctx, *_ = fixture
    model.count = 30
    draft = adopt_and_map(fixture)
    draft['form'].update(end=TODAY.isoformat(), daily_cap=10)
    flow.estimate(draft)
    assert draft['outline']['ranges']['over_cap']
    flow.generate(draft, ctx)
    assert draft['phase'] == 'ready'
    points = flow.services.store.records('points', draft['plan_id'])
    assert len(points) == 30 and all(p['minutes'] == 3 for p in points)
    assert draft['outline']['ranges']['total'][1]==90  # Prior to preparation: learning only.
    assert flow.services.store.get_plan(draft['plan_id'])['total_minutes'] == flow.public(draft)['outline']['ranges']['total'][1]

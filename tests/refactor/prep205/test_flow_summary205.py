"""Preparation summaries project current content, not obsolete worker counters."""
from datetime import date
from types import SimpleNamespace

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import point_materials as pm
from tests.refactor.material_questions70.test_material_questions70_contract import material_store70


def flow_for(store, root):
    services = SimpleNamespace(store=store, account_id=store.account_id,
                               data_root=root, _closed=False, gateway=None)
    return PlanFlow(services, lambda *_: None, lambda: date(2026, 10, 6))


def test_summary205_worker_cannot_restore_obsolete_80_point_counts(material_store70, tmp_path):
    store = material_store70
    pm.prepare_module(store, 'p', 'm', {}, lambda *_: None)
    flow = flow_for(store, tmp_path)
    flow._save_material_summary('p', 'm', dict(points=80, cloze_points=80, cloze_heavy=False))
    module = next(m for m in store.records('modules', 'p') if m['id'] == 'm')
    assert module['material_preparation']['points'] == 1
    assert module['material_preparation']['cloze_points'] == 0
    assert module['material_preparation']['questions'] == module['question_count'] == 2


def test_summary205_ready_recovery_repairs_counts_without_repreparing(material_store70, tmp_path):
    store = material_store70
    pm.prepare_module(store, 'p', 'm', {}, lambda *_: None)
    store.save_module_preparation('p', 'm', dict(state='failed', points=80,
        cloze_points=80, error='PAYLOAD_TOO_LARGE', attempts=3))
    flow = flow_for(store, tmp_path)
    assert flow._module_ready('p', 'm')
    old = next(m for m in store.records('modules', 'p') if m['id'] == 'm')
    flow._mark_material_ready('p', 'm', old['material_preparation'])
    module = next(m for m in store.records('modules', 'p') if m['id'] == 'm')
    assert module['material_preparation']['state'] == 'done'
    assert module['material_preparation']['points'] == 1
    assert module['material_preparation']['cloze_points'] == 0
    assert module['question_count'] == module['material_preparation']['questions'] == 2
    before = store.pending_records(1000)
    events = store.records('ledger_events', 'p')
    flow._mark_material_ready('p', 'm', module['material_preparation'])
    assert store.pending_records(1000) == before
    assert store.records('ledger_events', 'p') == events


def test_summary205_expired_plan_can_refresh_without_rewriting_timing(material_store70, tmp_path):
    store = material_store70
    pm.prepare_module(store, 'p', 'm', {}, lambda *_: None)
    store.save_module_preparation('p', 'm', dict(state='failed', points=80, cloze_points=80))
    flow = flow_for(store, tmp_path)
    flow.study_day = lambda: date(2026, 10, 27)  # The plan ended on Oct 11.
    old_module = next(m for m in store.records('modules', 'p') if m['id'] == 'm')
    events = store.records('ledger_events', 'p')
    flow._save_material_summary('p', 'm', dict(points=80, cloze_points=80, cloze_heavy=False))
    module = next(m for m in store.records('modules', 'p') if m['id'] == 'm')
    assert module['question_count'] == module['material_preparation']['questions'] == 2
    assert module['material_preparation']['points'] == 1
    assert module['material_preparation']['state'] == 'failed'  # Counts do not claim completion.
    assert module['check_minutes'] == old_module['check_minutes']
    assert store.records('ledger_events', 'p') == events

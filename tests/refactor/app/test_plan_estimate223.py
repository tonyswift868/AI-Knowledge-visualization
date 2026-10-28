from refactor.learning.app import plan_flow
from tests.refactor.plan37.test_grounded_plan37_flow import QUOTE, adopt_and_map, fixture


def test_223_no_original_questions_have_zero_preview_check_reserve(fixture):
    flow, _model, _ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    modules = plan_flow.agents.outline_modules(draft['outline'])
    assert sum(len(module['points']) for module in modules) > 0

    flow.estimate(draft, annotate=False)

    ranges = draft['outline']['ranges']
    assert ranges['estimated_question_count'] == 0
    assert ranges['total'] == [ranges['learning'], ranges['learning']]


def test_223_estimate_after_preparation_uses_actual_question_count(fixture):
    flow, _model, ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    flow.estimate(draft, annotate=False)
    flow.generate(draft, ctx)

    plan_id = draft['plan_id']
    module = flow.services.store.records('modules', plan_id)[0]
    saved = flow.load(plan_id)
    ranges = saved['outline']['ranges']
    assert module['question_count'] == 0
    assert ranges['estimated_question_count'] == module['question_count']
    assert ranges['total'] == [ranges['learning'], ranges['learning']]
    assert module['check_minutes'] == module['final_check_minutes'] == 0


def test_223_original_material_question_supplies_unprepared_estimate(fixture):
    flow, _model, _ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    plan_id = draft['plan_id']
    modules = plan_flow.agents.outline_modules(draft['outline'])
    chunk_id = modules[0]['points'][0]['chunk_ids'][0]
    chunk = next(c for c in flow.services.store.records('source_chunks', plan_id) if c['id'] == chunk_id)
    chunk['text'] = f'## What is pressure?\n\n{QUOTE}\n'
    chunk.pop('block_fragments', None)
    with flow.services.store.transaction():
        flow.services.store._put('source_chunks', plan_id, chunk)

    counts = flow._question_counts(draft, modules)
    assert counts == [1]
    modules[0].pop('id', None)
    modules[0]['points'][0].pop('id', None)
    assert flow._question_counts(draft, modules) == [1]
    flow.estimate(draft, annotate=False)
    assert draft['outline']['ranges']['estimated_question_count'] == 1


def test_223_actual_prepared_question_count_overrides_unprepared_fallback(fixture):
    from types import SimpleNamespace

    from refactor.learning.app.plan_flow import PlanFlow
    from tests.refactor.mastery204.test_store204_grants import prepared_store

    store = prepared_store(fixture[0].services.data_root / 'prepared-estimate')
    try:
        flow = object.__new__(PlanFlow)
        flow.services = SimpleNamespace(store=store)
        draft = {'plan_id': 'plan', 'sources': [{'source_id': 'source', 'status': 'adopted'}]}
        modules = [dict(id='module', points=[dict(id='point', chunk_ids=['chunk'])])]
        assert flow._question_counts(draft, modules) == [1]
    finally:
        store.close()

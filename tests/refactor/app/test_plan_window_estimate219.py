from refactor.learning.app import plan_flow
from tests.refactor.plan37.test_grounded_plan37_flow import fixture, adopt_and_map


class Signal:
    def connect(self, _handler):
        pass


class CaptureBridge:
    def __init__(self):
        self.handlers = {}
        self.event = Signal()
        self.closing = Signal()
    def register(self, name, handler, **kwargs):
        self.handlers[name] = handler
    def push(self, *_args, **_kwargs):
        pass


def test_219_plan_window_registration_dispatches_draft_adjustment(fixture, monkeypatch):
    flow, _model, ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    draft['phase'] = 'outline'
    flow.save(draft)
    bridge = CaptureBridge()
    monkeypatch.setattr(plan_flow, 'PlanFlow', lambda *_args, **_kwargs: flow)
    services = lambda: flow.services
    plan_flow.register(bridge, services, None, lambda _settings: flow.study_day())

    before = draft['form']['daily_cap']
    result = bridge.handlers['plan.window'](
        {'plan_id': draft['plan_id'], 'changes': {'daily_cap': before + 10}}, ctx)

    assert result['draft']['form']['daily_cap'] == before + 10
    active_call = []
    monkeypatch.setattr(flow, 'change_window', lambda plan_id, params: active_call.append((plan_id, params)) or {'active': True})
    active_params = {'plan_id': draft['plan_id'], 'end': '2026-10-20'}
    assert bridge.handlers['plan.window'](active_params, ctx) == {'active': True}
    assert active_call == [(draft['plan_id'], active_params)]


def test_219_plan_preview_includes_general_module_and_final_check_reserve(fixture):
    flow, _model, _ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    modules = plan_flow.agents.outline_modules(draft['outline'])
    point_count = sum(len(module['points']) for module in modules)
    assert point_count > 0

    flow.estimate(draft, annotate=False)

    ranges = draft['outline']['ranges']
    learning = ranges['learning']
    expected = plan_flow.map_ranges(
        flow._window(draft), point_count, module_counts=[len(m['points']) for m in modules],
        question_counts=[len(m['points']) for m in modules], learning_minutes=learning)
    assert ranges['total'] == expected['total']
    assert ranges['daily'] == expected['daily']
    assert ranges['total'][1] > learning
    assert draft['estimate']['note'] == '知识地图已核验；保存时不再生成知识点。总时长含暂估的检测储备，备题后按实际题量调整。'

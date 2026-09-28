from copy import deepcopy
from decimal import Decimal

import pytest

from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.pricing import PriceCatalog
from .plan_dialogue_batch9 import Batch9Budget, cases, classify, evaluate


def test_batch9_budget_keeps_unknown_reservation_and_three_yuan_cap(tmp_path):
    path = tmp_path / 'budget.json'
    budget = Batch9Budget(path, PriceCatalog.empty())
    budget.reserve()
    assert Decimal(budget.state['held']) == Decimal('2.50')
    resumed = Batch9Budget(path, PriceCatalog.empty())
    with pytest.raises(ModelError, match='预算'):
        resumed.reserve()
    another = Batch9Budget(tmp_path / 'second.json', PriceCatalog.empty())
    another.state['peak_amount'] = '.51'
    with pytest.raises(ModelError, match='预算'):
        another.reserve()


def test_batch9_checks_parameters_before_blaming_host():
    case = next(c for c in cases() if c['id'] == 'H-M03')
    before = dict(draft=dict(form={'level': 'beginner'}, messages=[]), records={}, plan={})
    after = deepcopy(before)
    after['draft']['form']['level'] = 'intermediate'
    calls = [dict(purpose='plan_intent', result=dict(text='{"intents":[{"kind":"update_form","level":"intermediate"}],"reply":""}'))]
    checks = evaluate(case, before, after, calls, {})
    assert classify(dict(checks=checks, calls=calls)) == '意图判断'
    calls[0]['result']['text'] = '{"intents":[{"kind":"update_form","level":"advanced"}],"reply":""}'
    checks = evaluate(case, before, after, calls, {})
    assert classify(dict(checks=checks, calls=calls)) == '宿主执行'


def test_scope_requires_every_requested_chapter_not_just_maximum():
    case = next(c for c in cases() if c['id'] == 'H-MI01')
    before = dict(draft=dict(form={'daily_cap': 30}, scope={}, messages=[]), records={}, plan={})
    after = deepcopy(before)
    after['draft'].update(form={'daily_cap': 45}, scope={'include': ['s1.2']})
    context = dict(sections=[dict(id=f's1.{n + 1}', title=f'教材 / 第 {n} 章') for n in range(1, 5)])
    calls = [dict(purpose='plan_intent', result=dict(text='{"intents":[{"kind":"update_form","daily_cap":45},{"kind":"limit_scope","include":["s1.2"],"exclude":[]}]}'))]
    checks = evaluate(case, before, after, calls, context)
    assert not next(c for c in checks if c['name'] == 'intent_scope')['passed']
    assert not next(c for c in checks if c['name'] == 'host_scope')['passed']

import io
import json
import logging
import threading
from datetime import date
from types import SimpleNamespace

import pytest

from refactor import runtime_logging
from refactor.learning.app.bridge import CallContext
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.connections.errors import ModelError
from refactor.learning.course import plan_intent
from refactor.learning.course.store import CourseStore
from tests.refactor.app.test_app_plan_flow import ScriptedModel, fetcher, form
from tests.refactor.planner.test_github_repo_import import fake_fetch


URL = 'https://github.com/tonyswift868/mentorflow-guide'
SAID = '按模块串讲一遍。资料我自己提供，只用这个仓库里的资料，不用另外找：' + URL


@pytest.fixture
def intake169(tmp_path):
    today = date(2026, 10, 19)
    model = ScriptedModel()
    model.sufficiency = [dict(enough=False, questions=['按模块串讲还是按面试题补？'],
                             goal='学习项目', focus=[]),
                         dict(enough=True, questions=[], goal='学习项目', focus=[])]
    store = CourseStore('intent169', tmp_path)
    services = SimpleNamespace(store=store, gateway=model, account_id='intent169', data_root=tmp_path)
    flow = PlanFlow(services, lambda *args: None, lambda: today, fetcher=fetcher)
    context = CallContext('intent169', threading.Event(), lambda *args: None)
    pid = flow.current()['plan_id']
    flow.run(pid, lambda draft, ctx: flow.submit_form(draft, ctx, form(today)), context)
    yield flow, context, pid, model
    store.close()


def test_host169_first_step_preserves_only_mine_and_link_until_confirmation(intake169):
    flow, ctx, pid, model = intake169
    draft = flow.load(pid)
    assert draft['phase'] == 'questions'
    draft['messages'].append(dict(role='user', kind='user_text', text=SAID))
    # Explicit host action: the parser contract is covered separately.
    flow._update_form(draft, ctx, dict(only_mine=True, notes_append=SAID))
    assert draft['form']['only_mine'] is True
    assert URL in draft['form']['notes']
    assert draft['phase'] == 'preview'
    assert not draft['sources']
    assert not any(row[0] == 'research' for row in model.calls)
    calls = []
    fixture_fetch = fake_fetch(calls)
    flow.repo_fetch = lambda url, cancel: fixture_fetch(
        url.replace('tonyswift868/mentorflow-guide', 'acme/book'), cancel)
    flow._confirm_preview(draft, ctx)
    assert draft['phase'] == 'sources'
    assert [row['url'] for row in draft['sources'] if row['status'] == 'adopted'] == [URL]
    assert calls and not any(row[0] == 'research' for row in model.calls)
    reopened = PlanFlow(flow.services, lambda *args: None, flow.study_day, fetcher=fetcher).load(pid)
    assert reopened['form']['only_mine'] is True
    assert URL in reopened['form']['notes']


def test_host169_chat_rechecks_once_for_single_material_preference_update(intake169):
    flow, ctx, pid, model = intake169
    before = len(model.calls)
    model.intents = [[dict(kind='update_form', only_mine=True, notes_append=SAID)]]
    result = flow.run(pid, lambda draft, call: flow.chat(draft, call, SAID), ctx)['draft']
    assert result['phase'] == 'preview'
    assert result['form']['only_mine'] is True
    assert URL in result['form']['notes']
    # One intent, one sufficiency verdict and one preview; no duplicate answer action.
    assert [call[0] for call in model.calls[before:]] == ['plan_intent', 'sufficiency', 'sufficiency']
    assert not result['sources']


def test_logs169_two_validation_failures_have_safe_field_and_reason(intake169):
    flow, ctx, pid, model = intake169
    context = dict(flow._intent_context(flow.load(pid)), phase='sources')
    bad = json.dumps(dict(intents=[dict(kind='import_sources', urls=['not-a-url'])], reply=''))
    model.complete = lambda *args, **kwargs: SimpleNamespace(text=bad)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(runtime_logging.DiagnosticFormatter())
    logger = logging.getLogger('refactor')
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        with pytest.raises(ModelError) as raised:
            flow._ask('plan_intent', plan_intent.intent_request(context, SAID),
                lambda text: plan_intent.parse_intents(text, context), ctx, plan_id=pid)
        assert raised.value.code == 'AGENT_FORMAT'
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    failures = [row for row in rows if row.get('state') == 'FORMAT_INVALID']
    assert len(failures) == 2
    assert [row['count'] for row in failures] == [1, 2]
    assert all(row['field'] == 'urls' and row['reason'] == 'invalid_url_list' for row in failures)
    assert all(row['purpose'] == 'plan_intent' and row['code'] == 'AGENT_FORMAT' for row in failures)
    assert SAID not in output.getvalue() and URL not in output.getvalue()


def test_logs169_unknown_exception_text_does_not_enter_json(intake169):
    flow, ctx, pid, model = intake169
    model.complete = lambda *args, **kwargs: SimpleNamespace(text='not-logged-model-response')
    secret = 'SYNTHETIC_KEY_SHOULD_STAY_PRIVATE'
    user_text = 'private learner original response'
    def reject(_):
        raise plan_intent.AgentFormatError(secret + user_text)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(runtime_logging.DiagnosticFormatter())
    logger = logging.getLogger('refactor')
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        with pytest.raises(ModelError):
            flow._ask('plan_intent', ('system', []), reject, ctx, plan_id=pid)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()
    assert secret not in output.getvalue() and user_text not in output.getvalue()
    assert 'not-logged-model-response' not in output.getvalue()

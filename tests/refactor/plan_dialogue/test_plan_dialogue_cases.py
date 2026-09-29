"""Offline checks of the evaluator, never a substitute for A-class live results."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.usage import UsageLog

from .plan_dialogue_checks import SUPPORTED, evaluate
from .plan_dialogue_harness import (
    HERE, OUTPUT_ROOT, Harness, ScriptedGateway, RepoWeb, clone_tree, load_cases,
    modules, question_prestate, render_case, seed_sources,
)
from .plan_dialogue_live import Budget, CATALOG, RecordingGateway, run_live


@pytest.fixture
def dialogue_tmp():
    root = OUTPUT_ROOT / "offline"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="check-", dir=root) as directory:
        yield Path(directory)


def test_plan_dialogue_dataset_has_required_classes_variants_and_executable_assertions():
    cases = load_cases()
    assert len(cases) == 51
    assert len({c["id"] for c in cases}) == len(cases)
    a, b = [c for c in cases if c["class"] == "A"], [c for c in cases if c["class"] == "B"]
    assert (len(a), len(b)) == (33, 18)
    assert all(c["status"] == "pending" for c in b)
    assert all(c["expected"] and set(c["expected"]) <= SUPPORTED and c["preconditions"] for c in cases)
    assert {c["phase"] for c in a} == {"questions", "sources", "outline"}
    assert {c["phase"] for c in b} == {"sources", "outline", "ready"}
    assert any(len(c["user"]) <= 1 for c in a)
    assert any(len(c["user"]) >= 180 for c in a)
    assert {"直接回答", "答非所问", "只答一部分", "一句话两件事"} <= {c["variant"] for c in a}
    assert all("form_unchanged" in c["expected"] for c in a)
    filenames = [p.name for p in HERE.parents[1].rglob("test_*.py")]
    for path in HERE.glob("test_*.py"):
        assert filenames.count(path.name) == 1


def test_plan_dialogue_requires_opt_in_before_account_or_network(monkeypatch):
    monkeypatch.delenv("MF_LIVE_EVAL", raising=False)
    with patch("refactor.learning.connections.gateway.ModelGateway", side_effect=AssertionError("must not construct")):
        with pytest.raises(RuntimeError, match="MF_LIVE_EVAL=1"):
            run_live()
    monkeypatch.setenv("MF_LIVE_EVAL", "1")
    monkeypatch.delenv("MF_LIVE_EVAL_B", raising=False)
    with patch("refactor.learning.connections.gateway.ModelGateway", side_effect=AssertionError("must not construct")):
        with pytest.raises(RuntimeError, match="MF_LIVE_EVAL_B=1"):
            run_live(include_b=True)


def test_plan_dialogue_real_route_and_copy_isolate_form_cards_and_course(dialogue_tmp):
    gateway = ScriptedGateway()
    template = dialogue_tmp / "template"
    h = Harness(template, gateway)
    try:
        pid = seed_sources(h)
        original = h.snapshot(pid)
        assert original["plan"]["state"] == "creating"
        assert len(original["records"]["source_chunks"]) >= 8
        assert [c["status"] for c in original["draft"]["sources"]] == ["adopted", "rejected"]
    finally:
        h.close()
    copy_root = dialogue_tmp / "copy"
    clone_tree(template, copy_root)
    h = Harness(copy_root, gateway)
    try:
        case = next(c for c in load_cases() if c["id"] == "A-Q05")
        question_prestate(h, pid, case)
        before = h.snapshot(pid)
        first = len(gateway.calls)
        h.call("plan.say", plan_id=pid, text=case["user"])
        after = h.snapshot(pid)
        assert [c["purpose"] for c in gateway.calls[first:]] == ["plan_intent", "sufficiency"]
        assert after["draft"]["phase"] == "sources"
        assert before["draft"]["form"] == after["draft"]["form"]
        h.call("plan.outline", plan_id=pid)
        assert len(modules(h.snapshot(pid))) >= 8
        assert not h.snapshot(pid)["records"]["points"]
    finally:
        h.close()
    h = Harness(template, gateway)
    try:
        assert h.snapshot(pid) == original
    finally:
        h.close()


def test_plan_dialogue_url_path_import_and_source_search_are_distinct(dialogue_tmp):
    gateway = ScriptedGateway()
    h = Harness(dialogue_tmp, gateway)
    try:
        pid = seed_sources(h)
        before = h.snapshot(pid)
        h.call("plan.say", plan_id=pid, text="https://docs.python.org/3/library/csv.html")
        after = h.snapshot(pid)
        case = next(c for c in load_cases() if c["id"] == "A-U01")
        assert all(c["passed"] for c in evaluate(case, before, after, [], []))
        assert not gateway.calls
        h.call("plan.say", plan_id=pid, text="请找有练习的入门资料")
        # No qualifying new source: a second round that adds nothing stops the search (16-MF04), then shortage is reported.
        assert [c["purpose"] for c in gateway.calls] == ["plan_intent", "research", "research"]
        assert any("权威资料不足" in m.get("text", "") for m in h.snapshot(pid)["draft"]["messages"])
        assert all(c["mode"] == "offline_synthetic_html" for c in h.web.calls)
    finally:
        h.close()


def test_plan_dialogue_oracle_rejects_state_mutation_even_with_reassuring_reply(dialogue_tmp):
    gateway = ScriptedGateway()
    h = Harness(dialogue_tmp, gateway)
    try:
        pid = seed_sources(h)
        before = h.snapshot(pid)
        after = copy.deepcopy(before)
        after["draft"]["phase"] = "ready"
        after["draft"]["form"]["daily_cap"] = 5
        after["plan"]["state"] = "active"
        after["records"]["points"].append({"id": "unauthorized"})
        case = next(c for c in load_cases() if c["id"] == "A-G01")
        failed = {c["name"] for c in evaluate(case, before, after, [], []) if not c["passed"]}
        assert {"phase", "form_unchanged", "plan_state_unchanged", "no_generated_points"} <= failed
    finally:
        h.close()


def test_plan_dialogue_search_oracle_requires_observed_tools_not_just_enable_flag(dialogue_tmp):
    h = Harness(dialogue_tmp, ScriptedGateway())
    try:
        pid = seed_sources(h)
        snap = h.snapshot(pid)
        case = next(c for c in load_cases() if c["id"] == "A-S07")
        calls = [{"purpose": "research", "enable_search": True, "result": {"usage": {"search_requests": 0}, "text": '{"sources":[]}'} }]
        failed = {c["name"] for c in evaluate(case, snap, snap, calls, []) if not c["passed"]}
        assert {"native_search_executed", "new_cards_min"} <= failed
    finally:
        h.close()


def test_plan_dialogue_repository_double_exercises_real_import_not_web_fixture(dialogue_tmp):
    from tests.refactor.planner.test_github_repo_import import fake_fetch
    recorded = []
    h = Harness(dialogue_tmp, ScriptedGateway(), repo_fetch=fake_fetch(recorded))
    try:
        pid = seed_sources(h)
        h.call("plan.say", plan_id=pid, text="https://github.com/acme/book?utm_source=fixture")
        draft = h.flow.load(pid)
        assert draft["sources"][-1]["url"] == "https://github.com/acme/book"
        assert len(h.repo.calls) == 5
        assert all(r["bytes"] > 0 and r["mode"] == "offline_repository" for r in h.repo.calls)
        assert all("github.com/acme/book" not in r["url"] for r in h.web.calls)
    finally:
        h.close()
    with pytest.raises(AssertionError, match="disabled"):
        RepoWeb()("https://api.github.com/repos/acme/book")


def response():
    return {"content": [{"type": "text", "text": "ok"}], "usage": {
        "input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 10},
        "model": "deepseek-v4-flash", "stop_reason": "end_turn"}


def test_plan_dialogue_budget_reserves_before_io_and_survives_interruption(dialogue_tmp):
    catalog = PriceCatalog(CATALOG)
    path = dialogue_tmp / "budget.json"
    budget = Budget(path, catalog)
    budget.reserve()
    assert json.loads(path.read_text())["held"] == "2.50"
    interrupted = Budget(path, catalog)
    with pytest.raises(ModelError, match="未发送"):
        interrupted.reserve()
    budget.received(response(), model="deepseek-v4-flash", searching=False,
                    started=datetime(2026, 9, 27, tzinfo=timezone.utc))
    assert Decimal(budget.state["peak_amount"]) == Decimal("0.000316")
    assert budget.state["held"] == "0"
    budget.state["peak_amount"] = "2.51"
    with pytest.raises(ModelError, match="预算"):
        budget.reserve()


def test_plan_dialogue_unknown_meter_stops_instead_of_zero_cost(dialogue_tmp):
    budget = Budget(dialogue_tmp / "budget.json", PriceCatalog.empty())
    budget.reserve()
    with pytest.raises(ModelError):
        budget.received(response(), model="deepseek-v4-flash", searching=False,
                        started=datetime(2026, 9, 27, tzinfo=timezone.utc))
    assert budget.state["blocked"] and budget.state["held"] == "2.50"


def test_plan_dialogue_reconciliation_never_releases_unknown_reservation(dialogue_tmp):
    from dataclasses import asdict
    from refactor.learning.connections.providers.anthropic_messages import parse
    budget = Budget(dialogue_tmp / 'budget.json', PriceCatalog(CATALOG))
    when = datetime(2026, 9, 27, 20, tzinfo=timezone.utc)
    usage = parse(response(), 'deepseek', 'deepseek-v4-flash', False).usage
    budget.state.update(blocked=True, reason='gateway_usage_incomplete', peak_amount='0.000316')
    budget.state['responses'] = [{'case': 'A-S01', 'at': when.isoformat(), 'usage': asdict(usage)}]
    with patch.object(__import__(Budget.__module__, fromlist=['utcnow']), 'utcnow', return_value=when):
        budget.resume_metered_failure()
    assert budget.state['peak_amount'] == '0.000316' and not budget.state['blocked']
    assert budget.transport_log.summary(when - timedelta(seconds=1),
        when + timedelta(seconds=1))['total']['amount'] == Decimal('0.000158')
    budget.state.update(blocked=True, reason='gateway_usage_incomplete', held='2.50')
    with pytest.raises(RuntimeError, match='Cannot reconcile'):
        budget.resume_metered_failure()


def test_plan_dialogue_gateway_isolates_ledger_before_construction(dialogue_tmp, monkeypatch):
    from contextlib import ExitStack
    import sqlite3
    from refactor.learning.connections.gateway import ModelGateway
    import importlib
    live = importlib.import_module(Budget.__module__)
    admin_root = dialogue_tmp / 'fake-admin'
    run_dir = dialogue_tmp / 'run'
    run_dir.mkdir()
    # New empty preset uses no credential backend; never create/read a real key.
    seed = ModelGateway(live.ADMIN, admin_root, price_catalog=PriceCatalog(CATALOG))
    seed.save_preset('deepseek')
    seed.close()
    original = (admin_root / 'learning/accounts' / live.ADMIN / 'model-usage.sqlite3').read_bytes()
    monkeypatch.setattr(live, 'ADMIN_ROOT', admin_root)
    budget = Budget(dialogue_tmp / 'budget.json', PriceCatalog(CATALOG))
    with ExitStack() as stack:
        gateway = live.live_gateway(stack, run_dir, budget)
        gateway.inner.usage_log.record('deepseek', 'deepseek-v4-flash', 'isolated', None, error_code='OFFLINE_ONLY')
        assert (run_dir / 'usage.sqlite3').exists()
        with pytest.raises(AssertionError, match='outside'):
            sqlite3.connect(r'D:\not-a-dialogue-test.sqlite3')
        with pytest.raises(AssertionError, match='credentials'):
            gateway.inner._credentials.put('f' * 32, 'fake-only')
    assert (admin_root / 'learning/accounts' / live.ADMIN / 'model-usage.sqlite3').read_bytes() == original


def test_plan_dialogue_failure_trace_sanitizes_secret_and_records_unknown_usage(dialogue_tmp):
    usage = UsageLog(dialogue_tmp / "usage.sqlite3", catalog=PriceCatalog(CATALOG))
    class Failing:
        usage_log = usage
        price_catalog = PriceCatalog(CATALOG)
        def complete(self, purpose, messages, **kwargs):
            usage.record("deepseek", "deepseek-v4-flash", purpose, None, error_code="MODEL_AUTH_FAILED")
            raise ModelError("MODEL_AUTH_FAILED", "TEST-SECRET-MUST-NOT-APPEAR")
    budget = Budget(dialogue_tmp / "budget.json", Failing.price_catalog)
    gateway = RecordingGateway(Failing(), budget, dialogue_tmp)
    with pytest.raises(ModelError):
        gateway.complete("research", [{"role": "user", "content": "合成测试"}], enable_search=True)
    raw = (dialogue_tmp / "model_calls.jsonl").read_text(encoding="utf-8")
    assert "TEST-SECRET-MUST-NOT-APPEAR" not in raw
    assert "MODEL_AUTH_FAILED" in raw
    assert budget.state["blocked"]
    assert gateway.summary()["total"]["unpriced_calls"] == 1


def test_plan_dialogue_future_b_ready_precondition_and_confirmation_are_executable(dialogue_tmp):
    from .plan_dialogue_live import prepare_b
    gateway = ScriptedGateway()
    h = Harness(dialogue_tmp, gateway)
    try:
        pid = seed_sources(h)
        h.call("plan.outline", plan_id=pid)
        case = next(c for c in load_cases() if c["id"] == "B-M01")
        prepare_b(h, pid, case)
        assert h.snapshot(pid)["draft"]["phase"] == "ready"
        assert h.snapshot(pid)["records"]["points"]
        c = next(c for c in load_cases() if c["id"] == "B-F01")
        assert "{" not in render_case(c, h.snapshot(pid))["expected"]["field_value"]
    finally:
        h.close()


def test_plan_dialogue_false_form_ack_is_failure_even_when_host_data_is_safe(dialogue_tmp):
    h = Harness(dialogue_tmp, ScriptedGateway())
    try:
        pid = seed_sources(h)
        h.call('plan.outline', plan_id=pid)
        before = h.snapshot(pid)
        after = copy.deepcopy(before)
        after['draft']['messages'].append({'role': 'ai', 'kind': 'text',
            'text': '关于每天 5 分钟：已按你的要求记入设置。'})
        case = next(c for c in load_cases() if c['id'] == 'A-G02')
        checks = {c['name']: c for c in evaluate(case, before, after, [], [])}
        assert checks['form_unchanged']['passed']
        assert not checks['no_false_form_change']['passed']
        after['draft']['messages'][-1]['text'] = '没有修改每日上限，目前仍为 60 分钟。'
        assert all(c['passed'] for c in evaluate(case, before, after, [], []))
    finally:
        h.close()


def test_plan_dialogue_topic_order_ignores_forward_reference_in_summary(dialogue_tmp):
    h = Harness(dialogue_tmp, ScriptedGateway())
    try:
        pid = seed_sources(h)
        before = h.snapshot(pid)
        cleaning = {'title': '数据清洗', 'summary': '清理数据', 'refs': ['s1.3'], 'ref_titles': ['第 3 章 数据清洗']}
        errors = {'title': '异常处理', 'summary': '为后续清洗打好基础', 'refs': ['s1.4'], 'ref_titles': ['第 4 章 异常处理']}
        before['draft'].update(phase='outline', outline={'version': 1, 'stages': [{'modules': [cleaning, errors]}]})
        after = copy.deepcopy(before)
        after['draft']['outline'] = {'version': 2, 'stages': [{'modules': [errors, cleaning]}]}
        case = next(c for c in load_cases() if c['id'] == 'A-O04')
        assert all(c['passed'] for c in evaluate(case, before, after, [], []))
        after['draft']['outline']['stages'][0]['modules'] = [cleaning, errors]
        assert any(not c['passed'] for c in evaluate(case, before, after, [], []))
    finally:
        h.close()


def test_plan_dialogue_official_repo_docs_and_recovered_retry_are_accepted(dialogue_tmp):
    h = Harness(dialogue_tmp, ScriptedGateway())
    try:
        pid = seed_sources(h)
        before = h.snapshot(pid)
        after = copy.deepcopy(before)
        after['draft']['sources'] += [
            {'card_id': 'official-doc', 'status': 'new', 'url': 'https://docs.python.org/3/library/csv.html'},
            {'card_id': 'official-repo', 'status': 'new', 'url': 'https://github.com/python/cpython/blob/main/Doc/library/csv.rst'}]
        case = next(c for c in load_cases() if c['id'] == 'A-S05')
        calls = [{'purpose': 'research', 'enable_search': True, 'error_code': 'MODEL_RESPONSE_INVALID'},
                 {'purpose': 'research', 'enable_search': True, 'result': {'usage': {'search_requests': 1},
                    'text': json.dumps({'sources': [{'url': 'https://docs.python.org/3/library/csv.html', 'title': '官方 CSV'}]})}}]
        checks = {c['name']: c for c in evaluate(case, before, after, calls, [])}
        assert checks['product_call_succeeded']['passed'] and checks['official_preferred']['passed']
        after['draft']['sources'][-2]['url'] = 'https://example.test/forum'
        checks = {c['name']: c for c in evaluate(case, before, after, calls, [])}
        assert not checks['official_preferred']['passed']
    finally:
        h.close()

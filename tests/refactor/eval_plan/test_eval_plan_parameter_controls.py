"""Offline validation of experiment isolation, accounting and paired comparisons."""
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re
import tempfile
import threading
from types import SimpleNamespace

import pytest

import plan_parameter_eval as evalplan


@pytest.fixture
def work(monkeypatch):
    # Short D: paths also exercise the real 69-character account namespace on Windows.
    evalplan.TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ep-", dir=evalplan.TEMP_ROOT) as folder:
        path = Path(folder)
        monkeypatch.setattr(evalplan, "TEMP_ROOT", path)
        yield path


def test_eval_plan_controls_change_only_prespecified_fields():
    configs = evalplan.configurations(date(2026, 9, 28))
    changed = lambda name: {k for k in configs[name] if configs[name][k] != configs["base"][k]}
    assert changed("advanced") == {"level"}
    assert changed("cap30") == {"daily_cap"}
    assert changed("weeks2") == {"end"}
    assert changed("tight") == {"end", "daily_cap"}
    assert changed("practice") == changed("principle") == {"notes"}
    assert changed("deep") == {"final_goal"}
    assert set(evalplan.run_order(configs)) == {(name, repeat) for name in configs for repeat in (1, 2)}
    assert evalplan.run_order(configs) == evalplan.run_order(configs)
    assert all(url in evalplan.learner_reply(configs["base"]) for url in evalplan.URLS)
    assert evalplan.learner_reply(configs["advanced"]) == evalplan.learner_reply(configs["base"])
    assert "独立用Python" in evalplan.learner_reply(configs["deep"])


def test_eval_plan_rejects_unisolated_root(work):
    with pytest.raises(ValueError):
        evalplan.Experiment(work)
    with pytest.raises(ValueError):
        evalplan.Experiment(work.parent / "not-the-experiment")


def test_eval_plan_live_is_opt_in_without_constructing_services(work, monkeypatch):
    monkeypatch.delenv("MF_LIVE_EVAL", raising=False)
    monkeypatch.setattr(evalplan, "Experiment", lambda *args: pytest.fail("No network or account access"))
    assert evalplan.main([]) == 0
    assert evalplan.main(["--root", str(work / "unused")]) == 0
    assert not (work / "unused").exists()


def test_eval_plan_excludes_other_account_activity():
    def group(key, cost):
        return dict(key=key, known_amounts={"CNY": cost}, calls=1, unpriced_calls=0, amount_complete=True,
                    known_tokens={"input_tokens": 10})
    own = evalplan.own_usage({"groups": [group("mine:trial", "0.02"), group("other:trial", "9")]}, "mine")
    assert own["known_amounts"] == {"CNY": Decimal("0.02")}
    assert own["calls"] == 1


@pytest.mark.parametrize("left,right,clear", [([10, 10], [20, 20], True), ([10, 20], [15, 25], False),
                                             ([10, 10], [10, 10], False)])
def test_eval_plan_effect_is_compared_to_repeat_jitter(left, right, clear):
    assert evalplan.contrast(left, right)["exceeds_repeat_variation"] is clear


def test_eval_plan_blind_input_does_not_leak_condition_metadata():
    record = {"draft": {"form": {"level": "SECRET", "notes": "SECRET"}, "goal": "SECRET", "outline": {
        "ranges": {"cap": 120}, "agent": "SECRET", "stages": [{"modules": [
            dict(id="unique", title="循环", summary="推理与行动", minutes=[30, 50], refs=["s1.1"])]}]}},
        "metrics": {"points": [dict(module_id="unique", id="SECRET", title="行动", summary="执行工具", minutes=8)]}}
    encoded = json.dumps(evalplan.blind_payload(record), ensure_ascii=False)
    assert "SECRET" not in encoded and "cap" not in encoded and "unique" not in encoded
    assert "执行工具" in encoded


def test_eval_plan_blind_resume_keeps_original_counterbalance(work):
    pair = ["base-r1", "weeks2-r1"]
    original = evalplan.blind_order(work, "date_only", 1, pair)
    reversed_labels = [pair[i] for i in reversed(original)]
    evalplan.write_json(work / "judges/date_only-r1-reverse.json", dict(order=reversed_labels))
    # Completion of unrelated, earlier pairs must never shift this pair's RNG order.
    evalplan.write_json(work / "judges/level-r1-forward.json", dict(order=["base-r1", "advanced-r1"]))
    assert evalplan.blind_order(work, "date_only", 1, pair) == original
    evalplan.write_json(work / "judges/date_only-r1-forward.json", dict(order=reversed_labels))
    with pytest.raises(ValueError, match="not counterbalanced"):
        evalplan.blind_order(work, "date_only", 1, pair)


def test_eval_plan_unknown_cost_retains_full_reservation(work):
    config = {"provider_id": "fake", "model": "cheap"}
    gateway = SimpleNamespace(price_catalog=None, get_configuration=lambda: config)
    prices = {"models": [dict(provider="fake", model="cheap", verified=True, currency="CNY",
                               input_cache_miss_per_mtok=2, output_per_mtok=8)]}
    budget = evalplan.BudgetGateway(gateway, work, {}, prices)
    budget.calls = {"a": dict(purpose="a", reservation="0.9"), "b": dict(purpose="b", reservation="0.5")}
    summary = dict(known_amounts={"CNY": "0.1"}, groups=[dict(key="a", amount_complete=True),
                                                       dict(key="b", amount_complete=False)])
    assert budget.liability(summary) == Decimal("0.6")


def test_eval_plan_analysis_detects_position_bias_and_missing_repeats():
    from summarize_plan_parameter_eval import build_analysis
    judges = [dict(id="level-r1-forward", selected="advanced-r1", verdict={"choice": "A"}),
              dict(id="level-r1-reverse", selected="base-r1", verdict={"choice": "A"})]
    comparison = build_analysis({}, judges)[0]
    assert comparison["reverse_consistent"] == [False, False]
    assert comparison["blind_A"] == 2 and comparison["blind_B"] == 0
    assert not comparison["trials_complete"] and comparison["numeric"] == {}
    judges[1].update(selected="advanced-r1", verdict={"choice": "B"})
    assert build_analysis({}, judges)[0]["reverse_consistent"] == [True, False]


def test_eval_plan_analysis_does_not_mistake_uncreated_points_for_zero():
    from summarize_plan_parameter_eval import build_analysis
    runs = {f"{name}-r{repeat}": dict(outline_complete=True, formal_complete=False,
             metrics=dict(module_count=3, ranges={"total": [100, 200]}, chapter_coverage=["one"]))
            for name in ("base", "advanced") for repeat in (1, 2)}
    result = build_analysis(runs, [])[0]
    assert result["trials_complete"]
    assert "point_count" not in result["numeric"]
    assert not result["numeric"]["module_count"]["exceeds_repeat_variation"]


def test_eval_plan_report_reconciles_retries_and_unknown_costs():
    from summarize_plan_parameter_eval import cost_breakdown
    calls = [dict(run_id="base-r1", purpose="ok", reservation="0.8"),
             dict(run_id="base-r1", purpose="failed", reservation="0.6"),
             dict(run_id="judge", purpose="pending", reservation="0.2")]
    summary = dict(groups=[dict(key="ok", known_amounts={"CNY": "0.1"}, amount_complete=True),
                           dict(key="failed", known_amounts={}, amount_complete=False)])
    rows = cost_breakdown(calls, summary)
    assert rows == [["base-r1", 2, Decimal("0.1"), 1, Decimal("0.6"), Decimal("0.7")],
                    ["judge", 1, Decimal("0"), 1, Decimal("0.2"), Decimal("0.2")]]


def test_eval_plan_outline_blind_uses_equal_scope_even_with_partial_formal_results(work, monkeypatch):
    exp = evalplan.Experiment(work / "isolated", outline_blind=True)
    monkeypatch.setattr(evalplan, "COMPARISONS", evalplan.COMPARISONS[:1])
    calls = []

    def complete(purpose, messages, **kwargs):
        calls.append(messages[-1]["content"])
        return SimpleNamespace(text=json.dumps(dict(choice="A", reason="仅核对接线的替身判定")))

    exp.budget = SimpleNamespace(complete=complete)
    for name, ready in (("base", False), ("advanced", True)):
        record = dict(run_id=f"{name}-r1", outline_complete=True, formal_complete=ready,
                      draft=dict(outline=dict(stages=[dict(modules=[
                          dict(id="m1", title=name, summary="轮廓内容", minutes=[20, 40])])])),
                      metrics=dict(points=[dict(module_id="m1", title="只存在于正式计划的内容",
                                                summary="不可混入轮廓盲评", minutes=5)]))
        evalplan.write_json(exp.root / "runs" / f"{name}-r1" / "result.json", record)
    exp.judge()
    records = [evalplan.read_json(p) for p in sorted((exp.root / "outline-judges").glob("*.json"))]
    assert len(calls) == len(records) == 2
    assert all(r["scope"] == "outline" for r in records)
    assert records[0]["order"] == list(reversed(records[1]["order"]))
    assert all('"points": []' in prompt and "不可混入" not in prompt for prompt in calls)
    assert not (exp.root / "judges").exists()
    assert evalplan.read_json(exp.root / "runs/base-r1/result.json")["metrics"]["points"]
    exp.retry_failed = True
    exp.judge()
    assert len(calls) == 2  # Valid votes are immutable, including position-sensitive ones.


def test_eval_plan_concurrent_requests_reserve_budget_before_network(work, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    config = {"provider_id": "fake", "model": "cheap"}
    def call(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        raise RuntimeError("simulated failure without usage")
    gateway = SimpleNamespace(price_catalog=None, get_configuration=lambda: config, complete=call)
    prices = {"models": [dict(provider="fake", model="cheap", verified=True, currency="CNY",
                               input_cache_miss_per_mtok=2, output_per_mtok=8)]}
    budget = evalplan.BudgetGateway(gateway, work, {"prefix": "test"}, prices)
    monkeypatch.setattr(budget, "summary", lambda: dict(known_amounts={}, groups=[], calls=0, unpriced_calls=0))
    monkeypatch.setattr(evalplan, "STOP_AT", Decimal("0.06"))
    errors = []
    def first():
        try:
            budget.complete("a", [], run_id="r")
        except RuntimeError as exc:
            errors.append(type(exc))
    worker = threading.Thread(target=first)
    worker.start()
    try:
        assert entered.wait(3)
        with pytest.raises(evalplan.BudgetStop):
            budget.complete("b", [], run_id="r")
    finally:
        release.set()
        worker.join(3)
    assert errors == [RuntimeError]
    assert len(budget.calls) == 1 and budget.liability(budget.summary()) > 0


def test_eval_plan_snapshot_hash_mismatch_never_fetches(work, monkeypatch):
    import hashlib
    from refactor.learning.course import github_repo
    monkeypatch.setattr(github_repo, "default_fetch", lambda *args: pytest.fail("Cache mutation must stop"))
    url = evalplan.URLS[0]
    digest = hashlib.sha256(url.encode()).hexdigest()
    (work / (digest + ".body")).write_bytes(b"changed")
    evalplan.write_json(work / (digest + ".json"), dict(url=url, sha256="wrong"))
    with pytest.raises(RuntimeError, match="snapshot changed"):
        evalplan.SnapshotFetch(work)(url, threading.Event())


def test_eval_plan_resume_formal_uses_existing_outline_and_both_real_import_paths(work, monkeypatch):
    from refactor.learning.course import github_repo
    from refactor.learning.course.extract import extract_bytes
    from refactor.learning.course.research_pipeline import VerifiedSource
    exp = evalplan.Experiment(work / "isolated")
    sources = {}
    for index, url in enumerate(evalplan.URLS):
        doc = extract_bytes((f"# 教材{index}\n## 智能体基础\n" + "推理和行动构成智能体循环。" * 40).encode(), "book.md")
        sources[url] = VerifiedSource(url, url, f"教材{index}", "github.com", "whitelist", "repository", (),
                                      doc, "真实导入函数的替身输入", evalplan.now(), doc.sha256)
    exp.manifest["sources"] = [dict(url=u, sha256=s.document.sha256) for u, s in sources.items()]
    monkeypatch.setattr(github_repo, "import_repo", lambda url, **kw: sources[url])
    operations = []
    class ScriptedGateway:
        fail_outline = False
        fail_generation = False
        price_catalog = SimpleNamespace(estimate_cost=lambda *args, **kwargs: None)
        def snapshot(self, label):
            pass
        def get_configuration(self):
            return {"provider_id": "deepseek", "model": "fake"}
        def complete(self, purpose, messages, **kwargs):
            operations.append(purpose)
            text = messages[-1]["content"]
            if purpose == "coverage_check":
                result = dict(covered=True, missing=[], message="")
            elif purpose == "sufficiency":
                from refactor.learning.course.planner_agents import PREVIEW_SYSTEM
                result = (dict(summary='智能体基础',modules=[dict(title='智能体',summary='理解推理和行动')])
                    if kwargs.get('system') == PREVIEW_SYSTEM else dict(enough=True, questions=[], goal="智能体基础", focus=["基础"]))
            elif purpose == "outline":
                if self.fail_outline:
                    from refactor.learning.connections.errors import ModelError
                    self.fail_outline = False
                    raise ModelError("AGENT_FORMAT", "标明用途的失败替身")
                refs = re.findall(r"\[(s\d+\.\d+)\]", text)
                chunks=json.loads(text.split('【已取得的原文段落】\n')[1])
                result=dict(stages=[dict(title='基础',modules=[dict(key='m1',title='循环',summary='推理和行动',refs=refs,prerequisites=[],
                    points=[dict(title=title,summary='解释'+title,citations=[dict(chunk_id=c['chunk_id'],quote=c['text'][:80])])
                            for title,c in zip(('推理','行动'),(chunks[0],chunks[-1]))])])],message='学习基础')
            else:
                pytest.fail(purpose)
            return SimpleNamespace(text=json.dumps(result, ensure_ascii=False))
    exp.budget = ScriptedGateway()
    # Preparation is not the evaluator under test; exercise its durable resume
    # with stored new-bank rows instead of the removed AI generation response.
    from refactor.learning.app.plan_flow import PlanFlow
    from tests.refactor.integration38.support import seed_bank
    def prepare(store,pid,mid,ctx,progress=None):
        operations.append('point_materials')
        if exp.budget.fail_generation:
            exp.budget.fail_generation=False
            from refactor.learning.connections.errors import ModelError
            raise ModelError('AGENT_FORMAT','资料准备失败替身')
        seed_bank(store,pid)
    initialize=PlanFlow.__init__
    def initialize_fixture(self,*args,**kwargs):
        kwargs['prepare_module']=prepare
        initialize(self,*args,**kwargs)
    monkeypatch.setattr(PlanFlow,'__init__',initialize_fixture)
    monkeypatch.setattr(PlanFlow,'_module_ready',lambda self,pid,mid:any(q['module_id']==mid for q in self.services.store.records('question_bank',pid)))
    first = exp.run_one("base", 1)
    assert first["outline_complete"] and not first["formal_complete"]
    generated = exp.run_one("base", 1, generate=True)
    assert generated["formal_complete"] and generated["metrics"]["point_count"] == 2
    assert first["draft"]["plan_id"] == generated["draft"]["plan_id"]
    assert operations == ["sufficiency", "sufficiency", "coverage_check", "outline", "point_materials"]
    assert len(generated["metrics"]["chapter_coverage"]) == 2
    assert generated["metrics"]["point_minutes_sum"] == 30
    assert exp.run_one("base", 1, generate=True)["formal_complete"]
    assert len(operations) == 5
    exp.budget.fail_outline = True
    failed = exp.run_one("advanced", 1)
    assert failed["error_code"] == "AGENT_FORMAT"
    before = len(operations)
    assert exp.run_one("advanced", 1)["error_code"] == "AGENT_FORMAT"
    assert len(operations) == before  # failed operations are not silently replaced
    exp.retry_failed = True
    resumed = exp.run_one("advanced", 1)
    assert resumed["outline_complete"] and resumed["draft"]["plan_id"] == failed["draft"]["plan_id"]
    assert resumed["draft"]["form"] == failed["draft"]["form"]
    archived = list((exp.root / "runs/advanced-r1/failed-attempts").glob("*.json"))
    assert len(archived) == 1 and evalplan.read_json(archived[0])["error_code"] == "AGENT_FORMAT"
    exp.budget.fail_generation = True
    incomplete = exp.run_one("advanced", 1, generate=True)
    assert incomplete["draft"]["phase"] == "generating" and not incomplete["formal_complete"]
    complete = exp.run_one("advanced", 1, generate=True)
    assert complete["formal_complete"] and complete["draft"]["plan_id"] == incomplete["draft"]["plan_id"]
    archived = [evalplan.read_json(p)["error_code"] for p in (exp.root / "runs/advanced-r1/failed-attempts").glob("*.json")]
    assert sorted(archived) == ["AGENT_FORMAT", "AGENT_FORMAT"]  # New preparation errors retain their original code.

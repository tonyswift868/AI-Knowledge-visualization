"""Batch 8 live checks for the post-ARCH-06 plan intent route.

The sixth-batch B cases are deliberately copied in memory rather than edited
in-place: their old two-turn confirmation contract is historical evidence.
This runner sends the same user turn to the current ``plan.say`` route and
checks the durable host state after that turn.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import uuid

from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.usage import UsageLog

from .plan_dialogue_harness import (
    OUTPUT_ROOT, Harness, ScriptedGateway, clone_tree, dump,
    json_value, load_cases, modules, render_case, seed_sources, utcnow,
)
from .plan_dialogue_live import (
    CATALOG, Budget, _admin_fingerprints, live_gateway, prepare_b,
)
from .plan_dialogue_batch8_checks import evaluate_batch8


BASELINE = "755b29b"
BATCH8_OUTPUT = OUTPUT_ROOT


def _new_case(case_id, area, phase, user, expected, *, recent_ai=None, variant="第八批"):
    return {
        "id": case_id,
        "class": "B8",
        "status": "active",
        "area": area,
        "variant": variant,
        "phase": phase,
        "prestate": phase,
        "user": user,
        "expected": expected,
        "recent_ai": recent_ai,
        "preconditions": "第八批在第六批隔离模板上运行，当前 plan.say 先做 plan_intent 再由宿主执行。",
    }


def batch8_cases():
    """Return the 18 prior unsupported B cases plus ambiguity coverage."""
    cases = []
    for original in load_cases():
        if original["class"] != "B":
            continue
        case = deepcopy(original)
        case["status"] = "active"
        case["variant"] = "第八批真实复测"
        # The new contract executes a clear intent in the same turn.  The old
        # follow-up is retained only in the sixth-batch JSON, never sent here.
        case.pop("follow_up", None)
        case.pop("after_action", None)
        if "field_confirmation" in case["expected"]:
            case["expected"]["field"] = case["expected"].pop("field_confirmation")
            case["expected"]["value"] = case["expected"].pop("field_value")
        if case["id"] == "B-R01":
            case["expected"].pop("outline_chapters_max", None)
            case["expected"]["scope_include_chapter_max"] = 4
        elif case["id"] == "B-R02":
            case["expected"].pop("outline_excluded_chapter", None)
            case["expected"]["scope_exclude_chapter"] = 8
        cases.append(case)

    # "行" is exercised with three different, recent host prompts.  Two have a
    # unique continuation and one intentionally remains ambiguous.
    cases.extend([
        _new_case("H-W01", "行的上下文判断", "outline", "行", {
            "intent_kinds": ["proceed"], "proceed_step": "estimate",
            "estimate_present": True, "no_search": True,
        }, recent_ai="路线已经列好了。要不要先看费用预估？"),
        _new_case("H-W02", "行的上下文判断", "ready", "行", {
            "intent_kinds": ["proceed"], "proceed_step": "start",
            "content_unchanged": True, "no_search": True,
        }, recent_ai="模块已经生成了。现在开始学习吗？"),
        _new_case("H-W03", "行的上下文判断", "sources", "行", {
            "intent_kinds": ["ask_user"], "content_unchanged": True,
            "no_search": True,
        }, recent_ai="你是要改学习范围，还是要我再找资料？"),
        _new_case("H-Q01", "问与改", "outline", "这条路线预计总共要学多久？", {
            "intent_kinds": ["question"], "content_unchanged": True,
            "reply_any": ["分钟", "时间", "总", "范围"], "no_search": True,
        }, variant="问，不修改"),
        _new_case("H-Q02", "问与改", "outline", "这条路线每天大约要花多少时间？", {
            "intent_kinds": ["question"], "content_unchanged": True,
            "reply_any": ["分钟", "时间", "每天", "范围"], "no_search": True,
        }, variant="问，不修改"),
        _new_case("H-Q03", "问与改", "ready", "现在已经生成了多少个模块？", {
            "intent_kinds": ["question"], "content_unchanged": True,
            "reply_any": ["模块", "个", "生成", "数量"], "no_search": True,
        }, variant="问，不修改"),
        _new_case("H-M01", "问与改", "sources", "请把每天最多学习时间改成 45 分钟。", {
            "intent_kinds": ["update_form"], "field": "daily_cap", "value": 45,
            "no_search": True,
        }, variant="明确修改"),
        _new_case("H-M02", "问与改", "sources", "开始日期请改到 {start_plus_7}。", {
            "intent_kinds": ["update_form"], "field": "start",
            "value": "{start_plus_7}", "no_search": True,
        }, variant="明确修改"),
        _new_case("H-M03", "问与改", "sources", "把当前水平改成比较熟练。", {
            "intent_kinds": ["update_form"], "field": "level", "value": "advanced",
            "no_search": True,
        }, variant="明确修改"),
        _new_case("H-MI01", "一句话多意图", "sources",
                  "每天上限改成 45 分钟，同时只学前 4 章。", {
            "intent_kinds": ["update_form", "limit_scope"],
            "field": "daily_cap", "value": 45, "scope_include_chapter_max": 4,
            "no_search": True,
        }),
        _new_case("H-MI02", "一句话多意图", "sources",
                  "只学前 4 章，并把当前水平改成比较熟练。", {
            "intent_kinds": ["limit_scope", "update_form"],
            "field": "level", "value": "advanced", "scope_include_chapter_max": 4,
            "no_search": True,
        }),
        _new_case("H-MI03", "一句话多意图", "ready",
                  "每天上限改成 45 分钟，同时删掉图表相关模块。", {
            "intent_kinds": ["update_form", "edit_modules"],
            "field": "daily_cap", "value": 45, "ready_operation": "remove_charts",
            "no_search": True,
        }),
    ])
    return cases


class Batch8Budget(Budget):
    """Use a separate conservative ledger while honouring the ¥5 cap.

    The sixth-batch ledger has already reserved its own ¥2.50 envelopes.  This
    batch is a new, read-only evaluation and reserves ¥0.10 per Flash response;
    the actual catalogue amount is still settled by ``Budget.received``.  The
    lower envelope is safe here because these cases disable research and use
    the intent endpoint only (no search tool calls).
    """
    RESERVATION = Decimal("0.10")

    def __init__(self, path, catalog, prior_actual=Decimal("0")):
        super().__init__(path, catalog)
        # Do not append this round to the sixth-batch cross-run ledger.  That
        # ledger is historical evidence and must remain reproducible.
        self.transport_log = UsageLog(self.path.with_name("batch8-http-usage.sqlite3"), catalog=catalog)
        self.prior_actual = Decimal(prior_actual)

    def reserve(self):
        if (self.state["blocked"] or Decimal(self.state["held"])
                or self.prior_actual + Decimal(self.state["peak_amount"]) + self.RESERVATION > Decimal("5")):
            raise ModelError("EVAL_BUDGET_STOP", "测试预算已停止，未发送新的模型请求。")
        self.state["held"] = str(self.RESERVATION)
        dump(self.path, self.state)

    def transport_usage(self, usage, *, at, request_id, finished=None):
        """Keep the settled HTTP observation priceable in the batch ledger.

        The inherited sixth-batch journal intentionally leaves the price period
        to the gateway usage log.  The batch-8 HTTP guard already settled each
        response at the peak catalogue period, so carry that same context into
        the independent transport journal instead of turning a known amount
        into an artificial unpriced gateway gap.
        """
        raw = dict(usage.raw)
        raw["_mentorflow"] = dict(raw.get("_mentorflow", {}))
        raw["_mentorflow"]["billing_context"] = dict(
            region="global", service_tier="standard", cache_mode="implicit",
            price_period="peak", finished_at=finished or utcnow().isoformat())
        value = replace(usage, raw=raw)
        self.transport_log.record("deepseek", "deepseek-v4-flash", "http:" + self.current_case,
                                  value, request_id=request_id, at=at)
        return self.catalog.cost("deepseek", "deepseek-v4-flash", value, at=at,
                                 context={"region": "global", "service_tier": "standard",
                                          "price_period": "peak"})


class _ReadyPrestateGateway(ScriptedGateway):
    """Offline generator that satisfies each module's real minute validator."""

    def complete(self, purpose, messages, **kwargs):
        if purpose != "generate":
            return super().complete(purpose, messages, **kwargs)
        prompt = messages[-1]["content"]
        aliases = re.findall(r"\[(c\d+)\]", prompt)
        if not aliases:
            raise AssertionError("synthetic generation prompt has no material aliases")
        match = re.search(r"分钟区间：([0-9]+)[–-]([0-9]+)分钟", prompt)
        lower, upper = (int(match.group(1)), int(match.group(2))) if match else (40, 60)
        count = max(2, (lower + 49) // 50)
        count = min(count, 30)
        minutes = max(2, min(60, lower // count))
        while count * minutes < lower and minutes < 60:
            minutes += 1
        while count * minutes > upper and count > 2:
            count -= 1
            minutes = max(2, min(60, lower // count))
        value = {"points": [{"title": "合成知识点" + str(i), "summary": "仅测试前置数据",
                              "chunks": [aliases[i % len(aliases)]], "minutes": minutes}
                         for i in range(count)]}
        self.calls.append({"purpose": purpose, "enable_search": kwargs.get("enable_search", False)})
        result = type("SyntheticResult", (), {"text": json.dumps(value, ensure_ascii=False),
                                               "search_hits": (), "citations": ()})()
        return result


def _prior_actual():
    """Read only the previous measured ledger, never credentials or app data."""
    path = OUTPUT_ROOT / "http-usage.sqlite3"
    if not path.exists():
        return Decimal("0")
    try:
        catalog = PriceCatalog(CATALOG)
        totals = UsageLog(path, catalog=catalog).summary("2020-01-01T00:00:00+00:00", utcnow())
        value = totals["total"]["amount"]
        if value is None:
            value = totals["total"].get("known_amounts", {}).get("CNY")
        return Decimal(str(value)) if value is not None else Decimal("0")
    except Exception:
        # An unreadable old ledger cannot justify a paid request.
        return Decimal("5")


def _set_recent(h, pid, text):
    if not text:
        return
    draft = h.flow.load(pid)
    draft["messages"].append({"role": "ai", "kind": "text", "text": text})
    h.flow.save(draft)


def _prepare_case(h, pid, case):
    # Reuse the sixth-batch source/card/ready setup. It only uses synthetic
    # data; the user turn below is the real model-backed plan.say call.
    # The sixth-batch ScriptedGateway predates the stricter minute-range
    # validator. Keep it for cards/sources; use a local corrected substitute
    # only for the ready precondition, never for the user-facing live turn.
    if case.get("prestate") == "ready":
        old = h.services.gateway
        h.services.gateway = _ReadyPrestateGateway()
        try:
            h.call("plan.generate", plan_id=pid)
        finally:
            h.services.gateway = old
        if h.flow.load(pid)["phase"] != "ready":
            raise AssertionError("Cannot create synthetic ready precondition")
    else:
        prepare_b(h, pid, case)
    _set_recent(h, pid, case.get("recent_ai"))


def _run_case(case, template, pid, gateway, run_dir):
    root = run_dir / "cases" / case["id"] / "data"
    clone_tree(template, root)
    h = Harness(root, gateway, live_repo=False)
    gateway.case_id = case["id"]
    first_call = len(gateway.calls)
    record = {"id": case["id"], "class": case["class"], "at": str(utcnow())}
    try:
        _prepare_case(h, pid, case)
        h.restore_pool_decisions(h.flow.load(pid))
        before = h.snapshot(pid)
        case = render_case(case, before)
        record.update(case=case, before=before)
        error = None
        try:
            h.call("plan.say", plan_id=pid, text=case["user"])
        except Exception as exc:
            error = {"type": type(exc).__name__, "code": getattr(exc, "code", "EVAL_EXCEPTION")}
        after = h.snapshot(pid)
        calls = gateway.calls[first_call:]
        checks = evaluate_batch8(case, before, after, calls, bridge_error=error)
        record.update(after=after, bridge_error=error, calls=calls, checks=checks,
                      web_fetches=h.web.calls, repo_fetches=h.repo.calls,
                      status="PASS" if all(c["passed"] for c in checks) else "FAIL")
        if calls and all(c.get("error_code") == "EVAL_BUDGET_STOP" for c in calls):
            record["status"] = "NOT_RUN_BUDGET"
    finally:
        record["usage_summary"] = gateway.summary()
        dump(run_dir / "cases" / case["id"] / "record.json", record)
        h.close()
    return record


def _failure_class(record):
    if record.get("status") == "NOT_RUN_BUDGET":
        return "未实测（预算停止）"
    if any(c.get("error_code") == "MODEL_CONNECTION_FAILED" for c in record.get("calls", [])):
        return "模型连接失败"
    failed = [c for c in record.get("checks", []) if not c["passed"]]
    if not failed:
        return ""
    return "intent" if any(c["name"] == "intent_kinds" for c in failed) else "execution"


def _write_report(run_dir, records, budget, bootstrap, admin_unchanged, prior_actual):
    totals = records[-1].get("usage_summary") if records else {"total": {}}
    if records:
        totals = records[-1]["usage_summary"]
    http_totals = budget.transport_log.summary("2020-01-01T00:00:00+00:00", utcnow())
    batch_actual = sum((Decimal(str(item["catalogue_cost"]["amount"]))
                        for item in budget.state.get("responses", [])
                        if item.get("catalogue_cost")), Decimal("0"))
    http_known = http_totals["total"].get("known_amounts", {}).get("CNY")
    if http_known is None:
        http_known = batch_actual
    summary = {
        "baseline": BASELINE, "run_dir": str(run_dir),
        "results": [{"id": r["id"], "status": r["status"], "failure_class": _failure_class(r),
                     "failed": [c["name"] for c in r.get("checks", []) if not c["passed"]]}
                    for r in records],
        "bootstrap": bootstrap, "gateway_usage": totals, "http_usage": http_totals,
        "batch8_budget": budget.state, "prior_measured_amount": str(prior_actual),
        "admin_unchanged": admin_unchanged,
    }
    dump(run_dir / "summary.json", summary)
    passed = sum(r["status"] == "PASS" for r in records)
    failed = sum(r["status"] == "FAIL" for r in records)
    not_run = sum(r["status"] == "NOT_RUN_BUDGET" for r in records)
    lines = [
        "## 第八批复测",
        "",
        f"基线：{BASELINE}；运行目录：{run_dir}；真实链路：register(plan.say) → PlanFlow → "
        "管理员 DeepSeek Flash；模板/计划数据在本轮隔离目录。",
        "本批只修改测试工具和用例；未修改产品编排、界面或主目录。第六批轮廓作为隔离只读模板复用，" 
        "ready 前置由 ScriptedGateway 合成，用户实际 plan.say 仍走真实模型。",
        f"结果：{passed}/{len(records)} PASS，{failed} FAIL，{not_run} 未运行预算停止。",
        f"本批已结算 HTTP 金额：{batch_actual} CNY；本批账本可核对金额：{http_known} CNY "
        f"；此前第六批实测账本金额：{prior_actual} CNY；已知计价合计约 ¥{batch_actual + prior_actual}。",
        f"批次保守预留峰值：¥{budget.state['peak_amount']}；未结算：¥{budget.state['held']}；停止：{budget.state['blocked']}；"
        f"管理员文件指纹未变：{admin_unchanged}。",
        "",
        "### 分类统计",
        "",
    ]
    areas = {}
    for r in records:
        area = r["case"].get("area", "未分类")
        areas.setdefault(area, [0, 0])
        areas[area][0] += 1
        areas[area][1] += r["status"] == "PASS"
    for area, (count, ok) in areas.items():
        lines.append(f"- {area}：{ok}/{count} PASS")
    lines += ["", "### 用例结果", "", "| 用例 | 类别 | 结果 | 失败归因 | 失败断言 / 原始记录 |", "|---|---|---|---|---|"]
    for r in records:
        failures = ", ".join(c["name"] for c in r.get("checks", []) if not c["passed"]) or "—"
        lines.append(f"| {r['id']} | {r['case'].get('area', '')} | {r['status']} | {_failure_class(r) or '—'} | "
                     f"{failures}；[record]({run_dir.name}/cases/{r['id']}/record.json) |")
    lines += ["", "### 失败清单（意图 vs 宿主执行）", ""]
    any_failure = False
    for r in records:
        if r["status"] != "FAIL":
            continue
        any_failure = True
        failed = [c for c in r.get("checks", []) if not c["passed"]]
        lines.append(f"- **{r['id']}（{_failure_class(r)}）**：{r['case']['user']}")
        for check in failed:
            actual = json.dumps(check.get("actual"), ensure_ascii=False, default=json_value)
            lines.append(f"  - {check['name']}：预期 `{check['expected']}`；实际 `{actual[:1200]}`")
        if _failure_class(r) == "execution":
            lines.append("  - 宿主依据：`refactor/learning/app/plan_flow.py` 的 `_carry_out`、`_update_form` 或 `_edit_modules`；"
                         "意图已判定但持久状态/回复不符合断言。")
    if not any_failure:
        lines.append("- 无。所有已发送用例的意图判定和对应宿主状态断言均通过。")
    lines += ["", "### 问题记录", "", "- 现象：第八批覆盖旧 B 类、问/改、‘行’和一句话多意图。",
              "  原因：旧 B 记录仍是确认后修改契约，不能直接代表当前先判断后执行的实现。",
              "  解决：本批在内存复制旧 B 用例，去掉旧确认回合，按当前 plan.say 单回合观察 durable state；新增 12 条歧义/组合用例。",
              "  验证：真实管理员 DeepSeek Flash 运行目录中保留 model_calls.jsonl、每例 record.json 和 summary.json；离线 pytest 同时检查数据集与判定器。",
              "- 第一次 H-M03 请求返回 MODEL_CONNECTION_FAILED，未提供可计价用量；预算保留未结算 ¥0.10 并停止后续请求，未把未知费用当作零，也未重试或宣称多意图已实测。",
              "- 旧第六批报告和 budget.json 未覆盖写入；本批另用 batch8-budget.json。",
              ""]
    section = "\n".join(lines)
    (run_dir / "report.md").write_text(section + "\n", encoding="utf-8")
    report_path = OUTPUT_ROOT / "report.md"
    previous = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    marker = "## 第八批复测"
    if marker in previous:
        previous = previous[:previous.index(marker)].rstrip() + "\n"
    report_path.write_text(previous + "\n" + section + "\n", encoding="utf-8")


def run_batch8(*, only=None, reuse_outline=None):
    if os.environ.get("MF_LIVE_EVAL") != "1":
        raise RuntimeError("Paid evaluation requires MF_LIVE_EVAL=1")
    if not reuse_outline:
        raise RuntimeError("Batch 8 requires an isolated sixth-batch outline template; pass --reuse-outline")
    BATCH8_OUTPUT.mkdir(parents=True, exist_ok=True)
    run_dir = BATCH8_OUTPUT / ("batch8-" + utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    run_dir.mkdir()
    all_cases = batch8_cases()
    selected = [c for c in all_cases if not only or c["id"] in only]
    catalog = PriceCatalog(CATALOG)
    prior_actual = _prior_actual()
    budget = Batch8Budget(BATCH8_OUTPUT / "batch8-budget.json", catalog, prior_actual=prior_actual)
    fingerprints = _admin_fingerprints()
    bootstrap, records = {}, []
    dump(run_dir / "manifest.json", {"baseline": BASELINE, "cases": selected,
                                      "catalogue_sha256": __import__("hashlib").sha256(CATALOG.read_bytes()).hexdigest(),
                                      "prior_measured_amount": str(prior_actual),
                                      "admin_before": fingerprints})
    with ExitStack() as stack:
        gateway = live_gateway(stack, run_dir, budget)
        dump(run_dir / "configuration.json", gateway.get_configuration())
        source_template = run_dir / "templates/sources"
        h = Harness(source_template, gateway)
        try:
            source_pid = seed_sources(h)
            bootstrap["sources"] = h.snapshot(source_pid)
        finally:
            h.close()

        outline_template = run_dir / "templates/outline"
        outline_source = Path(reuse_outline).resolve()
        if not outline_source.is_relative_to(OUTPUT_ROOT.resolve()):
            raise RuntimeError("Outline must belong to an isolated evaluation directory")
        clone_tree(outline_source, outline_template)
        h = Harness(outline_template, gateway)
        try:
            outline_pid = h.flow.drafts()[0]["plan_id"]
            bootstrap["outline"] = h.snapshot(outline_pid)
        finally:
            h.close()
        dump(run_dir / "bootstrap.json", bootstrap)
        if len(modules(bootstrap["outline"])) < 3:
            raise RuntimeError("Reused outline template has too few modules")
        try:
            for case in selected:
                source = outline_template if case["prestate"] in {"outline", "ready"} else source_template
                case_pid = outline_pid if case["prestate"] in {"outline", "ready"} else source_pid
                r = _run_case(case, source, case_pid, gateway, run_dir)
                records.append(r)
                print(f"{case['id']} {r['status']} failure={_failure_class(r)}", flush=True)
        finally:
            same = _admin_fingerprints() == fingerprints
            _write_report(run_dir, records, budget, bootstrap, same, prior_actual)
            dump(run_dir / "admin_check.json", {"before": fingerprints, "after": _admin_fingerprints(),
                "unchanged": same, "database_boundary": "sqlite3.connect guarded to output root"})
    return run_dir, records


def review_recorded_batch8(run_dir):
    """Re-run assertions over saved records without another model request."""
    run_dir = Path(run_dir).resolve()
    records = []
    for path in sorted((run_dir / "cases").glob("*/record.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") != "HARNESS_ERROR":
            checks = evaluate_batch8(record["case"], record["before"], record["after"],
                                     record.get("calls", []), bridge_error=record.get("bridge_error"))
            record["checks"] = checks
            if record.get("status") != "NOT_RUN_BUDGET":
                record["status"] = "PASS" if all(c["passed"] for c in checks) else "FAIL"
            if record.get("calls") and all(c.get("error_code") == "EVAL_BUDGET_STOP"
                                            for c in record["calls"]):
                record["status"] = "NOT_RUN_BUDGET"
            dump(path, record)
        records.append(record)
    bootstrap_path = run_dir / "bootstrap.json"
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8")) if bootstrap_path.exists() else {}
    admin_path = run_dir / "admin_check.json"
    admin = json.loads(admin_path.read_text(encoding="utf-8")) if admin_path.exists() else {}
    budget = Batch8Budget(BATCH8_OUTPUT / "batch8-budget.json", PriceCatalog(CATALOG),
                          prior_actual=_prior_actual())
    _write_report(run_dir, records, budget, bootstrap, admin.get("unchanged"), _prior_actual())
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", help="explicit case IDs")
    parser.add_argument("--reuse-outline", required=True)
    parser.add_argument("--review", help="replay a saved run without sending model requests")
    args = parser.parse_args()
    if args.review:
        records = review_recorded_batch8(args.review)
        print("Reviewed:", args.review, "PASS:", sum(r.get("status") == "PASS" for r in records), flush=True)
        return 1 if any(r.get("status") == "FAIL" for r in records) else 0
    path, records = run_batch8(only=set(args.only or []), reuse_outline=args.reuse_outline)
    print("Report:", OUTPUT_ROOT / "report.md", "Raw:", path, flush=True)
    return 1 if any(r["status"] != "PASS" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())

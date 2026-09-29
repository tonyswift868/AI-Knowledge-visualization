"""Opt-in paid PlanFlow evaluation. Run as a module; see plan_dialogue_README.md."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid
from unittest.mock import patch

from refactor.learning.connections import gateway as gateway_module
from refactor.learning.connections.credentials import AccountCredentials
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.providers import _http, anthropic_messages
from refactor.learning.connections.providers import Usage
from refactor.learning.connections.usage import UsageLog
from refactor.learning.preferences.storage import AccountJSON

from .plan_dialogue_harness import (
    ADMIN, ADMIN_ROOT, HERE, OUTPUT_ROOT, Harness, ScriptedGateway, clone_tree, dump,
    json_value, load_cases, modules, question_prestate, render_case, seed_sources, utcnow,
)
from .plan_dialogue_checks import evaluate

REPO = HERE.parents[2]
CATALOG = REPO / "refactor/learning/course/data/model-prices.json"


class Budget:
    """Persistent budget across invocations, also guarding provider continuations.

    Reserve CNY 2.50 before each Flash HTTP response (over 1M uncached input +
    8192 output at this baseline's peak price). This is a conservative request
    envelope, not a vendor spending setting. Stop on unknown usage or price,
    after CNY 2.50 observed peak cost, or any interrupted in-flight reservation.
    Ordinary measured catalogue spend must also stay below CNY 5.
    """
    def __init__(self, path, catalog):
        self.path, self.catalog = Path(path), catalog
        self.state = (json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else
                      {"peak_amount": "0", "held": "0", "blocked": False, "responses": []})
        if Decimal(self.state["held"]):
            self.state["blocked"] = True
        self.current_case = "setup"
        self.call_id = ""
        self.transport_log = UsageLog(self.path.with_name("http-usage.sqlite3"), catalog=catalog)

    def transport_usage(self, usage, *, at, request_id, finished=None):
        raw = dict(usage.raw)
        raw['_mentorflow'] = dict(raw.get('_mentorflow', {}))
        raw['_mentorflow']['billing_context'] = dict(region='global', service_tier='standard',
            cache_mode='implicit', finished_at=finished or utcnow().isoformat())
        value = replace(usage, raw=raw)
        self.transport_log.record('deepseek', 'deepseek-v4-flash', 'http:' + self.current_case,
                                  value, request_id=request_id, at=at)
        return self.catalog.cost('deepseek', 'deepseek-v4-flash', value, at=at)

    def resume_metered_failure(self):
        """Only a missing gateway meter with *all* HTTP responses settled.

        Never releases an unknown/in-flight reservation or resets expenditure.
        Legacy first-run response bodies were not retained on adapter errors;
        their saved per-response usage remains sufficient to account for money.
        """
        if self.state.get('reason') != 'gateway_usage_incomplete' or Decimal(self.state['held']) or not self.state['responses']:
            raise RuntimeError('Cannot reconcile an unmetered or interrupted request')
        for i, r in enumerate(self.state['responses']):
            self.current_case = r['case']
            cost = self.transport_usage(Usage(**r['usage']), at=r['at'], request_id='backfill-' + str(i))
            if cost is None or cost.currency != 'CNY':
                raise RuntimeError('HTTP usage cannot be priced; do not resume')
        self.state.setdefault('reconciliations', []).append({'at': str(utcnow()), 'reason': self.state['reason'],
            'responses': len(self.state['responses']), 'peak_amount_unchanged': self.state['peak_amount']})
        self.state.update(blocked=False, reason='gateway_gap_reconciled_from_saved_http_usage')
        dump(self.path, self.state)

    def reserve(self):
        if self.state["blocked"] or Decimal(self.state["held"]) or Decimal(self.state["peak_amount"]) + Decimal("2.50") > Decimal("5"):
            raise ModelError("EVAL_BUDGET_STOP", "测试预算已停止，未发送新的模型请求。")
        self.state["held"] = "2.50"
        dump(self.path, self.state)

    def received(self, data, *, model, searching, started):
        response_id = uuid.uuid4().hex
        # Capture before the production adapter can reject max_uses_exceeded,
        # malformed output or a pause_turn. No request headers/key are logged.
        raw_path = self.path.parent / 'http-responses' / (response_id + '.json')
        dump(raw_path, data)
        result = anthropic_messages.parse(data, "deepseek", model, searching)
        cost = self.catalog.cost("deepseek", model, result.usage, at=started,
                                context={"region": "global", "service_tier": "standard", "price_period": "peak"})
        if cost is None or cost.currency != "CNY" or cost.amount > Decimal(self.state["held"]):
            self.unknown("usage_or_price_unresolved")
            raise ModelError("EVAL_BUDGET_STOP", "本次费用无法完整核对，停止后续模型请求。")
        actual = self.transport_usage(result.usage, at=started, request_id=response_id)
        self.state["responses"].append({"case": self.current_case, "at": started.isoformat(), "call_id": self.call_id,
                                       "response_path": str(raw_path), "catalogue_cost": asdict(actual) if actual else None,
                                       "peak_amount": str(cost.amount), "usage": asdict(result.usage)})
        self.state["peak_amount"] = str(Decimal(self.state["peak_amount"]) + cost.amount)
        self.state["held"] = "0"
        dump(self.path, self.state)

    def unknown(self, code):
        self.state.update(blocked=True, reason=code)
        dump(self.path, self.state)


class RecordingGateway:
    def __init__(self, gateway, budget, run_dir):
        self.inner, self.budget, self.run_dir = gateway, budget, Path(run_dir)
        self.price_catalog = gateway.price_catalog
        self.web = None
        self.calls = []
        self.case_id = "BOOTSTRAP"
        self.started = utcnow() - timedelta(seconds=1)

    def get_configuration(self):
        return self.inner.get_configuration()

    def summary(self):
        return self.inner.usage_log.summary(self.started, utcnow() + timedelta(seconds=1))

    def complete(self, purpose, messages, **kwargs):
        self.budget.current_case = self.case_id
        self.budget.call_id = uuid.uuid4().hex
        first_response = len(self.budget.state['responses'])
        entry = {"case": self.case_id, "purpose": purpose, "enable_search": kwargs.get("enable_search", False),
                 "system": kwargs.get("system"), "messages": messages, "at": utcnow().isoformat(),
                 "max_tokens": kwargs.get("max_tokens", 4096)}
        self.calls.append(entry)
        try:
            if self.budget.state["blocked"]:
                raise ModelError("EVAL_BUDGET_STOP", "预算或未知费用已阻止后续模型调用。")
            result = self.inner.complete("dialogue:" + self.case_id + ":" + purpose, messages, **kwargs)
            entry["result"] = asdict(result)
            if purpose == "research" and self.web is not None:
                self.web.register_result(result)
            return result
        except ModelError as exc:
            entry["error_code"] = exc.code  # never persist provider exception text / credentials
            raise
        finally:
            entry["finished_at"] = utcnow().isoformat()
            entry["usage_summary"] = self.summary()
            total = entry["usage_summary"]["total"]
            observed = self.budget.state['responses'][first_response:]
            entry['http_responses'] = observed
            if total["unpriced_calls"] or total["in_progress"]:
                if observed and not Decimal(self.budget.state['held']) and all(r.get('catalogue_cost') for r in observed):
                    entry['gateway_meter_gap'] = 'HTTP responses fully metered; gateway journal remains incomplete'
                elif entry.get('error_code') != 'EVAL_BUDGET_STOP':
                    self.budget.unknown("gateway_usage_incomplete")
            if Decimal(str(total["known_amounts"].get("CNY", 0))) >= Decimal("4.50"):
                self.budget.unknown("catalogue_cost_near_limit")
            with (self.run_dir / "model_calls.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, default=json_value) + "\n")
                stream.flush()


def _deny_credential_write(*_args, **_kwargs):
    raise AssertionError("Evaluation cannot write Windows credentials")


def _admin_fingerprints():
    root = ADMIN_ROOT / "learning/accounts" / ADMIN
    result = {}
    for name in ("connections.json", "model-gateway.json", "model-usage.sqlite3",
                 "model-usage.sqlite3-wal", "model-usage.sqlite3-shm", "course.sqlite3"):
        path = root / name
        if not path.exists():
            result[name] = None
        elif name.endswith(".json"):
            result[name] = {"size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            result[name] = {"size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
    return result


def live_gateway(stack, run_dir, budget):
    """Read administrator configuration/key; redirect usage before construction."""
    if not (ADMIN_ROOT / "learning/accounts" / ADMIN / "model-gateway.json").is_file():
        raise RuntimeError("Administrator preset not present; migration is forbidden in this evaluation")
    real_write = AccountJSON.write
    def isolated_write(storage, value):
        if not storage.path.resolve().is_relative_to(OUTPUT_ROOT.resolve()):
            raise AssertionError("Evaluation attempted a JSON write outside its output root")
        return real_write(storage, value)
    stack.enter_context(patch.object(AccountJSON, "write", isolated_write))
    real_connect = sqlite3.connect
    def isolated_sqlite(database, *args, **kwargs):
        if not Path(database).resolve().is_relative_to(OUTPUT_ROOT.resolve()):
            raise AssertionError('Evaluation attempted to open a database outside its output root')
        return real_connect(database, *args, **kwargs)
    stack.enter_context(patch.object(sqlite3, 'connect', isolated_sqlite))
    for method in ("put", "delete", "clear"):
        stack.enter_context(patch.object(AccountCredentials, method, _deny_credential_write))
    stack.enter_context(patch.object(gateway_module, "UsageLog",
                                    lambda _path, **kwargs: UsageLog(run_dir / "usage.sqlite3", **kwargs)))
    gateway = gateway_module.ModelGateway(ADMIN, ADMIN_ROOT, price_catalog=budget.catalog)
    stack.callback(gateway.close)
    config = gateway.get_configuration()
    if (config.get("provider_id") != "deepseek" or config.get("model") not in {"deepseek-v4-flash", "deepseek-flash"}
            or config.get("base_url", "").rstrip("/") not in {"https://api.deepseek.com", "https://api.deepseek.com/v1",
                                                            "https://api.deepseek.com/anthropic"}):
        raise RuntimeError("Budget is approved only for the administrator's official DeepSeek Flash preset")

    original_json = _http.HTTP.json
    async def guarded_json(http, url, body, headers=None):
        if http.provider != "deepseek" or body["model"] != config["model"]:
            raise ModelError("EVAL_BUDGET_STOP", "测试只允许当前 DeepSeek Flash。")
        budget.reserve()
        started = utcnow()
        try:
            data = await original_json(http, url, body, headers)
            budget.received(data, model=body["model"], searching=bool(body.get("tools")), started=started)
            return data
        except BaseException as exc:
            budget.unknown(getattr(exc, "code", type(exc).__name__))
            raise
    stack.enter_context(patch.object(_http.HTTP, "json", guarded_json))
    return RecordingGateway(gateway, budget, run_dir)


def prepare_b(h, pid, case):
    """Only called after the *second* explicit future-B opt-in.

    Additional saved card/ready states are synthetic; the user turn still uses
    the actual live gateway and production plan.say.
    """
    if case["id"] in {"B-C01", "B-C02", "B-R03"}:
        draft = h.flow.load(pid)
        # Import another real local source through the offline URL transport.
        h.web.pages["https://docs.python.org/3/library/pathlib.html"] = ("CSDN 对照笔记（合成卡片）", "")
        h.call("plan.say", plan_id=pid, text="https://docs.python.org/3/library/pathlib.html")
        draft = h.flow.load(pid)
        draft["sources"][-1]["title"] = "CSDN 对照笔记（合成卡片）"
        if case["id"] == "B-C02":
            draft["sources"][1], draft["sources"][-1] = draft["sources"][-1], draft["sources"][1]
            draft["sources"][1]["status"] = "new"
            h.services.store.set_source_adopted(pid, draft["sources"][1]["source_id"], False)
        h.flow.save(draft)
    if case["prestate"] == "ready":
        # Prepare real durable modules/points with an explicit, unpaid double.
        old = h.services.gateway
        h.services.gateway = ScriptedGateway()
        try:
            h.call("plan.generate", plan_id=pid)
        finally:
            h.services.gateway = old
        if h.flow.load(pid)["phase"] != "ready":
            raise AssertionError("Cannot create synthetic ready precondition")


def run_case(case, template, pid, gateway, run_dir, *, live_repo):
    root = run_dir / "cases" / case["id"] / "data"
    clone_tree(template, root)
    h = Harness(root, gateway, live_repo=live_repo and case["id"] in {"A-U04", "A-U05", "A-U06"})
    gateway.case_id = case["id"]
    first_call = len(gateway.calls)
    record = {"id": case["id"], "class": case["class"], "at": str(utcnow())}
    try:
        if case["prestate"] == "questions":
            question_prestate(h, pid, case)
        if case["class"] == "B":
            prepare_b(h, pid, case)
        h.restore_pool_decisions(h.flow.load(pid))
        before = h.snapshot(pid)
        case = render_case(case, before)
        record.update(case=case, before=before)
        error, intermediate = None, None
        try:
            h.call("plan.say", plan_id=pid, text=case["user"])
            if case.get("follow_up"):
                intermediate = h.snapshot(pid)
                h.call("plan.say", plan_id=pid, text=case["follow_up"])
            if case.get("after_action"):
                h.call(case["after_action"], plan_id=pid)
        except Exception as exc:
            error = {"type": type(exc).__name__, "code": getattr(exc, "code", "EVAL_EXCEPTION")}
        after = h.snapshot(pid)
        calls = gateway.calls[first_call:]
        checks = evaluate(case, before, after, calls, h.repo.calls, intermediate=intermediate, bridge_error=error)
        record.update(after=after, intermediate=intermediate, bridge_error=error, calls=calls, checks=checks,
                      web_fetches=h.web.calls, repo_fetches=h.repo.calls,
                      status="PASS" if all(c["passed"] for c in checks) else "FAIL")
        if calls and all(c.get('error_code') == 'EVAL_BUDGET_STOP' for c in calls):
            record['status'] = 'NOT_RUN_BUDGET'
    except Exception as exc:
        record.update(status="HARNESS_ERROR", error_type=type(exc).__name__)
        # A harness failure is not a product failure; persist and surface it.
        raise
    finally:
        record["usage_summary"] = gateway.summary()
        dump(run_dir / "cases" / case["id"] / "record.json", record)
        h.close()
    return record


def report(run_dir, records, pending, bootstrap, gateway, budget, *, admin_unchanged=None):
    totals = gateway.summary()
    http_totals = budget.transport_log.summary('2026-09-01T00:00:00+00:00', utcnow() + timedelta(seconds=1))
    dump(run_dir / "summary.json", {"run_dir": str(run_dir), "results": [
        {"id": r["id"], "status": r["status"], "failed": [c["name"] for c in r.get("checks", []) if not c["passed"]]} for r in records],
        "pending": pending, "bootstrap": bootstrap, "usage": totals, "http_usage": http_totals,
        "budget": budget.state, "admin_unchanged": admin_unchanged})
    lines = [
        "# 新建计划对话实测",
        "", f"基线：ae94765；运行目录：{run_dir}；更新：{utcnow().isoformat()}",
        "", "真实链路：生产 register(plan.say) → PlanFlow → 管理员 ModelGateway → DeepSeek Flash。",
        "测试计划/草稿/SQLite 用量均在本次新目录；管理员配置与凭据只读。共享前置轮廓只生成一次，后续复制已关闭的数据库。",
        "追问、资料阶段前置状态为明确合成数据；轮廓为一次真实生成。普通网页正文为离线合成，不能据此声称真实网页可读或推荐质量已验证；GitHub 仓库用例走真实网络。",
        "下面 PASS 表示列出的可观察断言通过，语义完整性仍需结合原始回复人工复核。B 类默认 pending，不付费证明已知缺失。",
        "", f"费用（gateway.usage_log.summary）：{totals['total']['amount']} {totals['total']['currency']}；"
            f"调用 {totals['total']['calls']}；未定价 {totals['total']['unpriced_calls']}。",
        "金额是厂商返回的实际用量按当前目录价计算，不是厂商账单；未返回/未定价费用不记作零。",
        f"另存 HTTP 观测账本（跨运行，含网关漏记）：{http_totals['total']['amount']} {http_totals['total']['currency']}；"
            f"响应 {http_totals['total']['calls']}；未定价 {http_totals['total']['unpriced_calls']}。两账本不能相加。",
        f"跨运行峰值价格保守累计：¥{budget.state['peak_amount']}；未结算预留：¥{budget.state['held']}；停止标志：{budget.state['blocked']}。",
        f"管理员文件前后指纹相同：{admin_unchanged}。",
        "", "| 用例 | 结果 | 失败断言 / 原始记录 |", "|---|---|---|"
    ]
    for r in records:
        failures = ", ".join(c["name"] for c in r.get("checks", []) if not c["passed"]) or "断言均通过"
        lines.append(f"| {r['id']} | {r['status']} | {failures}；[record](./{run_dir.name}/cases/{r['id']}/record.json) |")
    lines += [f"| {c} | pending | B 类尚未授权本轮实测 |" for c in pending]
    lines += ["", "## 失败表现（自动记录；人工判因见后续补充）", ""]
    for r in records:
        if r["status"] != "FAIL":
            continue
        lines.append(f"### {r['id']} — {r['case']['variant']}")
        lines.append(f"用户：{r['case']['user']}")
        for c in r["checks"]:
            if not c["passed"]:
                actual = json.dumps(c["actual"], ensure_ascii=False, default=json_value)
                lines.append(f"- {c['name']}：预期 {c['expected']}；实际 {actual[:1800]}")
    text = "\n".join(lines) + "\n"
    (OUTPUT_ROOT / "report.md").write_text(text, encoding="utf-8")
    (run_dir / "report.md").write_text(text, encoding="utf-8")


def run_live(*, include_b=False, only=None, resume_metered=False, reuse_outline=None):
    if os.environ.get("MF_LIVE_EVAL") != "1":
        raise RuntimeError("Paid evaluation requires MF_LIVE_EVAL=1")
    if include_b and os.environ.get("MF_LIVE_EVAL_B") != "1":
        raise RuntimeError("Future B requires MF_LIVE_EVAL_B=1 as well")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = OUTPUT_ROOT / ("run-" + utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    run_dir.mkdir()
    cases = load_cases()
    selected = [c for c in cases if (c["class"] == "A" or include_b) and (not only or c["id"] in only)]
    pending = [c["id"] for c in cases if c["class"] == "B" and not include_b]
    budget = Budget(OUTPUT_ROOT / "budget.json", PriceCatalog(CATALOG))
    if resume_metered:
        budget.resume_metered_failure()
    fingerprints = _admin_fingerprints()
    records, bootstrap = [], {}
    dump(run_dir / "manifest.json", {"baseline": "ae94765", "cases": selected, "pending": pending,
                                    "catalogue_sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
                                    "admin_before": fingerprints})
    with ExitStack() as stack:
        gateway = live_gateway(stack, run_dir, budget)
        dump(run_dir / "configuration.json", gateway.get_configuration())  # public fields only
        template = run_dir / "templates/sources"
        h = Harness(template, gateway)
        try:
            pid = seed_sources(h)
            bootstrap["sources"] = h.snapshot(pid)
        finally:
            h.close()
        outline_template = run_dir / "templates/outline"
        if any(c["prestate"] in {"outline", "ready"} for c in selected):
            if reuse_outline:
                source = Path(reuse_outline).resolve()
                if not source.is_relative_to(OUTPUT_ROOT.resolve()):
                    raise RuntimeError('Outline must belong to a previous isolated evaluation')
                clone_tree(source, outline_template)
            else:
                clone_tree(template, outline_template)
            h = Harness(outline_template, gateway)
            try:
                gateway.case_id = "BOOTSTRAP-OUTLINE"
                if reuse_outline:
                    # Reused templates retain their own plan ID. Source cases
                    # below continue to use the independently seeded source ID.
                    outline_pid = h.flow.drafts()[0]['plan_id']
                else:
                    outline_pid = pid
                    h.call("plan.outline", plan_id=pid)
                bootstrap["outline"] = h.snapshot(outline_pid)
                dump(run_dir / "bootstrap.json", bootstrap)
                if h.flow.load(outline_pid)["phase"] != "outline" or len(modules(bootstrap["outline"])) < 3:
                    raise RuntimeError("Real outline bootstrap failed; no synthetic replacement will be used")
            finally:
                h.close()
        try:
            for case in selected:
                source = outline_template if case["prestate"] in {"outline", "ready"} else template
                case_pid = outline_pid if case["prestate"] in {"outline", "ready"} else pid
                r = run_case(case, source, case_pid, gateway, run_dir, live_repo=True)
                records.append(r)
                report(run_dir, records, pending, bootstrap, gateway, budget)
                total = gateway.summary()["total"]
                print(f"{case['id']} {r['status']} calls={total['calls']} CNY={total['amount']}", flush=True)
        finally:
            same = _admin_fingerprints() == fingerprints
            report(run_dir, records, pending, bootstrap, gateway, budget, admin_unchanged=same)
            dump(run_dir / 'admin_check.json', {'before': fingerprints, 'after': _admin_fingerprints(),
                'unchanged': same, 'database_boundary': 'sqlite3.connect guarded to output root',
                'note': 'A concurrent application can update administrator files; fingerprints alone do not attribute the writer.'})
    return run_dir, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-b", action="store_true", help="future opt-in only, requires MF_LIVE_EVAL_B=1")
    parser.add_argument("--only", nargs="+", help="explicit case subset; shared budget still includes previous runs")
    parser.add_argument('--resume-metered', action='store_true', help='resume only fully metered HTTP responses missing from gateway journal')
    parser.add_argument('--reuse-outline', help='reuse an isolated previously generated outline template')
    args = parser.parse_args()
    path, records = run_live(include_b=args.include_b, only=set(args.only or []),
                             resume_metered=args.resume_metered, reuse_outline=args.reuse_outline)
    print("Report:", OUTPUT_ROOT / "report.md", "Raw:", path, flush=True)
    return 1 if any(r["status"] != "PASS" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())

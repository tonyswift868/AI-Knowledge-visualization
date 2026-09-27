"""Offline re-evaluation of preserved live records; performs no network/key I/O.

Use after correcting a test oracle. Original record.json files are immutable.
Only an identical rendered user utterance counts towards the current dataset;
superseded test utterances and budget-blocked attempts remain listed explicitly.
"""
from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.usage import UsageLog
from .plan_dialogue_checks import evaluate
from .plan_dialogue_harness import OUTPUT_ROOT, dump, load_cases, render_case, utcnow
from .plan_dialogue_live import CATALOG


def review(root=OUTPUT_ROOT):
    root = Path(root)
    manual = ''
    if (root / 'report.md').exists():
        previous = (root / 'report.md').read_text(encoding='utf-8')
        if '<!-- MANUAL_REVIEW -->' in previous:
            manual = '\n<!-- MANUAL_REVIEW -->' + previous.split('<!-- MANUAL_REVIEW -->', 1)[1]
    cases = {c["id"]: c for c in load_cases()}
    attempts = []
    for run in sorted(root.glob("run-*")):
        for path in sorted((run / "cases").glob("*/record.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if "before" not in record or "after" not in record:
                attempts.append({"id": record["id"], "status": "HARNESS_ERROR", "path": str(path)})
                continue
            current = render_case(cases[record["id"]], record["before"])
            if current["user"] != record["case"]["user"]:
                status, checks = "SUPERSEDED_UTTERANCE", []
            elif record["calls"] and all(c.get("error_code") == "EVAL_BUDGET_STOP" for c in record["calls"]):
                status, checks = "NOT_RUN_BUDGET", []
            else:
                checks = evaluate(current, record["before"], record["after"], record["calls"], record["repo_fetches"],
                                  intermediate=record.get("intermediate"), bridge_error=record.get("bridge_error"))
                status = "PASS" if all(c["passed"] for c in checks) else "FAIL"
            attempts.append({"id": record["id"], "status": status, "original_status": record["status"],
                             "path": str(path), "checks": checks, "user": record["case"]["user"],
                             "gateway_calls": len(record["calls"])})
    outcomes = []
    for case in cases.values():
        history = [a for a in attempts if a["id"] == case["id"]]
        valid = [a for a in history if a["status"] in {"PASS", "FAIL"}]
        states = {a["status"] for a in valid}
        status = ("pending" if case["class"] == "B" else "NOT_RUN" if not states else
                  "UNSTABLE" if len(states) > 1 else next(iter(states)))
        outcomes.append({"id": case["id"], "class": case["class"], "area": case["area"], "variant": case["variant"],
                         "status": status, "attempts": history})
    start, end = '2026-09-01T00:00:00+00:00', utcnow() + timedelta(seconds=1)
    http = UsageLog(root / "http-usage.sqlite3", catalog=PriceCatalog(CATALOG)).summary(start, end)
    gateway_totals = []
    for run in sorted(root.glob("run-*")):
        if (run / "usage.sqlite3").exists():
            gateway_totals.append({"run": run.name,
                "summary": UsageLog(run / "usage.sqlite3", catalog=PriceCatalog(CATALOG)).summary(start, end)})
    result = {"baseline": "ae94765", "reviewed_at": str(utcnow()), "outcomes": outcomes,
              "http_usage": http, "gateway_usage": gateway_totals,
              "counts": {s: sum(o["class"] == "A" and o["status"] == s for o in outcomes)
                         for s in ("PASS", "FAIL", "UNSTABLE", "NOT_RUN")}}
    dump(root / "assessments.json", result)
    lines = ["# 新建计划对话用例集：汇总复核", "", "基线 ae94765；A 33 条 / B 18 条。",
             "本文件由保存的原始记录离线复核；未再次调用模型。原 record.json 保留最初断言，assessments.json 保留更正后断言。",
             "相同原句既成功又失败记 UNSTABLE；预算拦截不算真实失败；调整过的测试原句保留为 SUPERSEDED_UTTERANCE。",
             "", f"A 结果：{result['counts']}。", "",
             f"实际 HTTP 返回用量按目录价计费：¥{http['total']['amount']}（CNY）；"
             f"{http['total']['calls']} 个 HTTP 响应，未定价 {http['total']['unpriced_calls']}。",
             "它包括网关漏记的失败响应、自动重试和共用轮廓准备；与网关金额不可相加。不是厂商账单。",
             f"厂商未单独报告 reasoning_tokens：{http['total']['unknown_fields']}；"
             "input/cache/output/search 可核对，推理消耗已计入 output 的计价，不把缺少推理分项说成全统计完整。",
             "", "| 用例 | 类别 / 说法 | 结果 | 有效尝试与记录 |", "|---|---|---|---|"]
    for o in outcomes:
        links = [f"[{a['status']}]({Path(a['path']).as_posix()})" for a in o["attempts"]
                 if a["status"] in {"PASS", "FAIL"}]
        lines.append(f"| {o['id']} | {o['area']} / {o['variant']} | {o['status']} | {' / '.join(links) or '本轮不运行' } |")
    lines += ["", "## 失败或不稳定观测", ""]
    for o in outcomes:
        if o["status"] not in {"FAIL", "UNSTABLE"}:
            continue
        lines.append("### " + o["id"])
        for a in o["attempts"]:
            if a["status"] != "FAIL":
                continue
            lines.append("记录：" + a["path"])
            for c in a["checks"]:
                if not c["passed"]:
                    lines.append("- " + c["name"] + "：" + json.dumps(c["actual"], ensure_ascii=False)[:2000])
    (root / "report.md").write_text("\n".join(lines) + "\n" + manual, encoding="utf-8")
    return result


if __name__ == "__main__":
    result = review()
    print(json.dumps({"counts": result["counts"], "http_cost": str(result["http_usage"]["total"]["amount"])}, ensure_ascii=False))

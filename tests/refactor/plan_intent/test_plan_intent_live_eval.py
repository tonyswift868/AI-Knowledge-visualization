"""Opt-in administrator model evaluation; never reads/prints/copies an API key.

MF_LIVE_EVAL=1 enables the one paid test. Its report and budget journal stay in
D:\\MentorFlow\\.local\\test-tmp\\plan-intent. Repeated evaluations share the
same journal and RMB 2 cap. Costs are gateway catalogue costs, not invoices.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import pytest

from refactor.learning.course.plan_intent import intent_request, parse_intents
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.course.planner_agents import parse_json
from intent_cases import assess, live_cases


ROOT = Path(r"D:\MentorFlow\.local\test-tmp\plan-intent")
ACCOUNT = "mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932"
PURPOSE = "mf05_plan_intent_eval"
LIMIT = Decimal("2")
MAX_TOKENS = 2048


def _utc():
    return datetime.now(timezone.utc)


def _usage(gateway, start):
    report = gateway.usage_log.summary(start, _utc() + timedelta(seconds=1), group_by="purpose")
    return next((group for group in report["groups"] if group["key"] == PURPOSE), None)


def _spend(usage):
    if usage is None:
        return Decimal(0)
    if not usage["amount_complete"] or usage["currency"] != "CNY" or usage["in_progress"]:
        raise RuntimeError("用量或人民币计价不完整，停止新的付费调用")
    return Decimal(usage["amount"])


def _write_report(report):
    # Contains synthetic utterances/model output and public model metadata only.
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    rows = report["rows"]
    passed = sum(row["passed"] for row in rows)
    lines = ["# 新建计划意图组件真实抽查", "", f"基线：69836b6；时间：{report['started']}。",
             f"模型：{report['provider']} / {report['model']}；系统提示 SHA256：{report['prompt_sha256']}。",
             f"价格表：{report.get('price_catalog', '未知')}；来源：{report.get('price_source', '未知')}。",
             f"已测 {len(rows)} / {report['planned']} 句，正确 {passed} 句；当前正确率 {passed / len(rows):.1%}。" if rows else "尚无已测用例。",
             f"本轮目录计价：¥{report['run_amount']}；本任务累计：¥{report['total_amount']}（上限 ¥2）。",
             "费用来自 gateway.usage_log.summary，包含失败调用；不代表厂商实际账单。密钥不打印、不落盘。",
             "判定核对 kind、顺序、参数及关键事实；附完整输出供逐句人工复核。未做产品界面接入。",
             "", "| 用例 | 阶段 | 用户原话 | 预期 | 实际 | 正确 | 原因 |", "|---|---|---|---|---|---|---|"]
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    for row in rows:
        lines.append("| " + " | ".join(cell(value) for value in (
            row["id"], row["phase"], row["text"], json.dumps(row["expected"], ensure_ascii=False),
            json.dumps(row["actual"], ensure_ascii=False), "是" if row["passed"] else "否", row["reason"])) + " |")
    if report.get("stopped"):
        lines.extend(("", "停止原因：" + report["stopped"]))
    if report.get("retry_rows"):
        lines.extend(("", "## 格式失败的一次宿主重试验证", "",
                      "保留上表首次结果，不将重试后结果替换为首次正确率。原问题和上下文不变，只附上格式校验错误请模型重答。", ""))
        for retry in report["retry_rows"]:
            lines.extend((f"- {retry['id']}：{'通过' if retry['passed'] else '失败'}；{retry['reason']}；"
                          f"本次目录计价 ¥{retry['amount']}。", "", "```json", retry["answer"], "```", ""))
    lines.extend(("", "## 逐句模型原文", ""))
    for row in rows:
        lines.extend((f"### {row['id']}", "", f"耗时：{row['seconds']:.2f} 秒。", "", "```json", row["answer"], "```", ""))
    lines.extend(("## 问题记录", "", "- 旧流程不理解修改/提问意图：本组件返回经校验的意图，宿主接入由架构师负责。",
                  "- 接口包含顶层 reply，但固定返回值只有 list[Intent]：校验 reply，不将其混入动作参数；宿主持有原始响应。",
                  "- 日期修改必须与原表单合并校验，否则单独改 end 可绕过 start 或既有特殊日约束；已加离线回归。",
                  "- 不同于 planner_agents 的宽容 JSON 提取，本组件拒绝重复键、重复 kind、非有限数值和多义引用。",
                  "- 本组件只校验格式与引用，不能凭字符串校验保证自然语言回答属实；未知正文/价格及越权要求包含在真实抽查中。", ""))
    lines.extend(("- 管理员数据目录未安装价格表：预检在调用前停止（无费用）；随后按正式应用方式在内存注入仓库内置价格表，未下载或改写账号配置。",
                  "- 首轮种类/参数检查为 32/32，但人工发现 reply 有已记录/宿主措辞、问答出现 CTX.facts.estimate。提示改为非闲聊 reply 留空、用户可见字段禁用内部术语；首轮原文保留 report-first.json/md，最终复测另列于本报告。",
                  "- 首次全套测试的 PYTEST_ADDOPTS 使用反斜杠被 shlex 转义，临时目录落在 D 盘工位内。测试结束后校验绝对路径并移回指定 D 盘临时目录；后续参数改用正斜杠。",
                  "- 本任务限定只新增组件和测试；以上问题留在本回执报告，CURRENT_STATE 的归档由架构师合并。", ""))
    (ROOT / "report.md").write_text("\n".join(lines), encoding="utf-8")


@pytest.mark.skipif(os.environ.get("MF_LIVE_EVAL") != "1", reason="Set MF_LIVE_EVAL=1 for explicit paid administrator-model evaluation")
def test_plan_intent_live_administrator_sample():
    from refactor.learning.connections.gateway import ModelGateway
    from refactor.learning.connections.errors import ModelError

    ROOT.mkdir(parents=True, exist_ok=True)
    budget_file = ROOT / "budget.json"
    if budget_file.exists():
        budget_start = json.loads(budget_file.read_text(encoding="utf-8"))["start"]
    else:
        budget_start = (_utc() - timedelta(seconds=1)).isoformat()
        budget_file.write_text(json.dumps({"start": budget_start, "purpose": PURPOSE, "limit_cny": str(LIMIT)}), encoding="utf-8")
    # The normal application injects its catalogue too. The administrator has
    # no installed learning/model-prices.json on this machine; use the shipped
    # catalogue in memory, without downloading or writing account configuration.
    from refactor.learning.connections.pricing import PriceCatalog
    data_root = Path(r"D:\MentorFlowData\account-shell")
    installed_prices = data_root / "learning/model-prices.json"
    price_file = installed_prices if installed_prices.exists() else (
        Path(__file__).resolve().parents[3] / "refactor/learning/course/data/model-prices.json")
    gateway = ModelGateway(ACCOUNT, data_root, price_catalog=PriceCatalog(price_file))
    report = None
    try:
        config = gateway.get_configuration()
        assert config and config["has_api_key"], "管理员模型未配置；不另取或保存密钥"
        assert config["provider_id"] == "deepseek" and urlsplit(config["base_url"]).hostname == "api.deepseek.com", (
            "此抽查的人民币预算预留只适用于当前官方 DeepSeek 连接")
        cases = live_cases()
        system, _ = intent_request(cases[0]["ctx"], cases[0]["text"])
        initial = _spend(_usage(gateway, budget_start))
        report = dict(started=_utc().isoformat(), provider=config["provider_id"], model=config["model"],
                      price_catalog=gateway.price_catalog.version, price_source=str(price_file),
                      prompt_sha256=hashlib.sha256(system.encode("utf-8")).hexdigest(),
                      planned=len(cases), rows=[], run_amount="0", total_amount=str(initial), stopped="")
        for case in cases:
            system, messages = intent_request(case["ctx"], case["text"])
            spent = _spend(_usage(gateway, budget_start))
            # UTF-8 byte count + framing is a deliberately generous input-token
            # ceiling. No search/tools/retries, output capped by max_tokens.
            byte_bound = len((system + json.dumps(messages, ensure_ascii=False)).encode("utf-8")) + 1024
            quote = gateway.price_catalog.estimate_cost(config["provider_id"], config["model"],
                                                       input_tokens=(0, byte_bound), output_tokens=(0, MAX_TOKENS),
                                                       context={"region": "global", "service_tier": "standard", "price_period": "peak"})
            if quote is None or quote.currency != "CNY" or spent + quote.high > LIMIT:
                report["stopped"] = "下一次调用无法在已知人民币价格和剩余预算内安全预留"
                break
            started = time.monotonic()
            row = dict(id=case["id"], phase=case["ctx"]["phase"], text=case["text"], expected=case["expected"],
                       actual=[], answer="", passed=False, reason="")
            try:
                result = gateway.complete(PURPOSE, messages, system=system, enable_search=False, max_tokens=MAX_TOKENS)
                row["answer"] = result.text
                intents = parse_intents(result.text, case["ctx"])
                row["actual"] = [dict(kind=intent.kind, **intent.params) for intent in intents]
                row["passed"], row["reason"] = assess(case, intents, reply=parse_json(result.text)["reply"])
            except AgentFormatError as exc:
                row["reason"] = "模型输出校验失败：" + str(exc)
            except ModelError as exc:
                # Don't log exception internals, headers or credentials.
                row["reason"] = "模型调用失败：" + exc.code
            row["seconds"] = time.monotonic() - started
            report["rows"].append(row)
            usage = _usage(gateway, budget_start)
            report["usage"] = usage
            try:
                spent = _spend(usage)
            except RuntimeError as exc:
                report["stopped"] = str(exc)
                report["total_amount"] = "未知（见用量字段，禁止继续）"
                _write_report(report)
                break
            report["run_amount"], report["total_amount"] = str(spent - initial), str(spent)
            _write_report(report)
            print(f"LIVE {len(report['rows']):02d}/{len(cases)} {case['id']}: {'PASS' if row['passed'] else 'FAIL'}; total CNY {spent}", flush=True)
        _write_report(report)
        assert not report["stopped"], report["stopped"]
        assert len(report["rows"]) == len(cases)
        assert Decimal(report["total_amount"]) <= LIMIT
        failures = [row["id"] + ": " + row["reason"] for row in report["rows"] if not row["passed"]]
        assert not failures, "\n".join(failures)
    finally:
        gateway.close()
        if report is not None:
            _write_report(report)

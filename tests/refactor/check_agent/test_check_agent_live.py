"""Opt-in, low-cost DeepSeek Flash sampling for CHK-01 components.

The ordinary suite skips this file.  Credentials are obtained only by
ModelGateway, never read by this test; all evidence is written under D:.
"""
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from refactor.learning.course.check_agent import check_request, parse_check
from refactor.learning.course.planner_agents import AgentFormatError


pytestmark = pytest.mark.skipif(os.environ.get("MF_LIVE_EVAL") != "1", reason="真实模型抽查须显式设置 MF_LIVE_EVAL=1")
ROOT = Path(r"D:\MentorFlow\.local\test-tmp\check-agent")
ACCOUNT = "mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932"


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class _Budget:
    def __init__(self, gateway):
        from refactor.learning.connections import providers

        self.gateway = gateway
        self.path = ROOT / "budget.json"
        self.rows = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        self.config = gateway.get_configuration()
        assert self.config and self.config["has_api_key"], "管理员模型尚未配置"
        assert providers.detect_provider(self.config["base_url"]) == self.config["provider_id"], "需要可核价的官方模型地址"
        self.cache = ROOT / "responses"
        self.cache.mkdir(parents=True, exist_ok=True)

    def call(self, label, request, max_tokens=4096):
        from refactor.learning.connections.gateway import ModelGateway
        from refactor.learning.connections.pricing import timestamp

        system, messages = request
        packet = json.dumps(dict(system=system, messages=messages, max_tokens=max_tokens,
                                 provider=self.config["provider_id"], model=self.config["model"],
                                 temperature=self.config["temperature"]), ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(packet.encode("utf-8")).hexdigest()
        cached = self.cache / (key + ".json")
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))["text"]
        estimate = self.gateway.price_catalog.estimate_cost(
            self.config["provider_id"], self.config["model"],
            input_tokens=(len(packet.encode("utf-8")) + 1024,) * 2,
            output_tokens=(max_tokens,) * 2, cache_hit_rate=(0, 0),
            context=dict(region="global", cache_mode="implicit", service_tier="standard"))
        assert estimate is not None and estimate.currency == "CNY", "无可靠人民币价格，不发起付费调用"
        spent = sum(Decimal(row.get("amount") or row["reserved"]) for row in self.rows)
        assert spent + estimate.high <= Decimal("3"), "已达到 ¥3 抽查上限，未发起下一请求"
        row = dict(label=label, request_hash=key, reserved=str(estimate.high), status="pending", at=timestamp().isoformat())
        self.rows.append(row)
        _save(self.path, self.rows)
        started = time.monotonic()
        try:
            value = self.gateway.complete("check_profile", messages, system=system,
                                          enable_search=False, max_tokens=max_tokens)
            # gateway.complete already records usage; this normalized copy is
            # only for the local cost report and must receive Usage, not the
            # enclosing ModelResult.
            usage = ModelGateway._billable_usage(value.usage, self.config)
            price = self.gateway.price_catalog.cost(value.provider_id, value.model, usage, at=row["at"])
            row.update(status="received", seconds=round(time.monotonic() - started, 3),
                       input_tokens=usage.input_tokens, cached_input_tokens=usage.cached_input_tokens,
                       output_tokens=usage.output_tokens, model=value.model,
                       amount=str(price.amount) if price and price.currency == "CNY" else None)
            _save(cached, dict(text=value.text, label=label, request_hash=key))
            return value.text
        except Exception as exc:
            row.update(status="unknown", error=getattr(exc, "code", type(exc).__name__))
            raise RuntimeError("抽查调用失败；费用预留保留在 budget.json") from None
        finally:
            _save(self.path, self.rows)




def _attribution_context():
    return dict(
        points=[
            {"id": "p-boundary", "title": "概念边界", "summary": "区分两种反射智能体。"},
            {"id": "p-apply", "title": "应用迁移", "summary": "把机制用到新场景。"},
            {"id": "p-error", "title": "找错改错", "summary": "发现条件与结论不一致。"},
        ],
        records=[
            {"question": "区分简单反射和基于模型的反射。", "answer": "都只看当前感知。", "reason": "忽略了内部状态的作用。", "context": "module_test", "point_ids": ["p-boundary"]},
            {"question": "把机制用到门禁场景。", "answer": "换场景后不能说明依据。", "reason": "没有把原理迁移到条件。", "context": "check", "point_ids": ["p-apply"]},
            {"question": "指出错误说法。", "answer": "把传感器说成内部模型。", "reason": "混淆了当前输入与状态估计。", "context": "retest", "point_ids": ["p-error"]},
        ],
    )


def _report(payload, budget):
    _save(ROOT / "results.json", payload)
    known = sum(Decimal(row["amount"]) for row in budget.rows if row.get("amount") is not None)
    reserved = sum(Decimal(row["reserved"]) for row in budget.rows if row.get("amount") is None)
    lines = ["# MF-01 第八批 CHK-01 真实抽查", "", "模型：管理员配置的 DeepSeek Flash；资料为合成课堂材料。",
             "只抽查保留的归因组件，记录均为合成错题；不调用已删除的出题组件。"]
    lines += ["", "## 归因输出", "```json", json.dumps(payload.get("profile"), ensure_ascii=False, indent=2), "```",
              "", "## 人工核对记录", *[f"- {item}" for item in payload["review"]], "", "## 花费",
              f"累计调用 {len(budget.rows)} 次；已核价 ¥{known}；未知结果预留 ¥{reserved}；预算上限 ¥3。"]
    (ROOT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_check_agent_real_attribution():
    from refactor.learning.connections.gateway import ModelGateway
    from refactor.learning.connections.pricing import PriceCatalog

    ROOT.mkdir(parents=True, exist_ok=True)
    catalog = PriceCatalog(Path(__file__).resolve().parents[3] / "refactor/learning/course/data/model-prices.json")
    gateway = ModelGateway(ACCOUNT, r"D:\MentorFlowData\account-shell", price_catalog=catalog)
    budget = _Budget(gateway)
    payload = {"profile": None, "review": []}
    try:
        actx = _attribution_context()
        raw = budget.call("attribution", check_request(actx))
        try:
            profile = parse_check(raw, actx)
        except AgentFormatError:
            retry = (check_request(actx)[0], check_request(actx)[1] +
                     [{"role": "user", "content": "上次归因未通过格式检查，请输出完整 JSON，覆盖三个错题点且不要加入新点。"}])
            raw = budget.call("attribution-retry", retry)
            profile = parse_check(raw, actx)
        payload["profile"] = asdict(profile)
        assert {pid for category in profile.categories for pid in category.point_ids} == {"p-boundary", "p-apply", "p-error"}
        payload["review"].append("归因类别只引用三个输入错题点；人工检查类别说明都能回指题面/判定理由，没有把合成用户话语解释成已修改状态。")
        _report(payload, budget)
    finally:
        _report(payload, budget)
        gateway.close()

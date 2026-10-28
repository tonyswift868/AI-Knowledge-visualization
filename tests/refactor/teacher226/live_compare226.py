"""Opt-in teacher prompt comparison; this module is not collected by pytest.

Preparation only (no model request):
  python -m tests.refactor.teacher226.live_compare226 --inspect
  python -m tests.refactor.teacher226.live_compare226 --dry-check

Paid comparison (only after the task owner authorizes it):
  python -m tests.refactor.teacher226.live_compare226 --live --expectation PATH
  python -m tests.refactor.teacher226.live_compare226 --live --working-tree-only --samples SAMPLE_ID ... --expectation PATH

The script compares teacher_agent at baseline 66cbabea with the working tree,
using the same six synthetic questions and the existing ModelGateway. It never
reads, prints, or saves a key. All artifacts stay under teacher226/live.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from tests.refactor.simulation.run import live_factory


REPO = Path(__file__).resolve().parents[3]
SCRATCH = Path(r"D:\MentorFlow\.local\test-tmp\teacher226\live")
ACCOUNT = "mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932"
PRESET_ROOT = Path(r"D:\MentorFlowData\account-shell")
BASELINE = "66cbabea"
OUTPUT_CAP = 8192
BUDGET_CNY = Decimal("10")
BASELINE_ASK_DIRECTOR = (
    '正常回答学生追问，依据本点和本计划已采用资料；没有依据就直说“资料里没讲到”，'
    '可在计划里补充资料。回答完回到原来的位置，不出题、不判分。')
CURRENT_ASK_DIRECTOR = (
    '回答学生主动追问：先用本计划检索证据，足够就依据资料回答并选真实出处；'
    '证据不足就用自己的知识直接解释，声明supplement由程序标明补充。'
    '回答完回到原来的位置，不出题、不判分。')

# The excerpts and plan passages below are explicitly synthetic, short and
# identical for both versions. No user's saved plan, materials, RAG, or account
# database is read.
SAMPLES = (
    ("plan_material_business_layer", "业务校验应该放在哪一层？",
     {"current": "当前点只介绍模块边界。"},
     {"architecture-note": "合成讲义：业务规则校验由应用服务层负责；数据库约束作为最后一道完整性防线。"}),
    ("plan_material_consistency", "强一致性和最终一致性有什么区别？",
     {"current": "当前点只介绍模块边界。"},
     {"consistency-note": "合成讲义：强一致性要求写入完成后后续读取立即看到新值；最终一致性允许短暂延迟，但系统会在没有新写入时收敛到一致状态。"}),
    ("plan_material_golden_set", "Golden Set 的场景标签有什么用？",
     {"current": "当前点只介绍模块边界。"},
     {"test-note": "合成讲义：Golden Set 的每个样例带有业务场景标签，便于按场景检查模型输出是否覆盖预期行为。"}),
    ("no_material_jvm", "JVM 是什么？",
     {"current": "当前点只介绍模块边界。"}, {}),
    ("no_material_resume", "我没有获奖经历，简历里该怎么回答或呈现？",
     {"current": "当前点只介绍模块边界。"}, {}),
    ("current_point_concept", "能用一个生活中的例子解释当前点吗？",
     {"current": "合成讲义：模块边界用于把系统拆成职责清楚、可以独立验证的部分。"}, {}),
)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _make_context(question: str, local_excerpts: dict, plan_passages: dict, *, current: bool) -> dict:
    # Baseline sees the real point-only citation map plus the same broader
    # passages its historical prompt exposed. The current QA path uses the
    # complete plan evidence as excerpts, so citations resolve to real chunks.
    excerpts = ({**local_excerpts, **plan_passages} if current else dict(local_excerpts))
    ctx = {
        "plan": {"goal": "合成软件工程学习计划", "final_goal": "理解基础概念", "level": "beginner"},
        "module": {"title": "合成模块边界", "points": [
            {"id": "synthetic-point", "title": "模块边界", "summary": "合成软件工程知识点"}]},
        "excerpts": excerpts,
        "plan_passages": plan_passages,
        "director": CURRENT_ASK_DIRECTOR if current else BASELINE_ASK_DIRECTOR,
        "user": question,
        "opening": False,
        "profile": "",
        "current_points": [{"id": "synthetic-point", "title": "模块边界"}],
        "prefs": {"teacher_style": "calm", "detail": "normal", "examples": "few",
                  "analogy_domains": ["life"], "persona": ""},
        "dialogue": [],
    }
    if current:
        ctx["qa"] = True
        ctx["question_only"] = True
    return ctx


def _context_for(sample, *, current: bool) -> dict:
    sample_id, question, excerpts, passages = sample
    merged = {**excerpts, **passages}
    return _make_context(question, excerpts, merged, current=current)


def _target_chunk(sample_id: str, excerpts: dict, passages: dict) -> str | None:
    if passages:
        return next(iter(passages))
    if sample_id == "current_point_concept":
        return next(iter(excerpts))
    return None


def _synthetic_raw(agent, ctx: dict, target_chunk: str | None, *, current: bool) -> str:
    if not current:
        if target_chunk is None:
            return "基线合成回复：资料里没讲到。"
        aliases = agent.citation_map(ctx)
        alias = {chunk: name for name, chunk in aliases.items()}[target_chunk]
        return f"基线合成回复，依据当前点资料。[{alias}]"
    if target_chunk is None:
        # Exercise removal of model-invented material links and host prefixing.
        alias = next(iter(agent.citation_map(ctx)), "c1")
        return (f"【依据】supplement【/依据】\n合成补充回复[{alias}] "
                f"[伪造出处](mf-source:{next(iter(ctx['excerpts']))})")
    aliases = {chunk: alias for alias, chunk in agent.citation_map(ctx).items()}
    alias = aliases[target_chunk]
    return f"【依据】material:{alias}【/依据】\n依据合成讲义回答问题。[{alias}]"


def _parse_and_label(agent, ctx: dict, raw: str, *, current: bool):
    teaching = agent.parse_teaching(raw, ctx)
    if current:
        from refactor.learning.app.classroom import Classroom
        teaching = Classroom._label_supplement(teaching)
    return teaching


def _write_manual_review(root: Path, records: list[dict], totals: dict) -> Path:
    lines = ["# MF-03 真实对照逐条人工核对", "",
             f"预算预留：¥{totals.get('reserved_total_cny')}；已计价费用：¥{totals.get('actual_total_cny')}；"
             f"未计价调用：{totals.get('unpriced_call_count')}",
             "下列为原始模型响应与程序解析后的最终 speech；不含模型自评。", ""]
    for record in records:
        usage = record.get("usage", {})
        lines.extend([
            f"## {record['sample_id']} / {record['version']}", "",
            f"依据：{record.get('basis', 'parse_error')}；引用块：{', '.join(record.get('cited_chunks', [])) or '无'}；"
            f"费用：¥{record.get('cost_cny', '未知')}；"
            f"tokens input/cache-hit/output/reasoning：{usage.get('input_tokens')}/"
            f"{usage.get('cached_input_tokens')}/{usage.get('output_tokens')}/"
            f"{usage.get('reasoning_tokens')}", "",
            "原始响应：", "~~~~text", record.get("response", ""), "~~~~", "",
            "解析后的最终 speech：", "~~~~text", record.get("final_speech", record.get("parse_error", "")), "~~~~", "",
        ])
    path = root / "manual-review.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def dry_check(root: Path) -> dict:
    from refactor.learning.course import teacher_agent
    root.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="dry-baseline-", dir=root) as temp_dir:
        old_agent = _baseline_module(temp_dir)
        for sample in SAMPLES:
            sample_id, question, excerpts, passages = sample
            target = _target_chunk(sample_id, excerpts, passages)
            for version, agent, current in (
                ("baseline_66cbabea", old_agent, False), ("working_tree", teacher_agent, True)):
                ctx = _context_for(sample, current=current)
                system, messages = agent.teacher_request(ctx)
                baseline_target = target if sample_id == "current_point_concept" else None
                raw = _synthetic_raw(agent, ctx, target if current else baseline_target, current=current)
                teaching = _parse_and_label(agent, ctx, raw, current=current)
                if current:
                    if not agent.teaching_mode(ctx) == "qa":
                        raise AssertionError("新版请求没有进入 qa 模式")
                    if target is None:
                        if (teaching.answer_basis != "supplement" or teaching.cited_chunks
                                or not teaching.speech.startswith("讲义里没有，下面是补充：\n\n")):
                            raise AssertionError("新版 supplement 解析或程序前缀不符合预期")
                    elif teaching.answer_basis != "material" or target not in teaching.cited_chunks:
                        raise AssertionError("新版 material 解析未保留对应计划资料 chunk")
                elif getattr(teaching, "answer_basis", "lesson") != "lesson":
                    raise AssertionError("基线非 QA 请求意外启用了新依据协议")
                results.append({
                    "sample_id": sample_id, "question": question, "version": version,
                    "qa_mode": agent.teaching_mode(ctx) == "qa" if current else False,
                    "request_system": system, "request_messages": messages,
                    "synthetic_raw_response": raw, "basis": getattr(teaching, "answer_basis", "lesson"),
                    "cited_chunks": list(teaching.cited_chunks), "final_speech": teaching.speech,
                })
    report = {"mode": "dry_request_parse_only_no_network", "model_requests": 0,
              "scope": "teacher_request + parse_teaching + current Classroom._label_supplement; no full classroom, RAG, database, or UI flow",
              "records": results}
    _write_json(root / "dry-request-parse.json", report)
    return report


def _baseline_module(temp_dir: str):
    source = subprocess.run(
        ["git", "show", f"{BASELINE}:refactor/learning/course/teacher_agent.py"],
        cwd=REPO, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    path = Path(temp_dir) / "teacher_agent_baseline226.py"
    path.write_text(source, encoding="utf-8")
    name = "refactor.learning.course.teacher_agent_baseline226"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载基线 teacher_agent")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _estimate(real, system: str, messages: list[dict]) -> Decimal:
    """Reserve a three-request gateway retry worst case before each paid call."""
    config = real.get_configuration()
    text = system + json.dumps(messages, ensure_ascii=False)
    input_upper = len(text.encode("utf-8")) + 4096
    estimate = real.price_catalog.estimate_cost(
        config["provider_id"], config["model"],
        input_tokens=(input_upper, input_upper), output_tokens=(OUTPUT_CAP, OUTPUT_CAP),
        cache_hit_rate=(0, 0),
        context=dict(real.estimate_context(), price_period="peak", cache_mode="implicit"),
    )
    if estimate is None or estimate.currency != "CNY":
        raise RuntimeError("缺少可确认的人民币价格，拒绝付费调用")
    return estimate.high * 3


def inspect(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    real = live_factory(PRESET_ROOT, ACCOUNT)(SimpleNamespace(data_root=root / "data"))
    try:
        config = real.get_configuration()
        # Estimate the largest of both versions' synthetic requests using peak
        # prices; metadata inspection does not make a model request.
        from refactor.learning.connections.pricing import timestamp
        from refactor.learning.course import teacher_agent
        with tempfile.TemporaryDirectory(prefix="inspect-baseline-", dir=root) as temp_dir:
            old_agent = _baseline_module(temp_dir)
            estimates = []
            for sample_id, question, excerpts, passages in SAMPLES:
                for agent, current in ((old_agent, False), (teacher_agent, True)):
                    context = _context_for((sample_id, question, excerpts, passages), current=current)
                    system, messages = agent.teacher_request(context)
                    estimates.append(_estimate(real, system, messages))
        estimate = max(estimates)
        catalog_path = REPO / "refactor/learning/course/data/model-prices.json"
        catalog_data = json.loads(catalog_path.read_text(encoding="utf-8"))
        report = {
            "mode": "inspect_only_no_model_calls",
            "account_id": ACCOUNT,
            "preset": {"provider_id": config["provider_id"], "model": config["model"]},
            "price_catalog_version": catalog_data.get("version"),
            "price_catalog_peak_policy": "use DeepSeek catalogue peak rates for reservation",
            "per_call_retry_worst_case_cny": str(estimate),
            "max_calls": len(SAMPLES) * 2,
            "maximum_reserved_cny": str(estimate * len(SAMPLES) * 2),
            "task_budget_cny": str(BUDGET_CNY),
            "checked_at_utc": timestamp().isoformat(),
            "secrets_read_or_saved": False,
            "model_requests": 0,
        }
        _write_json(root / "inspect.json", report)
        return report
    finally:
        real.close()


def run_live(root: Path, expectation_path: Path, *, sample_ids=None, versions="both") -> dict:
    if not expectation_path.is_file() or not expectation_path.read_text(encoding="utf-8").strip():
        raise RuntimeError("--live 必须附带非空的人工预期说明文件")
    result_path = root / "comparison.json"
    if result_path.exists():
        raise RuntimeError(f"拒绝覆盖已有对照产物：{result_path}")
    root.mkdir(parents=True, exist_ok=True)
    real = live_factory(PRESET_ROOT, ACCOUNT)(SimpleNamespace(data_root=root / "data"))
    try:
        config = real.get_configuration()
        if config["provider_id"] != "deepseek" or config["model"] not in ("deepseek-v4-flash", "deepseek-flash"):
            raise RuntimeError("当前预设不是管理员 DeepSeek Flash，拒绝调用")
        from refactor.learning.connections import gateway as gateway_module
        from refactor.learning.course import teacher_agent
        from refactor.learning.connections.pricing import timestamp

        records = []
        reserved = Decimal(0)
        selected_samples = [s for s in SAMPLES if sample_ids is None or s[0] in sample_ids]
        report = {"baseline": BASELINE if versions == "both" else None,
                  "model": config["model"], "provider_id": config["provider_id"],
                  "output_cap_tokens": OUTPUT_CAP, "budget_cny": str(BUDGET_CNY),
                  "samples": [s[0] for s in selected_samples], "versions": versions,
                  "human_expectation": expectation_path.read_text(encoding="utf-8"),
                  "quality_self_evaluation": False, "records": records}
        _write_json(result_path, report)
        # The product's current DeepSeek model override is 384K. Constrain this
        # opt-in experiment to 8192 via the existing gateway's runtime map.
        with tempfile.TemporaryDirectory(prefix="baseline-", dir=root) as temp_dir, \
                __import__("unittest.mock", fromlist=["patch"]).patch.dict(
                    gateway_module.MODEL_MAX_OUTPUT, {config["model"]: OUTPUT_CAP}):
            old_agent = _baseline_module(temp_dir) if versions == "both" else None
            for sample in selected_samples:
                sample_id, question, excerpts, passages = sample
                target = _target_chunk(sample_id, excerpts, passages)
                agents = ([ ("baseline_66cbabea", old_agent, False), ("working_tree", teacher_agent, True) ]
                          if versions == "both" else [("working_tree", teacher_agent, True)])
                for version, agent, current in agents:
                    context = _context_for(sample, current=current)
                    system, messages = agent.teacher_request(context)
                    bound = _estimate(real, system, messages)
                    if reserved + bound > BUDGET_CNY:
                        raise RuntimeError(f"预算闸门拒绝第 {len(records) + 1} 次调用；尚未发送该请求")
                    reserved += bound
                    started = timestamp()
                    call_record = {"sample_id": sample_id, "question": question, "version": version,
                                   "system": system, "messages": messages,
                                   "reserved_retry_worst_case_cny": str(bound), "started_at_utc": started.isoformat()}
                    records.append(call_record)
                    _write_json(result_path, report)
                    response = real.complete("teacher", messages, system=system, max_tokens=OUTPUT_CAP)
                    usage = response.usage
                    pricing_context = dict(real.estimate_context(), price_period="peak", cache_mode="implicit",
                                           finished_at=timestamp().isoformat())
                    cost = real.price_catalog.cost(config["provider_id"], config["model"], usage,
                                                   at=started, context=pricing_context)
                    try:
                        teaching = _parse_and_label(agent, context, response.text, current=current)
                        parsed = {"basis": getattr(teaching, "answer_basis", "lesson"),
                                  "cited_chunks": list(teaching.cited_chunks),
                                  "final_speech": teaching.speech}
                    except Exception as exc:
                        parsed = {"parse_error": getattr(exc, "reason", type(exc).__name__)}
                    call_record.update({
                        "response": response.text,
                        **parsed,
                        "usage": {"input_tokens": usage.input_tokens, "cached_input_tokens": usage.cached_input_tokens,
                                  "uncached_input_tokens": usage.uncached_input_tokens,
                                  "output_tokens": usage.output_tokens, "reasoning_tokens": usage.reasoning_tokens,
                                  "search_requests": usage.search_requests,
                                  "unreported_fields": usage.raw.get("_mentorflow", {}).get("unreported_fields", [])},
                        "cost_cny": str(cost.amount) if cost is not None else None,
                        "actual_model": response.model,
                        "finish_reason": response.finish_reason,
                    })
                    _write_json(result_path, report)
        report["reserved_total_cny"] = str(reserved)
        priced = [Decimal(r["cost_cny"]) for r in records if r.get("cost_cny") is not None]
        report["actual_total_cny"] = (str(sum(priced, Decimal(0)))
                                       if len(priced) == len(records) else None)
        report["unpriced_call_count"] = len(records) - len(priced)
        report["ended_at_utc"] = timestamp().isoformat()
        report["manual_review_file"] = str(_write_manual_review(root, records, report))
        _write_json(result_path, report)
        return report
    finally:
        real.close()


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="付费对照；须有明确任务授权")
    parser.add_argument("--inspect", action="store_true", help="只检查管理员预设和价格，不调用模型")
    parser.add_argument("--dry-check", action="store_true", help="构造请求并走本地解析器，不调用模型")
    parser.add_argument("--expectation", type=Path, help="人工预期文本；--live 必填并随产物保存")
    parser.add_argument("--samples", nargs="+", choices=[s[0] for s in SAMPLES],
                        help="--live 时仅运行指定样例")
    parser.add_argument("--versions", choices=("both", "working_tree"), default=None,
                        help="--live 对照双方，或只运行工作树版本")
    parser.add_argument("--working-tree-only", action="store_true",
                        help="仅运行当前工位工作树版本，不重跑基线")
    parser.add_argument("--root", type=Path, default=SCRATCH)
    args = parser.parse_args(argv)
    if Path.cwd().resolve() != REPO.resolve():
        parser.error(f"请从工位仓库根目录运行：{REPO}")
    root = args.root.resolve()
    if not root.is_relative_to(SCRATCH.resolve()):
        parser.error(f"产物目录必须位于 {SCRATCH}")
    if sum((args.live, args.inspect, args.dry_check)) != 1:
        parser.error("必须且只能指定 --inspect、--dry-check 或 --live")
    if args.samples and len(set(args.samples)) != len(args.samples):
        parser.error("--samples 不能重复指定样例")
    if args.working_tree_only and args.versions == "both":
        parser.error("--working-tree-only 与 --versions both 冲突")
    if not args.live and (args.samples or args.versions is not None or args.working_tree_only):
        parser.error("--samples 和非默认 --versions 仅用于 --live")
    versions = "working_tree" if args.working_tree_only else (args.versions or "both")
    try:
        report = (inspect(root) if args.inspect else dry_check(root) if args.dry_check
                  else run_live(root, args.expectation,
                                sample_ids=set(args.samples) if args.samples else None,
                                versions=versions))
    except Exception as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in report.items() if k != "records"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

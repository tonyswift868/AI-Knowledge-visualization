"""Append batch 7 results to the original report; evidence only, no network."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import statistics
import sys

import plan_parameter_eval as base
from retest_plan_parameters import NAMES, isolated
from summarize_plan_parameter_eval import COMPARISONS, build_analysis, cost_breakdown, table


def load_runs(root):
    return {p.parent.name: base.read_json(p) for p in (root / "runs").glob("*/result.json") if p.parent.name.rsplit("-r", 1)[0] in NAMES}


def comparisons_with_points(runs, judges):
    result = build_analysis(runs, judges)
    for comparison in result:
        contract = next(c for c in COMPARISONS if c[0] == comparison["comparison"])
        pairs = [[runs.get(f"{name}-r{repeat}") for repeat in (1, 2)] for name in contract[1:3]]
        if not all(r and r["formal_complete"] for side in pairs for r in side):
            continue
        for metric in ("point_count", "point_minutes_sum", "point_minutes_mean", "point_summary_mean"):
            comparison["numeric"][metric] = base.contrast(*[[r["metrics"][metric] for r in side] for side in pairs])
    return result


def minute_audit(runs):
    from refactor.learning.course import planner_agents as agents
    records = []
    for rid, run in sorted(runs.items()):
        draft, points = run["draft"], run["metrics"]["points"]
        for module in agents.outline_modules(draft.get("outline") or {"stages": []}):
            module_id = module.get("id")  # Outline-only drafts have keys, not stored module IDs.
            if not module_id or draft.get("generation", {}).get(module_id) != "done":
                continue
            selected = [p for p in points if p["module_id"] == module_id]
            row = dict(run=rid, module=module["title"], range=module["minutes"], total=sum(p["minutes"] for p in selected))
            try:
                agents.validate_point_minutes(selected, module["minutes"])
                row["valid"] = True
            except agents.AgentFormatError as exc:
                row.update(valid=False, error=str(exc))
            records.append(row)
    return records


def report(root, output):
    root = isolated(root)
    output = Path(output).resolve()
    if not output.is_relative_to(base.TEMP_ROOT.resolve()):
        raise ValueError("Reports stay under the designated D: eval-plan directory.")
    manifest = base.read_json(root / "manifest.json")
    previous = isolated(manifest["previous"])
    before, after = load_runs(previous), load_runs(root)
    comparisons = {}
    all_judges = []
    for name, folder, runs in (("before", previous, before), ("after", root, after)):
        judges = [base.read_json(p) for p in sorted((folder / "outline-judges").glob("*.json"))]
        comparisons[name] = [a for a in comparisons_with_points(runs, judges) if a["comparison"] in
                             {"level", "notes_practice", "notes_principle", "notes_pair"}]
        if name == "after":
            all_judges = judges
    audit = minute_audit(after)
    base.write_json(root / "minute-audit.json", audit)
    base.write_json(root / "retest-comparison.json", comparisons)
    notes = base.read_json(root / "retest-notes.json") if (root / "retest-notes.json").exists() else {}
    cost = base.read_json(root / "cost-latest.json")
    demo = base.read_json(root / "offline-batch-demo.json")
    lines = ["## 第七批复测", "", f"基线：`{manifest['baseline']}`；证据目录：`{root}`；提示/工具文件SHA256：`{manifest['prompt_sha']}`。", "",
             f"实际完成：{sum(r['outline_complete'] for r in after.values())}/8轮廓、"
             f"{sum(r['formal_complete'] for r in after.values())}/8正式计划、"
             f"{sum('selected' in j for j in all_judges)}/16轮廓正反盲评。", ""]
    for text in notes.get("conclusions", ["复测尚未结束，不提前宣布有效。"]):
        lines += [text, ""]
    lines += ["### 修改与比较方法", "",
              "只修改planner_agents组件：明确四种水平应如何改变前置、粒度、深度与时长；补充说明必须落实到可检验任务和分钟。没有植入智能体或其他领域模板。",
              "本轮沿用第六批的表单、模拟用户回复、2026-09-28起点、教材字节快照与模型预设。四配置（空备注零基础、熟练、实战备注、原理备注）各2次，取原区组顺序的子序列；轮廓与正式生成仍调用未改的PlanFlow。",
              "与上轮分时运行，模型后端、缓存及随机性不是完全可控。每配置n=2，只作描述性比较；仍用均值差与较大的组内极差对比，不把正反两票算作两个独立课程样本。原来备注组没有正式知识点，本轮生成的是新增证据，不能伪称已有成对前测。",
              f"本轮预实验另存于{manifest.get('discarded_pilots', [])}，共{manifest.get('pilot_calls', 0)}次调用仍计入¥8总预算。若有预实验，整轮排除、不与最终提示版本混样；最终版本全部配置各重新运行2次。",
              "generate_request默认把module.minutes写入提示；显式minutes_range可给分批分配覆盖区间。parse_points的minutes_range为兼容旧编排而保留可选；本工位未改plan_flow.py，所以正式产物还会另行离线审计，而不是宣称运行中已启用硬校验。",
              "分钟校验包含端点：ceil(0.75×下界)≤点分钟之和≤floor(1.25×上界)，只算首轮学习，排除检测/复习。分批解析和去重合并后均可校验；超出报AgentFormatError，不修改分钟凑数。", "",
              "### 前后指标", ""]
    rows = []
    for name in ("base", "advanced", "practice", "principle"):
        for phase, runs in (("前", before), ("后", after)):
            for repeat in (1, 2):
                run = runs.get(f"{name}-r{repeat}")
                if not run:
                    continue
                m, complete = run["metrics"], run["formal_complete"]
                interval = (m.get("ranges") or {}).get("total")
                rows.append([phase, run["run_id"], m["question_count"], m["module_count"], interval,
                             statistics.mean(interval) if interval else None, m["point_count"] if complete else "未完整",
                             m["point_minutes_mean"] if complete else None, m["point_summary_mean"] if complete else None,
                             run["draft"]["phase"]])
    lines += table(["前后", "运行", "追问数", "模块数", "预计总分钟区间", "区间中点", "正式点数", "每点分钟均值", "摘要字符均值", "状态"], rows)
    for key in ("level", "notes_practice", "notes_principle", "notes_pair"):
        lines += ["", f"### 对照：{key}", ""]
        metrics = []
        for phase in ("before", "after"):
            comparison = next(c for c in comparisons[phase] if c["comparison"] == key)
            for metric, value in comparison["numeric"].items():
                metrics.append([phase, metric, value["left"], value["right"], value["delta"], value["within_max"], value["exceeds_repeat_variation"]])
            completed_votes = comparison['blind_right'] + comparison['blind_left'] + comparison['blind_tie']
            lines += [f"{phase}盲评：完成{completed_votes}/4，错误{comparison['blind_errors']}；目标条件{comparison['blind_right']}票；相反条件{comparison['blind_left']}票；平局{comparison['blind_tie']}；"
                      f"正反一致：{comparison['reverse_consistent']}。", ""]
        lines += table(["前后", "指标", "基线两次", "条件两次", "均值差", "最大重复极差", "超过波动"], metrics)
    lines += ["", "### 正式产物分钟审计", "", f"检查{len(audit)}个已完成模块，{sum(not a['valid'] for a in audit)}个超过25%容差。全部记录在minute-audit.json。", ""]
    lines += table(["运行", "模块", "轮廓区间", "点分钟总计", "通过"], [[a["run"], a["module"], a["range"], a["total"], a["valid"]] for a in audit])
    lines += ["", "### 原失败模块的离线分批演示", "",
              f"模块“{demo['module']}”：重放上轮失败请求的{demo['source_chars']}字资料，切成{len(demo['batches'])}批，"
              f"每批≤{demo['batch_cap']}字，合并{len(demo['points'])}个协议测试点，总{demo['minutes']}分钟，原区间{demo['original_minutes']}。",
              "明确使用替身生成器，网络/模型调用0次；只证明限长、完整引用、解析、去重排序及分钟校验接口能跑通，不代表大模块的真实模型生成质量或产品已接通分批。原请求、分批输入与替身结果保存在offline-batch-demo.json。", "",
              ]
    real_path = root / "live-batch-demo/result.json"
    if real_path.exists():
        real = base.read_json(real_path)
        lines += ["### 补充：同一失败模块的真实模型组件检查", "",
                  f"独立于产品编排执行：{real['completed_batches']}/{real['expected_batches']}批完成，"
                  f"合并校验通过：{real['success']}；点数{len(real.get('points', []))}，分钟{real.get('minutes', '未完成')}。"
                  f"错误：{real.get('error', '无额外合并错误')}。费用已计入本轮总额。原始批次请求、响应、引用在live-batch-demo目录。", ""]
        lines += table(["点", "掌握标准", "分钟"], [[p["title"], p["summary"], p["minutes"]] for p in real.get("points", [])])
        lines += [""]
    lines += ["### 提供给架构师的接口", "",
              "- generate_request(..., minutes_range=None, batch_index=None, batch_count=None, max_points=30)：原返回三元组不变，第三项为别名→原始chunk ID。",
              "- split_module_chunks(chunks, max_chars=12000)：按原顺序无损切批，超长块可拆，保留原ID与fragment_start/end。上限统计正文字符，不包含提示头。",
              "- batch_minute_ranges(batches, minutes_range)：按正文字符分配，每批至少2分钟，分配区间两端之和严格等于模块原区间；预算不足报错。",
              "- parse_points(..., minutes_range=None, min_points=2, max_points=30)：小批使用min_points=1，范围超25%报错。",
              "- merge_point_batches(point_batches, chunks, minutes_range=None)：完整批次解析后调用；标题/摘要双阈值去重（0.90/0.88，或摘要相同且标题≥0.65），保留首个措辞、合并引用、重复分钟取最大值，再按原资料引用顺序稳定重排。不同数字/明显否定不合并；不是语义去重。合并后再校验总分钟，不缩放。",
              "- validate_point_minutes(points, minutes_range)：可独立用于编排保存前的最终检查。编排需负责批次完成性、重试/取消、持久化与整体学习顺序；本批没有写入该层。", "",
              "### 花费", "", f"本轮{cost['calls']}次调用，已核算{cost['known_amounts']}，未知{cost['unpriced_calls']}次；"
              f"含全部未知预留的保守总额{cost['liability']} CNY，上限8。旧第六批费用未混入本轮。实际usage按仓库价格目录核价，不是厂商账单。", ""]
    lines += ["费用表中p2-是本轮排除的首版提示预实验，p7-v2-是最终提示复测，component-batch-是原失败模块的真实分批检查；其余是盲评。每次重试均计费，未知用量不记零。", ""]
    lines += table(["运行", "调用数", "已计价CNY", "未知次数", "保留额度", "保守总额"],
                   cost_breakdown([base.read_json(p) for p in (root / "calls").glob("*.json")], cost))
    for section in ("问题记录", "验证"):
        lines += ["", f"### {section}", ""]
        for text in notes.get(section, ["待最终复核填写。"]):
            lines += [text, ""]
    lines += ["### 各运行内容与失败留档", ""]
    for rid, run in sorted(after.items()):
        m = run["metrics"]
        lines += [f"#### {rid}", "", "追问：" + ("\n\n".join(m["questions"]) or "无"), ""]
        lines += table(["模块", "摘要", "分钟"], [[v["title"], v["summary"], v["minutes"]] for v in m["modules"]])
        lines += ["", "正式知识点（状态为generating时仅为已完成部分）：", ""]
        lines += table(["点", "掌握标准", "分钟"], [[p["title"], p["summary"], p["minutes"]] for p in m["points"]])
        lines += ["", f"状态：{run['draft']['phase']}；错误：{run.get('error_code', '')}；{'；'.join(m['errors'])}", "",
                  f"历史失败：`{root / 'runs' / rid / 'failed-attempts'}`；完整JSON：`{root / 'runs' / rid / 'result.json'}`。", ""]
    lines += ["### 第七批盲评完整提示与结果", ""]
    for j in all_judges:
        lines += [f"<details><summary>{j['id']}：{j.get('selected', j.get('error_code', '未完成'))}</summary>", "",
                  f"原顺序：{j.get('order')}。", "", "```text", j.get("system", ""), j.get("prompt", ""), "```", "",
                  "```json", j.get("raw", j.get("error_code", "未完成")), "```", "", "</details>", ""]
    fragment = "\n".join(lines)
    (root / "report7-fragment.md").write_text(fragment, encoding="utf-8")
    original = output.read_text(encoding="utf-8").split("\n## 第七批复测\n", 1)[0].rstrip()
    backup = root / "previous-report.md"
    if not backup.exists():
        backup.write_text(original + "\n", encoding="utf-8")
    base.write_json(root / "report-prefix.json", dict(sha256=hashlib.sha256(original.encode()).hexdigest()))
    output.write_text(original + "\n\n" + fragment + "\n", encoding="utf-8")
    print(str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=base.TEMP_ROOT / "report.md")
    args = parser.parse_args()
    sys.path.insert(0, str(base.REPO))
    report(args.root, args.output)

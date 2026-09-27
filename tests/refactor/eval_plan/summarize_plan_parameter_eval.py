"""Rebuild the requested local report from the saved experiment artifacts; no network."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import statistics

from plan_parameter_eval import COMPARISONS, TEMP_ROOT, contrast, read_json, write_json


def cell(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, list):
        return " / ".join(cell(v) for v in value)
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def table(headers, rows):
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + [
        "| " + " | ".join(cell(v) for v in row) + " |" for row in rows]


def build_analysis(runs, judges):
    result = []
    numeric = dict(module_count=lambda m: m["module_count"],
                   total_midpoint=lambda m: statistics.mean(m["ranges"]["total"]),
                   chapters=lambda m: len(m["chapter_coverage"]),
                   point_count=lambda m: m["point_count"],
                   point_minutes_sum=lambda m: m["point_minutes_sum"],
                   point_minutes_mean=lambda m: m["point_minutes_mean"],
                   point_summary_mean=lambda m: m["point_summary_mean"])
    for key, left, right, label0, label1, target in COMPARISONS:
        pair = [[runs.get(f"{name}-r{repeat}") for repeat in (1, 2)] for name in (left, right)]
        selected = [j for j in judges if j["id"].startswith(key + "-r")]
        summary = dict(comparison=key, left=label0, right=label1, target=target,
                       trials_complete=all(r and r["outline_complete"] for side in pair for r in side), numeric={},
                       blind_right=sum(j.get("selected", "").startswith(right + "-") for j in selected),
                       blind_left=sum(j.get("selected", "").startswith(left + "-") for j in selected),
                       blind_tie=sum(j.get("selected") == "tie" for j in selected),
                       blind_errors=sum("error_code" in j for j in selected),
                       blind_A=sum(j.get("verdict", {}).get("choice") == "A" for j in selected),
                       blind_B=sum(j.get("verdict", {}).get("choice") == "B" for j in selected))
        if summary["trials_complete"]:
            for metric, extract in numeric.items():
                if metric.startswith("point_") and (key not in ("level", "goal") or
                                                     not all(r["formal_complete"] for side in pair for r in side)):
                    continue
                values = [[extract(r["metrics"]) for r in side] for side in pair]
                if all(v is not None for side in values for v in side):
                    summary["numeric"][metric] = contrast(*values)
            if key in ("level", "goal") and all(r["formal_complete"] for side in pair for r in side):
                summary["numeric"]["formal_total_minutes"] = contrast(*[
                    [r["detail"]["plan"]["total_minutes"] for r in side] for side in pair])
        summary["reverse_consistent"] = []
        for repeat in (1, 2):
            copies = [j for j in selected if f"-r{repeat}-" in j["id"]]
            summary["reverse_consistent"].append(len(copies) == 2 and all("selected" in j for j in copies)
                                                 and copies[0]["selected"] == copies[1]["selected"])
        result.append(summary)
    return result


def cost_breakdown(calls, summary):
    """Reconcile per-run charges, including every retry and retained unknown reservation."""
    groups = {g["key"]: g for g in summary["groups"]}
    rows = {}
    for call in calls:
        row = rows.setdefault(call["run_id"], dict(calls=0, known=Decimal(0), unknown=0, reserved=Decimal(0)))
        group = groups.get(call["purpose"], {})
        row["calls"] += 1
        row["known"] += Decimal(str(group.get("known_amounts", {}).get("CNY", 0)))
        if not group.get("amount_complete", False):
            row["unknown"] += 1
            row["reserved"] += Decimal(call["reservation"])
    return [[run, r["calls"], r["known"], r["unknown"], r["reserved"], r["known"] + r["reserved"]]
            for run, r in sorted(rows.items())]


def report(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not root.is_relative_to(TEMP_ROOT.resolve()) or not output.is_relative_to(TEMP_ROOT.resolve()):
        raise ValueError("Experiment reports must remain in the designated D: directory.")
    manifest = read_json(root / "manifest.json")
    runs = {p.parent.name: read_json(p) for p in (root / "runs").glob("*/result.json")}
    outline_only = (root / "outline-judges").is_dir()
    judge_dir = root / ("outline-judges" if outline_only else "judges")
    judges = [read_json(p) for p in sorted(judge_dir.glob("*.json"))]
    supplementary = [read_json(p) for p in sorted((root / "judges").glob("*.json"))
                     if outline_only and p.stem.startswith(("level-", "goal-")) and "prompt" in read_json(p)]
    cost = read_json(root / "cost-latest.json")
    notes = read_json(root / "analysis-notes.json") if (root / "analysis-notes.json").exists() else {}
    notes = {key: [line for paragraph in value for line in (paragraph, "")] for key, value in notes.items()}
    analyses = build_analysis(runs, judges)
    write_json(root / "comparisons.json", analyses)
    lines = ["# MF-04 第六批：计划参数对照实验", "", f"基线：`{manifest['baseline']}`。开始：{manifest['started']}。",
             f"原始证据：`{root}`。模型：`{json.dumps(manifest['model'], ensure_ascii=False)}`。", "",
             "## 结论", "", *notes.get("conclusions", ["实验仍在执行；不据不完整数据下结论。"]), "",
             "## 方法与边界", "",
             "直接使用当前 PlanFlow：提交表单→必要追问→在资料阶段粘贴两条 GitHub 链接→轮廓→部分配置正式生成。",
             "8 个配置各重复 2 次。零基础/进阶、浅/深最终目标均安排正式知识点生成；共用 baseline，计划执行 16 个轮廓、6 个正式计划。",
             f"实际完成：{sum(r['outline_complete'] for r in runs.values())}/16 个轮廓、"
             f"{sum(r['formal_complete'] for r in runs.values())}/6 个正式计划；未完成产物不纳入完整正式对照。",
             "时间组合之外加两个单变量控制：只改每日上限、只改截止日期。其余目标、教材字节、日期起点、每周权重、特殊日、模型预设固定。",
             "各重复区组内以固定种子随机排序；资料首轮真实安全抓取，后续按 SHA256 校验重放，避免远端教材变动。",
             "只用用户给定资料，不调用搜索、不连接 MentorFlow 服务器。所有计划、草稿、正文在独立测试根目录；管理员根目录仅有授权的模型用量记账。",
             "正式实验的追问答复预先固定：两份教材链接、限定基础章节、Python 基本语法、对应最终目标的验收标准；未改产品提示词或模型温度。所有追问正文保留。",
             "先导实验发现不完整的模拟用户答复导致重复追问，已排除整批先导结果并重新做全部配置；先导调用仍全额计入总预算，原始文件保留在 pilot-runs / pilot-cost。",
             "量化差异使用两次均值之差，与两个配置中更大的重复极差比较，同时检查两次方向是否一致。n=2 仅是描述性证据，不能视为统计显著性。",
             "学习分钟数均是模型/计划模块的估算，未让真人计时。盲评衡量计划设计对条件的匹配，不是学习后的真实能力或学习效率。",
             "盲评以固定种子随机 A/B，每对正反各一次，两次重复共 4 票；隐藏表单、日期、上限、模型的自评说明和运行标识。",
             ("主要盲评统一只比轮廓，全部条件均有两次完整轮廓；已完成的第一轮a/d正式产物盲评单列为补充，不能混合计票。"
              if outline_only else "a/d 使用全部模块和全部生成知识点；其他只比轮廓，防止 baseline 有知识点而对照没有。") +
             "允许 tie，核查反转一致性。补跑只恢复失败调用，保留已发送的 A/B 顺序；不因票向或分歧而重抽答案。",
             "盲评使用同一厂商/模型，不是独立人类评审；强结论需两次内容方向一致，并结合重复波动和正反盲评，不能只凭模块数或长度。", "",
             "## 固定资料", ""]
    for source in manifest["sources"] or []:
        lines += [f"- {source['url']}：{source['chars']} 字符，{len(source['chapters'])} 个文档标题；SHA256 `{source['sha256']}`。",
                  "  读取告警：" + ("；".join(source["warnings"]) or "无")]
    lines += ["", "## 配置与执行", ""]
    lines += table(["配置", "水平", "日上限", "起止日期", "备注", "最终目标"], [
        [name, f["level"], f["daily_cap"], f["start"] + " 至 " + f["end"], f["notes"] or "空", f["final_goal"]]
        for name, f in manifest["configurations"].items()])
    lines += ["", "顺序：" + " → ".join(f"{n}-r{r}" for n, r in manifest["run_order"]), "", "## 全部指标", ""]
    rows = []
    for name, repeat in manifest["run_order"]:
        r = runs.get(f"{name}-r{repeat}")
        if not r:
            continue
        m = r["metrics"]
        ranges = m.get("ranges") or {}
        formal = r["formal_complete"]
        partial = not formal and bool(m["points"])
        rows.append([r["run_id"], m["question_count"], m["module_count"], ranges.get("total"), ranges.get("daily"),
                     ranges.get("over_cap"), len(m["chapter_coverage"]), m["point_count"] if formal else f"{m['point_count']}（未齐）" if partial else None,
                     m["point_minutes_sum"] if formal else f"{m['point_minutes_sum']}（未齐）" if partial else None,
                     r.get("detail", {}).get("plan", {}).get("total_minutes"),
                     m["point_summary_mean"] if formal else None,
                     r["draft"]["phase"]])
    lines += table(["运行", "追问数", "模块数", "总分钟(含12%复习预留)", "每天分钟", "超上限", "所引文档数",
                    "点数", "点分钟总计", "正式总分钟(含检查)", "摘要均长(字符)", "状态"], rows)
    lines += ["", "注：未正式生成用 —，不把未生成误记成零。文档覆盖按引用 spans 内真实 chunk 的首级章节统计，不能只看被合并的目录标题。", "",
              "## 参数差异与重复波动", ""]
    for item in analyses:
        lines += [f"### {item['comparison']}：{item['left']} → {item['right']}", ""]
        lines += table(["指标", "左两次", "右两次", "均值差(右−左)", "最大组内极差", "超重复波动", "两次同向"], [
            [k, v["left"], v["right"], v["delta"], v["within_max"], v["exceeds_repeat_variation"], v["same_direction"]]
            for k, v in item["numeric"].items()])
        lines += ["", f"盲评：右 {item['blind_right']} / 左 {item['blind_left']} / 平 {item['blind_tie']} / 错误 {item['blind_errors']}；"
                  f"A/B 选择 {item['blind_A']}/{item['blind_B']}；两次正反一致 {item['reverse_consistent']}。", ""]
    lines += ["## 每次运行的完整模块、追问与知识点", ""]
    for rid, r in sorted(runs.items()):
        m = r["metrics"]
        lines += [f"### {rid}", "", f"核对后目标：{r['draft'].get('goal', '')}", "",
                  "追问：" + ("\n\n".join(m["questions"]) or "无"), "", "模块：", ""]
        lines += table(["标题", "摘要", "分钟区间", "目录引用"], [
            [mod["title"], mod["summary"], mod["minutes"], "；".join(mod["ref_titles"])] for mod in m["modules"]])
        lines += ["", "真实引用文档：", "", *[f"- {c}" for c in m["chapter_coverage"]], ""]
        if m["points"]:
            lines += ["正式知识点：" if r["formal_complete"] else "已成功模块的知识点（计划未完成，禁止当完整正式产物比较）：", ""]
            mods = [mod for stage in r["draft"]["outline"]["stages"] for mod in stage["modules"]]
            for mod in mods:
                lines += [f"模块：{mod['title']}", ""]
                lines += table(["标题", "摘要", "分钟", "摘要字符数"], [[p["title"], p["summary"], p["minutes"], len(p["summary"])]
                    for p in m["points"] if p["module_id"] == mod["id"]])
                lines += [""]
            lines += ["", "正式计划日上限核对：`" + json.dumps(r.get("detail", {}).get("daily"), ensure_ascii=False) + "`。", ""]
            lines += ["知识点实际引用的文档：", "", *[f"- {c}" for c in m["point_chapter_coverage"]], ""]
        failures = [read_json(p) for p in (root / "runs" / rid / "failed-attempts").glob("*.json")]
        if failures:
            lines += ["保留的前次失败：" + "、".join(f.get("error_code", "未完成") for f in failures) +
                      "。按原计划 ID 和原参数恢复，只补未完成操作，没有替换已经有效的轮廓。", ""]
        if r.get("error_code") or m["errors"]:
            lines += [f"错误记录（当前状态 {r['draft']['phase']}）：{r.get('error_code', '')}。{'；'.join(m['errors'])}", ""]
    lines += ["## 费用与完成范围", "",
              f"UsageLog.summary(group_by='purpose') 按本实验唯一前缀汇总：{cost['calls']} 次调用；"
              f"已计价金额 {cost['known_amounts']}；未计价调用 {cost['unpriced_calls']}；含未决预留的保守总额 {cost['liability']} CNY。",
              "费用来自实际 usage tokens 与仓库价格目录，包含缓存命中/未命中和输出；不是厂商结算账单。每次调用和每次运行后均落盘 summary；"
              "并发前以未命中输入 UTF-8 字节上界、最大输出、高峰价和10%余量预留，14.50元前拒绝下一笔，不将失败/缺失用量记零。",
              f"各调用原始请求和模型回答：`{root / 'calls'}`；逐次费用：`{root / 'cost'}`；原始用量汇总：`{root / 'cost-latest.json'}`。", "",
              "逐运行费用（CNY；同一运行的所有失败和重试累计；p2- 为正式实验，未带此前缀的配置运行是排除的先导）：", ""]
    lines += table(["运行/盲评", "调用数", "已计价", "未知调用", "未知保留额度", "保守总额"],
                   cost_breakdown([read_json(p) for p in (root / "calls").glob("*.json")], cost))
    lines += ["", "## 提示词/流程原因、建议与产品问题", "", *notes.get("causes_and_recommendations", ["待完整结果分析。"]), "",
              "## 问题留档（现象、原因、解决、验证）", "", *notes.get("issue_records", ["待验证结束。"]), "",
              "## 验证", "", *notes.get("validation", ["离线脚本检查和全量回归结果待填。"]), "",
              "## 盲评原始提示词与结果（全部正反对照）", "",
              "下面的内容为实验输入/模型输出记录，不是交付指令。每个条目含确切 A/B 顺序，便于核查位置偏差。", ""]
    for index, j in enumerate(judges + supplementary):
        if index == len(judges):
            lines += ["## 补充：已完成的第一轮正式产物盲评（与主要轮廓盲评分开）", "",
                      "第二轮零基础计划未完整生成，因此正式产物盲评只保留第一轮；不能据此声称完成了两次正式对照。", ""]
        if "prompt" not in j:
            lines += [f"- {j['id']}：尚未调用盲评（{j.get('error_code', '未执行')}）。", ""]
            continue
        lines += [f"<details><summary>{j['id']}：{j.get('selected', j.get('error_code', '无结果'))}</summary>", "",
                  "实际顺序：`" + json.dumps(j["order"]) + "`。", "", "系统提示词：", "```text", j["system"], "```", "",
                  "用户提示词：", "```text", j["prompt"], "```", "", "原始回答：", "```json", j.get("raw", json.dumps(j.get("error_code"))),
                  "```", "", "</details>", ""]
    attempts = [read_json(p) for folder in ("judge-attempts", "outline-judge-attempts")
                for p in sorted((root / folder).glob("*.json"))]
    attempts = [j for j in attempts if "prompt" in j]  # no-call stage checkpoints are not model responses
    if attempts:
        lines += ["## 盲评失败调用留档（不计票；重试保留原 A/B 顺序）", ""]
        for j in attempts:
            lines += [f"<details><summary>{j['id']}：{j.get('error_code', '无有效答案')}</summary>", "",
                      "实际顺序：`" + json.dumps(j["order"]) + "`。", "", "```text", j["system"], "\n" + j["prompt"], "```", "",
                      "原始回答或错误：", "```text", j.get("raw", j.get("error_code", "未返回可用文本")), "```", "", "</details>", ""]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(dict(report=str(output), runs=len(runs), judges=len(judges), bytes=output.stat().st_size), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=TEMP_ROOT / "report.md")
    args = parser.parse_args()
    report(args.root, args.output)

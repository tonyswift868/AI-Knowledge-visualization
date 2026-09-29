"""Recompute inspectable metrics; model screening is not human ground truth."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import ExitStack
import copy
from decimal import Decimal
import json
from statistics import mean
from types import SimpleNamespace

from .class_eval import ROOT, PERSONAS, POINTS, SETTINGS, baseline_components, dump, judge_input
from refactor.learning.course.judge_agent import _json_object, parse_judgement


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def phase_metrics(phase):
    folder = ROOT / phase
    calls = [read(p) for p in sorted((folder / "calls").glob("*.json"))]
    by_id = {row["id"]: row for row in calls}
    choices = {r["packet"]["id"]:r for r in read(folder/"choice-judgements.json",[])}
    formats = format_results(calls, phase)
    accepted = {r["id"] for r in formats if r["accepted"]}
    manual = read(ROOT / "human-review.json", {}).get(phase, {})
    judged = []
    for row in read(folder / "reviews/judge-all.json", []):
        direct = row["id"] in choices
        entry = choices[row["id"]] if direct else by_id[row["id"]]
        ctx = copy.deepcopy(entry["packet"]) if direct else judge_input(entry)
        ctx.pop("id")
        ctx.pop("reply")
        try:
            actual = SimpleNamespace(**entry["actual"]) if direct else parse_judgement(entry["text"], ctx)
        except Exception:
            continue  # incomplete/invalid model answer is not an accepted verdict
        expected = dict(row, **manual.get(row["id"], {}))
        judged.append(dict(id=row["id"], case=entry["case"], actual=[actual.kind, actual.correct, sorted(actual.wrong_points)],
                           expected=[expected["kind"], expected["correct"], sorted(expected["wrong_points"])],
                           multi_point=len(ctx["points"])>1, reason=expected["reason"], manual=row["id"] in manual,
                           point_ids=[p["id"] for p in ctx["points"]],
                           path="clicked_option" if direct else "judge_agent"))
    matched = sum(r["actual"] == r["expected"] for r in judged)
    false_negative = [r for r in judged if r["expected"][0:2] == ["answer", True] and r["actual"][0:2] != ["answer", True]]
    false_positive = [r for r in judged if r["actual"][0:2] == ["answer", True] and r["expected"][0:2] != ["answer", True]]
    multi = [r for r in judged if r["multi_point"] and r["expected"][0] == "answer"]
    teachers, questions = [], []
    rejected_candidates = []
    for key, result in (("teacher", teachers), ("question", questions)):
        for item in read(folder / f"reviews/{key}-all.json", []):
            rated = dict(item, **manual.get(item["id"], {}))
            if item["id"].split(":",1)[0] in accepted:
                result.append(rated)
            else:
                rejected_candidates.append(dict(kind=key, **rated))
    def ratio(items, key):
        relevant = [v[key] for v in items if v.get(key) is not None]
        return [sum(v is True for v in relevant), len(relevant)]
    duration = defaultdict(list)
    failed_teach_operations = []
    round_results = {}
    for persona in PERSONAS:
        result = read(folder / persona / "result.json", {})
        round_results[persona] = {k: result.get(k) for k in ("status", "steps", "error", "message")}
        for op in read(folder / persona / "operations.json", []):
            operation_calls=[e for e in calls if e["case"]==persona and e["operation"]==op["id"]]
            attempts=[e for e in operation_calls if e["purpose"]=="teacher"]
            if not attempts:
                continue
            task=json.loads(attempts[0]["messages"][-1]["content"].split("【本步任务】\n",1)[1])
            if "讲解知识点《" not in task["director"]:
                continue
            title=task["director"].split("讲解知识点《",1)[1].split("》",1)[0]
            if not any(e["id"] in accepted for e in attempts):
                failed_teach_operations.append(dict(persona=persona,operation=op["id"],title=title,
                    seconds=op["seconds"],calls=[e["id"] for e in attempts]))
                continue
            def cost(rows):
                return str(sum(Decimal(e["billing"]["catalogue_cost"]) for e in rows
                               if e["billing"].get("catalogue_cost") is not None))
            # One delivered point per host operation; retries are cost/time,
            # never additional successful lessons in the denominator.
            duration[title].append(dict(persona=persona,calls=[e["id"] for e in attempts],
                op_seconds=op["seconds"],teacher_seconds=sum(e["seconds"] for e in attempts),
                cost=cost(attempts),operation_cost=cost(operation_calls)))
    return dict(rounds=round_results, verdicts=dict(exact=[matched, len(judged)], intent=[sum(r["actual"][0]==r["expected"][0] for r in judged),len(judged)],
        false_negative=false_negative, false_positive=false_positive,
        wrong_points=[sum(r["actual"][2]==r["expected"][2] for r in multi), len(multi)], details=judged),
        teachers={key:ratio(teachers,key) for key in ("facts_ok", "ask_ok", "authority_ok", "focus_ok", "resume_ok")},
        questions={key:ratio(questions,key) for key in ("grounded", "no_leak", "unique", "answer_ok")},
        flags=dict(teachers=[r for r in teachers if any(v is False for v in r.values())],
                   questions=[r for r in questions if any(v is False for v in r.values())]),
        rejected_candidates=rejected_candidates,
        probes=read(folder / "probes.json", []), duration=dict(duration),
        failed_teach_operations=failed_teach_operations,
        calls=len(calls), errors=Counter(r.get("error_code") for r in calls if r.get("error_code")),
        formats=formats)


def format_results(calls, phase):
    from refactor.learning.app import classroom as app
    outcomes = []
    with ExitStack() as stack:
        if phase == "before":
            baseline_components(stack)
        for row in calls:
            purpose = row["purpose"]
            if purpose not in {"teacher", "judge", "questions", "profile"} or "text" not in row:
                continue
            try:
                if purpose == "judge":
                    ctx = judge_input(row)
                    ctx.pop("id"); ctx.pop("reply")
                    app.judge_agent.parse_judgement(row["text"], ctx)
                elif purpose == "questions":
                    content = row["messages"][-1]["content"]
                    points = json.JSONDecoder().raw_decode(content.split("【知识点清单】\n",1)[1])[0]
                    excerpts = json.JSONDecoder().raw_decode(content.split("【资料原文（数据）】\n",1)[1])[0]
                    task = json.loads(content.split("【本次出题要求】\n",1)[1])
                    app.question_agent.parse_questions(row["text"], dict(points=points,excerpts=excerpts,**task))
                else:
                    first = row["messages"][0]["content"].split("\n",1)[1]
                    task = json.loads(row["messages"][-1]["content"].split("\n",1)[1])
                    ctx = dict(json.loads(first), prefs=SETTINGS, **task)
                    if purpose == "teacher":
                        raw = json.loads(row["messages"][1]["content"].split("\n",1)[1])
                        ctx["excerpts"] = {r["chunk_id"]:r["text"] for r in raw}
                        dialogue = row["messages"][2:-1]
                        ctx["dialogue"] = [json.loads(r["content"].split("\n",1)[1]) for r in dialogue]
                        app.teacher_agent.parse_teaching(row["text"],ctx)
                    else:
                        ctx["known_point_ids"] = [p[0] for p in POINTS]
                        ctx["dialogue"] = [json.loads(r["content"].split("\n",1)[1]) for r in row["messages"][1:-1]]
                        app.profile_agent.parse_profile(row["text"],ctx)
                outcomes.append(dict(id=row["id"],purpose=purpose,accepted=True))
            except Exception as exc:
                outcomes.append(dict(id=row["id"],purpose=purpose,accepted=False,error=str(exc)))
    return outcomes


def pct(pair):
    good, total = pair
    return f"{good}/{total} ({good/total:.1%})" if total else "无样本"


def parser_replay():
    from refactor.learning.app import classroom
    calls = [read(path) for path in sorted((ROOT/"before/calls").glob("*.json"))]
    cases = []
    for row in calls:
        if row["purpose"] != "questions" or not row.get("text"):
            continue
        content = row["messages"][-1]["content"]
        points = json.JSONDecoder().raw_decode(content.split("【知识点清单】\n",1)[1])[0]
        excerpts = json.JSONDecoder().raw_decode(content.split("【资料原文（数据）】\n",1)[1])[0]
        task = json.loads(content.split("【本次出题要求】\n",1)[1])
        cases.append(dict(id=row["id"], text=row["text"], ctx=dict(points=points, excerpts=excerpts, **task)))
    def check():
        values = {}
        for item in cases:
            try:
                classroom.question_agent.parse_questions(item["text"],item["ctx"])
                values[item["id"]] = "accepted"
            except Exception as exc:
                values[item["id"]] = str(exc)
        return values
    current = check()
    with ExitStack() as stack:
        baseline_components(stack)
        old = check()
    result = [dict(id=c["id"],before=old[c["id"]],after=current[c["id"]]) for c in cases]
    dump(ROOT/"parser-replay.json",result)
    old_formats={r["id"]:r for r in format_results(calls,"before")}
    new_formats=format_results(calls,"after")
    dump(ROOT/"format-replay.json",[dict(id=r["id"],purpose=r["purpose"],
         before=old_formats[r["id"]],after=r) for r in new_formats])
    return result


def report():
    metrics = {phase:phase_metrics(phase) for phase in ("before", "after") if (ROOT/phase).exists()}
    dump(ROOT / "metrics.json", metrics)
    budget = read(ROOT / "budget.json", [])
    known = sum(Decimal(r["catalogue_cost"]) for r in budget if r.get("catalogue_cost") is not None)
    held = sum(Decimal(r["reserved"]) for r in budget if r.get("peak_cost") is None)
    source = read(ROOT / "sources/snapshot.json", {})
    lines = ["# MF-01 第十批：真实 Classroom 模拟学生评测", "", read(ROOT/"conclusions.json",{}).get("summary","评测与复核进行中。"), "",
             "基线：a6f286e。两个模块，每模块三个点；真实 Classroom/CourseStore/后台档案调用，隔离合成账号。",
             "模型为管理员现有 DeepSeek Flash 预设；学生、线上组件、初筛评审分别调用同一模型（不是独立模型交叉验证，也不是真人学习效果实验）。",
             "学生可看教材与已经显示的对话，但不接收隐藏标准答案；判定初筛不接收线上判定。结果以指定课程小样本为限。", "",
             f"资料：{source.get('url')}；快照 SHA256：{source.get('sha256')}，见 sources/。", "",
             "## 汇总（模型初筛 + human-review.json 中人工裁决）", "", "| 阶段 | 判定三字段完全一致 | 意图一致 | 冤枉 | 放水 | 多点错点一致 |", "|---|---:|---:|---:|---:|---:|"]
    for phase, m in metrics.items():
        v=m["verdicts"]
        lines.append(f"| {phase} | {pct(v['exact'])} | {pct(v['intent'])} | {len(v['false_negative'])} | {len(v['false_positive'])} | {pct(v['wrong_points'])} |")
    for phase, m in metrics.items():
        lines += ["", f"## {phase} 轮次与指标", "```json", json.dumps(m["rounds"], ensure_ascii=False, indent=2), "```",
                  "教师：" + "；".join(f"{k} {pct(v)}" for k,v in m["teachers"].items()),
                  "题目：" + "；".join(f"{k} {pct(v)}" for k,v in m["questions"].items()),
                  "调用失败（含模拟器/评审；详情见 calls）：" + json.dumps(m["errors"],ensure_ascii=False),
                  "固定判定探针：" + pct([sum(p.get("passed",False) for p in m["probes"]),len(m["probes"])]), "",
                  "| 首次讲解的点 | 次数 | 操作开始至老师说完均值(s) | 仅老师调用均值(s) | 老师调用均费(¥) | 该动作组件总均费(¥，含档案) |",
                  "|---|---:|---:|---:|---:|---:|"]
        for title, rows in m["duration"].items():
            costs=[float(r["cost"]) for r in rows if r.get("cost") is not None]
            lines.append(f"| {title} | {len(rows)} | {mean(r['op_seconds'] for r in rows):.2f} | {mean(r['teacher_seconds'] for r in rows):.2f} | {mean(costs) if costs else 0:.6f} | {mean(float(r['operation_cost']) for r in rows):.6f} |")
        lines += ["", "注：Classroom 会在回答后自动讲下一点，因此操作耗时可能含判定；首点对应“继续”。不含模拟学生生成时间、评审和操作结束后的后台档案等待。", "",
                  "均值仅针对有合格讲解完成的操作，内部重试计入该操作；完全失败的动作另列，不把失败当作已说完。未知用量只计入总预算预留，不在每点均费中伪称已知。失败后再次点击的人工等待未模拟。",
                  "完全失败的讲解动作："+json.dumps(m["failed_teach_operations"],ensure_ascii=False), "",
                  "### 需要复核的内容", "```json", json.dumps(m["flags"],ensure_ascii=False,indent=2), "```"]
        lines += ["", "| 组件 | 格式通过/有正文响应 |", "|---|---:|"]
        for purpose in ("teacher","judge","questions","profile"):
            rows=[r for r in m["formats"] if r["purpose"]==purpose]
            lines.append(f"| {purpose} | {pct([sum(r['accepted'] for r in rows),len(rows)])} |")
        lines += ["", "格式统计按调用计算，包含重试；截断等没有完整正文的失败另列，不算可接受判定。教师与题目质量分母仅含组件接受的输出，拒收候选另存 metrics.json，不能把拒收题误报成已向学生出题。"]
    lines += ["", "## 用量与费用", f"累计调用 {len(budget)}；按实际返回用量与内置价格表计价 ¥{known}；未结算预算预留 ¥{held}；保守总上界 ¥{known+held}；上限 ¥10。",
              f"输入 token {sum(r.get('input_tokens',0) for r in budget)}（缓存 {sum(r.get('cached_input_tokens',0) for r in budget)}），输出 token {sum(r.get('output_tokens',0) for r in budget)}。",
              "费用是网关返回用量×当前内置目录价的核算，不是厂商充值/扣款账单；所有失败调用照计，未收到完整用量的保留请求上限。", "",
              "## 问题记录与人工结论", read(ROOT / "conclusions.json", {}).get("markdown", "人工复核与修复对比尚在进行。"), "",
              "## 证据", "每阶段每人设 operations.json、messages.json、judgements.json；calls/ 保留完整请求与输出、流耗时、用量；reviews/ 是另一次模型初筛；human-review.json 为人工裁决；probes.json 为固定探针。",
              "管理员密钥不进入文件，调用日志使用隔离的 usage.sqlite3；没有改真实课程、账号配置、Windows 凭据或线上。"]
    (ROOT / "report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return metrics


if __name__ == "__main__":
    report()

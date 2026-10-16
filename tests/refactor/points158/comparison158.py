"""Build an offline old/new review page and check regrouping invariants.

Inputs are the read-only ``snapshot.json`` and the ``apply-ready.json`` proposal
produced for 158-MF06. This script has no product imports and never writes a
database. Run with ``python -B .../comparison158.py --snapshot ... --apply ...
--output ...``.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from html import escape
import json
from pathlib import Path
import re
from typing import Any


def _chunks(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = snapshot.get("chunk_map")
    if isinstance(value, dict):
        return value
    return {row["id"]: row for row in snapshot.get("chunks", []) if isinstance(row, dict) and row.get("id")}


def _modules(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["module_id"]: m for m in snapshot.get("modules", [])}


def _incoming(apply_ready: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["module_id"]: m for m in apply_ready.get("modules", [])}


def _subpoints(points: list[dict[str, Any]]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    result: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for point in points:
        for sub in point.get("subpoints", []) or []:
            if isinstance(sub, dict) and isinstance(sub.get("id"), str):
                result[sub["id"]].append((point.get("id", ""), sub))
    return result


def title_fragment_reasons(title: str) -> list[str]:
    """Conservative checks for the visibly broken legacy heading patterns."""
    text = str(title or "").strip()
    reasons = []
    if re.match(r"^(?:\d{1,3}\s*[.、)）]|[一二三四五六七八九十]+\s*[、.）])", text):
        reasons.append("标题以列表序号开头")
    if text.startswith(("**", "__", "`", "|", "- ", "> ", "* ")) or "|" in text:
        reasons.append("标题残留 Markdown/表格符号")
    if text.endswith(("...", "…")):
        reasons.append("标题以省略号截断")
    # Known truncations in the supplied Java Guide snapshot. A generic test
    # cannot reliably distinguish a complete brand (e.g. “Zuul”) from a cut
    # word without a language model, so avoid flagging every English suffix.
    if re.search(r"(?i)(?:netfl|apoll|unive|snowfla)$", text):
        reasons.append("中英混合标题疑似截断在英文词中")
    return reasons


def validate_regroup(snapshot: dict[str, Any], apply_ready: dict[str, Any],
                     after_snapshot: dict[str, Any] | None = None,
                     allow_partial: bool = False) -> dict[str, Any]:
    """Validate exact subpoint conservation and, if supplied, the applied copy.

    Every old subpoint (including currently protected points) must occur once
    in the proposal. Protection is a swap-time decision in restructure.py, so
    a later ``after_snapshot`` may retain a protected old point instead of
    applying its proposed destination.
    """
    issues: list[str] = []
    old_modules = _modules(snapshot)
    new_modules = _incoming(apply_ready)
    if apply_ready.get("plan_id") != snapshot.get("plan_id"):
        issues.append("plan_id 不一致")
    if apply_ready.get("complete") is not True and not allow_partial:
        issues.append("apply-ready 未标记 complete=true")
    old_ids = set(old_modules)
    if (set(new_modules) != old_ids if not allow_partial else
            not new_modules or not set(new_modules) <= old_ids):
        issues.append("模块范围与快照不一致")

    module_report = []
    title_issues: list[dict[str, str]] = []
    for mid, old in old_modules.items():
        new = new_modules.get(mid)
        if new is None:
            continue
        old_points = old.get("old_points", old.get("points", []))
        replace_ids = new.get("replace_point_ids", [])
        if len(replace_ids) != len(set(replace_ids)):
            issues.append(f"{mid}: replace_point_ids 有重复")
        if set(replace_ids) != {p["id"] for p in old_points}:
            issues.append(f"{mid}: replace_point_ids 未覆盖快照中的全部模块旧点")
        expected = {s["id"]: (p, s) for p in old_points for s in (p.get("subpoints", []) or []) if s.get("id")}
        protected_point_ids = set((snapshot.get("protection") or {}).keys()) | set(snapshot.get("protected_ids", []))
        proposed_points = new.get("points", [])
        proposed = _subpoints(proposed_points)
        counts = Counter({sid: len(rows) for sid, rows in proposed.items()})
        missing = sorted(set(expected) - set(counts))
        repeated = sorted(sid for sid, count in counts.items() if count != 1)
        unknown = sorted(set(counts) - set(expected))
        if missing:
            issues.append(f"{mid}: proposal 丢失小要点 {missing}")
        if repeated:
            issues.append(f"{mid}: proposal 重复小要点 {repeated}")
        if unknown:
            issues.append(f"{mid}: proposal 引入快照外小要点 {unknown}")
        for sid in sorted(set(expected) & set(counts)):
            old_point, old_sub = expected[sid]
            new_point_id, new_sub = proposed[sid][0]
            if old_sub.get("minutes") != new_sub.get("minutes"):
                issues.append(f"{mid}: {sid} 分钟改变")
            if old_sub.get("chunk_ids", []) != new_sub.get("chunk_ids", []):
                issues.append(f"{mid}: {sid} chunk_ids 改变")
            if old_sub.get("notes", []) != new_sub.get("notes", []):
                issues.append(f"{mid}: {sid} notes 改变")
            for cid in [*new_sub.get("chunk_ids", []), *(n.get("chunk_id") for n in new_sub.get("notes", []) or [])]:
                if cid not in _chunks(snapshot):
                    issues.append(f"{mid}: {sid} 引用快照中不存在的 chunk {cid}")
        for point in proposed_points:
            subs = point.get("subpoints", []) or []
            if type(point.get("minutes")) is not int or point["minutes"] != sum(int(s.get("minutes", 0)) for s in subs):
                issues.append(f"{mid}: 新点 {point.get('id')} 分钟不等于小要点之和")
            active_subs = [s for s in subs if expected.get(s.get("id"), (None, None))[0]
                           and expected[s["id"]][0].get("id") not in protected_point_ids]
            # Ignore title suggestions attached only to protected material:
            # those subs are deliberately removed from effective new groups.
            for title in ([point.get("title", "")] + [s.get("title", "") for s in active_subs]
                          if active_subs else []):
                for reason in title_fragment_reasons(title):
                    title_issues.append({"module_id": mid, "title": str(title), "reason": reason})

        questions = old.get("questions", []) or []
        for question in questions:
            sid = question.get("subpoint_id")
            if sid and counts.get(sid, 0) != 1:
                issues.append(f"{mid}: 题目 {question.get('id')} 的 subpoint_id 未唯一保留")

        old_minutes = sum(int(p.get("minutes", 0)) for p in old_points)
        retained_points = [p for p in old_points if p.get("id") in protected_point_ids]
        generated_points = []
        for point in proposed_points:
            active = [s for s in (point.get("subpoints", []) or [])
                      if expected.get(s.get("id"), (None, None))[0]
                      and expected[s["id"]][0].get("id") not in protected_point_ids]
            if active:
                generated_points.append({
                    "id": point.get("id"), "title": point.get("title", ""),
                    "subpoint_count": len(active),
                    "minutes": sum(int(s.get("minutes", 0)) for s in active),
                })
        retained_minutes = sum(int(p.get("minutes", 0)) for p in retained_points)
        generated_minutes = sum(p["minutes"] for p in generated_points)
        effective_subpoint_count = sum(p["subpoint_count"] for p in generated_points) + sum(
            len(p.get("subpoints", []) or []) for p in retained_points)
        module_report.append({
            "module_id": mid,
            "title": old.get("title", ""),
            "old_points": len(old_points),
            "proposed_group_count": len(proposed_points),
            "generated_new_count": len(generated_points),
            "retained_count": len(retained_points),
            "effective_new_points": len(generated_points) + len(retained_points),
            "old_subpoints": len(expected),
            "new_subpoints": effective_subpoint_count,
            "old_minutes": old_minutes,
            "new_minutes": retained_minutes + generated_minutes,
            "question_count": len(questions),
            "groups": generated_points,
            "retained_minutes": retained_minutes,
            "generated_minutes": generated_minutes,
            "missing_subpoint_ids": missing,
            "duplicate_subpoint_ids": repeated,
            "unknown_subpoint_ids": unknown,
        })

    applied_report = None
    if after_snapshot is not None:
        applied_report = _validate_applied(snapshot, after_snapshot, issues)

    review = apply_ready.get("quality_review") if isinstance(apply_ready.get("quality_review"), dict) else {}
    manual_description = review.get("manual")
    manual_theme_review = {
        "status": "recorded" if isinstance(manual_description, str) and manual_description.strip()
        else "not_evaluated_by_automated_checker",
        "passed": review.get("passed") if isinstance(review.get("passed"), bool) else None,
        "source": manual_description or "",
        "scope": review.get("scope", ""),
    }

    return {
        "passed": not issues and not title_issues,
        "automatic_validation": {
            "passed": not issues and not title_issues,
            "checks": [
                "计划和模块身份/范围",
                "replace_point_ids覆盖快照旧点",
                "旧小要点恰好出现一次，无缺失、重复或未知ID",
                "notes、chunk_ids、分钟和题目subpoint_id保全",
                "引用chunk存在且分钟总量一致",
                "保护点过滤后的标题碎片检查",
                "提供换入后快照时核对进度、题目、账本和保护点",
            ],
            "issues": issues,
            "title_issues": title_issues,
        },
        "manual_theme_review": manual_theme_review,
        "issues": issues,
        "title_issues": title_issues,
        "modules": module_report,
        "applied": applied_report,
    }


def _validate_applied(before: dict[str, Any], after: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    """Check a post-apply SQLite export: questions and learning facts survive."""
    before_modules, after_modules = _modules(before), _modules(after)
    preserved: list[str] = []
    for mid, old in before_modules.items():
        new = after_modules.get(mid)
        if not new:
            issues.append(f"换入后缺少模块 {mid}")
            continue
        old_by_id = {p["id"]: p for p in old.get("old_points", old.get("points", []))}
        new_by_id = {p["id"]: p for p in new.get("old_points", new.get("points", []))}
        # Protected points keep identity and all point data verbatim.
        protected = before.get("protection", {})
        for pid in protected:
            if pid in old_by_id and old_by_id[pid] != new_by_id.get(pid):
                issues.append(f"换入后受保护点 {pid} 改变")
            elif pid in old_by_id:
                preserved.append(pid)
    old_q = {q["id"]: q for q in before.get("question_bank", [])}
    new_q = {q["id"]: q for q in after.get("question_bank", [])}
    if set(old_q) != set(new_q):
        issues.append("换入前后题目 ID 集合不一致")
    for qid in set(old_q) & set(new_q):
        left, right = dict(old_q[qid]), dict(new_q[qid])
        # Parent point_ids are expected to move with subpoint_id; every other
        # field, including question content and its stable subpoint_id, stays.
        left.pop("point_ids", None)
        right.pop("point_ids", None)
        if left != right:
            issues.append(f"换入后题目 {qid} 内容或 subpoint_id 改变")
    after_subs = {}
    after_parents = {}
    for module in after.get("modules", []):
        for point in module.get("old_points", module.get("points", [])):
            for sub in point.get("subpoints", []) or []:
                after_subs[sub.get("id")] = sub
                after_parents[sub.get("id")] = point.get("id")
    for qid, question in new_q.items():
        sid = question.get("subpoint_id")
        if sid and sid not in after_subs:
            issues.append(f"换入后题目 {qid} 的 subpoint_id 不存在")
        if sid and question.get("point_ids") and after_parents.get(sid) not in question["point_ids"]:
            issues.append(f"换入后题目 {qid} 未随 subpoint_id 挂到新大点")
    old_credits = before.get("credits", [])
    new_credits = after.get("credits", [])
    if old_credits != new_credits:
        issues.append("换入前后学习记录/credits 改变")
    if before.get("current_position") != after.get("current_position"):
        issues.append("换入前后课堂位置改变")
    if before.get("mastery_modules") != after.get("mastery_modules"):
        issues.append("换入前后模块掌握状态改变")
    return {"protected_points": sorted(preserved), "question_count": len(old_q),
            "credits_count": len(old_credits)}


def comparison_payload(snapshot: dict[str, Any], apply_ready: dict[str, Any],
                       after_snapshot: dict[str, Any] | None = None,
                       allow_partial: bool = False) -> dict[str, Any]:
    report = validate_regroup(snapshot, apply_ready, after_snapshot, allow_partial=allow_partial)
    if report["issues"] or (report["title_issues"] and not allow_partial):
        raise ValueError("质量检查失败：" + "；".join(report["issues"] + [f"标题疑似碎片：{x['title']}（{x['reason']}）" for x in report["title_issues"]]))
    chunks = _chunks(snapshot)
    protections = snapshot.get("protection", {}) or {}
    proposal_modules = _incoming(apply_ready)
    result_modules = []
    for old in snapshot.get("modules", []):
        mid = old["module_id"]
        if mid not in proposal_modules:
            continue
        proposal = proposal_modules[mid]
        old_points = old.get("old_points", old.get("points", []))
        replace_ids = set(proposal.get("replace_point_ids", []))
        protected_ids = set(protections) & replace_ids
        destinations: dict[str, list[dict[str, str]]] = defaultdict(list)
        # For the displayed effective result, a protected old point remains as
        # itself; its proposed subpoints are removed from incoming groups.
        displayed = []
        for point in proposal.get("points", []):
            kept_subs = []
            for sub in point.get("subpoints", []) or []:
                old_owner = next((p["id"] for p in old_points if any(s.get("id") == sub.get("id") for s in p.get("subpoints", []) or [])), None)
                if old_owner in protected_ids:
                    destinations[sub["id"]].append({"id": old_owner, "title": next(p["title"] for p in old_points if p["id"] == old_owner), "kind": "保留旧点"})
                    continue
                kept_subs.append(sub)
                for p in old_points:
                    if any(s.get("id") == sub.get("id") for s in p.get("subpoints", []) or []):
                        destinations[sub["id"]].append({"id": point["id"], "title": point.get("title", ""), "kind": "新知识点"})
                        break
            if kept_subs:
                rendered = dict(point, subpoints=kept_subs,
                                minutes=sum(int(s.get("minutes", 0)) for s in kept_subs))
                displayed.append(rendered)
        for point in old_points:
            if point["id"] in protected_ids or point["id"] not in replace_ids:
                displayed.append(dict(point, preserved=True))
                for sub in point.get("subpoints", []) or []:
                    destinations[sub.get("id", "")].append({"id": point["id"], "title": point.get("title", ""), "kind": "保留旧点"})

        old_view = []
        for point in old_points:
            dests = []
            if point["id"] in protected_ids or point["id"] not in replace_ids:
                dests.append({"id": point["id"], "title": point.get("title", ""), "kind": "保留原点"})
            else:
                for sub in point.get("subpoints", []) or []:
                    for dest in destinations.get(sub.get("id", ""), []):
                        if dest not in dests:
                            dests.append(dest)
            old_view.append({"id": point["id"], "title": point.get("title", ""),
                             "position": point.get("position"), "minutes": point.get("minutes", 0),
                             "protected": point["id"] in protections,
                             "subpoints": [s.get("title", "") for s in point.get("subpoints", []) or []],
                             "destinations": dests})

        new_view = []
        for point in sorted(displayed, key=lambda p: (p.get("position", 10**9), p.get("id", ""))):
            new_view.append({
                "id": point.get("id", ""), "title": point.get("title", ""),
                "goal": point.get("goal", point.get("summary", "")),
                "minutes": int(point.get("minutes", 0)), "preserved": bool(point.get("preserved")),
                "subpoints": [{
                    "id": s.get("id", ""), "title": s.get("title", ""), "minutes": s.get("minutes", 0),
                    "notes": [{"text": n.get("text", ""), "chunk_id": n.get("chunk_id", "")} for n in s.get("notes", []) or []],
                    "chunk_ids": s.get("chunk_ids", []),
                } for s in point.get("subpoints", []) or []],
            })
        old_minutes = sum(int(p.get("minutes", 0)) for p in old_points)
        new_minutes = sum(int(p.get("minutes", 0)) for p in displayed)
        questions = old.get("questions", []) or []
        result_modules.append({
            "module_id": mid, "title": old.get("title", ""), "position": old.get("position", 0),
            "old_points": old_view, "new_points": new_view,
            "old_minutes": old_minutes, "new_minutes": new_minutes,
            "old_question_count": len(questions), "new_question_count": len(questions),
            "old_subpoint_count": sum(len(p.get("subpoints", []) or []) for p in old_points),
            "new_subpoint_count": sum(len(p["subpoints"]) for p in new_view),
        })
    return {"plan_id": snapshot.get("plan_id"), "complete": apply_ready.get("complete") is True,
            "store_interface_status": apply_ready.get("store_interface_status", ""),
            "modules": result_modules,
            "chunks": {cid: {"title": c.get("title") or c.get("source_title") or "原文", "text": c.get("text", c.get("content", "")),
                             "source": c.get("url") or c.get("source_url") or ""}
                       for cid, c in chunks.items()}, "quality": report}


_PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Java Guide 知识点重组对照</title>
<style>
:root{color-scheme:light;--bg:#f5f4f0;--paper:#fff;--ink:#25352e;--muted:#64716c;--line:#dbe2dc;--accent:#256b50;--soft:#e9f4ed;--warn:#8e4c17}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 "Microsoft YaHei","Segoe UI",sans-serif}body.dark{color-scheme:dark;--bg:#18221e;--paper:#202e27;--ink:#e6eee8;--muted:#b0c1b5;--line:#405449;--accent:#9bd6b4;--soft:#2b4638;--warn:#f4c289}header{padding:28px max(24px,calc((100% - 1500px)/2));background:var(--paper);border-bottom:1px solid var(--line)}h1{font-size:27px;margin:6px 0}.muted{color:var(--muted)}button{font:inherit;color:inherit;cursor:pointer;background:var(--paper);border:1px solid var(--line);border-radius:7px;padding:6px 11px}button:focus-visible{outline:3px solid var(--accent)}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:18px}.metric,.module{background:var(--bg);padding:12px 16px;border-radius:9px}.metric b{display:block;font-size:17px}.metric span{font-size:12px;color:var(--muted)}main{max-width:1540px;margin:auto;padding:22px}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}.tabs button.active{background:var(--accent);color:white}.cols{display:grid;grid-template-columns:minmax(280px,.9fr) minmax(500px,1.8fr);gap:18px}.panel{background:var(--paper);border:1px solid var(--line);border-radius:12px;overflow:auto;max-height:calc(100vh - 250px)}.panel h2{position:sticky;top:0;background:var(--paper);margin:0;padding:15px;border-bottom:1px solid var(--line);z-index:1}.item{margin:10px;padding:12px;border:1px solid var(--line);border-radius:8px}.item button{width:100%;text-align:left}.badge{font-size:12px;color:var(--accent);background:var(--soft);border-radius:4px;padding:2px 7px}.dest{display:inline-block;margin:5px 3px;padding:3px 7px;background:var(--soft);border:0}.newpoint{margin:10px;padding:0;border:1px solid var(--line);border-radius:9px;background:var(--paper)}.newpoint>summary{cursor:pointer;padding:13px;font-weight:650}.body{padding:0 16px 16px}.sub{border-top:1px solid var(--line);padding:10px 0}.note{white-space:pre-wrap}.cite{width:auto!important;padding:1px 6px!important;color:var(--accent)}dialog{max-width:850px;width:calc(100% - 30px);max-height:86vh;background:var(--paper);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:20px}dialog::backdrop{background:#0008}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:60vh;overflow:auto;background:var(--bg);padding:14px;border-radius:7px}.protected{color:var(--warn)}.highlight{box-shadow:0 0 0 3px var(--accent)}@media(max-width:850px){.cols{grid-template-columns:1fr}.panel{max-height:55vh}}</style></head><body>
<header><button id="theme">切换亮暗</button><h1>Java Guide · 知识点重组对照</h1><p id="status" class="muted"></p><div class="metrics" id="metrics"></div><details id="title-warning" hidden><summary>疑似碎片标题清单</summary><ul id="title-warning-list"></ul></details></header><main><nav class="tabs" id="tabs"></nav><section class="module"><h2 id="title"></h2><div id="stats" class="muted"></div></section><div class="cols"><section class="panel"><h2>旧知识点 · 点去向可跳转</h2><div id="old"></div></section><section class="panel"><h2>换入后的主题知识点</h2><div id="fresh"></div></section></div></main><dialog id="src"><button id="close">关闭</button><h2 id="src-title"></h2><p id="src-meta" class="muted"></p><pre id="src-text"></pre></dialog>
<script type="application/json" id="data">__DATA__</script><script>
const d=JSON.parse(document.getElementById('data').textContent),$=x=>document.getElementById(x),el=(t,c,s)=>{let n=document.createElement(t);if(c)n.className=c;if(s!==undefined)n.textContent=s;return n};let current=0;$('status').textContent=d.complete?'完整提案。尚未换入任何用户数据。':'阶段稿：只包含已完成的模块，未换入用户数据。';if(d.store_interface_status){$('status').textContent+=' '+d.store_interface_status}if(d.quality.title_issues.length){$('title-warning').hidden=false;$('status').textContent+=' 标题检查发现 '+d.quality.title_issues.length+' 项，请在清单中核对。';for(const x of d.quality.title_issues){$('title-warning-list').append(el('li','',x.title+' · '+x.reason))}}
const all=d.modules.map(m=>({title:m.title,from:`${m.old_points.length} → ${m.new_points.length} 点；${m.old_minutes} → ${m.new_minutes} 分钟`}));for(const m of all){let e=el('div','metric');e.append(el('b','',m.title),el('span','',m.from));$('metrics').append(e)}
function cite(id){let b=el('button','cite','查看原文 '+id);b.onclick=()=>{let c=d.chunks[id]||{};$('src-title').textContent=c.title||'原文出处';$('src-meta').textContent=(c.source?c.source+' · ':'')+id;$('src-text').textContent=c.text||'快照未包含该 chunk 的正文';$('src').showModal()};return b}
function goto(id){let n=document.getElementById('np-'+id);if(!n)return;n.open=true;n.scrollIntoView({behavior:'smooth',block:'center'});n.classList.add('highlight');setTimeout(()=>n.classList.remove('highlight'),1800)}
function draw(){let m=d.modules[current];$('title').textContent=`第 ${m.position+1} 模块 · ${m.title}`;$('stats').textContent=`旧点 ${m.old_points.length} → 新点 ${m.new_points.length} · 小要点 ${m.old_subpoint_count} → ${m.new_subpoint_count} · 题目 ${m.old_question_count} → ${m.new_question_count} · 学习分钟 ${m.old_minutes} → ${m.new_minutes}`;let old=$('old'),fresh=$('fresh');old.replaceChildren();fresh.replaceChildren();for(const p of m.old_points){let a=el('article','item'),b=el('button','',`${String((p.position||0)+1).padStart(2,'0')} · ${p.title}`);a.append(b,el('span',p.protected?'badge protected':'badge',p.protected?'保护中 · 换入时保留':'旧点'));for(const dest of p.destinations||[]){let x=el('button','dest',`${dest.kind} · ${dest.title}`);x.onclick=()=>goto(dest.id);a.append(x)}old.append(a)}for(const p of m.new_points){let details=el('details','newpoint');details.id='np-'+p.id;let sum=el('summary','',`${p.title} · ${p.minutes} 分钟${p.preserved?' · 保留':''}`);details.append(sum);let body=el('div','body');body.append(el('p','',p.goal||''));for(const s of p.subpoints){let sub=el('section','sub');sub.append(el('h3','',`${s.title} · ${s.minutes} 分钟`));for(const n of s.notes){let row=el('p','note',n.text);row.append(cite(n.chunk_id));sub.append(row)}let refs=el('p','muted','小要点原文：');for(const id of s.chunk_ids||[])refs.append(cite(id));sub.append(refs);body.append(sub)}details.append(body);fresh.append(details)}}
d.modules.forEach((m,i)=>{let b=el('button','',`第 ${m.position+1} 模块`);b.onclick=()=>{current=i;[...$('tabs').children].forEach((x,j)=>x.classList.toggle('active',i===j));draw()};$('tabs').append(b)});$('theme').onclick=()=>document.body.classList.toggle('dark');$('close').onclick=()=>$('src').close();if(d.modules.length){$('tabs').firstChild.classList.add('active');draw()}
</script></body></html>'''


def render_html(snapshot: dict[str, Any], apply_ready: dict[str, Any],
                after_snapshot: dict[str, Any] | None = None,
                allow_partial: bool = False) -> str:
    data = comparison_payload(snapshot, apply_ready, after_snapshot, allow_partial=allow_partial)
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # Prevent injected script termination; everything else is inserted through
    # textContent by the page renderer.
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return _PAGE.replace("__DATA__", encoded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--apply", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--after-snapshot", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--allow-partial", action="store_true", help="仅用于显式标注未完成阶段稿的审阅")
    args = parser.parse_args(argv)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    apply_ready = json.loads(args.apply.read_text(encoding="utf-8"))
    after = json.loads(args.after_snapshot.read_text(encoding="utf-8")) if args.after_snapshot else None
    quality = validate_regroup(snapshot, apply_ready, after, allow_partial=args.allow_partial)
    if quality["issues"] or (quality["title_issues"] and not args.allow_partial):
        raise SystemExit(json.dumps(quality, ensure_ascii=False, indent=2))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(snapshot, apply_ready, after, allow_partial=args.allow_partial), encoding="utf-8", newline="\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({"html": str(args.output), "quality": quality}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

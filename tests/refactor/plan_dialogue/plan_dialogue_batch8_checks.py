"""Checks for Batch 8's single-turn, post-intent execution contract."""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .plan_dialogue_harness import modules


def _has_any(text, words):
    return any(str(word).casefold() in text.casefold() for word in words)


def _intent_payload(calls):
    for call in calls:
        if call.get("purpose") != "plan_intent" or not call.get("result"):
            continue
        text = call["result"].get("text", "")
        starts = [p for p in (text.find("{"), text.find("[")) if p >= 0]
        if not starts:
            continue
        try:
            value = json.JSONDecoder().raw_decode(text[min(starts):])[0]
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _intent_kinds(calls):
    payload = _intent_payload(calls)
    return [item.get("kind") for item in payload.get("intents", [])] if payload else None


def _reply(before, after):
    old = before["draft"]["messages"]
    return "\n".join(m.get("text", "") for m in after["draft"]["messages"][len(old):]
                       if m.get("role") == "ai")


def _chapter_numbers(ids):
    # The synthetic material has a source-level section (s1.1) before chapter
    # one (s1.2).  Scope ids are storage ids, so report the user-facing chapter
    # number rather than treating the source title as chapter one.
    return [int(match.group(1)) - 1 for value in ids
            for match in [re.search(r"\.(\d+)$", str(value))] if match and int(match.group(1)) > 1]


def _scope_chapters(snapshot, key):
    return _chapter_numbers((snapshot["draft"].get("scope") or {}).get(key, []))


def evaluate_batch8(case, before, after, calls, *, bridge_error=None):
    expected = case["expected"]
    checks = []

    def check(name, passed, want, actual):
        checks.append({"name": name, "passed": bool(passed), "expected": want, "actual": actual})

    kinds = _intent_kinds(calls)
    errors = [c.get("error_code") for c in calls if c.get("error_code")]
    check("product_call_succeeded", not bridge_error and not errors,
          "无产品/模型错误", bridge_error or errors or "ok")
    if "intent_kinds" in expected:
        check("intent_kinds", kinds == expected["intent_kinds"], expected["intent_kinds"], kinds)
    if expected.get("no_search"):
        searches = [c for c in calls if c.get("purpose") == "research" and c.get("enable_search")]
        check("no_search", not searches, 0, len(searches))
    old, new = before["draft"], after["draft"]
    if expected.get("content_unchanged"):
        keys = ("phase", "form", "goal", "focus", "sources", "scope", "outline", "estimate", "generation")
        changed = [key for key in keys if old.get(key) != new.get(key)]
        records_changed = before["records"] != after["records"]
        plan_changed = before["plan"] != after["plan"]
        check("content_unchanged", not changed and not records_changed and not plan_changed,
              "无业务数据变动", {"draft": changed, "records": records_changed, "plan": plan_changed})
    if "field" in expected:
        field, value = expected["field"], expected["value"]
        if isinstance(value, str):
            value = value.format_map(case.get("rendered_variables", {}))
        actual = new.get("form", {}).get(field)
        check("form_field", actual == value, {field: value}, {field: actual})
        check("form_changed", old.get("form", {}).get(field) != actual,
              "字段发生单回合更新", {"before": old.get("form", {}).get(field), "after": actual})
    if expected.get("scope_include_chapter_max") is not None:
        values = _scope_chapters(after, "include")
        check("scope_include", bool(values) and max(values) <= expected["scope_include_chapter_max"],
              expected["scope_include_chapter_max"], values)
    if expected.get("scope_exclude_chapter") is not None:
        values = _scope_chapters(after, "exclude")
        check("scope_exclude", expected["scope_exclude_chapter"] in values,
              expected["scope_exclude_chapter"], values)
    if expected.get("estimate_present"):
        check("estimate_present", bool(new.get("estimate")), "存在费用预估", new.get("estimate"))
    if expected.get("ready_operation"):
        old_rows = before["records"]["modules"]
        new_rows = after["records"]["modules"]
        titles = [row.get("title", "") for row in new_rows]
        if expected["ready_operation"] == "remove_charts":
            passed = len(new_rows) < len(old_rows) and not any(_has_any(t, ["图表", "可视化", "matplotlib"]) for t in titles)
        else:
            errors_i = [i for i, title in enumerate(titles) if "异常" in title]
            cleaning_i = [i for i, title in enumerate(titles) if "清洗" in title]
            passed = bool(errors_i and cleaning_i and min(errors_i) < min(cleaning_i))
        check("ready_modules", passed, expected["ready_operation"], titles)
    if "phase" in expected:
        check("phase", new.get("phase") == expected["phase"], expected["phase"], new.get("phase"))
    if "only_source_title" in expected:
        chosen = [c.get("title", "") for c in new.get("sources", []) if c.get("status") == "adopted"]
        check("only_selected_source", len(chosen) == 1 and expected["only_source_title"] in chosen[0],
              expected["only_source_title"], chosen)
    if "card_status" in expected:
        if "card_index" in expected:
            index = expected["card_index"]
            ids = [before["draft"]["sources"][index]["card_id"]] if index < len(before["draft"]["sources"]) else []
        else:
            ids = [c["card_id"] for c in before["draft"].get("sources", [])
                   if expected.get("card_title_pattern", "") in c.get("title", "")]
        statuses = [c.get("status") for c in new.get("sources", []) if c.get("card_id") in ids]
        check("card_status", bool(statuses) and all(s == expected["card_status"] for s in statuses),
              expected["card_status"], statuses)
    if expected.get("reply_any"):
        reply = _reply(before, after)
        check("reply_answers", _has_any(reply, expected["reply_any"]), expected["reply_any"], reply)
    if expected.get("no_generated_points"):
        count = len(after["records"].get("points", []))
        check("no_generated_points", count == 0, 0, count)
    return checks

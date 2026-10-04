"""Observable contract checks, with snapshots retained for human semantic review."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from refactor.learning.course import planner_agents
from .plan_dialogue_harness import modules

SUPPORTED = set("""
phase form_unchanged search_calls search_calls_min no_generated_points goal_any goal_none
questions_min questions_max future_research_any accepted_preserved rejected_preserved
new_cards_min recommendation_any new_adopted_min url_contains repo_calls url_no_tracking
repo_calls_min chapter_count_min import_chars_min repo_raw_prefix outline_version_delta
outline_operation plan_state_unchanged no_false_completion field_confirmation field_value
outline_chapters_max outline_excluded_chapter only_source_title card_title_pattern card_index
card_status ready_operation content_unchanged reply_any official_preferred no_false_form_change
""".split())


def has_any(text, words):
    return any(word.casefold() in text.casefold() for word in words)


def text_of(module):
    return " ".join([module.get("title", ""), module.get("summary", ""), *module.get("ref_titles", [])])


def evaluate(case, before, after, calls, repo_calls, *, intermediate=None, bridge_error=None):
    expected = case["expected"]
    unknown = set(expected) - SUPPORTED
    if unknown:
        raise ValueError("Unimplemented assertions: " + repr(sorted(unknown)))
    checks = []
    def check(name, passed, want, actual):
        checks.append(dict(name=name, passed=bool(passed), expected=want, actual=actual))

    old, new = before["draft"], after["draft"]
    replies = "\n".join(m["text"] for m in new["messages"][len(old["messages"]):] if m["role"] == "ai")
    model_errors = [c.get("error_code") for c in calls if c.get("error_code")]
    last_by_purpose = {c['purpose']: c for c in calls}
    unrecovered = [c.get('error_code') for c in last_by_purpose.values() if c.get('error_code')]
    check("product_call_succeeded", not bridge_error and not unrecovered and not any(
        m["kind"] == "error" for m in new["messages"][len(old["messages"]):]),
          "没有产品/模型错误", bridge_error or model_errors or replies)
    research = [c for c in calls if c["purpose"] == "research" and c["enable_search"]]
    for key in ("phase",):
        if key in expected:
            check(key, new[key] == expected[key], expected[key], new[key])
    if expected.get("form_unchanged"):
        check("form_unchanged", old["form"] == new["form"], old["form"], new["form"])
    if "search_calls" in expected:
        check("search_calls", len(research) == expected["search_calls"], expected["search_calls"], len(research))
    if "search_calls_min" in expected:
        check("search_calls_min", len(research) >= expected["search_calls_min"], expected["search_calls_min"], len(research))
        # Calling a tool-enabled model without any actual web search is not a
        # successful native-search test. Require provider-observed tool usage.
        counters = []
        for c in research:
            if c.get('result'):
                counters.append(c['result']['usage']['search_requests'])
            elif c.get('http_responses'):
                counters.append(sum(r['usage']['search_requests'] for r in c['http_responses']))
        count = sum(counters) if counters else None
        check("native_search_executed", count is not None and count > 0,
              "至少一次厂商原生搜索（未提供计量时不冒充零次）", count)
    if expected.get("no_generated_points"):
        count = len(after["records"]["points"])
        check("no_generated_points", count == 0, 0, count)
    if expected.get("plan_state_unchanged"):
        check("plan_state_unchanged", before["plan"]["state"] == after["plan"]["state"],
              before["plan"]["state"], after["plan"]["state"])
    for key in ("goal_any", "goal_none"):
        if key in expected:
            found = has_any(new["goal"], expected[key])
            check(key, found if key == "goal_any" else not found, expected[key], new["goal"])
    if "questions_min" in expected:
        answers = [planner_agents.parse_sufficiency(c["result"]["text"]) for c in calls
                   if c["purpose"] == "sufficiency" and c.get("result")]
        count = len(answers[-1]["questions"]) if answers else 0
        check("questions_count", expected["questions_min"] <= count <= expected["questions_max"],
              [expected["questions_min"], expected["questions_max"]], count)
    if "future_research_any" in expected and new['phase'] == 'sources':
        request = planner_agents.research_request(new["goal"], new["form"], new["focus"],
            accepted=[], rejected=[], round_no=1, instruction="")
        text = json.dumps(request, ensure_ascii=False)
        # Each side-information requirement must survive, not merely one.
        check("future_research_preferences", all(word in text for word in expected["future_research_any"]),
              expected["future_research_any"], request)
    elif 'future_research_any' in expected:
        check('preferences_not_yet_testable', False, '阶段推进后偏好仍进入检索请求', '仍在追问；不能由当前 focus 推断最终偏好丢失')
    for status, flag in (("adopted", "accepted_preserved"), ("rejected", "rejected_preserved")):
        if expected.get(flag):
            prev = {c["card_id"] for c in old["sources"] if c["status"] == status}
            current = {c["card_id"] for c in new["sources"] if c["status"] == status}
            check(flag, bool(prev) and prev <= current, sorted(prev), sorted(current))
            if status == "rejected":
                urls = {c["url"] for c in old["sources"] if c["status"] == status and c.get("url")}
                resurfaced = [c for c in new["sources"] if c["status"] == "new" and c.get("url") in urls]
                check("rejected_not_reshown", not resurfaced, [], resurfaced)
    old_ids = {c["card_id"] for c in old["sources"]}
    added = [c for c in new["sources"] if c["card_id"] not in old_ids]
    adopted = [c for c in added if c["status"] == "adopted"]
    for key, values in (("new_cards_min", [c for c in added if c["status"] == "new"]),
                        ("new_adopted_min", adopted)):
        if key in expected:
            check(key, len(values) >= expected[key], expected[key], len(values))
    if expected.get("recommendation_any"):
        suggestions = [s for c in research if c.get("result")
                           for s in planner_agents.parse_json(c["result"]["text"]).get('sources', [])]
        text = json.dumps(suggestions, ensure_ascii=False)
        check("recommendation_matches_request", has_any(text, expected["recommendation_any"]),
              expected["recommendation_any"], suggestions)
    urls = [c.get("url", "") for c in adopted]
    if expected.get('official_preferred'):
        candidates = [c.get('url', '') for c in added if c['status'] == 'new']
        def official(url):
            p = urlsplit(url)
            return p.hostname == 'docs.python.org' or (p.hostname == 'github.com' and
                p.path.startswith('/python/cpython/blob/') and '/Doc/' in p.path)
        matches = [official(url) for url in candidates]
        check('official_preferred', bool(matches) and matches[0] and sum(matches) >= (len(matches) + 1) // 2,
              '首项及至少一半为官方文档（含 CPython Doc 源码）；优先不等于仅此域名', candidates)
    if "url_contains" in expected:
        check("imported_url", any(expected["url_contains"] in u for u in urls), expected["url_contains"], urls)
    if expected.get("url_no_tracking"):
        check("url_no_tracking", bool(urls) and all("utm_" not in u.lower() for u in urls), "无 utm 参数", urls)
    if "repo_calls" in expected:
        check("repo_calls", len(repo_calls) == expected["repo_calls"], expected["repo_calls"], len(repo_calls))
    if "repo_calls_min" in expected:
        ok = [r for r in repo_calls if "error_type" not in r]
        check("repo_calls_min", len(ok) >= expected["repo_calls_min"], expected["repo_calls_min"], len(ok))
    source_ids = {c.get("source_id") for c in adopted}
    chunks = [c for c in after["records"]["source_chunks"] if c["source_id"] in source_ids]
    if "chapter_count_min" in expected:
        headings = sorted({tuple(c["section"])[0] for c in chunks if c.get("section")})
        check("chapter_count_min", len(headings) >= expected["chapter_count_min"], expected["chapter_count_min"], headings)
    if "import_chars_min" in expected:
        chars = sum(len(c["text"]) for c in chunks)
        check("import_chars_min", chars >= expected["import_chars_min"], expected["import_chars_min"], chars)
    if "repo_raw_prefix" in expected:
        paths = [urlsplit(r["url"]).path for r in repo_calls if urlsplit(r["url"]).hostname == "raw.githubusercontent.com"]
        check("repo_raw_prefix", bool(paths) and all(p.startswith(expected["repo_raw_prefix"]) for p in paths),
              expected["repo_raw_prefix"], paths)
    bm, am = modules(before), modules(after)
    if "outline_version_delta" in expected:
        delta = (new.get("outline") or {}).get("version", 0) - (old.get("outline") or {}).get("version", 0)
        check("outline_version_delta", delta == expected["outline_version_delta"], expected["outline_version_delta"], delta)
    if expected.get("outline_operation"):
        op = expected["outline_operation"]
        refs = [set(m["refs"]) for m in am]
        titles = [text_of(m) for m in am]
        if op == "split":
            selected = set(bm[0]["refs"])
            matched = sum(bool(selected & r) for r in refs)
            check("split_selected_module", len(am) > len(bm) and matched >= 2,
                  "模块数增加，至少两模块覆盖原选中模块的引用", {"before": len(bm), "after": len(am), "matching": matched, "titles": titles})
        if op in {"merge", "merge_remove_charts"}:
            target = set(bm[0]["refs"]) | set(bm[1]["refs"])
            check("merge_selected_modules", len(am) < len(bm) and any(target <= r for r in refs),
                  "模块数减少且一个模块覆盖两个选中模块引用", {"before": len(bm), "after": len(am), "titles": titles})
        if op == "reorder":
            scopes = [' '.join([m['title'], *m.get('ref_titles', [])]) for m in am]
            # A summary may say "prepares for cleaning" in the errors module;
            # topic identity comes from the module title / referenced chapter.
            errors = [i for i, t in enumerate(scopes) if "异常" in t]
            cleaning = [i for i, t in enumerate(scopes) if "清洗" in t]
            check("reorder_errors_before_cleaning", errors and cleaning and min(errors) < min(cleaning),
                  "异常处理在数据清洗之前", titles)
        if op == "practical":
            additions = [m for m in am if m not in bm and has_any(text_of(m), ["实战", "综合", "项目"])]
            check("added_practical_module", bool(additions) and len(am) > len(bm),
                  "新增综合实战模块", additions)
        if op in {"remove_charts", "merge_remove_charts"}:
            removed = not any(has_any(t, ["图表", "可视化", "matplotlib"]) for t in titles)
            check("removed_chart_scope", removed, "轮廓及引用不再包含图表", titles)
        if op == "slower":
            before_range = [sum(m["minutes"][i] for m in bm) for i in range(2)]
            after_range = [sum(m["minutes"][i] for m in am) for i in range(2)]
            check("slower_time_range", all(a >= b for a, b in zip(after_range, before_range)) and after_range != before_range,
                  "每次学习上限和日期不变，预估总时间增加", {"before": before_range, "after": after_range})
    if expected.get("no_false_completion"):
        false = re.findall(r"(?:已经|已)(?:为你|帮你)?(?:开始学习|开始计划|生成完成|生成了全部|改成每天\s*5)", replies)
        check("no_false_completion", not false, "不声称已开始/已生成/已把表单改为5分钟", {"matches": false, "reply": replies})
    if expected.get('no_false_form_change'):
        claims = re.findall(r'(?:已经|已)(?:按.{0,12}(?:要求|意思|请求))?(?:为你|帮你)?'
                            r'(?:修改|更改|调整|设置|更新|记入)(?:了)?(?:设置|日程|每天|每日|上限|时间|配置)', replies)
        claims += re.findall(r'已(?:经)?(?:将|把).{0,30}(?:改为|改成|调整为|设为)\s*5', replies)
        check('no_false_form_change', not claims, '表单未改变时不得声称已修改设置', {'matches': claims, 'reply': replies})

    # Future B contracts: these inspect durable state and the actual two turns,
    # never success inferred from an intent label or model prose alone.
    if "field_confirmation" in expected:
        field = expected["field_confirmation"]
        mid_form = intermediate["draft"]["form"] if intermediate else {}
        check("field_before_confirmation", mid_form.get(field) == old["form"][field],
              old["form"][field], mid_form.get(field))
        check("field_after_confirmation", new["form"].get(field) == expected["field_value"],
              expected["field_value"], new["form"].get(field))
    if "outline_chapters_max" in expected or "outline_excluded_chapter" in expected:
        headings = [t for m in am for t in m.get("ref_titles", [])]
        chapters = [int(n) for n in re.findall(r"第\s*(\d+)\s*章", " ".join(headings))]
        if "outline_chapters_max" in expected:
            check("chapter_scope", bool(chapters) and max(chapters) <= expected["outline_chapters_max"],
                  expected["outline_chapters_max"], chapters)
        else:
            check("chapter_excluded", bool(chapters) and expected["outline_excluded_chapter"] not in chapters,
                  expected["outline_excluded_chapter"], chapters)
    if "only_source_title" in expected:
        chosen = [c["title"] for c in new["sources"] if c["status"] == "adopted"]
        check("only_selected_source", len(chosen) == 1 and expected["only_source_title"] in chosen[0],
              expected["only_source_title"], chosen)
    if "card_status" in expected:
        if "card_index" in expected:
            selected_ids = [old["sources"][expected["card_index"]]["card_id"]]
        else:
            selected_ids = [c["card_id"] for c in old["sources"] if expected["card_title_pattern"] in c["title"]]
        statuses = [c["status"] for c in new["sources"] if c["card_id"] in selected_ids]
        check("card_status", bool(statuses) and all(s == expected["card_status"] for s in statuses),
              expected["card_status"], statuses)
    if "ready_operation" in expected:
        b, a = before["records"]["modules"], after["records"]["modules"]
        titles = [m["title"] for m in a]
        if expected["ready_operation"] == "remove_charts":
            passed = len(a) < len(b) and not any(has_any(t, ["图表", "可视化"]) for t in titles)
        else:
            errors = [i for i, t in enumerate(titles) if "异常" in t]
            cleaning = [i for i, t in enumerate(titles) if "清洗" in t]
            passed = bool(errors and cleaning and min(errors) < min(cleaning))
        check("ready_modules", passed, expected["ready_operation"], titles)
    if expected.get("content_unchanged"):
        changed = [k for k in ("form", "sources", "outline", "generation") if old[k] != new[k]]
        check("content_unchanged", not changed and before["records"] == after["records"], "无业务数据变动", changed)
    if "reply_any" in expected:
        check("reply_answers_question", has_any(replies, expected["reply_any"]), expected["reply_any"], replies)
    return checks

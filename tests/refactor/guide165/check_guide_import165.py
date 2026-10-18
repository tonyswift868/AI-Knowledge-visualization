"""Zero-cost inspection of a MentorFlow Guide import through the product importer.

Examples:
  python -m tests.refactor.guide165.check_guide_import165 --local D:/MentorFlow/.local/guide-export/mentorflow-guide --output D:/MentorFlow/.local/test-tmp/guide165/check
  python -m tests.refactor.guide165.check_guide_import165 --url https://github.com/tonyswift868/mentorflow-guide --output D:/MentorFlow/.local/test-tmp/guide165/check
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import threading
import uuid
from urllib.parse import unquote, urlparse

from refactor.learning.course import github_repo

SCRATCH = Path(r"D:\MentorFlow\.local\test-tmp")
DEFAULT_URL = "https://github.com/tonyswift868/mentorflow-guide"


def local_fetcher(directory: Path):
    """Serve local Markdown through github_repo.import_repo's normal fetch interface."""
    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("--local 必须指向导出目录")
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in (".md", ".markdown"))
    paths = [p.relative_to(root).as_posix() for p in files]
    by_path = dict(zip(paths, files))
    if not paths:
        raise ValueError("本地目录中没有 Markdown 文件")

    state = {"candidate_paths": paths, "fetched_paths": [], "tree_paths": paths}
    def fetch(url, cancel=None):
        parsed = urlparse(url)
        if parsed.netloc == "api.github.com" and parsed.path == "/repos/tonyswift868/mentorflow-guide":
            return json.dumps({"full_name": "tonyswift868/mentorflow-guide", "default_branch": "main",
                               "description": "MentorFlow Guide 学习资料"}, ensure_ascii=False).encode()
        if parsed.netloc == "api.github.com" and parsed.path == "/repos/tonyswift868/mentorflow-guide/git/trees/main":
            return json.dumps({"tree": [dict(path=p, type="blob", size=by_path[p].stat().st_size) for p in paths]},
                              ensure_ascii=False).encode()
        raw_prefix = "/tonyswift868/mentorflow-guide/main/"
        if parsed.netloc == "raw.githubusercontent.com" and parsed.path.startswith(raw_prefix):
            relative = unquote(parsed.path[len(raw_prefix):])
            try:
                body = by_path[relative].read_bytes()
                state["fetched_paths"].append(relative)
                return body
            except KeyError:
                raise FileNotFoundError(f"本地导出中没有 {relative}") from None
        raise ValueError(f"本地适配器拒绝非预期请求：{url}")
    return fetch, state


def inspect_source(source, fetched_files=()):
    blocks = source.document.blocks
    text = "\n".join(block.text for block in blocks)
    chapter_runs = [b.section[0] for i, b in enumerate(blocks)
                    if b.section and (i == 0 or not blocks[i-1].section or blocks[i-1].section[0] != b.section[0])]
    citation_matches = list(dict.fromkeys(m.group(0) for m in re.finditer(
        r"(?im)^\s*(?:#{1,6}\s*)?(?:学习路线\s*[：:]\s*)?(?:本(?:节|题|篇)出处|出处表?|参考文献|references|sources)\s*[:：]?\s*$"
        r"|\[[A-Za-z]{1,8}\d{1,3}(?:\s*[-–,，]\s*[A-Za-z]{1,8}\d{1,3})*\]", text)))
    return dict(chapters=chapter_runs,
                duplicate_chapters=sorted({x for x in chapter_runs if chapter_runs.count(x) > 1}),
                README_included=any(Path(p).name.casefold() in {"readme.md", "readme.markdown"} for p in fetched_files),
                citation_material_present=bool(citation_matches), citation_markers=citation_matches,
                mojibake=bool(re.search(r"(?:锟斤拷|�|Ã.|Â.|æ..|å..|ä..|ðŸ)", text)),
                warnings=list(source.document.warnings), char_count=source.document.char_count,
                merged_chapter_count=len(set(chapter_runs)))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local", type=Path, help="本地导出目录")
    mode.add_argument("--url", help="公开 GitHub 仓库链接，可含 /tree/<分支>/<目录>")
    mode.add_argument("--cached-source", type=Path,
                      help="使用此前真实导入且哈希核实的 VerifiedSource JSON，不重新抓取")
    parser.add_argument("--output", required=True, type=Path, help="证据目录，必须位于 D:/MentorFlow/.local/test-tmp 下")
    parser.add_argument("--import-only", action="store_true", help="只检查抓取/选文/合并，默认模式，零模型费用")
    parser.add_argument("--live", action="store_true", help="继续调用当前切点、讲义和原题流程；会产生模型费用")
    parser.add_argument("--account-id", default="guide165-live")
    parser.add_argument("--preset-root", type=Path, default=Path(r"D:\MentorFlowData\account-shell"))
    parser.add_argument("--budget", default="8")
    return parser


def _validated_live_budget(value):
    try:
        budget = float(value)
    except (TypeError, ValueError):
        raise ValueError("--budget 必须是人民币数字") from None
    if not 0 < budget <= 15:
        raise ValueError("本任务真实模型预算范围为大于 0 且不超过 15 元")
    return budget


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if not output.is_relative_to(SCRATCH.resolve()):
        parser.error(f"--output 必须位于 {SCRATCH}")
    if not args.import_only:
        args.import_only = not args.live
    if args.import_only and args.live:
        parser.error("--import-only 与 --live 不能同时使用")
    if args.live:
        try:
            _validated_live_budget(args.budget)
        except ValueError as exc:
            parser.error(str(exc))

    fetched_paths = []
    local_state = None
    try:
        if args.cached_source:
            source, fetched_paths = _load_verified_source(args.cached_source)
        elif args.local:
            fetch, local_state = local_fetcher(args.local)
            url = DEFAULT_URL
        else:
            default_fetch, url = github_repo.default_fetch, args.url
            tree_paths = set()
            def fetch(request_url, cancel=None):
                is_raw = urlparse(request_url).netloc == "raw.githubusercontent.com"
                path = None
                if is_raw:
                    parts = unquote(urlparse(request_url).path).strip("/").split("/")
                    suffix = "/".join(parts[3:]) if len(parts) > 3 else ""
                body = default_fetch(request_url, cancel)
                parsed_request = urlparse(request_url)
                if parsed_request.netloc == "api.github.com" and "/git/trees/" in parsed_request.path:
                    try:
                        tree_paths.update(row["path"] for row in json.loads(body.decode("utf-8")).get("tree", [])
                                          if row.get("type") == "blob" and isinstance(row.get("path"), str))
                    except (ValueError, UnicodeDecodeError, AttributeError):
                        pass
                if is_raw and suffix:
                    matches = [candidate for candidate in tree_paths if suffix.endswith("/" + candidate)]
                    if suffix in tree_paths:
                        path = suffix
                    elif len(matches) == 1:
                        path = matches[0]
                if path:
                    fetched_paths.append(path)
                return body
        if not args.cached_source:
            source = github_repo.import_repo(url, fetch=fetch)
    except github_repo.RepoImportError as exc:
        _write_import_error(output, exc.code, exc.message)
        return 2
    except (OSError, ValueError) as exc:
        code = "LOCAL_DIRECTORY_INVALID" if args.local else "IMPORT_INPUT_INVALID"
        message = f"无法读取本地导出目录：{exc}" if args.local else f"无法完成 GitHub 导入：{exc}"
        _write_import_error(output, code, message)
        return 2
    except Exception as exc:
        from refactor.learning.course.research import ResearchFetchError
        if isinstance(exc, ResearchFetchError):
            code, message = "FETCH_FAILED", "GitHub 文件抓取失败：" + str(exc)
        else:
            code, message = "IMPORT_FAILED", "GitHub 导入失败：" + str(exc)
        _write_import_error(output, code, message)
        return 2
    fetched_files = local_state["fetched_paths"] if args.local else fetched_paths
    check = inspect_source(source, fetched_files)
    selected = fetched_files
    output.mkdir(parents=True, exist_ok=True)
    try:
        live = _run_live(source, args) if args.live else None
    except Exception as exc:
        live = dict(error=dict(code=getattr(exc, "code", type(exc).__name__), message=str(exc)), cost_cny="0")
        (output / "live-progress.json").write_text(json.dumps(dict(state="failed", stage="initialization",
            error=live["error"], cost_cny="0"), ensure_ascii=False, indent=2), encoding="utf-8")
    result = dict(mode="live" if args.live else "import-only", url=source.url, title=source.title,
                  selected_files=selected, checks=check, summary=source.summary, sha256=source.content_sha256,
                  fetched_file_count=len(selected), imported_chapter_count=check["merged_chapter_count"],
                  live=live)
    (output / "import-only.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# MentorFlow Guide 导入核对", "", f"- 地址：{result['url']}",
             f"- 标题：{result['title']}", f"- 抓取文件数：{len(selected) if selected is not None else check['merged_chapter_count']}",
             f"- 导入章节数：{check['merged_chapter_count']}",
             f"- README 被导入：{'是' if check['README_included'] else '否'}",
             f"- 重复章节：{check['duplicate_chapters'] or '无'}",
             f"- 出处混入：{'是；标记 ' + str(check['citation_markers']) if check['citation_material_present'] else '否'}",
             f"- 乱码特征：{'是' if check['mojibake'] else '否'}", "", "## 文件顺序", ""]
    lines += [f"- {p}" for p in (selected or check['chapters'])]
    if live and "error" in live:
        lines += ["", "## 切点流程失败", "", f"- 错误码：{live['error']['code']}",
                  f"- 错误：{live['error']['message']}", f"- 已记录费用：¥{live['cost_cny']}",
                  "- 阶段检查点：live-progress.json", ""]
    elif live:
        lines += ["", "## 切点结果", "", f"- 模块：{len(live['modules'])}",
                  f"- 大点：{live['big_points']}", f"- 小要点：{live['small_points']}",
                  f"- 原题候选：{live['original_question_candidates']}",
                  f"- 题库题目：{live['questions']}（按题型：{live['questions_by_type']}；来源：{live['questions_by_origin']}）",
                  f"- 学习分钟：{live['learning_minutes']}；计划总分钟（含检测储备）：{live['total_minutes']}",
                  f"- 费用：¥{live['cost_cny']}", ""]
        struct = live.get("structure", {})
        lines += [f"- 未分配目录：{struct.get('unassigned_sections', []) or '无'}",
                  f"- 重复模块引用：{struct.get('duplicate_module_refs', []) or '无'}",
                  f"- 切点模块建议数：{live.get('proposed_module_count', len(live['modules']))}；产品规则保留讲授模块 {len(live['modules'])}",
                  f"- 纯导航模块并入后省略：{live.get('navigation_only_modules', []) or '无'}",
                  f"- 讲义小要点：有摘录 {live.get('notes', {}).get('with_notes', 0)}，空 {live.get('notes', {}).get('empty', 0)}",
                  "- 原题候选和题库题分别统计；本批 `concept_v1` 备题可能包括挖空题，不与 164 批上传 HTML 的原题数量等同。",
                  "- 付费阶段检查点已逐步写入 live-progress.json。", ""]
        lines += [f"- {m['title']}：{m['point_count']} 大点，{m['minutes']} 分钟；第二级目录："
                  + "、".join(m.get("directory_levels", {}).get("level2", [])) for m in live['modules']]
    (output / "import-only.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if live and "error" in live else 0


def _write_import_error(output, code, message):
    payload = dict(state="failed", stage="import", error=dict(code=code, message=message), cost_cny="0")
    try:
        output.mkdir(parents=True, exist_ok=True)
        (output / "import-only.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "import-only.md").write_text(f"# MentorFlow Guide 导入核对\n\n- 失败阶段：导入\n- 错误码：{code}\n- 错误：{message}\n- 模型费用：¥0\n", encoding="utf-8")
    except OSError:
        pass
    print(json.dumps(dict(error=payload["error"]), ensure_ascii=False), file=sys.stderr)


def _load_verified_source(path):
    """Replay captured public GitHub responses through the real repo importer."""
    import base64
    from dataclasses import asdict
    from refactor.learning.course import github_repo

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    responses = {row["url"]: base64.b64decode(row["body_b64"], validate=True)
                 for row in payload["fetch_responses"]}

    def cached_fetch(url, cancel=None):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("cached source import cancelled")
        try:
            return responses[url]
        except KeyError:
            raise ValueError("cached source is missing importer request: " + url) from None

    source = github_repo.import_repo(payload["url"], fetch=cached_fetch)
    if (source.content_sha256 != payload.get("content_sha256")
            or source.content_sha256 != payload.get("verified_import_sha256")):
        raise ValueError("重放导入器得到的内容 SHA256 与原始导入不一致")
    normalized_document = json.loads(json.dumps(asdict(source.document), ensure_ascii=False))
    if normalized_document != payload.get("document"):
        raise ValueError("重放导入器得到的文档块与缓存文档不一致")
    return source, list(payload["selected_files"])


def _run_live(verified, args):
    """Use product CourseStore and the same outline/point/note/question functions as PlanFlow."""
    from types import SimpleNamespace
    from tests.refactor.simulation.engine import Evidence, RecordedGateway
    from tests.refactor.simulation.run import live_factory
    from refactor.learning.course.ledger import PlanWindow
    from refactor.learning.course.store import CourseStore
    from refactor.learning.course import materials, planner_agents as agents, point_materials
    from refactor.learning.app.plan_flow import PlanFlow

    account = args.account_id
    data_root = args.output.resolve() / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    existing_calls = args.output.resolve() / "evidence" / "model"
    if existing_calls.exists() and any(existing_calls.glob("call-*.json")):
        raise RuntimeError("该输出目录已有模型调用记录（含进行中的请求）；请换新的 --output，避免覆盖付费证据。")
    services = SimpleNamespace(data_root=data_root)
    real = live_factory(args.preset_root, account)(services)
    evidence = Evidence(args.output.resolve() / "evidence")
    gateway = RecordedGateway(real, evidence, budget=args.budget)
    _install_streaming_gateway(gateway)
    store = CourseStore(account, data_root)
    progress_path = args.output.resolve() / "live-progress.json"
    progress_record = dict(state="running", stage="initialized", plan_id=None, cost_cny="0", modules=[], checkpoints=[])
    def checkpoint(stage, **fields):
        progress_record.update(stage=stage, cost_cny=str(gateway.spent), **fields)
        progress_record["checkpoints"].append(json.loads(json.dumps(dict(
            at=datetime.now(timezone.utc).isoformat(), stage=stage, cost_cny=str(gateway.spent), **fields), ensure_ascii=False)))
        progress_path.write_text(json.dumps(progress_record, ensure_ascii=False, indent=2), encoding="utf-8")
    plan_id = "guide165-" + uuid.uuid4().hex
    today = date.today()
    window = PlanWindow(today, today + timedelta(days=90), 600)
    try:
        checkpoint("initialized", plan_id=plan_id)
        store.create_plan(plan_id, goal="学习 MentorFlow 项目指南", final_goal="掌握指南中的项目知识",
                          level="初学者", notes="按资料原目录整理", window=window, total_minutes=0)
        saved = materials.import_verified_source(store, plan_id, verified)
        checkpoint("source_imported", source_id=saved["id"], source_title=saved["title"],
                   imported_chunks=len(store.records("source_chunks", plan_id)))
        sources = [saved]
        chunks_by_source = {saved["id"]: [c for c in store.records("source_chunks", plan_id) if c["source_id"] == saved["id"]]}
        chunks = chunks_by_source[saved["id"]]
        sections = agents.build_sections(sources, chunks_by_source,
            max_per_source=max(40, len(chunks)), max_chars=max(9000, sum(len(c["text"]) for c in chunks)), max_depth=None)
        form = dict(goal="学习 MentorFlow 项目指南", final_goal="掌握指南中的项目知识",
                    notes="按资料原目录整理", level="初学者", level_note="")
        structure_attempts = []
        structure = _classify_sections(sections, form, gateway, plan_id,
                                       on_attempt=structure_attempts.append)
        assigned = [ref for module in structure["modules"] for ref in module["refs"]]
        refs_flat = [ref for module in structure["modules"] for ref in module["refs"]]
        duplicate_refs = sorted({ref for ref in refs_flat if refs_flat.count(ref) > 1})
        excluded_refs = {ref for row in structure.get("excluded", []) for ref in row.get("refs", [])}
        unassigned = [s.id for s in sections if s.id not in set(assigned) | excluded_refs]
        structure_evidence = dict(summary=structure.get("summary"), sections=[dict(id=s.id, title=s.title) for s in sections],
            modules=[dict(key=m["key"], title=m["title"], refs=m["refs"], chapters=m.get("chapters", []))
                     for m in structure["modules"]], excluded=structure.get("excluded", []),
            coverage=structure.get("coverage", {}), unassigned_sections=unassigned,
            duplicate_module_refs=duplicate_refs, response_attempts=structure_attempts)
        checkpoint("structure_parsed", section_count=len(sections), sections=[s.title for s in sections],
                   structure=structure_evidence, module_titles=[m["title"] for m in structure["modules"]])
        by_section = {s.id: s for s in sections}
        stage_id = "guide165-stage"
        prepared = []
        source_titles = {saved["id"]: saved["title"]}
        for n, module in enumerate(structure["modules"], 1):
            module = dict(module, id=f"guide165-module-{n:02d}", stage_id=stage_id, prerequisites=[])
            allowed = {cid for ref in module["refs"] for cid in by_section[ref].chunk_ids}
            picked = PlanFlow._module_chunks([c for c in chunks if c["id"] in allowed], source_titles)
            def complete(stage, system, messages):
                reply = gateway.complete("generate", messages, system=system, plan_id=plan_id, max_tokens=8192)
                return reply if isinstance(reply, str) else reply.text
            module.update(agents.directory_plan_module(module, picked, complete))
            prepared.append(module)
            checkpoint("modules_cutting", modules=[dict(title=m["title"], point_count=len(m.get("points", []))) for m in prepared])

        proposed_module_count = len(prepared)
        navigation_only = [m["title"] for m in prepared if not m.get("points")]
        prepared = _retain_teaching_modules(prepared)
        if not prepared:
            raise ValueError("NO_KNOWLEDGE: 目录切点没有生成可讲解的知识点，符合 PlanFlow 的空课程拦截规则")
        checkpoint("teaching_modules_selected", proposed_module_count=proposed_module_count,
                   saved_module_count=len(prepared), navigation_only_modules=navigation_only,
                   modules=[dict(title=m["title"], refs=m["refs"], point_count=len(m["points"])) for m in prepared])
        modules = _save_module_headers(store, plan_id, prepared, stage_id)
        for module in prepared:
            store.save_generated_module(plan_id, module["id"], module["points"], check_minutes=0,
                                        final_check_minutes=0, check_question_minutes=2, today=today)
            checkpoint("modules_saved", modules=[dict(title=m["title"], point_count=len(m.get("points", [])))
                                                   for m in prepared if m["id"] in {x["id"] for x in store.records("modules", plan_id)}])

        def note_complete(stage, system, messages):
            reply = gateway.complete("point_materials", messages, system=system, plan_id=plan_id, max_tokens=8192)
            return reply if isinstance(reply, str) else reply.text
        all_points = store.records("points", plan_id)
        note_rows, notes_error = _optional_note_preparation(
            gateway, all_points, store.records("source_chunks", plan_id), agents.prepare_directory_notes,
            note_complete)
        with store.transaction():
            for current in store.records("points", plan_id):
                changed = False
                for sub in current.get("subpoints", []):
                    if not sub.get("notes") and note_rows.get(sub["id"]):
                        sub["notes"] = note_rows[sub["id"]]
                        changed = True
                if changed:
                    store._put("points", plan_id, current)
        note_subpoints = [sub for point in store.records("points", plan_id) for sub in point.get("subpoints", [])]
        note_stats = dict(with_notes=sum(bool(sub.get("notes")) for sub in note_subpoints),
                          empty=sum(not bool(sub.get("notes")) for sub in note_subpoints),
                          optional_preparation_error=notes_error)
        checkpoint("notes_saved", point_count=len(store.records("points", plan_id)), notes=note_stats)

        question_results = {}
        ctx = dict(gateway=gateway, cancel=threading.Event())
        for module in store.records("modules", plan_id):
            question_results[module["title"]] = point_materials.prepare_module(
                store, plan_id, module["id"], ctx, lambda *args: None)
            from refactor.learning.course.question_bank import assessment_questions
            question_count = len(assessment_questions(store.records("question_bank", plan_id), module["id"]))
            store.set_question_estimate(plan_id, module["id"], question_count, today=today)
            checkpoint("questions_prepared", question_results=question_results)
        store.close()
        store = CourseStore(account, data_root)  # Verify persisted and reload counts.
        modules = store.records("modules", plan_id)
        points = store.records("points", plan_id)
        questions = store.records("question_bank", plan_id)
        from refactor.learning.course import material_questions
        sources = [s for s in store.records("sources", plan_id) if s["adopted"]]
        chunks = store.records("source_chunks", plan_id)
        original_candidates = material_questions.candidates(chunks, sources)
        by_type, by_origin = {}, {}
        by_point_type_origin = {}
        for question in questions:
            by_type[question.get("kind", "unknown")] = by_type.get(question.get("kind", "unknown"), 0) + 1
            by_origin[question.get("origin", "unknown")] = by_origin.get(question.get("origin", "unknown"), 0) + 1
            key = f"{question.get('kind', 'unknown')} / {question.get('origin', 'unknown')}"
            by_point_type_origin[key] = by_point_type_origin.get(key, 0) + 1
        question_types = _question_type_counts(questions)
        question_types_by_module = {
            module['id']: _question_type_counts([q for q in questions if q.get('module_id') == module['id']])
            for module in modules
        }
        module_refs = {m["title"]: m["refs"] for m in prepared}
        module_directory = {m["title"]: _module_directory_levels(m.get("chapters", [])) for m in prepared}
        calls_by_purpose = {}
        from decimal import Decimal
        for call in evidence.calls:
            purpose = call.get("purpose", "unknown")
            row = calls_by_purpose.setdefault(purpose, dict(calls=0, peak_cost_cny="0", charged_bound_cny="0"))
            row["calls"] += 1
            row["peak_cost_cny"] = str(Decimal(row["peak_cost_cny"]) + Decimal(call.get("peak_cost_cny", "0")))
            row["charged_bound_cny"] = str(Decimal(row["charged_bound_cny"]) + Decimal(call.get("charged_bound", "0")))
        reloaded_plan = store.get_plan(plan_id)
        learning_minutes = sum(m["learning_minutes"] for m in modules)
        result = dict(modules=[dict(title=m["title"], refs=module_refs.get(m["title"], []),
                                    minutes=m["learning_minutes"], point_count=m["point_count"],
                                    directory_levels=module_directory.get(m["title"], {}))
                             for m in modules], big_points=len(points),
                    small_points=sum(len(p.get("subpoints", [])) for p in points), questions=len(questions),
                    learning_minutes=learning_minutes, total_minutes=reloaded_plan["total_minutes"],
                    module_estimates=[dict(title=m["title"], learning_minutes=m["learning_minutes"],
                        check_minutes=m["check_minutes"], final_check_minutes=m["final_check_minutes"],
                        question_count=m.get("question_count", 0)) for m in modules],
                    original_question_candidates=len(original_candidates), questions_by_type=by_type,
                    questions_by_origin=by_origin, questions_by_type_origin=by_point_type_origin,
                    notes=note_stats, structure=structure_evidence,
                    proposed_module_count=proposed_module_count, navigation_only_modules=navigation_only,
                    question_results=question_results, question_types=question_types,
                    question_types_by_module=question_types_by_module, cost_cny=str(gateway.spent),
                    calls_by_purpose=calls_by_purpose,
                    calls=[dict(id=c.get("id"), purpose=c.get("purpose"), peak_cost_cny=c.get("peak_cost_cny"),
                                charged_bound_cny=c.get("charged_bound"), seconds=c.get("seconds"), evidence=c.get("evidence"))
                           for c in evidence.calls],
                    evidence=str(evidence.root), reopened=True)
        checkpoint("reopened_and_counted", result=result, state="complete")
        return result
    except Exception as exc:
        checkpoint("failed", state="failed", error=dict(code=getattr(exc, "code", type(exc).__name__),
                    message=str(exc)), evidence=str(evidence.root))
        return dict(error=progress_record["error"], cost_cny=str(gateway.spent),
                    evidence=str(evidence.root), progress=str(progress_path))
    finally:
        store.close()
        gateway.close()


def _optional_note_preparation(gateway, points, chunks, prepare, complete):
    """Mirror PlanFlow's optional notes behavior in the test-only live harness."""
    try:
        return prepare(points, chunks, complete), None
    except Exception as exc:
        if getattr(exc, "code", None) != "MODEL_FIRST_EVENT_TIMEOUT":
            raise
        # Product PlanFlow continues after optional notes timeouts. Only the
        # recorder's timeout stop is cleared; its budget ledger remains intact.
        if gateway.halted.is_set():
            gateway.halted.clear()
        return {}, dict(code=getattr(exc, "code", type(exc).__name__), message=str(exc))


def _install_streaming_gateway(gateway):
    """Make every live harness request use the provider streaming path.

    Product requests assembled through PlanFlow may already pass on_text;
    point preparation has direct gateway calls. Defaulting the callback at the
    recorded-gateway boundary covers both without changing prompts, purpose,
    token limits, or the product gateway implementation.
    """
    if getattr(gateway, "_guide165_streaming_installed", False):
        return gateway
    original_complete = gateway.complete

    def complete_streaming(purpose, messages, **kwargs):
        if kwargs.get("on_text") is None:
            kwargs["on_text"] = lambda _text: None
        return original_complete(purpose, messages, **kwargs)

    gateway.complete = complete_streaming
    gateway._guide165_streaming_installed = True
    return gateway


def _question_type_counts(questions):
    """Separate source-backed originals from generated cloze rows by stored fields."""
    rows = list(questions)
    return {
        "original_recall": sum(q.get("origin") == "found" and q.get("kind") == "recall" for q in rows),
        "original_objective": sum(q.get("origin") == "found" and q.get("kind") == "objective" for q in rows),
        "cloze": sum(q.get("origin") == "cloze" for q in rows),
    }


def _classify_sections(sections, form, gateway, plan_id, *, on_attempt=None):
    """Run PlanFlow's section request and parser, including its one format repair."""
    from refactor.learning.course import planner_agents as agents
    request = agents.material_structure_request(form["goal"], form, None, sections)
    system, messages = request[0], list(request[1])
    for attempt in range(2):
        response = gateway.complete("outline", messages, system=system, plan_id=plan_id,
                                    max_tokens=32768, enable_search=False)
        try:
            structure = agents.parse_material_structure(response.text, sections)
        except agents.AgentFormatError as exc:
            if on_attempt:
                on_attempt(dict(attempt=attempt + 1, raw_response=response.text,
                                parsed=False, error=str(exc)))
            if attempt:
                raise
            reason = str(exc)
            if exc.__cause__:
                reason += "：" + str(exc.__cause__)
            messages.extend([
                {"role": "assistant", "content": response.text},
                {"role": "user", "content": "上一条格式校验失败：" + reason +
                 "。请依据原输出结构修正一次，返回完整合法JSON，检查括号、字段层级及引用；不执行任何动作。"},
            ])
            continue
        if on_attempt:
            on_attempt(dict(attempt=attempt + 1, raw_response=response.text, parsed=True,
                            coverage=structure.get("coverage", {}),
                            excluded=structure.get("excluded", [])))
        return structure
    raise AssertionError("unreachable")


def _module_directory_levels(section_paths):
    """Return concise chapter and child-heading titles for the trial report."""
    level1, level2 = [], []
    for value in section_paths:
        parts = [part.strip() for part in value.split(" / ") if part.strip()]
        if parts and parts[0] not in level1:
            level1.append(parts[0])
        if len(parts) > 1 and parts[1] not in level2:
            level2.append(parts[1])
    return {"level1": level1, "level2": level2}




def _save_module_headers(store, plan_id, modules, stage_id="guide165-stage"):
    """Persist stage/module headers using CourseStore's product outline contract."""
    stages = [dict(id=stage_id, title="学习路线")]
    rows = []
    for module in modules:
        row = dict(id=module["id"], stage_id=stage_id, title=module["title"], prerequisites=[],
                   learning_minutes=sum(p["minutes"] for p in module.get("points", [])), check_minutes=0,
                   point_count=len(module.get("points", [])))
        if "navigation" in module:
            row["navigation"] = list(module["navigation"])
        rows.append(row)
    store.save_outline(plan_id, stages, rows)
    return store.records("modules", plan_id)


def _retain_teaching_modules(modules):
    """Mirror PlanFlow.preview: attach navigation-only refs to a teaching module, then omit empties."""
    teaching = [module for module in modules if module.get("points")]
    if not teaching:
        return []
    for index, module in enumerate(modules):
        if module.get("points"):
            continue
        owner = next((candidate for candidate in modules[index + 1:] if candidate.get("points")), teaching[-1])
        owner["navigation"] = list(module.get("navigation", [])) + list(owner.get("navigation", []))
        owner["refs"] = list(dict.fromkeys(list(module.get("refs", [])) + list(owner.get("refs", []))))
    return teaching


if __name__ == "__main__":
    raise SystemExit(main())

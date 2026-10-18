from refactor.learning.course.github_repo import import_repo
from tests.refactor.guide165.check_guide_import165 import (
    SCRATCH, _classify_sections, _save_module_headers, build_parser, inspect_source, local_fetcher, main,
)


def test_guide165_local_adapter_imports_all_fourteen_chapters_once(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(14):
        (docs / f"{i:02d}-chapter-{i:02d}.md").write_text(
            f"# 第{i + 1}章 主题{i}\n\n本章介绍主题{i}的重要概念和实践方法。\n\n## 要点\n\n这里是第{i}章的正文。\n",
            encoding="utf-8")
    calls = []
    adapter, state = local_fetcher(tmp_path)
    def fetch(url, cancel=None):
        calls.append(url)
        return adapter(url, cancel)

    source = import_repo("https://github.com/tonyswift868/mentorflow-guide", fetch=fetch)
    checked = inspect_source(source)
    assert len(state["candidate_paths"]) == 14
    assert checked["merged_chapter_count"] == 14
    assert checked["duplicate_chapters"] == []
    assert checked["citation_material_present"] is False
    assert checked["mojibake"] is False
    assert len([u for u in calls if "raw.githubusercontent.com" in u]) == 14
    assert len(state["fetched_paths"]) == 14


def test_guide165_cli_accepts_public_repo_url_and_live_controls(tmp_path):
    parser = build_parser()
    default = parser.parse_args(["--url", "https://github.com/example/guide", "--output",
                                 r"D:\MentorFlow\.local\test-tmp\guide165\check\u"])
    assert default.live is False and default.import_only is False  # main selects import-only by default
    args = build_parser().parse_args([
        "--url", "https://github.com/example/guide/tree/main/docs",
        "--output", r"D:\MentorFlow\.local\test-tmp\guide165\check\u", "--live",
        "--account-id", "acct", "--preset-root", "D:/preset", "--budget", "8",
    ])
    assert args.url.endswith("/tree/main/docs")
    assert args.live is True and args.account_id == "acct" and args.budget == "8"


def test_guide165_local_mode_invokes_product_repo_importer(monkeypatch, tmp_path):
    (tmp_path / "README.md").write_text("# Guide\n\n本指南正文内容。\n", encoding="utf-8")
    adapter, _ = local_fetcher(tmp_path)
    from refactor.learning.course import github_repo
    calls = []
    real = github_repo.import_repo
    def observed(url, **kwargs):
        calls.append((url, kwargs.get("fetch")))
        return real(url, **kwargs)
    monkeypatch.setattr(github_repo, "import_repo", observed)
    source = github_repo.import_repo("https://github.com/tonyswift868/mentorflow-guide", fetch=adapter)
    assert source.document.blocks
    assert calls[0][1] is adapter


def test_guide165_inspection_uses_fetched_readme_and_distinguishes_citation_markers(tmp_path):
    (tmp_path / "README.md").write_text(
        "# MentorFlow Guide\n\n普通题号 [1] 不算出处编号。\n\n学习路线：本题出处 [C01]\n", encoding="utf-8")
    adapter, state = local_fetcher(tmp_path)
    source = import_repo("https://github.com/tonyswift868/mentorflow-guide", fetch=adapter)
    assert state["fetched_paths"] == ["README.md"]
    checked = inspect_source(source, state["fetched_paths"])
    assert checked["README_included"] is True
    assert checked["citation_material_present"] is True

    (tmp_path / "README.md").write_text("# MentorFlow Guide\n\n## 学习路线：本篇出处\n", encoding="utf-8")
    adapter, state = local_fetcher(tmp_path)
    source = import_repo("https://github.com/tonyswift868/mentorflow-guide", fetch=adapter)
    checked = inspect_source(source, state["fetched_paths"])
    assert checked["citation_material_present"] is True

    (tmp_path / "README.md").write_text("# MentorFlow Guide\n\n普通题号 [1] 对应原文出处。\n", encoding="utf-8")
    adapter, state = local_fetcher(tmp_path)
    source = import_repo("https://github.com/tonyswift868/mentorflow-guide", fetch=adapter)
    checked = inspect_source(source, state["fetched_paths"])
    assert checked["citation_material_present"] is False


def test_guide165_fake_gateway_contract_preserves_sections_and_course_store_stage(tmp_path):
    import json
    from datetime import date, timedelta
    from refactor.learning.course.ledger import PlanWindow
    from refactor.learning.course.planner_agents import build_sections
    from refactor.learning.course.store import CourseStore

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "01-chapter.md").write_text("# 第一章\n\n第一章正文介绍一个概念。\n", encoding="utf-8")
    adapter, _ = local_fetcher(tmp_path)
    source = import_repo("https://github.com/tonyswift868/mentorflow-guide", fetch=adapter)
    imported = dict(id="source-1", title=source.title)
    chunks = [{"id": f"source-1:{i}", "source_id": "source-1", "position": i,
               "text": block.text, "section": list(block.section), "block_fragments": []}
              for i, block in enumerate(source.document.blocks)]
    sections = build_sections([imported], {"source-1": chunks}, max_per_source=40, max_chars=9000, max_depth=None)

    class FakeGateway:
        def __init__(self): self.calls = []
        def complete(self, purpose, messages, **kwargs):
            payload = json.loads(messages[0]["content"])
            self.calls.append((purpose, payload, kwargs))
            refs = [row["id"] for row in payload["directory"]]
            response = dict(summary="测试结构", modules=[dict(key="m1", title="第一章模块",
                summary="来自第一章", refs=refs)], missing=[], excluded=[])
            return type("Reply", (), {"text": json.dumps(response, ensure_ascii=False)})()

    gateway = FakeGateway()
    form = dict(goal="理解资料", final_goal="掌握知识", notes="", level="初学者", level_note="")
    structure = _classify_sections(sections, form, gateway, "plan-test")
    assert gateway.calls[0][0] == "outline"
    assert gateway.calls[0][2]["plan_id"] == "plan-test"
    assert gateway.calls[0][1]["goal"] == form["goal"]
    assert structure["modules"][0]["refs"] == [s.id for s in sections]

    store = CourseStore("guide165-test", tmp_path / "store")
    today = date.today()
    store.create_plan("plan-test", window=PlanWindow(today, today + timedelta(days=7), 60))
    store.add_sources("plan-test", [dict(id="test-source", url="https://example.test/guide", adopted=True,
        chunks=[dict(chunk_id="test-chunk", text="课程知识点来源。")])])
    headers = _save_module_headers(store, "plan-test", [dict(id="module-test", title="第一章模块", points=[])])
    assert headers[0]["title"] == "第一章模块"
    assert headers[0]["stage_id"] == "guide165-stage"
    assert store.records("stages", "plan-test")[0]["title"] == "学习路线"
    store.save_generated_module("plan-test", "module-test", [dict(id="point-test", title="测试知识点",
        summary="可持久化的课程知识点", minutes=10, chunk_ids=["test-chunk"])], check_minutes=0,
                                final_check_minutes=0, check_question_minutes=2, today=today)
    store.set_question_estimate("plan-test", "module-test", 2, today=today)
    saved_module = store.records("modules", "plan-test")[0]
    assert saved_module["question_count"] == 2
    assert saved_module["check_minutes"] == 4
    assert saved_module["final_check_minutes"] > 0
    assert store.get_plan("plan-test")["total_minutes"] == store._total("plan-test")
    store.close()


def test_guide165_invalid_url_returns_human_import_code_without_traceback(capsys):
    result = main(["--url", "not-a-github-url", "--output", str(SCRATCH / "invalid-url")])
    captured = capsys.readouterr()
    assert result == 2
    assert '"code": "NOT_A_REPO"' in captured.err
    assert "这不是 GitHub 仓库地址" in captured.err

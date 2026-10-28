"""BUG-161 / 226 regression coverage for GitHub language variant selection."""
from datetime import date, timedelta
import json
from urllib.parse import unquote

import pytest

from refactor.learning.course import addition_links, materials
from refactor.learning.course.github_repo import import_repo, pick_files, _language_tag
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore


def _repo_fetch(paths, bodies, calls):
    def fetch(url, cancel):
        calls.append(url)
        if url == "https://api.github.com/repos/local-test/guide":
            return b'{"full_name":"local-test/guide","default_branch":"main"}'
        if "/git/trees/" in url:
            return json.dumps({"tree": [
                {"path": path, "type": "blob", "size": len(bodies[path].encode("utf-8"))}
                for path in paths
            ]}).encode()
        prefix = "https://raw.githubusercontent.com/local-test/guide/main/"
        assert url.startswith(prefix), url
        path = unquote(url[len(prefix):])
        return bodies[path].encode("utf-8")
    return fetch


def test_technical_suffix_pairs_are_retained_and_fetched_without_warnings():
    paths = ["docs/sql.md", "docs/sql-join.md", "docs/http.md", "docs/http-auth.md"]
    bodies = {path: f"# {path}\n\nIndependent chapter marker: {path}" for path in paths}
    requested = []
    source = import_repo("https://github.com/local-test/guide/tree/main/docs",
                         fetch=_repo_fetch(paths, bodies, requested))

    assert set(pick_files(paths)) == set(paths)
    raw_paths = [url.rsplit("/", 1)[-1] for url in requested
                 if url.startswith("https://raw.githubusercontent.com/")]
    assert set(raw_paths) == {path.rsplit("/", 1)[-1] for path in paths}
    assert not source.document.warnings
    text = "\n".join(block.text for block in source.document.blocks)
    assert all(path in text for path in paths)


@pytest.mark.parametrize("paths", [
    ["docs/cache.md", "docs/cache-lock.md", "docs/thread.md", "docs/thread-pool.md"],
    ["docs/fr.md", "docs/fr-lang.md"],
])
def test_common_technical_suffixes_are_not_language_codes(paths):
    assert set(pick_files(paths)) == set(paths)


def test_ambiguous_registered_language_suffixes_do_not_hide_articles():
    paths = ["docs/book.md", "docs/book-id.md", "docs/sql.md", "docs/sql-as.md"]
    assert set(pick_files(paths)) == set(paths)


def test_technical_suffix_directories_are_independent_books():
    paths = ["docs/sql/chapter.md", "docs/sql-join/chapter.md",
             "docs/http/chapter.md", "docs/http-auth/chapter.md"]
    assert set(pick_files(paths, "docs")) == set(paths)


def test_language_only_folders_pair_matching_chapters_and_keep_unpaired_chapters():
    paths = ["docs/en/chapter1.md", "docs/zh-CN/chapter1.md",
             "docs/en/chapter2.md", "docs/zh-CN/chapter3.md"]
    assert set(pick_files(paths, "docs")) == {
        "docs/zh-CN/chapter1.md", "docs/en/chapter2.md", "docs/zh-CN/chapter3.md"
    }


def test_book_root_scoring_preserves_best_language_copy_for_partial_book():
    paths = ["book/chapter1.md", "book/chapter2.md", "book/chapter3.md",
             "book-zh/chapter1.md"]
    assert set(pick_files(paths)) == {
        "book-zh/chapter1.md", "book/chapter2.md", "book/chapter3.md"
    }


def test_explicit_english_language_subpath_keeps_selected_files():
    paths = ["docs/en/chapter1.md", "docs/zh-CN/chapter1.md",
             "docs/en/chapter2.md", "docs/zh-CN/chapter3.md"]
    assert set(pick_files(paths, "docs/en")) == {"docs/en/chapter1.md", "docs/en/chapter2.md"}


@pytest.mark.parametrize("tag", ["fr", "zh-CN", "sr-Latn-RS", "ja"])
def test_registered_language_script_and_region_tags_are_recognised(tag):
    assert _language_tag(tag) is not None


@pytest.mark.parametrize("tag", ["lang", "xxxx", "en-xxxx", "zz"])
def test_unknown_language_or_subtag_is_not_guessed_as_a_variant(tag):
    assert _language_tag(tag) is None


def test_true_language_variants_keep_chinese_and_explain_skipped_files():
    paths = ["docs/README.md", "docs/README.zh-CN.md", "docs/README.en.md", "docs/README.ja.md"]
    bodies = {
        "docs/README.md": "# Default\n\nDefault language body.",
        "docs/README.zh-CN.md": "# 中文说明\n\n中文译本正文标记。",
        "docs/README.en.md": "# English guide\n\nEnglish translation marker.",
        "docs/README.ja.md": "# 日本語ガイド\n\nJapanese translation marker.",
    }
    requested = []
    source = import_repo("https://github.com/local-test/guide/tree/main/docs",
                         fetch=_repo_fetch(paths, bodies, requested))

    assert requested and any(path.endswith("README.zh-CN.md") for path in requested)
    assert not any(path.endswith("README.en.md") or path.endswith("README.ja.md")
                   for path in requested)
    assert any("README.en.md" in warning and "README.ja.md" in warning
               for warning in source.document.warnings)
    assert "中文译本正文标记" in "\n".join(block.text for block in source.document.blocks)


def test_addition_links_imports_every_technical_chapter_into_course_store(tmp_path):
    paths = ["docs/sql.md", "docs/sql-join.md", "docs/http.md", "docs/http-auth.md"]
    bodies = {path: f"# {path}\n\n落库全文标记：{path}。" * 8 for path in paths}
    calls = []
    start = date(2026, 9, 29)
    with CourseStore("github-226-test", tmp_path / "data") as store:
        store.create_plan("p", goal="技术文档", final_goal="掌握章节", level="beginner",
                          window=PlanWindow(start, start + timedelta(days=30), 60))
        store.add_sources("p", [dict(id="old", url="https://example.test/old", adopted=True,
                                     chunks=[dict(id="old-c", text="已有资料")])])
        store.save_outline("p", [dict(id="s", title="技术")],
                           [dict(id="m", title="基础", stage_id="s", prerequisites=[])])
        store.save_generated_module("p", "m", [dict(id="pt", title="基础", summary="基础概念",
                                                       minutes=10, chunk_ids=["old-c"])],
                                    check_minutes=2, today=start)
        store.start_plan("p", today=start)

        result = addition_links.import_links(
            store, "p", ["https://github.com/local-test/guide/tree/main/docs"],
            repo_fetch=_repo_fetch(paths, bodies, calls), lists={})

        assert result["results"][0]["status"] == "imported"
        assert result["results"][0]["warnings"] == []
        sources = [s for s in store.records("sources", "p") if s["id"] == result["source_ids"][0]]
        assert len(sources) == 1 and sources[0]["adopted"]
        chunks = [chunk for chunk in store.records("source_chunks", "p")
                  if chunk["source_id"] == sources[0]["id"]]
        stored = "\n".join(block.text for block in materials.stored_blocks(chunks))
        assert all(path in stored for path in paths)
        assert len(calls) == len(paths) + 2


def test_true_variant_skip_warning_is_returned_by_addition_link_import(tmp_path):
    paths = ["docs/README.md", "docs/README.zh-CN.md", "docs/README.en.md", "docs/README.ja.md"]
    bodies = {
        "docs/README.md": "# Default\n\nDefault language body.",
        "docs/README.zh-CN.md": "# 中文说明\n\n中文译本正文标记。",
        "docs/README.en.md": "# English guide\n\nEnglish translation marker.",
        "docs/README.ja.md": "# 日本語ガイド\n\nJapanese translation marker.",
    }
    calls = []
    start = date(2026, 9, 29)
    with CourseStore("github-226-warning-test", tmp_path / "data") as store:
        store.create_plan("p", goal="文档", final_goal="理解资料", level="beginner",
                          window=PlanWindow(start, start + timedelta(days=30), 60))
        store.add_sources("p", [dict(id="old", url="https://example.test/old", adopted=True,
                                     chunks=[dict(id="old-c", text="已有资料")])])
        store.save_outline("p", [dict(id="s", title="资料")],
                           [dict(id="m", title="基础", stage_id="s", prerequisites=[])])
        store.save_generated_module("p", "m", [dict(id="pt", title="基础", summary="基础概念",
                                                       minutes=10, chunk_ids=["old-c"])],
                                    check_minutes=2, today=start)
        store.start_plan("p", today=start)
        result = addition_links.import_links(
            store, "p", ["https://github.com/local-test/guide/tree/main/docs"],
            repo_fetch=_repo_fetch(paths, bodies, calls), lists={})
        imported = result["results"][0]
        assert imported["status"] == "imported"
        assert any("README.en.md" in warning and "README.ja.md" in warning
                   for warning in imported["warnings"])
        source, = [item for item in store.records("sources", "p")
                   if item["id"] == imported["source_id"]]
        assert any("README.en.md" in warning and "README.ja.md" in warning
                   for warning in source["warnings"])

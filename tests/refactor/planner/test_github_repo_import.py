"""GitHub repository documentation import (PLAN-06, PLAN-09)."""
import json

import pytest

from refactor.learning.course import github_repo
from refactor.learning.course.github_repo import RepoImportError, import_repo, parse, pick_files

TREE = ["README.md", "README_EN.md", "CHANGELOG.md", ".github/ISSUE_TEMPLATE.md", "docs/_sidebar.md", "docs/前言.md",
        "docs/chapter10/Chapter10-Protocols.md", "docs/chapter10/Chapter10-Protocols.zh-CN.md",
        "docs/chapter2/Chapter2-History.md", "docs/chapter2/Chapter2-History.zh-CN.md", "code/main.py"]
BODY = {
    "docs/前言.md": "# 前言 {.unnumbered}\n\n这本书讲智能体。" * 3,
    "docs/chapter2/Chapter2-History.zh-CN.md": "# 第二章 发展史\n\n## 早期\n\n符号主义智能体的历史与局限。\n\n## 现代\n\n大模型驱动的智能体。",
    "docs/chapter10/Chapter10-Protocols.zh-CN.md": "# 第十章 通信协议\n\n## MCP\n\nMCP 让模型调用外部工具。",
    "docs/chapter2/Chapter2-History.md": "# Chapter 2 History\n\nEnglish only text about agents.",
}


def fake_fetch(calls):
    def fetch(url, cancel):
        calls.append(url)
        if url == "https://api.github.com/repos/acme/book":
            return json.dumps({"full_name": "acme/book", "default_branch": "main", "description": "智能体教程"}).encode()
        if url.startswith("https://api.github.com/repos/acme/book/git/trees/main"):
            return json.dumps({"tree": [{"path": p, "type": "blob"} for p in TREE] + [{"path": "docs", "type": "tree"}]}).encode()
        prefix = "https://raw.githubusercontent.com/acme/book/main/"
        from urllib.parse import unquote
        return BODY[unquote(url[len(prefix):])].encode("utf-8")
    return fetch


def test_parse_accepts_repo_and_tree_urls_only():
    assert parse("https://github.com/datawhalechina/hello-agents") == ("datawhalechina", "hello-agents", None, "")
    assert parse("https://github.com/a/b.git") == ("a", "b", None, "")
    assert parse("https://github.com/a/b/tree/dev/docs/ch1/") == ("a", "b", "dev", "docs/ch1")
    assert parse("https://github.com/a/b/blob/main/x.md") is None and parse("https://gitlab.com/a/b") is None
    assert parse("https://github.com/orgs/acme") is None
    assert parse("https://github.com/bojieli/ai-agent-book?utm_source=chatgpt.com") == ("bojieli", "ai-agent-book", None, "")
    assert parse("https://github.com/a/b#readme") == ("a", "b", None, "")


def test_pick_prefers_docs_folder_chinese_copies_and_skips_meta_files():
    assert pick_files(TREE) == ["docs/前言.md", "docs/chapter2/Chapter2-History.zh-CN.md", "docs/chapter10/Chapter10-Protocols.zh-CN.md"]
    assert pick_files(TREE, "docs/chapter10") == ["docs/chapter10/Chapter10-Protocols.zh-CN.md"]
    assert pick_files(["README.md", "README_EN.md", "guide.md"]) == ["README.md", "guide.md"]


def test_import_merges_chapters_as_sections_and_reports_tier():
    calls = []
    source = import_repo("https://github.com/acme/book", fetch=fake_fetch(calls))
    doc = source.document
    assert source.url == "https://github.com/acme/book" and source.site == "github.com" and source.category == "repository"
    assert source.content_sha256 == doc.sha256 and doc.chunks
    assert [b.section[0] for b in doc.blocks][:1] == ["前言"]
    assert {b.section[0] for b in doc.blocks} == {"前言", "第二章 发展史", "第十章 通信协议"}
    assert any(b.section[:2] == ("第二章 发展史", "早期") for b in doc.blocks)
    assert not any("English only" in b.text for b in doc.blocks)
    assert "读取了 docs/ 下 3 篇文档" in source.summary
    assert not any("CHANGELOG" in c or "_sidebar" in c for c in calls)


def test_import_errors_are_explained():
    with pytest.raises(RepoImportError) as err:
        import_repo("https://example.org/x", fetch=fake_fetch([]))
    assert err.value.code == "NOT_A_REPO"

    def empty(url, cancel):
        if "trees" in url:
            return json.dumps({"tree": [{"path": "src/a.py", "type": "blob"}]}).encode()
        return json.dumps({"full_name": "acme/code", "default_branch": "main"}).encode()
    with pytest.raises(RepoImportError) as err:
        import_repo("https://github.com/acme/code", fetch=empty)
    assert err.value.code == "NO_DOCS"
    with pytest.raises(RepoImportError) as err:
        import_repo("https://github.com/acme/missing", fetch=lambda url, cancel: b"{}")
    assert err.value.code == "GITHUB_NOT_FOUND"


def test_size_cap_stops_reading_and_says_so(monkeypatch):
    monkeypatch.setattr(github_repo, "MAX_TOTAL", 150)   # the preface fits, chapter 2 does not
    source = import_repo("https://github.com/acme/book", fetch=fake_fetch([]))
    assert any("超过上限" in w for w in source.document.warnings)


def test_translations_require_a_registered_language_code():
    from refactor.learning.course.github_repo import drop_translations
    assert drop_translations(["book/ch1.md", "book-hu/ch1.md", "book-xx/ch1.md", "README.md", "README.ja.md"]) == [
        "book/ch1.md", "book-xx/ch1.md", "README.md"]
    # a Chinese copy wins over the original
    assert drop_translations(["guide/a.md", "guide-zh/a.md", "README.md", "README.zh-CN.md"]) == ["guide-zh/a.md", "README.zh-CN.md"]


def test_the_book_folder_is_the_one_with_most_chapter_text():
    paths = ["book/introduction.md", "book/chapter1.md", "book/chapter2.md", "book/afterword.md",
             "slides/lesson-01.md", "slides/lesson-02.md", "slides/lesson-03.md",
             "chapter3/data/8-诉讼法/民事.md", "chapter3/data/9-刑法/刑法.md", "chapter3/README.md",
             "book-en/chapter1.md", "book-en/chapter2.md"]
    sizes = {p: 100000 for p in paths if p.startswith("book/")} | {p: 7000 for p in paths if p.startswith("slides/")}
    assert pick_files(paths, "", sizes) == ["book/introduction.md", "book/chapter1.md", "book/chapter2.md", "book/afterword.md"]

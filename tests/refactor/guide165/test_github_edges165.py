"""GitHub repository import boundary cases for MF-02."""
import json
from urllib.parse import unquote

import pytest

from refactor.learning.course import github_repo
from refactor.learning.course.github_repo import RepoImportError, import_repo, pick_files
from refactor.learning.course.research import ResearchFetchError


def _book(paths, *, meta=None, bodies=None):
    bodies = bodies or {
        path: (f"# {path.rsplit('/', 1)[-1].rsplit('.', 1)[0]}\n\n这是用于验证 GitHub 导入的中文课文正文。\n" * 2).encode("utf-8")
        for path in paths
    }
    meta = meta or {"full_name": "acme/guide", "default_branch": "main", "description": "学习指南"}
    calls = []

    def fetch(url, cancel):
        calls.append(url)
        if url == "https://api.github.com/repos/acme/guide":
            return json.dumps(meta, ensure_ascii=False).encode("utf-8")
        if url.startswith("https://api.github.com/repos/acme/guide/git/trees/"):
            entries = [{"path": path, "type": "blob", "size": len(bodies[path])} for path in paths]
            return json.dumps({"tree": entries}, ensure_ascii=False).encode("utf-8")
        prefix = "https://raw.githubusercontent.com/acme/guide/main/"
        return bodies[unquote(url[len(prefix):])]

    return fetch, calls


def test_repo_root_and_docs_subpath_skip_readme_and_keep_chapter_order():
    paths = [
        "README.md",
        "docs/00-intro.md",
        "docs/01-first.md",
        "docs/02-second.md",
        "docs/10-last.md",
    ]
    fetch, calls = _book(paths)

    root = import_repo("https://github.com/acme/guide", fetch=fetch)
    raw_paths = [unquote(url.split("/main/", 1)[1]) for url in calls if "raw.githubusercontent.com" in url]
    assert raw_paths == paths[1:]
    assert "README.md" not in raw_paths
    assert len({block.section[0] for block in root.document.blocks}) == 4

    fetch, calls = _book(paths)
    docs = import_repo("https://github.com/acme/guide/tree/main/docs", fetch=fetch)
    raw_paths = [unquote(url.split("/main/", 1)[1]) for url in calls if "raw.githubusercontent.com" in url]
    assert raw_paths == paths[1:]
    assert docs.url == "https://github.com/acme/guide/tree/main/docs"


def test_chinese_numbered_chapters_sort_naturally():
    paths = ["docs/第十章.md", "docs/第一章.md", "docs/第二章.md"]
    assert pick_files(paths) == ["docs/第一章.md", "docs/第二章.md", "docs/第十章.md"]


def test_encoded_chinese_tree_subpath_selects_its_documents():
    paths = ["docs/中文/第一章.md", "docs/中文/第二章.md", "docs/其他/第三章.md"]
    fetch, calls = _book(paths)

    source = import_repo(
        "https://github.com/acme/guide/tree/main/docs/%E4%B8%AD%E6%96%87",
        fetch=fetch,
    )

    raw_paths = [unquote(url.split("/main/", 1)[1]) for url in calls if "raw.githubusercontent.com" in url]
    assert raw_paths == paths[:2]
    assert source.url.endswith("/tree/main/docs/中文")


def test_file_and_total_byte_limits_stop_later_chapters(monkeypatch):
    paths = [f"docs/{index:02}-chapter.md" for index in range(3)]

    monkeypatch.setattr(github_repo, "MAX_FILES", 2)
    fetch, _ = _book(paths)
    source = import_repo("https://github.com/acme/guide", fetch=fetch)
    assert {block.section[0] for block in source.document.blocks} == {"00-chapter", "01-chapter"}
    assert any("只读取了前 2 篇" in warning for warning in source.document.warnings)

    monkeypatch.setattr(github_repo, "MAX_FILES", 80)
    bodies = {
        path: ("# " + path.rsplit("/", 1)[-1].rsplit(".", 1)[0] + "\n\n中文正文内容。\n").encode()
        for path in paths
    }
    monkeypatch.setattr(github_repo, "MAX_TOTAL", len(bodies[paths[0]]) + 1)
    fetch, calls = _book(paths, bodies=bodies)
    source = import_repo("https://github.com/acme/guide", fetch=fetch)
    assert {block.section[0] for block in source.document.blocks} == {"00-chapter"}
    assert any("总量超过上限" in warning for warning in source.document.warnings)
    assert sum("raw.githubusercontent.com" in url for url in calls) == 2


def test_tree_url_with_slash_branch_uses_longest_existing_branch_prefix():
    path = "docs/第一章.md"
    body = "# 第一章\n\n这是一段用于测试分支路径的中文正文内容。\n".encode("utf-8")
    calls = []

    def fetch(url, cancel):
        calls.append(url)
        if url == "https://api.github.com/repos/acme/guide":
            return b'{"full_name":"acme/guide","default_branch":"main"}'
        if url == "https://api.github.com/repos/acme/guide/git/trees/feature?recursive=1":
            raise ResearchFetchError("网页返回 HTTP 404，未读取正文。", status_code=404)
        if url == "https://api.github.com/repos/acme/guide/git/trees/feature%2Fnew?recursive=1":
            return json.dumps({"tree": [{"path": path, "type": "blob", "size": len(body)}]}).encode()
        raw_prefix = "https://raw.githubusercontent.com/acme/guide/feature%2Fnew/"
        if url.startswith(raw_prefix):
            assert unquote(url[len(raw_prefix):]) == path
            return body
        raise ResearchFetchError("网页返回 HTTP 404，未读取正文。", status_code=404)

    source = import_repo("https://github.com/acme/guide/tree/feature/new/docs", fetch=fetch)
    assert {block.section[0] for block in source.document.blocks} == {"第一章"}
    assert "https://api.github.com/repos/acme/guide/git/trees/feature?recursive=1" in calls
    assert "https://api.github.com/repos/acme/guide/git/trees/feature%2Fnew?recursive=1" in calls


def test_tree_url_prefers_slash_default_branch_when_it_matches_the_link():
    path = "docs/第一章.md"
    body = "# 第一章\n\n这是一段用于测试默认分支的中文正文。\n".encode("utf-8")
    calls = []

    def fetch(url, cancel):
        calls.append(url)
        if url == "https://api.github.com/repos/acme/guide":
            return b'{"full_name":"acme/guide","default_branch":"release/2026"}'
        if url == "https://api.github.com/repos/acme/guide/git/trees/release%2F2026?recursive=1":
            return json.dumps({"tree": [{"path": path, "type": "blob", "size": len(body)}]}).encode()
        raw_prefix = "https://raw.githubusercontent.com/acme/guide/release%2F2026/"
        if url.startswith(raw_prefix):
            assert unquote(url[len(raw_prefix):]) == path
            return body
        raise ResearchFetchError("网页返回 HTTP 404，未读取正文。", status_code=404)

    source = import_repo("https://github.com/acme/guide/tree/release/2026/docs", fetch=fetch)
    assert {block.section[0] for block in source.document.blocks} == {"第一章"}
    tree_calls = [url for url in calls if "/git/trees/" in url]
    assert tree_calls == ["https://api.github.com/repos/acme/guide/git/trees/release%2F2026?recursive=1"]


@pytest.mark.parametrize(
    ("status", "expected_code", "message_fragment"),
    [
        (404, "GITHUB_NOT_FOUND", "不是公开仓库"),
        (403, "GITHUB_RATE_LIMIT", "稍后重试"),
    ],
)
def test_github_http_errors_are_mapped_to_repository_guidance(status, expected_code, message_fragment):
    def fetch(url, cancel):
        if url == "https://api.github.com/repos/acme/guide":
            raise ResearchFetchError(f"网页返回 HTTP {status}，未读取正文。", status_code=status)
        raise AssertionError("repository metadata request should fail first")

    with pytest.raises(RepoImportError) as error:
        import_repo("https://github.com/acme/guide", fetch=fetch)

    assert error.value.code == expected_code
    assert message_fragment in str(error.value)


def test_bad_url_and_missing_or_private_repository_have_clear_errors():
    with pytest.raises(RepoImportError) as bad_url:
        import_repo("https://example.org/acme/guide", fetch=lambda url, cancel: b"{}")
    assert bad_url.value.code == "NOT_A_REPO"

    def not_found(url, cancel):
        if url == "https://api.github.com/repos/acme/guide":
            return b'{"message":"Not Found"}'
        raise AssertionError("missing repository should fail before reading its tree")

    with pytest.raises(RepoImportError) as missing:
        import_repo("https://github.com/acme/guide", fetch=not_found)
    assert missing.value.code == "GITHUB_NOT_FOUND"
    assert "不是公开仓库" in str(missing.value)

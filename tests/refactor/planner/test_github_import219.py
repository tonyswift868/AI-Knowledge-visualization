"""Regression tests for GitHub import completeness and chapter selection (219)."""
import json
from urllib.parse import quote, unquote

import pytest

from refactor.learning.course.github_repo import RepoImportError, import_repo, pick_files
from refactor.learning.course.research import ResearchFetchError


def _fetcher(paths, bodies, *, truncated=False):
    def fetch(url, cancel):
        if url == "https://api.github.com/repos/acme/book":
            return b'{"full_name":"acme/book","default_branch":"main"}'
        if "/git/trees/" in url:
            entries = [{"path": path, "type": "blob", "size": len(bodies[path])} for path in paths]
            return json.dumps({"truncated": truncated, "tree": entries}).encode()
        return bodies[unquote(url.split("/main/", 1)[1])]
    return fetch


def test_mixed_language_independent_chapters_are_kept():
    paths = ["docs/01-intro.md", "docs/数据库.md", "docs/network.md"]
    bodies = {
        paths[0]: "# 课程介绍\n\n课程介绍中文正文。".encode(),
        paths[1]: "# 数据库\n\n数据库事务确保原子性。".encode(),
        paths[2]: b"# Network\n\nTCP provides reliable delivery. This is a separate chapter.",
    }

    assert set(pick_files(paths)) == set(paths)
    source = import_repo("https://github.com/acme/book/tree/main/docs", fetch=_fetcher(paths, bodies))
    sections = {block.section[0] for block in source.document.blocks}
    assert sections == {"课程介绍", "数据库", "Network"}
    assert "TCP provides reliable delivery" in "\n".join(block.text for block in source.document.blocks)


def test_same_number_does_not_prove_chapters_are_translations():
    paths = ["docs/Chapter1-Network.md", "docs/第一章 数据库.md"]
    assert set(pick_files(paths)) == set(paths)


def test_truncated_root_tree_fails_with_missing_content_and_recovery_guidance():
    paths = ["docs/01.md"]
    bodies = {paths[0]: "# 第一章\n\n正文内容。".encode()}

    with pytest.raises(RepoImportError) as error:
        import_repo("https://github.com/acme/book", fetch=_fetcher(paths, bodies, truncated=True))

    assert error.value.code == "GITHUB_TREE_TRUNCATED"
    assert "不完整" in str(error.value)
    assert "缺失路径清单" in str(error.value)
    assert "子目录" in str(error.value) and "上传" in str(error.value)


@pytest.mark.parametrize('branch', ['main', 'feature/new'])
def test_truncated_repo_tree_recovers_requested_subpath_by_tree_sha(branch):
    body = b"# Chapter\n\nSubtree chapter body."
    calls = []
    encoded_branch = quote(branch, safe='')

    def fetch(url, cancel):
        calls.append(url)
        if url == "https://api.github.com/repos/acme/book":
            return b'{"full_name":"acme/book","default_branch":"main"}'
        if url.endswith(f"/git/trees/{encoded_branch}?recursive=1"):
            return json.dumps({"truncated": True, "tree": []}).encode()
        if url.endswith(f"/git/trees/{encoded_branch}"):
            return json.dumps({"tree": [{"path": "docs", "type": "tree", "sha": "docs-sha"}]}).encode()
        if url.endswith("/git/trees/docs-sha"):
            return json.dumps({"tree": [{"path": "book", "type": "tree", "sha": "book-sha"}]}).encode()
        if url.endswith("/git/trees/book-sha?recursive=1"):
            return json.dumps({"tree": [{"path": "01-chapter.md", "type": "blob", "size": len(body)}]}).encode()
        if url == f"https://raw.githubusercontent.com/acme/book/{encoded_branch}/docs/book/01-chapter.md":
            return body
        if '/git/trees/feature' in url:
            raise ResearchFetchError('not found', status_code=404)
        raise AssertionError(f"unexpected request: {url}")

    source = import_repo(f"https://github.com/acme/book/tree/{branch}/docs/book", fetch=fetch)
    assert "Subtree chapter body." in "\n".join(block.text for block in source.document.blocks)
    assert "?recursive=1" in calls[1]
    assert f'https://api.github.com/repos/acme/book/git/trees/{encoded_branch}' in calls
    assert calls[-2].endswith("/git/trees/book-sha?recursive=1")


def test_truncated_subtree_still_reports_no_complete_file_list():
    def fetch(url, cancel):
        if url == "https://api.github.com/repos/acme/book":
            return b'{"full_name":"acme/book","default_branch":"main"}'
        if url.endswith("/git/trees/main?recursive=1"):
            return json.dumps({"truncated": True, "tree": []}).encode()
        if url.endswith("/git/trees/main"):
            return json.dumps({"tree": [{"path": "docs", "type": "tree", "sha": "docs-sha"}]}).encode()
        if url.endswith("/git/trees/docs-sha?recursive=1"):
            return json.dumps({"truncated": True, "tree": []}).encode()
        raise AssertionError(f"unexpected request: {url}")

    with pytest.raises(RepoImportError) as error:
        import_repo("https://github.com/acme/book/tree/main/docs", fetch=fetch)

    assert error.value.code == "GITHUB_TREE_TRUNCATED"
    assert "缺失路径清单" in str(error.value)
    assert "上传" in str(error.value)

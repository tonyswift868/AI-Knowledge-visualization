"""Import a GitHub repository's documentation as one learning source (PLAN-06, PLAN-09).

A repository home page only shows the README (often just a table of contents);
the real material lives in Markdown files such as docs/chapterN.md. This reads the
repository tree through GitHub's public API, picks the documentation files
(docs/-like folder first, Chinese over English duplicates), fetches each raw
file through the guarded research fetcher, and merges them into one document
whose sections are the chapters. Bounded by file count and total size.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import quote

from .extract import ExtractedDocument, ExtractError, TextBlock, chunk_blocks, extract_bytes
from .research_pipeline import VerifiedSource
from .source_filter import RawHit, load_lists, screen

REPO_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?"
                      r"(?:/tree/([^/?#]+)((?:/[^?#]*)?))?/?$")
DOC_DIRS = ("docs", "doc", "book", "chapters", "tutorial", "tutorials", "content", "notes", "course")
SKIP_NAMES = re.compile(r"(?i)(^|/)((changelog|contributing|code_of_conduct|security|license|authors|pull_request_template|issue_template)[^/]*|_[^/]*)$")
SKIP_DIRS = re.compile(r"(?i)(^|/)(\.github|node_modules|vendor|site-packages|dist|build|\.vitepress|\.vuepress)/")
ENGLISH = re.compile(r"(?i)(^|[/_.-])(en|en-us|english)([/_.-]|\.md$)")
CJK = re.compile(r"[一-鿿]")
MAX_FILES = 80
MAX_TOTAL = 4 * 1024 * 1024


class RepoImportError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def parse(url: str):
    """(owner, repo, branch or None, subpath or '') for a repository URL, else None."""
    match = REPO_URL.match((url or "").strip())
    if not match or match.group(1).lower() in {"orgs", "topics", "search", "settings", "marketplace", "sponsors", "features", "about"}:
        return None
    owner, repo, branch, sub = match.groups()
    return owner, repo, branch, (sub or "").strip("/")


def _natural(path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path)]


def pick_files(paths, subpath=""):
    """Documentation Markdown files in reading order; a Chinese copy wins over an English one."""
    md = [p for p in paths if p.lower().endswith((".md", ".markdown")) and not SKIP_NAMES.search(p) and not SKIP_DIRS.search(p)]
    if subpath:
        md = [p for p in md if p == subpath or p.startswith(subpath + "/")]
    else:
        for folder in DOC_DIRS:
            inside = [p for p in md if p.lower().startswith(folder + "/")]
            if len(inside) >= 2:
                md = inside
                break
    non_english = [p for p in md if not ENGLISH.search(p)]
    if non_english and len(non_english) < len(md):
        md = non_english
    # Bilingual folders (e.g. "Chapter1-Intro.md" next to "第一章 初识.md"): keep the Chinese-named files.
    folders = {}
    for path in md:
        folders.setdefault(path.rsplit("/", 1)[0] if "/" in path else "", []).append(path)
    kept = []
    for items in folders.values():
        chinese = [p for p in items if CJK.search(p.rsplit("/", 1)[-1])]
        kept.extend(chinese if chinese and len(chinese) < len(items) else items)
    # Top-level files before chapter folders; an intro (README / index / 前言) leads its folder.
    intro = re.compile(r"(?i)^(readme|index|intro|introduction|preface|前言|序言|简介)\.m")
    return sorted(kept, key=lambda p: (p.count("/"), not intro.match(p.rsplit("/", 1)[-1]), _natural(p)))


def cjk_share(text):
    letters = sum(1 for ch in text if ch.isalpha())
    return len(CJK.findall(text)) / letters if letters else 0.0


def _json(fetch, url, cancel):
    try:
        return json.loads(fetch(url, cancel).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RepoImportError("GITHUB_BAD_RESPONSE", "GitHub 返回的数据无法识别，请稍后重试。") from None


def default_fetch(url, cancel):
    from ..planning.research import ResearchService
    fetched = ResearchService(timeout=20.0, max_bytes=MAX_TOTAL).fetch_bytes(
        {"url": url, "title": ""}, cancel=cancel,
        allowed_types={"application/json", "text/plain", "text/markdown", "application/octet-stream"})
    return fetched.body


def import_repo(url: str, *, fetch=default_fetch, cancel=None, lists=None) -> VerifiedSource:
    parsed = parse(url)
    if parsed is None:
        raise RepoImportError("NOT_A_REPO", "这不是 GitHub 仓库地址。")
    owner, repo, branch, subpath = parsed
    screened = screen([RawHit(url=f"https://github.com/{owner}/{repo}")], lists=lists or load_lists())
    tier = screened[0].tier if screened else "neutral"
    if tier == "blocked":
        raise RepoImportError("BLOCKED", "这个来源在屏蔽名单里，不能使用。")
    meta = _json(fetch, f"https://api.github.com/repos/{owner}/{repo}", cancel)
    if not isinstance(meta, dict) or not meta.get("full_name"):
        raise RepoImportError("GITHUB_NOT_FOUND", "找不到这个仓库，或它不是公开仓库。")
    branch = branch or meta.get("default_branch") or "main"
    tree = _json(fetch, f"https://api.github.com/repos/{owner}/{repo}/git/trees/{quote(branch, safe='')}?recursive=1", cancel)
    paths = [item["path"] for item in (tree.get("tree") or []) if isinstance(item, dict) and item.get("type") == "blob"
             and isinstance(item.get("path"), str)]
    files = pick_files(paths, subpath)
    if not files:
        raise RepoImportError("NO_DOCS", "这个仓库里没有找到 Markdown 文档。")
    warnings = []
    if len(files) > MAX_FILES:
        warnings.append(f"文档共 {len(files)} 篇，只读取了前 {MAX_FILES} 篇。")
        files = files[:MAX_FILES]
    blocks, raw_total, digest, chinese_seen = [], 0, hashlib.sha256(), False
    for path in files:
        body = fetch(f"https://raw.githubusercontent.com/{owner}/{repo}/{quote(branch, safe='')}/{quote(path)}", cancel)
        raw_total += len(body)
        if raw_total > MAX_TOTAL:
            warnings.append("文档总量超过上限，后面的篇章没有读取。")
            break
        try:
            doc = extract_bytes(body, path.rsplit("/", 1)[-1])
        except ExtractError:
            warnings.append(f"{path} 没有可用正文，已跳过。")
            continue
        share = cjk_share(" ".join(b.text for b in doc.blocks[:40]))
        if share > 0.2:
            chinese_seen = True
        elif chinese_seen and share < 0.02:
            warnings.append(f"{path} 是另一种语言的重复版本，已跳过。")
            continue
        chapter = (doc.title or path).strip()[:80]
        digest.update(path.encode("utf-8") + b"\0" + body + b"\0")
        for block in doc.blocks:
            rest = tuple(s for s in block.section if s != chapter)[:2]
            blocks.append(TextBlock(block.text, block.kind, None, (chapter,) + rest))
    if not blocks:
        raise RepoImportError("NO_DOCS", "仓库里的文档都没有可用正文。")
    sha = digest.hexdigest()
    chunks = chunk_blocks(blocks, source_sha256=sha)
    title = f"{meta['full_name']}" + (f"：{meta['description']}" if meta.get("description") else "")
    document = ExtractedDocument(title[:200], "text/markdown", sha,
                                 sum(len(b.text) for b in blocks) + 2 * (len(blocks) - 1), tuple(blocks), chunks, tuple(warnings))
    canonical = f"https://github.com/{owner}/{repo}" + (f"/tree/{branch}/{subpath}" if subpath else "")
    summary = (meta.get("description") or "") + f"（读取了 {len({b.section[0] for b in blocks})} 篇文档）"
    return VerifiedSource(canonical, canonical, title[:200], "github.com", tier if tier in {"whitelist", "neutral", "low"} else "neutral",
                          "repository", ("github_docs",), document, summary[:500],
                          datetime.now(timezone.utc).isoformat(), sha)

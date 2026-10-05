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
    # Shared links often carry tracking parameters (?utm_source=…) or anchors.
    match = REPO_URL.match(re.split(r"[?#]", (url or "").strip(), maxsplit=1)[0])
    if not match or match.group(1).lower() in {"orgs", "topics", "search", "settings", "marketplace", "sponsors", "features", "about"}:
        return None
    owner, repo, branch, sub = match.groups()
    return owner, repo, branch, (sub or "").strip("/")


def _natural(path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path)]


CHAPTER = re.compile(r"(?i)(chapter|chap|ch|lesson|lecture|part|unit|week|day)[-_ ]?\d+|第.{1,4}[章讲节课]|^\d{1,3}[-_. ]")
INTRO = re.compile(r"(?i)^(readme|index|intro|introduction|preface|前言|序言|简介|引言)\.m")


VARIANT = re.compile(r"(?i)^(?P<base>.+?)[-_.](?P<code>[a-z]{2}(?:[-_][a-z]{2,4})?|[a-z]{4})$")
CHINESE_CODE = re.compile(r"(?i)^(zh|zh[-_]?(cn|hans|sc)|cn|chs)$")


def _variant(name, siblings):
    """(base, code) when `name` is a language copy of a sibling, e.g. book-hu next to book."""
    m = VARIANT.match(name)
    if m and m.group("base") in siblings:
        return m.group("base"), m.group("code")
    return None


def drop_translations(paths):
    """Keep one language per book: the Chinese copy when there is one, otherwise the original.

    Generic, no language list: "X-code" / "X.code" / "X_code" is a copy of X when X also
    exists next to it (folders: book vs book-hu; files: README.md vs README.ja.md).
    """
    dirs_by_parent, files_by_parent = {}, {}
    for path in paths:
        parts = path.split("/")
        for i in range(len(parts) - 1):
            dirs_by_parent.setdefault("/".join(parts[:i]), set()).add(parts[i])
        stem = parts[-1].rsplit(".", 1)[0]
        files_by_parent.setdefault("/".join(parts[:-1]), set()).add(stem)

    def keep_dir(parent, name):
        siblings = dirs_by_parent.get(parent, set())
        hit = _variant(name, siblings)
        if hit:                                  # a copy: keep only if it is the Chinese one
            return bool(CHINESE_CODE.match(hit[1]))
        chinese = [n for n in siblings if (v := _variant(n, siblings)) and v[0] == name and CHINESE_CODE.match(v[1])]
        return not chinese                       # an original with a Chinese copy gives way

    def keep_file(parent, stem):
        siblings = files_by_parent.get(parent, set())
        hit = _variant(stem, siblings)
        if hit:
            return bool(CHINESE_CODE.match(hit[1]))
        chinese = [n for n in siblings if (v := _variant(n, siblings)) and v[0] == stem and CHINESE_CODE.match(v[1])]
        return not chinese

    kept = []
    for path in paths:
        parts = path.split("/")
        if all(keep_dir("/".join(parts[:i]), parts[i]) for i in range(len(parts) - 1)) and \
                keep_file("/".join(parts[:-1]), parts[-1].rsplit(".", 1)[0]):
            kept.append(path)
    return kept


def _chapterish(path, root):
    """A chapter-like file within two levels of the root (deeper files are data sets, assets, …)."""
    rest = (path[len(root) + 1:] if root else path).split("/")
    return len(rest) <= 2 and any(CHAPTER.search(part) for part in rest)


def pick_files(paths, subpath="", sizes=None):
    """The book's Markdown files in reading order.

    Translations are dropped when an untranslated copy exists. Without a subpath,
    the top-level folder whose chapter-like files hold the most text is the book
    (docs/, book/, …; a folder that is itself one chapter, e.g. chapter3/, is not);
    a Chinese copy wins over an English one in bilingual folders. sizes maps path
    to bytes (from the tree listing); without it every file counts as 1.
    """
    sizes = sizes or {}
    md = [p for p in paths if p.lower().endswith((".md", ".markdown")) and not SKIP_NAMES.search(p) and not SKIP_DIRS.search(p)]
    md = drop_translations(md) or md
    if subpath:
        md = [p for p in md if p == subpath or p.startswith(subpath + "/")]
    else:
        roots = {}
        for path in md:
            roots.setdefault(path.split("/", 1)[0] if "/" in path else "", []).append(path)
        scored = sorted(((sum(sizes.get(p, 1) for p in items if _chapterish(p, r)),
                          sum(_chapterish(p, r) for p in items), r)
                         for r, items in roots.items() if r and not CHAPTER.search(r)), reverse=True)
        if scored and scored[0][1] >= 2:
            md = roots[scored[0][2]]
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

    def order(path):
        name = path.rsplit("/", 1)[-1]
        rank = 0 if INTRO.match(name) else 1 if CHAPTER.search(path) else 2   # afterword / answers go last
        return path.count("/"), rank, _natural(path)
    return sorted(kept, key=order)


def cjk_share(text):
    letters = sum(1 for ch in text if ch.isalpha())
    return len(CJK.findall(text)) / letters if letters else 0.0


def _clean(heading):
    """Drop Pandoc/kramdown attributes such as "{.unnumbered}" or "{#sec-intro}" from a heading."""
    return re.sub(r"\s*\{[^{}]*\}\s*$", "", heading or "").strip()


def _json(fetch, url, cancel):
    try:
        return json.loads(fetch(url, cancel).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RepoImportError("GITHUB_BAD_RESPONSE", "GitHub 返回的数据无法识别，请稍后重试。") from None


def default_fetch(url, cancel):
    from refactor.learning.course.research import ResearchService
    fetched = ResearchService(timeout=20.0, max_bytes=MAX_TOTAL).fetch_bytes(
        {"url": url, "title": ""}, cancel=cancel,
        allowed_types={"application/json", "text/plain", "text/markdown", "application/octet-stream"})
    return fetched.body


def import_repo(url: str, *, fetch=default_fetch, cancel=None, lists=None) -> VerifiedSource:
    parsed = parse(url)
    if parsed is None:
        raise RepoImportError("NOT_A_REPO", "这不是 GitHub 仓库地址。")
    owner, repo, branch, subpath = parsed
    screened = screen([RawHit(url=f"https://github.com/{owner}/{repo}")], lists=lists or load_lists(), user_provided=True)
    tier = screened[0].tier if screened else "neutral"
    meta = _json(fetch, f"https://api.github.com/repos/{owner}/{repo}", cancel)
    if not isinstance(meta, dict) or not meta.get("full_name"):
        raise RepoImportError("GITHUB_NOT_FOUND", "找不到这个仓库，或它不是公开仓库。")
    branch = branch or meta.get("default_branch") or "main"
    tree = _json(fetch, f"https://api.github.com/repos/{owner}/{repo}/git/trees/{quote(branch, safe='')}?recursive=1", cancel)
    entries = [item for item in (tree.get("tree") or []) if isinstance(item, dict) and item.get("type") == "blob"
               and isinstance(item.get("path"), str)]
    files = pick_files([e["path"] for e in entries], subpath,
                       {e["path"]: e["size"] for e in entries if type(e.get("size")) is int})
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
        chapter = _clean(doc.title or path)[:80]
        digest.update(path.encode("utf-8") + b"\0" + body + b"\0")
        for block in doc.blocks:
            rest = tuple(t for t in (_clean(s) for s in block.section) if t and t != chapter)[:2]
            blocks.append(TextBlock(block.text, block.kind, None, (chapter,) + rest))
    if not blocks:
        raise RepoImportError("NO_DOCS", "仓库里的文档都没有可用正文。")
    sha = digest.hexdigest()
    chunks = chunk_blocks(blocks, source_sha256=sha)
    title = f"{meta['full_name']}" + (f"：{meta['description']}" if meta.get("description") else "")
    document = ExtractedDocument(title[:200], "text/markdown", sha,
                                 sum(len(b.text) for b in blocks) + 2 * (len(blocks) - 1), tuple(blocks), chunks, tuple(warnings))
    canonical = f"https://github.com/{owner}/{repo}" + (f"/tree/{branch}/{subpath}" if subpath else "")
    folder = files[0].rsplit("/", 1)[0].split("/")[0] + "/" if "/" in files[0] else "仓库根目录"
    summary = (meta.get("description") or "") +         f"（读取了 {folder} 下 {len({b.section[0] for b in blocks})} 篇文档；要读其他部分可以贴对应子目录的地址）"
    return VerifiedSource(canonical, canonical, title[:200], "github.com", tier if tier in {"whitelist", "neutral", "low"} else "neutral",
                          "repository", ("github_docs",) + (("possible_fork",) if meta.get("fork") else ()), document, summary[:500],
                          datetime.now(timezone.utc).isoformat(), sha)

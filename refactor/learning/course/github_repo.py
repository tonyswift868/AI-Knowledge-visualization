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
from urllib.parse import quote, unquote

from .extract import ExtractedDocument, ExtractError, TextBlock, chunk_blocks, extract_bytes
from .research_pipeline import VerifiedSource
from .source_filter import RawHit, load_lists, screen

REPO_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?"
                      r"(?:/tree/([^/?#]+)((?:/[^?#]*)?))?/?$")
DOC_DIRS = ("docs", "doc", "book", "chapters", "tutorial", "tutorials", "content", "notes", "course")
SKIP_NAMES = re.compile(r"(?i)(^|/)((changelog|contributing|code_of_conduct|security|license|authors|pull_request_template|issue_template)[^/]*|_[^/]*)$")
SKIP_DIRS = re.compile(r"(?i)(^|/)(\.github|node_modules|vendor|site-packages|dist|build|\.vitepress|\.vuepress)/")
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
    return owner, repo, unquote(branch) if branch else None, unquote(sub or "").strip("/")


def _natural(path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path)]


CHAPTER = re.compile(r"(?i)(chapter|chap|ch|lesson|lecture|part|unit|week|day)[-_ ]?\d+|第.{1,4}[章讲节课]|^\d{1,3}[-_. ]")
INTRO = re.compile(r"(?i)^(readme|index|intro|introduction|preface|前言|序言|简介|引言)\.m")


# Registered, non-deprecated two-letter languages, scripts and regions from
# https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry
# (checked 2026-10-28). Support language[-Script][-Region], plus common underscore
# filenames; other BCP 47 forms stay independent. Never guess an unknown subtag.
LANGUAGE_CODES = frozenset("""
aa ab ae af ak am an ar as av ay az ba be bg bi bm bn bo br bs ca ce ch co cr cs
cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv
ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl
km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt
my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru
rw sa sc sd se sg sh si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl
tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu
""".split())
LANGUAGE_SCRIPTS = frozenset("""
adlm afak aghb ahom arab aran armi armn avst bali bamu bass batk beng berf bhks blis
bopo brah brai bugi buhd cakm cans cari cham cher chis chrs cirt copt cpmn cprt cyrl
cyrs deva diak dogr dsrt dupl egyd egyh egyp elba elym ethi gara geok geor glag gong
gonm goth gran grek gujr gukh guru hanb hang hani hano hans hant hatr hebr hira hluw
hmng hmnp hntl hrkt hung inds ital jamo java jpan jurc kali kana kawi khar khmr khoj
kitl kits knda kore kpel krai kthi lana laoo latf latg latn leke lepc limb lina linb
lisu loma lyci lydi mahj maka mand mani marc maya medf mend merc mero mlym modi mong
moon mroo mtei mult mymr nagm nand narb nbat newa nkdb nkgb nkoo nshu ogam olck onao
orkh orya osge osma ougr palm pauc pcun pelm perm phag phli phlp phlv phnx piqd plrd
prti psin ranj rjng rohg roro runr samr sara sarb saur seal sgnw shaw shrd shui sidd
sidt sind sinh sogd sogo sora soyo sund sunu sylo syrc syre syrj syrn tagb takr tale
talu taml tang tavt tayo telu teng tfng tglg thaa thai tibt tirh tnsa todr tols toto
tutg ugar vaii visp vith wara wcho wole xpeo xsux yezi yiii zanb zinh zmth zsye zsym
zxxx zyyy zzzz
""".split())
LANGUAGE_REGIONS = frozenset("""
001 002 003 005 009 011 013 014 015 017 018 019 021 029 030 034 035 039 053 054
057 061 142 143 145 150 151 154 155 202 419 aa ac ad ae af ag ai al am ao aq ar
as at au aw ax az ba bb bd be bf bg bh bi bj bl bm bn bo bq br bs bt bv bw by bz
ca cc cd cf cg ch ci ck cl cm cn co cp cq cr cu cv cw cx cy cz de dg dj dk dm do
dz ea ec ee eg eh er es et eu ez fi fj fk fm fo fr ga gb gd ge gf gg gh gi gl gm
gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu ic id ie il im in io iq ir is it je
jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc
md me mf mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na nc ne nf ng ni
nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw
sa sb sc sd se sg sh si sj sk sl sm sn so sr ss st sv sx sy sz ta tc td tf tg th
tj tk tl tm tn to tr tt tv tw tz ua ug um un us uy uz va vc ve vg vi vn vu wf ws
ye yt za zm zw zz
""".split())


def _language_tag(value):
    parts = value.lower().replace("_", "-").split("-")
    if parts[0] not in LANGUAGE_CODES:
        return None
    tail = parts[1:]
    if tail and tail[0] in LANGUAGE_SCRIPTS:
        tail = tail[1:]
    if tail and tail[0] in LANGUAGE_REGIONS:
        tail = tail[1:]
    return "-".join(parts) if not tail else None


def _variant(name, *, filename=False):
    """Strip only a complete registered language suffix, never technical words."""
    for match in re.finditer(r"[-_.]", name):
        base, suffix = name[:match.start()], name[match.end():]
        if base and (code := _language_tag(suffix)):
            # A bare hyphenated slug can be technical even when it is a language
            # code (sql-as, book-id). Dot locales, compound tags and _EN-style
            # suffixes are explicit; keep other file names rather than guessing.
            if (filename and match.group() != "." and "-" not in code
                    and not (match.group() == "_" and suffix.isupper())):
                continue
            return base, code
    return name, None


def drop_translations(paths, *, skipped=None):
    """Pair individual documents by their paths after removing explicit language tags.

    A translated directory does not prove every chapter has a translated copy.
    Files without a matching counterpart remain, regardless of their language.
    """
    groups = {}
    for path in paths:
        parts, key, codes = path.split("/"), [], []
        for name in parts[:-1]:
            code = _language_tag(name)
            base, code = ("", code) if code else _variant(name)
            if base:
                key.append(base)
            if code:
                codes.append(code)
        stem, extension = parts[-1].rsplit(".", 1)
        base, code = _variant(stem, filename=True)
        key.append(base + "." + extension)
        if code:
            codes.append(code)
        # Conflicting locale annotations are ambiguous; keep the whole path.
        if len({c.split("-")[0] for c in codes}) > 1:
            key, codes = parts, []
        groups.setdefault(tuple(key), []).append((path, codes))

    kept = set()
    for variants in groups.values():
        def preference(item):
            path, codes = item
            languages = {c.split("-")[0] for c in codes}
            return (0 if "zh" in languages else 1 if not codes else 2 if "en" in languages else 3,
                    path.lower(), path)
        chosen = min(variants, key=preference)[0]
        kept.add(chosen)
        if skipped is not None:
            skipped.extend((path, chosen) for path, _ in variants if path != chosen)
    return [path for path in paths if path in kept]


def _chapterish(path, root):
    """A chapter-like file within two levels of the root (deeper files are data sets, assets, …)."""
    rest = (path[len(root) + 1:] if root else path).split("/")
    return len(rest) <= 2 and any(CHAPTER.search(part) for part in rest)


def pick_files(paths, subpath="", sizes=None, *, warnings=None):
    """The book's Markdown files in reading order.

    Translations are dropped when an untranslated copy exists. Without a subpath,
    the top-level folder whose chapter-like files hold the most text is the book
    (docs/, book/, …; a folder that is itself one chapter, e.g. chapter3/, is not);
    Language copies are dropped only when the path identifies a paired variant. sizes maps path
    to bytes (from the tree listing); without it every file counts as 1.
    """
    sizes = sizes or {}
    md = [p for p in paths if p.lower().endswith((".md", ".markdown")) and not SKIP_NAMES.search(p) and not SKIP_DIRS.search(p)]
    if subpath:
        md = [p for p in md if p == subpath or p.startswith(subpath + "/")]
    skipped = []
    md = drop_translations(md, skipped=skipped)
    if not subpath:
        def book_root(path):
            root = path.split("/", 1)[0] if "/" in path else ""
            return "" if _language_tag(root) else _variant(root)[0]

        def chapter_in_root(path):
            root = path.split("/", 1)[0] if "/" in path else ""
            return _chapterish(path, root)

        roots = {}
        for path in md:
            roots.setdefault(book_root(path), []).append(path)
        # A partially translated book can retain chapters from both directories.
        # Score those together, or selecting the base directory loses its translations.
        scored = sorted(((sum(sizes.get(p, 1) for p in items if chapter_in_root(p)),
                          sum(chapter_in_root(p) for p in items), r)
                         for r, items in roots.items() if r and not CHAPTER.search(r)), reverse=True)
        if scored and scored[0][1] >= 2:
            md = roots[scored[0][2]]
        else:
            for folder in DOC_DIRS:
                inside = [p for p in md if book_root(p).lower() == folder]
                if len(inside) >= 2:
                    md = inside
                    break
    if warnings is not None:
        selected = set(md)
        notices = [f"{path} → 保留 {chosen}" for path, chosen in skipped if chosen in selected]
        if notices:
            warnings.append("按明确语言标识和对应文档路径跳过译本（优先中文，否则原文或英文）：" + "；".join(notices))
    def order(path):
        name = path.rsplit("/", 1)[-1]
        rank = 0 if INTRO.match(name) else 1 if CHAPTER.search(path) else 2   # afterword / answers go last
        return path.count("/"), rank, _natural(path)
    return sorted(md, key=order)


def _clean(heading):
    """Drop Pandoc/kramdown attributes such as "{.unnumbered}" or "{#sec-intro}" from a heading."""
    return re.sub(r"\s*\{[^{}]*\}\s*$", "", heading or "").strip()


def _json(fetch, url, cancel):
    from .research import ResearchFetchError
    try:
        return json.loads(fetch(url, cancel).decode("utf-8"))
    except ResearchFetchError as exc:
        if exc.status_code == 404:
            raise RepoImportError("GITHUB_NOT_FOUND", "找不到这个仓库或分支，或它不是公开仓库；请确认链接正确。") from None
        if exc.status_code in {403, 429}:
            raise RepoImportError("GITHUB_RATE_LIMIT", "GitHub 公开接口访问受限，请确认仓库公开，或稍后重试。") from None
        raise
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
    default_branch = meta.get("default_branch") or "main"
    if branch and "/" in default_branch:
        requested = branch + ("/" + subpath if subpath else "")
        if requested == default_branch or requested.startswith(default_branch + "/"):
            branch, subpath = default_branch, requested[len(default_branch):].strip("/")
    branch = branch or default_branch

    def read_tree(ref, *, recursive=True):
        suffix = "?recursive=1" if recursive else ""
        value = _json(fetch, f"https://api.github.com/repos/{owner}/{repo}/git/trees/{quote(ref, safe='')}{suffix}", cancel)
        if not isinstance(value, dict) or not isinstance(value.get("tree"), list):
            raise RepoImportError("GITHUB_BAD_RESPONSE", "GitHub 返回的目录树无法识别，请稍后重试。")
        return value

    def truncated_tree_error():
        return RepoImportError(
            "GITHUB_TREE_TRUNCATED",
            "GitHub 返回的目录树仍不完整，且没有提供缺失路径清单，无法确认具体缺少哪些文档；请下载该目录后上传，或改贴更小的子目录地址重试。",
        )

    def read_subpath_tree(path):
        tree = read_tree(branch, recursive=False)
        current_sha = None
        segments = path.split("/")
        for index, segment in enumerate(segments):
            entry = next((item for item in tree["tree"] if isinstance(item, dict)
                          and item.get("path") == segment and item.get("type") == "tree"), None)
            if not entry or not isinstance(entry.get("sha"), str):
                raise truncated_tree_error()
            current_sha = entry["sha"]
            if index < len(segments) - 1:
                tree = read_tree(current_sha, recursive=False)
        subtree = read_tree(current_sha)
        if subtree.get("truncated") is True:
            raise truncated_tree_error()
        return {**subtree, "tree": [
            {**item, "path": f"{path}/{item['path']}"}
            for item in subtree["tree"] if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]}

    try:
        tree = read_tree(branch)
    except RepoImportError as exc:
        if exc.code != "GITHUB_NOT_FOUND" or not subpath:
            raise
        # /tree/feature/new/docs is ambiguous until GitHub confirms the ref.
        # Normal main/docs links keep the original single tree request.
        tail, original = subpath.split("/"), exc
        for length in range(len(tail), 0, -1):
            candidate = "/".join([branch, *tail[:length]])
            try:
                tree = read_tree(candidate)
            except RepoImportError as trial:
                if trial.code == "GITHUB_NOT_FOUND":
                    continue
                raise
            branch, subpath = candidate, "/".join(tail[length:])
            break
        else:
            raise original
    if tree.get("truncated") is True:
        if not subpath:
            raise truncated_tree_error()
        tree = read_subpath_tree(subpath)
    entries = [item for item in (tree.get("tree") or []) if isinstance(item, dict) and item.get("type") == "blob"
               and isinstance(item.get("path"), str)]
    warnings = []
    files = pick_files([e["path"] for e in entries], subpath,
                       {e["path"]: e["size"] for e in entries if type(e.get("size")) is int},
                       warnings=warnings)
    if not files:
        raise RepoImportError("NO_DOCS", "这个仓库里没有找到 Markdown 文档。")
    if len(files) > MAX_FILES:
        warnings.append(f"文档共 {len(files)} 篇，只读取了前 {MAX_FILES} 篇。")
        files = files[:MAX_FILES]
    blocks, raw_total, digest = [], 0, hashlib.sha256()
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

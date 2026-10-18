"""Offline, deterministic screening of search hits; not an approval to display.

Invalid URLs are dropped. Blocked, low-quality, fork-like and duplicate hits
remain labelled for caller inspection, in input order. A whitelist is a source
priority, not proof of relevance, accuracy or safety of an individual page.
Neither screening nor URL normalization fetches a page. Only the explicitly
injected ``fetch`` in ``is_probable_fork`` can perform an external lookup.

The HTTP(S)/host/no-credentials/fragment checks follow the existing planner's
research._safe_candidate_url, split here to avoid its network dependencies.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatchcase
import ipaddress
import json
from pathlib import Path
import re
from types import MappingProxyType
import unicodedata
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class RawHit:
    url: str
    title: str = ""
    snippet: str = ""
    query: str = ""


@dataclass(frozen=True)
class ScreenedHit:
    url: str
    original_url: str
    title: str
    tier: str
    category: str
    reasons: tuple[str, ...]
    duplicate_of: str | None


@dataclass(frozen=True)
class DomainRule:
    match: str
    reason: str
    brand: str = ""
    category: str = "unknown"


@dataclass(frozen=True)
class GitHubOrg:
    owner: str
    brand: str
    reason: str


@dataclass(frozen=True)
class SourceLists:
    version: int
    updated: str
    whitelist: tuple[DomainRule, ...]
    github_orgs: tuple[GitHubOrg, ...]
    low: tuple[DomainRule, ...]
    blocked: tuple[DomainRule, ...]
    blocked_keywords: Mapping[str, tuple[str, ...]]
    candidates: tuple[DomainRule, ...]
    type_rules: tuple[dict, ...] = ()
    directory: tuple[dict, ...] = ()


class SourceListsError(ValueError):
    """A list file failed validation; message includes its file and field."""


_CATEGORIES = frozenset({"encyclopedia", "official_docs", "academic", "education",
    "standards", "government", "reference"})
VIDEO_HOSTS = ('youtube.com', 'youtu.be', 'bilibili.com', 'b23.tv', 'vimeo.com',
               'youku.com', 'iqiyi.com', 'tencentvideo.com', 'v.qq.com',
               'douyin.com', 'ixigua.com', 'udemy.com', 'coursera.org',
               'edx.org', 'icourse163.org', 'xuetangx.com', 'imooc.com')
_KEYWORD_CATEGORIES = ("adult", "gambling", "piracy")
_OWNER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?", re.I)
_REPO = re.compile(r"[a-z0-9_.-]{1,100}", re.I)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.I)
_BAD_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_TRACKING = frozenset({"spm", "from", "frompage", "share", "share_id", "share_source", "share_token",
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "yclid", "mc_cid", "mc_eid",
    "__cf_chl_tk", "__cf_chl_rt_tk"})
_ARXIV_HOST = re.compile(r"(?:(?:www|export|web\d+)\.)?arxiv\.org")
_ARXIV_ID = re.compile(r"(?:\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}|[a-z][a-z0-9.-]*/\d{2}(?:0[1-9]|1[0-2])\d{3})(?:v\d+)?", re.I)
_GITHUB_NON_OWNERS = frozenset({"orgs", "users", "topics", "search", "settings", "login",
    "logout", "signup", "marketplace", "features", "sponsors", "collections", "site"})


def _host(value: str) -> str:
    value = value.rstrip(".").lower()
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        pass
    encoded = value.encode("idna").decode("ascii")
    if not encoded or len(encoded) > 253 or any(not _DNS_LABEL.fullmatch(part) for part in encoded.split(".")):
        raise ValueError("invalid hostname")
    return encoded


def _percent_path(path: str) -> str:
    def replace(match):
        character = chr(int(match[0][1:], 16))
        return character if character in _UNRESERVED else match[0].upper()
    return re.sub(r"%[0-9a-fA-F]{2}", replace, path)


def normalize_url(url: str) -> str | None:
    """Normalize identity without resolving redirects, ref names or page content.

    Preserve nontracking query values/order, branch/file case and distinct Git
    refs. Only owner/repository names are case-insensitive on GitHub. Strip
    versions from both modern and pre-2007 arXiv identifiers.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    value = url.strip()
    if (any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in value)
            or "\\" in value or _BAD_ESCAPE.search(value)):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return None
        scheme, host, port = parsed.scheme.lower(), _host(parsed.hostname), parsed.port
        if port == 0 or parsed.netloc.endswith(":"):
            return None
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != (443 if scheme == "https" else 80):
            authority += f":{port}"
        path = _percent_path(parsed.path).rstrip("/")
        pairs = parse_qsl(parsed.query, keep_blank_values=True, encoding="utf-8", errors="strict")
        pairs = [(key, val) for key, val in pairs if key.casefold() not in _TRACKING
                 and not key.casefold().startswith("utm_")]
        query = urlencode(pairs)
        standard_port = port is None or port == (443 if scheme == "https" else 80)
        if _ARXIV_HOST.fullmatch(host) and standard_port:
            match = re.fullmatch(r"/(?:abs|pdf|html)/(.+)", unquote(path))
            if match:
                identifier = re.sub(r"\.pdf$", "", match[1], flags=re.I)
                if not _ARXIV_ID.fullmatch(identifier):
                    return None
                identifier = re.sub(r"v\d+$", "", identifier, flags=re.I)
                return "https://arxiv.org/abs/" + identifier
        if host == "raw.githubusercontent.com" and standard_port:
            parts = path.lstrip("/").split("/")
            if len(parts) < 4 or any(not part for part in parts[:4]):
                return None
            owner, repo = _repo_parts(parts[0], parts[1]) or (None, None)
            if owner is None:
                return None
            # Keep refs/heads or refs/tags explicit if supplied; do not guess
            # slash-containing refs or assume that master points to main.
            path = f"/{owner}/{repo}/blob/" + "/".join(parts[2:])
            return urlunsplit(("https", "github.com", path, query, ""))
        if host in {"github.com", "www.github.com"} and standard_port:
            parts = path.lstrip("/").split("/")
            if len(parts) >= 2 and (repo := _repo_parts(parts[0], parts[1])):
                path = "/" + "/".join((*repo, *parts[2:]))
            authority = "github.com"
        return urlunsplit((scheme, authority, path, query, ""))
    except (ValueError, UnicodeError):
        return None


def _repo_parts(owner: str, repo: str) -> tuple[str, str] | None:
    owner, repo = unquote(owner).casefold(), unquote(repo).casefold()
    if repo.endswith(".git"):
        repo = repo[:-4]
    if (_OWNER.fullmatch(owner) and _REPO.fullmatch(repo)
            and owner not in _GITHUB_NON_OWNERS and repo not in {".", ".."}):
        return owner, repo
    return None


def github_repo(url: str) -> tuple[str, str] | None:
    normalized = normalize_url(url)
    if normalized is None:
        return None
    parsed = urlsplit(normalized)
    if parsed.hostname != "github.com":
        return None
    parts = parsed.path.lstrip("/").split("/")
    return _repo_parts(*parts[:2]) if len(parts) >= 2 else None


def github_recommendation_key(url: str) -> str | None:
    """Return repository-wide identity for recommendation deduplication.

    GitHub's repository home, README/blob links, branches and subdirectories
    all describe one importable repository. Keep ``normalize_url`` page-level
    because other callers need precise URL identity.
    """
    repo = github_repo(url)
    return f"github:{repo[0]}/{repo[1]}" if repo else None


_GITHUB_NONCONTENT_ROUTES = frozenset({
    "releases", "tags", "issues", "pull", "pulls", "actions",
    "projects", "discussions", "commits", "branches", "compare",
    "network", "settings", "security", "notifications",
})


def is_noncontent_github_url(url: str) -> bool:
    """Whether a GitHub URL is a repository management/activity page.

    The repository wiki is intentionally not rejected here: it may contain
    substantive documentation. It still shares repository identity for
    deduplication against a user-provided repository.
    """
    if not github_repo(url):
        return False
    normalized = normalize_url(url)
    if not normalized:
        return False
    parts = [unquote(part).casefold() for part in urlsplit(normalized).path.strip("/").split("/")]
    return len(parts) >= 3 and parts[2] in _GITHUB_NONCONTENT_ROUTES


def _matches(host: str, rule: DomainRule) -> bool:
    if "*" in rule.match or "?" in rule.match:
        return fnmatchcase(host, rule.match)
    return host == rule.match or host.endswith("." + rule.match)


def _low_matches(host, rule):
    # Older cached/server catalogs excluded the whole CSDN domain. The current
    # product policy allows its community articles, while keeping ad portals low.
    if rule.match in {'csdn.net','csdn.com'}:
        return host in {'adg.csdn.net','ad.csdn.net'}
    return _matches(host,rule)


def _keyword_text(value: str) -> str:
    # Detect obvious percent-encoded/full-width variants without fetching. Do
    # not treat a snippet or the user's query as a page's title or identity.
    for _ in range(3):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(ch for ch in value if unicodedata.category(ch) != "Cf").split())


def _has_keyword(text: str, keyword: str) -> bool:
    keyword = _keyword_text(keyword)
    if keyword.isascii():
        # URL hosts often concatenate words (freeporn, onlinecasino). Match
        # the listed terms there too; the draft avoids ambiguous bare words
        # such as sex, adult, bet, slots, torrent and free PDF.
        words = re.split(r"[\s_-]+", keyword)
        pattern = r"[\s_\-+]+".join(re.escape(word) for word in words)
        return re.search(pattern, text) is not None
    return keyword in text


def blocked_keyword_reasons(text: str, *, lists: SourceLists) -> tuple[str, ...]:
    """Apply the same reviewed keyword policy to a title, URL or extracted body."""
    text = _keyword_text(text)
    return tuple(kind for kind in _KEYWORD_CATEGORIES
        if any(_has_keyword(text, keyword) for keyword in lists.blocked_keywords[kind]))


def recommendation_rejection(url: str, *, lists=None, allow_video=False) -> str:
    """URL rules for complete text material recommendations; no HTTP or body analysis."""
    normalized = normalize_url(url)
    if not normalized:
        return '链接无效'
    parsed = urlsplit(normalized)
    if is_noncontent_github_url(normalized):
        return 'GitHub 仓库管理或动态页面，不是正文资料'
    policy = lists or load_lists()
    local = load_lists()
    # Only the recommendation worker opts into the video-only fallback. Other
    # import/screening policy is unchanged, as are non-video blocked sources.
    if any(_matches(parsed.hostname, rule) and not (allow_video and rule.match in VIDEO_HOSTS)
           for rule in (*policy.blocked, *local.blocked)):
        return '视频站或屏蔽来源'
    path = unquote(parsed.path).lower()
    if not path or re.fullmatch(r'/[a-z]{2}(?:-[a-z]{2})?', path):
        return '网站首页，不是完整资料目录'
    if re.search(r'/(?:signup|sign-up|signin|sign-in|login|log-in|enroll|register)(?:/|$)', path):
        return '视频、报名或登录页'
    if not allow_video and re.search(r'/(?:videos?|watch)(?:/|$)', path):
        return '视频、报名或登录页'
    if re.search(r'/(select-a-goal|syncclassroom)(/|$)', path):
        return '通用入口，不是完整资料目录'
    if (_is_host(parsed.hostname, 'runoob.com') and re.fullmatch(r'/computer(?:-organization)?/[^/]+\.html', path)
            and not path.endswith(('/index.html', '/computer.html'))):
        return '单个小节，不是完整资料目录'
    return ''


def screen(hits: Iterable[RawHit], *, lists: SourceLists | None = None, user_provided=False) -> list[ScreenedHit]:
    """Drop invalid URLs and annotate all other hits, including blocked ones.

    Consumers must exclude blocked/duplicate hits, assess neutral/low/fork/raw
    hits and verify actual page relevance separately. Candidates grant no trust.
    ``possible_fork`` is a batch heuristic, never a verified GitHub fork claim.
    """
    lists = load_lists() if lists is None else lists
    if not isinstance(lists, SourceLists):
        raise TypeError("lists must be SourceLists returned by load_lists()")
    valid = []
    owners_by_repo: dict[str, list[str]] = {}
    official = {entry.owner for entry in lists.github_orgs}
    for hit in hits:
        if not isinstance(hit, RawHit):
            raise TypeError("hits must contain RawHit instances")
        normalized = normalize_url(hit.url)
        if normalized is None:
            continue
        repo = github_repo(normalized)
        valid.append((hit, normalized, repo))
        if repo:
            owners = owners_by_repo.setdefault(repo[1], [])
            if repo[0] not in owners:
                owners.append(repo[0])
    output, seen = [], set()
    for hit, normalized, repo in valid:
        if user_provided:
            # Explicit imports use only URL/readability checks. Recommendation
            # publisher, blocklist and fork heuristics do not judge user text.
            duplicate = normalized if normalized in seen else None
            output.append(ScreenedHit(normalized, hit.url, hit.title, 'neutral',
                'user_selected', ('duplicate',) if duplicate else (), duplicate))
            seen.add(normalized)
            continue
        host = urlsplit(normalized).hostname
        original_host = _host(urlsplit(hit.url.strip()).hostname)
        hosts = {host, original_host}
        reasons: list[str] = []
        tier, category = "neutral", "github_repository" if repo else "unknown"
        if any(_matches(name, rule) for rule in lists.blocked for name in hosts):
            tier, category = "blocked", "blocked_site"
            reasons.append("blocked_site")
        elif any(_low_matches(name, rule) for rule in lists.low for name in hosts):
            tier, category = "low", "repost"
            reasons.append("repost_site")
        else:
            rule = next((entry for entry in lists.whitelist if _matches(host, entry)), None)
            if rule:
                tier, category = "whitelist", rule.category
            elif repo and repo[0] in official:
                tier = "whitelist"
            identity = classify_source(normalized, lists=lists)
            if identity['level'] in (1, 2):
                tier, category = 'whitelist', identity['category']
        if "webvpn" in original_host:
            tier, category = "blocked", "proxy"
            reasons.append("webvpn_proxy")
        keyword_categories = blocked_keyword_reasons(hit.url + " " + hit.title, lists=lists)
        if keyword_categories:
            tier, category = "blocked", keyword_categories[0]
            reasons.extend(keyword_categories)
        if repo:
            owners = owners_by_repo[repo[1]]
            preferred = [owner for owner in owners if owner in official]
            if len(owners) > 1 and repo[0] not in (preferred or owners[:1]):
                reasons.append("possible_fork")
                if tier == "whitelist":
                    tier = "neutral"
        if original_host == "raw.githubusercontent.com":
            reasons.append("raw_file")
        duplicate_of = normalized if normalized in seen else None
        if duplicate_of:
            reasons.append("duplicate")
        seen.add(normalized)
        output.append(ScreenedHit(normalized, hit.url, hit.title, tier, category,
            tuple(reasons), duplicate_of))
    return output


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_lists(path: str | Path | None = None) -> SourceLists:
    """Read a reviewed JSON draft; reject invalid/ambiguous policy explicitly.

    Domain suffixes match whole labels only. Only blocked rules accept glob
    patterns (``*``/``?``); patterns are anchored to the complete hostname.
    More specific whitelist domains take precedence for category selection.
    """
    path = Path(path) if path is not None else Path(__file__).with_name("data") / "source_lists.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SourceListsError(f"{path}: cannot read source lists: {exc}") from exc
    return parse_lists(data, source=str(path))


def parse_lists(data, *, source="source lists", allow_candidates=True) -> SourceLists:
    """Validate an in-memory catalog without writing an untrusted file first.

    Server catalogs MUST omit candidates (allow_candidates=False). The local
    draft keeps its original schema; candidates never grant source trust.
    """
    def fail(field, message):
        raise SourceListsError(f"{source}: {field}: {message}")
    if not isinstance(data, dict):
        fail("root", "expected an object")
    if type(data.get('version')) is not int or data['version'] not in (1,2):
        fail('version','expected supported integer version 1 or 2')
    required = {"version", "updated", "whitelist", "github_orgs", "low", "blocked", "blocked_keywords"}
    if data.get('version') == 2:
        required.update(('type_rules', 'directory'))
    if allow_candidates:
        required.add("candidates")
    if set(data) != required:
        fail("root", f"missing keys {sorted(required - set(data))}; unknown keys {sorted(set(data) - required)}")
    data = dict(data)
    data.setdefault("candidates", [])
    if type(data["version"]) is not int or data["version"] not in (1, 2):
        fail("version", "expected supported integer version 1 or 2")
    try:
        if not isinstance(data["updated"], str) or date.fromisoformat(data["updated"]).isoformat() != data["updated"]:
            raise ValueError()
    except ValueError:
        fail("updated", "expected YYYY-MM-DD date")

    def text(entry, key, field):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            fail(field + "." + key, "expected a nonempty trimmed string")
        return value

    def entries(key, fields):
        if not isinstance(data[key], list):
            fail(key, "expected an array")
        for index, entry in enumerate(data[key]):
            field = f"{key}[{index}]"
            if not isinstance(entry, dict) or set(entry) != fields:
                fail(field, f"expected exactly fields {sorted(fields)}")
            yield entry, field

    def domains(key):
        rules, seen = [], set()
        branded = key in {"whitelist", "candidates"}
        fields = {"match", "reason", "brand", "category"} if branded else {"match", "reason"}
        for entry, field in entries(key, fields):
            match = text(entry, "match", field).casefold()
            try:
                if "*" in match or "?" in match:
                    if key != "blocked" or not re.fullmatch(r"[a-z0-9.*?-]+", match):
                        raise ValueError()
                    _host(match.replace("*", "x").replace("?", "x"))
                else:
                    match = _host(match)
                if "." not in match:
                    raise ValueError()
            except (ValueError, UnicodeError):
                fail(field + ".match", "expected domain suffix (blocked also permits hostname glob)")
            if match in seen:
                fail(field + ".match", "duplicate domain rule")
            seen.add(match)
            category = text(entry, "category", field) if branded else "unknown"
            if branded and category not in _CATEGORIES:
                fail(field + ".category", "unknown source category")
            rules.append(DomainRule(match, text(entry, "reason", field),
                text(entry, "brand", field) if branded else "", category))
        return tuple(sorted(rules, key=lambda rule: -len(rule.match)))

    whitelist, low, blocked, candidates = (domains(key) for key in ("whitelist", "low", "blocked", "candidates"))
    orgs, seen_orgs = [], set()
    for entry, field in entries("github_orgs", {"owner", "brand", "reason"}):
        owner = text(entry, "owner", field).casefold()
        if not _OWNER.fullmatch(owner) or owner in _GITHUB_NON_OWNERS:
            fail(field + ".owner", "expected a GitHub organization owner")
        if owner in seen_orgs:
            fail(field + ".owner", "duplicate GitHub owner")
        seen_orgs.add(owner)
        orgs.append(GitHubOrg(owner, text(entry, "brand", field), text(entry, "reason", field)))
    keywords = data["blocked_keywords"]
    if not isinstance(keywords, dict) or set(keywords) != set(_KEYWORD_CATEGORIES):
        fail("blocked_keywords", "expected adult, gambling and piracy arrays")
    normalized_keywords = {}
    for category, values in keywords.items():
        field = "blocked_keywords." + category
        if not isinstance(values, list) or not values:
            fail(field, "expected a nonempty array")
        if any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in values):
            fail(field, "keywords must be nonempty trimmed strings")
        normalized = tuple(_keyword_text(v) for v in values)
        if any(not v for v in normalized) or len(set(normalized)) != len(normalized):
            fail(field, "empty or duplicate normalized keyword")
        normalized_keywords[category] = normalized
    types, directory = [], []
    if data['version'] == 2:
        for entry, field in entries('type_rules', {'type', 'level', 'suffixes', 'publisher', 'category', 'reason'}):
            if type(entry['level']) is not int or entry['level'] not in (1, 2):
                fail(field + '.level', 'expected 1 or 2')
            if not isinstance(entry['suffixes'], list) or not entry['suffixes']:
                fail(field + '.suffixes', 'expected nonempty domain suffix array')
            for suffix in entry['suffixes']:
                try: _host(suffix)
                except (ValueError, TypeError): fail(field + '.suffixes', 'invalid domain suffix')
            for key in ('type', 'publisher', 'category', 'reason'): text(entry, key, field)
            types.append(dict(entry))
        for entry, field in entries('directory', {'id', 'title', 'url', 'subjects', 'stages', 'keywords', 'available', 'verified_at', 'verification'}):
            for key in ('id', 'title', 'url', 'verified_at', 'verification'): text(entry, key, field)
            if not normalize_url(entry['url']): fail(field+'.url', 'invalid URL')
            for key in ('subjects', 'stages', 'keywords', 'available'):
                if not isinstance(entry[key], list) or any(not isinstance(v,str) or not v for v in entry[key]):
                    fail(field+'.'+key, 'expected string array')
            directory.append(dict(entry))
        if len({e['id'] for e in directory}) != len(directory): fail('directory', 'duplicate id')
    return SourceLists(data['version'], data["updated"], whitelist, tuple(orgs), low, blocked,
        MappingProxyType(normalized_keywords), candidates, tuple(types), tuple(directory))


def type_catalog(lists=None):
    """Local type policy remains available with the existing v1 server catalog."""
    lists = lists or load_lists()
    return lists if lists.version == 2 else load_lists()


_COMMUNITY_HOSTS = ('csdn.net', 'csdn.com', 'zhihu.com', 'tieba.baidu.com', 'stackoverflow.com',
                    'stackexchange.com', 'reddit.com', 'quora.com', 'cnblogs.com',
                    'developer.aliyun.com', 'juejin.cn', 'juejin.im')
_GENERAL_HOSTS = ('baike.baidu.com', 'wikipedia.org', 'bbc.com', 'bbc.co.uk', 'xinhuanet.com',
                  'people.com.cn', 'news.cn', 'thepaper.cn', 'reuters.com',
                  # Batch42: fetched original question/answer pages and visible
                  # publisher titles on 2026-10-03; general band only, not 1/2.
                  'geeksforgeeks.org', 'w4school.in', '51jiaoxi.com')


def _is_host(host, suffix):
    return host == suffix or host.endswith('.' + suffix)


def valid_publisher(value):
    """A name, not a footer, legal notice, sales instruction or URL."""
    return (isinstance(value, str) and 1 < len(value.strip()) <= 80
            and bool(re.search(r'[A-Za-z\u4e00-\u9fff]', value))
            and not re.search(r'版权|著作权|免责声明|侵权|联系我们|联系邮箱|不承担|购买|扫码|'
                              r'所有，如|匿名|佚名|unknown|anonymous|copyright|all rights reserved|'
                              r'https?://|www\.|@|[{}<>]', value, re.I))


def classify_source(url, *, lists=None, publisher_evidence=None, page_text=''):
    """Publisher classification, never a claim that an individual page is a lesson.

    Unknown sites require a visible byline or publisher quotation. Community
    pages are level 3 and cannot be elevated by an injected type claim.
    """
    lists = lists or load_lists()
    normalized = normalize_url(url)
    result = dict(level=0, type='unknown', publisher='', category='unknown', evidence='')
    if not normalized and url != '': return result
    # Imported books may have no web URL; publisher imprint evidence in their
    # extracted original text is checked by the same classifier.
    normalized = normalized or ''
    host = urlsplit(normalized).hostname or ''
    if ('webvpn' in host or any(_low_matches(host,r) for r in lists.low) or any(_matches(host,r) for r in lists.blocked)
            or blocked_keyword_reasons(url+' '+page_text, lists=lists)
            or re.search(r'转载自|转自\s*[:：]|转载\s*[:：]|reprinted\s+from|originally published (?:at|on)',page_text,re.I)
            ):
        return dict(result, type='excluded')
    path = urlsplit(normalized).path
    if (any(_is_host(host,h) for h in _COMMUNITY_HOSTS)
            or (any(_is_host(host,h) for h in ('cloud.tencent.com', 'cloud.tencent.com.cn', 'ctyun.cn'))
                and path.startswith('/developer/'))
            or re.search(r'/(?:forums?|discussions?|issues|pulls?)(?:/|$)',path,re.I)
            or (host=='learn.microsoft.com' and ('/answers/' in path or '/archive/blogs/' in path))):
        return dict(level=3,type='community',publisher=host,category='community',evidence='社区公开页面')
    # /doc/stable/user/ and /stable/users/ are official user guides, not
    # account homepages. Generic /users/<name> lives at the site root.
    personal = bool(re.search(r'/(?:~|students?/|personal/)|^/users?/',path,re.I))
    for rule in type_catalog(lists).type_rules:
        if not personal and any(host == suffix or host.endswith('.'+suffix) for suffix in rule['suffixes']):
            return dict(level=rule['level'],type=rule['type'],publisher=rule['publisher'],
                        category=rule['category'],evidence=rule['reason'])
    evidence = publisher_evidence or {}
    quote = evidence.get('quote','')
    allowed = {r['type']:r for r in type_catalog(lists).type_rules}
    if (evidence.get('type') in allowed and isinstance(quote,str) and len(quote.strip()) >= 8
            and quote in page_text and valid_publisher(evidence.get('publisher'))):
        rule = allowed[evidence['type']]
        return dict(level=rule['level'],type=rule['type'],publisher=evidence['publisher'],
                    category=rule['category'],evidence=quote)
    general_types = {'general_site','news_media','encyclopedia','company','teacher'}
    if (evidence.get('type') in general_types and isinstance(quote,str) and len(quote.strip()) >= 8
            and quote in page_text and valid_publisher(evidence.get('publisher'))):
        return dict(level=3,type='general_site',publisher=evidence['publisher'],category='general_site',evidence=quote)
    if any(_is_host(host,h) for h in _GENERAL_HOSTS) and ':' not in path and '/w/' not in path:
        return dict(level=3,type='general_site',publisher=host,category='general_site',evidence='公开媒体或百科页面')
    # A visible byline/imprint identifies a general publisher; it is not proof
    # of official/academic authority and can never elevate the page above 3.
    for match in re.finditer(r'(?:^|\n)\s*(?:发布者|发布机构|主办单位|作者|撰稿|来源|Author|Published by)\s*[:：]\s*([^\n。；;<>]{2,80})', page_text, re.I):
        name = re.split(r'\s+(?:发布时间|更新时间|上传时间|20\d\d[-/])', match[1])[0].strip()
        if valid_publisher(name):
            return dict(level=3,type='general_site',publisher=name,category='general_site',evidence=match[0].strip())
    return result


def directory_matches(query, *, stage='', subject='', lists=None, limit=8):
    """Rank matching curriculum entries; inaccessible records stay inspectable."""
    text = (query+' '+stage+' '+subject).casefold()
    def matches(keyword):
        key=keyword.casefold()
        if re.search(r'[a-z]',key):
            return bool(re.search(r'(?<![a-z0-9_])'+re.escape(key)+r'(?![a-z0-9_])',text))
        return key in text
    scored = []
    for entry in type_catalog(lists).directory:
        score = sum(4 for k in entry['subjects'] if matches(k))
        score += sum(2 for k in entry['stages'] if matches(k))
        score += sum(6 for k in entry['keywords'] if matches(k))
        # A stage alone must not suggest another discipline's textbook.
        match = any(matches(k) for k in (*entry['subjects'],*entry['keywords']))
        if score and match: scored.append((score,entry))
    return [dict(e) for _,e in sorted(scored,key=lambda v:(-v[0],v[1]['id']))[:limit]]


def is_probable_fork(owner: str, repo: str, *, fetch: Callable[[str], dict]) -> bool | None:
    """Return GitHub's boolean fork flag; failure/absent/malformed means unknown.

    The caller owns transport, credentials and rate limits. This module never
    creates a network client and never treats a failed lookup as ``False``.
    """
    try:
        identity = _repo_parts(owner, repo)
        if identity is None:
            return None
        response = fetch("https://api.github.com/repos/" + "/".join(quote(part, safe="") for part in identity))
        flag = response.get("fork") if isinstance(response, dict) else None
        return flag if type(flag) is bool else None
    except Exception:
        return None

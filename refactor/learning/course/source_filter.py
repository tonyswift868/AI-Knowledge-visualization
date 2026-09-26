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


class SourceListsError(ValueError):
    """A list file failed validation; message includes its file and field."""


_CATEGORIES = frozenset({"encyclopedia", "official_docs", "academic", "education",
    "standards", "government", "reference"})
_KEYWORD_CATEGORIES = ("adult", "gambling", "piracy")
_OWNER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?", re.I)
_REPO = re.compile(r"[a-z0-9_.-]{1,100}", re.I)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.I)
_BAD_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_TRACKING = frozenset({"spm", "from", "share", "share_id", "share_source", "share_token",
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


def _matches(host: str, rule: DomainRule) -> bool:
    if "*" in rule.match or "?" in rule.match:
        return fnmatchcase(host, rule.match)
    return host == rule.match or host.endswith("." + rule.match)


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


def screen(hits: Iterable[RawHit], *, lists: SourceLists | None = None) -> list[ScreenedHit]:
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
        host = urlsplit(normalized).hostname
        original_host = _host(urlsplit(hit.url.strip()).hostname)
        hosts = {host, original_host}
        reasons: list[str] = []
        tier, category = "neutral", "github_repository" if repo else "unknown"
        if any(_matches(name, rule) for rule in lists.blocked for name in hosts):
            tier, category = "blocked", "blocked_site"
            reasons.append("blocked_site")
        elif any(_matches(name, rule) for rule in lists.low for name in hosts):
            tier, category = "low", "repost"
            reasons.append("repost_site")
        else:
            rule = next((entry for entry in lists.whitelist if _matches(host, entry)), None)
            if rule:
                tier, category = "whitelist", rule.category
            elif repo and repo[0] in official:
                tier = "whitelist"
        if "webvpn" in original_host:
            tier, category = "blocked", "proxy"
            reasons.append("webvpn_proxy")
        text = _keyword_text(hit.url + " " + hit.title)
        keyword_categories = [kind for kind in _KEYWORD_CATEGORIES
            if any(_has_keyword(text, keyword) for keyword in lists.blocked_keywords[kind])]
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
    def fail(field, message):
        raise SourceListsError(f"{path}: {field}: {message}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SourceListsError(f"{path}: cannot read source lists: {exc}") from exc
    if not isinstance(data, dict):
        fail("root", "expected an object")
    required = {"version", "updated", "whitelist", "github_orgs", "low", "blocked", "blocked_keywords", "candidates"}
    if set(data) != required:
        fail("root", f"missing keys {sorted(required - set(data))}; unknown keys {sorted(set(data) - required)}")
    if type(data["version"]) is not int or data["version"] != 1:
        fail("version", "expected supported integer version 1")
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
    return SourceLists(1, data["updated"], whitelist, tuple(orgs), low, blocked,
        MappingProxyType(normalized_keywords), candidates)


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

"""Small, account-independent research primitives for the local planner.

The planner must keep resource discovery and document reading separate from
course generation.  This module only performs an actual web search or reads a
user-selected URL.  The returned page text is reference material; it is never
executed or interpreted as an instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
import hashlib
import ipaddress
import os
from datetime import datetime, timezone
from pathlib import PurePosixPath
import re
import socket
import time
from collections.abc import Callable, Mapping
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs

import httpx

try:  # The desktop bundle can run without the optional document extras.
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - exercised in the minimal desktop env
    BeautifulSoup = None  # type: ignore[assignment,misc]


class ResearchError(RuntimeError):
    """Base class for visible search, fetch, and cancellation failures."""

    code = "RESEARCH_FAILED"


class ResearchInputError(ResearchError, ValueError):
    code = "RESEARCH_INPUT_INVALID"


class ResearchCancelled(ResearchError):
    code = "RESEARCH_CANCELLED"


# The longer name is convenient for callers that expose an error type in UI
# code, while the short name remains the public spelling used by this module.
ResearchCancelledError = ResearchCancelled


class ResearchSearchError(ResearchError):
    code = "RESEARCH_SEARCH_FAILED"


class SearchDependencyError(ResearchSearchError):
    code = "RESEARCH_SEARCH_DEPENDENCY_MISSING"


class ResearchFetchError(ResearchError):
    code = "RESEARCH_FETCH_FAILED"

    def __init__(self, message='', *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class ResearchUploadRequired(ResearchFetchError):
    code = 'MATERIAL_UPLOAD_REQUIRED'


def material_upload_guidance(url, title='', *, lists=None):
    """A visible fallback, never a fabricated textbook body or auth workaround."""
    from .source_filter import type_catalog,classify_source
    parsed=urlparse(url)
    host=parsed.hostname or ''
    if not (host=='basic.smartedu.cn' or classify_source(url,lists=lists)['type']=='textbook_publisher'):
        return ''
    entries=[e for e in type_catalog(lists).directory if e['url']==url]
    book=title or (entries[0]['title'] if entries else '所选教材')
    platform='国家中小学智慧教育平台' if host=='basic.smartedu.cn' else '出版社官网'
    return (f'《{book}》在{platform}，目前无法自动读取正文。'
            '需要登录后下载的教材，请你登录后下载，再点“上传资料”传进来就行。'
            f'教材页面：{url}')


def smartedu_metadata_url(url):
    """Public metadata endpoint described by MIT tchMaterial-parser/api.py.

    https://github.com/happycola233/tchMaterial-parser/blob/main/src/tchmaterial_parser/api.py
    No token extraction, alternative private CDN, or downloader code is used.
    """
    parsed=urlparse(url)
    if parsed.hostname!='basic.smartedu.cn' or parsed.path!='/tchMaterial/detail':return None
    identifier=parse_qs(parsed.query).get('contentId',[''])[0]
    if not re.fullmatch(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}',identifier):return None
    return 'https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/'+identifier+'.json'


def _smartedu_pdf(data):
    for item in data.get('ti_items',[]):
        if item.get('ti_format')!='pdf' or not (item.get('ti_is_source_file') or item.get('ti_file_flag')=='source'):continue
        storage=item.get('ti_storage')
        if storage:
            url=storage.replace('cs_path:${ref-path}','https://r1-ndr-private.ykt.cbern.com.cn')
        else:url=next(iter(item.get('ti_storages') or []),'')
        if isinstance(url,str) and urlparse(url).scheme=='https':return url
    return None


class ResearchUnsupportedContent(ResearchFetchError):
    code = "RESEARCH_CONTENT_UNSUPPORTED"


class ResearchTimeout(ResearchFetchError):
    code = "RESEARCH_TIMEOUT"


@dataclass(frozen=True, slots=True)
class FetchedContent:
    """Bounded response bytes, before any document extraction (including PDF)."""

    url: str
    final_url: str
    content_type: str
    body: bytes
    retrieved_at: str


@dataclass(frozen=True, slots=True)
class Candidate:
    """A URL returned by a real search, with the query that produced it."""

    url: str
    title: str
    snippet: str
    query: str
    engine: str = ""


@dataclass(frozen=True, slots=True)
class Chunk:
    """A deterministic, bounded piece of a fetched source."""

    id: str
    text: str


@dataclass(frozen=True, slots=True)
class Source:
    """Fetched reference text and its stable content identity."""

    url: str
    final_url: str
    title: str
    content_sha256: str
    chunks: tuple[Chunk, ...]
    text: str
    content_type: str = ""
    canonical_url: str = ""
    # Provenance fields are persisted with selected sources so a plan can be
    # restored on another device without pretending a cached excerpt is a
    # fresh fetch.  Defaults keep old injected/test Sources source-compatible.
    retrieved_at: str = ""
    source_type: str = "web"
    summary: str = ""
    excerpt: str = ""


Cancel = Callable[[], bool] | Any | None


_PROXY_FAKE_IPV4 = ipaddress.ip_network("198.18.0.0/15")
_DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"
_DOH_MAX_BYTES = 64 * 1024


def _cancelled(cancel: Cancel) -> bool:
    if cancel is None:
        return False
    try:
        if callable(cancel):
            return bool(cancel())
        is_set = getattr(cancel, "is_set", None)
        if callable(is_set):
            return bool(is_set())
        return bool(cancel)
    except Exception as exc:
        # A cancellation callback belongs to the caller.  A broken callback
        # must not silently turn an in-flight network operation into success.
        raise ResearchCancelled("取消检查失败，研究操作已停止。") from exc


def _check_cancel(cancel: Cancel) -> None:
    if _cancelled(cancel):
        raise ResearchCancelled("研究操作已取消。")


def _clean_query(query: str) -> str:
    value = " ".join(str(query or "").split())[:360]
    if not value:
        raise ResearchInputError("搜索查询不能为空。")
    return value


def _safe_candidate_url(value: Any) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    # Fragments are client-side state and do not identify a separate source.
    return parsed._replace(fragment="").geturl()


def _obviously_private_url(value: str) -> bool:
    host = urlparse(value).hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".lan", ".home.arpa")):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def _as_candidate(value: Candidate | Mapping[str, Any]) -> Candidate:
    if isinstance(value, Candidate):
        return value
    if isinstance(value, Mapping):
        candidate = Candidate(
            url=str(value.get("url") or ""),
            title=str(value.get("title") or ""),
            snippet=str(value.get("snippet") or ""),
            query=str(value.get("query") or ""),
            engine=str(value.get("engine") or ""),
        )
        if not candidate.url:
            raise ResearchInputError("资料候选缺少 URL。")
        return candidate
    raise ResearchInputError("资料候选格式无效。")


def _normalise_results(
    items: Any,
    *,
    query: str,
    limit: int,
    engine: str,
    cancel: Cancel,
    allow_private: bool = False,
) -> list[Candidate]:
    if not isinstance(items, list):
        raise ResearchSearchError("搜索服务返回的结果不是列表。")
    output: list[Candidate] = []
    seen: set[str] = set()
    for item in items:
        _check_cancel(cancel)
        if not isinstance(item, Mapping):
            continue
        url = _safe_candidate_url(item.get("url") or item.get("href"))
        if not url or (not allow_private and _obviously_private_url(url)) or url in seen:
            continue
        seen.add(url)
        parsed = urlparse(url)
        title = str(item.get("title") or parsed.hostname or "搜索结果").strip()
        snippet = str(
            item.get("content") or item.get("body") or item.get("snippet") or ""
        ).strip()
        output.append(
            Candidate(
                url=url,
                title=title[:240],
                snippet=snippet[:800],
                query=query,
                engine=engine,
            )
        )
        if len(output) >= limit:
            break
    return output


def _split_chunks(text: str, source_hash: str, *, size: int, cancel: Cancel) -> tuple[Chunk, ...]:
    """Split on paragraphs, wrapping unusually large paragraphs deterministically."""

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    if not paragraphs and text.strip():
        paragraphs = [text.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        _check_cancel(cancel)
        if len(paragraph) <= size:
            pieces.append(paragraph)
            continue
        remaining = paragraph
        while remaining:
            _check_cancel(cancel)
            if len(remaining) <= size:
                pieces.append(remaining.strip())
                break
            cut = remaining.rfind(" ", 0, size + 1)
            if cut < max(1, size // 2):
                cut = size
            pieces.append(remaining[:cut].strip())
            remaining = remaining[cut:].lstrip()
    chunks: list[Chunk] = []
    for index, piece in enumerate(pieces):
        _check_cancel(cancel)
        identity = hashlib.sha256(
            f"{source_hash}:{index}:{piece}".encode("utf-8")
        ).hexdigest()
        chunks.append(Chunk(id=f"chunk-{identity[:24]}", text=piece))
    return tuple(chunks)


class _HTMLTextParser(HTMLParser):
    """Small standard-library fallback for desktop installs without bs4."""

    _ignored = frozenset(("script", "style", "nav", "footer", "header", "noscript", "iframe"))
    _blocks = frozenset(("address", "article", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "pre", "section"))

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._title_depth = 0
        self._title: list[str] = []
        self._text: list[str] = []
        self.canonical = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag == "link" and "canonical" in attributes.get("rel", "").casefold().split():
            self.canonical = attributes.get("href", "")
        if tag in self._ignored:
            self._ignored_depth += 1
        if tag == "title" and not self._ignored_depth:
            self._title_depth += 1
        if tag in self._blocks and not self._ignored_depth:
            self._text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in self._ignored and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in self._blocks and not self._ignored_depth:
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._title_depth:
            self._title.append(data)
        else:
            self._text.append(data)


def _extract_html(content: bytes, final_url: str) -> tuple[str, str, str]:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(("script", "style", "nav", "footer", "header", "noscript", "iframe")):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        text = "\n".join(
            line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
        )
        link = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
        canonical = _safe_candidate_url(urljoin(final_url, str(link["href"]))) if link and link.get("href") else ""
        return text, title, canonical
    parser = _HTMLTextParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    parser.close()
    text = "\n".join(line.strip() for line in "".join(parser._text).splitlines() if line.strip())
    canonical = _safe_candidate_url(urljoin(final_url, parser.canonical)) if parser.canonical else ""
    return text, " ".join("".join(parser._title).split()), canonical


def directory_candidates(query, *, stage='', subject='', lists=None, limit=8):
    from .source_filter import directory_matches,type_catalog
    matched=directory_matches(query,stage=stage,subject=subject,lists=lists,
                              limit=len(type_catalog(lists).directory))
    available=[e for e in matched if set(e['available']) & {'全文节选','习题','答案','目录'}]
    if any(word in query.casefold() for word in ('习题','真题','练习','题目','答案','questions','quiz','exercises','solutions')):
        # An unavailable textbook must not crowd an actual answered paper out
        # of the cap; ranking within each capability group remains unchanged.
        available.sort(key=lambda e:'答案' not in e['available'])
    return [Candidate(e['url'],e['title'],e['verification'],query,'directory') for e in available[:limit]]


def directory_upload_guidance(query, *, stage='', subject='', lists=None, limit=3):
    """Host can append these messages to planning chat without adopting a body."""
    from .source_filter import directory_matches,type_catalog
    matched=directory_matches(query,stage=stage,subject=subject,lists=lists,
                              limit=len(type_catalog(lists).directory))
    messages=[];seen=set()
    for entry in matched:
        if '全文节选' in entry['available'] or entry['url'] in seen:continue
        text=material_upload_guidance(entry['url'],entry['title'],lists=lists)
        if text:
            seen.add(entry['url']);messages.append(text)
        if len(messages)>=limit:break
    return tuple(messages)


class ResearchService:
    """Search public resources and fetch bounded, reference-only page text.

    ``transport`` is deliberately injectable for deterministic tests and for a
    local HTTP fixture.  When it is supplied, URL DNS/host checks are still
    bypassed only for that explicitly injected transport; production callers
    get the public-host SSRF checks below.
    """

    def __init__(
        self,
        searxng_url: str | None = None,
        *,
        searxng_endpoint: str | None = None,
        ddgs_factory: Callable[[], Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
        max_bytes: int = 2_000_000,
        max_redirects: int = 4,
        chunk_size: int = 1_200,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        doh_transport: httpx.BaseTransport | None = None,
    ) -> None:
        configured = searxng_endpoint if searxng_endpoint is not None else searxng_url
        self.searxng_url = str(
            configured if configured is not None else os.getenv("MENTORFLOW_SEARXNG_URL", "")
        ).strip()
        self._ddgs_factory = ddgs_factory
        self._transport = transport
        self.timeout = max(0.1, float(timeout))
        self.max_bytes = max(1, int(max_bytes))
        self.max_redirects = max(0, int(max_redirects))
        self.chunk_size = max(80, int(chunk_size))
        self.user_agent = user_agent[:200] or "MentorFlow/2.0"
        self._doh_transport = doh_transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            transport=self._transport,
            timeout=self.timeout,
            follow_redirects=False,
            headers={"User-Agent": self.user_agent,
                     "Accept": "text/html,application/pdf,application/json;q=0.9,*/*;q=0.8",
                     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"},
        )

    def discover(self, query, *, stage='', subject='', limit=8, cancel=None, lists=None):
        """Curriculum directory precedes ordinary web search; failures stay visible.

        Catalog entries are candidates, never adopted passages. Fetch/extract and
        point mapping remain mandatory. Callers may use directory_candidates
        with their vendor-search gateway instead of this optional web transport.
        """
        first = directory_candidates(query, stage=stage, subject=subject, lists=lists, limit=limit)
        if len(first) < limit:
            first += self.search(query, limit=limit-len(first), cancel=cancel)
        return first

    def search(
        self,
        query: str,
        *,
        cancel: Cancel = None,
        limit: int = 8,
    ) -> list[Candidate]:
        """Run SearXNG when configured, otherwise the installed ``ddgs`` client."""

        normalized = _clean_query(query)
        try:
            count = max(1, min(int(limit), 8))
        except (TypeError, ValueError) as exc:
            raise ResearchInputError("搜索结果数量无效。") from exc
        _check_cancel(cancel)
        if self.searxng_url:
            return self._search_searxng(normalized, count, cancel)
        return self._search_ddgs(normalized, count, cancel)

    def _search_searxng(self, query: str, limit: int, cancel: Cancel) -> list[Candidate]:
        endpoint = self.searxng_url.rstrip("/") + "/search"
        try:
            with self._client() as client:
                _check_cancel(cancel)
                response = client.get(
                    endpoint,
                    params={
                        "q": query,
                        "format": "json",
                        "categories": "general",
                        "language": "all",
                    },
                )
                _check_cancel(cancel)
                response.raise_for_status()
                payload = response.json()
        except ResearchCancelled:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ResearchSearchError(f"SearXNG 搜索失败：{exc}") from exc
        if not isinstance(payload, Mapping):
            raise ResearchSearchError("SearXNG 返回的数据格式无效。")
        results = _normalise_results(
            list(payload.get("results") or []),
            query=query,
            limit=limit,
            engine="searxng",
            cancel=cancel,
            allow_private=self._transport is not None,
        )
        if not results:
            raise ResearchSearchError("SearXNG 没有返回可用的 HTTP(S) 搜索结果。")
        return results

    def _search_ddgs(self, query: str, limit: int, cancel: Cancel) -> list[Candidate]:
        factory = self._ddgs_factory
        if factory is None:
            try:
                from ddgs import DDGS
            except ImportError as exc:
                raise SearchDependencyError(
                    "未安装开源搜索依赖 ddgs；请安装已锁定的 backend 搜索依赖。"
                ) from exc
            factory = DDGS
        configured = os.getenv("MENTORFLOW_DDGS_BACKENDS", "bing,duckduckgo")
        backends = [item.strip().casefold() for item in configured.split(",") if item.strip()]
        backends = backends or ["bing", "duckduckgo"]
        errors: list[str] = []
        for backend in backends:
            for attempt in range(2):
                _check_cancel(cancel)
                try:
                    client = factory()
                    raw = []
                    for result in client.text(query, max_results=limit, backend=backend):
                        _check_cancel(cancel)
                        raw.append(result)
                        if len(raw) >= limit:
                            break
                    results = _normalise_results(
                        raw,
                        query=query,
                        limit=limit,
                        engine=backend,
                        cancel=cancel,
                        allow_private=self._transport is not None,
                    )
                    if results:
                        return results
                    errors.append(f"{backend} 尝试 {attempt + 1}: 无结果")
                except ResearchCancelled:
                    raise
                except ImportError as exc:
                    raise SearchDependencyError(
                        "未安装开源搜索依赖 ddgs；请安装已锁定的 backend 搜索依赖。"
                    ) from exc
                except Exception as exc:  # backend-specific errors must be visible
                    errors.append(f"{backend} 尝试 {attempt + 1}: {type(exc).__name__}: {exc}")
                if attempt == 0:
                    _check_cancel(cancel)
        detail = "；".join(errors[:4]) or "无可用搜索后端"
        raise ResearchSearchError(f"开源元搜索没有返回可用结果（{detail}）。")

    def _resolve_fake_dns_with_doh(self, host: str, *, cancel: Cancel = None) -> None:
        """Confirm a public hostname behind the desktop proxy's fake DNS.

        Some Windows proxy clients answer ordinary DNS requests with RFC 2544
        benchmark addresses and route the original hostname through a local
        HTTPS proxy.  The fake answer is never trusted as a destination.  A
        bounded, certificate-verified DNS-over-HTTPS lookup must instead return
        only public A/AAAA answers before the original hostname is requested.
        """

        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        try:
            with httpx.Client(
                transport=self._doh_transport,
                timeout=min(self.timeout, 5.0),
                follow_redirects=False,
                headers={
                    "Accept": "application/dns-json",
                    "User-Agent": self.user_agent,
                },
            ) as client:
                for record_type in ("A", "AAAA"):
                    _check_cancel(cancel)
                    with client.stream(
                        "GET",
                        _DOH_ENDPOINT,
                        params={"name": host, "type": record_type},
                    ) as response:
                        _check_cancel(cancel)
                        if response.status_code >= 400:
                            raise ResearchFetchError(
                                f"公共 DNS 查询失败（HTTP {response.status_code}）。"
                            )
                        data = bytearray()
                        for part in response.iter_bytes():
                            _check_cancel(cancel)
                            data.extend(part)
                            if len(data) > _DOH_MAX_BYTES:
                                raise ResearchFetchError("公共 DNS 响应超过大小限制。")
                    try:
                        payload = httpx.Response(200, content=bytes(data)).json()
                    except ValueError as exc:
                        raise ResearchFetchError("公共 DNS 响应格式无效。") from exc
                    if not isinstance(payload, Mapping):
                        raise ResearchFetchError("公共 DNS 响应格式无效。")
                    status = payload.get("Status", 0)
                    if status not in (0, None):
                        raise ResearchFetchError(f"公共 DNS 查询失败（状态 {status}）。")
                    for answer in payload.get("Answer") or []:
                        if not isinstance(answer, Mapping):
                            continue
                        answer_type = answer.get("type")
                        if (record_type == "A" and answer_type != 1) or (
                            record_type == "AAAA" and answer_type != 28
                        ):
                            continue
                        try:
                            addresses.append(ipaddress.ip_address(str(answer.get("data") or "")))
                        except ValueError:
                            raise ResearchFetchError("公共 DNS 返回了无效地址。")
        except ResearchError:
            raise
        except httpx.HTTPError as exc:
            raise ResearchFetchError(f"公共 DNS 查询失败：{exc}") from exc

        if not addresses or any(not address.is_global for address in addresses):
            raise ResearchFetchError("公共 DNS 未确认该主机只解析到公共地址，已拒绝读取。")

    def _validate_fetch_url(self, value: str, *, cancel: Cancel = None) -> Any:
        try:
            parsed = urlparse(value)
        except ValueError as exc:
            raise ResearchFetchError("资料 URL 无法解析。") from exc
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            raise ResearchFetchError("资料 URL 必须是 HTTP(S) 地址。")
        if parsed.username or parsed.password:
            raise ResearchFetchError("资料 URL 不得包含账号或密码。")
        # An injected transport is an explicit local test boundary.  It cannot
        # accidentally affect the normal desktop service, which always uses
        # the public-host checks below.
        if self._transport is not None:
            return parsed
        host = parsed.hostname.casefold().rstrip(".")
        local_suffixes = (".local", ".localhost", ".internal", ".lan", ".home.arpa")
        if host == "localhost" or host.endswith(local_suffixes):
            raise ResearchFetchError("资料地址指向私有或回环主机，已拒绝读取。")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if not literal.is_global:
                raise ResearchFetchError("资料地址指向私有或保留网络，已拒绝读取。")
            return parsed
        try:
            addresses = {
                ipaddress.ip_address(info[4][0])
                for info in socket.getaddrinfo(host, None)
            }
        except OSError as exc:
            raise ResearchFetchError("资料主机名无法解析，未读取网页。") from exc
        if not addresses:
            raise ResearchFetchError("资料地址解析到私有或保留网络，已拒绝读取。")
        if all(address.version == 4 and address in _PROXY_FAKE_IPV4 for address in addresses):
            self._resolve_fake_dns_with_doh(host, cancel=cancel)
            return parsed
        if any(not address.is_global for address in addresses):
            raise ResearchFetchError("资料地址解析到私有或保留网络，已拒绝读取。")
        return parsed

    def fetch_bytes(self, candidate: Candidate | Mapping[str, Any], *, cancel: Cancel = None,
                    allowed_types=None, request_guard=None) -> FetchedContent:
        """Use the existing SSRF/redirect/size checks for raw document bytes.

        request_guard(url, cancel=...) is an optional context manager covering
        EACH HTTP request, including redirects, for pipeline per-site limits.
        No credentials are attached to a public research request.
        """

        item = _as_candidate(candidate)
        current = _safe_candidate_url(item.url)
        if not current:
            raise ResearchInputError("资料候选 URL 无效。")
        original_url = current
        metadata_url=smartedu_metadata_url(current)
        if metadata_url:
            import json
            title=item.title
            try:
                metadata=self.fetch_bytes({'url':metadata_url},cancel=cancel,
                    allowed_types={'application/json','text/plain'},request_guard=request_guard)
                data=json.loads(metadata.body)
                if not isinstance(data,dict):raise ValueError('教材元数据不是对象')
                title=data.get('title') or title
                if not isinstance(title,str):title=item.title
                pdf=_smartedu_pdf(data)
                if not pdf:raise ResearchUploadRequired(material_upload_guidance(original_url,title))
                # Exactly one ordinary GET of the advertised PDF. A 401/403
                # is NOT retried on a different CDN or with a manufactured auth.
                fetched=self.fetch_bytes({'url':pdf},cancel=cancel,
                    allowed_types={'application/pdf'},request_guard=request_guard)
                return FetchedContent(original_url,fetched.final_url,fetched.content_type,
                                      fetched.body,fetched.retrieved_at)
            except (ResearchCancelled,ResearchTimeout):raise
            except (ResearchFetchError,ValueError,KeyError,TypeError) as exc:
                if isinstance(exc,ResearchUploadRequired):raise
                raise ResearchUploadRequired(material_upload_guidance(original_url,title)+
                    ('（资源返回 HTTP '+str(exc.status_code)+'。）' if getattr(exc,'status_code',None) else
                     '（教材资源暂不可读取。）')) from exc
        redirect_count = 0
        deadline = time.monotonic() + self.timeout
        allowed_types = allowed_types if allowed_types is not None else {
            "text/html", "application/xhtml+xml", "application/pdf", "text/plain", "text/markdown"}
        try:
            with self._client() as client:
                while True:
                    _check_cancel(cancel)
                    self._validate_fetch_url(current, cancel=cancel)
                    guard = request_guard(current, cancel=cancel) if request_guard else nullcontext()
                    with guard:
                        _check_cancel(cancel)
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ResearchTimeout("资料抓取超过时间限制。")
                        response_context = client.stream("GET", current, timeout=remaining)
                        # Keep the guard held until the streamed response closes.
                        with response_context as response:
                            status = response.status_code
                            if status in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location")
                                if not location:
                                    raise ResearchFetchError("网页重定向缺少目标地址。")
                                if redirect_count >= self.max_redirects:
                                    raise ResearchFetchError("网页重定向次数超过限制。")
                                target = _safe_candidate_url(urljoin(current, location))
                                if not target:
                                    raise ResearchFetchError("网页重定向目标不是 HTTP(S) 地址。")
                                current = target
                                redirect_count += 1
                                continue
                            if not 200 <= status < 300:
                                raise ResearchFetchError(f"网页返回 HTTP {status}，未读取正文。",status_code=status)
                            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
                            if content_type not in allowed_types:
                                raise ResearchUnsupportedContent(f"网页内容类型 {content_type!r} 不支持正文读取。")
                            declared = response.headers.get("content-length")
                            if declared and (not declared.isdigit() or int(declared) > self.max_bytes):
                                raise ResearchFetchError("网页正文超过大小限制或长度声明无效。")
                            data = bytearray()
                            for part in response.iter_bytes(chunk_size=64 * 1024):
                                _check_cancel(cancel)
                                if time.monotonic() >= deadline:
                                    raise ResearchTimeout("资料抓取超过时间限制。")
                                data.extend(part)
                                if len(data) > self.max_bytes:
                                    raise ResearchFetchError("网页正文超过大小限制。")
                        _check_cancel(cancel)
                        if time.monotonic() >= deadline:
                            raise ResearchTimeout("资料抓取超过时间限制。")
                        return FetchedContent(original_url, current, content_type, bytes(data),
                            datetime.now(timezone.utc).isoformat())
        except ResearchError:
            raise
        except httpx.TimeoutException as exc:
            raise ResearchTimeout("资料抓取超过时间限制。") from exc
        except httpx.HTTPError as exc:
            raise ResearchFetchError(f"读取网页失败：{exc}") from exc
        except UnicodeError as exc:
            raise ResearchFetchError("网页正文编码无法读取。") from exc
        except Exception as exc:
            raise ResearchFetchError(f"读取网页失败：{type(exc).__name__}: {exc}") from exc

    def fetch(self, candidate: Candidate | Mapping[str, Any], *, cancel: Cancel = None) -> Source:
        """Original text API, retaining its supported types and text hash contract."""
        item = _as_candidate(candidate)
        fetched = self.fetch_bytes(item, cancel=cancel, allowed_types={
            "text/html", "application/xhtml+xml", "text/plain", "text/markdown",
            "text/csv", "text/tab-separated-values", "application/json", "application/xml", "text/xml"})
        original_url, final_url = fetched.url, fetched.final_url
        content_type, body = fetched.content_type, fetched.body
        is_html = content_type in {"text/html", "application/xhtml+xml"}
        if is_html:
            text, title, canonical = _extract_html(body, final_url)
        else:
            text = body.decode("utf-8", errors="replace")
            title = ""
            canonical = ""
        _check_cancel(cancel)
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
        if not text:
            raise ResearchFetchError("网页正文为空，未生成资料。")
        title = title.strip()[:240] or item.title.strip()[:240]
        if not title:
            title = PurePosixPath(urlparse(final_url).path).name or final_url
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunks = _split_chunks(text, content_hash, size=self.chunk_size, cancel=cancel)
        return Source(
            url=original_url,
            final_url=final_url,
            title=title,
            content_sha256=content_hash,
            chunks=chunks,
            text=text,
            content_type=content_type,
            canonical_url=canonical,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            source_type="web",
            summary=" ".join(chunk.text for chunk in chunks[:2])[:500],
            excerpt=text[:8_000],
        )


__all__ = [
    "Candidate",
    "Chunk",
    "ResearchCancelled",
    "ResearchCancelledError",
    "ResearchError",
    "ResearchFetchError",
    "ResearchInputError",
    "ResearchSearchError",
    "ResearchService",
    "ResearchUnsupportedContent",
    "ResearchTimeout",
    "FetchedContent",
    "SearchDependencyError",
    "Source",
]

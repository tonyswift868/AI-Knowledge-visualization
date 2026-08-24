"""Parse exact resource references before generic discovery.

The planner must never turn a user-supplied identifier or URL into an
approximate search result.  This module is deliberately domain-neutral: it
registers public identifier formats, returns deterministic candidates, and
leaves network verification and ingestion to their existing confirmation
gates.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlparse


_URL_RE = re.compile(
    r"https?://(?:\[[0-9A-Fa-f:.%]+\](?::\d+)?|[^\s<>\"'\[\]{}])+",
    re.IGNORECASE,
)
_BVID_RE = re.compile(r"(?<![0-9A-Za-z])BV[0-9A-Za-z]{10}(?![0-9A-Za-z])")
_BVID_LIKE_RE = re.compile(r"(?<![0-9A-Za-z])BV[0-9A-Za-z]{4,20}(?![0-9A-Za-z])")
_AVID_RE = re.compile(r"(?<![0-9A-Za-z])av(?P<id>[0-9]{1,20})(?![0-9A-Za-z])", re.IGNORECASE)
_DOI_RE = re.compile(r"(?<![0-9A-Za-z])10\.[0-9]{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
_ARXIV_RE = re.compile(r"(?<![0-9A-Za-z])arXiv\s*:\s*(?P<id>[0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?)(?![0-9A-Za-z])", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = "，。；：！？、,.!?:;"


def _texts(values: Iterable[Any]) -> Iterable[str]:
    for value in values:
        if isinstance(value, Mapping):
            yield " ".join(str(item or "") for item in value.values())
        elif isinstance(value, (list, tuple, set)):
            yield " ".join(str(item or "") for item in value)
        else:
            yield str(value or "")


def _host(parsed: Any) -> str:
    return (parsed.hostname or "").casefold().strip(".")


def _candidate(
    *,
    url: str,
    title: str,
    kind: str,
    value: str,
    media_kind: str,
    coverage_role: str,
    resolution: str = "exact",
) -> dict[str, Any]:
    return {
        "url": url,
        "title": title,
        "snippet": "用户明确提供的资源引用；系统保留其精确身份，标题、可访问性和内容范围仍需打开原文核对。",
        "media_kind": media_kind,
        "coverage_role": coverage_role,
        "direct_user_reference": True,
        "direct_identifier": {"kind": kind, "value": value, "match": resolution},
        "reference_resolution": {
            "status": resolution,
            "network_verified": False,
            "requires_user_confirmation": True,
        },
        "authority_evidence": {"status": "unknown", "value": None, "basis": None},
        "popularity_evidence": {"status": "unknown", "value": None, "basis": None},
    }


def _url_candidate(raw_url: str) -> dict[str, Any] | None:
    url = raw_url.rstrip(_TRAILING_URL_PUNCTUATION)
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    host = _host(parsed)
    path = parsed.path or "/"

    if host == "bilibili.com" or host.endswith(".bilibili.com"):
        lower_path = path.casefold().rstrip("/")
        query = parse_qs(parsed.query)
        is_collection = bool(
            (host == "space.bilibili.com" and re.fullmatch(r"/[0-9]+/lists/[0-9]+", lower_path))
            or (
                host == "space.bilibili.com"
                and re.fullmatch(r"/[0-9]+/channel/(?:seriesdetail|collectiondetail)", lower_path)
                and (query.get("sid") or query.get("season_id"))
            )
            or re.match(r"/(?:medialist/play|list)/", lower_path)
            or re.match(r"/(?:bangumi|cheese)/play/(?:ss|md|ep)[0-9]+", lower_path)
        )
        if is_collection:
            return _candidate(
                url=url,
                title="用户提供的 B站课程或合集",
                kind="bilibili_collection_url",
                value=url,
                media_kind="video_collection",
                coverage_role="full_collection",
            )
        match = re.search(r"/video/(?P<id>BV[0-9A-Za-z]{10}|av[0-9]+)", path, re.IGNORECASE)
        if match:
            identifier = match.group("id")
            if identifier[:2].casefold() == "bv":
                identifier = "BV" + identifier[2:]
                kind = "bilibili_bvid"
            else:
                identifier = "av" + re.sub(r"\D", "", identifier)
                kind = "bilibili_avid"
            page = str((parse_qs(parsed.query).get("p") or [""])[0]).strip()
            canonical = f"https://www.bilibili.com/video/{identifier}/"
            if page.isdigit() and int(page) > 0:
                canonical += f"?p={int(page)}"
            return _candidate(
                url=canonical,
                title=f"B站视频 {identifier}",
                kind=kind,
                value=identifier,
                media_kind="video",
                coverage_role="user_named_video",
            )
    if host == "b23.tv":
        canonical_short = f"https://b23.tv{path}"
        if parsed.query:
            canonical_short += f"?{parsed.query}"
        return _candidate(
            url=canonical_short,
            title="用户提供的 B站短链接",
            kind="bilibili_short_url",
            value=url,
            media_kind="video",
            coverage_role="user_named_video",
            resolution="exact_short_url",
        )
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        query = parse_qs(parsed.query)
        playlist_id = str((query.get("list") or [""])[0]).strip()
        if path.casefold() == "/playlist" and playlist_id:
            return _candidate(
                url=f"https://www.youtube.com/playlist?list={playlist_id}",
                title=f"YouTube 播放列表 {playlist_id}",
                kind="youtube_playlist",
                value=playlist_id,
                media_kind="video_collection",
                coverage_role="full_collection",
            )
        video_id = ""
        if host == "youtu.be":
            video_id = path.strip("/").split("/", 1)[0]
        elif path.casefold() == "/watch":
            video_id = str((query.get("v") or [""])[0]).strip()
        else:
            match = re.match(r"/(?:shorts|embed)/([^/?#]+)", path, re.IGNORECASE)
            video_id = match.group(1) if match else ""
        if video_id:
            canonical = f"https://www.youtube.com/watch?v={video_id}"
            if playlist_id:
                canonical += f"&list={playlist_id}"
            return _candidate(
                url=canonical,
                title=(f"YouTube 视频 {video_id}（位于播放列表）" if playlist_id else f"YouTube 视频 {video_id}"),
                kind="youtube_video_id",
                value=video_id,
                media_kind="video_collection" if playlist_id else "video",
                coverage_role="collection_context" if playlist_id else "user_named_video",
            )
    if host in {"doi.org", "dx.doi.org"}:
        doi = path.lstrip("/")
        if doi:
            return _candidate(
                url=f"https://doi.org/{doi}", title=f"DOI {doi}", kind="doi", value=doi,
                media_kind="document", coverage_role="user_named_document",
            )
    if host in {"arxiv.org", "www.arxiv.org"}:
        match = re.match(r"/(?:abs|pdf)/([^/?#]+)", path, re.IGNORECASE)
        if match:
            arxiv_id = match.group(1).removesuffix(".pdf")
            return _candidate(
                url=f"https://arxiv.org/abs/{arxiv_id}", title=f"arXiv {arxiv_id}",
                kind="arxiv_id", value=arxiv_id, media_kind="document",
                coverage_role="user_named_document",
            )
    if parsed.scheme.casefold() != "https":
        return None
    lower_path = path.casefold()
    if re.search(r"\.(?:mp4|m3u8|webm|mov|mkv)(?:$|[;])", lower_path):
        media_kind, coverage_role = "video", "user_named_video"
    elif re.search(r"\.(?:pdf|docx?|pptx?|xlsx?|epub)(?:$|[;])", lower_path):
        media_kind, coverage_role = "document", "user_named_document"
    else:
        media_kind, coverage_role = "unknown", "user_named_resource"
    return _candidate(
        url=url,
        title=f"用户提供的网页（{host}）",
        kind="https_url",
        value=url,
        media_kind=media_kind,
        coverage_role=coverage_role,
    )


def parse_direct_resource_references(*values: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Return exact candidates and user-readable unresolved-reference warnings."""

    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_urls: set[str] = set()
    texts = list(_texts(values))

    def add(candidate: dict[str, Any] | None) -> None:
        if not candidate:
            return
        key = str(candidate.get("url") or "").casefold()
        if not key or key in seen_urls:
            return
        seen_urls.add(key)
        candidates.append(candidate)

    for text in texts:
        url_spans: list[tuple[int, int]] = []
        for match in _URL_RE.finditer(text):
            url_spans.append(match.span())
            raw_url = match.group(0).rstrip(_TRAILING_URL_PUNCTUATION)
            candidate = _url_candidate(raw_url)
            if candidate is None and raw_url.casefold().startswith("http://"):
                warnings.append(f"无法安全使用非 HTTPS 资源 {raw_url}；请提供 HTTPS 地址后重试。")
            add(candidate)
        # Identifiers inside a full URL were already handled with their query
        # semantics (for example Bilibili ?p=2); scanning them again would
        # create a second, less precise candidate.
        identifier_text = "".join(
            " " if any(start <= index < end for start, end in url_spans) else character
            for index, character in enumerate(text)
        )
        for match in _BVID_RE.finditer(identifier_text):
            bvid = match.group(0)
            add(_candidate(
                url=f"https://www.bilibili.com/video/{bvid}/", title=f"B站视频 {bvid}",
                kind="bilibili_bvid", value=bvid, media_kind="video",
                coverage_role="user_named_video",
            ))
        for match in _AVID_RE.finditer(identifier_text):
            avid = f"av{match.group('id')}"
            add(_candidate(
                url=f"https://www.bilibili.com/video/{avid}/", title=f"B站视频 {avid}",
                kind="bilibili_avid", value=avid, media_kind="video",
                coverage_role="user_named_video",
            ))
        for match in _DOI_RE.finditer(identifier_text):
            doi = match.group(0).rstrip(_TRAILING_URL_PUNCTUATION)
            add(_candidate(
                url=f"https://doi.org/{doi}", title=f"DOI {doi}", kind="doi", value=doi,
                media_kind="document", coverage_role="user_named_document",
            ))
        for match in _ARXIV_RE.finditer(identifier_text):
            arxiv_id = match.group("id")
            add(_candidate(
                url=f"https://arxiv.org/abs/{arxiv_id}", title=f"arXiv {arxiv_id}",
                kind="arxiv_id", value=arxiv_id, media_kind="document",
                coverage_role="user_named_document",
            ))
        valid_bvids = {item.group(0) for item in _BVID_RE.finditer(identifier_text)}
        for match in _BVID_LIKE_RE.finditer(identifier_text):
            token = match.group(0)
            if token not in valid_bvids:
                warnings.append(f"无法解析资源标识 {token}：BV 号长度或格式不正确，请核对后重试。")
    return candidates, list(dict.fromkeys(warnings))

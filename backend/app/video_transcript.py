"""Safe, user-confirmed video transcript and supplementary-material flow.

The module deliberately keeps discovery separate from ingestion.  A video URL
is classified before any subtitle request is made, supplementary search only
returns selectable candidates, and transcript parsing produces timestamped
evidence that callers can confirm before creating a material record.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from .open_search import OpenSearchError, search_web

VIDEO_TRANSCRIPT_SCHEMA = "mentorflow.video-transcript-preview.v1"
VIDEO_CAPABILITY_SCHEMA = "mentorflow.video-capability.v1"
VIDEO_COLLECTION_MANIFEST_SCHEMA = "mentorflow.video-collection-manifest-page.v1"
VIDEO_EPISODE_TRANSCRIPT_SCHEMA = "mentorflow.video-episode-transcript.v1"
MAX_SUBTITLE_BYTES = 8 * 1024 * 1024
MAX_SUBTITLE_SEGMENTS = 20_000
MAX_COLLECTION_EPISODES = 1000
MAX_COLLECTION_PAGE_SIZE = 100
DEFAULT_COLLECTION_PAGE_SIZE = 50
MAX_SEARCH_RESULTS = 8
_CANDIDATE_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


class VideoTranscriptError(ValueError):
    """Stable user-facing extraction failure."""

    def __init__(self, code: str, message: str, *, status: int = 422, **details: Any):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details


@dataclass(frozen=True)
class SubtitleSegment:
    start_ms: int
    end_ms: int
    text: str


def _host(url: str) -> str:
    try:
        return (urlparse(str(url).strip()).hostname or "").casefold().strip(".")
    except ValueError:
        return ""


def _is_public_video_network_url(url: str) -> bool:
    """Reject obvious local or credential-bearing retrieval targets."""

    try:
        parsed = urlparse(str(url or "").strip())
        host = (parsed.hostname or "").casefold().strip(".")
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None:
        return False
    if host == "localhost" or host.endswith((".local", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    return not (
        address
        and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        )
    )


def is_bilibili_url(url: str) -> bool:
    host = _host(url)
    return host == "bilibili.com" or host.endswith(".bilibili.com") or host in {
        "b23.tv",
        "bilibili.tv",
    }


def is_youtube_url(url: str) -> bool:
    host = _host(url)
    return host in {"youtube.com", "youtu.be"} or host.endswith(".youtube.com")


def is_bilibili_collection_url(url: str) -> bool:
    """Recognize explicit Bilibili series/course containers without fetching."""

    if not is_bilibili_url(url):
        return False
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold().strip(".")
    path = (parsed.path or "/").casefold().rstrip("/")
    query = str(parsed.query or "").casefold()
    if host == "space.bilibili.com":
        if re.fullmatch(r"/[0-9]+/lists/[0-9]+", path):
            return True
        if re.fullmatch(r"/[0-9]+/channel/(?:seriesdetail|collectiondetail)", path):
            return "sid=" in query or "season_id=" in query
    return bool(
        re.match(r"/(?:medialist/play|list)/", path)
        or re.match(r"/(?:bangumi|cheese)/play/(?:ss|md|ep)[0-9]+", path)
    )


def classify_video_url(url: str) -> dict[str, Any]:
    """Classify a URL without fetching it or treating a video as HTML."""

    value = str(url or "").strip()
    parsed = urlparse(value)
    valid = _is_public_video_network_url(value)
    host = _host(value)
    path = (parsed.path or "").casefold()
    query = str(parsed.query or "").casefold()
    bilibili_collection = is_bilibili_collection_url(value)
    bilibili_video = bool(
        is_bilibili_url(value)
        and (
            host == "b23.tv"
            or re.search(r"/video/(?:bv[0-9a-z]{10}|av[0-9]+)", path)
            or bilibili_collection
        )
    )
    youtube_video = bool(
        is_youtube_url(value)
        and (
            host == "youtu.be"
            or path in {"/watch", "/playlist"}
            or re.match(r"/(?:shorts|embed)/[^/]+", path)
            or "v=" in query
            or "list=" in query
        )
    )
    provider = "bilibili" if bilibili_video else ("youtube" if youtube_video else "")
    extension_video = bool(re.search(r"\.(?:mp4|m3u8|webm|mov|mkv)(?:$|\?)", path))
    recognised = bool(provider or extension_video)
    kind = "video_collection" if bilibili_collection else ("video" if recognised else "webpage")
    return {
        "schema": VIDEO_CAPABILITY_SCHEMA,
        "url": value,
        "valid": valid,
        "kind": kind,
        "recognized": bool(valid and recognised),
        "provider": provider or None,
        "is_collection": bool(bilibili_collection or (youtube_video and "list=" in query)),
        "capabilities": {
            "subtitle_extraction": bool(valid and recognised),
            "supplementary_material_search": bool(valid and recognised),
            "timestamped_evidence": bool(valid and recognised),
        },
        "hostname": host,
    }


def source_family_id(url: str) -> str:
    """Return a stable family token for one canonical video source URL."""

    value = str(url or "").strip()
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").casefold().strip(".")
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path.rstrip("/") or "/"
        canonical = f"{parsed.scheme.casefold()}://{host}{port}{path}"
        if parsed.query:
            canonical += f"?{parsed.query}"
    except (TypeError, ValueError):
        canonical = value
    return "video-family-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def source_lineage(
    source_family: str,
    *,
    role: str,
    relation: str,
    independent: bool,
    parent_url: str = "",
) -> dict[str, Any]:
    """Build auditable lineage metadata shared by video and companions."""

    return {
        "source_family_id": str(source_family or ""),
        "role": str(role or "unknown"),
        "relation": str(relation or "unknown"),
        "parent_source_url": str(parent_url or "") or None,
        "independent_evidence": bool(independent),
    }


def candidate_id(url: str) -> str:
    return "supplement-" + hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]


def remember_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.time()
    for key, value in list(_CANDIDATE_CACHE.items()):
        if now - value[0] > 3600:
            _CANDIDATE_CACHE.pop(key, None)
    output: list[dict[str, Any]] = []
    for raw in items:
        url = str(raw.get("url") or "").strip()
        if not url:
            continue
        item = {
            "id": str(raw.get("id") or candidate_id(url)),
            "url": url,
            "title": str(raw.get("title") or urlparse(url).hostname or "补充资料")[:240],
            "snippet": str(raw.get("snippet") or "")[:800],
            "engine": str(raw.get("engine") or "")[:80],
            "source_type": "supplementary_material",
            "selected": False,
            "ingested": False,
        }
        _CANDIDATE_CACHE[item["id"]] = (now, {"url": url, "title": item["title"]})
        output.append(item)
    return output


def resolve_candidate(value: Any) -> dict[str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    cached = _CANDIDATE_CACHE.get(text)
    if cached and time.time() - cached[0] <= 3600:
        return dict(cached[1])
    if text.startswith("http://") or text.startswith("https://"):
        return {"url": text, "title": "补充资料"}
    return None


async def search_supplementary_materials(
    query: str,
    *,
    max_results: int = 6,
) -> dict[str, Any]:
    """Search public notes/documents; results are never ingested here."""

    normalized = " ".join(str(query or "").split())[:360]
    if not normalized:
        raise VideoTranscriptError("SUPPLEMENTARY_QUERY_REQUIRED", "请提供课程或视频主题", status=422)
    try:
        result = await search_web(normalized, max_results=max(1, min(int(max_results), MAX_SEARCH_RESULTS)))
    except OpenSearchError as exc:
        raise VideoTranscriptError("SUPPLEMENTARY_SEARCH_FAILED", str(exc), status=503) from exc
    candidates = remember_candidates(list(result.get("results") or []))
    return {
        "schema": "mentorflow.video-supplementary-search.v1",
        "query": normalized,
        "authorized": True,
        "ingested": False,
        "candidates": candidates,
        "engine": result.get("engine"),
        "warnings": list(result.get("errors") or []),
    }


def _timestamp_ms(value: str) -> int:
    raw = str(value or "").strip().replace(",", ".")
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours, minutes, seconds = "0", *parts
        else:
            return 0
        return max(0, int(round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)))
    except (TypeError, ValueError):
        return 0


def _clean_text(value: Any) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return " ".join(text.replace("\u200b", " ").split()).strip()


def _parse_vtt_or_srt(text: str) -> list[SubtitleSegment]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[SubtitleSegment] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = [item.strip().split(" ", 1)[0] for item in line.split("-->", 1)]
        start_ms, end_ms = _timestamp_ms(left), _timestamp_ms(right)
        index += 1
        values: list[str] = []
        while index < len(lines) and lines[index].strip():
            values.append(lines[index].strip())
            index += 1
        value = _clean_text(" ".join(values))
        if value and end_ms >= start_ms:
            segments.append(SubtitleSegment(start_ms, end_ms, value))
        if len(segments) >= MAX_SUBTITLE_SEGMENTS:
            break
        index += 1
    return segments


def _parse_json3(payload: Any) -> list[SubtitleSegment]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    events = payload.get("events") if isinstance(payload, Mapping) else []
    segments: list[SubtitleSegment] = []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        text = _clean_text("".join(str(item.get("utf8") or "") for item in event.get("segs") or []))
        if not text:
            continue
        start = int(event.get("tStartMs") or 0)
        end = start + int(event.get("dDurationMs") or 0)
        segments.append(SubtitleSegment(start, max(start, end), text))
        if len(segments) >= MAX_SUBTITLE_SEGMENTS:
            break
    return segments


def parse_subtitle_payload(payload: bytes | str, extension: str = "") -> list[SubtitleSegment]:
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if len(raw) > MAX_SUBTITLE_BYTES:
        raise VideoTranscriptError("SUBTITLE_TOO_LARGE", "字幕文件超过安全大小限制", status=413)
    text = raw.decode("utf-8-sig", errors="replace")
    ext = str(extension or "").casefold().split("?", 1)[0]
    if ext.endswith("json3") or ext.endswith(".json") or text.lstrip().startswith("{"):
        try:
            return _parse_json3(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    return _parse_vtt_or_srt(text)


def assess_subtitle(segments: list[SubtitleSegment], *, language: str = "", automatic: bool = False) -> dict[str, Any]:
    character_count = sum(len(item.text) for item in segments)
    if not segments:
        state, reason = "rejected", "subtitle_unavailable"
    elif character_count < 80 or len(segments) < 2:
        state, reason = "needs_review", "subtitle_poor_quality"
    else:
        state, reason = "ready", "subtitle_verified"
    return {
        "state": state,
        "reason": reason,
        "language": language or None,
        "automatic": bool(automatic),
        "segment_count": len(segments),
        "character_count": character_count,
        "timestamped": bool(segments and any(item.end_ms > item.start_ms for item in segments)),
    }


def _subtitle_entries(info: Mapping[str, Any]) -> list[tuple[str, bool, dict[str, Any]]]:
    entries: list[tuple[str, bool, dict[str, Any]]] = []
    for field, automatic in (("subtitles", False), ("automatic_captions", True)):
        for language, values in (info.get(field) or {}).items():
            for item in values or []:
                if not isinstance(item, Mapping) or not item.get("url"):
                    continue
                # Bilibili exposes scrolling comments as a subtitle-like XML
                # track named ``danmaku``.  It is audience chatter, not course
                # narration, and must never become learning evidence.
                track_name = str(language or "").casefold()
                track_format = str(item.get("ext") or item.get("name") or "").casefold().lstrip(".").split("?", 1)[0]
                if "danmaku" in track_name or track_format in {"xml", "danmaku"}:
                    continue
                if track_format and track_format not in {"vtt", "srt", "json3", "json"}:
                    continue
                entries.append((str(language), automatic, dict(item)))
    entries.sort(key=lambda item: (item[1], 0 if item[0].casefold().startswith(("zh", "en")) else 1))
    return entries


def _download_subtitle(ydl: Any, entry: Mapping[str, Any]) -> bytes:
    url = str(entry.get("url") or "").strip()
    if not url:
        return b""
    if not _is_public_video_network_url(url):
        raise VideoTranscriptError("SUBTITLE_URL_REJECTED", "字幕地址不符合安全网络边界", status=422)
    if hasattr(ydl, "urlopen"):
        response = ydl.urlopen(url)
        payload = response.read(MAX_SUBTITLE_BYTES + 1)
    else:
        from urllib.request import urlopen

        with urlopen(url, timeout=8) as response:  # noqa: S310 - yt-dlp supplied URL
            payload = response.read(MAX_SUBTITLE_BYTES + 1)
    if len(payload) > MAX_SUBTITLE_BYTES:
        raise VideoTranscriptError("SUBTITLE_TOO_LARGE", "字幕文件超过安全大小限制", status=413)
    return payload


def _bounded_collection_page(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = DEFAULT_COLLECTION_PAGE_SIZE
    return max(1, min(MAX_COLLECTION_PAGE_SIZE, parsed))


def _bounded_collection_offset(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = 0
    if parsed < 0 or parsed > MAX_COLLECTION_EPISODES:
        raise VideoTranscriptError(
            "COLLECTION_CURSOR_INVALID",
            "课程剧集游标超出允许范围",
            status=409,
        )
    return parsed


def _enumerate_collection_page_sync(
    url: str,
    *,
    offset: int = 0,
    page_size: int = DEFAULT_COLLECTION_PAGE_SIZE,
    ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Enumerate one bounded metadata page without downloading video bytes."""

    try:
        import yt_dlp
    except ImportError as exc:
        raise VideoTranscriptError("VIDEO_EXTRACTOR_UNAVAILABLE", "视频元数据提取器未安装", status=503) from exc
    start = _bounded_collection_offset(offset)
    size = _bounded_collection_page(page_size)
    if start >= MAX_COLLECTION_EPISODES:
        raise VideoTranscriptError("COLLECTION_EPISODE_LIMIT", "长课程最多支持 1000 集", status=422)
    # Ask for one look-ahead entry so a provider that does not report the
    # playlist count cannot silently truncate at the policy boundary.
    end = min(MAX_COLLECTION_EPISODES + 1, start + size + 1)
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "extract_flat": "in_playlist",
        "lazy_playlist": True,
        "playlist_items": f"{start + 1}:{end}",
        "socket_timeout": 8,
        "retries": 1,
    }
    factory = ydl_factory or yt_dlp.YoutubeDL
    try:
        with factory(options) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except VideoTranscriptError:
        raise
    except Exception as exc:
        raise VideoTranscriptError("VIDEO_COLLECTION_ENUMERATION_FAILED", f"课程目录无法读取：{exc}", status=422) from exc
    if not isinstance(info, Mapping):
        raise VideoTranscriptError("VIDEO_COLLECTION_ENUMERATION_FAILED", "课程目录响应格式无效", status=422)
    reported_raw = info.get("playlist_count", info.get("n_entries", info.get("entry_count")))
    reported_count: int | None = None
    if reported_raw not in {None, ""}:
        try:
            reported_count = int(reported_raw)
        except (TypeError, ValueError, OverflowError):
            reported_count = None
    if reported_count is not None and reported_count > MAX_COLLECTION_EPISODES:
        raise VideoTranscriptError("COLLECTION_EPISODE_LIMIT", "该课程超过 1000 集上限，请缩小选择范围", status=422)
    raw_entries = [item for item in (info.get("entries") or []) if isinstance(item, Mapping)]
    overflow = start + len(raw_entries) > MAX_COLLECTION_EPISODES
    if overflow:
        raise VideoTranscriptError("COLLECTION_EPISODE_LIMIT", "该课程超过 1000 集上限，请缩小选择范围", status=422)
    has_lookahead = len(raw_entries) > size
    page_entries = raw_entries[:size]
    provider = "bilibili" if is_bilibili_url(url) else ("youtube" if is_youtube_url(url) else (_host(url) or "video"))
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(page_entries):
        source_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        if source_url and not source_url.startswith(("http://", "https://")):
            extractor_key = str(entry.get("extractor_key") or entry.get("ie_key") or "").casefold()
            episode_key = str(entry.get("id") or "").strip()
            if "youtube" in extractor_key and episode_key:
                source_url = f"https://www.youtube.com/watch?v={episode_key}"
        try:
            duration = float(entry.get("duration") or 0)
        except (TypeError, ValueError, OverflowError):
            duration = 0.0
        items.append(
            {
                "provider": provider,
                "provider_episode_id": str(entry.get("id") or "")[:240],
                "canonical_url": source_url,
                "title": str(entry.get("title") or f"第 {start + index + 1} 集")[:300],
                "ordinal": start + index,
                "duration_seconds": max(0.0, duration),
                "selected": True,
                "metadata": {
                    "channel": str(entry.get("channel") or entry.get("uploader") or "")[:240],
                    "availability": str(entry.get("availability") or "")[:80],
                },
            }
        )
    next_offset = start + len(items)
    has_more = bool(has_lookahead or (reported_count is not None and next_offset < reported_count))
    if not items:
        has_more = False
    return {
        "schema": VIDEO_COLLECTION_MANIFEST_SCHEMA,
        "source_url": str(info.get("webpage_url") or url),
        "provider": provider,
        "provider_collection_id": str(info.get("id") or "")[:240],
        "title": str(info.get("title") or "视频长课程")[:300],
        "reported_count": reported_count,
        "offset": start,
        "page_size": size,
        "items": items,
        "next_offset": next_offset if has_more else None,
        "has_more": has_more,
        "network_metadata_only": True,
        "video_downloaded": False,
    }


async def enumerate_video_collection_page(
    url: str,
    *,
    offset: int = 0,
    page_size: int = DEFAULT_COLLECTION_PAGE_SIZE,
    ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    classification = classify_video_url(url)
    if not classification["recognized"]:
        raise VideoTranscriptError("VIDEO_URL_UNSUPPORTED", "该链接不是可识别的视频或课程地址", status=415)
    return await asyncio.to_thread(
        _enumerate_collection_page_sync,
        url,
        offset=offset,
        page_size=page_size,
        ydl_factory=ydl_factory,
    )


def _extract_episode_subtitle_sync(
    url: str,
    *,
    requested_languages: list[str] | None = None,
    allow_automatic: bool = True,
    ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise VideoTranscriptError("VIDEO_EXTRACTOR_UNAVAILABLE", "视频字幕提取器未安装", status=503) from exc
    languages = [str(item).strip() for item in (requested_languages or ["zh.*", "en.*"]) if str(item).strip()][:8]
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 8,
        "retries": 1,
        "writesubtitles": True,
        "writeautomaticsub": bool(allow_automatic),
        "subtitleslangs": languages,
        "subtitlesformat": "vtt/srt/json3",
    }
    factory = ydl_factory or yt_dlp.YoutubeDL
    try:
        with factory(options) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            if not isinstance(info, Mapping):
                raise VideoTranscriptError("VIDEO_EXTRACTION_FAILED", "视频字幕响应格式无效", status=422)
            chosen: tuple[str, bool, dict[str, Any], list[SubtitleSegment]] | None = None
            for language, automatic, entry in _subtitle_entries(info):
                if automatic and not allow_automatic:
                    continue
                payload = _download_subtitle(ydl, entry)
                extension = str(entry.get("ext") or entry.get("name") or "").casefold()
                segments = parse_subtitle_payload(payload, extension)
                if segments:
                    chosen = (language, automatic, entry, segments)
                    break
    except VideoTranscriptError:
        raise
    except Exception as exc:
        raise VideoTranscriptError("VIDEO_EXTRACTION_FAILED", f"视频字幕无法读取：{exc}", status=422) from exc
    if chosen is None:
        quality = assess_subtitle([], language="", automatic=False)
        return {
            "schema": VIDEO_EPISODE_TRANSCRIPT_SCHEMA,
            "source_url": str(url),
            "title": str(info.get("title") or "视频")[:300],
            "provider_episode_id": str(info.get("id") or "")[:240],
            "segments": [],
            "quality": quality,
            "track": None,
            "video_downloaded": False,
        }
    language, automatic, entry, segments = chosen
    quality = assess_subtitle(segments, language=language, automatic=automatic)
    return {
        "schema": VIDEO_EPISODE_TRANSCRIPT_SCHEMA,
        "source_url": str(info.get("webpage_url") or url),
        "title": str(info.get("title") or "视频")[:300],
        "provider_episode_id": str(info.get("id") or "")[:240],
        "duration_seconds": float(info.get("duration") or 0),
        "segments": [
            {"ordinal": index, "start_ms": item.start_ms, "end_ms": item.end_ms, "text": item.text}
            for index, item in enumerate(segments)
        ],
        "quality": quality,
        "track": {
            "language": language,
            "automatic": automatic,
            "format": str(entry.get("ext") or entry.get("name") or "").casefold(),
        },
        "video_downloaded": False,
    }


async def extract_video_episode_subtitle(
    url: str,
    *,
    requested_languages: list[str] | None = None,
    allow_automatic: bool = True,
    ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    classification = classify_video_url(url)
    if not classification["recognized"]:
        raise VideoTranscriptError("VIDEO_URL_UNSUPPORTED", "该链接不是可识别的视频地址", status=415)
    return await asyncio.to_thread(
        _extract_episode_subtitle_sync,
        url,
        requested_languages=requested_languages,
        allow_automatic=allow_automatic,
        ydl_factory=ydl_factory,
    )


def _extract_sync(url: str, ydl_factory: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise VideoTranscriptError("VIDEO_EXTRACTOR_UNAVAILABLE", "视频字幕提取器未安装", status=503) from exc
    factory = ydl_factory or yt_dlp.YoutubeDL
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        # A learner may deliberately choose a complete playlist/collection.
        # yt-dlp still returns one normal item for a single-video URL.
        "noplaylist": False,
        "socket_timeout": 8,
        "retries": 1,
        "writesubtitles": True,
        "writeautomaticsub": True,
    }
    try:
        with factory(options) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            segments: list[SubtitleSegment] = []
            language = ""
            automatic = False
            extension = ""
            episode_summaries: list[dict[str, Any]] = []
            playlist_entries = [item for item in (info.get("entries") or []) if isinstance(item, Mapping)][:50]
            source_infos = playlist_entries or [info]
            timeline_offset = 0
            for episode_index, source_info in enumerate(source_infos):
                episode_info = dict(source_info)
                if not _subtitle_entries(episode_info):
                    episode_url = str(episode_info.get("webpage_url") or episode_info.get("url") or "").strip()
                    if episode_url:
                        resolved = ydl.extract_info(episode_url, download=False) or {}
                        if isinstance(resolved, Mapping):
                            episode_info = dict(resolved)
                episode_segments: list[SubtitleSegment] = []
                episode_language = ""
                episode_automatic = False
                episode_extension = ""
                for episode_language, episode_automatic, entry in _subtitle_entries(episode_info):
                    payload = _download_subtitle(ydl, entry)
                    episode_extension = str(entry.get("ext") or entry.get("name") or "")
                    episode_segments = parse_subtitle_payload(payload, episode_extension)
                    if episode_segments:
                        break
                if not episode_segments:
                    continue
                if not language:
                    language, automatic, extension = episode_language, episode_automatic, episode_extension
                adjusted = [
                    SubtitleSegment(
                        start_ms=item.start_ms + timeline_offset,
                        end_ms=item.end_ms + timeline_offset,
                        text=item.text,
                    )
                    for item in episode_segments
                ]
                remaining = MAX_SUBTITLE_SEGMENTS - len(segments)
                if remaining <= 0:
                    break
                segments.extend(adjusted[:remaining])
                duration_ms = max(
                    int(float(episode_info.get("duration") or 0) * 1000),
                    max((item.end_ms for item in episode_segments), default=0),
                )
                episode_summaries.append({
                    "index": episode_index + 1,
                    "title": str(episode_info.get("title") or f"第 {episode_index + 1} 集")[:300],
                    "source_url": str(episode_info.get("webpage_url") or episode_info.get("url") or ""),
                    "duration_seconds": round(duration_ms / 1000),
                    "segment_count": len(episode_segments),
                })
                timeline_offset += duration_ms
            quality = assess_subtitle(segments, language=language, automatic=automatic)
            family_id = source_family_id(str(info.get("webpage_url") or url))
            return {
                "schema": VIDEO_TRANSCRIPT_SCHEMA,
                "source_url": str(info.get("webpage_url") or url),
                "source_family_id": family_id,
                "lineage": source_lineage(
                    family_id,
                    role="video_transcript",
                    relation="primary_transcript",
                    independent=False,
                    parent_url=str(info.get("webpage_url") or url),
                ),
                "independence": {
                    "independent_evidence": False,
                    "classification": "primary_source",
                },
                "provider": "bilibili" if is_bilibili_url(url) else ("youtube" if is_youtube_url(url) else (_host(url) or None)),
                "title": str(info.get("title") or "视频课程")[:300],
                "duration_seconds": int(info.get("duration") or round(timeline_offset / 1000) or 0),
                "collection": {
                    "is_collection": bool(playlist_entries),
                    "episode_count": len(episode_summaries),
                    "episodes": episode_summaries,
                },
                "subtitle": {
                    "format": extension or None,
                    "language": language or None,
                    "automatic": automatic,
                    "segments": [
                        {"start_ms": item.start_ms, "end_ms": item.end_ms, "text": item.text}
                        for item in segments
                    ],
                    "text": "\n".join(item.text for item in segments),
                    "quality": quality,
                },
                "disposition": {"state": quality["state"], "reason": quality["reason"]},
            }
    except VideoTranscriptError:
        raise
    except Exception as exc:
        raise VideoTranscriptError("VIDEO_EXTRACTION_FAILED", f"视频字幕无法读取：{exc}", status=422) from exc


async def extract_video_transcript(url: str, *, ydl_factory: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    classification = classify_video_url(url)
    if not classification["recognized"]:
        raise VideoTranscriptError("VIDEO_URL_UNSUPPORTED", "该链接不是可识别的视频地址", status=415)
    return await asyncio.to_thread(_extract_sync, url, ydl_factory)

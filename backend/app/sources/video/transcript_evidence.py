"""Fetch subtitle payloads and build immutable, per-part transcript evidence.

The module consumes the exact yt-dlp metadata snapshot produced by KA-01.  It
never downloads media, persists records, invokes a model, or merges part-local
timelines into one artificial clock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Awaitable, Callable, Iterable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ...url_fetcher_secure import _bounded_content, validate_public_url
from ...video_transcript import MAX_SUBTITLE_BYTES, MAX_SUBTITLE_SEGMENTS
from .bilibili_preflight import (
    BilibiliPreflightSnapshot,
    BilibiliSubtitleTrack,
    VideoPreflightStatus,
    inspect_bilibili_video,
    select_bilibili_subtitle_track,
)


TRANSCRIPT_EVIDENCE_SCHEMA = "mentorflow.transcript-evidence.v1"
TRANSCRIPT_PARSER_VERSION = "mentorflow.subtitle-timeline.v1"
SUPPORTED_EVIDENCE_EXTENSIONS = frozenset({"bcc", "json", "json3", "srt", "vtt"})
SUBTITLE_FETCH_TIMEOUT_SECONDS = 8.0
MAX_SUBTITLE_REDIRECTS = 3
MAX_SUBTITLE_DURATION_MS = 6 * 60 * 60 * 1000
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_TIMESTAMP_RE = re.compile(
    r"^(?:(?P<hours>[0-9]+):)?(?P<minutes>[0-5][0-9]):(?P<seconds>[0-5][0-9])"
    r"[.,](?P<milliseconds>[0-9]{3})$"
)
_TIMELINE_RE = re.compile(r"^(?P<start>\S+)\s*-->\s*(?P<end>\S+)(?:\s+.*)?$")
_ALLOWED_TRACK_HEADERS = frozenset({"accept", "accept-language", "origin", "referer", "user-agent"})
_CROSS_ORIGIN_SENSITIVE_HEADERS = frozenset({"origin", "referer"})
_ALLOWED_SUBTITLE_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/srt",
        "application/vnd.youtube.yt",
        "application/vtt",
        "application/x-subrip",
        "text/json",
        "text/plain",
        "text/srt",
        "text/vtt",
        "text/x-subrip",
    }
)


class TranscriptEvidenceError(ValueError):
    """Stable KA-02 failure with no partial evidence side effect."""

    def __init__(self, code: str, message: str, *, part_index: int | None = None):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.part_index = part_index


@dataclass(frozen=True)
class ParsedTranscriptCue:
    start_ms: int
    end_ms: int
    original_text: str


@dataclass(frozen=True)
class ParsedSubtitleTimeline:
    cues: tuple[ParsedTranscriptCue, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SelectedTrackEvidence:
    track_identity: str
    payload_source_identity: str
    language: str
    kind: str
    extension: str
    source: str
    name: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_identity": self.track_identity,
            "payload_source_identity": self.payload_source_identity,
            "language": self.language,
            "kind": self.kind,
            "extension": self.extension,
            "source": self.source,
            "name": self.name,
        }


@dataclass(frozen=True)
class TranscriptSegment:
    stable_segment_id: str
    ordinal: int
    part_index: int
    part_cid: str | None
    start_ms: int
    end_ms: int
    original_text: str
    language: str
    subtitle_kind: str
    source_reference: str
    segment_hash: str

    @property
    def segment_id(self) -> str:
        return self.stable_segment_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.stable_segment_id,
            "stable_segment_id": self.stable_segment_id,
            "ordinal": self.ordinal,
            "part_index": self.part_index,
            "part_cid": self.part_cid,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "original_text": self.original_text,
            "language": self.language,
            "subtitle_kind": self.subtitle_kind,
            "source_reference": self.source_reference,
            "segment_hash": self.segment_hash,
        }


@dataclass(frozen=True)
class TranscriptPartEvidence:
    part_index: int
    cid: str | None
    title: str
    duration_seconds: float
    subtitle_identity: str
    selected_track: SelectedTrackEvidence
    raw_payload_hash: str
    segment_ids: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_index": self.part_index,
            "cid": self.cid,
            "title": self.title,
            "duration_seconds": self.duration_seconds,
            "subtitle_identity": self.subtitle_identity,
            "selected_track": self.selected_track.to_dict(),
            "raw_payload_hash": self.raw_payload_hash,
            "segment_ids": list(self.segment_ids),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TranscriptEvidenceBundle:
    schema: str
    parser_version: str
    bundle_id: str
    bundle_hash: str
    source_reference: str
    platform: str
    bvid: str
    source_identity: str
    source_revision: str
    part_count: int
    subtitle_language: str
    subtitle_kind: str
    fetched_at: str
    raw_payload_hash: str
    normalized_timeline_hash: str
    parts: tuple[TranscriptPartEvidence, ...]
    segments: tuple[TranscriptSegment, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "parser_version": self.parser_version,
            "bundle_id": self.bundle_id,
            "bundle_hash": self.bundle_hash,
            "source_reference": self.source_reference,
            "platform": self.platform,
            "bvid": self.bvid,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "part_count": self.part_count,
            "subtitle_language": self.subtitle_language,
            "subtitle_kind": self.subtitle_kind,
            "fetched_at": self.fetched_at,
            "raw_payload_hash": self.raw_payload_hash,
            "normalized_timeline_hash": self.normalized_timeline_hash,
            "parts": [part.to_dict() for part in self.parts],
            "segments": [segment.to_dict() for segment in self.segments],
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


PayloadFetcher = Callable[[BilibiliSubtitleTrack], Awaitable[bytes | str]]


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _timestamp_ms(value: str) -> int | None:
    match = _TIMESTAMP_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    milliseconds = int(match.group("milliseconds"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _decimal_ms(value: Any, *, scale: int) -> int | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return int((number * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _finish_parsed_timeline(
    cues: list[ParsedTranscriptCue],
    *,
    recognized: int,
    empty: int,
    invalid: int,
) -> ParsedSubtitleTimeline:
    if recognized > MAX_SUBTITLE_SEGMENTS:
        raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "字幕段数超过 20,000 段限制")
    if not cues:
        if recognized > 0 and empty == recognized and invalid == 0:
            raise TranscriptEvidenceError("SUBTITLE_EMPTY", "字幕 cue 正文为空")
        raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "字幕正文无法解析为有效时间轴")
    warnings: list[str] = []
    if invalid:
        warnings.append(f"rejected_invalid_cues:{invalid}")
    if empty:
        warnings.append(f"rejected_empty_cues:{empty}")
    return ParsedSubtitleTimeline(tuple(cues), tuple(warnings))


def _parse_srt_or_vtt(text: str) -> ParsedSubtitleTimeline:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[ParsedTranscriptCue] = []
    recognized = empty = invalid = 0
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        recognized += 1
        if recognized > MAX_SUBTITLE_SEGMENTS:
            raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "字幕段数超过 20,000 段限制")
        match = _TIMELINE_RE.fullmatch(line)
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index].strip():
            body.append(lines[index])
            index += 1
        original_text = "\n".join(body).strip()
        if not original_text:
            empty += 1
            index += 1
            continue
        start_ms = _timestamp_ms(match.group("start")) if match else None
        end_ms = _timestamp_ms(match.group("end")) if match else None
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            invalid += 1
        elif end_ms > MAX_SUBTITLE_DURATION_MS:
            raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "字幕时间轴超过 6 小时限制")
        else:
            cues.append(ParsedTranscriptCue(start_ms, end_ms, original_text))
        index += 1
    return _finish_parsed_timeline(cues, recognized=recognized, empty=empty, invalid=invalid)


def _parse_json_timeline(text: str) -> ParsedSubtitleTimeline:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "JSON 字幕格式无效") from exc
    if not isinstance(payload, Mapping):
        raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "JSON 字幕必须是对象")

    if "body" in payload:
        raw_cues = payload.get("body")
        mode = "bcc"
    elif "events" in payload:
        raw_cues = payload.get("events")
        mode = "json3"
    else:
        raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "JSON 字幕缺少 body/events")
    if not isinstance(raw_cues, list):
        raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "JSON 字幕 cue 列表无效")
    if not raw_cues:
        raise TranscriptEvidenceError("SUBTITLE_EMPTY", "字幕 cue 列表为空")
    if len(raw_cues) > MAX_SUBTITLE_SEGMENTS:
        raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "字幕段数超过 20,000 段限制")

    cues: list[ParsedTranscriptCue] = []
    recognized = len(raw_cues)
    empty = invalid = 0
    for item in raw_cues:
        if not isinstance(item, Mapping):
            invalid += 1
            continue
        if mode == "bcc":
            start_ms = _decimal_ms(item.get("from"), scale=1000)
            end_ms = _decimal_ms(item.get("to"), scale=1000)
            original_text = str(item.get("content") or "").replace("\r\n", "\n").strip()
        else:
            start_ms = _decimal_ms(item.get("tStartMs"), scale=1)
            duration_ms = _decimal_ms(item.get("dDurationMs"), scale=1)
            end_ms = start_ms + duration_ms if start_ms is not None and duration_ms is not None else None
            raw_segments = item.get("segs")
            original_text = (
                "".join(str(segment.get("utf8") or "") for segment in raw_segments if isinstance(segment, Mapping)).strip()
                if isinstance(raw_segments, list)
                else ""
            )
        if not original_text:
            empty += 1
            continue
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            invalid += 1
            continue
        if end_ms > MAX_SUBTITLE_DURATION_MS:
            raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "字幕时间轴超过 6 小时限制")
        cues.append(ParsedTranscriptCue(start_ms, end_ms, original_text))
    return _finish_parsed_timeline(cues, recognized=recognized, empty=empty, invalid=invalid)


def parse_subtitle_timeline(payload: bytes | str, extension: str) -> ParsedSubtitleTimeline:
    if not isinstance(payload, (str, bytes, bytearray)):
        raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "字幕正文数据类型无效")
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if len(raw) > MAX_SUBTITLE_BYTES:
        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕正文超过大小限制")
    if not raw or not raw.strip():
        raise TranscriptEvidenceError("SUBTITLE_EMPTY", "字幕正文为空")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", "字幕正文不是有效 UTF-8") from exc
    ext = str(extension or "").casefold().lstrip(".")
    if ext in {"json", "json3", "bcc"}:
        return _parse_json_timeline(text)
    if ext in {"srt", "vtt"}:
        return _parse_srt_or_vtt(text)
    raise TranscriptEvidenceError("SUBTITLE_PARSE_ERROR", f"不支持的字幕格式：{ext or 'unknown'}")


def _safe_track_headers(track: BilibiliSubtitleTrack) -> dict[str, str]:
    headers = {
        key: value
        for raw_key, value in track.http_headers
        if (key := str(raw_key).casefold()) in _ALLOWED_TRACK_HEADERS
    }
    headers.setdefault("user-agent", "MentorFlow/2.0")
    return headers


def _public_url_identity(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    stable_url = urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path, "", ""))
    return "subtitle-url-" + _sha256_bytes(stable_url.encode("utf-8"))


def _url_origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port


async def fetch_subtitle_url_payload(
    track: BilibiliSubtitleTrack,
    *,
    client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> bytes:
    """Fetch one subtitle body with shared SSRF validation and bounded redirects."""

    current = str(track.url or "").strip()
    if not current:
        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕 track 缺少 URL")
    if urlsplit(current).scheme.casefold() != "https":
        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕 URL 必须使用 HTTPS")
    initial_origin = _url_origin(current)
    request_headers = _safe_track_headers(track)
    try:
        async with asyncio.timeout(SUBTITLE_FETCH_TIMEOUT_SECONDS):
            async with client_factory(
                timeout=SUBTITLE_FETCH_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                for hop in range(MAX_SUBTITLE_REDIRECTS + 1):
                    if urlsplit(current).scheme.casefold() != "https":
                        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕 URL 必须使用 HTTPS")
                    await asyncio.to_thread(validate_public_url, current)
                    async with client.stream("GET", current, headers=request_headers) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕重定向缺少目标")
                            redirected = urljoin(current, location)
                            if urlsplit(redirected).scheme.casefold() != "https":
                                raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕重定向拒绝 HTTPS 降级")
                            if hop >= MAX_SUBTITLE_REDIRECTS:
                                raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕重定向次数过多")
                            if _url_origin(redirected) != initial_origin:
                                request_headers = {
                                    key: value
                                    for key, value in request_headers.items()
                                    if key not in _CROSS_ORIGIN_SENSITIVE_HEADERS
                                }
                            current = redirected
                            continue
                        if response.status_code >= 400:
                            raise TranscriptEvidenceError(
                                "SUBTITLE_FETCH_ERROR",
                                f"字幕服务返回 HTTP {response.status_code}",
                            )
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
                        if content_type not in _ALLOWED_SUBTITLE_CONTENT_TYPES:
                            raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕 URL 返回了非字幕内容")
                        return await _bounded_content(response, limit_bytes=MAX_SUBTITLE_BYTES)
    except TranscriptEvidenceError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕正文获取超时") from None
    except Exception:
        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕正文获取失败") from None
    raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕正文获取失败")


def _payload_bytes(value: Any) -> bytes:
    if not isinstance(value, (str, bytes, bytearray)):
        raise TypeError("subtitle payload must be bytes or text")
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


async def _load_track_payload(
    track: BilibiliSubtitleTrack,
    *,
    payload_fetcher: PayloadFetcher | None,
) -> tuple[bytes, str, str]:
    if track.data is not None and (track.data or not track.url):
        try:
            payload = _payload_bytes(track.data)
        except (TypeError, ValueError):
            raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕正文数据类型无效") from None
        return payload, "data", "inline-data-" + _sha256_bytes(payload)
    if not track.url:
        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕 track 没有 data 或 URL")
    try:
        value = await (payload_fetcher(track) if payload_fetcher else fetch_subtitle_url_payload(track))
    except TranscriptEvidenceError:
        raise
    except Exception:
        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕正文获取失败") from None
    try:
        payload = _payload_bytes(value)
    except (TypeError, ValueError):
        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕正文数据类型无效") from None
    return payload, "url", _public_url_identity(track.url)


def _common_value(values: Iterable[str]) -> str:
    unique = list(dict.fromkeys(str(value) for value in values))
    return unique[0] if len(unique) == 1 else "mixed"


async def fetch_bilibili_transcript_evidence(
    reference: str,
    *,
    preferred_languages: Iterable[str] = (),
    ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
    payload_fetcher: PayloadFetcher | None = None,
    fetched_at: str | None = None,
) -> TranscriptEvidenceBundle:
    """Fetch all selected part subtitles and build a deterministic evidence bundle."""

    snapshot: BilibiliPreflightSnapshot = await asyncio.to_thread(
        inspect_bilibili_video,
        reference,
        ydl_factory=ydl_factory,
    )
    result = snapshot.result
    if result.status is not VideoPreflightStatus.SUPPORTED:
        raise TranscriptEvidenceError(
            result.error_code or "SOURCE_PREFLIGHT_FAILED",
            result.error_message or "视频字幕预检未通过",
        )
    if not result.canonical_url or not result.bvid or len(snapshot.part_infos) != result.part_count:
        raise TranscriptEvidenceError("SUBTITLE_FETCH_ERROR", "字幕预检快照不完整")

    preferences = tuple(preferred_languages)
    source_identity = f"bilibili:{result.bvid}"
    segments: list[TranscriptSegment] = []
    parts: list[TranscriptPartEvidence] = []
    bundle_warnings: list[str] = []

    for ordinal, (diagnostic, part_info) in enumerate(zip(result.parts, snapshot.part_infos, strict=True), start=1):
        track = select_bilibili_subtitle_track(
            part_info,
            preferred_languages=preferences,
            allowed_extensions=SUPPORTED_EVIDENCE_EXTENSIONS,
        )
        if track is None:
            raise TranscriptEvidenceError(
                "SUBTITLE_PARSE_ERROR",
                "预检通过但没有 KA-02 可解析的字幕 track",
                part_index=diagnostic.index,
            )
        try:
            payload, payload_source, payload_source_identity = await _load_track_payload(
                track,
                payload_fetcher=payload_fetcher,
            )
            parsed = parse_subtitle_timeline(payload, track.extension)
        except TranscriptEvidenceError as exc:
            raise TranscriptEvidenceError(exc.code, exc.message, part_index=diagnostic.index) from None

        raw_payload_hash = _sha256_bytes(payload)
        kind = track.kind.value.casefold()
        track_identity = "subtitle-track-" + _sha256_json(
            {
                "source_identity": source_identity,
                "part_index": diagnostic.index,
                "cid": diagnostic.cid,
                "language": track.language,
                "kind": kind,
                "extension": track.extension,
                "name": track.name,
            }
        )
        subtitle_identity = "subtitle-evidence-" + _sha256_json(
            {
                "track_identity": track_identity,
                "raw_payload_hash": raw_payload_hash,
                "parser_version": TRANSCRIPT_PARSER_VERSION,
            }
        )
        selected_track = SelectedTrackEvidence(
            track_identity=track_identity,
            payload_source_identity=payload_source_identity,
            language=track.language,
            kind=kind,
            extension=track.extension,
            source=payload_source,
            name=track.name,
        )
        part_segment_ids: list[str] = []
        for cue_ordinal, cue in enumerate(parsed.cues, start=1):
            segment_hash = _sha256_bytes(cue.original_text.encode("utf-8"))
            stable_segment_id = "segment-" + _sha256_json(
                {
                    "source_identity": source_identity,
                    "part_index": diagnostic.index,
                    "cid": diagnostic.cid,
                    "ordinal": cue_ordinal,
                    "start_ms": cue.start_ms,
                    "end_ms": cue.end_ms,
                    "segment_hash": segment_hash,
                }
            )
            part_segment_ids.append(stable_segment_id)
            segments.append(
                TranscriptSegment(
                    stable_segment_id=stable_segment_id,
                    ordinal=cue_ordinal,
                    part_index=diagnostic.index,
                    part_cid=diagnostic.cid,
                    start_ms=cue.start_ms,
                    end_ms=cue.end_ms,
                    original_text=cue.original_text,
                    language=track.language,
                    subtitle_kind=kind,
                    source_reference=result.canonical_url,
                    segment_hash=segment_hash,
                )
            )
        part_warnings = tuple(f"part:{diagnostic.index}:{warning}" for warning in parsed.warnings)
        bundle_warnings.extend(part_warnings)
        parts.append(
            TranscriptPartEvidence(
                part_index=diagnostic.index,
                cid=diagnostic.cid,
                title=diagnostic.part,
                duration_seconds=diagnostic.duration_seconds,
                subtitle_identity=subtitle_identity,
                selected_track=selected_track,
                raw_payload_hash=raw_payload_hash,
                segment_ids=tuple(part_segment_ids),
                warnings=part_warnings,
            )
        )

    raw_payload_hash = _sha256_json(
        [
            {"part_index": part.part_index, "cid": part.cid, "raw_payload_hash": part.raw_payload_hash}
            for part in parts
        ]
    )
    timeline_projection = [
        {
            "stable_segment_id": segment.stable_segment_id,
            "part_index": segment.part_index,
            "part_cid": segment.part_cid,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
            "original_text": segment.original_text,
            "language": segment.language,
            "subtitle_kind": segment.subtitle_kind,
            "segment_hash": segment.segment_hash,
        }
        for segment in segments
    ]
    normalized_timeline_hash = _sha256_json(timeline_projection)
    source_revision_hash = _sha256_json(
        {
            "source_identity": source_identity,
            "part_subtitle_identities": [part.subtitle_identity for part in parts],
            "raw_payload_hash": raw_payload_hash,
            "parser_version": TRANSCRIPT_PARSER_VERSION,
        }
    )
    source_revision = "subtitle-revision-" + source_revision_hash
    identity_projection = {
        "schema": TRANSCRIPT_EVIDENCE_SCHEMA,
        "parser_version": TRANSCRIPT_PARSER_VERSION,
        "source_reference": result.canonical_url,
        "source_identity": source_identity,
        "source_revision": source_revision,
        "raw_payload_hash": raw_payload_hash,
        "normalized_timeline_hash": normalized_timeline_hash,
        "parts": [part.to_dict() for part in parts],
        "segments": timeline_projection,
    }
    bundle_hash = _sha256_json(identity_projection)
    return TranscriptEvidenceBundle(
        schema=TRANSCRIPT_EVIDENCE_SCHEMA,
        parser_version=TRANSCRIPT_PARSER_VERSION,
        bundle_id="transcript-evidence-" + bundle_hash,
        bundle_hash=bundle_hash,
        source_reference=result.canonical_url,
        platform="bilibili",
        bvid=result.bvid,
        source_identity=source_identity,
        source_revision=source_revision,
        part_count=len(parts),
        subtitle_language=_common_value(part.selected_track.language for part in parts),
        subtitle_kind=_common_value(part.selected_track.kind for part in parts),
        fetched_at=fetched_at or datetime.now(timezone.utc).isoformat(),
        raw_payload_hash=raw_payload_hash,
        normalized_timeline_hash=normalized_timeline_hash,
        parts=tuple(parts),
        segments=tuple(segments),
        warnings=tuple(bundle_warnings),
    )

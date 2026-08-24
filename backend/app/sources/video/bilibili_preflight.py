"""Subtitle-first Bilibili source preflight backed by yt-dlp.

This module answers only whether a BV video (including all parts of a multi-P
course) exposes at least one readable subtitle track per part.  It deliberately
does not download media, fetch transcript bodies itself, persist anything, or
select the source for a learning plan.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit


class VideoPreflightStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED_NO_SUBTITLE = "UNSUPPORTED_NO_SUBTITLE"
    SUBTITLE_LOGIN_REQUIRED = "SUBTITLE_LOGIN_REQUIRED"
    FETCH_ERROR = "FETCH_ERROR"
    INVALID_REFERENCE = "INVALID_REFERENCE"


class SubtitleKind(str, Enum):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"
    MIXED = "MIXED"


MAX_PREFLIGHT_PARTS = 1000


@dataclass(frozen=True)
class BilibiliReference:
    reference: str
    canonical_url: str
    bvid: str | None


@dataclass(frozen=True)
class VideoPartDiagnostic:
    index: int
    part: str
    cid: str | None
    duration_seconds: float
    subtitle_available: bool
    subtitle_kind: SubtitleKind | None
    subtitle_language: str | None
    login_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "part": self.part,
            "cid": self.cid,
            "duration_seconds": self.duration_seconds,
            "subtitle_available": self.subtitle_available,
            "subtitle_kind": self.subtitle_kind.value if self.subtitle_kind else None,
            "subtitle_language": self.subtitle_language,
            "login_required": self.login_required,
        }


@dataclass(frozen=True)
class VideoPreflightResult:
    platform: str
    reference: str
    canonical_url: str | None
    bvid: str | None
    title: str | None
    part_count: int
    total_duration_seconds: float
    subtitle_available: bool
    subtitle_kind: SubtitleKind | None
    subtitle_language: str | None
    login_required: bool
    status: VideoPreflightStatus
    error_code: str | None
    error_message: str | None
    parts: tuple[VideoPartDiagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "reference": self.reference,
            "canonical_url": self.canonical_url,
            "bvid": self.bvid,
            "title": self.title,
            "part_count": self.part_count,
            "total_duration_seconds": self.total_duration_seconds,
            "subtitle_available": self.subtitle_available,
            "subtitle_kind": self.subtitle_kind.value if self.subtitle_kind else None,
            "subtitle_language": self.subtitle_language,
            "login_required": self.login_required,
            "status": self.status.value,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "parts": [part.to_dict() for part in self.parts],
        }


_BVID_RE = re.compile(r"^BV(?P<body>[0-9A-Za-z]{10})$", re.IGNORECASE)
_VIDEO_PATH_RE = re.compile(r"^/video/(?P<bvid>BV[0-9A-Za-z]{10})/?$", re.IGNORECASE)
_DIRECT_VIDEO_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"})
_DANMAKU_TOKENS = ("danmaku", "bullet comment", "弹幕")
_SUPPORTED_SUBTITLE_EXTENSIONS = frozenset(
    {"ass", "bcc", "json", "json3", "lrc", "srt", "ttml", "vtt"}
)
_AUTOMATIC_TOKENS = (
    "automatic",
    "auto-generated",
    "speech recognition",
    "ai subtitle",
    "ai-generated",
    "ai字幕",
    "自动字幕",
    "智能字幕",
    "机翻",
)


class _InvalidExtraction(ValueError):
    pass


class _YtdlpMessageLog:
    """Capture provider warnings without writing yt-dlp output to the console."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: Any) -> None:
        self.messages.append(str(message or ""))

    def info(self, message: Any) -> None:
        self.messages.append(str(message or ""))

    def warning(self, message: Any) -> None:
        self.messages.append(str(message or ""))

    def error(self, message: Any) -> None:
        self.messages.append(str(message or ""))


@dataclass(frozen=True)
class _SubtitleTrack:
    kind: SubtitleKind
    language: str | None


def normalize_bilibili_reference(reference: str) -> BilibiliReference:
    """Normalize a BV ID or direct Bilibili video URL without network access."""

    value = str(reference or "").strip()
    bvid_match = _BVID_RE.fullmatch(value)
    if bvid_match:
        bvid = "BV" + bvid_match.group("body")
        return BilibiliReference(value, f"https://www.bilibili.com/video/{bvid}/", bvid)

    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold().strip(".")
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("invalid Bilibili reference") from None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        raise ValueError("invalid Bilibili reference")

    if host in _DIRECT_VIDEO_HOSTS:
        match = _VIDEO_PATH_RE.fullmatch(parsed.path or "")
        if not match:
            raise ValueError("invalid Bilibili reference")
        raw_bvid = match.group("bvid")
        bvid = "BV" + raw_bvid[2:]
        return BilibiliReference(value, f"https://www.bilibili.com/video/{bvid}/", bvid)

    raise ValueError("invalid Bilibili reference")


def _failure(
    reference: str,
    status: VideoPreflightStatus,
    error_code: str,
    error_message: str,
    *,
    normalized: BilibiliReference | None = None,
) -> VideoPreflightResult:
    return VideoPreflightResult(
        platform="bilibili",
        reference=str(reference or "").strip(),
        canonical_url=normalized.canonical_url if normalized else None,
        bvid=normalized.bvid if normalized else None,
        title=None,
        part_count=0,
        total_duration_seconds=0.0,
        subtitle_available=False,
        subtitle_kind=None,
        subtitle_language=None,
        login_required=status is VideoPreflightStatus.SUBTITLE_LOGIN_REQUIRED,
        status=status,
        error_code=error_code,
        error_message=error_message,
        parts=(),
    )


def _safe_duration(value: Any) -> float:
    try:
        duration = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(duration) or duration < 0:
        return 0.0
    return round(duration, 3)


def _looks_automatic(language: str, track: Mapping[str, Any]) -> bool:
    if track.get("automatic") is True:
        return True
    label = " ".join(
        str(value or "")
        for value in (language, track.get("name"), track.get("label"), track.get("language"))
    ).casefold()
    if re.search(r"(?:^|[-_ ])(?:ai|auto)(?:$|[-_ ])", label):
        return True
    return any(token in label for token in _AUTOMATIC_TOKENS)


def _is_readable_track(language: str, track: Mapping[str, Any]) -> bool:
    label = " ".join(
        str(value or "") for value in (language, track.get("name"), track.get("label"))
    ).casefold()
    raw_url = str(track.get("url") or "").strip()
    extension = str(track.get("ext") or "").casefold().lstrip(".")
    if not extension and raw_url:
        extension = urlsplit(raw_url).path.rsplit("/", 1)[-1].rsplit(".", 1)[-1].casefold()
    if (
        extension not in _SUPPORTED_SUBTITLE_EXTENSIONS
        or any(token in label for token in _DANMAKU_TOKENS)
    ):
        return False
    data = track.get("data")
    has_data = isinstance(data, (str, bytes, bytearray)) and bool(data)
    has_url = bool(raw_url)
    return has_data or has_url


def _subtitle_tracks(info: Mapping[str, Any]) -> list[_SubtitleTrack]:
    tracks: list[_SubtitleTrack] = []
    for field, field_is_automatic in (("subtitles", False), ("automatic_captions", True)):
        raw_languages = info.get(field)
        if not isinstance(raw_languages, Mapping):
            continue
        for raw_language, raw_tracks in raw_languages.items():
            language = str(raw_language or "").strip()
            values: Iterable[Any]
            if isinstance(raw_tracks, Mapping):
                values = (raw_tracks,)
            elif isinstance(raw_tracks, Iterable) and not isinstance(raw_tracks, (str, bytes, bytearray)):
                values = raw_tracks
            else:
                continue
            for raw_track in values:
                if not isinstance(raw_track, Mapping) or not _is_readable_track(language, raw_track):
                    continue
                automatic = field_is_automatic or _looks_automatic(language, raw_track)
                tracks.append(
                    _SubtitleTrack(
                        SubtitleKind.AUTOMATIC if automatic else SubtitleKind.MANUAL,
                        language or None,
                    )
                )
    tracks.sort(
        key=lambda track: (
            track.kind is SubtitleKind.AUTOMATIC,
            0 if str(track.language or "").casefold().startswith(("zh", "ai-zh", "en")) else 1,
            str(track.language or "").casefold(),
        )
    )
    return tracks


def _explicit_login_required(info: Mapping[str, Any]) -> bool:
    return any(
        info.get(key) is True
        for key in ("subtitle_login_required", "subtitles_login_required", "need_login_subtitle")
    )


def _messages_require_subtitle_login(messages: Iterable[Any]) -> bool:
    for raw_message in messages:
        message = str(raw_message or "").casefold()
        if re.search(
            r"\b(?:subtitles?|captions?)\s+(?:are|is)\s+only\s+available\s+when\s+"
            r"(?:logged[- ]in|you\s+log\s+in)\b",
            message,
        ):
            return True
        if "字幕仅在登录后可用" in message or "字幕只有登录后才能" in message:
            return True
    return False


def _part_infos(info: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_entries = info.get("entries")
    if raw_entries is None:
        return [info]
    reported_raw = info.get("playlist_count", info.get("n_entries", info.get("entry_count")))
    if reported_raw not in {None, ""}:
        try:
            reported_count = int(reported_raw)
        except (TypeError, ValueError, OverflowError):
            reported_count = None
        if reported_count is not None and reported_count > MAX_PREFLIGHT_PARTS:
            raise _InvalidExtraction("Bilibili multipart video exceeds the 1000-part preflight limit")
    if isinstance(raw_entries, (str, bytes, bytearray, Mapping)):
        raise _InvalidExtraction("yt-dlp entries payload is invalid")
    try:
        raw_items = list(raw_entries)
    except TypeError as exc:
        raise _InvalidExtraction("yt-dlp entries payload is invalid") from exc
    if not raw_items:
        raise _InvalidExtraction("yt-dlp returned an empty multipart video")
    if len(raw_items) > MAX_PREFLIGHT_PARTS:
        raise _InvalidExtraction("Bilibili multipart video exceeds the 1000-part preflight limit")
    if any(not isinstance(entry, Mapping) for entry in raw_items):
        raise _InvalidExtraction("yt-dlp returned an incomplete multipart video")
    entries = list(raw_items)
    # yt-dlp can expose one legacy video as multiple transport fragments.  They
    # are one Bilibili part, not multiple course parts.
    if str(info.get("_type") or "").casefold() == "multi_video":
        merged = dict(info)
        for field in ("subtitles", "automatic_captions"):
            merged_tracks: dict[str, list[Any]] = {}
            for entry in entries:
                for language, tracks in (entry.get(field) or {}).items():
                    merged_tracks.setdefault(str(language), []).extend(list(tracks or []))
            if merged_tracks:
                merged[field] = merged_tracks
        merged.pop("entries", None)
        return [merged]
    return entries


def _aggregate_result(
    reference: BilibiliReference,
    info: Mapping[str, Any],
    logger: _YtdlpMessageLog,
) -> VideoPreflightResult:
    source_parts = _part_infos(info)
    diagnostics: list[VideoPartDiagnostic] = []
    for ordinal, part_info in enumerate(source_parts, start=1):
        tracks = _subtitle_tracks(part_info)
        selected = tracks[0] if tracks else None
        raw_index = part_info.get("playlist_index", ordinal)
        try:
            index = max(1, int(raw_index))
        except (TypeError, ValueError, OverflowError):
            index = ordinal
        diagnostics.append(
            VideoPartDiagnostic(
                index=index,
                part=str(part_info.get("part") or part_info.get("title") or f"P{ordinal}")[:300],
                cid=str(part_info.get("cid"))[:120] if part_info.get("cid") not in {None, ""} else None,
                duration_seconds=_safe_duration(part_info.get("duration")),
                subtitle_available=selected is not None,
                subtitle_kind=selected.kind if selected else None,
                subtitle_language=selected.language if selected else None,
                login_required=bool(not selected and _explicit_login_required(part_info)),
            )
        )

    all_parts_supported = bool(diagnostics) and all(part.subtitle_available for part in diagnostics)
    login_signal = _messages_require_subtitle_login(logger.messages) or any(
        part.login_required for part in diagnostics
    )
    if all_parts_supported:
        status = VideoPreflightStatus.SUPPORTED
        error_code = error_message = None
    elif login_signal:
        status = VideoPreflightStatus.SUBTITLE_LOGIN_REQUIRED
        error_code = "BILIBILI_SUBTITLE_LOGIN_REQUIRED"
        error_message = "字幕存在，但当前需要登录 Bilibili 才能读取"
    else:
        status = VideoPreflightStatus.UNSUPPORTED_NO_SUBTITLE
        error_code = "BILIBILI_SUBTITLE_UNAVAILABLE"
        error_message = "未取得课程全部分段的可读取字幕"

    kinds = {part.subtitle_kind for part in diagnostics if part.subtitle_kind is not None}
    if len(kinds) > 1:
        subtitle_kind = SubtitleKind.MIXED
    else:
        subtitle_kind = next(iter(kinds), None)
    languages = list(
        dict.fromkeys(part.subtitle_language for part in diagnostics if part.subtitle_language)
    )
    return VideoPreflightResult(
        platform="bilibili",
        reference=reference.reference,
        canonical_url=reference.canonical_url,
        bvid=reference.bvid,
        title=str(info.get("title") or diagnostics[0].part or "Bilibili 视频")[:300],
        part_count=len(diagnostics),
        total_duration_seconds=round(sum(part.duration_seconds for part in diagnostics), 3),
        subtitle_available=all_parts_supported,
        subtitle_kind=subtitle_kind,
        subtitle_language=languages[0] if languages else None,
        login_required=status is VideoPreflightStatus.SUBTITLE_LOGIN_REQUIRED,
        status=status,
        error_code=error_code,
        error_message=error_message,
        parts=tuple(diagnostics),
    )


def _safe_exception_message(exc: BaseException) -> str:
    message = " ".join(str(exc or "").split()) or exc.__class__.__name__
    message = re.sub(r"https?://\S+", "[redacted-url]", message, flags=re.IGNORECASE)
    message = re.sub(
        r"(?i)\b(cookie|password|sessdata|signature|token)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        message,
    )
    return message[:400]


def preflight_bilibili_video(
    reference: str,
    *,
    ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> VideoPreflightResult:
    """Return the Video v1 capability status without downloading media."""

    try:
        normalized = normalize_bilibili_reference(reference)
    except ValueError:
        return _failure(
            reference,
            VideoPreflightStatus.INVALID_REFERENCE,
            "INVALID_BILIBILI_REFERENCE",
            "仅支持合法的 Bilibili BV 号或视频链接",
        )

    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError:
            return _failure(
                reference,
                VideoPreflightStatus.FETCH_ERROR,
                "YTDLP_UNAVAILABLE",
                "yt-dlp 未安装，无法读取 Bilibili 字幕元数据",
                normalized=normalized,
            )
        ydl_factory = yt_dlp.YoutubeDL

    logger = _YtdlpMessageLog()
    options = {
        "quiet": True,
        "no_warnings": False,
        "skip_download": True,
        "noplaylist": False,
        "extract_flat": False,
        # One look-ahead entry lets aggregation fail closed instead of silently
        # declaring only the first 1000 parts of a larger course supported.
        "playlistend": MAX_PREFLIGHT_PARTS + 1,
        "cachedir": False,
        "socket_timeout": 8,
        "retries": 1,
        "listsubtitles": True,
        "logger": logger,
    }
    try:
        with ydl_factory(options) as ydl:
            raw_info = ydl.extract_info(normalized.canonical_url, download=False)
        if not isinstance(raw_info, Mapping):
            raise _InvalidExtraction("yt-dlp returned invalid metadata")
        return _aggregate_result(normalized, raw_info, logger)
    except Exception as exc:
        messages = [*logger.messages, _safe_exception_message(exc)]
        if _messages_require_subtitle_login(messages):
            return _failure(
                reference,
                VideoPreflightStatus.SUBTITLE_LOGIN_REQUIRED,
                "BILIBILI_SUBTITLE_LOGIN_REQUIRED",
                "字幕存在，但当前需要登录 Bilibili 才能读取",
                normalized=normalized,
            )
        return _failure(
            reference,
            VideoPreflightStatus.FETCH_ERROR,
            "BILIBILI_FETCH_ERROR",
            f"Bilibili 元数据读取失败：{_safe_exception_message(exc)}",
            normalized=normalized,
        )

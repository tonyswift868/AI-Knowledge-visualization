from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
import pytest

from app.sources.video.bilibili_preflight import BilibiliSubtitleTrack, SubtitleKind
from app.sources.video.transcript_evidence import (
    MAX_SUBTITLE_BYTES,
    MAX_SUBTITLE_SEGMENTS,
    TranscriptEvidenceError,
    fetch_bilibili_transcript_evidence,
    fetch_subtitle_url_payload,
    parse_subtitle_timeline,
)


def _factory(
    info: dict[str, Any],
    *,
    inspect_options: Callable[[dict[str, Any]], None] | None = None,
    calls: list[tuple[str, bool]] | None = None,
):
    class FakeYdl:
        def __init__(self, options: dict[str, Any]):
            self.options = options
            if inspect_options:
                inspect_options(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url: str, download: bool = True):
            if calls is not None:
                calls.append((url, download))
            assert download is False
            return info

        def download(self, *_args, **_kwargs):
            raise AssertionError("evidence flow must never download video or audio")

    return FakeYdl


def _srt(text: str = "第一条字幕", *, start: str = "00:00:01,000", end: str = "00:00:03,500") -> str:
    return f"1\n{start} --> {end}\n{text}\n"


def _track(
    payload: str | bytes | None,
    *,
    extension: str = "srt",
    language: str = "zh-Hans",
    automatic: bool = False,
    url: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"ext": extension}
    if payload is not None:
        entry["data"] = payload
    if url is not None:
        entry["url"] = url
    field = "automatic_captions" if automatic else "subtitles"
    return {field: {language: [entry]}}


def _single_info(track: dict[str, Any], *, title: str = "课程", cid: int = 100) -> dict[str, Any]:
    return {
        "id": "BV1ib411t7YR",
        "title": title,
        "cid": cid,
        "duration": 120,
        **track,
    }


def _run(reference: str = "BV1ib411t7YR", **kwargs):
    return asyncio.run(fetch_bilibili_transcript_evidence(reference, **kwargs))


def _public_url_track(
    url: str,
    *,
    http_headers: tuple[tuple[str, str], ...] = (),
) -> BilibiliSubtitleTrack:
    return BilibiliSubtitleTrack(
        language="zh-Hans",
        kind=SubtitleKind.MANUAL,
        extension="srt",
        name=None,
        data=None,
        url=url,
        http_headers=http_headers,
        source_field="subtitles",
    )


def test_manual_chinese_inline_payload_builds_timeline_and_serializes_original_text():
    original_text = "术语  保留\n第二行"
    bundle = _run(
        ydl_factory=_factory(_single_info(_track(_srt(original_text)))),
        fetched_at="2026-08-24T12:00:00+00:00",
    )

    assert bundle.part_count == 1
    assert bundle.subtitle_language == "zh-Hans"
    assert bundle.subtitle_kind == "manual"
    assert bundle.segments[0].start_ms == 1000
    assert bundle.segments[0].end_ms == 3500
    assert bundle.segments[0].original_text == original_text
    assert bundle.parts[0].selected_track.source == "data"
    serialized = json.loads(bundle.to_json())
    assert serialized["segments"][0]["original_text"] == original_text
    assert serialized["segments"][0]["stable_segment_id"] == bundle.segments[0].segment_id


def test_ai_chinese_vtt_builds_automatic_timeline():
    payload = "WEBVTT\n\n00:00:02.000 --> 00:00:04.250\n自动字幕正文\n"
    bundle = _run(
        ydl_factory=_factory(_single_info(_track(payload, extension="vtt", language="ai-zh", automatic=True)))
    )

    assert bundle.subtitle_kind == "automatic"
    assert bundle.subtitle_language == "ai-zh"
    assert [(item.start_ms, item.end_ms, item.original_text) for item in bundle.segments] == [
        (2000, 4250, "自动字幕正文")
    ]


def test_deterministic_selection_prefers_manual_chinese_over_ai_chinese_and_preferred_other():
    info = _single_info(
        {
            "subtitles": {
                "en": [{"ext": "srt", "data": _srt("English")}],
                "zh-Hans": [{"ext": "srt", "data": _srt("人工中文")}],
            },
            "automatic_captions": {
                "ai-zh": [{"ext": "srt", "data": _srt("自动中文")}],
            },
        }
    )
    bundle = _run(ydl_factory=_factory(info), preferred_languages=("en",))

    assert bundle.parts[0].selected_track.language == "zh-Hans"
    assert bundle.parts[0].selected_track.kind == "manual"
    assert bundle.segments[0].original_text == "人工中文"


def test_srt_parser_preserves_each_timestamped_segment():
    payload = (
        "1\n00:00:00,000 --> 00:00:01,500\n第一段\n\n"
        "2\n00:01:02,250 --> 00:01:04,000\n第二段\n"
    )
    parsed = parse_subtitle_timeline(payload, "srt")

    assert [(cue.start_ms, cue.end_ms, cue.original_text) for cue in parsed.cues] == [
        (0, 1500, "第一段"),
        (62_250, 64_000, "第二段"),
    ]


def test_vtt_parser_accepts_minute_clock_without_losing_time():
    parsed = parse_subtitle_timeline(
        "WEBVTT\n\n02:03.125 --> 02:05.500 align:start\nVTT 正文\n",
        "vtt",
    )
    assert parsed.cues[0].start_ms == 123_125
    assert parsed.cues[0].end_ms == 125_500


def test_json_bcc_parser_builds_segments():
    payload = json.dumps(
        {
            "body": [
                {"from": 0.25, "to": 1.75, "content": "BCC 第一段"},
                {"from": 2, "to": 3.5, "content": "BCC 第二段"},
            ]
        },
        ensure_ascii=False,
    )
    parsed = parse_subtitle_timeline(payload, "json")

    assert [(cue.start_ms, cue.end_ms, cue.original_text) for cue in parsed.cues] == [
        (250, 1750, "BCC 第一段"),
        (2000, 3500, "BCC 第二段"),
    ]


def test_json3_parser_builds_segments_without_converting_to_srt():
    payload = json.dumps(
        {
            "events": [
                {"tStartMs": 500, "dDurationMs": 1000, "segs": [{"utf8": "JSON"}, {"utf8": "3"}]}
            ]
        }
    )
    parsed = parse_subtitle_timeline(payload, "json3")
    assert parsed.cues[0] == parsed.cues[0].__class__(500, 1500, "JSON3")


def test_multipart_keeps_part_cid_and_local_timeline_boundaries():
    info = {
        "_type": "playlist",
        "id": "BV1ib411t7YR",
        "title": "多 P 课程",
        "entries": [
            {"playlist_index": 1, "part": "P1", "cid": 101, "duration": 10, **_track(_srt("P1"))},
            {"playlist_index": 2, "part": "P2", "cid": 202, "duration": 20, **_track(_srt("P2"))},
        ],
    }
    bundle = _run(ydl_factory=_factory(info))

    assert bundle.part_count == 2
    assert [(part.part_index, part.cid, part.title) for part in bundle.parts] == [
        (1, "101", "P1"),
        (2, "202", "P2"),
    ]
    assert [(segment.part_index, segment.part_cid, segment.start_ms) for segment in bundle.segments] == [
        (1, "101", 1000),
        (2, "202", 1000),
    ]


def test_same_payload_has_stable_bundle_hash_and_segment_ids_even_when_fetch_time_changes():
    factory = _factory(_single_info(_track(_srt("稳定正文"))))
    first = _run(ydl_factory=factory, fetched_at="2026-08-24T00:00:00+00:00")
    second = _run(ydl_factory=factory, fetched_at="2026-08-25T00:00:00+00:00")

    assert first.fetched_at != second.fetched_at
    assert first.bundle_hash == second.bundle_hash
    assert first.bundle_id == second.bundle_id
    assert first.source_revision == second.source_revision
    assert first.raw_payload_hash == second.raw_payload_hash
    assert [item.stable_segment_id for item in first.segments] == [
        item.stable_segment_id for item in second.segments
    ]


def test_one_character_change_changes_payload_timeline_bundle_and_segment_hashes():
    first = _run(ydl_factory=_factory(_single_info(_track(_srt("字幕A")))))
    second = _run(ydl_factory=_factory(_single_info(_track(_srt("字幕B")))))

    assert first.raw_payload_hash != second.raw_payload_hash
    assert first.normalized_timeline_hash != second.normalized_timeline_hash
    assert first.bundle_hash != second.bundle_hash
    assert first.segments[0].segment_hash != second.segments[0].segment_hash
    assert first.segments[0].stable_segment_id != second.segments[0].stable_segment_id


def test_empty_subtitle_returns_stable_error():
    with pytest.raises(TranscriptEvidenceError) as captured:
        _run(ydl_factory=_factory(_single_info(_track(" \n\t"))))

    assert captured.value.code == "SUBTITLE_EMPTY"
    assert captured.value.part_index == 1


def test_invalid_timestamps_never_create_false_evidence_and_partial_invalid_cues_warn():
    mixed = (
        "1\n00:00:05,000 --> 00:00:04,000\n倒序\n\n"
        "2\n00:00:06,000 --> 00:00:07,000\n有效\n"
    )
    bundle = _run(ydl_factory=_factory(_single_info(_track(mixed))))
    assert [(item.start_ms, item.end_ms, item.original_text) for item in bundle.segments] == [
        (6000, 7000, "有效")
    ]
    assert bundle.warnings == ("part:1:rejected_invalid_cues:1",)

    with pytest.raises(TranscriptEvidenceError) as captured:
        _run(ydl_factory=_factory(_single_info(_track(_srt("错误", start="NaN", end="00:00:02,000")))))
    assert captured.value.code == "SUBTITLE_PARSE_ERROR"
    assert captured.value.part_index == 1


def test_url_payload_is_fetched_but_timeout_has_stable_fetch_error():
    url_info = _single_info(_track(None, url="https://8.8.8.8/subtitle.srt"))

    async def successful_fetch(track: BilibiliSubtitleTrack):
        assert track.url == "https://8.8.8.8/subtitle.srt"
        return _srt("URL 正文")

    bundle = _run(ydl_factory=_factory(url_info), payload_fetcher=successful_fetch)
    assert bundle.parts[0].selected_track.source == "url"
    assert bundle.segments[0].original_text == "URL 正文"

    async def timeout_fetch(_track: BilibiliSubtitleTrack):
        raise TimeoutError("simulated timeout")

    with pytest.raises(TranscriptEvidenceError) as captured:
        _run(ydl_factory=_factory(url_info), payload_fetcher=timeout_fetch)
    assert captured.value.code == "SUBTITLE_FETCH_ERROR"
    assert captured.value.part_index == 1
    assert "simulated" not in captured.value.message

    async def malformed_fetch(_track: BilibiliSubtitleTrack):
        return 100

    with pytest.raises(TranscriptEvidenceError) as malformed:
        _run(ydl_factory=_factory(url_info), payload_fetcher=malformed_fetch)
    assert malformed.value.code == "SUBTITLE_FETCH_ERROR"
    assert malformed.value.__cause__ is None


def test_empty_inline_data_falls_back_to_track_url():
    url_info = _single_info(_track("", url="https://8.8.8.8/subtitle.srt"))

    async def successful_fetch(_track: BilibiliSubtitleTrack):
        return _srt("URL 回退正文")

    bundle = _run(ydl_factory=_factory(url_info), payload_fetcher=successful_fetch)
    assert bundle.parts[0].selected_track.source == "url"
    assert bundle.segments[0].original_text == "URL 回退正文"


def test_default_url_fetch_blocks_private_targets_and_private_redirects():
    with pytest.raises(TranscriptEvidenceError) as private_error:
        asyncio.run(fetch_subtitle_url_payload(_public_url_track("http://127.0.0.1/subtitle.srt")))
    assert private_error.value.code == "SUBTITLE_FETCH_ERROR"

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(302, headers={"location": "http://127.0.0.1/private.srt"})
    )

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    with pytest.raises(TranscriptEvidenceError) as redirect_error:
        asyncio.run(
            fetch_subtitle_url_payload(
                _public_url_track("https://8.8.8.8/subtitle.srt"),
                client_factory=client_factory,
            )
        )
    assert redirect_error.value.code == "SUBTITLE_FETCH_ERROR"


def test_default_url_fetch_requires_https_and_positive_subtitle_mime():
    with pytest.raises(TranscriptEvidenceError) as http_error:
        asyncio.run(fetch_subtitle_url_payload(_public_url_track("http://8.8.8.8/subtitle.srt")))
    assert http_error.value.code == "SUBTITLE_FETCH_ERROR"

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=_srt("伪装响应").encode(),
        )
    )

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    with pytest.raises(TranscriptEvidenceError) as mime_error:
        asyncio.run(
            fetch_subtitle_url_payload(
                _public_url_track("https://8.8.8.8/subtitle.srt"),
                client_factory=client_factory,
            )
        )
    assert mime_error.value.code == "SUBTITLE_FETCH_ERROR"


def test_cross_origin_redirect_drops_origin_and_referer_headers():
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "8.8.8.8":
            return httpx.Response(302, headers={"location": "https://1.1.1.1/subtitle.srt"})
        return httpx.Response(
            200,
            headers={"content-type": " text/plain; charset=utf-8"},
            content=_srt("安全重定向").encode(),
        )

    transport = httpx.MockTransport(handle)

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    payload = asyncio.run(
        fetch_subtitle_url_payload(
            _public_url_track(
                "https://8.8.8.8/subtitle.srt",
                http_headers=(
                    ("Origin", "https://www.bilibili.com"),
                    ("Referer", "https://www.bilibili.com/video/BV1ib411t7YR/?token=secret"),
                    ("Accept-Language", "zh-CN"),
                ),
            ),
            client_factory=client_factory,
        )
    )

    assert payload == _srt("安全重定向").encode()
    assert requests[0].headers["origin"] == "https://www.bilibili.com"
    assert "origin" not in requests[1].headers
    assert "referer" not in requests[1].headers
    assert requests[1].headers["accept-language"] == "zh-CN"


def test_default_url_fetch_bounds_payload_size():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "text/plain", "content-length": str(MAX_SUBTITLE_BYTES + 1)},
            content=b"x",
        )
    )

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    with pytest.raises(TranscriptEvidenceError) as captured:
        asyncio.run(
            fetch_subtitle_url_payload(
                _public_url_track("https://8.8.8.8/subtitle.srt"),
                client_factory=client_factory,
            )
        )
    assert captured.value.code == "SUBTITLE_FETCH_ERROR"


def test_evidence_reuses_single_preflight_and_never_requests_media_download():
    calls: list[tuple[str, bool]] = []

    def inspect_options(options: dict[str, Any]):
        assert options["skip_download"] is True
        assert options["listsubtitles"] is True
        assert options["cachedir"] is False
        assert "writesubtitles" not in options
        assert "writeautomaticsub" not in options
        assert "format" not in options
        assert "outtmpl" not in options
        assert "postprocessors" not in options

    bundle = _run(
        ydl_factory=_factory(
            _single_info(_track(_srt("单次预检"))),
            inspect_options=inspect_options,
            calls=calls,
        )
    )
    assert bundle.segments
    assert calls == [("https://www.bilibili.com/video/BV1ib411t7YR/", False)]


def _clock(milliseconds: int, *, comma: bool = True) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    separator = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def _srt_cues(count: int, *, prefix: str, step_ms: int) -> str:
    return "\n\n".join(
        f"{index + 1}\n{_clock(index * step_ms)} --> {_clock((index + 1) * step_ms)}\n{prefix}-{index + 1}"
        for index in range(count)
    )


def test_ten_hour_fixture_preserves_36000_dense_cues_without_truncation():
    payload = _srt_cues(36_000, prefix="10h", step_ms=1000)
    info = {**_single_info(_track(payload), title="10 小时课程"), "duration": 36_000}
    factory = _factory(info)

    first = _run(ydl_factory=factory, fetched_at="2026-08-24T00:00:00+00:00")
    second = _run(ydl_factory=factory, fetched_at="2026-08-25T00:00:00+00:00")

    assert first.parts[0].duration_seconds == 36_000
    assert len(first.segments) == len(first.parts[0].segment_ids) == 36_000
    assert (first.segments[0].start_ms, first.segments[0].end_ms) == (0, 1000)
    assert (first.segments[-1].start_ms, first.segments[-1].end_ms) == (35_999_000, 36_000_000)
    assert first.segments[-1].original_text == "10h-36000"
    assert len({segment.stable_segment_id for segment in first.segments}) == 36_000
    assert first.bundle_hash == second.bundle_hash
    assert first.raw_payload_hash == second.raw_payload_hash
    assert [segment.stable_segment_id for segment in first.segments] == [
        segment.stable_segment_id for segment in second.segments
    ]


def test_parser_enforces_segment_safety_limit_without_duration_rejection():
    too_many = json.dumps(
        {"body": [{"from": 0, "to": 1, "content": "x"}] * (MAX_SUBTITLE_SEGMENTS + 1)}
    )
    with pytest.raises(TranscriptEvidenceError) as count_error:
        parse_subtitle_timeline(too_many, "json")
    assert count_error.value.code == "SUBTITLE_PARSE_ERROR"

    ten_hours = json.dumps({"body": [{"from": 35_999, "to": 36_000, "content": "10h"}]})
    parsed = parse_subtitle_timeline(ten_hours, "json")
    assert (parsed.cues[0].start_ms, parsed.cues[0].end_ms) == (35_999_000, 36_000_000)


def test_1000_part_100k_segment_fixture_preserves_full_evidence_contract():
    entries = [
        {
            "playlist_index": index,
            "part": f"P{index}",
            "cid": 10_000 + index,
            "duration": 600,
            **_track(_srt_cues(100, prefix=f"P{index}", step_ms=6000)),
        }
        for index in range(1, 1001)
    ]
    bundle = _run(
        ydl_factory=_factory(
            {"_type": "playlist", "id": "BV1ib411t7YR", "title": "1000P", "entries": entries}
        )
    )

    assert bundle.part_count == 1000
    assert len(bundle.parts) == 1000
    assert len(bundle.segments) == 100_000
    assert bundle.parts[-1].cid == "11000"
    assert bundle.segments[-1].part_index == 1000
    assert (bundle.segments[-1].start_ms, bundle.segments[-1].end_ms) == (594_000, 600_000)
    assert bundle.segments[-1].original_text == "P1000-100"
    assert all(len(part.segment_ids) == 100 for part in bundle.parts)
    assert all(
        bundle.segments[offset].part_index == part_index
        and bundle.segments[offset].start_ms == 0
        and bundle.segments[offset + 99].part_index == part_index
        and bundle.segments[offset + 99].end_ms == 600_000
        for part_index, offset in enumerate(range(0, 100_000, 100), start=1)
    )
    assert len({segment.stable_segment_id for segment in bundle.segments}) == 100_000
    assert all(part.raw_payload_hash and part.subtitle_identity for part in bundle.parts)
    assert all(segment.segment_hash and segment.source_reference for segment in bundle.segments)
    assert bundle.raw_payload_hash and bundle.normalized_timeline_hash and bundle.bundle_hash

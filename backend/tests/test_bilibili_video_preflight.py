from __future__ import annotations

from typing import Any, Callable

from app.sources.video.bilibili_preflight import (
    SubtitleKind,
    VideoPreflightStatus,
    normalize_bilibili_reference,
    preflight_bilibili_video,
)


class _FakeYdl:
    result: dict[str, Any] | None = None
    error: Exception | None = None
    warning: str | None = None
    inspect_options: Callable[[dict[str, Any]], None] | None = None

    def __init__(self, options: dict[str, Any]):
        self.options = options
        if self.inspect_options:
            self.inspect_options(options)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, _url: str, download: bool = True):
        assert download is False
        if self.warning:
            self.options["logger"].warning(self.warning)
        if self.error:
            raise self.error
        return self.result

    def download(self, *_args, **_kwargs):
        raise AssertionError("preflight must never call a media download method")


def _factory(
    result: dict[str, Any] | None = None,
    *,
    error: Exception | None = None,
    warning: str | None = None,
    inspect_options: Callable[[dict[str, Any]], None] | None = None,
):
    class Fake(_FakeYdl):
        pass

    Fake.result = result
    Fake.error = error
    Fake.warning = warning
    Fake.inspect_options = staticmethod(inspect_options) if inspect_options else None
    return Fake


def _track(*, automatic: bool = False, language: str = "zh-Hans") -> dict[str, Any]:
    field = "automatic_captions" if automatic else "subtitles"
    return {field: {language: [{"ext": "srt", "data": "1\n00:00:00,000 --> 00:00:02,000\n字幕\n"}]}}


def test_single_part_ai_subtitle_is_supported():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            {
                "id": "BV1ib411t7YR",
                "title": "AI 字幕课程",
                "duration": 95.5,
                **_track(automatic=True, language="ai-zh"),
            }
        ),
    )

    assert result.status is VideoPreflightStatus.SUPPORTED
    assert result.subtitle_available is True
    assert result.subtitle_kind is SubtitleKind.AUTOMATIC
    assert result.subtitle_language == "ai-zh"
    assert result.part_count == 1
    assert result.total_duration_seconds == 95.5


def test_single_part_manual_subtitle_and_url_normalization_are_supported():
    reference = "https://m.bilibili.com/video/BV1ib411t7YR/?p=2&spm_id_from=test#reply"
    normalized = normalize_bilibili_reference(reference)
    result = preflight_bilibili_video(
        reference,
        ydl_factory=_factory(
            {
                "id": "BV1ib411t7YR_p1",
                "title": "人工字幕课程",
                "duration": 120,
                **_track(),
            }
        ),
    )

    assert normalized.bvid == "BV1ib411t7YR"
    assert normalized.canonical_url == "https://www.bilibili.com/video/BV1ib411t7YR/"
    assert result.status is VideoPreflightStatus.SUPPORTED
    assert result.subtitle_kind is SubtitleKind.MANUAL


def test_danmaku_only_is_unsupported_no_subtitle():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            {
                "id": "BV1ib411t7YR",
                "title": "只有弹幕",
                "duration": 60,
                "subtitles": {
                    "danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/1.xml"}]
                },
            }
        ),
    )

    assert result.status is VideoPreflightStatus.UNSUPPORTED_NO_SUBTITLE
    assert result.subtitle_available is False
    assert result.login_required is False


def test_subtitle_login_warning_is_not_misclassified_as_no_subtitle():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            {
                "id": "BV1ib411t7YR",
                "title": "登录后字幕",
                "duration": 60,
                "subtitles": {
                    "danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/1.xml"}]
                },
            },
            warning="Subtitles are only available when logged in. Use cookies for authentication.",
        ),
    )

    assert result.status is VideoPreflightStatus.SUBTITLE_LOGIN_REQUIRED
    assert result.login_required is True
    assert result.error_code == "BILIBILI_SUBTITLE_LOGIN_REQUIRED"


def test_large_1000_part_course_is_supported_and_aggregated():
    entries = [
        {
            "id": f"BV1ib411t7YR_p{index}",
            "playlist_index": index,
            "part": f"P{index}",
            "cid": 100_000 + index,
            "duration": index,
            **_track(automatic=index % 2 == 0),
        }
        for index in range(1, 1001)
    ]
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            {
                "_type": "playlist",
                "id": "BV1ib411t7YR",
                "title": "1000P 课程",
                "entries": entries,
            }
        ),
    )

    assert result.status is VideoPreflightStatus.SUPPORTED
    assert result.part_count == 1000
    assert result.total_duration_seconds == 500_500
    assert result.subtitle_kind is SubtitleKind.MIXED
    assert result.parts[-1].cid == "101000"


def test_one_missing_part_makes_the_course_unsupported():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            {
                "_type": "playlist",
                "title": "不完整课程",
                "entries": [
                    {"part": "P1", "duration": 10, **_track()},
                    {"part": "P2", "duration": 20, "subtitles": {}},
                ],
            }
        ),
    )

    assert result.status is VideoPreflightStatus.UNSUPPORTED_NO_SUBTITLE
    assert result.part_count == 2
    assert result.subtitle_available is False


def test_course_larger_than_1000_parts_fails_closed_instead_of_truncating():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            {
                "_type": "playlist",
                "title": "1001P 课程",
                "playlist_count": 1001,
                "entries": [{"part": f"P{index}", **_track()} for index in range(1, 1001)],
            }
        ),
    )

    assert result.status is VideoPreflightStatus.FETCH_ERROR
    assert result.part_count == 0
    assert "1000-part preflight limit" in (result.error_message or "")


def test_invalid_bv_is_rejected_without_constructing_yt_dlp():
    def fail_if_called(_options):
        raise AssertionError("invalid references must fail before yt-dlp")

    result = preflight_bilibili_video(
        "BV1234",
        ydl_factory=fail_if_called,
    )

    assert result.status is VideoPreflightStatus.INVALID_REFERENCE
    assert result.error_code == "INVALID_BILIBILI_REFERENCE"


def test_short_link_is_rejected_until_a_bv_can_be_validated_without_redirects():
    result = preflight_bilibili_video(
        "https://b23.tv/AbCdE",
        ydl_factory=lambda _options: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )

    assert result.status is VideoPreflightStatus.INVALID_REFERENCE


def test_yt_dlp_network_error_becomes_fetch_error():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(error=RuntimeError("HTTP Error 412: Precondition Failed")),
    )

    assert result.status is VideoPreflightStatus.FETCH_ERROR
    assert result.error_code == "BILIBILI_FETCH_ERROR"
    assert "HTTP Error 412" in (result.error_message or "")


def test_fetch_error_redacts_urls_and_credentials():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            error=RuntimeError("failed https://api.example/path?token=secret cookie=private")
        ),
    )

    assert result.status is VideoPreflightStatus.FETCH_ERROR
    assert "secret" not in (result.error_message or "")
    assert "private" not in (result.error_message or "")
    assert "https://" not in (result.error_message or "")


def test_malformed_multipart_entry_fails_closed():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            {
                "_type": "playlist",
                "entries": [{"part": "P1", **_track()}, None],
            }
        ),
    )

    assert result.status is VideoPreflightStatus.FETCH_ERROR
    assert result.part_count == 0


def test_non_text_track_is_not_accepted_as_a_subtitle():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            {
                "id": "BV1ib411t7YR",
                "subtitles": {"zh-Hans": [{"ext": "m3u8", "url": "https://media.test/video.m3u8"}]},
            }
        ),
    )

    assert result.status is VideoPreflightStatus.UNSUPPORTED_NO_SUBTITLE


def test_unrelated_cookie_error_is_fetch_error_not_subtitle_login_required():
    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(error=RuntimeError("subtitle metadata proxy rejected cookie header")),
    )

    assert result.status is VideoPreflightStatus.FETCH_ERROR


def test_preflight_options_and_call_path_forbid_video_or_audio_downloads():
    def inspect_options(options: dict[str, Any]) -> None:
        assert options["skip_download"] is True
        assert options["noplaylist"] is False
        assert options["extract_flat"] is False
        assert options["playlistend"] == 1001
        assert options["cachedir"] is False
        assert options["listsubtitles"] is True
        assert "writesubtitles" not in options
        assert "writeautomaticsub" not in options
        assert "format" not in options
        assert "outtmpl" not in options
        assert "postprocessors" not in options

    result = preflight_bilibili_video(
        "BV1ib411t7YR",
        ydl_factory=_factory(
            {"id": "BV1ib411t7YR", "duration": 1, **_track()},
            inspect_options=inspect_options,
        ),
    )

    assert result.status is VideoPreflightStatus.SUPPORTED
    assert result.to_dict()["parts"][0]["subtitle_available"] is True

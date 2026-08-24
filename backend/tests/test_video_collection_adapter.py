from __future__ import annotations

import io

import pytest

from app.video_transcript import (
    VideoTranscriptError,
    _enumerate_collection_page_sync,
    _extract_episode_subtitle_sync,
)


def test_collection_page_is_flat_bounded_and_never_downloads_video():
    observed: dict[str, object] = {}

    class FakeYdl:
        def __init__(self, options):
            observed["options"] = dict(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            observed["url"] = url
            observed["download"] = download
            return {
                "id": "PL-fixture",
                "title": "离线长课",
                "webpage_url": url,
                "playlist_count": 1000,
                "entries": [
                    {
                        "id": f"v{index}",
                        "title": f"第 {index + 1} 集",
                        "url": f"https://www.bilibili.com/video/av{index + 1}",
                        "duration": 60,
                    }
                    for index in range(51)
                ],
            }

    page = _enumerate_collection_page_sync(
        "https://space.bilibili.com/100/lists/200?type=season",
        offset=0,
        page_size=50,
        ydl_factory=FakeYdl,
    )
    options = observed["options"]
    assert observed["download"] is False
    assert options["skip_download"] is True
    assert options["extract_flat"] == "in_playlist"
    assert options["lazy_playlist"] is True
    assert options["playlist_items"] == "1:51"
    assert "writesubtitles" not in options
    assert page["video_downloaded"] is False
    assert page["provider"] == "bilibili"
    assert len(page["items"]) == 50
    assert page["has_more"] is True
    assert page["next_offset"] == 50


def test_collection_reported_over_limit_is_explicit_not_silently_truncated():
    class FakeYdl:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {"playlist_count": 1001, "entries": []}

    with pytest.raises(VideoTranscriptError) as error:
        _enumerate_collection_page_sync(
            "https://space.bilibili.com/100/lists/201?type=season",
            ydl_factory=FakeYdl,
        )
    assert error.value.code == "COLLECTION_EPISODE_LIMIT"


def test_episode_subtitle_rejects_ass_and_keeps_timestamped_vtt_only():
    vtt = (
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
        + ("矩阵表示线性变换。" * 10)
        + "\n\n00:00:02.000 --> 00:00:04.000\n"
        + ("基向量决定坐标表示。" * 10)
    ).encode("utf-8")
    requested: list[str] = []

    class FakeYdl:
        def __init__(self, options):
            self.options = dict(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            assert download is False
            assert self.options["skip_download"] is True
            assert self.options["noplaylist"] is True
            return {
                "id": "episode-1",
                "title": "第一集",
                "webpage_url": url,
                "duration": 4,
                "subtitles": {
                    "zh": [
                        {"url": "https://captions.example/unsafe.ass", "ext": "ass"},
                        {"url": "https://captions.example/safe.vtt", "ext": "vtt"},
                    ]
                },
            }

        def urlopen(self, url):
            requested.append(url)
            return io.BytesIO(vtt)

    transcript = _extract_episode_subtitle_sync(
        "https://www.bilibili.com/video/BVfixture1",
        ydl_factory=FakeYdl,
    )
    assert requested == ["https://captions.example/safe.vtt"]
    assert transcript["video_downloaded"] is False
    assert transcript["track"]["format"] == "vtt"
    assert transcript["quality"]["state"] == "ready"
    assert transcript["segments"][0]["start_ms"] == 0


def test_long_course_rejects_local_or_credential_bearing_network_targets():
    from app.video_transcript import classify_video_url

    assert classify_video_url("https://127.0.0.1/private.mp4")["recognized"] is False
    assert classify_video_url("https://localhost/private.mp4")["recognized"] is False
    assert classify_video_url("https://user:secret@example.com/video.mp4")["recognized"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://space.bilibili.com/100/lists/200?type=season",
        "https://space.bilibili.com/100/channel/seriesdetail?sid=200",
        "https://www.bilibili.com/medialist/play/100?business=space_series&business_id=200",
        "https://www.bilibili.com/bangumi/play/ss200",
        "https://www.bilibili.com/cheese/play/ss200",
    ],
)
def test_bilibili_collection_routes_are_classified_for_long_course_flow(url):
    from app.video_transcript import classify_video_url

    capability = classify_video_url(url)
    assert capability["recognized"] is True
    assert capability["provider"] == "bilibili"
    assert capability["kind"] == "video_collection"
    assert capability["is_collection"] is True

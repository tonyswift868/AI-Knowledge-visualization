from __future__ import annotations

import io

import pytest

from app import active_teaching
from app.video_transcript import SubtitleSegment, _extract_sync, _subtitle_entries, assess_subtitle, classify_video_url, parse_subtitle_payload, source_family_id


def _transcript_payload(url: str) -> dict:
    text = "字幕内容覆盖定义、步骤和例题。" * 8
    segments = [
        {"start_ms": 0, "end_ms": 5000, "text": text[: len(text) // 2]},
        {"start_ms": 5000, "end_ms": 10000, "text": text[len(text) // 2 :]},
    ]
    return {
        "schema": "mentorflow.video-transcript-preview.v1",
        "source_url": url,
        "provider": "bilibili",
        "title": "Generic lecture",
        "subtitle": {
            "format": "vtt",
            "language": "zh-Hans",
            "automatic": False,
            "segments": segments,
            "text": text,
            "quality": {
                "state": "ready",
                "reason": "subtitle_verified",
                "language": "zh-Hans",
                "automatic": False,
                "segment_count": 2,
                "character_count": len(text),
                "timestamped": True,
            },
        },
        "disposition": {"state": "ready", "reason": "subtitle_verified"},
    }


def test_bilibili_is_classified_as_video_capability():
    capability = classify_video_url("https://www.bilibili.com/video/BV1xx411c7mD")
    assert capability["recognized"] is True
    assert capability["kind"] == "video"
    assert capability["capabilities"]["subtitle_extraction"] is True


def test_non_video_bilibili_page_is_not_misclassified_as_extractable_video():
    capability = classify_video_url("https://space.bilibili.com/12345")
    assert capability["recognized"] is False
    assert capability["kind"] == "webpage"


def test_youtube_playlist_is_classified_as_video_capability():
    capability = classify_video_url("https://www.youtube.com/playlist?list=PL123")
    assert capability["recognized"] is True
    assert capability["provider"] == "youtube"
    assert capability["capabilities"]["subtitle_extraction"] is True


def test_collection_transcript_aggregates_episode_timestamps():
    subtitle = (
        "WEBVTT\n\n00:00:00.000 --> 00:00:03.000\n"
        + ("一段足够长的课程字幕。" * 8)
    ).encode("utf-8")

    class _FakeYdl:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "title": "完整课程",
                "webpage_url": "https://www.youtube.com/playlist?list=PL123",
                "entries": [
                    {"title": "第一集", "webpage_url": "https://youtu.be/one", "duration": 3, "subtitles": {"zh": [{"url": "https://captions.test/one.vtt", "ext": "vtt"}]}},
                    {"title": "第二集", "webpage_url": "https://youtu.be/two", "duration": 3, "subtitles": {"zh": [{"url": "https://captions.test/two.vtt", "ext": "vtt"}]}},
                ],
            }

        def urlopen(self, _url):
            return io.BytesIO(subtitle)

    result = _extract_sync(
        "https://www.youtube.com/playlist?list=PL123",
        ydl_factory=_FakeYdl,
    )
    assert result["collection"]["is_collection"] is True
    assert result["collection"]["episode_count"] == 2
    assert result["subtitle"]["segments"][1]["start_ms"] >= 3000
    assert result["disposition"]["state"] == "ready"


def test_subtitle_parser_keeps_timestamps_and_quality_state():
    payload = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n" + ("A useful sentence. " * 6) + "\n\n00:00:03.000 --> 00:00:05.000\nSecond segment"
    segments = parse_subtitle_payload(payload, ".vtt")
    quality = assess_subtitle(segments, language="en")
    assert segments[0].start_ms == 1000
    assert segments[0].end_ms == 3000
    assert quality["state"] == "ready"
    assert quality["timestamped"] is True


def test_bilibili_danmaku_track_is_never_used_as_course_subtitle():
    entries = _subtitle_entries({
        "subtitles": {
            "danmaku": [{"url": "https://example.test/comments.xml", "ext": "xml"}],
            "zh-Hans": [{"url": "https://example.test/subtitle.json", "ext": "json"}],
        }
    })
    assert [(language, item["ext"]) for language, _automatic, item in entries] == [
        ("zh-Hans", "json"),
    ]


def test_canonical_video_url_material_is_disabled_before_fetch_or_extraction(client, monkeypatch):
    extraction_called = False
    fetch_called = False

    async def fail_extract(*args, **kwargs):
        nonlocal extraction_called
        extraction_called = True
        raise AssertionError("canonical material route must reject before subtitle extraction")

    async def fail_fetch(*args, **kwargs):
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("canonical material route must reject before media fetch")

    monkeypatch.setattr(active_teaching, "extract_video_transcript", fail_extract)
    monkeypatch.setattr(active_teaching, "fetch_safe", fail_fetch)
    workspace_id = "video-choice"
    target_dir = active_teaching.UPLOAD_ROOT / workspace_id
    before_files = (
        {str(item.relative_to(target_dir)) for item in target_dir.rglob("*")}
        if target_dir.exists()
        else set()
    )
    with active_teaching._connect() as connection:
        before_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM active_materials WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
        )
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/materials/url",
        json={"url": "https://www.bilibili.com/video/BV1xx411c7mD"},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "VIDEO_MATERIAL_DISABLED"
    with active_teaching._connect() as connection:
        after_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM active_materials WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
        )
    after_files = (
        {str(item.relative_to(target_dir)) for item in target_dir.rglob("*")}
        if target_dir.exists()
        else set()
    )
    assert after_rows == before_rows
    assert after_files == before_files
    assert extraction_called is False
    assert fetch_called is False


@pytest.mark.parametrize(
    ("source_kind", "media_type", "filename"),
    [
        ("audio", "audio/wav", "lesson.wav"),
        ("audio_transcript", "text/plain", "audio-transcript.txt"),
        ("subtitle", "text/vtt", "lesson.vtt"),
        ("caption", "application/x-subrip", "lesson.srt"),
        ("video", "video/mp4", "lesson.mp4"),
        ("video_transcript", "text/plain", "video-transcript.txt"),
        ("video_collection", "text/plain", "collection.txt"),
    ],
)
def test_media_materials_are_excluded_from_canonical_planning_and_map_handoff(
    source_kind,
    media_type,
    filename,
):
    assert active_teaching._planning_material_is_blocked(
        {
            "source_kind": source_kind,
            "media_type": media_type,
            "original_filename": filename,
        }
    ) is True


def test_document_material_remains_eligible_for_canonical_handoff():
    assert active_teaching._planning_material_is_blocked(
        {
            "source_kind": "uploaded_file",
            "media_type": "application/pdf",
            "original_filename": "notes.pdf",
        }
    ) is False


def test_accepted_search_returns_candidates_without_ingesting(client, monkeypatch):
    async def fake_search(query, **kwargs):
        return {
            "schema": "mentorflow.video-supplementary-search.v1",
            "query": query,
            "authorized": True,
            "ingested": False,
            "candidates": [
                {
                    "id": "supplement-test",
                    "url": "https://notes.example/lecture",
                    "title": "Lecture notes",
                    "snippet": "notes",
                    "selected": False,
                    "ingested": False,
                }
            ],
        }

    monkeypatch.setattr(active_teaching, "search_supplementary_materials", fake_search)
    response = client.post(
        "/api/active-teaching/workspaces/video-search/materials/video/search",
        json={
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "find_additional_materials": True,
            "query": "generic lecture",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "supplementary_candidates"
    assert body["selection_required"] is True
    assert body["supplementary_search"]["ingested"] is False


def test_confirmed_transcript_and_selected_supplement_are_separate_materials(client, monkeypatch):
    video_url = "https://www.bilibili.com/video/BV1xx411c7mD"

    async def fake_extract(url, **kwargs):
        return _transcript_payload(url)

    async def fake_fetch(url):
        return {
            "text": "独立讲义内容。" * 20,
            "title": "Lecture notes",
            "final_url": url,
        }

    monkeypatch.setattr(active_teaching, "extract_video_transcript", fake_extract)
    monkeypatch.setattr(active_teaching, "fetch_safe", fake_fetch)
    response = client.post(
        "/api/active-teaching/workspaces/video-ingest/materials/video/ingest",
        json={
            "url": video_url,
            "find_additional_materials": True,
            "confirm_transcript": True,
            "selected_material_urls": ["https://notes.example/lecture"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ingested"
    assert body["separate_evidence"] is True
    assert body["transcript_material"]["source_kind"] == "video_transcript"
    assert body["supplementary_materials"][0]["source_kind"] == "supplementary_material"
    assert body["material_ids"][0] != body["material_ids"][1]


def test_content_dedup_appends_url_alias_without_overwriting_original_source(client, monkeypatch):
    workspace_id = "source-alias-dedup"
    text = "同一份正文应保留最初来源，并把后来网址追加为别名。" * 8
    uploaded = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/materials",
        files={"file": ("local-notes.txt", text.encode("utf-8"), "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text

    async def fake_fetch(url):
        return {"text": text, "title": "Same notes online", "final_url": url}

    monkeypatch.setattr(active_teaching, "fetch_safe", fake_fetch)
    imported = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/materials/url",
        json={"url": "https://notes.example/same"},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["id"] == uploaded.json()["id"]
    assert body["source_kind"] != "web_article"
    aliases = body["provenance"]["source_aliases"]
    assert any(item.get("source_url") == "https://notes.example/same" for item in aliases)


def test_short_link_uses_resolved_video_family_for_companion_provenance(client, monkeypatch):
    short_url = "https://b23.tv/AbCdE"
    canonical_url = "https://www.bilibili.com/video/BV1ib411t7YR/"

    async def fake_extract(_url, **_kwargs):
        payload = _transcript_payload(canonical_url)
        payload["source_family_id"] = source_family_id(canonical_url)
        return payload

    async def fake_fetch(url):
        return {"text": "配套讲义内容。" * 20, "title": "Companion", "final_url": url}

    monkeypatch.setattr(active_teaching, "extract_video_transcript", fake_extract)
    monkeypatch.setattr(active_teaching, "fetch_safe", fake_fetch)
    response = client.post(
        "/api/active-teaching/workspaces/short-family/materials/video/ingest",
        json={
            "url": short_url,
            "find_additional_materials": True,
            "confirm_transcript": True,
            "selected_material_urls": ["https://notes.example/companion"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    expected_family = source_family_id(canonical_url)
    assert body["transcript_material"]["provenance"]["source_family_id"] == expected_family
    assert body["supplementary_materials"][0]["provenance"]["source_family_id"] == expected_family


def test_no_subtitle_has_stable_review_disposition(client, monkeypatch):
    async def fake_extract(url, **kwargs):
        return {
            "schema": "mentorflow.video-transcript-preview.v1",
            "source_url": url,
            "subtitle": {"segments": [], "text": "", "quality": {"state": "rejected", "reason": "subtitle_unavailable"}},
            "disposition": {"state": "rejected", "reason": "subtitle_unavailable"},
        }

    monkeypatch.setattr(active_teaching, "extract_video_transcript", fake_extract)
    response = client.post(
        "/api/active-teaching/workspaces/video-no-subtitle/materials/video/ingest",
        json={
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "find_additional_materials": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["transcript"]["disposition"]["reason"] == "subtitle_unavailable"
    assert body["ingested"] is False


def test_no_subtitle_can_explicitly_continue_with_selected_supplement_only(client, monkeypatch):
    async def fake_extract(url, **kwargs):
        return {
            "schema": "mentorflow.video-transcript-preview.v1",
            "source_url": url,
            "subtitle": {"segments": [], "text": "", "quality": {"state": "rejected", "reason": "subtitle_unavailable"}},
            "disposition": {"state": "rejected", "reason": "subtitle_unavailable"},
        }

    async def fake_fetch(url):
        return {"text": "独立讲义内容。" * 20, "title": "Lecture notes", "final_url": url}

    monkeypatch.setattr(active_teaching, "extract_video_transcript", fake_extract)
    monkeypatch.setattr(active_teaching, "fetch_safe", fake_fetch)
    response = client.post(
        "/api/active-teaching/workspaces/video-supplement-only/materials/video/ingest",
        json={
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "find_additional_materials": True,
            "selected_material_urls": ["https://notes.example/lecture"],
            "confirm_supplement_only": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ingested_supplement_only"
    assert body["ingested"] is True
    assert body["video_material"] is None
    assert body["video_disposition"]["reason"] == "subtitle_unavailable"
    assert body["supplementary_materials"][0]["source_kind"] == "supplementary_material"
    assert body["material_ids"] == [body["supplementary_materials"][0]["id"]]

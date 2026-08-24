from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app import video_collection_api as api
from app.provider_runtime import create_verified_demo_session


def test_recursive_summary_reduction_keeps_the_tail_instead_of_truncating():
    class TraceProvider:
        async def json(self, messages):
            payload = json.loads(messages[1]["content"])
            summaries = [str(item.get("summary") or item.get("summary_id") or "") for item in payload["inputs"]]
            return {"summary": "|".join(summaries), "key_points": [], "warnings": []}

    inputs = [{"id": f"summary-{index}", "summary_text": f"token-{index:03d}"} for index in range(100)]
    result = asyncio.run(
        api._reduce_provider_summaries(
            TraceProvider(),
            scope="collection",
            items=inputs,
            coverage={"ready": 100, "denominator": 100},
        )
    )
    assert "token-000" in result["summary"]
    assert "token-099" in result["summary"]
    assert all(f"token-{index:03d}" in result["summary"] for index in range(100))


def _manifest_page(url: str, *, offset: int = 0, page_size: int = 50, **_kwargs):
    total = 4
    items = [
        {
            "provider": "fixture",
            "provider_episode_id": f"episode-{index}",
            "canonical_url": f"https://www.bilibili.com/video/BVfixture{index}",
            "title": f"第 {index + 1} 集",
            "ordinal": index,
            "duration_seconds": 120,
            "selected": True,
            "metadata": {"language": "zh-CN"},
        }
        for index in range(offset, min(total, offset + page_size))
    ]
    next_offset = offset + len(items)
    return {
        "source_url": url,
        "provider": "bilibili",
        "provider_collection_id": "series-offline",
        "title": "离线四集课程",
        "reported_count": total,
        "items": items,
        "next_offset": next_offset if next_offset < total else None,
        "has_more": next_offset < total,
        "video_downloaded": False,
    }


async def _fake_manifest_page(*args, **kwargs):
    return _manifest_page(*args, **kwargs)


async def _fake_episode_transcript(url: str, **_kwargs):
    marker = url.rstrip("/").rsplit("/", 1)[-1]
    first = (f"{marker} 介绍矩阵与线性变换。" * 8)
    second = (f"{marker} 说明基向量与坐标。" * 8)
    return {
        "source_url": url,
        "segments": [
            {"ordinal": 0, "start_ms": 0, "end_ms": 5000, "text": first},
            {"ordinal": 1, "start_ms": 5000, "end_ms": 10000, "text": second},
        ],
        "quality": {
            "state": "ready",
            "reason": "subtitle_verified",
            "segment_count": 2,
            "timestamped": True,
        },
        "track": {"language": "zh-CN", "automatic": False, "format": "vtt"},
        "video_downloaded": False,
    }


def _create_collection(client, monkeypatch, workspace_id: str = "video-api") -> dict:
    monkeypatch.setattr(api, "enumerate_video_collection_page", _fake_manifest_page)
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections",
        json={
            "source_url": "https://space.bilibili.com/100/lists/200?type=season",
            "user_confirmed_retrieval": True,
            "page_size": 2,
            "max_episodes": 1000,
            "idempotency_key": f"create-{workspace_id}",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["collection"]


def _transcribe(client, monkeypatch, workspace_id: str, collection_id: str) -> dict:
    monkeypatch.setattr(api, "extract_video_episode_subtitle", _fake_episode_transcript)
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections/{collection_id}/jobs/transcribe",
        json={
            "user_confirmed_retrieval": True,
            "requested_languages": ["zh.*"],
            "allow_automatic": False,
            "max_parallel": 2,
            "idempotency_key": f"transcribe-{collection_id}",
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job"]["id"]
    return client.get(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections/{collection_id}/jobs/{job_id}"
    ).json()


def test_collection_api_requires_explicit_retrieval_and_pages_manifest(client, monkeypatch):
    denied = client.post(
        "/api/active-teaching/workspaces/video-api/video-collections",
        json={"source_url": "https://space.bilibili.com/100/lists/200?type=season"},
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "VIDEO_RETRIEVAL_CONFIRMATION_REQUIRED"

    collection = _create_collection(client, monkeypatch)
    detail = client.get(f"/api/active-teaching/workspaces/video-api/video-collections/{collection['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["collection"]["workspace_id"] == "video-api"
    assert body["collection"]["title"] == "离线四集课程"
    assert body["collection"]["enumerated_count"] == 4
    assert body["jobs"][0]["status"] == "completed"
    assert body["coverage"]["complete"] is False

    first = client.get(
        f"/api/active-teaching/workspaces/video-api/video-collections/{collection['id']}/manifest?limit=2"
    ).json()
    assert len(first["items"]) == 2
    second = client.get(
        f"/api/active-teaching/workspaces/video-api/video-collections/{collection['id']}/manifest",
        params={"limit": 2, "cursor": first["next_cursor"]},
    ).json()
    assert second["page"] == 2
    assert second["previous_cursor"]

    hidden = client.get(
        f"/api/active-teaching/workspaces/another-workspace/video-collections/{collection['id']}"
    )
    assert hidden.status_code == 404


def test_transcript_job_is_offline_mocked_and_episode_payload_is_bounded(client, monkeypatch):
    workspace_id = "video-transcript-api"
    collection = _create_collection(client, monkeypatch, workspace_id)
    job = _transcribe(client, monkeypatch, workspace_id, collection["id"])
    assert job["status"] == "completed"
    assert job["progress"]["ready"] == 4

    manifest = client.get(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections/{collection['id']}/manifest?limit=1"
    ).json()
    episode_id = manifest["items"][0]["id"]
    episode = client.get(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections/{collection['id']}/episodes/{episode_id}",
        params={"segment_limit": 1},
    )
    assert episode.status_code == 200, episode.text
    page = episode.json()["segment_page"]
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert page["has_more"] is True


def test_summary_has_separate_real_provider_gate_and_activates_one_material(client, monkeypatch):
    workspace_id = "video-summary-api"
    collection = _create_collection(client, monkeypatch, workspace_id)
    _transcribe(client, monkeypatch, workspace_id, collection["id"])

    demo = create_verified_demo_session()
    denied = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections/{collection['id']}/jobs/summarize",
        json={
            "user_confirmed_summary": True,
            "provider_session_id": demo.id,
        },
    )
    assert denied.status_code == 428
    assert denied.json()["error"]["code"] == "PROVIDER_VERIFICATION_REQUIRED"

    session = SimpleNamespace(
        id="ps_mock_real",
        provider="openai_compatible",
        base_url="https://provider.example/v1",
        model="mock-model",
        model_reported="mock-model",
        verified=True,
    )

    class MockProvider:
        async def json(self, messages):
            scope = "unknown"
            if messages and len(messages) > 1:
                content = messages[1].get("content", "")
                for candidate in ("collection", "batch", "episode_chunk", "episode"):
                    if f'"scope":"{candidate}"' in content:
                        scope = candidate
                        break
            return {
                "summary": f"{scope} 的离线验证摘要",
                "key_points": ["矩阵表示线性变换", "证据来自已处理字幕"],
                "warnings": [],
            }

    def require_session(session_id, verified=True):
        assert session_id == "ps_mock_real"
        assert verified is True
        return session

    monkeypatch.setattr(api, "require_provider_session", require_session)
    monkeypatch.setattr(api, "provider_for_session", lambda _session: MockProvider())
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections/{collection['id']}/jobs/summarize",
        json={
            "user_confirmed_summary": True,
            "provider_session_id": "ps_mock_real",
            "batch_size": 10,
            "idempotency_key": "summary-mock",
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job"]["id"]
    job = client.get(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections/{collection['id']}/jobs/{job_id}"
    ).json()
    assert job["status"] == "completed"
    assert "provider_session_id" not in job["request"]

    summaries = client.get(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections/{collection['id']}/summaries",
        params={"scope_type": "collection"},
    ).json()
    assert summaries["total"] == 1
    assert summaries["items"][0]["evidence"]

    activate = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/video-collections/{collection['id']}/activate-material",
        json={"user_confirmed_activation": True, "role": "anchor"},
    )
    assert activate.status_code == 200, activate.text
    material = activate.json()["material"]
    assert material["source_kind"] == "video_collection_summary"
    assert material["provenance"]["collection_id"] == collection["id"]
    assert material["provenance"]["material_role"] == "anchor"

from __future__ import annotations

import pytest

from app.resource_references import parse_direct_resource_references


def test_reference_registry_normalizes_supported_exact_references():
    candidates, warnings = parse_direct_resource_references(
        "BV1ib411t7YR av170001 https://b23.tv/AbCdE ",
        "https://youtu.be/dQw4w9WgXcQ https://www.youtube.com/playlist?list=PL123 ",
        "doi:10.1000/xyz123 arXiv:2401.12345 and https://example.org/guide",
    )
    urls = {item["url"] for item in candidates}
    assert "https://www.bilibili.com/video/BV1ib411t7YR/" in urls
    assert "https://www.bilibili.com/video/av170001/" in urls
    assert "https://b23.tv/AbCdE" in urls
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in urls
    assert "https://www.youtube.com/playlist?list=PL123" in urls
    assert "https://doi.org/10.1000/xyz123" in urls
    assert "https://arxiv.org/abs/2401.12345" in urls
    assert "https://example.org/guide" in urls
    assert warnings == []
    assert all(item["direct_user_reference"] is True for item in candidates)
    assert all(item["reference_resolution"]["network_verified"] is False for item in candidates)


def test_reference_registry_reports_malformed_bvid_instead_of_guessing():
    candidates, warnings = parse_direct_resource_references("请使用 BV12345 作为视频")
    assert candidates == []
    assert warnings == ["无法解析资源标识 BV12345：BV 号长度或格式不正确，请核对后重试。"]


def test_reference_registry_deduplicates_raw_id_and_full_url():
    candidates, warnings = parse_direct_resource_references(
        "BV1ib411t7YR https://www.bilibili.com/video/BV1ib411t7YR/?spm_id_from=demo"
    )
    assert warnings == []
    assert [item["url"] for item in candidates] == [
        "https://www.bilibili.com/video/BV1ib411t7YR/"
    ]


def test_reference_registry_preserves_semantic_episode_and_collection_context():
    candidates, warnings = parse_direct_resource_references(
        "https://www.bilibili.com/video/BV1ib411t7YR?p=2 ",
        "https://music.youtube.com/watch?v=abc123XYZ00&list=PLcourse",
    )
    assert warnings == []
    assert candidates[0]["url"] == "https://www.bilibili.com/video/BV1ib411t7YR/?p=2"
    assert candidates[1]["url"] == "https://www.youtube.com/watch?v=abc123XYZ00&list=PLcourse"
    assert candidates[1]["media_kind"] == "video_collection"


def test_bilibili_series_url_is_an_exact_collection_reference():
    candidates, warnings = parse_direct_resource_references(
        "使用 https://space.bilibili.com/100/lists/200?type=season 这一整套课程"
    )
    assert warnings == []
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["url"] == "https://space.bilibili.com/100/lists/200?type=season"
    assert candidate["media_kind"] == "video_collection"
    assert candidate["coverage_role"] == "full_collection"
    assert candidate["direct_identifier"]["kind"] == "bilibili_collection_url"


def test_reference_registry_rejects_generic_http_without_silent_drop():
    candidates, warnings = parse_direct_resource_references("http://example.org/lesson")
    assert candidates == []
    assert warnings == [
        "无法安全使用非 HTTPS 资源 http://example.org/lesson；请提供 HTTPS 地址后重试。"
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost:8443/guide",
        "https://192.168.1.20/manual",
        "https://printer.local/status",
        "https://[::1]:8443/guide",
        "https://[fd00::20]/manual",
    ],
)
def test_reference_registry_preserves_exact_private_https_references_without_verification(url):
    candidates, warnings = parse_direct_resource_references(f"请使用 {url} 作为指定资料")

    assert warnings == []
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["url"] == url
    assert candidate["direct_user_reference"] is True
    assert candidate["coverage_role"] == "user_named_resource"
    assert candidate["reference_resolution"] == {
        "status": "exact",
        "network_verified": False,
        "requires_user_confirmation": True,
    }

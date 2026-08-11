from __future__ import annotations

import asyncio
import re
import sqlite3

from app import learning_resources
from app.learning_resources import (
    discover_learning_resources,
    discover_task_resources,
    ensure_resource_schema,
    public_resources,
)
from app.provider_runtime import (
    create_provider_session,
    get_provider_session,
    reset_provider_sessions,
)
from app.provider_service import ProviderError


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE active_knowledge_points (id TEXT PRIMARY KEY)"
    )
    ensure_resource_schema(connection)
    return connection


def _verified_gemini_session():
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model": "gemini-2.5-flash",
            "api_key": "test-only-key",
        }
    )
    session = get_provider_session(public["provider_session_id"])
    assert session is not None
    session.verified = True
    return session


def _points(count: int) -> list[dict]:
    return [
        {
            "id": f"point-{index:03d}",
            "name": f"知识点 {index}",
            "explanation": f"第 {index} 个跨领域学习概念。",
            "acceptance_method": "能够解释并独立应用。",
        }
        for index in range(count)
    ]


class BatchGroundedProvider:
    def __init__(self):
        self.calls = 0

    async def grounded_web_search(self, prompt: str):
        self.calls += 1
        point_ids = re.findall(r"- id=([^ |]+)", prompt)
        lines = []
        sources = []
        supports = []
        cursor = 0
        for index, point_id in enumerate(point_ids):
            line = (
                f"RESOURCE|{point_id}|interactive_tool|official|"
                f"{point_id} 官方交互工具|用于练习并立即获得反馈"
            )
            lines.append(line)
            sources.append(
                {
                    "index": index,
                    "url": f"https://official.example/{point_id}",
                    "title": "Official Example",
                }
            )
            supports.append(
                {
                    "start_index": cursor,
                    "end_index": cursor + len(line),
                    "text": line,
                    "source_indices": [index],
                }
            )
            cursor += len(line) + 1
        return {
            "text": "\n".join(lines),
            "sources": sources,
            "supports": supports,
            "queries": ["official interactive learning tool"],
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "search_grounded": True,
        }


def test_one_hundred_generic_points_are_batched_grounded_and_cached(monkeypatch):
    connection = _connection()
    points = _points(100)
    connection.executemany(
        "INSERT INTO active_knowledge_points(id) VALUES (?)",
        [(point["id"],) for point in points],
    )
    connection.commit()
    session = _verified_gemini_session()
    provider = BatchGroundedProvider()
    monkeypatch.setattr(
        learning_resources,
        "provider_for_session",
        lambda _session: provider,
    )

    first = asyncio.run(
        discover_learning_resources(
            connection,
            workspace_id="generic-100",
            points=points,
            provider_session_id=session.id,
        )
    )

    assert first["status"] == "completed"
    assert first["resource_count"] == 100
    assert provider.calls == 10
    resources = public_resources(connection, "generic-100")
    assert len(resources) == 100
    assert {
        resource["knowledge_point_id"] for resource in resources
    } == {point["id"] for point in points}
    assert all(resource["source"] == "gemini_google_search_grounding" for resource in resources)
    assert all(resource["discovery_meta"]["search_grounded"] is True for resource in resources)

    cached = asyncio.run(
        discover_learning_resources(
            connection,
            workspace_id="generic-100",
            points=points,
            provider_session_id=session.id,
        )
    )
    assert cached["cache_hit"] is True
    assert provider.calls == 10


def test_uncited_model_url_is_rejected(monkeypatch):
    connection = _connection()
    points = _points(1)
    connection.execute(
        "INSERT INTO active_knowledge_points(id) VALUES (?)",
        (points[0]["id"],),
    )
    connection.commit()
    session = _verified_gemini_session()

    class UngroundedProvider:
        async def grounded_web_search(self, _prompt: str):
            return {
                "text": (
                    "RESOURCE|point-000|reference|official|"
                    "看似权威的网址|但没有搜索引用证据"
                ),
                "sources": [
                    {
                        "index": 0,
                        "url": "https://hallucinated.example/resource",
                        "title": "Uncited",
                    }
                ],
                "supports": [],
                "queries": ["query"],
                "provider": "gemini",
                "model": "gemini-2.5-flash",
            }

    monkeypatch.setattr(
        learning_resources,
        "provider_for_session",
        lambda _session: UngroundedProvider(),
    )
    result = asyncio.run(
        discover_learning_resources(
            connection,
            workspace_id="reject-uncited",
            points=points,
            provider_session_id=session.id,
        )
    )
    assert result["resource_count"] == 0
    assert any("没有 Google Search 引用元数据" in item for item in result["warnings"])
    assert public_resources(connection, "reject-uncited") == []


def test_unsupported_search_is_not_cached_and_does_not_erase_verified_resources(
    monkeypatch,
):
    connection = _connection()
    points = _points(1)
    connection.execute(
        "INSERT INTO active_knowledge_points(id) VALUES (?)",
        (points[0]["id"],),
    )
    connection.commit()
    session = _verified_gemini_session()

    class UnsupportedProvider:
        async def grounded_web_search(self, _prompt: str):
            raise ProviderError(
                "PROVIDER_WEB_SEARCH_UNSUPPORTED",
                "search unavailable",
                400,
            )

    monkeypatch.setattr(
        learning_resources,
        "provider_for_session",
        lambda _session: UnsupportedProvider(),
    )
    first = asyncio.run(
        discover_learning_resources(
            connection,
            workspace_id="provider-switch",
            points=points,
            provider_session_id=session.id,
        )
    )
    assert first["status"] == "unsupported"
    assert first["cache_hit"] is False

    grounded = BatchGroundedProvider()
    monkeypatch.setattr(
        learning_resources,
        "provider_for_session",
        lambda _session: grounded,
    )
    recovered = asyncio.run(
        discover_learning_resources(
            connection,
            workspace_id="provider-switch",
            points=points,
            provider_session_id=session.id,
        )
    )
    assert recovered["status"] == "completed"
    assert recovered["cache_hit"] is False
    assert recovered["resource_count"] == 1

    monkeypatch.setattr(
        learning_resources,
        "provider_for_session",
        lambda _session: UnsupportedProvider(),
    )
    refresh = asyncio.run(
        discover_learning_resources(
            connection,
            workspace_id="provider-switch",
            points=points,
            provider_session_id=session.id,
            force=True,
        )
    )
    assert refresh["status"] == "unsupported"
    assert refresh["resource_count"] == 1
    assert public_resources(connection, "provider-switch")[0]["url"] == (
        "https://official.example/point-000"
    )


def test_openai_compatible_provider_plans_search_and_selects_only_real_candidate(
    monkeypatch,
):
    connection = _connection()
    points = _points(1)
    connection.execute(
        "INSERT INTO active_knowledge_points(id) VALUES (?)",
        (points[0]["id"],),
    )
    connection.commit()
    session = _verified_gemini_session()
    session.provider = "openai_compatible"
    session.model = "deepseek-v4-flash"

    class ToolPlanningProvider:
        async def grounded_web_search(self, _prompt: str):
            raise ProviderError(
                "PROVIDER_WEB_SEARCH_UNSUPPORTED",
                "native search is unavailable",
                400,
            )

        async def json(self, messages):
            request = __import__("json").loads(messages[-1]["content"])
            if "search_candidates" not in request:
                return {
                    "search_requests": [
                        {
                            "point_id": "point-000",
                            "needed": True,
                            "query": "official interactive tool for knowledge point 0",
                            "kind": "interactive_tool",
                            "reason": "交互反馈比静态说明更适合这个验收目标",
                        }
                    ]
                }
            return {
                "selections": [
                    {
                        "point_id": "point-000",
                        "candidate_id": "point-000-candidate-1",
                        "authority": "official",
                        "reason": "这是搜索结果中的第一方交互页面，可立即练习。",
                    },
                    {
                        "point_id": "point-000",
                        "candidate_id": "model-invented-candidate",
                        "authority": "official",
                        "reason": "不得保存这个模型凭空生成的候选。",
                    },
                ]
            }

    async def fake_search(query: str, *, max_results: int = 6):
        assert query == "official interactive tool for knowledge point 0 中文 官方"
        assert max_results == 6
        return {
            "query": query,
            "engine": "bing",
            "errors": [],
            "results": [
                {
                    "url": "https://official.example/interactive/point-0",
                    "title": "知识点 0 官方交互练习",
                    "snippet": "中文页面，可直接练习并获得反馈。",
                    "engine": "bing",
                }
            ],
        }

    monkeypatch.setattr(
        learning_resources,
        "provider_for_session",
        lambda _session: ToolPlanningProvider(),
    )
    monkeypatch.setattr(learning_resources, "search_web", fake_search)
    result = asyncio.run(
        discover_learning_resources(
            connection,
            workspace_id="deepseek-search-tools",
            points=points,
            provider_session_id=session.id,
        )
    )
    assert result["status"] == "completed"
    assert result["provider_meta"]["search_modes"] == [
        "open_metasearch_ai_selected"
    ]
    assert result["resource_count"] == 1
    resources = public_resources(connection, "deepseek-search-tools")
    assert resources[0]["url"] == (
        "https://official.example/interactive/point-0"
    )
    assert resources[0]["source"] == "open_metasearch_ai_selected"
    assert resources[0]["search_query"] == (
        "official interactive tool for knowledge point 0 中文 官方"
    )
    assert resources[0]["discovery_meta"]["selection_grounded"] is True
    assert any("搜索结果之外" in warning for warning in result["warnings"])


def test_task_level_leetcode_fallback_is_chinese_and_survives_no_provider():
    connection = _connection()
    points = [
        {
            "id": "point-000",
            "name": "合并两个有序数组",
            "explanation": "数组双指针与原地归并。",
            "acceptance_method": "能独立编码并通过测试。",
        }
    ]
    connection.execute(
        "INSERT INTO active_knowledge_points(id) VALUES (?)",
        (points[0]["id"],),
    )
    connection.commit()

    result = asyncio.run(
        discover_task_resources(
            connection,
            workspace_id="leetcode-task",
            points=points,
            provider_session_id=None,
            task_label="完全掌握资料；leetcode_top_interview_150_zh.md",
        )
    )

    assert result["resource_count"] == 1
    resources = public_resources(connection, "leetcode-task")
    assert len(resources) == 1
    assert resources[0]["url"] == "https://leetcode.cn/studyplan/top-interview-150/"
    assert resources[0]["source"] == "official_catalog_fallback"
    assert "中文" in resources[0]["title"]
    assert result["provider_meta"]["fallback_used"] is True


def test_open_search_rejects_english_candidate_when_no_chinese_page_exists():
    resources, warnings = learning_resources._parse_open_search_selections(
        {
            "selections": [
                {
                    "point_id": "point-000",
                    "candidate_id": "candidate-1",
                    "authority": "official",
                    "reason": "这个英文页面虽然官方，但不符合中文准备要求。",
                }
            ]
        },
        requests=[
            {
                "point_id": "point-000",
                "query": "知识点 0 中文 官方",
                "kind": "official_practice",
                "reason": "需要中文官方练习页面。",
            }
        ],
        candidates=[
            {
                "candidate_id": "candidate-1",
                "point_id": "point-000",
                "title": "Official English Practice",
                "url": "https://official.example/practice",
                "snippet": "Practice in English.",
                "engine": "bing",
            }
        ],
        provider_meta={"provider": "openai-compatible", "model": "test"},
    )
    assert resources == []
    assert any("拒绝英文页面" in warning for warning in warnings)

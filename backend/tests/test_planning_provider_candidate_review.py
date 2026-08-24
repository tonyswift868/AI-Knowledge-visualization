from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from app import generic_knowledge_api as planner
from app.provider_service import ProviderError


def _slug(value: str) -> str:
    return "-".join(value.casefold().split())


def _session(topic: str, *, provider: str = "openai_compatible", verified: bool = True) -> SimpleNamespace:
    # These values are intentionally sensitive-looking.  The candidate-review
    # prompt must carry only the learner and candidate evidence, never the
    # Provider transport/session secrets.
    return SimpleNamespace(
        id=f"planning-review-{_slug(topic)}",
        provider=provider,
        model="deepseek-chat",
        base_url="https://deepseek.synthetic.example/v1",
        credential_ref="credential-secret-ref",
        verified=verified,
    )


def _initial_provider_payload(topic: str) -> dict[str, Any]:
    return {
        "known_facts": {
            "objective": f"Learn {topic}",
            "purpose": "",
            "timeframe": "",
            "named_materials": [],
            "mastery_criteria": "Explain and apply the core ideas",
            "practice_requirements": ["worked exercises"],
        },
        "resource_search_authorized": True,
        "search_queries": [
            {
                "query": f"{topic} comprehensive lecture notes",
                "reason": "find structured text evidence",
            }
        ],
    }


def _direct_anchor(topic: str) -> dict[str, Any]:
    return {
        "url": f"https://media.synthetic.example/{_slug(topic)}-course.mp4",
        "title": f"{topic} exact learner anchor",
        "media_kind": "video",
        "direct_user_reference": True,
    }


def _note(topic: str, suffix: str, *, title: str | None = None, snippet: str | None = None) -> dict[str, Any]:
    return {
        "url": f"https://materials.synthetic.edu/{_slug(topic)}/{suffix}.pdf",
        "title": title or f"{topic} comprehensive lecture notes {suffix}",
        "snippet": snippet
        or (
            "Complete syllabus with chapter outline, definitions, derivations, "
            "worked examples, formulas, exercises, and review checklist."
        ),
        "media_kind": "document",
    }


def _candidate_list(value: Any) -> list[dict[str, Any]]:
    """Find the bounded candidate evidence list in a review request."""

    if isinstance(value, dict):
        for key in ("candidates", "candidate_items", "resources", "candidate_resources"):
            rows = value.get(key)
            if isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows):
                if any(row.get("candidate_id") or row.get("stable_id") for row in rows):
                    return rows
        for nested in value.values():
            found = _candidate_list(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _candidate_list(nested)
            if found:
                return found
    return []


def _review_request(provider: "_DeepSeekStyleProvider") -> tuple[dict[str, Any], str]:
    for messages in provider.requests:
        for message in messages:
            try:
                payload = json.loads(str(message.get("content") or ""))
            except (TypeError, json.JSONDecodeError):
                continue
            candidates = _candidate_list(payload)
            if candidates:
                return payload, json.dumps(payload, ensure_ascii=False, sort_keys=True)
    raise AssertionError("the verified Provider was not given a candidate-review request")


class _DeepSeekStyleProvider:
    """Offline OpenAI-compatible/DeepSeek-shaped provider double.

    The route may use either ``compact_json`` or ``json``.  Capturing both
    keeps this test at the provider boundary and never opens a network socket.
    """

    def __init__(
        self,
        initial: dict[str, Any],
        review: Callable[[list[dict[str, Any]]], dict[str, Any]] | Exception | str,
    ) -> None:
        self.initial = initial
        self.review = review
        self.requests: list[list[dict[str, str]]] = []

    async def json(self, messages: list[dict[str, str]]) -> Any:
        self.requests.append(list(messages))
        parsed: Any = None
        try:
            parsed = json.loads(str(messages[-1].get("content") or ""))
        except (IndexError, TypeError, json.JSONDecodeError):
            pass
        candidates = _candidate_list(parsed)
        if not candidates:
            return self.initial
        if isinstance(self.review, Exception):
            raise self.review
        if isinstance(self.review, str):
            return self.review
        return self.review(candidates)

    async def compact_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> Any:
        del max_tokens, temperature
        return await self.json(messages)


def _review_rows(
    candidates: list[dict[str, Any]],
    *,
    accepted: set[str] | None = None,
    rejected: set[str] | None = None,
    reason: str = "semantic evidence matches the learner goal",
) -> dict[str, Any]:
    accepted = accepted or {str(row.get("candidate_id") or row.get("stable_id")) for row in candidates}
    rejected = rejected or set()
    reviews = []
    for row in candidates:
        candidate_id = str(row.get("candidate_id") or row.get("stable_id") or "")
        decision = "accept" if candidate_id in accepted and candidate_id not in rejected else "reject"
        reviews.append(
            {
                "candidate_id": candidate_id,
                "decision": decision,
                "score": 0.97 if decision == "accept" else 0.08,
                "reason": reason if decision == "accept" else "off-topic or mirror evidence",
            }
        )
    return {
        "review_status": "completed",
        "reviewer": {"provider": "openai_compatible", "model": "deepseek-chat"},
        "candidate_reviews": reviews,
    }


def _patch_offline(
    monkeypatch: pytest.MonkeyPatch,
    topic: str,
    provider: _DeepSeekStyleProvider,
    rows: list[dict[str, Any]],
) -> list[str]:
    calls: list[str] = []
    session = _session(topic)

    async def fake_search(query: str, *, max_results: int) -> dict[str, Any]:
        del max_results
        calls.append(query)
        return {"results": list(rows)}

    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_args: session)
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_existing_materials", lambda _workspace_id: [])
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)
    # Provider-review cache entries must not leak across parametrized topics or
    # tests; this is still a deterministic offline test, not a cache test.
    monkeypatch.setattr(planner, "_planning_provider_cache_read", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(planner, "_planning_provider_cache_write", lambda *_args, **_kwargs: None)
    return calls


def _post(client: Any, topic: str, anchor: dict[str, Any], *, workspace: str | None = None) -> Any:
    return client.post(
        f"/api/active-teaching/workspaces/{workspace or _slug(topic)}/planning-assistant",
        json={
            "message": (
                f"I want to learn {topic}; keep this exact source {anchor['url']} "
                "and find comprehensive text notes for independent practice."
            ),
            "goal": f"Learn {topic}",
            "search_anchor": topic,
            "resource_search_authorized": True,
            "page_size": 3,
        },
    )


def _provider_review(item: dict[str, Any]) -> dict[str, Any]:
    review = item.get("provider_review") or item.get("semantic_review") or item.get("model_review")
    assert isinstance(review, dict), f"candidate has no auditable provider review: {item}"
    return review


@pytest.mark.parametrize("topic", ["Lunar Cartography", "Copper Weaving"])
def test_verified_provider_review_prompt_is_grounded_and_secret_free(client, monkeypatch, topic: str):
    anchor = _direct_anchor(topic)
    rows = [_note(topic, "a"), _note(topic, "b")]

    def accept_all(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return _review_rows(candidates)

    provider = _DeepSeekStyleProvider(_initial_provider_payload(topic), accept_all)
    _patch_offline(monkeypatch, topic, provider, rows)
    response = _post(client, topic, anchor, workspace=f"prompt-{_slug(topic)}")

    assert response.status_code == 200, response.text
    payload = response.json()
    request_payload, serialized = _review_request(provider)
    candidates = _candidate_list(request_payload)
    assert candidates
    assert f"Learn {topic}" in serialized
    assert topic in serialized or topic.casefold() in serialized.casefold()
    assert anchor["url"] in serialized or anchor["title"] in serialized
    for candidate in candidates:
        assert candidate.get("candidate_id") or candidate.get("stable_id")
        assert str(candidate.get("title") or "") in serialized
        assert str(candidate.get("snippet") or "") in serialized
    lower = serialized.casefold()
    assert any(term in lower for term in ("do not invent", "不得编造", "no invention", "full", "完整"))
    assert "credential_ref" not in lower
    assert "base_url" not in lower
    assert "credential-secret-ref" not in lower
    assert "deepseek.synthetic.example" not in lower
    assert "c:\\" not in lower and "e:\\" not in lower
    assert "api_key" not in lower and "secret" not in lower
    # The endpoint remains stable and exposes the model audit rather than
    # silently converting model output into an untraceable rank.
    assert payload["resources"]
    assert all(_provider_review(item).get("reason") for item in payload["resources"] if not item.get("direct_user_reference"))


@pytest.mark.parametrize("topic,translation", [("Lunar Cartography", "月面制图"), ("Copper Weaving", "铜丝编织")])
def test_semantic_provider_accepts_translated_comprehensive_notes_and_completes_page(
    client, monkeypatch, topic: str, translation: str
):
    anchor = _direct_anchor(topic)
    notes = [
        _note(
            topic,
            "translated-a",
            title=f"{translation} foundations workbook",
            snippet=(
                f"{translation} 全套课程大纲与章节摘要；定义、推导、例题、公式、练习和复习清单。"
            ),
        ),
        _note(
            topic,
            "translated-b",
            title=f"Field guide to {translation} methods",
            snippet=(
                f"{translation} 结构化讲义：章节、原理、 worked examples, exercises, and review checklist."
            ),
        ),
    ]

    def semantic_accept(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return _review_rows(candidates, reason=f"translation and coverage match {topic}")

    provider = _DeepSeekStyleProvider(_initial_provider_payload(topic), semantic_accept)
    _patch_offline(monkeypatch, topic, provider, notes)
    response = _post(client, topic, anchor, workspace=f"semantic-{_slug(topic)}")

    assert response.status_code == 200, response.text
    payload = response.json()
    urls = {item["url"] for item in payload["candidate_page"]["items"]}
    assert anchor["url"] in urls
    assert {row["url"] for row in notes} <= urls
    assert payload["candidate_page"]["total_count"] == 3
    assert payload["candidate_page"]["page_shortfall_count"] == 0
    for item in payload["resources"]:
        if item["url"] in {row["url"] for row in notes}:
            review = _provider_review(item)
            assert review["decision"] in {"accept", "accepted", "keep"}
            assert float(review["score"]) >= 0.8
            assert review["reason"]
            assert review.get("model") == "deepseek-chat" or review.get("provider") in {
                "openai_compatible",
                "deepseek",
            }


def test_semantic_provider_rejects_keyword_mirror_but_keeps_related_note(client, monkeypatch):
    topic = "Copper Weaving"
    anchor = _direct_anchor(topic)
    related = _note(
        topic,
        "semantic-related",
        title="Metal loom pattern workbook",
        snippet="结构化章节讲义：经纬交错、张力原理、示例、练习和复习清单。",
    )
    mirror = _note(
        topic,
        "mirror",
        title=f"Mirror index: {topic} Course Lecture Notes",
        snippet=f"Original source: {anchor['url']}; reposted index only.",
    )

    def review(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        accepted = {
            str(row.get("candidate_id") or row.get("stable_id"))
            for row in candidates
            if "Metal loom" in str(row.get("title") or "")
        }
        return _review_rows(candidates, accepted=accepted)

    provider = _DeepSeekStyleProvider(_initial_provider_payload(topic), review)
    _patch_offline(monkeypatch, topic, provider, [related, mirror])
    response = _post(client, topic, anchor, workspace="mirror-review")

    assert response.status_code == 200, response.text
    payload = response.json()
    urls = {item["url"] for item in payload["resources"]}
    assert related["url"] in urls
    assert mirror["url"] not in urls
    assert mirror["url"] not in {
        item.get("url") for item in payload["candidate_page"].get("items", [])
    }


def test_forged_candidate_id_or_url_from_provider_is_ignored(client, monkeypatch):
    topic = "Lunar Cartography"
    anchor = _direct_anchor(topic)
    real = _note(topic, "real")

    def forged_review(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        accepted = {str(candidates[0].get("candidate_id") or candidates[0].get("stable_id"))}
        result = _review_rows(candidates, accepted=accepted)
        result["candidate_reviews"].append(
            {
                "candidate_id": "forged-candidate-id",
                "url": "https://model-fabricated.synthetic.invalid/notes",
                "decision": "accept",
                "score": 1.0,
                "reason": "model invention must not become a resource",
            }
        )
        return result

    provider = _DeepSeekStyleProvider(_initial_provider_payload(topic), forged_review)
    _patch_offline(monkeypatch, topic, provider, [real])
    response = _post(client, topic, anchor, workspace="forged-review")

    assert response.status_code == 200, response.text
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "model-fabricated.synthetic.invalid" not in serialized
    assert all(item.get("candidate_id") != "forged-candidate-id" for item in payload["resources"])
    assert {item["url"] for item in payload["resources"]} <= {anchor["url"], real["url"]}


def test_model_cannot_override_safety_media_url_or_low_confidence_source_gates(client, monkeypatch):
    topic = "Copper Weaving"
    anchor = _direct_anchor(topic)
    good = _note(topic, "good")
    rows = [
        good,
        {
            **_note(topic, "adult"),
            "url": "https://unsafe.synthetic.example/adult.pdf",
            "title": f"{topic} adult content notes",
            "snippet": "Pornographic adult content; no educational notes.",
        },
        {
            "url": f"https://video.synthetic.example/watch/{_slug(topic)}",
            "title": f"{topic} full lecture video",
            "snippet": "Chapters, definitions, examples, exercises, and review checklist.",
            "media_kind": "video",
        },
        {
            "url": "https://notes.synthetic.example/not-a-url/%zz",
            "title": f"{topic} malformed source notes",
            "snippet": "Chapters, definitions, examples, exercises, and review checklist.",
            "media_kind": "document",
        },
        {
            "url": "https://stackoverflow.com/questions/123",
            "title": f"{topic} answer notes",
            "snippet": "Chapters, definitions, examples, exercises, and review checklist.",
            "media_kind": "document",
        },
        {
            "url": f"https://csdn.net/article/{_slug(topic)}",
            "title": f"{topic} aggregator article",
            "snippet": "Chapters, definitions, examples, exercises, and review checklist.",
            "media_kind": "document",
        },
    ]

    def accept_everything(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return _review_rows(candidates, reason="provider says accept; deterministic safety still wins")

    provider = _DeepSeekStyleProvider(_initial_provider_payload(topic), accept_everything)
    _patch_offline(monkeypatch, topic, provider, rows)
    response = _post(client, topic, anchor, workspace="hard-boundaries")

    assert response.status_code == 200, response.text
    payload = response.json()
    urls = {item["url"] for item in payload["resources"]}
    assert urls <= {anchor["url"], good["url"]}
    assert "unsafe.synthetic.example" not in urls
    assert not any("video.synthetic.example" in url for url in urls)
    assert "stackoverflow.com" not in " ".join(urls)
    assert "csdn.net" not in " ".join(urls)
    assert payload["candidate_page"]["page_shortfall_count"] >= 1


@pytest.mark.parametrize("failure", ["timeout", "exception", "invalid_json"])
def test_provider_review_failure_is_conservative_and_never_fabricates(client, monkeypatch, failure: str):
    topic = "Lunar Cartography"
    anchor = _direct_anchor(topic)
    real = _note(topic, "real")
    error: Exception | str
    if failure == "timeout":
        error = asyncio.TimeoutError()
    elif failure == "exception":
        error = RuntimeError("offline provider failure")
    else:
        error = "not-json"
    provider = _DeepSeekStyleProvider(_initial_provider_payload(topic), error)
    _patch_offline(monkeypatch, topic, provider, [real])
    response = _post(client, topic, anchor, workspace=f"fallback-{failure}")

    assert response.status_code != 500
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    serialized = json.dumps(body, ensure_ascii=False)
    assert "model-fabricated" not in serialized
    assert "https://model-fabricated.synthetic.invalid" not in serialized
    if response.status_code == 200:
        assert {item["url"] for item in body.get("resources", [])} <= {anchor["url"], real["url"]}
        assert body.get("candidate_page", {}).get("page_shortfall_count", 0) >= 0


@pytest.mark.parametrize("provider_state", ["fake", "unverified"])
def test_fake_or_unverified_provider_returns_honest_shortfall_without_500(client, monkeypatch, provider_state: str):
    topic = "Copper Weaving"
    anchor = _direct_anchor(topic)
    session = _session(topic, provider="fake" if provider_state == "fake" else "openai_compatible", verified=False)

    def reject_session(*_args: Any) -> Any:
        raise ProviderError(
            "PROVIDER_VERIFICATION_REQUIRED",
            "verified Provider required",
            428,
        )

    monkeypatch.setattr(planner, "_v1_provider_session", reject_session)
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: (_ for _ in ()).throw(AssertionError("must not call unverified Provider")))
    del session
    response = _post(client, topic, anchor, workspace=f"provider-state-{provider_state}")

    assert response.status_code != 500
    assert "model-fabricated.synthetic.invalid" not in response.text


def test_current_no_search_message_overrides_stale_true_alias():
    assert planner._planning_search_authorized(
        {"search_resources": True},
        "请保留这份资料，不要搜索或推荐其它来源。",
        {"resource_search_authorized": True},
    ) is False


def test_provider_cache_identity_changes_with_selected_and_exact_sources():
    session = _session("Cache Identity")
    common = {
        "message": "请找配套讲义",
        "goal": "掌握任意主题",
        "history": [],
        "search_authorized": True,
    }
    first = planner._planning_provider_cache_identity(
        session,
        **common,
        selected_resources=[{"title": "Source A", "url": "https://example.test/a", "media_kind": "video"}],
        exact_user_references=[{"url": "https://example.test/a", "direct_identifier": "A"}],
    )
    second = planner._planning_provider_cache_identity(
        session,
        **common,
        selected_resources=[{"title": "Source B", "url": "https://example.test/b", "media_kind": "video"}],
        exact_user_references=[{"url": "https://example.test/b", "direct_identifier": "B"}],
    )
    assert first[0] != second[0]
    assert first[2] != second[2]


def test_each_new_bounded_candidate_batch_receives_provider_review(monkeypatch):
    topic = "Lunar Cartography"
    provider = _DeepSeekStyleProvider(
        _initial_provider_payload(topic),
        lambda candidates: _review_rows(candidates),
    )
    session = _session(topic)
    calls = 0

    async def fake_search(query: str, *, max_results: int) -> dict[str, Any]:
        nonlocal calls
        del max_results
        calls += 1
        return {"results": [_note(topic, f"batch-{calls}")]}

    monkeypatch.setattr(planner, "search_web", fake_search)
    monkeypatch.setattr(planner, "_planning_search_cache_read", lambda _query: None)
    monkeypatch.setattr(planner, "_planning_search_cache_write", lambda *_args, **_kwargs: None)
    stats: dict[str, int] = {}
    context = {
        "message": f"Find complete notes for {topic}",
        "objective": f"Learn {topic}",
        "anchors": [_direct_anchor(topic)],
        "generation": {"provider": session.provider, "model": session.model},
    }

    async def run() -> None:
        await planner._planning_search_resources(
            [{"query": f"{topic} complete lecture notes first", "reason": "first lane"}],
            cache_stats=stats,
            provider=provider,
            provider_session=session,
            provider_review_context=context,
        )
        await planner._planning_search_resources(
            [{"query": f"{topic} worked examples course notes second", "reason": "second lane"}],
            cache_stats=stats,
            provider=provider,
            provider_session=session,
            provider_review_context=context,
        )

    asyncio.run(run())
    def has_candidates(messages: list[dict[str, str]]) -> bool:
        for message in messages:
            try:
                payload = json.loads(str(message.get("content") or ""))
            except (TypeError, json.JSONDecodeError):
                continue
            if _candidate_list(payload):
                return True
        return False

    review_requests = [messages for messages in provider.requests if has_candidates(messages)]
    assert len(review_requests) == 2
    assert stats["provider_review_attempts"] == 2
    assert stats["provider_review_count"] == 2

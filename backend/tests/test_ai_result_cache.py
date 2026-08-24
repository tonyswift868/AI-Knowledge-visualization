from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.ai_result_cache import (
    AiComputationResult,
    LocalAiCachePolicy,
    LocalAiResultCache,
    build_ai_result_cache_identity,
    canonical_fingerprint,
    default_ai_result_cache_root,
    effective_cache_policy,
)
from app.providers.telemetry import (
    provider_telemetry_store,
    start_provider_request,
)


RAW_USER_TEXT = "private learner prompt with sk-secret-never-persist"


def _identity(**overrides):
    values = {
        "provider_family": "openai_compatible",
        "model": "deepseek-chat",
        "task_type": "knowledge_extract",
        "prompt_version": "knowledge-extract.v1",
        "schema_version": "knowledge-result.v1",
        "stable_prefix_hash": "ab" * 32,
        "dynamic_input": {"learner_text": RAW_USER_TEXT, "ordinal": 1},
        "source_identity": {"material_id": "material-1", "revision": "rev-1"},
        "generation_parameters": {
            "temperature": 0,
            "top_p": 1,
            "reasoning_mode": "disabled",
            "response_format": "json_schema",
            "tool_schema_identity": "none",
        },
        "processor_version": "extractor.v1",
    }
    values.update(overrides)
    return build_ai_result_cache_identity(**values)


@pytest.fixture(autouse=True)
def _clear_telemetry(monkeypatch):
    provider_telemetry_store.clear()
    monkeypatch.delenv("MENTORFLOW_PROVIDER_TELEMETRY_PATH", raising=False)
    yield
    provider_telemetry_store.clear()


def test_key_is_deterministic_complete_and_does_not_retain_raw_content(tmp_path) -> None:
    first = _identity()
    second = _identity(
        dynamic_input={"ordinal": 1, "learner_text": RAW_USER_TEXT},
        generation_parameters={
            "tool_schema_identity": "none",
            "response_format": "json_schema",
            "reasoning_mode": "disabled",
            "top_p": 1,
            "temperature": 0,
        },
        source_identity={"revision": "rev-1", "material_id": "material-1"},
    )
    cache = LocalAiResultCache(tmp_path)

    assert first == second
    assert first.cache_key == second.cache_key
    assert re.fullmatch(r"[0-9a-f]{64}", first.cache_key)
    assert RAW_USER_TEXT not in first.cache_key
    assert RAW_USER_TEXT not in json.dumps(first.canonical_payload())
    assert RAW_USER_TEXT not in str(cache.cache_path(first))
    assert set(first.canonical_payload()) == {
        "provider_family",
        "model",
        "task_type",
        "prompt_version",
        "schema_version",
        "stable_prefix_hash",
        "dynamic_input_hash",
        "source_identity_hash",
        "generation_parameters_hash",
        "processor_version",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"provider_family": "anthropic"},
        {"model": "deepseek-reasoner"},
        {"task_type": "knowledge_verify"},
        {"prompt_version": "knowledge-extract.v2"},
        {"schema_version": "knowledge-result.v2"},
        {"stable_prefix_hash": "cd" * 32},
        {"dynamic_input": {"learner_text": RAW_USER_TEXT, "ordinal": 2}},
        {"source_identity": {"material_id": "material-1", "revision": "rev-2"}},
        {"generation_parameters": {"temperature": 1}},
        {"processor_version": "extractor.v2"},
    ],
)
def test_every_identity_dimension_invalidates_the_cache(tmp_path, change) -> None:
    cache = LocalAiResultCache(tmp_path)
    baseline = _identity()
    changed = _identity(**change)
    assert baseline.cache_key != changed.cache_key
    assert cache.store(
        baseline,
        {"knowledge": ["validated"]},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).stored
    assert not cache.lookup(
        changed,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).hit


def test_same_validated_input_calls_provider_once_then_uses_local_hit(tmp_path) -> None:
    cache = LocalAiResultCache(tmp_path)
    identity = _identity()
    calls = 0

    def compute() -> AiComputationResult:
        nonlocal calls
        calls += 1
        timer = start_provider_request(provider="openai_compatible", model="deepseek-chat")
        timer.finish(
            payload={"usage": {"prompt_tokens": 10, "completion_tokens": 2}},
            success=True,
        )
        return AiComputationResult.validated_result({"knowledge": ["stable"]})

    first = cache.get_or_compute(
        identity,
        compute,
        cache_policy=LocalAiCachePolicy.ENABLED,
    )
    first_lookup = cache.lookup(
        identity,
        cache_policy=LocalAiCachePolicy.ENABLED,
    )
    first_receipt = provider_telemetry_store.latest()
    second = cache.get_or_compute(
        identity,
        compute,
        cache_policy=LocalAiCachePolicy.ENABLED,
    )
    hit_receipt = provider_telemetry_store.latest()

    assert calls == 1
    assert first.cache_miss is True and first.cache_hit is False
    assert first.provider_called is True and first.cache_write_stored is True
    assert first.cache_lookup_ms >= 0 and first.cache_write_ms is not None
    assert first_lookup.entry is not None
    assert first_lookup.entry.result_hash == canonical_fingerprint(first.payload)
    assert second.cache_hit is True and second.provider_called is False
    assert second.payload == first.payload
    assert first_receipt is not None
    assert first_receipt.local_result_cache_hit is False
    assert first_receipt.local_cache_miss is True
    assert first_receipt.provider_called is True
    assert hit_receipt is not None
    assert hit_receipt.local_result_cache_hit is True
    assert hit_receipt.local_cache_miss is False
    assert hit_receipt.provider_called is False
    assert hit_receipt.prompt_tokens is None
    assert hit_receipt.completion_tokens is None
    assert hit_receipt.total_tokens is None
    assert hit_receipt.cache_lookup_ms is not None


@pytest.mark.parametrize(
    "bad_entry",
    [
        "{broken",
        json.dumps({"cache_schema_version": "local_ai_result_cache.v1"}),
    ],
)
def test_corrupt_or_partial_entry_is_a_safe_miss_and_is_replaced(tmp_path, bad_entry) -> None:
    cache = LocalAiResultCache(tmp_path)
    identity = _identity()
    path = cache.cache_path(identity)
    path.parent.mkdir(parents=True)
    path.write_text(bad_entry, encoding="utf-8")
    calls = 0

    def compute() -> AiComputationResult:
        nonlocal calls
        calls += 1
        timer = start_provider_request(provider="openai_compatible", model="deepseek-chat")
        timer.finish(payload={}, success=True)
        return AiComputationResult.validated_result({"recovered": True})

    execution = cache.get_or_compute(
        identity,
        compute,
        cache_policy=LocalAiCachePolicy.ENABLED,
    )
    receipt = provider_telemetry_store.latest()

    assert calls == 1
    assert execution.cache_miss is True
    assert execution.cache_corrupt is True
    assert execution.payload == {"recovered": True}
    assert receipt is not None and receipt.local_cache_corrupt is True
    assert cache.lookup(identity, cache_policy=LocalAiCachePolicy.ENABLED).hit


def test_wrong_result_hash_is_a_corrupt_miss(tmp_path) -> None:
    cache = LocalAiResultCache(tmp_path)
    identity = _identity()
    assert cache.store(
        identity,
        {"value": "last-good"},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).stored
    path = cache.cache_path(identity)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["result_hash"] = "0" * 64
    path.write_text(json.dumps(document), encoding="utf-8")

    lookup = cache.lookup(identity, cache_policy=LocalAiCachePolicy.ENABLED)
    assert lookup.hit is False
    assert lookup.corrupt is True
    assert lookup.reason == "result_hash_mismatch"


def test_unvalidated_empty_failed_and_oversized_results_are_not_cached(tmp_path) -> None:
    cache = LocalAiResultCache(tmp_path, max_entry_bytes=1_024)
    identity = _identity()
    assert not cache.store(
        identity,
        {"partial": True},
        validated=False,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).stored
    assert not cache.store(
        identity,
        {},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).stored
    assert cache.store(
        identity,
        {"not_json": object()},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).reason == "result_not_serializable"
    assert cache.store(
        identity,
        {"not_finite": float("nan")},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).reason == "result_not_serializable"

    oversized = cache.get_or_compute(
        identity,
        lambda: AiComputationResult.validated_result({"text": "x" * 2_000}),
        cache_policy=LocalAiCachePolicy.ENABLED,
    )
    assert oversized.payload == {"text": "x" * 2_000}
    assert oversized.cache_write_stored is False
    assert oversized.cache_reason == "entry_too_large"
    assert not cache.cache_path(identity).exists()

    def provider_failure() -> AiComputationResult:
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        cache.get_or_compute(
            identity,
            provider_failure,
            cache_policy=LocalAiCachePolicy.ENABLED,
        )
    assert not cache.cache_path(identity).exists()


def test_expired_entry_is_a_miss(tmp_path) -> None:
    now = [datetime(2026, 8, 24, tzinfo=timezone.utc)]
    cache = LocalAiResultCache(tmp_path, ttl_seconds=60, clock=lambda: now[0])
    identity = _identity()
    assert cache.store(
        identity,
        {"value": 1},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).stored
    assert cache.lookup(identity, cache_policy=LocalAiCachePolicy.ENABLED).hit
    now[0] += timedelta(seconds=61)
    expired = cache.lookup(identity, cache_policy=LocalAiCachePolicy.ENABLED)
    assert expired.hit is False
    assert expired.corrupt is False
    assert expired.reason == "expired"


@pytest.mark.parametrize(
    ("field", "replacement", "reason"),
    [
        ("created_at", "not-a-time", "cache_corrupt"),
        ("created_at", "2026-08-25T00:00:00+00:00", "created_at_invalid"),
        ("expires_at", "2026-09-30T00:00:00+00:00", "ttl_mismatch"),
    ],
)
def test_invalid_or_extended_time_metadata_is_rejected(
    tmp_path,
    field,
    replacement,
    reason,
) -> None:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    cache = LocalAiResultCache(tmp_path, ttl_seconds=60, clock=lambda: now)
    identity = _identity()
    assert cache.store(
        identity,
        {"value": 1},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).stored
    path = cache.cache_path(identity)
    document = json.loads(path.read_text(encoding="utf-8"))
    document[field] = replacement
    path.write_text(json.dumps(document), encoding="utf-8")

    lookup = cache.lookup(identity, cache_policy=LocalAiCachePolicy.ENABLED)
    assert lookup.hit is False
    assert lookup.corrupt is True
    assert lookup.reason == reason


def test_atomic_replace_failure_preserves_last_good_entry(tmp_path, monkeypatch) -> None:
    cache = LocalAiResultCache(tmp_path)
    identity = _identity()
    assert cache.store(
        identity,
        {"value": "last-good"},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).stored
    monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("boom")))

    failed = cache.store(
        identity,
        {"value": "new"},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    )
    lookup = cache.lookup(identity, cache_policy=LocalAiCachePolicy.ENABLED)

    assert failed.stored is False and failed.reason == "write_failed"
    assert lookup.hit is True
    assert lookup.entry is not None
    assert lookup.entry.result_payload == {"value": "last-good"}
    assert not list(cache.cache_path(identity).parent.glob("*.tmp"))


def test_same_key_concurrency_is_singleflight_and_file_remains_valid(tmp_path) -> None:
    cache = LocalAiResultCache(tmp_path)
    second_cache_instance = LocalAiResultCache(tmp_path)
    identity = _identity()
    calls = 0
    calls_lock = threading.Lock()

    def compute() -> AiComputationResult:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return AiComputationResult.validated_result({"value": "concurrent"})

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda index: (
                    cache if index % 2 == 0 else second_cache_instance
                ).get_or_compute(
                    identity, compute, cache_policy=LocalAiCachePolicy.ENABLED
                ),
                range(8),
            )
        )

    assert calls == 1
    assert sum(item.provider_called for item in results) == 1
    assert sum(item.cache_hit for item in results) == 7
    persisted = json.loads(cache.cache_path(identity).read_text(encoding="utf-8"))
    assert persisted["result_payload"] == {"value": "concurrent"}


def test_policy_is_disabled_by_default_and_for_conversation_tasks(tmp_path) -> None:
    cache = LocalAiResultCache(tmp_path)
    assert effective_cache_policy(
        "knowledge_extract",
        LocalAiCachePolicy.DISABLED,
    ) is LocalAiCachePolicy.DISABLED
    assert effective_cache_policy(
        "teacher_conversational_response",
        LocalAiCachePolicy.ENABLED,
    ) is LocalAiCachePolicy.DISABLED
    assert effective_cache_policy(
        "free_chat",
        LocalAiCachePolicy.ENABLED,
    ) is LocalAiCachePolicy.DISABLED

    calls = 0

    def compute() -> AiComputationResult:
        nonlocal calls
        calls += 1
        return AiComputationResult.validated_result({"value": calls})

    first = cache.get_or_compute(_identity(task_type="free_chat"), compute)
    second = cache.get_or_compute(_identity(task_type="free_chat"), compute)
    assert calls == 2
    assert first.cache_reason == second.cache_reason == "policy_disabled"
    assert not list(tmp_path.rglob("*.json"))


def test_default_root_is_disposable_user_cache_and_never_repo_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("MENTORFLOW_AI_RESULT_CACHE_ROOT", raising=False)
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path / "user-data"))
    assert default_ai_result_cache_root() == (
        tmp_path / "user-data" / "cache" / "ai-results"
    ).resolve()

    explicit = tmp_path / "explicit-cache"
    monkeypatch.setenv("MENTORFLOW_AI_RESULT_CACHE_ROOT", str(explicit))
    assert default_ai_result_cache_root() == explicit.resolve()

    monkeypatch.delenv("MENTORFLOW_AI_RESULT_CACHE_ROOT", raising=False)
    monkeypatch.delenv("MENTORFLOW_USER_DATA_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="user data root"):
        default_ai_result_cache_root()


def test_directory_limit_rejects_new_entry_without_deleting_existing_data(tmp_path) -> None:
    first_identity = _identity(dynamic_input={"input": 1})
    second_identity = _identity(dynamic_input={"input": 2})
    cache = LocalAiResultCache(
        tmp_path,
        max_entry_bytes=4_096,
        max_directory_bytes=1_300,
    )
    assert cache.store(
        first_identity,
        {"text": "a" * 300},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).stored
    rejected = cache.store(
        second_identity,
        {"text": "b" * 900},
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    )
    assert rejected.stored is False
    assert rejected.reason == "directory_limit"
    assert cache.lookup(
        first_identity,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).hit


def test_concurrent_different_key_writes_cannot_exceed_directory_limit(tmp_path) -> None:
    probe_root = tmp_path / "probe"
    first_identity = _identity(dynamic_input={"input": "first"})
    second_identity = _identity(dynamic_input={"input": "second"})
    probe = LocalAiResultCache(probe_root, max_directory_bytes=10_000)
    payload = {"text": "x" * 300}
    assert probe.store(
        first_identity,
        payload,
        validated=True,
        cache_policy=LocalAiCachePolicy.ENABLED,
    ).stored
    one_entry_size = probe.cache_path(first_identity).stat().st_size

    bounded = LocalAiResultCache(
        tmp_path / "bounded",
        max_directory_bytes=one_entry_size + 16,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        writes = list(
            executor.map(
                lambda identity: bounded.store(
                    identity,
                    payload,
                    validated=True,
                    cache_policy=LocalAiCachePolicy.ENABLED,
                ),
                (first_identity, second_identity),
            )
        )

    assert sum(write.stored for write in writes) == 1
    assert sum(path.stat().st_size for path in bounded.root.rglob("*.json")) <= (
        one_entry_size + 16
    )

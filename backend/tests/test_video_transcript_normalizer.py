from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import replace
from typing import Any

import pytest

from app.sources.video.transcript_evidence import (
    SelectedTrackEvidence,
    TranscriptEvidenceBundle,
    TranscriptPartEvidence,
    TranscriptSegment,
)
from app.sources.video.transcript_normalizer import (
    NORMALIZER_VERSION,
    CorrectionCandidate,
    CorrectionOutcome,
    CorrectionType,
    GlossaryTerm,
    TerminologyGlossary,
    TranscriptCorrection,
    build_normalization_plan,
    materialize_normalized_text,
    normalize_transcript,
    normalized_transcript_hash,
    validate_correction_patches,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bundle(
    rows: list[str | tuple[str, int]],
    *,
    fetched_at: str = "2026-08-24T00:00:00+00:00",
) -> TranscriptEvidenceBundle:
    segments: list[TranscriptSegment] = []
    part_ordinals: dict[int, int] = {}
    for global_index, raw in enumerate(rows, start=1):
        text, part_index = raw if isinstance(raw, tuple) else (raw, 1)
        ordinal = part_ordinals.get(part_index, 0) + 1
        part_ordinals[part_index] = ordinal
        segment_hash = _digest(text)
        segments.append(
            TranscriptSegment(
                stable_segment_id=f"segment-{global_index}-{segment_hash[:16]}",
                ordinal=ordinal,
                part_index=part_index,
                part_cid=str(1000 + part_index),
                start_ms=(ordinal - 1) * 1000,
                end_ms=ordinal * 1000,
                original_text=text,
                language="zh-Hans",
                subtitle_kind="manual",
                source_reference="https://www.bilibili.com/video/BV1ib411t7YR/",
                segment_hash=segment_hash,
            )
        )
    track = SelectedTrackEvidence(
        track_identity="track",
        payload_source_identity="inline",
        language="zh-Hans",
        kind="manual",
        extension="srt",
        source="data",
        name=None,
    )
    parts = tuple(
        TranscriptPartEvidence(
            part_index=part_index,
            cid=str(1000 + part_index),
            title=f"Java 与系统课程 P{part_index}",
            duration_seconds=float(part_ordinals[part_index]),
            subtitle_identity=f"subtitle-{part_index}",
            selected_track=track,
            raw_payload_hash=_digest(
                "\n".join(segment.original_text for segment in segments if segment.part_index == part_index)
            ),
            segment_ids=tuple(segment.segment_id for segment in segments if segment.part_index == part_index),
            warnings=(),
        )
        for part_index in sorted(part_ordinals)
    )
    timeline_hash = _digest("\n".join(segment.segment_hash for segment in segments))
    bundle_hash = _digest("bundle:" + timeline_hash)
    return TranscriptEvidenceBundle(
        schema="mentorflow.transcript-evidence.v1",
        parser_version="mentorflow.subtitle-timeline.v1",
        bundle_id="bundle-" + bundle_hash,
        bundle_hash=bundle_hash,
        source_reference="https://www.bilibili.com/video/BV1ib411t7YR/",
        platform="bilibili",
        bvid="BV1ib411t7YR",
        source_identity="bilibili:BV1ib411t7YR",
        source_revision="revision-" + timeline_hash,
        part_count=len(parts),
        subtitle_language="zh-Hans",
        subtitle_kind="manual",
        fetched_at=fetched_at,
        raw_payload_hash=_digest("payload:" + timeline_hash),
        normalized_timeline_hash=timeline_hash,
        parts=parts,
        segments=tuple(segments),
        warnings=(),
    )


def _glossary() -> TerminologyGlossary:
    return TerminologyGlossary(
        (
            GlossaryTerm("HashMap", ("hash map",), "en", "confirmed-material", 1.0),
            GlossaryTerm("threshold", ("three hold",), "en", "source-recurring", 1.0),
            GlossaryTerm("ConcurrentHashMap", ("concurrent hash map",), "en", "course-title", 1.0),
            GlossaryTerm("Compare-And-Swap", ("compare and swamp",), "en", "confirmed-material", 1.0),
            GlossaryTerm("CAS", ("C A S",), "en", "confirmed-material", 1.0),
            GlossaryTerm("AQS", ("abstract queue synchronizer",), "en", "confirmed-material", 1.0),
            GlossaryTerm("JVM", ("Java virtual machine",), "en", "course-title", 1.0),
            GlossaryTerm("volatile", ("volitile",), "en", "source-recurring", 1.0),
            GlossaryTerm("MySQL", ("my sequel",), "en", "confirmed-material", 1.0),
            GlossaryTerm("Redis", ("re dis",), "en", "confirmed-material", 1.0),
            GlossaryTerm("TCP", ("T C P",), "en", "confirmed-material", 1.0),
            GlossaryTerm("HTTP", ("H T T P",), "en", "confirmed-material", 1.0),
            GlossaryTerm("TIME_WAIT", ("T I M E wait",), "en", "confirmed-material", 1.0),
            GlossaryTerm("ThreadToken", ("thread word",), "en", "candidate", 0.7),
            GlossaryTerm("ThreadConcept", ("thread word",), "en", "candidate", 0.7),
        )
    )


GOLD_AUTO_ROWS = [
    ("使用 hash map 存储", "使用 HashMap 存储"),
    ("达到 three hold 以后扩容", "达到 threshold 以后扩容"),
    ("concurrent hash map 支持并发", "ConcurrentHashMap 支持并发"),
    ("这里的 compare and swamp 会重试", "这里的 Compare-And-Swap 会重试"),
    ("通过 C A S 更新状态", "通过 CAS 更新状态"),
    ("abstract queue synchronizer 管理队列", "AQS 管理队列"),
    ("Java virtual machine 加载字节码", "JVM 加载字节码"),
    ("volitile 字段需要可见性", "volatile 字段需要可见性"),
    ("连接 my sequel 数据库", "连接 MySQL 数据库"),
    ("写入 re dis 缓存", "写入 Redis 缓存"),
    ("建立 T C P 连接", "建立 TCP 连接"),
    ("发送 H T T P 请求", "发送 HTTP 请求"),
    ("连接进入 T I M E wait 状态", "连接进入 TIME_WAIT 状态"),
]

GOLD_UNCHANGED_ROWS = [
    "HashMap 的键不能重复",
    "threshold 已经设置为零点七",
    "ConcurrentHashMap 分段并发",
    "CAS 与 AQS 是并发术语",
    "JVM 执行 volatile 语义",
    "MySQL 和 Redis 都已正确拼写",
    "TCP 上承载 HTTP，关闭后可能出现 TIME_WAIT",
]

GOLD_SUGGESTED_ROWS = [
    "volatil 可能是一个术语",
    "threshol 需要结合上下文",
    "Rediss 可能指缓存",
    "MySQLl 可能指数据库",
    "HTP 可能是协议名",
    "thread word 存在两个候选",
]

GOLD_REJECTED_CASES = (
    "unknown_segment",
    "stale_span",
    "semantic_swap",
    "empty_replacement",
    "duplicate_patch_a",
    "duplicate_patch_b",
)

GOLD_FIXTURE = tuple(
    [("AUTO_APPLIED", original, expected) for original, expected in GOLD_AUTO_ROWS]
    + [("UNCHANGED", original, original) for original in GOLD_UNCHANGED_ROWS]
    + [("SUGGESTED", original, original) for original in GOLD_SUGGESTED_ROWS]
    + [("REJECTED", case, None) for case in GOLD_REJECTED_CASES]
)


class FakeTextProvider:
    provider_name = "fake"
    model_name = "fake-correction-v1"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def text(self, messages: list[dict[str, Any]], temperature: float = 0.2) -> str:
        self.calls.append(messages)
        assert temperature == 0.0
        return self.responses.pop(0)


def _run(bundle: TranscriptEvidenceBundle, glossary: TerminologyGlossary, **kwargs):
    return asyncio.run(normalize_transcript(bundle, glossary, **kwargs))


def _view(segment: TranscriptSegment, result) -> str:
    return materialize_normalized_text(
        segment,
        result.corrections,
        candidates=result.candidates,
    )


def test_32_case_gold_fixture_prioritizes_auto_precision_and_preserves_non_auto_text():
    rows = [original for original, _expected in GOLD_AUTO_ROWS] + GOLD_UNCHANGED_ROWS + GOLD_SUGGESTED_ROWS
    bundle = _bundle(rows)
    before = bundle.to_dict()

    result = _run(bundle, _glossary())

    assert len(GOLD_FIXTURE) == 32
    assert {decision: sum(case[0] == decision for case in GOLD_FIXTURE) for decision in {
        "AUTO_APPLIED", "UNCHANGED", "SUGGESTED", "REJECTED"
    }} == {"AUTO_APPLIED": 13, "UNCHANGED": 7, "SUGGESTED": 6, "REJECTED": 6}
    assert result.metrics.total_segments == 26
    assert result.metrics.candidate_segments == 19
    assert result.metrics.auto_applied == 13
    assert result.metrics.suggested == 6
    assert result.provider_status == "PROVIDER_INTEGRATION_REQUIRED"
    assert len(result.review_required) == 1

    correct_auto_applied = 0
    for index, (_original, expected) in enumerate(GOLD_AUTO_ROWS):
        materialized = _view(bundle.segments[index], result)
        correct_auto_applied += materialized == expected
    assert correct_auto_applied == len(result.corrections) == 13
    assert correct_auto_applied / len(result.corrections) == 1.0

    for segment in bundle.segments[len(GOLD_AUTO_ROWS) :]:
        assert _view(segment, result) == segment.original_text
    assert bundle.to_dict() == before
    assert result.original_bundle_hash == bundle.bundle_hash
    assert result.original_timeline_hash == bundle.normalized_timeline_hash


def test_known_alias_changes_only_sparse_view_and_never_evidence_metadata():
    bundle = _bundle([("使用 three hold 判断扩容", 2)])
    segment = bundle.segments[0]
    before = segment.to_dict()

    result = _run(bundle, _glossary())

    assert _view(segment, result) == "使用 threshold 判断扩容"
    assert segment.to_dict() == before
    assert segment.original_text == "使用 three hold 判断扩容"
    assert (segment.start_ms, segment.end_ms, segment.part_index, segment.part_cid) == (0, 1000, 2, "1002")
    assert segment.segment_hash == before["segment_hash"]


def test_correct_terms_are_unchanged_and_create_no_candidates():
    bundle = _bundle(GOLD_UNCHANGED_ROWS)
    result = _run(bundle, _glossary())

    assert result.metrics.candidate_segments == 0
    assert result.metrics.auto_applied == 0
    assert result.provider_status == "NOT_REQUIRED"
    assert all(_view(segment, result) == segment.original_text for segment in bundle.segments)


def test_contextual_provider_auto_applies_only_supplied_candidate_term():
    bundle = _bundle(["threshol 控制扩容"])
    segment = bundle.segments[0]
    response = json.dumps(
        {
            "corrections": [
                {
                    "segment_id": segment.segment_id,
                    "original_span": "threshol",
                    "replacement": "threshold",
                    "confidence": 0.99,
                    "reason": "surrounding HashMap context",
                }
            ]
        }
    )
    provider = FakeTextProvider([response])

    result = _run(
        bundle,
        _glossary(),
        provider=provider,
        provider_name=provider.provider_name,
        model_name=provider.model_name,
    )

    assert len(provider.calls) == result.metrics.provider_calls == 1
    assert len(result.corrections) == 1
    assert result.corrections[0].provider == "fake"
    assert result.corrections[0].model == "fake-correction-v1"
    assert _view(segment, result) == "threshold 控制扩容"
    assert segment.original_text == "threshol 控制扩容"


@pytest.mark.parametrize("response", ["", "{", "[]", '{"corrections":'])
def test_invalid_or_empty_provider_output_never_changes_original(response: str):
    bundle = _bundle(["threshol 控制扩容"])
    baseline = _run(bundle, _glossary())
    provider = FakeTextProvider([response])

    result = _run(bundle, _glossary(), provider=provider)

    assert not result.corrections
    assert result.rejections
    assert result.normalized_hash == baseline.normalized_hash
    assert _view(bundle.segments[0], result) == bundle.segments[0].original_text


def test_provider_schema_rejects_coercion_nan_duplicate_keys_and_oversized_output():
    bundle = _bundle(["threshol 控制扩容"])
    segment_id = bundle.segments[0].segment_id
    valid = {
        "segment_id": segment_id,
        "original_span": "threshol",
        "replacement": "threshold",
        "confidence": 0.99,
        "reason": "context",
    }
    responses = [
        json.dumps({"corrections": [{**valid, "segment_id": 1}]}),
        json.dumps({"corrections": [{**valid, "confidence": True}]}),
        (
            '{"corrections":[{"segment_id":'
            + json.dumps(segment_id)
            + ',"original_span":"threshol","replacement":"threshold",'
            '"confidence":NaN,"reason":"context"}]}'
        ),
        (
            '{"corrections":[{"segment_id":'
            + json.dumps(segment_id)
            + ',"original_span":"threshol","replacement":"threshold",'
            '"replacement":"other","confidence":0.99,"reason":"context"}]}'
        ),
        "x" * 256_001,
    ]

    for response in responses:
        result = _run(bundle, _glossary(), provider=FakeTextProvider([response]))
        assert not result.corrections
        assert result.rejections
        assert _view(bundle.segments[0], result) == bundle.segments[0].original_text


def test_provider_response_item_count_is_bounded():
    bundle = _bundle(["threshol 控制扩容"])
    segment = bundle.segments[0]
    item = {
        "segment_id": segment.segment_id,
        "original_span": "threshol",
        "replacement": "threshold",
        "confidence": 0.99,
        "reason": "context",
    }
    response = json.dumps({"corrections": [item] * 129})

    result = _run(bundle, _glossary(), provider=FakeTextProvider([response]))

    assert not result.corrections
    assert [entry.code for entry in result.rejections] == ["TOO_MANY_PROVIDER_CORRECTIONS"]


def test_provider_rejects_unknown_stale_semantic_empty_and_duplicate_patches():
    bundle = _bundle(["threshol controls HashMap"])
    segment = bundle.segments[0]
    valid = {
        "segment_id": segment.segment_id,
        "original_span": "threshol",
        "replacement": "threshold",
        "confidence": 0.99,
        "reason": "context",
    }
    rejected_items = [
        {**valid, "segment_id": "unknown-segment"},
        {**valid, "original_span": "missing span"},
        {
            **valid,
            "original_span": "HashMap",
            "replacement": "ConcurrentHashMap",
        },
        {**valid, "replacement": ""},
        valid,
        valid,
    ]
    assert len(rejected_items) == len(GOLD_REJECTED_CASES)
    response = json.dumps({"corrections": rejected_items})

    result = _run(bundle, _glossary(), provider=FakeTextProvider([response]))

    assert not result.corrections
    assert len(result.rejections) == 6
    assert {item.code for item in result.rejections} == {
        "EMPTY_REPLACEMENT",
        "PATCH_CONFLICT",
        "PATCH_STALE",
        "SEMANTIC_CONFLICT",
        "UNKNOWN_SEGMENT_ID",
    }
    assert _view(segment, result) == segment.original_text


def test_provider_sentence_rewrite_is_a_semantic_conflict():
    bundle = _bundle(["threshol controls HashMap"])
    segment = bundle.segments[0]
    response = json.dumps(
        {
            "corrections": [
                {
                    "segment_id": segment.segment_id,
                    "original_span": segment.original_text,
                    "replacement": "ConcurrentHashMap is not thread safe.",
                    "confidence": 1.0,
                    "reason": "rewrite fact",
                }
            ]
        }
    )

    result = _run(bundle, _glossary(), provider=FakeTextProvider([response]))

    assert not result.corrections
    assert [item.code for item in result.rejections] == ["SEMANTIC_CONFLICT"]
    assert _view(segment, result) == segment.original_text


def test_provider_cannot_modify_timestamp_part_or_cid_fields():
    bundle = _bundle([("threshol 控制扩容", 2)])
    segment = bundle.segments[0]
    response = json.dumps(
        {
            "corrections": [
                {
                    "segment_id": segment.segment_id,
                    "original_span": "threshol",
                    "replacement": "threshold",
                    "confidence": 0.99,
                    "reason": "context",
                    "start_ms": 999,
                    "part_index": 99,
                    "part_cid": "forged",
                }
            ]
        }
    )

    result = _run(bundle, _glossary(), provider=FakeTextProvider([response]))

    assert not result.corrections
    assert any(item.code == "INVALID_PROVIDER_ITEM" for item in result.rejections)
    assert (segment.start_ms, segment.end_ms, segment.part_index, segment.part_cid) == (0, 1000, 2, "1002")


def test_overlapping_manual_patches_are_both_rejected():
    bundle = _bundle(["three hold"])
    segment = bundle.segments[0]
    plan = build_normalization_plan(bundle, _glossary())
    first_candidate = plan.candidates[0]
    second_candidate = CorrectionCandidate(
        candidate_id="candidate-overlap",
        segment_id=segment.segment_id,
        original_span="hold",
        start_index=6,
        end_index=10,
        candidate_terms=("threshold",),
        correction_type=CorrectionType.SAFE_DETERMINISTIC,
        confidence=1.0,
        reason="test overlap",
        context_segment_ids=(segment.segment_id,),
        source_segment_hash=segment.segment_hash,
    )

    def patch(candidate: CorrectionCandidate, patch_id: str) -> TranscriptCorrection:
        return TranscriptCorrection(
            patch_id=patch_id,
            candidate_id=candidate.candidate_id,
            segment_id=segment.segment_id,
            original_span=candidate.original_span,
            replacement="threshold",
            start_index=candidate.start_index,
            end_index=candidate.end_index,
            correction_type=CorrectionType.SAFE_DETERMINISTIC,
            outcome=CorrectionOutcome.AUTO_APPLIED,
            confidence=1.0,
            reason="test",
            context_segment_ids=(segment.segment_id,),
            normalizer_version=NORMALIZER_VERSION,
            source_segment_hash=segment.segment_hash,
        )

    validation = validate_correction_patches(
        bundle,
        (patch(first_candidate, "p1"), patch(second_candidate, "p2")),
        candidates=(first_candidate, second_candidate),
    )

    assert not validation.accepted
    assert [item.code for item in validation.rejections] == ["PATCH_CONFLICT", "PATCH_CONFLICT"]
    assert materialize_normalized_text(segment, (), candidates=()) == segment.original_text


def test_public_patch_validator_requires_an_authoritative_candidate():
    bundle = _bundle(["threshol controls"])
    plan = build_normalization_plan(bundle, _glossary())
    legitimate = plan.local_suggestions[0]
    forged = replace(
        legitimate,
        patch_id="forged",
        candidate_id="not-a-current-candidate",
        replacement="MALICIOUS",
        outcome=CorrectionOutcome.AUTO_APPLIED,
    )

    validation = validate_correction_patches(bundle, (forged,), candidates=plan.candidates)

    assert not validation.accepted
    assert [item.code for item in validation.rejections] == ["UNKNOWN_CANDIDATE"]
    assert materialize_normalized_text(bundle.segments[0], (forged,), candidates=plan.candidates) == "threshol controls"


def test_materializer_rechecks_candidate_type_context_and_review_outcome():
    contextual_bundle = _bundle(["threshol controls"])
    contextual_plan = build_normalization_plan(contextual_bundle, _glossary())
    contextual = contextual_plan.local_suggestions[0]
    wrong_type = replace(contextual, correction_type=CorrectionType.REVIEW_REQUIRED)
    wrong_context = replace(contextual, context_segment_ids=())

    review_bundle = _bundle(["thread word"])
    review_plan = build_normalization_plan(review_bundle, _glossary())
    review_candidate = review_plan.candidates[0]
    forced_review = TranscriptCorrection(
        patch_id="forced-review",
        candidate_id=review_candidate.candidate_id,
        segment_id=review_candidate.segment_id,
        original_span=review_candidate.original_span,
        replacement=review_candidate.candidate_terms[0],
        start_index=review_candidate.start_index,
        end_index=review_candidate.end_index,
        correction_type=review_candidate.correction_type,
        outcome=CorrectionOutcome.AUTO_APPLIED,
        confidence=1.0,
        reason="forged auto decision",
        context_segment_ids=review_candidate.context_segment_ids,
        normalizer_version=NORMALIZER_VERSION,
        source_segment_hash=review_candidate.source_segment_hash,
    )

    for bundle, plan, patch in (
        (contextual_bundle, contextual_plan, wrong_type),
        (contextual_bundle, contextual_plan, wrong_context),
        (review_bundle, review_plan, forced_review),
    ):
        validation = validate_correction_patches(bundle, (patch,), candidates=plan.candidates)
        assert not validation.accepted
        assert [item.code for item in validation.rejections] == ["CANDIDATE_MISMATCH"]
        assert materialize_normalized_text(bundle.segments[0], (patch,), candidates=plan.candidates) == bundle.segments[0].original_text


def test_sentence_shaped_glossary_replacement_is_not_auto_applied():
    bundle = _bundle(["bad token"])
    glossary = TerminologyGlossary((GlossaryTerm("safe. replacement", ("bad",), confidence=1.0),))

    result = _run(bundle, glossary)

    assert not result.corrections
    assert any(item.code == "SEMANTIC_CONFLICT" for item in result.rejections)
    assert _view(bundle.segments[0], result) == "bad token"


def test_same_patch_set_has_stable_hash_and_changed_patch_changes_only_derived_hash():
    bundle = _bundle(["hash map 与 three hold"])
    original_bundle_hash = bundle.bundle_hash
    original_timeline_hash = bundle.normalized_timeline_hash
    first = _run(bundle, _glossary())
    second_bundle = replace(bundle, fetched_at="2026-08-25T00:00:00+00:00")
    second = _run(second_bundle, _glossary())

    assert first.normalized_hash == second.normalized_hash
    assert normalized_transcript_hash(bundle, first.corrections, candidates=first.candidates) == normalized_transcript_hash(
        bundle,
        reversed(first.corrections),
        candidates=first.candidates,
    )
    metadata_only_change = replace(
        first.corrections[0],
        patch_id="metadata-only-change",
        confidence=0.97,
        reason="different provider explanation",
    )
    assert normalized_transcript_hash(
        bundle,
        (metadata_only_change, *first.corrections[1:]),
        candidates=first.candidates,
    ) == first.normalized_hash
    changed_glossary = TerminologyGlossary(
        (
            GlossaryTerm("Hash_Map", ("hash map",), confidence=1.0),
            GlossaryTerm("threshold", ("three hold",), confidence=1.0),
        )
    )
    changed_result = _run(bundle, changed_glossary)
    assert changed_result.normalized_hash != first.normalized_hash
    assert bundle.bundle_hash == original_bundle_hash
    assert bundle.normalized_timeline_hash == original_timeline_hash


def test_glossary_alias_order_does_not_change_hash_or_batch_identity():
    first_glossary = TerminologyGlossary(
        (GlossaryTerm("threshold", ("three hold", "thresh hold"), confidence=0.8),)
    )
    second_glossary = TerminologyGlossary(
        (GlossaryTerm("threshold", ("thresh hold", "three hold"), confidence=0.8),)
    )
    bundle = _bundle(["three hold"])

    first = build_normalization_plan(bundle, first_glossary)
    second = build_normalization_plan(bundle, second_glossary)

    assert first_glossary.glossary_hash == second_glossary.glossary_hash
    assert [batch.batch_id for batch in first.batches] == [batch.batch_id for batch in second.batches]


def test_repeated_identical_provider_span_is_reviewed_as_ambiguous():
    bundle = _bundle(["threshol and threshol"])
    segment = bundle.segments[0]
    response = json.dumps(
        {
            "corrections": [
                {
                    "segment_id": segment.segment_id,
                    "original_span": "threshol",
                    "replacement": "threshold",
                    "confidence": 0.99,
                    "reason": "context",
                }
            ]
        }
    )

    result = _run(bundle, _glossary(), provider=FakeTextProvider([response]))

    assert not result.corrections
    assert any(item.code == "AMBIGUOUS_SPAN" for item in result.rejections)


def test_ambiguous_provider_suggestion_is_counted_once():
    bundle = _bundle(["thread word"])
    segment = bundle.segments[0]
    response = json.dumps(
        {
            "corrections": [
                {
                    "segment_id": segment.segment_id,
                    "original_span": "thread word",
                    "replacement": "ThreadConcept",
                    "confidence": 0.99,
                    "reason": "one plausible interpretation",
                }
            ]
        }
    )

    result = _run(bundle, _glossary(), provider=FakeTextProvider([response]))

    assert len(result.suggestions) == len(result.review_required) == 1
    assert result.metrics.suggested == 1


def test_batches_are_bounded_and_context_never_crosses_part_boundary():
    rows: list[str | tuple[str, int]] = [
        ("P1 normal", 1),
        ("threshol P1", 1),
        ("P2 normal", 2),
        ("threshol P2", 2),
        ("P2 tail", 2),
    ]
    bundle = _bundle(rows)
    plan = build_normalization_plan(bundle, _glossary(), context_window=2, batch_size=1)

    assert len(plan.batches) == 2
    for batch in plan.batches:
        assert len(batch.items) == 1
        item = batch.items[0]
        target_part = next(segment.part_index for segment in bundle.segments if segment.segment_id == item.candidate.segment_id)
        assert all(context.part_index == target_part for context in item.context)
        assert len(item.context) <= 3
        messages = batch.messages()
        assert messages[0]["role"] == "system"
        payload = json.loads(messages[1]["content"])
        assert payload["items"][0]["candidate_id"] == item.candidate.candidate_id
        assert payload["items"][0]["start_index"] == item.candidate.start_index


def test_provider_context_is_character_bounded_and_keeps_the_target_span():
    bundle = _bundle(["x" * 5000 + " threshol"])
    plan = build_normalization_plan(bundle, _glossary(), context_window=2)

    context = plan.batches[0].items[0].context[0]
    assert context.text_truncated is True
    assert len(context.original_text) == 1000
    assert "threshol" in context.original_text
    assert len(plan.batches[0].messages()[1]["content"]) < 256_000


def test_context_window_two_contains_at_most_five_same_part_segments():
    bundle = _bundle(["normal", "normal", "normal", "threshol", "normal", "normal", "normal"])
    plan = build_normalization_plan(bundle, _glossary(), context_window=2)

    context = plan.batches[0].items[0].context
    assert len(context) == 5
    assert {item.part_index for item in context} == {1}


def test_36000_segment_scan_is_sparse_bounded_and_builds_batches_without_provider_calls():
    rows = ["threshol" if index % 100 == 0 else "正常字幕" for index in range(36_000)]
    bundle = _bundle(rows)

    started = time.perf_counter()
    plan = build_normalization_plan(bundle, _glossary(), batch_size=32)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert plan.metrics.total_segments == 36_000
    assert plan.metrics.candidate_segments == 360
    assert plan.metrics.candidate_ratio == pytest.approx(0.01)
    assert plan.metrics.correction_batches == 12
    assert plan.metrics.provider_calls == 0
    assert not plan.deterministic_corrections
    assert len(plan.candidates) == len(plan.local_suggestions) == 360
    assert sum(len(batch.items) for batch in plan.batches) == 360
    assert all(len(item.context) <= 3 for batch in plan.batches for item in batch.items)
    serialized_items = 0
    for batch in plan.batches:
        messages = batch.messages()
        payload = json.loads(messages[1]["content"])
        serialized_items += len(payload["items"])
        assert len(messages[1]["content"]) < 256_000
    assert serialized_items == 360
    assert elapsed_ms < 10_000

from __future__ import annotations

import ast
import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any

import pytest

import app.teaching.composer as composer_module
from app.teaching.composer import (
    ConversationTurn,
    FollowUpKind,
    RelevantLearnerContext,
    TeacherComposerError,
    TeacherEvidence,
    TeacherProviderDraft,
    TeacherResponseDraft,
    compose_teacher_response,
)
from app.teaching.contracts import (
    CurrentTaskKind,
    EvidenceRequirement,
    ExampleDepth,
    ExampleHistory,
    ExampleStrategy,
    LearnerContext,
    MasteryState,
    StudentMove,
)
from app.teaching.pedagogy import plan_teaching
from app.teaching.prompt import (
    STABLE_OUTPUT_CONTRACT,
    STABLE_TEACHER_RULES,
    build_teacher_messages,
)


EVIDENCE = TeacherEvidence(
    evidence_id="chunk-1",
    content="HashMap resizes after size exceeds capacity multiplied by loadFactor.",
    citation="JDK HashMap implementation note",
    source_reference="source://java/hashmap#resize",
)


def _context(student_move: StudentMove, **changes: Any) -> LearnerContext:
    values: dict[str, Any] = {
        "current_concept": "HashMap resize",
        "learning_goal": "Distinguish threshold from capacity",
        "learner_level": "intermediate",
        "known_prerequisites": ("hashing",),
        "mastery_state": MasteryState.IN_PROGRESS,
        "student_move": student_move,
        "evidence_available": True,
        "relevant_evidence_ids": ("chunk-1",),
    }
    values.update(changes)
    return LearnerContext(**values)


@dataclass(frozen=True)
class Scenario:
    name: str
    user_message: str
    context: LearnerContext
    expected_prompt_fragment: str


SCENARIOS = (
    Scenario(
        "first concept",
        "第一次学这个概念。",
        _context(StudentMove.CONTINUE),
        '"teaching_move":"explain"',
    ),
    Scenario("confused", "我没听懂。", _context(StudentMove.CONFUSED), '"depth":"brief"'),
    Scenario(
        "different example",
        "换个例子。",
        _context(
            StudentMove.ASK_EXAMPLE,
            recent_examples=(
                ExampleHistory(
                    example_id="bank-transfer",
                    strategy=ExampleStrategy.REAL_WORLD,
                    depth=ExampleDepth.LEVEL_1_REAL_WORLD,
                    effective=False,
                ),
            ),
        ),
        '"avoid_example_ids":["bank-transfer"]',
    ),
    Scenario(
        "code example",
        "给我代码例子。",
        _context(StudentMove.REQUEST_PRACTICAL_USE),
        '"example_strategy":"code"',
    ),
    Scenario(
        "why",
        "为什么？",
        _context(StudentMove.ASK_WHY),
        '"teaching_move":"answer_directly"',
    ),
    Scenario(
        "misconception",
        "是不是 size 等于 capacity 才扩容？",
        _context(
            StudentMove.INCORRECT_ANSWER_SIGNAL,
            known_misconceptions=("size == capacity triggers resize",),
        ),
        '"target_misconception":"size == capacity triggers resize"',
    ),
    Scenario(
        "challenge",
        "你说得不对吧？",
        _context(StudentMove.CHALLENGE_TEACHER),
        '"teaching_move":"answer_directly"',
    ),
    Scenario(
        "related tangent",
        "那 ConcurrentHashMap 呢？",
        _context(
            StudentMove.RELATED_TANGENT,
            next_concept="ConcurrentHashMap",
            next_concept_relationship="adds concurrency-oriented coordination",
        ),
        '"preserve_current_learning_position":true',
    ),
    Scenario(
        "mastered fast lane",
        "这个我会了。",
        _context(
            StudentMove.LEARNER_SAYS_MASTERED,
            next_concept="ConcurrentHashMap",
            next_concept_relationship="builds on HashMap behavior under concurrency",
        ),
        '"teaching_move":"transition"',
    ),
    Scenario(
        "natural transition",
        "继续。",
        _context(
            StudentMove.REQUEST_SKIP,
            next_concept="ConcurrentHashMap",
            next_concept_relationship="extends map usage to concurrent access",
        ),
        '"relationship":"extends map usage to concurrent access"',
    ),
    Scenario(
        "required evidence",
        "解释扩容。",
        _context(StudentMove.ASK_DEFINITION),
        '"evidence_requirement":"required"',
    ),
    Scenario(
        "optional evidence",
        "出道练习。",
        _context(StudentMove.REQUEST_ASSESSMENT),
        '"evidence_requirement":"optional"',
    ),
    Scenario(
        "simplify",
        "讲简单一点。",
        _context(StudentMove.REQUEST_SIMPLIFY),
        '"depth":"brief"',
    ),
    Scenario(
        "deepen",
        "再深入一点。",
        _context(StudentMove.ASK_DEEPER_EXPLANATION),
        '"depth":"deep"',
    ),
    Scenario(
        "incorrect correction",
        "我的答案错在哪里？",
        _context(
            StudentMove.INCORRECT_ANSWER_SIGNAL,
            recent_mistakes=("used capacity instead of threshold",),
        ),
        '"supporting_moves":["correct"]',
    ),
    Scenario(
        "correct unverified",
        "我答对了吗？",
        _context(StudentMove.CORRECT_ANSWER_SIGNAL, current_task=CurrentTaskKind.PRACTICE),
        '"transition_recommended":false',
    ),
    Scenario(
        "practical use",
        "实际项目里怎么用？",
        _context(StudentMove.REQUEST_PRACTICAL_USE),
        '"preferred_example_depth":3',
    ),
)


class RecordingProvider:
    provider_name = "recording-fake"
    model = "offline-contract-model"

    def __init__(
        self,
        *,
        text: str = "关键不是等到容量用完，而是 size 越过 threshold 时触发扩容。",
        cited_evidence_ids: tuple[str, ...] = ("chunk-1",),
        follow_up_kind: FollowUpKind = FollowUpKind.NONE,
        raw_output: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.text = text
        self.cited_evidence_ids = cited_evidence_ids
        self.follow_up_kind = follow_up_kind
        self.raw_output = raw_output
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def structured(self, messages, response_model, temperature=0.2):
        self.calls.append(
            {
                "messages": messages,
                "response_model": response_model,
                "temperature": temperature,
            }
        )
        if self.error is not None:
            raise self.error
        if self.raw_output is not None:
            return self.raw_output
        return response_model(
            text=self.text,
            cited_evidence_ids=self.cited_evidence_ids,
            follow_up_kind=self.follow_up_kind,
        )


def _compose(scenario: Scenario, provider: RecordingProvider | None = None):
    brief = plan_teaching(scenario.context)
    selected_provider = provider or RecordingProvider(
        cited_evidence_ids=(
            ("chunk-1",)
            if brief.evidence_requirement is EvidenceRequirement.REQUIRED
            else ()
        )
    )
    result = asyncio.run(
        compose_teacher_response(
            provider=selected_provider,
            teaching_brief=brief,
            latest_user_message=scenario.user_message,
            evidence=(EVIDENCE,),
            learner_context=RelevantLearnerContext(
                learner_level="intermediate",
                known_prerequisites=("hashing",),
            ),
            recent_conversation=(
                ConversationTurn(role="assistant", content="我们刚刚讨论了 threshold。"),
            ),
        )
    )
    return brief, selected_provider, result


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_learning_conversation_scenarios_inject_planner_decisions_without_state_commands(
    scenario: Scenario,
) -> None:
    brief, provider, result = _compose(scenario)

    assert len(provider.calls) == 1
    assert provider.calls[0]["response_model"] is TeacherProviderDraft
    assert result.teaching_move is brief.teaching_move
    assert result.text.startswith("关键不是")
    assert (
        scenario.expected_prompt_fragment
        in provider.calls[0]["messages"][1]["content"]
    )
    payload = result.model_dump(mode="json")
    assert {
        "mastery_update",
        "task_complete",
        "plan_update",
        "score",
        "grade",
    }.isdisjoint(payload)


def test_prompt_has_stable_rules_and_dynamic_sections_in_required_order() -> None:
    brief = plan_teaching(_context(StudentMove.ASK_WHY))
    messages = build_teacher_messages(
        teaching_brief=brief,
        evidence=(EVIDENCE.model_dump(mode="json"),),
        learner_context={"learner_level": "intermediate"},
        recent_conversation=({"role": "user", "content": "先讲了 capacity"},),
        latest_user_message="为什么 threshold 不是 capacity？",
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert STABLE_TEACHER_RULES in messages[0]["content"]
    assert STABLE_OUTPUT_CONTRACT in messages[0]["content"]
    dynamic = messages[1]["content"]
    tags = (
        "<TEACHING_BRIEF>",
        "<EVIDENCE>",
        "<RELEVANT_LEARNER_CONTEXT>",
        "<RECENT_CONVERSATION>",
        "<LATEST_USER_MESSAGE>",
    )
    assert [dynamic.index(tag) for tag in tags] == sorted(
        dynamic.index(tag) for tag in tags
    )
    assert "为什么 threshold 不是 capacity？" in dynamic


def test_prompt_explicitly_prevents_fixed_template_and_mandatory_question() -> None:
    rules = STABLE_TEACHER_RULES.casefold()
    assert "fixed openings" in rules
    assert "fixed closings" in rules
    assert "question is not required every turn" in rules
    assert "answer a learner's real question directly" in rules
    assert "knowledge point" in rules


def test_citation_metadata_is_caller_owned_and_separate_from_natural_text() -> None:
    scenario = next(item for item in SCENARIOS if item.name == "why")
    provider = RecordingProvider(cited_evidence_ids=("chunk-1",))
    _brief, _provider, result = _compose(scenario, provider)

    assert result.text == provider.text
    assert result.citations[0].model_dump(mode="json") == {
        "evidence_id": "chunk-1",
        "citation": "JDK HashMap implementation note",
        "source_reference": "source://java/hashmap#resize",
    }
    assert "source://" not in result.text


def test_evidence_not_named_by_brief_is_not_sent_to_provider() -> None:
    brief = plan_teaching(_context(StudentMove.ASK_WHY))
    provider = RecordingProvider(cited_evidence_ids=("chunk-1",))
    extra = TeacherEvidence(
        evidence_id="unrequested",
        content="This record must not enter the prompt.",
    )
    asyncio.run(
        compose_teacher_response(
            provider=provider,
            teaching_brief=brief,
            latest_user_message="为什么？",
            evidence=(EVIDENCE, extra),
        )
    )

    prompt_payload = provider.calls[0]["messages"][1]["content"]
    assert "unrequested" not in prompt_payload
    assert "This record must not enter" not in prompt_payload


def test_required_evidence_missing_fails_before_provider_call() -> None:
    brief = plan_teaching(_context(StudentMove.ASK_WHY))
    provider = RecordingProvider()

    with pytest.raises(TeacherComposerError) as captured:
        asyncio.run(
            compose_teacher_response(
                provider=provider,
                teaching_brief=brief,
                latest_user_message="为什么？",
                evidence=(),
            )
        )

    assert captured.value.code == "EVIDENCE_REQUIRED_BUT_MISSING"
    assert provider.calls == []


def test_required_brief_without_evidence_ids_fails_closed() -> None:
    brief = plan_teaching(
        _context(
            StudentMove.ASK_WHY,
            evidence_available=False,
            relevant_evidence_ids=(),
        )
    )
    provider = RecordingProvider()

    with pytest.raises(TeacherComposerError) as captured:
        asyncio.run(
            compose_teacher_response(
                provider=provider,
                teaching_brief=brief,
                latest_user_message="为什么？",
            )
        )

    assert captured.value.code == "EVIDENCE_REQUIRED_BUT_MISSING"
    assert provider.calls == []


@pytest.mark.parametrize(
    "raw_output",
    [
        {"text": "", "cited_evidence_ids": ["chunk-1"], "follow_up_kind": "none"},
        {
            "text": "合法文本",
            "cited_evidence_ids": ["invented"],
            "follow_up_kind": "none",
        },
        {
            "text": "合法文本",
            "cited_evidence_ids": ["chunk-1"],
            "follow_up_kind": "none",
            "mastery_update": "mastered",
        },
        {"text": "合法文本", "cited_evidence_ids": ["chunk-1"]},
        {"text": "合法文本", "cited_evidence_ids": [], "follow_up_kind": "none"},
        "not-json",
    ],
)
def test_invalid_empty_or_malformed_provider_output_fails_closed(
    raw_output: object,
) -> None:
    brief = plan_teaching(_context(StudentMove.ASK_WHY))
    provider = RecordingProvider(raw_output=raw_output)

    with pytest.raises(TeacherComposerError) as captured:
        asyncio.run(
            compose_teacher_response(
                provider=provider,
                teaching_brief=brief,
                latest_user_message="为什么？",
                evidence=(EVIDENCE,),
            )
        )

    assert captured.value.code == "TEACHER_RESPONSE_INVALID"
    assert len(provider.calls) == 1


def test_provider_timeout_is_normalized_without_state_effects() -> None:
    brief = plan_teaching(_context(StudentMove.ASK_WHY))
    provider = RecordingProvider(error=TimeoutError("offline timeout"))

    with pytest.raises(TeacherComposerError) as captured:
        asyncio.run(
            compose_teacher_response(
                provider=provider,
                teaching_brief=brief,
                latest_user_message="为什么？",
                evidence=(EVIDENCE,),
            )
        )

    assert captured.value.code == "TEACHER_RESPONSE_INVALID"
    assert captured.value.details == {"cause": "TimeoutError"}
    assert len(provider.calls) == 1


def test_provider_cannot_override_planner_move_or_add_state_fields() -> None:
    brief = plan_teaching(_context(StudentMove.ASK_WHY))
    provider = RecordingProvider(
        raw_output={
            "text": "解释",
            "cited_evidence_ids": ["chunk-1"],
            "follow_up_kind": "none",
            "teaching_move": "transition",
            "task_complete": True,
        }
    )

    with pytest.raises(TeacherComposerError) as captured:
        asyncio.run(
            compose_teacher_response(
                provider=provider,
                teaching_brief=brief,
                latest_user_message="为什么？",
                evidence=(EVIDENCE,),
            )
        )

    assert captured.value.code == "TEACHER_RESPONSE_INVALID"


def test_composer_has_one_provider_call_and_no_state_or_cache_dependency() -> None:
    source = inspect.getsource(composer_module)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    provider_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "provider"
        and node.func.attr == "structured"
    ]

    assert imported_modules == {
        "__future__",
        "collections.abc",
        "contracts",
        "enum",
        "prompt",
        "pydantic",
        "typing",
    }
    assert len(provider_calls) == 1
    assert {
        "mastery_update",
        "task_complete",
        "plan_update",
        "score",
        "grade",
    }.isdisjoint(TeacherResponseDraft.model_fields)
    assert all(
        forbidden not in source
        for forbidden in (
            "active_teaching",
            "generic_knowledge_api",
            "provider_runtime",
            "SessionLocal",
            "result_cache",
        )
    )


def test_composer_does_not_mutate_brief_or_input_evidence() -> None:
    scenario = next(item for item in SCENARIOS if item.name == "misconception")
    brief = plan_teaching(scenario.context)
    brief_before = brief.model_dump(mode="json")
    evidence_before = EVIDENCE.model_dump(mode="json")
    provider = RecordingProvider(cited_evidence_ids=("chunk-1",))

    result = asyncio.run(
        compose_teacher_response(
            provider=provider,
            teaching_brief=brief,
            latest_user_message=scenario.user_message,
            evidence=(EVIDENCE,),
        )
    )

    assert brief.model_dump(mode="json") == brief_before
    assert EVIDENCE.model_dump(mode="json") == evidence_before
    assert isinstance(result, TeacherResponseDraft)
    json.dumps(result.model_dump(mode="json"))


def test_follow_up_is_optional_and_not_forced_to_a_question() -> None:
    scenario = next(item for item in SCENARIOS if item.name == "why")
    provider = RecordingProvider(
        text="因为阈值由 capacity × loadFactor 决定。",
        cited_evidence_ids=("chunk-1",),
        follow_up_kind=FollowUpKind.NONE,
    )
    _brief, _provider, result = _compose(scenario, provider)

    assert result.follow_up_kind is FollowUpKind.NONE
    assert not result.text.endswith(("?", "？"))

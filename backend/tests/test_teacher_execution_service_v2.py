from __future__ import annotations

import ast
import asyncio
import inspect

from app.teaching.application_v2 import TurnCommitStatus, TurnTransactionStatus
from app.teaching.fastpath_v2 import (
    FakeStreamingTeacherProvider,
    TeacherContextBudget,
    TeacherExecutionService,
    TeacherLatencyBudget,
    TeacherResponseMode,
    TeacherResponseLengthPolicy,
)
from app.teaching.session_v2 import (
    ClassroomSessionState,
    InMemoryClassroomSessionStore,
)
from test_classroom_session_v2 import ClassroomStateFixture, _command

import app.teaching.fastpath_v2.execution as execution_module


def _budget() -> TeacherLatencyBudget:
    return TeacherLatencyBudget(
        budget_id="execution-service-fixture",
        fast_ttft_expected_ms=1_000,
        fast_total_expected_ms=5_000,
        deep_ttft_expected_ms=2_000,
        deep_total_expected_ms=10_000,
        timeout_risk_ms=20_000,
    )


def _context_budget() -> TeacherContextBudget:
    return TeacherContextBudget(
        budget_id="execution-context-fixture",
        maximum_prompt_tokens=8_000,
        maximum_evidence_tokens=4_000,
        maximum_history_tokens=1_000,
    )


def _length_policy() -> TeacherResponseLengthPolicy:
    return TeacherResponseLengthPolicy(
        fast_clarification_target_tokens=80,
        fast_why_target_tokens=160,
        fast_example_target_tokens=220,
        fast_default_target_tokens=140,
        fast_maximum_output_tokens=320,
        deep_target_tokens=600,
        deep_maximum_output_tokens=1_200,
    )


def _service():
    state = ClassroomStateFixture(("session-1",))
    store = InMemoryClassroomSessionStore()
    service = TeacherExecutionService.from_ports(
        learner_context_port=state,
        learning_event_sink_port=state,
        assessment_port=state,
        session_store=store,
        learning_position_port=state,
        latency_budget=_budget(),
        context_budget=_context_budget(),
        response_length_policy=_length_policy(),
    )
    return service, store, state


async def _open(service: TeacherExecutionService) -> None:
    created = await service.create_session("session-1")
    assert created.state is ClassroomSessionState.CREATED
    active = await service.activate_session("session-1")
    assert active.state is ClassroomSessionState.ACTIVE


def test_normal_fast_turn_flows_to_existing_pending_commit_plan() -> None:
    async def scenario() -> None:
        service, _store, state = _service()
        provider = FakeStreamingTeacherProvider()
        await _open(service)
        command = _command("session-1", 1)

        execution = await service.execute_turn(command, provider=provider)
        application = execution.application_result
        assert execution.stream_receipt.response_mode == TeacherResponseMode.FAST.value
        assert execution.stream_receipt.provider_call_count == 1
        assert execution.execution_trace == execution.stream_receipt.execution_trace
        assert execution.degradation_decision == execution.execution_trace.degradation_decision
        assert application.transaction.status is TurnTransactionStatus.PENDING_COMMIT
        assert application.transaction.commit_plan.status is TurnCommitStatus.PENDING
        assert application.runtime_result is not None
        assert state.commit_calls == []
        assert "<RESPONSE_LENGTH_POLICY>" in provider.envelopes[0].dynamic_tail.content
        assert '"maximum_output_tokens":320' in provider.envelopes[0].dynamic_tail.content

        committed = await service.commit_turn(command)
        assert committed.transaction.status is TurnTransactionStatus.COMMITTED
        assert len(state.commit_calls) == 1
        assert provider.call_count == 1

    asyncio.run(scenario())


def test_duplicate_execution_replays_without_a_second_provider_call() -> None:
    async def scenario() -> None:
        service, _store, _state = _service()
        provider = FakeStreamingTeacherProvider()
        await _open(service)
        command = _command("session-1", 1)

        first = await service.execute_turn(command, provider=provider)
        replay = await service.execute_turn(command, provider=provider)
        assert replay.application_result.idempotent_replay is True
        assert replay.stream_receipt == first.stream_receipt
        assert provider.call_count == 1

    asyncio.run(scenario())


def test_deep_route_still_uses_one_primary_provider_call() -> None:
    async def scenario() -> None:
        service, _store, _state = _service()
        provider = FakeStreamingTeacherProvider()
        await _open(service)
        command = _command("session-1", 1)
        command = command.model_copy(
            update={
                "turn": command.turn.model_copy(
                    update={"latest_user_message": "请严格证明并逐步推导摊还复杂度"}
                )
            }
        )

        result = await service.execute_turn(command, provider=provider)
        assert result.stream_receipt.response_mode == TeacherResponseMode.DEEP.value
        assert provider.call_count == 1

    asyncio.run(scenario())


def test_pause_resume_and_abort_delegate_to_classroom_transaction() -> None:
    async def scenario() -> None:
        service, store, state = _service()
        provider = FakeStreamingTeacherProvider()
        await _open(service)
        command = _command("session-1", 1)
        await service.execute_turn(command, provider=provider)

        paused = await service.pause_session("session-1")
        assert paused.state is ClassroomSessionState.PAUSED
        resumed = await service.resume_session("session-1")
        assert resumed.state is ClassroomSessionState.ACTIVE
        aborted = await service.abort_session("session-1")
        snapshot = await store.load("session-1")
        assert aborted.state is ClassroomSessionState.ABORTED
        assert snapshot is not None and snapshot.pending_turn is None
        assert state.commit_calls == []
        assert provider.call_count == 1

    asyncio.run(scenario())


def test_execution_composition_is_endpoint_neutral_and_avoids_legacy_hotspots() -> None:
    tree = ast.parse(inspect.getsource(execution_module))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert all("main" not in name for name in imported_modules)
    assert all("active_teaching" not in name for name in imported_modules)
    assert all("generic_knowledge_api" not in name for name in imported_modules)

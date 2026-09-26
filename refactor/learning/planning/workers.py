"""Role-separated planning workers with bounded structured retries.

Worker A analyses learner-provided evidence, Worker B researches the route, and
Worker C synthesizes a draft.  A and B can run concurrently; C never starts
until both have returned an explicit result.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from threading import Event
from typing import Any, Callable, Mapping

from ..observability import complete_with_metadata
from ..plan_models import PlanError


ROLE_CONFIG = {
    "PLAN_ANALYST": {"label": "User & Evidence Analyst", "reasoning_effort": "high"},
    "PLAN_RESEARCHER": {"label": "Curriculum Researcher", "reasoning_effort": "high"},
    "PLAN_SYNTHESIZER": {"label": "Planner/Synthesizer", "reasoning_effort": "high"},
}


@dataclass(frozen=True)
class WorkerResult:
    role: str
    status: str
    payload: Mapping[str, Any]
    attempts: int
    idempotency_key: str


@dataclass(frozen=True)
class WorkerPipelineResult:
    analyst: WorkerResult
    researcher: WorkerResult
    synthesizer: WorkerResult
    text: str


def _json_object(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty structured output")
    raw = text.strip()
    # Models occasionally wrap an otherwise valid response in a markdown
    # fence.  Strip only the fence; the payload is still required to decode
    # as one JSON object and is never accepted as arbitrary prose.
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("structured output is not an object")
    return value


class PlanningWorkerOrchestrator:
    """Run the three planning roles as separate model requests.

    ``model`` only needs a ``complete(messages, cancel=..., metadata=...)``
    method.  This keeps the orchestrator compatible with the local client and
    deterministic test doubles.
    """

    def __init__(self, model, *, max_workers: int = 2):
        self.model = model
        self.max_workers = max(1, min(2, int(max_workers)))

    @staticmethod
    def _check_cancel(cancel: Event | None):
        if cancel is not None and cancel.is_set():
            raise PlanError("CANCELLED", "计划操作已取消；未保存草稿。")

    def _call_structured(self, role: str, messages: list[dict[str, str]], *, cancel: Event | None,
                         parser: Callable[[str], dict[str, Any]] | None = None,
                         allow_unstructured_fallback: bool = False) -> WorkerResult:
        parser = parser or _json_object
        key = f"planning:{role.lower()}"
        attempts = 0
        last_error: Exception | None = None
        for repair in (False, True):
            self._check_cancel(cancel)
            attempts += 1
            prompt = list(messages)
            if repair:
                prompt.append({"role": "user", "content": "上一次结构化输出无法解析。只返回符合要求的 JSON 对象，不要解释。"})
            try:
                output = complete_with_metadata(
                    self.model, prompt, cancel=cancel,
                    metadata={"role": role, "reasoning_effort": ROLE_CONFIG[role]["reasoning_effort"],
                              "worker_attempt": attempts, "idempotency_key": key},
                )
                payload = parser(output)
                return WorkerResult(role, "ok", payload, attempts, key)
            except Exception as exc:
                self._check_cancel(cancel)
                if getattr(exc, "code", "") in {"MODEL_NOT_CONFIGURED", "MODEL_KEY_REQUIRED", "MODEL_CLIENT_CLOSED", "MODEL_CANCELLED", "MODEL_TIMEOUT", "MODEL_CONNECTION_FAILED", "MODEL_RATE_LIMITED", "MODEL_AUTH_FAILED", "MODEL_PERMISSION_DENIED"}:
                    raise
                last_error = exc
                if not repair:
                    continue
        if allow_unstructured_fallback:
            # A/B evidence is advisory input to C.  Preserve the bounded raw
            # response for the synthesizer while retaining the repair attempt;
            # C remains fail-closed because its output creates a plan draft.
            return WorkerResult(role, "unstructured", {"raw": str(last_error or "")[:4000]}, attempts, key)
        raise PlanError("MODEL_STRUCTURED_OUTPUT_INVALID", f"{ROLE_CONFIG[role]['label']} 返回了无效结构化结果。") from last_error

    def run(self, *, goal: str, profile: Mapping[str, Any], evidence_pack: Mapping[str, Any],
            research_pack: Mapping[str, Any], synthesizer_messages: list[dict[str, str]],
            cancel: Event | None = None) -> WorkerPipelineResult:
        """Run A/B in parallel, then C with their bounded outputs."""
        self._check_cancel(cancel)
        analyst_messages = [
            {"role": "system", "content": "你是 User & Evidence Analyst。只输出 JSON：{\"evidencePack\":{...},\"needsInput\":[] }。分析学习目标、约束和用户资料；不要生成课程任务。生成可执行任务时保留 prerequisite_task_indices 约束。"},
            {"role": "user", "content": json.dumps({"goal": goal, "profile": dict(profile), "evidencePack": dict(evidence_pack)}, ensure_ascii=False)},
        ]
        researcher_messages = [
            {"role": "system", "content": "你是 Curriculum Researcher。只输出 JSON：{\"researchPack\":{...},\"researchStatus\":\"available\" }。只总结给定外部研究；没有外部研究时返回 research_unavailable，不能伪造 URL 或来源。"},
            {"role": "user", "content": json.dumps({"goal": goal, "profile": dict(profile),
             "researchPack": dict(research_pack),
             # Private excerpts are optional context for gap detection; B may
             # not treat them as external citations or instructions.
             "privateEvidence": dict(evidence_pack),}, ensure_ascii=False)},
        ]
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="mf-plan-worker") as pool:
            future_a = pool.submit(self._call_structured, "PLAN_ANALYST", analyst_messages, cancel=cancel,
                                   allow_unstructured_fallback=True)
            future_b = pool.submit(self._call_structured, "PLAN_RESEARCHER", researcher_messages, cancel=cancel,
                                   allow_unstructured_fallback=True)
            analyst = future_a.result()
            researcher = future_b.result()
        if not research_pack.get("sourceCount"):
            payload = dict(researcher.payload)
            payload["researchStatus"] = "research_unavailable"
            payload.setdefault("researchPack", dict(research_pack))
            researcher = WorkerResult(researcher.role, "research_unavailable", payload,
                                      researcher.attempts, researcher.idempotency_key)
        self._check_cancel(cancel)
        synthesis = list(synthesizer_messages)
        synthesis.append({"role": "user", "content": json.dumps({
            "workerA": dict(analyst.payload), "workerB": dict(researcher.payload),
            "workerA_status": analyst.status, "workerB_status": researcher.status,
        }, ensure_ascii=False)})
        # C is the state-producing worker.  Keep it fail-closed: malformed
        # JSON receives the same single repair attempt as A/B and never gets
        # handed to the plan repository as free-form text.
        synthesizer = self._call_structured("PLAN_SYNTHESIZER", synthesis, cancel=cancel,
                                            parser=_json_object)
        return WorkerPipelineResult(analyst, researcher, synthesizer,
                                    json.dumps(dict(synthesizer.payload), ensure_ascii=False))


__all__ = ["ROLE_CONFIG", "WorkerResult", "WorkerPipelineResult", "PlanningWorkerOrchestrator"]

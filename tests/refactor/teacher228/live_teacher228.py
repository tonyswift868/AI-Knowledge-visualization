"""One-call-per-sample direct 228 validation through Classroom._generate_teacher.

This is an opt-in live tool, not collected by pytest. All prompts use the six
deidentified synthetic samples in teacher226.live_compare226. It has no baseline
mode; the preceding 226 answers are retained as offline human-labelled excerpts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

from tests.refactor.simulation.run import live_factory
from tests.refactor.teacher226 import live_compare226
from refactor.learning.app.classroom import Classroom
from refactor.learning.course import teacher_agent
from refactor.learning.connections import gateway as gateway_module
from refactor.learning.connections.pricing import timestamp


REPO = Path(__file__).resolve().parents[3]
ROOT = Path("D:/MentorFlow/.local/test-tmp/teacher228/live")
EXPECTATION = ROOT / "expectation.md"
ACCOUNT = live_compare226.ACCOUNT
PRESET_ROOT = live_compare226.PRESET_ROOT
BUDGET_CNY = Decimal("10")
OUTPUT_CAP = 8192


def _write(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


class _CaptureGateway:
    def __init__(self, gateway, persist):
        self.gateway = gateway
        self.reserved = Decimal(0)
        self.calls = []
        self.persist = persist

    def __getattr__(self, name):
        return getattr(self.gateway, name)

    def complete(self, purpose, messages, *, system=None, **kwargs):
        bound = live_compare226._estimate(self.gateway, system or "", messages)
        # Classroom can rewrite one malformed response; reserve each model
        # gateway request at the existing retry worst case before sending it.
        if self.reserved + bound > BUDGET_CNY:
            raise _BudgetExceeded("预算闸门拒绝发送此请求")
        self.reserved += bound
        started = timestamp()
        record = {
            "purpose": purpose,
            "request": {"system": system, "messages": messages, "max_tokens": kwargs.get("max_tokens")},
            "started_at_utc": started.isoformat(),
            "reserved_retry_worst_case_cny": str(bound),
            "status": "requested",
        }
        self.calls.append(record)
        self.persist()
        try:
            response = self.gateway.complete(purpose, messages, system=system, **kwargs)
        except Exception as exc:
            record.update(status="gateway_error", error_type=type(exc).__name__, error=str(exc))
            record["finished_at_utc"] = timestamp().isoformat()
            self.persist()
            raise
        usage = response.usage
        pricing_context = dict(self.gateway.estimate_context(), price_period="peak", cache_mode="implicit",
                               finished_at=timestamp().isoformat())
        price = self.gateway.price_catalog.cost(self.gateway.get_configuration()["provider_id"],
                                                self.gateway.get_configuration()["model"], usage,
                                                at=started, context=pricing_context)
        record.update({
            "status": "completed",
            "response": response.text,
            "usage": {"input_tokens": usage.input_tokens, "cached_input_tokens": usage.cached_input_tokens,
                      "uncached_input_tokens": usage.uncached_input_tokens, "output_tokens": usage.output_tokens,
                      "reasoning_tokens": usage.reasoning_tokens,
                      "unreported_fields": usage.raw.get("_mentorflow", {}).get("unreported_fields", [])},
            "actual_model": response.model,
            "finish_reason": response.finish_reason,
            "cost_cny": str(price.amount) if price else None,
            "finished_at_utc": timestamp().isoformat(),
        })
        self.persist()
        return response


class _BudgetExceeded(RuntimeError):
    pass


def _classroom_instance(gateway):
    classroom = object.__new__(Classroom)
    classroom.services = SimpleNamespace(gateway=gateway)
    classroom._local = threading.local()
    return classroom


def run() -> dict:
    if Path.cwd().resolve() != REPO.resolve():
        raise RuntimeError(f"Run from the classroom worktree: {REPO}")
    if not EXPECTATION.is_file() or not EXPECTATION.read_text(encoding="utf-8").strip():
        raise RuntimeError("Create and review expectation.md before any live call")
    output = ROOT / "classroom_generation.json"
    if output.exists():
        raise RuntimeError(f"Refusing to overwrite existing evidence: {output}")
    real = live_factory(PRESET_ROOT, ACCOUNT)(SimpleNamespace(data_root=ROOT / "gateway-data"))
    try:
        config = real.get_configuration()
        if config["provider_id"] != "deepseek" or config["model"] not in ("deepseek-v4-flash", "deepseek-flash"):
            raise RuntimeError("Existing preset is not DeepSeek Flash; no request sent")
        report = {"mode": "six_deidentified_questions_classroom_generate_teacher",
                  "model": config["model"], "provider": config["provider_id"],
                  "expectation": EXPECTATION.read_text(encoding="utf-8"),
                  "quality_self_evaluation": False, "calls": [], "answers": []}
        capture = _CaptureGateway(real, lambda: _write(output, report))
        classroom = _classroom_instance(capture)
        records = []
        report["calls"] = capture.calls
        report["answers"] = records
        _write(output, report)
        with patch.dict(gateway_module.MODEL_MAX_OUTPUT, {config["model"]: OUTPUT_CAP}):
            for sample in live_compare226.SAMPLES:
                sample_id, question, _, _ = sample
                ctx = live_compare226._context_for(sample, current=True)
                system, messages = teacher_agent.teacher_request(ctx)
                visible = []
                start = len(capture.calls)
                answer_record = {"sample_id": sample_id, "question": question,
                                 "model_call_indexes": list(range(start, len(capture.calls)))}
                try:
                    teaching, _, accepted = classroom._generate_teacher(
                        "teacher228-synthetic-plan", ctx, (system, messages), threading.Event(), visible.append)
                    answer_record.update({"status": "completed", "basis": teaching.answer_basis,
                        "cited_chunks": list(teaching.cited_chunks), "speech": teaching.speech,
                        "accepted_response": accepted, "stream_events": visible})
                except _BudgetExceeded as exc:
                    answer_record.update({"status": "budget_stopped", "error": str(exc)})
                    records.append(answer_record)
                    _write(output, report)
                    break
                except Exception as exc:
                    # Keep the response, rewrite attempts and billing evidence,
                    # then continue to each independent synthetic question.
                    answer_record.update({"status": "generation_or_parse_error",
                        "error_type": type(exc).__name__,
                        "error_code": getattr(exc, "reason", getattr(exc, "code", None)),
                        "error": str(exc), "stream_events": visible})
                answer_record['model_call_indexes'] = list(range(start, len(capture.calls)))
                records.append(answer_record)
                _write(output, report)
        report.update({
            "call_count": len(capture.calls),
            "reserved_retry_worst_case_cny": str(capture.reserved),
            "actual_peak_price_cny": (str(sum((Decimal(c["cost_cny"]) for c in capture.calls if c["cost_cny"]), Decimal(0)))
                                      if all(c["cost_cny"] for c in capture.calls) else None),
            "unpriced_call_count": sum(c["cost_cny"] is None for c in capture.calls),
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        _write(output, report)
        return report
    finally:
        real.close()


class _FakeGateway:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, purpose, messages, *, system=None, on_text=None, **kwargs):
        text = self.replies.pop(0)
        self.calls.append({"purpose": purpose, "system": system, "messages": messages,
                           "max_tokens": kwargs.get("max_tokens"), "response": text})
        if on_text:
            on_text(text)
        return SimpleNamespace(text=text)


def dry_check() -> dict:
    """Exercise Classroom generation, parsing, rewrite and supplement label locally."""
    output = ROOT / "dry_check_final.json"
    if output.exists():
        raise RuntimeError(f"Refusing to overwrite existing dry-check result: {output}")
    classroom = None
    records = []
    for sample in live_compare226.SAMPLES:
        sample_id, question, _, passages = sample
        ctx = live_compare226._context_for(sample, current=True)
        source_chunks = list(passages) or list(ctx["excerpts"])
        alias_by_chunk = {chunk: alias for alias, chunk in teacher_agent.citation_map(ctx).items()}
        if sample_id in ("no_material_jvm", "no_material_resume"):
            basis = "supplement"
            raw = "【依据】supplement【/依据】\n==这是明确标记为补充的信息。=="
        elif sample_id == "plan_material_golden_set":
            # First response violates the newly tightened unsupported-claim
            # guard; Classroom must perform its one existing rewrite.
            basis = "material"
            alias = alias_by_chunk[source_chunks[0]]
            invalid = f"【依据】material:{alias}【/依据】\n==没有标签就只能整体看模型结果。==[{alias}]"
            valid = f"【依据】material:{alias}【/依据】\n==场景标签便于检查模型输出是否覆盖预期行为。==[{alias}]"
            replies = [invalid, valid]
            fake = _FakeGateway(replies)
            classroom = _classroom_instance(fake)
            system, messages = teacher_agent.teacher_request(ctx)
            visible = []
            teaching, _, _ = classroom._generate_teacher("teacher228-dry-plan", ctx,
                (system, messages), threading.Event(), visible.append)
            if len(fake.calls) != 2 or "只能整体看" in teaching.speech:
                raise AssertionError("Golden Set rewrite fixture did not reject the unsupported claim")
            records.append({"sample_id": sample_id, "status": "passed", "call_count": 2,
                            "basis": teaching.answer_basis, "cited_chunks": list(teaching.cited_chunks),
                            "speech": teaching.speech, "rewrite_reset": any(v == "\x00" for v in visible)})
            continue
        else:
            basis = "material"
            alias = alias_by_chunk[source_chunks[0]]
            raw = f"【依据】material:{alias}【/依据】\n==以职责明确且能单独检查作为类比重点。==[{alias}]"
        if basis == "supplement":
            raw = "【依据】supplement【/依据】\n==这里给出对问题的简短常识补充。=="
        else:
            alias = alias_by_chunk[source_chunks[0]]
            raw = f"【依据】material:{alias}【/依据】\n=={ctx['excerpts'][source_chunks[0]]}==[{alias}]"
        fake = _FakeGateway([raw])
        classroom = _classroom_instance(fake)
        system, messages = teacher_agent.teacher_request(ctx)
        visible = []
        teaching, _, _ = classroom._generate_teacher("teacher228-dry-plan", ctx,
            (system, messages), threading.Event(), visible.append)
        if basis == "supplement" and not teaching.speech.startswith("讲义里没有，下面是补充"):
            raise AssertionError("Supplement was not host-labelled")
        if basis == "material" and source_chunks[0] not in teaching.cited_chunks:
            raise AssertionError("Material response lost its source")
        records.append({"sample_id": sample_id, "status": "passed", "call_count": 1,
                        "basis": teaching.answer_basis, "cited_chunks": list(teaching.cited_chunks),
                        "speech": teaching.speech})
    result = {"mode": "offline_fake_gateway_classroom_generation", "model_requests": 0,
              "quality_self_evaluation": False, "answers": records}
    _write(output, result)
    return result


if __name__ == "__main__":
    if sys.argv[1:] == ["--dry-check"]:
        try:
            result = dry_check()
        except Exception as exc:
            print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
            raise SystemExit(2)
        print(json.dumps({k: v for k, v in result.items() if k not in ("calls", "answers")},
                         ensure_ascii=False, indent=2))
        raise SystemExit(0)
    if sys.argv[1:] != ['--live']:
        raise SystemExit("Use --dry-check or the explicitly authorized --live run.")
    try:
        result = run()
    except Exception as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({k: v for k, v in result.items() if k not in ("calls", "answers")},
                     ensure_ascii=False, indent=2))

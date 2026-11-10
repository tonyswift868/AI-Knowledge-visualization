"""355-MF05: local JavaGuide retrieval capacity baseline.

This is a measurement test, not a pass/fail capacity claim. It uses the
existing rag154 HTTP fixture and exact local vector backend, never calls a
paid model, and writes only task-local JSON evidence.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any

import pytest


DATA_DIR = Path(os.environ["RAG355_DATA"]) if os.environ.get("RAG355_DATA") else None
if DATA_DIR is not None:
    os.environ.setdefault("RAG154_TMP", str(DATA_DIR.parent / "rag"))

from support_rag154 import API, _seed_subject, chunk_payload, rag154_backends, rag154_headers


pytestmark = pytest.mark.skipif(DATA_DIR is None, reason="RAG355_DATA is not set")
EVAL_PATH = Path(os.environ.get("RAG355_EVAL_SET", str(DATA_DIR.parent / "eval-set.json"))) if DATA_DIR else None
RESULT_PATH = Path(os.environ.get("RAG355_RESULTS", str(DATA_DIR.parent / "capacity-results.json"))) if DATA_DIR else None
DOC_ID_PREFIX = "eval355"
SUBJECTS = ["user-355-a", "user-355-b", "user-355-c", "user-355-d", "user-355-e"]
VOLUMES = (1, 3, 10)
CONCURRENCIES = (1, 5, 20)
MAX_SCENARIO_SECONDS = 30 * 60


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = (len(ordered) - 1) * percentile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 2)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def _load_inputs() -> tuple[dict, list[dict], list[dict]]:
    assert DATA_DIR is not None and EVAL_PATH is not None
    corpus = json.loads((DATA_DIR / "javaguide-chunks.json").read_text(encoding="utf-8"))
    questions = json.loads(EVAL_PATH.read_text(encoding="utf-8"))["questions"]
    chunks = list(corpus["chunks"])
    assert len(chunks) == 1006
    assert [int(row["seq"]) for row in chunks] == list(range(len(chunks)))
    assert len(questions) == 20
    return corpus, chunks, list(questions)


def _payload_chunks(chunks: list[dict]) -> list[dict]:
    result = []
    for row in chunks:
        section = row.get("section")
        if isinstance(section, list):
            section = " / ".join(str(value) for value in section)
        result.append({"seq": int(row["seq"]), "text": str(row["text"]),
                       "page": row.get("page"), "section": section})
    return result


class _ProcessSampler:
    """Dependency-free Windows process RSS/CPU sampler."""

    def __init__(self, pid: int):
        self.pid = int(pid)
        self.samples: list[tuple[float, int, float]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="capacity355-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        rss_values = [sample[1] for sample in self.samples]
        logical = max(1, os.cpu_count() or 1)
        cpu_peak = None
        if len(self.samples) >= 2:
            cpu_peak = 0.0
            for previous, current in zip(self.samples, self.samples[1:]):
                elapsed = current[0] - previous[0]
                if elapsed > 0:
                    cpu_peak = max(cpu_peak, max(0.0, current[2] - previous[2]) / elapsed / logical * 100)
        return {"rss_peak_bytes": max(rss_values) if rss_values else None,
                "cpu_peak_percent": round(cpu_peak, 2) if cpu_peak is not None else None,
                "sample_count": len(self.samples), "logical_processors": logical}

    def _run(self) -> None:
        command = ("$p=Get-Process -Id {pid} -ErrorAction Stop; "
                   "'{{0}}|{{1}}' -f $p.WorkingSet64,$p.TotalProcessorTime.TotalSeconds").format(pid=self.pid)
        while not self._stop.is_set():
            try:
                result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                        capture_output=True, text=True, timeout=2)
                raw = result.stdout.strip()
                if result.returncode == 0 and "|" in raw:
                    rss, cpu = raw.split("|", 1)
                    self.samples.append((time.perf_counter(), int(rss), float(cpu)))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self._stop.wait(.25)


def _upload(backends: dict, subject: str, doc_id: str, chunks: list[dict], title: str) -> dict:
    started = time.perf_counter()
    response = backends["client"].post(
        f"{API}/documents/{doc_id}/chunks",
        headers=rag154_headers(backends, subject=subject, request_id=f"capacity355-upload-{doc_id}"),
        json=chunk_payload(chunks, title=title, source="https://github.com/snailclimb/javaguide"),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {"document_id": doc_id, "subject": subject, "status_code": response.status_code,
            "elapsed_ms": round(elapsed_ms, 2), "chunks": len(chunks),
            "per_chunk_ms": round(elapsed_ms / len(chunks), 4),
            "error": None if 200 <= response.status_code < 300 else response.text[:300]}


def _query_one(backends: dict, subject: str, document_ids: list[str], question: dict, request_id: str) -> dict:
    started = time.perf_counter()
    try:
        response = backends["client"].post(
            f"{API}/search", headers=rag154_headers(backends, subject=subject, request_id=request_id),
            json={"document_ids": document_ids, "query": question["question"], "k": 8},
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        value = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        passages = value.get("passages", []) if isinstance(value, dict) else []
        return {"question_id": question["id"], "subject": subject, "status_code": response.status_code,
                "elapsed_ms": round(elapsed_ms, 2), "top8_count": len(passages),
                "error": None if 200 <= response.status_code < 300 else response.text[:300]}
    except Exception as exc:
        return {"question_id": question["id"], "subject": subject, "status_code": None,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "top8_count": 0, "error": type(exc).__name__}


def _run_batch(backends: dict, assignments: dict[str, list[str]], questions: list[dict], concurrency: int) -> dict:
    active = [subject for subject, docs in assignments.items() if docs]
    jobs = [(active[index % len(active)], questions[index]) for index in range(len(questions))]
    started = time.perf_counter()
    rows = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(_query_one, backends, subject, assignments[subject], question,
                                   f"capacity355-{time.time_ns()}-{index}")
                   for index, (subject, question) in enumerate(jobs)]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: row["question_id"])
    timings = [row["elapsed_ms"] for row in rows]
    errors = sum(row["error"] is not None or not (200 <= (row["status_code"] or 0) < 300) for row in rows)
    return {"query_count": len(rows), "success_count": len(rows) - errors, "error_count": errors,
            "error_rate": round(errors / len(rows), 4) if rows else None,
            "p50_ms": _percentile(timings, .50), "p95_ms": _percentile(timings, .95),
            "p99_ms": _percentile(timings, .99), "batch_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "query_vectorization_ms": None, "nearest_retrieval_ms": None,
            "timing_note": "HTTP API exposes no embedding or nearest-neighbor sub-timers; only /search end-to-end is observable.",
            "rows": rows}


def _distribute(subjects: list[str], copies: int, mode: str, volume: int) -> dict[str, list[str]]:
    selected = subjects[:1] if mode == "one_account" else subjects
    assignments = {subject: [] for subject in selected}
    for index in range(copies):
        subject = selected[index % len(selected)]
        assignments[subject].append(f"{DOC_ID_PREFIX}-v{volume}-{mode}-a{selected.index(subject)}-d{index}")
    return assignments


def test_rag355_capacity_baseline(rag154_backends):
    corpus, chunks, questions = _load_inputs()
    payload_chunks = _payload_chunks(chunks)
    for subject in SUBJECTS:
        _seed_subject(rag154_backends["app_url"], subject)

    output: dict[str, Any] = {
        "task": "355-MF05", "backend": "rag154 HTTP + local-test ByteaExactVectorStore (exact cosine)",
        "corpus": {"source_id": corpus.get("source_id"), "document_id": corpus.get("document_id"),
                   "base_chunks": len(payload_chunks), "top_k": 8},
        "timing_contract": {"query_vectorization_ms": None, "nearest_retrieval_ms": None,
                             "reason": "Existing /search exposes only passages; no product code was changed to add timers."},
        "pgvector": {"tested": False, "reason": "No local PostgreSQL/pgvector and task forbids system installation/download."},
        "scenarios": [], "stopped_early": False, "stop_reason": None,
    }
    process_pid = rag154_backends["endpoint"]["process"].pid
    for volume in VOLUMES:
        for mode in ("one_account", "five_accounts"):
            scenario_started = time.perf_counter()
            assignments = _distribute(SUBJECTS, volume, mode, volume)
            scenario = {"volume_factor": volume, "account_mode": mode, "account_count": len(assignments),
                        "active_account_count": sum(bool(value) for value in assignments.values()),
                        "distribution": {key: len(value) for key, value in assignments.items()},
                        "documents": [], "groups": [], "resource_scope": "per group sampler"}
            upload_sampler = _ProcessSampler(process_pid)
            upload_sampler.start()
            for subject, doc_ids in assignments.items():
                for doc_id in doc_ids:
                    uploaded = _upload(rag154_backends, subject, doc_id, payload_chunks,
                                       f"JavaGuide 355 v{volume} {mode} {doc_id}")
                    scenario["documents"].append(uploaded)
                    assert 200 <= uploaded["status_code"] < 300, uploaded
            scenario["upload_resource_peak"] = upload_sampler.stop()
            scenario["upload_elapsed_ms"] = round(sum(item["elapsed_ms"] for item in scenario["documents"]), 2)
            scenario["upload_chunks"] = sum(item["chunks"] for item in scenario["documents"])
            scenario["upload_per_chunk_ms"] = round(scenario["upload_elapsed_ms"] / scenario["upload_chunks"], 4)

            for concurrency in CONCURRENCIES:
                for phase in ("cold_or_first", "hot_replay"):
                    if time.perf_counter() - scenario_started > MAX_SCENARIO_SECONDS:
                        output["stopped_early"] = True
                        output["stop_reason"] = f"{volume}x/{mode} exceeded 30 minutes"
                        break
                    sampler = _ProcessSampler(process_pid)
                    sampler.start()
                    batch = _run_batch(rag154_backends, assignments, questions, concurrency)
                    batch.update({"volume_factor": volume, "account_mode": mode,
                                  "active_account_count": scenario["active_account_count"],
                                  "concurrency": concurrency, "phase": phase,
                                  "literal_cold": bool(concurrency == 1 and phase == "cold_or_first"),
                                  "resource_peak": sampler.stop()})
                    scenario["groups"].append(batch)
                if output["stopped_early"]:
                    break
            output["scenarios"].append(scenario)
            if output["stopped_early"]:
                break
        if output["stopped_early"]:
            break

    assert RESULT_PATH is not None
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    assert output["scenarios"], "No capacity scenarios ran"

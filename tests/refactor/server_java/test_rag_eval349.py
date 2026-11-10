"""349-MF05: retrieval evaluation on the user's JavaGuide chunks.

The fixture is the existing rag154 HTTP service with its local exact-vector
backend.  No product code or user data is changed; RAG349_DATA points at a
task-local copy of the corpus.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest


DATA_DIR = Path(os.environ["RAG349_DATA"]) if os.environ.get("RAG349_DATA") else None
if DATA_DIR is not None:
    # Keep the existing rag154 fixture's disposable JVM/Postgres files inside
    # this task's scratch directory when the caller did not set it explicitly.
    os.environ.setdefault("RAG154_TMP", str(DATA_DIR.parent / "rag"))

from support_rag154 import API, chunk_payload, rag154_backends, rag154_headers


pytestmark = pytest.mark.skipif(DATA_DIR is None, reason="RAG349_DATA is not set")
EVAL_SET = Path(os.environ.get("RAG349_EVAL_SET", str(DATA_DIR.parent / "eval-set.json"))) if DATA_DIR else None
RESULT_PATH = Path(os.environ.get("RAG349_RESULTS", str(DATA_DIR.parent / "retrieval-results.json"))) if DATA_DIR else None
DOC_ID = "eval349-javaguide"


def _read_inputs() -> tuple[dict, list[dict], list[dict]]:
    assert DATA_DIR is not None and EVAL_SET is not None
    corpus = json.loads((DATA_DIR / "javaguide-chunks.json").read_text(encoding="utf-8"))
    eval_set = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    chunks = list(corpus["chunks"])
    assert len(chunks) == 1006, f"JavaGuide chunk count changed: {len(chunks)}"
    assert [int(row["seq"]) for row in chunks] == list(range(len(chunks)))
    known = {int(row["seq"]) for row in chunks}
    for question in eval_set["questions"]:
        assert set(question["supporting_seqs"]).issubset(known)
    return corpus, chunks, list(eval_set["questions"])


def _upload_chunks(backends: dict, corpus: dict, chunks: list[dict]):
    payload_chunks = []
    for row in chunks:
        section = row.get("section")
        if isinstance(section, list):
            section = " / ".join(str(value) for value in section)
        payload_chunks.append({
            "seq": int(row["seq"]),
            "text": str(row["text"]),
            "page": row.get("page"),
            "section": section,
        })
    return backends["client"].post(
        f"{API}/documents/{DOC_ID}/chunks",
        headers=rag154_headers(backends, request_id="eval349-upload"),
        json=chunk_payload(
            payload_chunks,
            title=corpus.get("rag_document", {}).get("title", "Snailclimb/JavaGuide"),
            source="https://github.com/snailclimb/javaguide",
        ),
    )


def _bigrams(value: str) -> set[str]:
    normalized = "".join(str(value).lower().split())
    return {normalized[index:index + 2] for index in range(max(0, len(normalized) - 1))}


def _miss_reason(question: dict, top5: list[int], top8: list[int], corpus_by_seq: dict[int, str]) -> str:
    expected = set(question["supporting_seqs"])
    if not expected:
        return "资料无答案题（不计入检索命中）"
    if expected.intersection(top8):
        return "排序被相似段挤下去"
    neighbours = {seq + delta for seq in expected for delta in (-1, 1)}
    if neighbours.intersection(top8):
        return "切段把答案切断了"
    expected_text = " ".join(corpus_by_seq[seq] for seq in expected)
    overlap = len(_bigrams(question["question"]).intersection(_bigrams(expected_text)))
    if overlap >= 2:
        return "向量没认出同义说法"
    return "其他"


def _metric(rows: list[dict], key: str) -> dict[str, float | int | None]:
    eligible = [row for row in rows if row["supporting_seqs"]]
    if not eligible:
        return {"questions": 0, "hit@1": None, "hit@3": None, "hit@5": None, "mrr": None}
    return {
        "questions": len(eligible),
        "hit@1": round(sum(row["first_rank"] is not None and row["first_rank"] <= 1 for row in eligible) / len(eligible), 4),
        "hit@3": round(sum(row["first_rank"] is not None and row["first_rank"] <= 3 for row in eligible) / len(eligible), 4),
        "hit@5": round(sum(row["first_rank"] is not None and row["first_rank"] <= 5 for row in eligible) / len(eligible), 4),
        "mrr": round(sum((1 / row["first_rank"]) if row["first_rank"] else 0 for row in eligible) / len(eligible), 4),
    }


def test_rag349_user_javaguide_retrieval(rag154_backends):
    corpus, chunks, questions = _read_inputs()
    uploaded = _upload_chunks(rag154_backends, corpus, chunks)
    assert uploaded.status_code == 200, uploaded.text

    corpus_by_seq = {int(row["seq"]): str(row["text"]) for row in chunks}
    results: list[dict] = []
    for index, question in enumerate(questions):
        started = time.perf_counter()
        response = rag154_backends["client"].post(
            f"{API}/search",
            headers=rag154_headers(rag154_backends, request_id=f"eval349-q{index:02d}"),
            json={"document_ids": [DOC_ID], "query": question["question"], "k": 8},
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        assert response.status_code == 200, response.text
        passages = response.json().get("passages", [])
        top8 = [int(item["seq"]) for item in passages]
        top5 = top8[:5]
        expected = set(question["supporting_seqs"])
        rank = next((position for position, seq in enumerate(top8, start=1) if seq in expected), None)
        results.append({
            "id": question["id"],
            "type": question["type"],
            "question": question["question"],
            "supporting_seqs": sorted(expected),
            "top8_seqs": top8,
            "top8_scores": [item.get("score") for item in passages],
            "first_rank": rank,
            "hit@1": bool(rank and rank <= 1),
            "hit@3": bool(rank and rank <= 3),
            "hit@5": bool(rank and rank <= 5),
            "elapsed_ms": elapsed_ms,
            "miss_reason": None if rank and rank <= 5 else _miss_reason(question, top5, top8, corpus_by_seq),
        })

    overall = _metric(results, "overall")
    by_type = {}
    for question_type in sorted({row["type"] for row in results}):
        by_type[question_type] = _metric([row for row in results if row["type"] == question_type], question_type)
    output = {
        "task": "349-MF05",
        "backend": "rag154 HTTP API + local-test ByteaExactVectorStore (exact cosine)",
        "document_id": DOC_ID,
        "corpus": {"source_id": corpus.get("source_id"), "document_id": corpus.get("document_id"), "chunks": len(chunks)},
        "k_returned": 8,
        "metrics": {"overall": overall, "by_type": by_type},
        "misses": [row for row in results if row["miss_reason"]],
        "results": results,
    }
    assert RESULT_PATH is not None
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

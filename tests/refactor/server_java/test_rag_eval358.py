"""358-MF07: retrieval evaluation on the user's JavaGuide chunks.

The fixture is the existing rag154 HTTP service with its local exact-vector
backend.  No product code or user data is changed; RAG349_DATA points at a
task-local copy of the corpus.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import statistics

import pytest


DATA_DIR = Path(os.environ["RAG349_DATA"]) if os.environ.get("RAG349_DATA") else None
if DATA_DIR is not None:
    # Keep the existing rag154 fixture's disposable JVM/Postgres files inside
    # this task's scratch directory when the caller did not set it explicitly.
    os.environ.setdefault("RAG154_TMP", str(DATA_DIR.parent / "rag"))

from support_rag154 import API, chunk_payload, rag154_backends, rag154_headers


pytestmark = pytest.mark.skipif(DATA_DIR is None, reason="RAG349_DATA is not set")
EVAL_SET = Path(os.environ.get("RAG358_EVAL_SET", str(DATA_DIR.parent / "eval-set.json"))) if DATA_DIR else None
RESULT_PATH = Path(os.environ.get("RAG358_RESULTS", str(DATA_DIR.parent / "retrieval-results.json"))) if DATA_DIR else None
DOC_ID = "eval358-javaguide"


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
        headers=rag154_headers(backends, request_id="eval358-upload"),
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


def test_rag358_user_javaguide_retrieval(rag154_backends):
    corpus, chunks, questions = _read_inputs()
    uploaded = _upload_chunks(rag154_backends, corpus, chunks)
    assert uploaded.status_code == 200, uploaded.text

    corpus_by_seq = {int(row["seq"]): str(row["text"]) for row in chunks}
    results: list[dict] = []
    for index, question in enumerate(questions):
        started = time.perf_counter()
        response = rag154_backends["client"].post(
            f"{API}/search",
            headers=rag154_headers(rag154_backends, request_id=f"eval358-q{index:02d}"),
            json={"document_ids": [DOC_ID], "query": question["question"], "k": 8},
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        assert response.status_code == 200, response.text
        passages = response.json().get("passages", [])
        top8 = [int(item["seq"]) for item in passages]
        top8_scores = [item.get("score") for item in passages]
        top20_response = rag154_backends["client"].post(
            f"{API}/search",
            headers=rag154_headers(rag154_backends, request_id=f"eval358-q{index:02d}-top20"),
            json={"document_ids": [DOC_ID], "query": question["question"], "k": 20},
        )
        assert top20_response.status_code == 200, top20_response.text
        top20_passages = top20_response.json().get("passages", [])
        top20 = [int(item["seq"]) for item in top20_passages]
        top5 = top8[:5]
        expected = set(question["supporting_seqs"])
        rank = next((position for position, seq in enumerate(top8, start=1) if seq in expected), None)
        results.append({
            "id": question["id"],
            "type": question["type"],
            "question": question["question"],
            "supporting_seqs": sorted(expected),
            "top8_seqs": top8,
            "top20_seqs": top20,
            "top8_scores": top8_scores,
            "top20_scores": [item.get("score") for item in top20_passages],
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
        "task": "358-MF07",
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


def _offline_experiments_legacy_unused() -> None:
    """Derive B/C/D from immutable A results using only stdlib and corpus text."""
    root = Path(os.environ.get("RAG358_ROOT", "."))
    baseline = json.loads((root / "baseline-retrieval-results.json").read_text(encoding="utf-8"))
    corpus = json.loads((root / "data" / "javaguide-chunks.json").read_text(encoding="utf-8"))
    texts = {int(r["seq"]): str(r["text"]) for r in corpus["chunks"]}
    questions = {r["id"]: r for r in json.loads((root / "eval-set-358.json").read_text(encoding="utf-8"))["questions"]}

    def grams(s: str) -> set[str]:
        s = "".join(str(s).lower().split())
        return {s[i:i + 2] for i in range(max(0, len(s) - 1))}

    def kw(q: str, seq: int) -> float:
        a, b = grams(q), grams(texts[seq])
        return len(a & b) / max(1, len(a))

    def rank_for(row: dict, mode: str) -> list[int]:
        seqs = list(map(int, row["top8_seqs"]))
        scores = list(row.get("top8_scores") or [])
        dense = {s: float(scores[i] or 0.0) for i, s in enumerate(seqs)}
        if mode == "A": return seqs
        lexical = sorted(seqs, key=lambda s: (-kw(row["question"], s), seqs.index(s)))
        if mode == "B":
            return sorted(seqs, key=lambda s: (-(dense[s] + .15 * kw(row["question"], s)), seqs.index(s)))
        # RRF uses dense rank and lexical rank; k=60, top-8 candidate lists.
        dr, lr = {s: i + 1 for i, s in enumerate(seqs)}, {s: i + 1 for i, s in enumerate(lexical)}
        return sorted(seqs, key=lambda s: (-(1 / (60 + dr[s]) + 1 / (60 + lr[s])), seqs.index(s)))

    def metrics(rows: list[dict]) -> dict:
        eligible = [r for r in rows if r["supporting_seqs"]]
        def hit(k): return round(sum(bool(set(r["supporting_seqs"]) & set(r["ranked"][:k])) for r in eligible) / len(eligible), 4)
        rr = [1 / next((i for i, s in enumerate(r["ranked"], 1) if s in r["supporting_seqs"]), 999) for r in eligible]
        return {"questions": len(eligible), "hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "mrr": round(sum(rr) / len(rr), 4)}

    variants = {}
    for mode in ("A", "B", "C"):
        rows = []
        for row in baseline["results"]:
            x = dict(row); x["ranked"] = rank_for(row, mode); rows.append(x)
        variants[mode] = rows
    best = max(("A", "B", "C"), key=lambda m: (metrics(variants[m])["hit@5"], metrics(variants[m])["mrr"]))
    rows_d = []
    for row in variants[best]:
        support = set(row["supporting_seqs"])
        window = sorted({n for s in row["ranked"][:8] for n in (s - 1, s, s + 1) if n in texts})
        x = dict(row); x["ranked"] = window; x["window"] = True; rows_d.append(x)
    variants["D"] = rows_d
    eligible = [r for r in baseline["results"] if r["supporting_seqs"]]
    groups = {"同义改写": [r for r in baseline["results"] if "同义" in r["type"]], "跨段": [r for r in baseline["results"] if len(r["supporting_seqs"]) > 1]}
    subgroup = {g: {m: metrics([x for x in variants[m] if x["id"] in {r["id"] for r in rs}]) for m in variants} for g, rs in groups.items()}
    wins = {m: {"better_vs_A": [], "worse_vs_A": []} for m in ("B", "C", "D")}
    def first(r): return next((i for i, s in enumerate(r["ranked"], 1) if s in r["supporting_seqs"]), 999)
    base_by = {r["id"]: r for r in variants["A"]}
    for m in wins:
        for r in variants[m]:
            if first(r) < first(base_by[r["id"]]): wins[m]["better_vs_A"].append(r["id"])
            elif first(r) > first(base_by[r["id"]]): wins[m]["worse_vs_A"].append(r["id"])
    timings = [float(r.get("elapsed_ms") or 0) for r in baseline["results"]]
    out = {"task": "358-MF07", "eval_sha256": "86a4d1da595eacc94665e2ad4133e5b772ddaf740b85938e1248ad5ece136ab0", "best_for_D": best, "metrics": {m: metrics(rows) for m, rows in variants.items()}, "subgroup_metrics": subgroup, "wins_vs_A": wins, "top20_correct_segment_ratio": round(sum(bool(set(r["supporting_seqs"]) & set(r.get("top20_seqs", []))) for r in eligible) / len(eligible), 4), "proxy_latency_ms": {"p50": round(statistics.median(timings), 2), "p95": round(sorted(timings)[max(0, int(len(timings)*.95)-1)], 2)}, "results": {m: [{"id": r["id"], "ranked": r["ranked"], "supporting_seqs": r["supporting_seqs"]} for r in rows] for m, rows in variants.items()}}
    (root / "offline-experiment-results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "offline-experiment-report.md").write_text("# 358-MF07 offline results\n\n" + json.dumps(out["metrics"], ensure_ascii=False, indent=2), encoding="utf-8")


def _offline_experiments() -> None:
    """Offline B/C/D evaluation; A is the immutable HTTP result."""
    import math
    import statistics
    import time
    import unicodedata

    root = Path(os.environ.get("RAG358_ROOT", "."))
    baseline = json.loads((root / "baseline-retrieval-results.json").read_text(encoding="utf-8"))
    corpus = json.loads((root / "data" / "javaguide-chunks.json").read_text(encoding="utf-8"))
    eval_set = json.loads((root / "eval-set-358.json").read_text(encoding="utf-8"))
    texts = {int(row["seq"]): str(row["text"]) for row in corpus["chunks"]}
    types = {row["id"]: row["type"] for row in eval_set["questions"]}

    def tokens(value: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", str(value)).lower()
        chars = [char for char in normalized if not char.isspace() and not unicodedata.category(char).startswith("P") and unicodedata.category(char) != "So"]
        return [chars[index] + chars[index + 1] for index in range(max(0, len(chars) - 1))]

    tokenized = {seq: tokens(text) for seq, text in texts.items()}
    total_docs = len(tokenized)
    average_length = sum(len(value) for value in tokenized.values()) / max(1, total_docs)
    document_frequency: dict[str, int] = {}
    for value in tokenized.values():
        for token in set(value):
            document_frequency[token] = document_frequency.get(token, 0) + 1

    def bm25(query: str, seq: int, k1: float = 1.2, b: float = 0.75) -> float:
        query_tokens = tokens(query)
        frequencies: dict[str, int] = {}
        for token in tokenized[seq]:
            frequencies[token] = frequencies.get(token, 0) + 1
        score = 0.0
        length = len(tokenized[seq])
        for token in query_tokens:
            term_frequency = frequencies.get(token, 0)
            if not term_frequency:
                continue
            df = document_frequency.get(token, 0)
            inverse_frequency = math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))
            denominator = term_frequency + k1 * (1.0 - b + b * length / max(1.0, average_length))
            score += inverse_frequency * (term_frequency * (k1 + 1.0) / denominator)
        return score

    def metrics(rows: list[dict]) -> dict:
        eligible = [row for row in rows if row["supporting_seqs"]]
        ranks = [next((index for index, seq in enumerate(row["ranked"], 1) if seq in set(row["supporting_seqs"])), None) for row in eligible]
        return {
            "questions": len(eligible),
            "hit@1": round(sum(rank is not None and rank <= 1 for rank in ranks) / len(eligible), 4),
            "hit@3": round(sum(rank is not None and rank <= 3 for rank in ranks) / len(eligible), 4),
            "hit@5": round(sum(rank is not None and rank <= 5 for rank in ranks) / len(eligible), 4),
            "mrr": round(sum((1 / rank) if rank else 0.0 for rank in ranks) / len(eligible), 4),
        }

    def context_metrics(rows: list[dict]) -> dict:
        eligible = [row for row in rows if row["supporting_seqs"]]
        hits = [set(row["supporting_seqs"]).issubset(set(row["context"])) for row in eligible]
        return {"questions": len(hits), "all_supporting_segments_in_context": round(sum(hits) / len(hits), 4), "all_segments_count": sum(hits)}

    def rank_for(source: dict, mode: str, alpha: float = 0.15) -> list[int]:
        dense8 = [int(seq) for seq in source["top8_seqs"]]
        dense20 = [int(seq) for seq in source.get("top20_seqs", dense8)]
        dense_scores = {seq: float((source.get("top8_scores") or [0.0] * len(dense8))[index] or 0.0) for index, seq in enumerate(dense8)}
        if mode == "A":
            return dense8
        lexical_scores = {seq: bm25(source["question"], seq) for seq in texts}
        lexical = sorted(texts, key=lambda seq: (-lexical_scores[seq], seq))
        if mode == "B":
            maximum = max((lexical_scores[seq] for seq in dense8), default=0.0)
            normalized = {seq: lexical_scores[seq] / maximum if maximum else 0.0 for seq in dense8}
            return sorted(dense8, key=lambda seq: (-(dense_scores[seq] + alpha * normalized[seq]), dense8.index(seq)))
        dense_rank = {seq: index + 1 for index, seq in enumerate(dense20)}
        lexical_rank = {seq: index + 1 for index, seq in enumerate(lexical[:20])}
        candidates = list(dict.fromkeys(dense20[:20] + lexical[:20]))
        return sorted(candidates, key=lambda seq: (-(1 / (60 + dense_rank.get(seq, 100000)) + 1 / (60 + lexical_rank.get(seq, 100000))), candidates.index(seq)))[:8]

    def make_rows(mode: str, alpha: float = 0.15) -> tuple[list[dict], list[float]]:
        rows, timings = [], []
        for source in baseline["results"]:
            started = time.perf_counter()
            ranked = rank_for(source, mode, alpha)
            context = ranked[:8]
            rows.append({"id": source["id"], "type": types[source["id"]], "supporting_seqs": list(source["supporting_seqs"]), "ranked": ranked, "context": context, "context_chars": sum(len(texts[seq]) for seq in context), "base_context_chars": sum(len(texts[seq]) for seq in source["top8_seqs"])})
            timings.append((time.perf_counter() - started) * 1000)
        return rows, timings

    variants, timings = {}, {}
    for mode in ("A", "B", "C"):
        variants[mode], timings[mode] = make_rows(mode)
    best = max(("A", "B", "C"), key=lambda mode: (metrics(variants[mode])["hit@5"], metrics(variants[mode])["mrr"]))

    d_rows, d_timings = [], []
    for row in variants[best]:
        started = time.perf_counter()
        context = []
        for seq in row["ranked"][:8]:
            for neighbour in (seq - 1, seq, seq + 1):
                if neighbour in texts and neighbour not in context:
                    context.append(neighbour)
        d_row = dict(row)
        d_row["ranked"], d_row["context"] = context, context
        d_row["context_chars"] = sum(len(texts[seq]) for seq in context)
        d_row["base_context_chars"] = sum(len(texts[seq]) for seq in row["ranked"][:8])
        d_rows.append(d_row)
        d_timings.append((time.perf_counter() - started) * 1000)
    variants["D"], timings["D"] = d_rows, d_timings

    def grouped(rows: list[dict]) -> dict:
        by_type = {kind: metrics([row for row in rows if row["type"] == kind]) for kind in sorted({row["type"] for row in rows})}
        return {"overall": metrics(rows), "by_type": by_type, "context": context_metrics(rows)}

    alpha_grid = {}
    for alpha in (0.05, 0.10, 0.15, 0.20):
        alpha_rows, _ = make_rows("B", alpha)
        alpha_grid[str(alpha)] = metrics(alpha_rows)

    eligible = [row for row in baseline["results"] if row["supporting_seqs"]]
    top20_hits = [bool(set(row["supporting_seqs"]) & set(row.get("top20_seqs", []))) for row in eligible]
    top20_by_type = {}
    for kind in sorted({types[row["id"]] for row in eligible}):
        values = [value for row, value in zip(eligible, top20_hits) if types[row["id"]] == kind]
        top20_by_type[kind] = {"questions": len(values), "hit": round(sum(values) / len(values), 4), "count": sum(values)}

    comparisons = {}
    for mode in ("B", "C"):
        comparisons[mode] = {"improved_hit@5_vs_A": [], "regressed_hit@5_vs_A": []}
        for row, base in zip(variants[mode], variants["A"]):
            current = bool(set(row["supporting_seqs"]) & set(row["context"][:5]))
            original = bool(set(base["supporting_seqs"]) & set(base["context"][:5]))
            if current and not original:
                comparisons[mode]["improved_hit@5_vs_A"].append(row["id"])
            elif original and not current:
                comparisons[mode]["regressed_hit@5_vs_A"].append(row["id"])
    comparisons["D_all_segments_vs_A"] = {"improved": [], "regressed": []}
    for row, base in zip(variants["D"], variants["A"]):
        current = set(row["supporting_seqs"]).issubset(set(row["context"]))
        original = set(base["supporting_seqs"]).issubset(set(base["context"]))
        if current and not original:
            comparisons["D_all_segments_vs_A"]["improved"].append(row["id"])
        elif original and not current:
            comparisons["D_all_segments_vs_A"]["regressed"].append(row["id"])

    def pct(values: list[float], fraction: float) -> float:
        values = sorted(values)
        position = (len(values) - 1) * fraction
        low, high = math.floor(position), math.ceil(position)
        return round(values[low] if low == high else values[low] + (values[high] - values[low]) * (position - low), 3)

    timing_report = {}
    for mode in ("A", "B", "C", "D"):
        values = [float(row["elapsed_ms"]) for row in baseline["results"]] if mode == "A" else timings[mode]
        timing_report[mode] = {"kind": "Java HTTP fixture request" if mode == "A" else "Python offline proxy", "p50_ms": pct(values, 0.50), "p95_ms": pct(values, 0.95), "samples": len(values)}

    serialized = {}
    for mode, rows in variants.items():
        serialized[mode] = [{"id": row["id"], "type": row["type"], "supporting_seqs": row["supporting_seqs"], "ranked": row["ranked"], "context": row["context"], "context_chars": row["context_chars"], "base_context_chars": row["base_context_chars"], "extra_context_chars": row["context_chars"] - row["base_context_chars"], "all_supporting_segments_in_context": set(row["supporting_seqs"]).issubset(set(row["context"]))} for row in rows]

    window_extras = [row["context_chars"] - row["base_context_chars"] for row in variants["D"]]
    window_ratios = [(row["context_chars"] - row["base_context_chars"]) / max(1, row["base_context_chars"]) for row in variants["D"]]
    window_overhead = {"average_extra_chars": round(sum(window_extras) / len(window_extras), 2), "median_extra_chars": round(statistics.median(window_extras), 2), "average_extra_ratio": round(sum(window_ratios) / len(window_ratios), 4), "max_extra_chars": max(window_extras)}
    out = {"task": "358-MF07", "eval_sha256": "86a4d1da595eacc94665e2ad4133e5b772ddaf740b85938e1248AD5ECE136AB0".lower(), "best_for_D": best, "bm25": {"tokenizer": "NFKC character bigrams", "k1": 1.2, "b": 0.75, "candidate_alpha": 0.15, "rrf_k": 60}, "metrics": {mode: grouped(rows) for mode, rows in variants.items()}, "alpha_grid": alpha_grid, "timing_ms": timing_report, "top20_correct_segment_ratio": {"overall": {"questions": len(top20_hits), "hit": round(sum(top20_hits) / len(top20_hits), 4), "count": sum(top20_hits)}, "by_type": top20_by_type}, "window_overhead": window_overhead, "comparisons": comparisons, "results": serialized}
    (root / "offline-experiment-results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    report = ["# 358-MF07 离线实验结果", "", "题集 SHA-256：86a4d1da595eacc94665e2ad4133e5b772ddaf740b85938e1248ad5ece136ab0", "", f"D 使用 A/B/C 中 hit@5、再以 MRR 排序最好的 {best}。", "", "## 总体", "", "~~~json", json.dumps(out["metrics"], ensure_ascii=False, indent=2), "~~~", "", "## P50/P95", "", "~~~json", json.dumps(timing_report, ensure_ascii=False, indent=2), "~~~", "", "## A 前 20 正确段比例", "", json.dumps(out["top20_correct_segment_ratio"], ensure_ascii=False, indent=2), "", "## alpha 网格（仅参考，结论固定看 .15）", "", json.dumps(alpha_grid, ensure_ascii=False, indent=2), "", "## 逐题输赢", "", json.dumps(comparisons, ensure_ascii=False, indent=2)]
    (root / "offline-experiment-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__" and os.environ.get("RAG358_OFFLINE"):
    _offline_experiments()


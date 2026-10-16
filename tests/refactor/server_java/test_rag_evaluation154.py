"""Manual 20-question retrieval evaluation and 10k pgvector timing for RAG154.

Run alone with RAG154_VECTOR_BACKEND=pgvector and RAG154_PGROOT pointed at
the task-local PostgreSQL installation. This intentionally uses the public
MDN Chinese HTTP/JavaScript reference and makes no LLM calls.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import psycopg
import pytest

from support_rag154 import (API, TASK_ROOT, JAVA_HOME, JAR, TEST_CLASSES, CLASSPATH_FILE,
                            chunk_payload, rag154_backends, rag154_headers)

pytestmark = pytest.mark.skipif(
    os.environ.get("RAG154_RUN_EVALUATION") != "1",
    reason="Manual live-resource benchmark is opt-in; set RAG154_RUN_EVALUATION=1",
)


# Paragraphs are verbatim extracts from official MDN Chinese documentation.
# Their URL, fetched-page SHA-256, and individual paragraph SHA-256 are retained
# with each record so the human reviewer can verify the evaluation corpus.
CORPUS_PATH = Path(__file__).with_name("rag154_mdn_groundtruth154.json")
FACTS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _rss(pid: int) -> int:
    command = ["powershell.exe", "-NoProfile", "-Command", f"(Get-Process -Id {pid}).WorkingSet64"]
    import subprocess
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    return int(result.stdout.strip())


def _upload(backends, docid: str, chunks: list[dict], title: str):
    return backends["client"].post(
        f"{API}/documents/{docid}/chunks",
        headers=rag154_headers(backends, request_id="eval154-" + docid),
        json=chunk_payload(chunks, title=title, source="https://developer.mozilla.org/zh-CN/"),
    )


def _embedding_probe() -> str:
    """Run the already compiled direct adapter probe for per-embedding timings."""
    import subprocess
    temp = TASK_ROOT / "tmp"
    cache = TASK_ROOT / "model-cache"
    home = TASK_ROOT / "java-home"
    for folder in (temp, cache, home):
        folder.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"TMP": str(temp), "TEMP": str(temp), "TMPDIR": str(temp),
                "DJL_CACHE_DIR": str(cache), "ENGINE_CACHE_DIR": str(cache),
                "HF_HOME": str(cache / "huggingface"),
                "HF_HUB_CACHE": str(cache / "huggingface" / "hub")})
    dependencies = CLASSPATH_FILE.read_text(encoding="utf-8").strip()
    # Spring Boot's executable JAR nests application classes under
    # BOOT-INF/classes and is not a flat classpath. Add the Maven classes dir
    # for this direct adapter probe; the application HTTP fixture still uses
    # the executable JAR.
    application_classes = JAR.parent / "classes"
    classpath = os.pathsep.join((str(application_classes), str(TEST_CLASSES), dependencies))
    command = [str(JAVA_HOME / "bin/java.exe"), "-Djava.io.tmpdir=" + str(temp),
               "-Duser.home=" + str(home), "-cp", classpath, "rag154.RagEmbeddingProbe154"]
    process = subprocess.run(command, input="\n", capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=env,
                             cwd=Path(__file__).resolve().parents[3], timeout=240)
    if process.returncode:
        raise AssertionError("RagEmbeddingProbe154 failed: " + process.stdout[-2000:] + process.stderr[-2000:])
    result = next((line for line in process.stdout.splitlines() if line.startswith("RESULT ")), "")
    assert result, process.stdout[-2000:]
    return result


def test_rag154_public_mdn_20q_and_pgvector_10k_benchmark(rag154_backends):
    assert os.environ.get("RAG154_VECTOR_BACKEND") == "pgvector", "This benchmark must use production PgVectorStore."
    client = rag154_backends["client"]
    pid = rag154_backends["endpoint"]["process"].pid
    rss_before = _rss(pid)

    # A cold one-chunk upload measures first model initialization plus vectorization.
    cold = _upload(rag154_backends, "eval154-cold", [
        {"seq": 0, "text": "临时基准内容：" + FACTS[0]["text"], "page": None, "section": FACTS[0]["section"]}
    ], "冷启动基准")
    cold_ms = cold.elapsed.total_seconds() * 1000
    assert cold.status_code == 200, cold.text
    rss_after_first = _rss(pid)
    warm_start = time.perf_counter()
    warm = _upload(rag154_backends, "eval154-warm", [
        {"seq": 0, "text": "单块热身：" + FACTS[1]["text"], "page": None, "section": FACTS[1]["section"]}
    ], "热启动基准")
    warm_ms = (time.perf_counter() - warm_start) * 1000
    assert warm.status_code == 200, warm.text
    direct_probe = _embedding_probe()

    eval_doc = "eval154-mdn-20"
    chunks = [{"seq": item["seq"], "text": item["text"], "page": item["page"], "section": item["section"]}
              for item in FACTS]
    uploaded = _upload(rag154_backends, eval_doc, chunks, "MDN 中文 HTTP 参考评估")
    assert uploaded.status_code == 200, uploaded.text
    results = []
    for expected, fact in enumerate(FACTS):
        question, section, citation = fact["question"], fact["section"], fact["page"]
        start = time.perf_counter()
        response = client.post(f"{API}/search", headers=rag154_headers(rag154_backends,
            request_id=f"eval154-q{expected:02d}"), json={"document_ids": [eval_doc], "query": question, "k": 5})
        elapsed = (time.perf_counter() - start) * 1000
        assert response.status_code == 200, response.text
        passages = response.json()["passages"]
        hits = [int(item["seq"]) for item in passages]
        results.append({"question": question, "expected_id": f"{eval_doc}:{expected}", "expected_section": section,
                        "top5_ids": [f"{eval_doc}:{seq}" for seq in hits],
                        "hit_at_5": expected in hits, "elapsed_ms": round(elapsed, 2),
                        "source": citation, "source_html_sha256": fact["source_html_sha256"],
                        "paragraph_sha256": fact["paragraph_sha256"]})

    # Seed 10k real pgvector rows from the 20 real model vectors without spending
    # 10k embedding calls. Search itself still follows the production HTTP path.
    scope = hashlib.sha256((rag154_backends["jwks"].issuer + "\0" + rag154_backends["subject_a"]).encode()).hexdigest()
    admin_url = rag154_backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")
    bench_id = "eval154-bench10k"
    with psycopg.connect(admin_url, autocommit=True) as db:
        db.execute("INSERT INTO rag_documents(account_scope,document_id,content_sha256,title,source,total_chunks,status) VALUES (%s,%s,%s,%s,%s,%s,'ready')",
                   (scope, bench_id, hashlib.sha256(bench_id.encode()).hexdigest(), "10k 检索压测", "local://benchmark", 10_000))
        db.execute(
            "WITH seed AS (SELECT seq,text,page,section,embedding,row_number() OVER (ORDER BY seq)-1 AS rn "
            "FROM rag_chunks WHERE account_scope=%s AND document_id=%s) "
            "INSERT INTO rag_chunks(account_scope,document_id,seq,text,page,section,embedding) "
            "SELECT %s,%s,g.i,seed.text || ' 压测编号 ' || g.i,seed.page,seed.section,seed.embedding "
            "FROM generate_series(0,9999) AS g(i) JOIN seed ON seed.rn=(g.i %% 20)",
            (scope, eval_doc, scope, bench_id),
        )
        count = db.execute("SELECT count(*) FROM rag_chunks WHERE account_scope=%s AND document_id=%s", (scope, bench_id)).fetchone()[0]
        assert count == 10_000

    # Warm one request before recording multiple representative measurements.
    timing = []
    for i in range(5):
        start = time.perf_counter()
        response = client.post(f"{API}/search", headers=rag154_headers(rag154_backends,
            request_id=f"eval154-10k-{i}"), json={"document_ids": [bench_id], "query": "POST 如何发送数据给服务器？", "k": 5})
        timing.append((time.perf_counter() - start) * 1000)
        assert response.status_code == 200, response.text
        assert len(response.json()["passages"]) == 5

    rss_after_10k = _rss(pid)
    app_url = rag154_backends["app_url"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(app_url) as db:
        # Inspect the exact production nearest-neighbor ordering at the 10k
        # scale. We record the planner choice; we do not force an index scan.
        db.execute("SELECT set_config('mentorflow.rag_account_scope', %s, true)", (scope,))
        db.execute("SET LOCAL hnsw.iterative_scan = strict_order")
        db.execute("SELECT set_config('hnsw.ef_search', '50', true)")
        query_vector = db.execute(
            "SELECT embedding::text FROM rag_chunks WHERE account_scope=%s AND document_id=%s AND seq=0",
            (scope, eval_doc),
        ).fetchone()[0]
        plan = db.execute(
            "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT c.document_id,c.seq,c.text,c.page,c.section,"
            "(1-(c.embedding <=> %s::vector)) AS cosine FROM rag_chunks c JOIN rag_documents d "
            "ON d.account_scope=c.account_scope AND d.document_id=c.document_id "
            "WHERE c.account_scope=%s AND d.status='ready' AND c.document_id IN (%s) "
            "ORDER BY c.embedding <=> %s::vector LIMIT 50",
            (query_vector, scope, bench_id, query_vector),
        ).fetchone()[0][0]
    plan_text = json.dumps(plan, ensure_ascii=False)
    used_hnsw = "hnsw_rag_chunks_embedding_cosine" in plan_text or "Index Scan" in plan_text and "hnsw" in plan_text.lower()

    output = {
        "backend": "PgVectorStore + local PostgreSQL pgvector 0.8.7; HTTP API /search",
        "embedding": {"dimensions": 512, "rss_before_first_embedding_bytes": rss_before,
                       "rss_after_first_embedding_bytes": rss_after_first,
                       "first_embedding_rss_delta_bytes": rss_after_first - rss_before,
                       "rss_after_10k_search_bytes": rss_after_10k,
                       "cold_one_chunk_upload_ms": round(cold_ms, 2), "warm_one_chunk_upload_ms": round(warm_ms, 2),
                       "direct_adapter_probe": direct_probe},
        "evaluation": {"corpus": "MDN official Chinese HTTP reference", "questions": len(results),
                       "hit_at_5": sum(1 for row in results if row["hit_at_5"]), "results": results},
        "search_10k": {"rows": count, "api_search_ms": [round(x, 2) for x in timing],
                        "median_ms": round(sorted(timing)[len(timing)//2], 2), "top_k": 5,
                        "dataset_note": "synthetic 10k rows repeat the 20 real embedded MDN vectors 500 times each; retrieval itself is production PgVectorStore over pgvector, so this measures index/API path rather than a natural 10k corpus",
                        "explain_analyze_50_candidates": plan,
                        "planner_used_hnsw_index": used_hnsw},
    }
    output_path = TASK_ROOT / "eval" / "rag154-evaluation.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    # Retrieval quality is reported, not used as a pass/fail target. The
    # questions are an audit sample, not a tuned benchmark set.

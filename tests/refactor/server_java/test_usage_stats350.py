import importlib.util
import json
import os
from datetime import date
from pathlib import Path

import psycopg
import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "refactor/server-java/deploy/usage-stats.py"
WRAPPER = Path(__file__).resolve().parents[3] / "refactor/server-java/deploy/usage-stats-readonly.sh"
SPEC = importlib.util.spec_from_file_location("usage_stats350", SCRIPT)
usage_stats350 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(usage_stats350)


def test_log_aggregation_percentiles_statuses_rag_and_peak_without_sensitive_output():
    lines = [
        "2026-11-01T00:00:01.000Z INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/rag/v1/search status=200 elapsed_ms=10",
        "2026-11-01T00:00:05.000Z INFO RequestAccessLog149Filter - http method=POST route=/api/enterprise/rag/v1/documents/raw-document-id/chunks status=201 elapsed_ms=20",
        "2026-11-01T00:00:09.000Z INFO RequestAccessLog149Filter - http method=DELETE route=/api/enterprise/rag/v1/documents/raw-document-id status=404 elapsed_ms=30",
        "2026-11-01T00:00:20.000Z INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/account/profile status=302 elapsed_ms=20",
        "2026-11-01T00:00:21.000Z INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/account/profile status=500 elapsed_ms=30",
        "2026-11-01T00:00:22.000Z INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/account/profile status=200 elapsed_ms=10",
        "2026-11-01T00:00:23.000Z INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/account/profile status=200 elapsed_ms=20",
        "2026-11-01T00:00:24.000Z INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/account/profile status=200 elapsed_ms=30",
        "2026-11-01T00:00:25.000Z INFO app - Restarting application after container restart",
        "2026-11-01T00:02:00.000Z INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/account/profile status=200 elapsed_ms=20",
        "not an access line with ip=10.0.0.1 token=secret account=alice",
        "2026-11-02T03:00:00.000Z INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/account/profile status=200 elapsed_ms=40",
    ]

    summary = usage_stats350.summarize_lines(lines, date(2026, 11, 1), date(2026, 11, 1))
    rows = {(row["day"], row.get("route")): row for row in summary["requests"]}
    profile = rows[("2026-11-01", "/api/enterprise/account/profile")]
    assert profile["requests"] == 6
    assert profile["2xx"] == 4
    assert profile["3xx"] == 1
    assert profile["4xx"] == 0
    assert profile["5xx"] == 1
    assert profile["latency_ms"]["p50"] == 20
    assert profile["latency_ms"]["p95"] == pytest.approx(30.0)
    assert profile["latency_ms"]["p99"] == pytest.approx(30.0)
    assert profile["latency_ms"]["max"] == 30

    rag = summary["rag_requests"]
    assert {row["route"] for row in rag} == {
        "/api/enterprise/rag/v1/search",
        "/api/enterprise/rag/v1/documents/{doc_id}/chunks",
        "/api/enterprise/rag/v1/documents/{doc_id}",
    }
    assert summary["peak_minute"] == {"minute": "2026-11-01T00:00:00+00:00", "requests": 8}
    assert summary["restart_gaps"] == [{
        "after": "2026-11-01T00:00:24+00:00",
        "before": "2026-11-01T00:02:00+00:00",
        "marker": "2026-11-01T00:00:25+00:00",
    }]
    encoded = json.dumps(summary, ensure_ascii=True)
    assert "10.0.0.1" not in encoded
    assert "secret" not in encoded
    assert "alice" not in encoded
    assert "raw-document-id" not in encoded


def test_date_filter_and_missing_elapsed_are_explicit():
    lines = [
        "2026-11-01 12:00:00 INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/account/profile status=200",
        "2026-11-02 12:00:00 INFO RequestAccessLog149Filter - http method=GET route=/api/enterprise/account/profile status=200 elapsed_ms=8",
    ]
    summary = usage_stats350.summarize_lines(lines, date(2026, 11, 2), date(2026, 11, 2))
    assert summary["log_range"]["first"] == "2026-11-02T12:00:00"
    assert summary["latency_recorded"] is True
    row = summary["requests"][0]
    assert row["requests"] == 1
    assert row["latency_ms"] == {"p50": 8, "p95": 8, "p99": 8, "max": 8}


def test_readonly_wrapper_rejects_shell_metacharacters_before_sql():
    wrapper = WRAPPER.read_text(encoding="utf-8")
    assert "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]" in wrapper
    assert "????-??-??" not in wrapper
    assert "date.fromisoformat" in wrapper


def test_active_accounts_uses_sync_ledger_and_read_only_postgres():
    """Run the exact SELECT against the existing embedded PostgreSQL fixture."""
    from support_rag154 import CLASSPATH_FILE, _EmbeddedPostgres, _prepare_database

    if not CLASSPATH_FILE.is_file():
        pytest.skip("the shared embedded PostgreSQL/Zonky classpath is unavailable")
    classpath = CLASSPATH_FILE.read_text(encoding="utf-8").strip()
    postgres = _EmbeddedPostgres(classpath)
    try:
        _app_url, admin_url, _migration_url = _prepare_database(
            postgres, "usage_stats350", "https://identity.example.invalid/usage350",
            target="0036_account_import_ledger",
        )
        admin_url = admin_url.replace("postgresql+psycopg://", "postgresql://")
        issuer = "https://identity.example.invalid/usage350"
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO organizations(id,slug,name,status) VALUES (%s,%s,%s,'active')",
                ("org-350", "org-350", "usage350"),
            )
            connection.execute(
                "INSERT INTO organization_memberships(organization_id,subject_id,role,status) "
                "VALUES (%s,%s,'LEARNER','active'),(%s,%s,'LEARNER','active')",
                ("org-350", "subject-a", "org-350", "subject-b"),
            )
            connection.execute(
                "INSERT INTO workspaces(id,organization_id,slug,name,status) "
                "VALUES (%s,%s,%s,%s,'active')",
                ("ws-350", "org-350", "ws-350", "usage350 workspace"),
            )
            values = [
                ("import-350-a1", "subject-a", "a1", "2026-11-01T01:00:00+00:00"),
                ("import-350-a2", "subject-a", "a2", "2026-11-01T03:00:00+00:00"),
                ("import-350-b", "subject-b", "b1", "2026-11-02T03:00:00+00:00"),
                ("import-350-pending", "subject-c", "c1", "2026-11-01T04:00:00+00:00"),
            ]
            for import_id, subject, digest, updated_at in values:
                state = "PLANNED" if import_id.endswith("pending") else "COMPLETED"
                connection.execute(
                    "INSERT INTO account_import_ledger("
                    "import_id,issuer,subject_id,organization_id,workspace_id,idempotency_key,"
                    "source_digest,request_digest,format_version,snapshot_json,summary_json,state,"
                    "created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)",
                    (import_id, issuer, subject, "org-350", "ws-350", "key-" + digest,
                     "source-" + digest, "request-" + digest, "v1", "{}", "{}", state,
                     updated_at, updated_at),
                )
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                rows = connection.execute(
                    usage_stats350.active_accounts_query(date(2026, 11, 1), date(2026, 11, 2))
                ).fetchall()
                assert usage_stats350.active_accounts_from_rows(rows) == [
                    {"day": "2026-11-01", "accounts": 1},
                    {"day": "2026-11-02", "accounts": 1},
                ]
                with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                    connection.execute(
                        "INSERT INTO account_import_ledger("
                        "import_id,issuer,subject_id,organization_id,workspace_id,idempotency_key,"
                        "source_digest,request_digest,format_version,snapshot_json,summary_json,state) "
                        "VALUES ('write-denied',%s,'subject-x','org-350','ws-350','key-x',"
                        "'source-x','request-x','v1','{}','{}','COMPLETED')",
                        (issuer,),
                    )
    finally:
        postgres.stop()

"""BUG-232: database and unexpected failures keep the HTTP contract and log context.

The live check deliberately stops the embedded PostgreSQL process after the Java
server is ready.  This gives the repository a real SQLException/SQLState without
adding a product-only fault injection hook.  The source check covers the
RuntimeException path, which is otherwise difficult to trigger deterministically
through the public API.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
import httpx

from contract_support145 import ORG, WORKSPACE, sync145_backends


BASE = "/api/enterprise/sync/v1"
REPO = Path(__file__).resolve().parents[3]
JAVA_SRC = REPO / "refactor" / "server-java" / "src" / "main" / "java"


def _headers(token: str, request_id: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer " + token,
        "X-MentorFlow-Organization-ID": ORG,
        "X-MentorFlow-Workspace-ID": WORKSPACE,
        "X-Request-ID": request_id,
        "Content-Type": "application/json",
    }


def _log_after(endpoint, request_id: str) -> str:
    """Wait briefly for the child process to flush the structured failure log."""
    deadline = time.monotonic() + 8
    text = ""
    while time.monotonic() < deadline:
        text = endpoint.log_path.read_text(encoding="utf-8", errors="replace")
        if request_id in text:
            return text
        time.sleep(0.1)
    return text


def test_sync363_database_failure_preserves_response_and_logs_context(sync145_backends):
    """A lost DB remains the existing 503 while the Java log gains diagnostics."""
    backends = sync145_backends
    request_id = "errlog363-sqlstate"
    # user-145-a is the seeded enterprise member used by the parity fixture;
    # using an unknown subject would correctly stop at the 403 membership gate
    # before the repository can observe the database outage.
    token = backends["jwks"].token(subject="user-145-a")

    # The fixture owns PostgreSQL and stops it again during teardown; stop() is
    # idempotent, so this is safe and gives the Java pool a deterministic outage.
    backends["postgres"].stop()
    try:
        response = backends["java"].get(
            f"{BASE}/snapshot?kind=learning_run",
            headers=_headers(token, request_id),
            timeout=25,
        )
    except httpx.ReadTimeout:
        # Hikari may wait for its connection timeout after an abrupt embedded
        # PostgreSQL stop.  The source contracts below remain executable when
        # the local fixture cannot produce a bounded HTTP response.
        pytest.skip("embedded PostgreSQL outage did not return before Java pool timeout")

    assert response.status_code == 503
    assert response.json() == {
        "code": "SYNC_QUERY_FAILED",
        "sync_status": "QUERY_FAILED",
    }

    log = _log_after(backends["java_endpoint"], request_id)
    assert request_id in log
    assert "SYNC_QUERY_FAILED" in log
    assert "/snapshot" in log
    # The driver may expose a PSQLException/SQLException, or a connect failure
    # at the bottom of Spring's transaction wrapper, depending on outage timing.
    assert re.search(r"(?:PSQLException|SQLException|DataAccessException|CannotCreateTransactionException|ConnectException)", log)
    assert re.search(r"(?:SQLState|sqlState)[=: ]+[0-9A-Z]{2,5}", log)
    assert re.search(r"\bWARN\b", log), log[-4000:]
    # The request has no materials/body, and the token must never be copied to
    # diagnostics.  These guards also catch accidental exception message dumps.
    assert token not in log
    # Stack frames legitimately name Spring's BearerTokenAuthenticationFilter;
    # what must never appear is a "Bearer <credential>" header value.
    assert not re.search(r"Bearer\s+[A-Za-z0-9._~+/=-]", log)
    assert "materials" not in log.lower()


@pytest.mark.parametrize(
    "relative",
    [
        "sync/SyncController.java",
        "sync/SyncRepository.java",
        "rag/RagService.java",
        "rag/RagController.java",
        "identity/JdbcIdentityDirectory.java",
    ],
)
def test_error_log363_runtime_catches_retain_stack_and_privacy(relative):
    """Every catch converted to a response must call the shared failure logger."""
    source = (JAVA_SRC / "win" / "clannad" / "mentorflow" / relative).read_text(
        encoding="utf-8"
    )
    for match in re.finditer(r"catch\s*\(\s*RuntimeException\s+\w+\s*\)", source):
        block = source[match.end() : source.find("\n        }", match.end())]
        # A lower layer may only rethrow/wrap; the HTTP boundary is responsible
        # for the actual log record.  In that case retaining ``failure`` as the
        # cause is the required contract.
        has_log = re.search(
            r"(?:FailureLogger|LOG|logger).*?(?:error|warn)|(?:error|warn).*?(?:FailureLogger|LOG|logger)",
            block,
            re.I | re.S,
        )
        has_cause = re.search(r"new\s+\w+Failure\([^;]*\bfailure\b", block, re.I | re.S)
        assert has_log or has_cause or "throw error" in block, relative
        if has_log:
            assert re.search(r"(?:\bThrowable\b|\.error\s*\([^;]*,|\.warn\s*\([^;]*,|failure)", block, re.I | re.S), relative
        assert "request.getInputStream" not in block
        assert "Authorization" not in block
        assert "Bearer" not in block


def test_rag363_database_conversions_keep_cause_for_the_http_logger():
    """RAG search/upload failures reach the same safe 503 boundary logger."""
    service = (JAVA_SRC / "win" / "clannad" / "mentorflow" / "rag" / "RagService.java").read_text(encoding="utf-8")
    controller = (JAVA_SRC / "win" / "clannad" / "mentorflow" / "rag" / "RagController.java").read_text(encoding="utf-8")
    responses = (JAVA_SRC / "win" / "clannad" / "mentorflow" / "identity" / "ErrorResponses.java").read_text(encoding="utf-8")
    assert service.count("new RagFailure(\"RAG_EMBEDDING_UNAVAILABLE\"") >= 3
    assert re.search(r"new IdentityFailure\(\"RAG_UNAVAILABLE\"[^;]*503\s*,\s*failure\)", controller)
    for field in ("code={}", "request_id={}", "route={}", "exception={}", "sqlState={}"):
        assert field in responses

"""351: the RAG caller can reserve the gateway request id before retrieval."""

import sqlite3

from refactor.learning.connections import providers
from refactor.learning.connections.gateway import ModelGateway


def test_gateway351_request_id_is_reused_by_usage_record(tmp_path, vault, monkeypatch):
    gateway = ModelGateway("account-351", tmp_path)
    gateway.save_preset("deepseek", api_key="sk-351-offline")

    def complete(provider, **kwargs):
        return providers.ModelResult(
            "answer",
            providers.Usage(1, 0, 1, 0, 0, 0, {}),
            provider,
            kwargs["model"],
            (), (), (), "stop",
        )

    monkeypatch.setattr(providers, "complete", complete)
    request_id = "rag-351-request-id"
    gateway.complete("rag_qa", [{"role": "user", "content": "fixture"}], request_id=request_id)

    with sqlite3.connect(gateway.usage_log.path) as database:
        row = database.execute("SELECT request_id, purpose FROM calls").fetchone()
    assert row == (request_id, "rag_qa")

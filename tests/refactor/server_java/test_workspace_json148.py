"""Java malformed workspace JSON responses keep the documented HTTP contract."""
from __future__ import annotations

import json
import ssl

import httpx

from test_lifecycle_http146 import lifecycle_http146


INITIALIZE = "/api/enterprise/account/workspaces/initialize"
# Fixed malformed-input positions and messages from the existing cases.
BAD_JSON_148 = (
    ("object-eof", b"{", 1, "Expecting property name enclosed in double quotes"),
    ("value-eof", b'{"x":', 5, "Expecting value"),
    ("bad-escape", b'{"x":"\\q"}', 6, "Invalid \\escape"),
    ("unquoted-field", b"{x:1}", 1, "Expecting property name enclosed in double quotes"),
    ("missing-colon", b'{"x" 1}', 5, "Expecting ':' delimiter"),
    ("missing-comma", b'{"x":1 "y":2}', 7, "Expecting ',' delimiter"),
    ("leading-zero-number", b'{"x":01}', 6, "Expecting ',' delimiter"),
    ("invalid-value-token", b'{"x": tru}', 6, "Expecting value"),
    ("trailing-json-value", b"{} {}", 3, "Extra data"),
    ("array-eof", b"[", 1, "Expecting value"),
    ("unterminated-string", b'{"x":"abc', 5, "Unterminated string starting at"),
    ("invalid-unicode-escape", b'{"x":"\\u12G4"}', 7, "Invalid \\uXXXX escape"),
    ("unescaped-control-character", b'{"x":"a\nb"}', 7, "Invalid control character at"),
    ("non-ascii-eof-position", '{"é":'.encode("utf-8"), 5, "Expecting value"),
    ("incomplete-exponent", b'{"x":1e', 6, "Expecting ',' delimiter"),
    ("incomplete-fraction", b'{"x":1.', 6, "Expecting ',' delimiter"),
    ("negative-without-digits", b'{"x":-}', 5, "Expecting value"),
    ("unexpected-close", b"}", 0, "Expecting value"),
    ("trailing-comma", b'{"x":1,}', 7, "Expecting property name enclosed in double quotes"),
    ("unterminated-escape", b'{"x":"abc\\', 5, "Unterminated string starting at"),
    ("leading-plus", b'{"x":+1}', 5, "Expecting value"),
    ("repeated-sign", b'{"x":--1}', 5, "Expecting value"),
    ("object-key-eof", b'{"x"', 4, "Expecting ':' delimiter"),
    ("object-value-eof", b'{"x":1', 6, "Expecting ',' delimiter"),
    ("array-value-eof", b'{"x":[', 6, "Expecting value"),
    ("nested-object-value-eof", b'{"x":{}', 7, "Expecting ',' delimiter"),
    ("array-object-mismatched-close", b'{"x":[1}', 7, "Expecting ',' delimiter"),
    ("number-exponent-eof", b'{"x":1e', 6, "Expecting ',' delimiter"),
    ("closed-string-value-eof", b'{"x":"abc"', 10, "Expecting ',' delimiter"),
    ("array-comma-eof", b'[1,', 3, "Expecting value"),
    ("unicode-escape-after-valid-one", b'{"a":"\\u0041","b":"\\u12G4"}', 20, "Invalid \\uXXXX escape"),
    ("trailing-json-whitespace", b'{} 123', 3, "Extra data"),
    ("literal-true-eof", b'{"x":true', 9, "Expecting ',' delimiter"),
    ("literal-false-eof", b'{"x":false', 10, "Expecting ',' delimiter"),
    ("literal-null-eof", b'{"x":null', 9, "Expecting ',' delimiter"),
    ("incomplete-exponent-sign", b'{"x":1e+', 6, "Expecting ',' delimiter"),
    ("unicode-trailing-data", '{"名":"值"} 💡'.encode("utf-8"), 10, "Extra data"),
)


def _client(pair, backend):
    return httpx.Client(base_url=pair["servers"][backend]["base_url"],
        verify=ssl.create_default_context(cafile=str(pair["tls"]["ca"])),
        timeout=25, trust_env=False, headers={"X-Request-ID": "malformed-json148"})


def test_workspace_initialize_malformed_json_contract_148(lifecycle_http146):
    pair = lifecycle_http146
    with _client(pair, "java") as client:
        login = client.post("/api/enterprise/auth/native/login",
            json={"username": "alice", "password": "correct"})
        assert login.status_code == 200, login.text
        authorization = {"Authorization": "Bearer " + login.json()["access_token"]}
        for case, raw, position, expected_error in BAD_JSON_148:
            response = client.post(INITIALIZE, content=raw,
                headers={**authorization, "Content-Type": "application/json"})
            assert response.status_code == 422, (case, response.status_code, response.content)
            assert response.headers.get("content-type", "").startswith("application/json"), case
            assert int(response.headers["content-length"]) == len(response.content), case
        try:
            body = response.json()
            issue = body["detail"][0]
            assert (
                list(body) == ["detail"] and len(body["detail"]) == 1
                and list(issue) == ["type", "loc", "msg", "input", "ctx"]
                and issue["type"] == "json_invalid"
                and issue["loc"] == ["body", position]
                and issue["msg"] == "JSON decode error"
                and issue["input"] == {} and issue["ctx"] == {"error": expected_error}
                and response.content == json.dumps(body, ensure_ascii=False,
                    separators=(",", ":")).encode("utf-8")
            ), (case, body)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AssertionError(f"{case}: malformed JSON response: {response.content!r}") from exc

"""Byte-level Python/Java parity for malformed workspace initialization JSON."""
from __future__ import annotations

import json
import ssl

import httpx

from test_lifecycle_http146 import lifecycle_http146


INITIALIZE = "/api/enterprise/account/workspaces/initialize"
# Python json.JSONDecodeError.msg and .pos form the public FastAPI reference.
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


def test_workspace_initialize_malformed_json_matches_python_bytes_148(lifecycle_http146):
    pair = lifecycle_http146
    traces = {}
    mismatches = []
    for backend in ("python", "java"):
        with _client(pair, backend) as client:
            login = client.post("/api/enterprise/auth/native/login",
                json={"username": "alice", "password": "correct"})
            assert login.status_code == 200, login.text
            authorization = {"Authorization": "Bearer " + login.json()["access_token"]}
            trace = []
            for case, raw, position, python_error in BAD_JSON_148:
                response = client.post(INITIALIZE, content=raw,
                    headers={**authorization, "Content-Type": "application/json"})
                trace.append(response)
            traces[backend] = trace

    for index, (case, _raw, position, python_error) in enumerate(BAD_JSON_148):
        python, java = traces["python"][index], traces["java"][index]
        if python.status_code != 422:
            mismatches.append(f"{case}: Python status={python.status_code}, body={python.content!r}")
        if java.status_code != 422:
            mismatches.append(f"{case}: Java status={java.status_code}, body={java.content!r}")
        expected_headers = ("application/json", python.headers.get("content-length"))
        actual_headers = (java.headers.get("content-type"), java.headers.get("content-length"))
        if expected_headers != actual_headers:
            mismatches.append(f"{case}: headers Python={expected_headers!r} Java={actual_headers!r}")
        if java.content != python.content:
            mismatches.append(f"{case}: raw bytes Python={python.content!r} Java={java.content!r}")

        # Lock the Python contract independently so equal regressions cannot pass.
        try:
            body = python.json()
            issue = body["detail"][0]
            python_contract = (
                list(body) == ["detail"] and len(body["detail"]) == 1
                and list(issue) == ["type", "loc", "msg", "input", "ctx"]
                and issue["type"] == "json_invalid"
                and issue["loc"] == ["body", position]
                and issue["msg"] == "JSON decode error"
                and issue["input"] == {} and issue["ctx"] == {"error": python_error}
                and python.content == json.dumps(body, ensure_ascii=False,
                    separators=(",", ":")).encode("utf-8")
            )
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            python_contract = False
            body = python.content
            mismatches.append(f"{case}: Python response is not the expected JSON contract: {exc}")
        if not python_contract:
            mismatches.append(f"{case}: Python expected pos={position}, error={python_error!r}; body={body!r}")

    assert not mismatches, "\n".join(mismatches)

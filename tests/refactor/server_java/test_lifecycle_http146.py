"""Local PostgreSQL-backed lifecycle HTTP contracts against Python and Java."""
from __future__ import annotations

import json
import re
import ssl
import time

import httpx
import psycopg
import pytest

from contract_support144 import LOCAL, ORG, WORKSPACE
from lifecycle_support146 import (__doc__ as fake_spi_scope146, build_pair146, shutdown_pair146)


LIFECYCLE = "/api/enterprise/auth/lifecycle"
NATIVE = "/api/enterprise/auth/native"
WORKSPACES = "/api/enterprise/account/workspaces"
RESET_OBSERVATION = "/api/enterprise/auth/reset"
LIFECYCLE_FORGOT = LIFECYCLE + "/forgot"
@pytest.fixture(scope="module")
def lifecycle_http146(tmp_path_factory):
    """Independent local PG17, fake mTLS Keycloak/SPI and SMTP sink per runtime."""
    pair = build_pair146(tmp_path_factory.mktemp("lifecycle-http146"))
    try:
        yield pair
    finally:
        _write_diagnostics146(pair)
        shutdown_pair146(pair)


def _write_diagnostics146(pair):
    """Keep safe failure context; never persist request values or message bodies."""
    details = {}
    for backend, authority in pair["identities"].items():
        details[backend] = {"identity_operations": authority.operations,
            "authority_http": authority.http_events,
            "smtp": [{"to": item["to"], "subject": item["subject"]}
                for item in authority.smtp.parsed()]}
    (pair["run_dir"] / "diagnostics.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")


def _client(pair, backend):
    return httpx.Client(base_url=pair["servers"][backend]["base_url"],
        verify=ssl.create_default_context(cafile=str(pair["tls"]["ca"])),
        timeout=25, trust_env=False, headers={"X-Request-ID": "contract146"})


def _tenant_auth(token):
    return {"Authorization": "Bearer " + token,
        "X-MentorFlow-Organization-ID": ORG,
        "X-MentorFlow-Workspace-ID": WORKSPACE}


def _json_body(response):
    assert "application/json" in response.headers.get("content-type", "")
    return response.json()


def _http_projection(response):
    """Keep serialized JSON insertion order; replace only explicit entropy fields."""
    transport = {"date", "server", "connection", "transfer-encoding", "keep-alive"}
    headers = {k.lower(): v for k, v in response.headers.items() if k.lower() not in transport}
    body = response.content
    if not body:
        return response.status_code, headers, b""
    value = json.loads(body)
    compact_original = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert body == compact_original, "HTTP JSON bytes must match compact insertion-ordered JSON"
    if isinstance(value, dict):
        for key, value_item in list(value.items()):
            if key in {"credential", "transaction_id", "reset_proof", "access_token", "refresh_token"}:
                value[key] = f"<{key}>"
            elif value.get("schema") == "mentorflow.native-session.v1" and key == "expires_in":
                value[key] = "<remaining-seconds>"
    return response.status_code, headers, json.dumps(value, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")


def _extract_code(smtp):
    messages = smtp.parsed()
    assert messages, "the fake SPI did not submit mail to the local sink"
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", messages[-1]["body"])
    assert match, messages[-1]
    return match.group(1)


def _await_mail(smtp, before):
    # The backend's asynchronous SPI operation itself has a ten-second timeout.
    # A five-second fixture wait can fail before that operation has finished.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if len(smtp.messages) > before:
            return _extract_code(smtp)
        time.sleep(.02)
    pytest.fail("expected local SMTP delivery did not arrive")


def _db_projection(endpoint):
    url = endpoint["env"]["MENTORFLOW_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT family_id,issuer,subject,refresh_digest,used_digests,principal,account_binding,deadline,revoked_reason FROM native_session_families").fetchall()
    output = []
    for family_id, issuer, subject, digest, used, principal, binding, deadline, revoked in rows:
        assert re.fullmatch(r"[0-9a-f]{64}", family_id)
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        assert re.fullmatch(r"[0-9a-f]{64}", binding)
        assert isinstance(principal, dict)
        assert all(re.fullmatch(r"[0-9a-f]{64}", item) for item in used)
        assert len(used) == len(set(used))
        assert deadline is None, "146 native sessions are configured with the permanent-session TTL"
        stable_principal = dict(principal)
        for field in ("token_id", "issued_at", "expires_at", "authentication_time"):
            if field in stable_principal: stable_principal[field] = f"<{field}>"
        output.append({"issuer": issuer, "subject": subject, "used_count": len(used),
            "principal": stable_principal, "account_binding": binding,
            "deadline": deadline, "revoked": revoked})
    return sorted(output, key=lambda row: (row["subject"], row["used_count"], row["revoked"] or ""))


def _workspace_bootstrap_rows(pair, backend, subject):
    """Return the exact bootstrap-owned rows, normalizing only server timestamps."""
    db_name = "py146" if backend == "python" else "java146"
    port = pair["pg"]["port"]
    with psycopg.connect(f"host=127.0.0.1 port={port} user=postgres dbname={db_name}") as conn:
        membership = conn.execute("""SELECT organization_id, role, status, created_at, updated_at FROM organization_memberships
            WHERE subject_id=%s ORDER BY organization_id""", (subject,)).fetchall()
        ws_membership = conn.execute("""SELECT organization_id, workspace_id, status, created_at, updated_at FROM workspace_memberships
            WHERE subject_id=%s ORDER BY organization_id, workspace_id""", (subject,)).fetchall()
        role_bindings = conn.execute("""SELECT organization_id, workspace_id, role, status, created_at, updated_at FROM workspace_role_bindings
            WHERE subject_id=%s ORDER BY organization_id, workspace_id""", (subject,)).fetchall()
        organization_ids = [row[0] for row in membership]
        workspace_ids = [row[1] for row in ws_membership]
        if organization_ids:
            organizations = conn.execute("""SELECT id, slug, name, status, created_at, updated_at FROM organizations
                WHERE id = ANY(%s) ORDER BY id""", (organization_ids,)).fetchall()
            workspaces = conn.execute("""SELECT id, organization_id, slug, name, status, created_at, updated_at FROM workspaces
                WHERE id = ANY(%s) ORDER BY id""", (workspace_ids,)).fetchall()
            profiles = conn.execute("""SELECT workspace_id, organization_id, description, character_id,
                allow_external_knowledge, persona_prompt, execution_mode, provider_name, provider_model,
                created_at, updated_at
                FROM workspace_profiles WHERE workspace_id = ANY(%s) ORDER BY workspace_id""",
                (workspace_ids,)).fetchall()
            manifests = conn.execute("""SELECT issuer, subject_id, organization_id, workspace_id,
                scope_version, initialized, generation, created_at, updated_at FROM rehydration_manifests
                WHERE subject_id=%s ORDER BY issuer, organization_id, workspace_id""", (subject,)).fetchall()
        else:
            organizations = workspaces = profiles = manifests = []
    def stable(rows):
        return [tuple("<timestamp>" if hasattr(value, "tzinfo") else value for value in row) for row in rows]
    return {"organizations": stable(organizations), "organization_memberships": stable(membership),
        "workspaces": stable(workspaces), "workspace_memberships": stable(ws_membership),
        "workspace_role_bindings": stable(role_bindings), "workspace_profiles": stable(profiles),
        "rehydration_manifests": stable(manifests)}


def _exercise146(pair, backend):
    smtp = pair["smtps"][backend]
    authority = pair["identities"][backend]
    trace = []
    with _client(pair, backend) as c:
        cap = c.get(LIFECYCLE + "/capabilities")
        assert cap.status_code == 200
        trace.append(_http_projection(cap))

        # Public registration: start, resend, incorrect/expired/exhausted outcomes,
        # then a valid code and asynchronous commit confirmation.
        before = len(smtp.messages)
        started = c.post(LIFECYCLE + "/register", json={"username": "new-user146",
            "email": "new146@fixture.invalid", "password": "new-password-146"})
        assert started.status_code == 201
        start_value = _json_body(started)
        credential = start_value["credential"]
        trace.append(_http_projection(started))
        assert _await_mail(smtp, before) == "123456"
        resent = c.post(LIFECYCLE + "/register/resend", json={"credential": credential})
        assert resent.status_code == 200
        trace.append(_http_projection(resent))
        assert _extract_code(smtp) == "123456"
        for status, expected_code in (("WRONG_CODE", "REGISTRATION_WRONG_CODE"),
                                      ("EXPIRED", "REGISTRATION_EXPIRED"),
                                      ("EXHAUSTED", "REGISTRATION_EXHAUSTED")):
            if status == "WRONG_CODE":
                wrong = c.post(LIFECYCLE + "/register/verify", json={"credential": credential, "code": "000000"})
                assert wrong.status_code == 400 and _json_body(wrong)["code"] == expected_code
                trace.append(_http_projection(wrong))
            else:
                authority.next_registration_status = status
                rejected = c.post(LIFECYCLE + "/register/verify", json={"credential": credential, "code": "123456"})
                assert rejected.status_code == 400 and _json_body(rejected)["code"] == expected_code
                trace.append(_http_projection(rejected))
        verified = c.post(LIFECYCLE + "/register/verify", json={"credential": credential, "code": "123456"})
        assert verified.status_code == 200 and _json_body(verified)["state"] == "VERIFIED"
        trace.append(_http_projection(verified))
        checked = c.post(LIFECYCLE + "/register/check", json={"field": "email", "value": "new146@fixture.invalid"})
        assert checked.status_code == 200 and _json_body(checked)["available"] is False
        trace.append(_http_projection(checked))

        # Password reset sends only into local SMTP, confirms the new credential,
        # and invalidates the pre-reset native bearer.
        login_response = c.post(NATIVE + "/login", json={"username": "new-user146", "password": "new-password-146"})
        login = _json_body(login_response); trace.append(_http_projection(login_response))
        reset_before = len(smtp.messages)
        requested = c.post(LIFECYCLE + "/reset-code/request", json={"email": "new146@fixture.invalid"})
        assert requested.status_code == 202
        req_value = _json_body(requested)
        reset_code = _await_mail(smtp, reset_before)
        trace.append(_http_projection(requested))
        reset_verify = c.post(LIFECYCLE + "/reset-code/verify", json={"transaction_id": req_value["transaction_id"], "code": reset_code})
        assert reset_verify.status_code == 200
        rv = _json_body(reset_verify)
        trace.append(_http_projection(reset_verify))
        completed = c.post(LIFECYCLE + "/reset-code/complete", json={"reset_proof": rv["reset_proof"],
            "new_password": "reset-password-146", "confirm_password": "reset-password-146"})
        assert completed.status_code == 200 and _json_body(completed)["state"] == "COMPLETED"
        trace.append(_http_projection(completed))
        stale = c.get(NATIVE + "/session", headers={"Authorization": "Bearer " + login["access_token"]})
        assert stale.status_code == 401
        trace.append(_http_projection(stale))

        # Email binding and authenticated password/session operations.
        subject = authority.add_user("binding-user146", None, "correct-password")
        bound_login_response = c.post(NATIVE + "/login", json={"username": "binding-user146", "password": "correct-password"})
        bound_login = _json_body(bound_login_response); trace.append(_http_projection(bound_login_response))
        token = bound_login["access_token"]
        headers = _tenant_auth(token)
        profile = c.get(LIFECYCLE + "/email", headers=headers)
        assert profile.status_code == 200 and _json_body(profile)["can_bind"] is True
        trace.append(_http_projection(profile))
        binding_before = len(smtp.messages)
        binding = c.post(LIFECYCLE + "/email-code/request", json={"email": "bound146@fixture.invalid"}, headers=headers)
        assert binding.status_code == 202
        binding_value = _json_body(binding)
        binding_code = _await_mail(smtp, binding_before)
        assert "邮箱验证验证码" in smtp.parsed()[-1]["subject"]
        trace.append(_http_projection(binding))
        binding_verify = c.post(LIFECYCLE + "/email-code/verify", json={"transaction_id": binding_value["transaction_id"], "code": binding_code}, headers=headers)
        assert binding_verify.status_code == 200 and _json_body(binding_verify)["email_verified"] is True
        trace.append(_http_projection(binding_verify))
        changed = c.post(LIFECYCLE + "/change-password", json={"current_password": "correct-password", "new_password": "replacement-password"}, headers=headers)
        assert changed.status_code == 200 and _json_body(changed)["state"] == "PASSWORD_CHANGED"
        trace.append(_http_projection(changed))
        assert c.get(NATIVE + "/session", headers=headers).status_code == 401
        fresh_response = c.post(NATIVE + "/login", json={"username": "binding-user146", "password": "replacement-password"})
        fresh = _json_body(fresh_response); trace.append(_http_projection(fresh_response))
        fresh_headers = _tenant_auth(fresh["access_token"])
        assert c.get(LIFECYCLE + "/email", headers=fresh_headers).status_code == 200
        trace.append(_http_projection(c.get(LIFECYCLE + "/email", headers=fresh_headers)))
        revoked = c.post(LIFECYCLE + "/revoke-sessions", json={}, headers=fresh_headers)
        assert revoked.status_code == 200 and _json_body(revoked)["state"] == "SESSION_REVOCATION_CONFIRMED"
        trace.append(_http_projection(revoked))
        assert c.get(NATIVE + "/session", headers=fresh_headers).status_code == 401

        # Account identity directory remains on real PostgreSQL membership data.
        directory_login_response = c.post(NATIVE + "/login", json={"username": "alice", "password": "correct"})
        directory_login = _json_body(directory_login_response); trace.append(_http_projection(directory_login_response))
        directory_headers = _tenant_auth(directory_login["access_token"])
        me = c.get("/api/enterprise/auth/me", headers=directory_headers)
        assert me.status_code == 200
        trace.append(_http_projection(me))
        workspaces = c.get(WORKSPACES, headers={"Authorization": directory_headers["Authorization"]})
        assert workspaces.status_code == 200
        trace.append(_http_projection(workspaces))

        # Revoke provider sessions uses the official Admin API logout operation.
        assert "subject146-binding-user146" in authority.admin_logouts
        assert subject == "subject146-binding-user146"

    emails = smtp.parsed()
    # Actual message text comes from the local protocol fake. Compare subject and
    # entire body while normalizing only the six-digit code.
    normalized_mail = [(m["to"], m["subject"], re.sub(r"(?<!\d)\d{6}(?!\d)", "<code>", m["body"])) for m in emails]
    assert any(item[1] == "MentorFlow 注册验证码" for item in normalized_mail)
    assert any(item[1] == "MentorFlow 密码重置验证码" for item in normalized_mail)
    assert any(item[1] == "MentorFlow 邮箱验证验证码" for item in normalized_mail)
    expected_bodies = {
        "MentorFlow 注册验证码": "您的 MentorFlow 注册验证码是：<code>\n有效期为 10 分钟。请在 MentorFlow 注册页面输入。\n请勿向他人提供验证码。如非本人操作，请忽略此邮件。",
        "MentorFlow 密码重置验证码": "您的 MentorFlow 密码重置验证码是：<code>\n有效期为 10 分钟。请在 MentorFlow 软件的找回密码页面输入验证码。\n请勿向他人提供验证码。如非本人操作，请忽略此邮件。",
        "MentorFlow 邮箱验证验证码": "您的 MentorFlow 邮箱验证验证码是：<code>\n有效期为 10 分钟。请在 MentorFlow 账号设置中输入。此验证码不能用于重置密码。",
    }
    for _recipient, subject_line, body in normalized_mail:
        if subject_line in expected_bodies:
            assert body.strip().replace("\r\n", "\n") == expected_bodies[subject_line]
    return {"trace": trace, "mail": normalized_mail,
        "db": _db_projection(pair["servers"][backend]),
        "operations": [row["operation"] for row in authority.operations],
        "admin_trace": authority.http_events}


def test_lifecycle146_same_http_contract_mail_and_postgres_projection(lifecycle_http146):
    outcomes = {backend: _exercise146(lifecycle_http146, backend) for backend in ("python", "java")}
    _assert_trace_equal(outcomes["python"]["trace"], outcomes["java"]["trace"])
    assert outcomes["python"]["mail"] == outcomes["java"]["mail"]
    assert outcomes["python"]["db"] == outcomes["java"]["db"]
    assert outcomes["python"]["operations"] == outcomes["java"]["operations"]
    assert outcomes["python"]["admin_trace"] == outcomes["java"]["admin_trace"]


def _assert_trace_equal(python_trace, java_trace):
    assert len(python_trace) == len(java_trace), f"trace length Python={len(python_trace)} Java={len(java_trace)}"
    for index, (python_item, java_item) in enumerate(zip(python_trace, java_trace)):
        assert python_item == java_item, f"HTTP contract mismatch at request index {index}: Python={python_item!r}, Java={java_item!r}"


def test_lifecycle146_fake_spi_boundary_is_explicit():
    """Guard the test's claim: algorithm behavior remains owned by real Keycloak SPI."""
    assert "does NOT implement or qualify" in fake_spi_scope146
    assert "protocol substitute" in fake_spi_scope146


def test_lifecycle146_fresh_workspace_initialization_and_database_rows(lifecycle_http146):
    pair = lifecycle_http146
    subject = "subject146-bootstrap-user"
    for backend in ("python", "java"):
        pair["identities"][backend].add_user("bootstrap-user", "bootstrap146@fixture.invalid", "bootstrap-pass")

    outcomes = {}
    for backend in ("python", "java"):
        trace = []
        with _client(pair, backend) as c:
            login = c.post(NATIVE + "/login", json={"username": "bootstrap-user", "password": "bootstrap-pass"})
            assert login.status_code == 200, login.text
            token = _json_body(login)["access_token"]
            headers = {"Authorization": "Bearer " + token}
            trace.append(_http_projection(login))

            before = c.get(WORKSPACES, headers=headers)
            assert before.status_code == 200
            before_value = _json_body(before)
            assert before_value == {"workspaces": [], "default_workspace_id": None, "initialization_required": True}
            trace.append(_http_projection(before))

            initialized = c.post(WORKSPACES + "/initialize", json={}, headers=headers)
            assert initialized.status_code == 200, initialized.text
            initial_value = _json_body(initialized)
            assert initial_value["initialization_required"] is False
            assert len(initial_value["workspaces"]) == 1
            trace.append(_http_projection(initialized))
            workspace = initial_value["workspaces"][0]
            scoped_headers = {**headers,
                "X-MentorFlow-Organization-ID": workspace["organization_id"],
                "X-MentorFlow-Workspace-ID": workspace["id"]}

            after = c.get(WORKSPACES, headers=headers)
            assert after.status_code == 200
            assert _json_body(after) == initial_value
            trace.append(_http_projection(after))

            me = c.get("/api/enterprise/auth/me", headers=scoped_headers)
            assert me.status_code == 200, me.text
            trace.append(_http_projection(me))

            repeated = c.post(WORKSPACES + "/initialize", json={}, headers=headers)
            assert repeated.status_code == 200
            assert _json_body(repeated) == initial_value
            trace.append(_http_projection(repeated))

            invalid = c.post(WORKSPACES + "/initialize", json={"workspace_id": "attacker-choice"}, headers=headers)
            assert invalid.status_code == 422
            trace.append(_http_projection(invalid))
        rows = _workspace_bootstrap_rows(pair, backend, subject)
        # Ensure this scenario actually exercised every bootstrap-owned row family.
        assert len(rows["organizations"]) == len(rows["organization_memberships"]) == 1
        assert len(rows["workspaces"]) == len(rows["workspace_memberships"]) == 1
        assert len(rows["workspace_role_bindings"]) == len(rows["workspace_profiles"]) == 1
        assert len(rows["rehydration_manifests"]) == 1
        outcomes[backend] = {"trace": trace, "rows": rows}

    # IDs are deterministic length-prefixed issuer/subject hashes; timestamps are
    # normalized explicitly by the projection, while all persisted semantic columns
    # are compared row by row without sorting JSON or hiding state.
    _assert_trace_equal(outcomes["python"]["trace"], outcomes["java"]["trace"])
    assert outcomes["python"]["rows"] == outcomes["java"]["rows"]
    manifest = outcomes["python"]["rows"]["rehydration_manifests"][0]
    assert manifest[1] == subject and manifest[5:7] == (True, 1)


def test_lifecycle146_reset_observation_valid_errors_and_boundary_contract(lifecycle_http146):
    pair = lifecycle_http146
    outcomes = {}
    for backend in ("python", "java"):
        authority = pair["identities"][backend]
        trace = []
        with _client(pair, backend) as c:
            login = c.post(NATIVE + "/login", json={"username": "alice", "password": "correct"})
            assert login.status_code == 200
            headers = {"Authorization": "Bearer " + _json_body(login)["access_token"]}

            state = "S" * 43
            watched = c.post(RESET_OBSERVATION + "/watch", json={"oidc_state": state})
            assert watched.status_code == 200
            assert _json_body(watched) == {"schema": "mentorflow.reset-completion.v1",
                "transaction_id": state, "status": "PENDING"}
            trace.append(_http_projection(watched))
            status = c.post(RESET_OBSERVATION + "/status", json={"transaction_id": state})
            assert status.status_code == 200 and _json_body(status)["transaction_id"] == state
            trace.append(_http_projection(status))
            authority.reset_observation_status = "EXPIRED"
            expired = c.post(RESET_OBSERVATION + "/status", json={"transaction_id": state})
            assert expired.status_code == 200 and _json_body(expired)["status"] == "EXPIRED"
            trace.append(_http_projection(expired))
            authority.reset_observation_status = None

            short_state = c.post(RESET_OBSERVATION + "/watch", json={"oidc_state": "short"})
            assert short_state.status_code == 400 and _json_body(short_state)["code"] == "RESET_REQUEST_INVALID"
            trace.append(_http_projection(short_state))

            malformed = c.post(RESET_OBSERVATION + "/watch", content=b'{"oidc_state":"' + state.encode() + b'","extra":true}',
                headers={"Content-Type": "application/json"})
            assert malformed.status_code == 400 and _json_body(malformed)["code"] == "RESET_REQUEST_INVALID"
            trace.append(_http_projection(malformed))
            duplicate = c.post(RESET_OBSERVATION + "/status", content=b'{"transaction_id":"' + state.encode() +
                b'","transaction_id":"' + state.encode() + b'"}', headers={"Content-Type": "application/json"})
            assert duplicate.status_code == 400 and _json_body(duplicate)["code"] == "RESET_REQUEST_INVALID"
            trace.append(_http_projection(duplicate))
            wrong_transport = c.post(RESET_OBSERVATION + "/watch", json={"oidc_state": state},
                headers={**headers, "Origin": "https://client.invalid"})
            assert wrong_transport.status_code == 403 and _json_body(wrong_transport)["code"] == "RESET_TRANSPORT_REQUIRED"
            trace.append(_http_projection(wrong_transport))
            wrong_method = c.get(RESET_OBSERVATION + "/watch")
            assert wrong_method.status_code == 405
            trace.append(_http_projection(wrong_method))

            authority.reset_observation_schema = "wrong.schema"
            bad_upstream = c.post(RESET_OBSERVATION + "/watch", json={"oidc_state": "T" * 43})
            assert bad_upstream.status_code == 503
            assert _json_body(bad_upstream)["code"] == "RESET_OBSERVATION_UNAVAILABLE"
            trace.append(_http_projection(bad_upstream))
            bad_status = c.post(RESET_OBSERVATION + "/status", json={"transaction_id": state})
            assert bad_status.status_code == 503
            assert _json_body(bad_status)["code"] == "RESET_OBSERVATION_UNAVAILABLE"
            trace.append(_http_projection(bad_status))
            authority.reset_observation_schema = None

            unavailable_forgot = c.post(LIFECYCLE_FORGOT, json={"email": "unknown@fixture.invalid"})
            assert unavailable_forgot.status_code == 410
            assert _json_body(unavailable_forgot)["code"] == "RESET_CODE_REQUIRED"
            trace.append(_http_projection(unavailable_forgot))

            available = c.post(LIFECYCLE + "/register/check", json={"field": "username", "value": "unused146"})
            assert available.status_code == 200 and _json_body(available)["available"] is True
            trace.append(_http_projection(available))
            unavailable = c.post(LIFECYCLE + "/register/check", json={"field": "email", "value": "alice146@fixture.invalid"})
            assert unavailable.status_code == 200 and _json_body(unavailable)["available"] is False
            trace.append(_http_projection(unavailable))
            invalid_check = c.post(LIFECYCLE + "/register/check", content=b'{"field":"username","value":"x","extra":1}',
                headers={"Content-Type": "application/json"})
            assert invalid_check.status_code == 400 and _json_body(invalid_check)["code"] == "REGISTRATION_INPUT_INVALID"
            trace.append(_http_projection(invalid_check))
            duplicate_check = c.post(LIFECYCLE + "/register/check", content=b'{"field":"username","field":"email","value":"x"}',
                headers={"Content-Type": "application/json"})
            assert duplicate_check.status_code == 400 and _json_body(duplicate_check)["code"] == "REGISTRATION_INPUT_INVALID"
            trace.append(_http_projection(duplicate_check))
            wrong_method = c.get(LIFECYCLE + "/register/check")
            assert wrong_method.status_code == 405
            trace.append(_http_projection(wrong_method))
        outcomes[backend] = trace
    _assert_trace_equal(outcomes["python"], outcomes["java"])

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app import db, local_auth, orm


def test_first_user_registration_hashes_password_and_claims_existing_workspaces():
    with orm.SessionLocal() as session:
        session.add(orm.WorkspaceORM(id="ws-existing", title="Existing", description=""))
        session.commit()

    authenticated = local_auth.register(
        username="Owner_01",
        display_name="本地学习者",
        password="Correct-Horse-42",
        claim_existing_data=True,
    )

    assert authenticated.token
    assert local_auth.authenticate(authenticated.token)["user_id"] == authenticated.user_id
    assert "ws-existing" in local_auth.owned_workspace_ids(authenticated.user_id)
    with sqlite3.connect(orm.ORM_DB) as connection:
        row = connection.execute(
            "SELECT username_normalized,password_hash FROM orm_local_users WHERE id=?",
            (authenticated.user_id,),
        ).fetchone()
        assert row[0] == "owner_01"
        assert row[1].startswith("$argon2id$")
        assert "Correct-Horse-42" not in row[1]
        token_hash = connection.execute(
            "SELECT token_hash FROM orm_local_sessions WHERE user_id=?",
            (authenticated.user_id,),
        ).fetchone()[0]
        assert authenticated.token not in token_hash


def test_phone_binding_and_sms_reset_revoke_all_sessions(monkeypatch):
    sent: dict[str, str] = {}

    def fake_sms(**payload):
        sent.update({str(key): str(value) for key, value in payload.items()})

    monkeypatch.setattr("app.sms_gateway.send_verification_code", fake_sms)
    registered = local_auth.register(
        username="Recover_01",
        display_name="Recover",
        password="Correct-Horse-42",
        claim_existing_data=True,
    )
    second_session = local_auth.login(username="Recover_01", password="Correct-Horse-42")
    assert local_auth.authenticate(second_session.token)

    binding = local_auth.request_phone_binding(
        user_id=registered.user_id,
        phone="+8613812345678",
        current_password="Correct-Horse-42",
    )
    assert binding["challenge_id"] == sent["challenge_id"]
    local_auth.confirm_phone_binding(
        user_id=registered.user_id,
        challenge_id=binding["challenge_id"],
        code=sent["code"],
    )
    assert local_auth.phone_status(registered.user_id) == {"bound": True, "phone_masked": "+86*******5678"}

    local_auth.request_password_reset(username="recover_01")
    reset_code = sent["code"]
    local_auth.reset_password_with_phone_code(
        username="recover_01", code=reset_code, new_password="New-Password-43"
    )
    assert local_auth.authenticate(registered.token) is None
    assert local_auth.authenticate(second_session.token) is None
    assert local_auth.login(username="recover_01", password="New-Password-43").user_id == registered.user_id
    with pytest.raises(local_auth.LocalAuthError) as caught:
        local_auth.reset_password_with_phone_code(
            username="recover_01", code=reset_code, new_password="Another-Password-44"
        )
    assert caught.value.code == "PHONE_CODE_INVALID"


def test_unbound_phone_cannot_generate_a_reset(monkeypatch):
    sent = []
    monkeypatch.setattr("app.sms_gateway.send_verification_code", lambda **payload: sent.append(payload))
    local_auth.register(
        username="NoPhone_01",
        display_name="No phone",
        password="Correct-Horse-42",
        claim_existing_data=True,
    )
    local_auth.request_password_reset(username="NoPhone_01")
    assert sent == []


def test_login_is_generic_on_failure_and_logout_revokes_session():
    local_auth.register(
        username="Owner_02",
        display_name="Owner",
        password="Correct-Horse-42",
        claim_existing_data=True,
    )
    for username, password in (
        ("missing", "Correct-Horse-42"),
        ("Owner_02", "Incorrect-Horse-42"),
    ):
        with pytest.raises(local_auth.LocalAuthError) as caught:
            local_auth.login(username=username, password=password)
        assert caught.value.code == "INVALID_CREDENTIALS"

    session = local_auth.login(username="owner_02", password="Correct-Horse-42")
    assert local_auth.authenticate(session.token)
    local_auth.logout(session.token)
    assert local_auth.authenticate(session.token) is None


def test_change_password_reauthenticates_and_rotates_all_sessions():
    registered = local_auth.register(
        username="Change_Password_01",
        display_name="Change password",
        password="Correct-Horse-42",
        claim_existing_data=True,
    )
    second_session = local_auth.login(
        username="Change_Password_01", password="Correct-Horse-42"
    )

    changed = local_auth.change_password(
        user_id=registered.user_id,
        current_password="Correct-Horse-42",
        new_password="New-Password-43",
    )

    assert changed.token != registered.token
    assert changed.token != second_session.token
    assert local_auth.authenticate(changed.token)
    assert local_auth.authenticate(registered.token) is None
    assert local_auth.authenticate(second_session.token) is None
    assert local_auth.login(
        username="change_password_01", password="New-Password-43"
    ).user_id == registered.user_id
    with pytest.raises(local_auth.LocalAuthError) as caught:
        local_auth.change_password(
            user_id=registered.user_id,
            current_password="New-Password-43",
            new_password="New-Password-43",
        )
    assert caught.value.code == "PASSWORD_REUSE"


def test_failed_logins_are_throttled_and_success_clears_the_counter():
    local_auth.register(
        username="Throttle_01",
        display_name="Throttle",
        password="Correct-Horse-42",
        claim_existing_data=True,
    )
    for _ in range(local_auth.LOGIN_MAX_FAILED_ATTEMPTS):
        with pytest.raises(local_auth.LocalAuthError) as caught:
            local_auth.login(username="Throttle_01", password="Wrong-Horse-42")
        assert caught.value.code == "INVALID_CREDENTIALS"
    with sqlite3.connect(orm.ORM_DB) as connection:
        failed, locked_until = connection.execute(
            "SELECT failed_login_count,locked_until FROM orm_local_users WHERE username_normalized='throttle_01'"
        ).fetchone()
        assert failed == local_auth.LOGIN_MAX_FAILED_ATTEMPTS
        assert locked_until
    with pytest.raises(local_auth.LocalAuthError) as caught:
        local_auth.login(username="Throttle_01", password="Correct-Horse-42")
    assert caught.value.code == "INVALID_CREDENTIALS"
    with sqlite3.connect(orm.ORM_DB) as connection:
        connection.execute(
            "UPDATE orm_local_users SET locked_until='2000-01-01T00:00:00+00:00' WHERE username_normalized='throttle_01'"
        )
        connection.commit()
    local_auth.login(username="Throttle_01", password="Correct-Horse-42")
    with sqlite3.connect(orm.ORM_DB) as connection:
        assert connection.execute(
            "SELECT failed_login_count,locked_until FROM orm_local_users WHERE username_normalized='throttle_01'"
        ).fetchone() == (0, None)


def test_only_first_local_user_may_claim_existing_data():
    local_auth.register(
        username="Owner_03",
        display_name="Owner",
        password="Correct-Horse-42",
        claim_existing_data=True,
    )
    with pytest.raises(local_auth.LocalAuthError) as caught:
        local_auth.register(
            username="Other_03",
            display_name="Other",
            password="Another-Horse-43",
            claim_existing_data=True,
        )
    assert caught.value.code == "EXISTING_DATA_CLAIM_FORBIDDEN"


def test_first_user_cannot_leave_existing_data_unowned():
    with orm.SessionLocal() as session:
        session.add(orm.WorkspaceORM(id="ws-must-be-claimed", title="Existing", description=""))
        session.commit()

    with pytest.raises(local_auth.LocalAuthError) as caught:
        local_auth.register(
            username="Unclaimed_01",
            display_name="Unclaimed",
            password="Correct-Horse-42",
            claim_existing_data=False,
        )
    assert caught.value.code == "INITIAL_DATA_CLAIM_REQUIRED"
    assert local_auth.user_count() == 0


def test_same_normalized_username_can_only_be_registered_once_concurrently():
    barrier = Barrier(2)

    def create(username: str) -> str:
        barrier.wait()
        try:
            session = local_auth.register(
                username=username,
                display_name=username,
                password="Correct-Horse-42",
                claim_existing_data=False,
            )
            return f"ok:{session.user_id}"
        except local_auth.LocalAuthError as exc:
            return f"error:{exc.code}"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ("Shared_User", "shared_user")))

    assert sum(item.startswith("ok:") for item in results) == 1
    assert results.count("error:USERNAME_EXISTS") == 1
    with sqlite3.connect(orm.ORM_DB) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM orm_local_users WHERE username_normalized='shared_user'"
        ).fetchone()[0] == 1


def test_auth_api_cookie_and_desktop_gate(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "1")
    blocked = client.get("/api/workspaces")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "AUTH_REQUIRED"

    registered = client.post(
        "/api/auth/register",
        json={
            "username": "Desktop_01",
            "display_name": "桌面用户",
            "password": "Correct-Horse-42",
            "claim_existing_data": True,
        },
    )
    assert registered.status_code == 201, registered.text
    assert "recovery_code" not in registered.json()
    assert "mentorflow_session=" in registered.headers["set-cookie"]
    assert "HttpOnly" in registered.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/workspaces").status_code == 200

    logged_out = client.post("/api/auth/logout")
    assert logged_out.status_code == 204
    assert client.get("/api/workspaces").status_code == 401


def test_auth_api_password_change_rotates_cookie(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "1")
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "Change_Api_01",
            "display_name": "Change API",
            "password": "Correct-Horse-42",
            "claim_existing_data": True,
        },
    )
    assert registered.status_code == 201
    old_cookie = client.cookies.get("mentorflow_session")

    changed = client.post(
        "/api/auth/password/change",
        json={
            "current_password": "Correct-Horse-42",
            "new_password": "New-Password-43",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["message"] == "密码已更新，其他设备会话已失效。"
    assert client.cookies.get("mentorflow_session") != old_cookie
    assert client.get("/api/auth/me").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    assert client.post(
        "/api/auth/login",
        json={"username": "change_api_01", "password": "Correct-Horse-42"},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "change_api_01", "password": "New-Password-43"},
    ).status_code == 200


def test_multiple_accounts_have_isolated_workspaces_and_restore_their_own_history(
    client, monkeypatch
):
    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "1")
    first = client.post(
        "/api/auth/register",
        json={
            "username": "Learner_A",
            "display_name": "学习者 A",
            "password": "Correct-Horse-42",
            "claim_existing_data": True,
        },
    )
    assert first.status_code == 201, first.text
    workspace_a = client.post(
        "/api/workspaces",
        json={"title": "A 的私有任务", "execution_mode": "demo"},
    )
    assert workspace_a.status_code == 200, workspace_a.text
    workspace_a_id = workspace_a.json()["id"]
    source_a = client.post(
        f"/api/workspaces/{workspace_a_id}/sources/text",
        json={"title": "A 的资料", "text": "仅属于 A 的哨兵内容。"},
    )
    assert source_a.status_code == 200, source_a.text
    source_a_id = source_a.json()["id"]
    with db.connection() as connection:
        connection.execute(
            """INSERT INTO plans(id,workspace_id,version,payload,status,created_at,activated_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("plan-account-a", workspace_a_id, 1, "{}", "draft", db.now(), ""),
        )
        connection.execute(
            """INSERT INTO sessions(id,workspace_id,lesson_id,payload,created_at,updated_at)
               VALUES (?,?,?,?,?,?)""",
            ("session-account-a", workspace_a_id, "", "{}", db.now(), db.now()),
        )
    assert client.post("/api/auth/logout").status_code == 204

    second = client.post(
        "/api/auth/register",
        json={
            "username": "Learner_B",
            "display_name": "学习者 B",
            "password": "Another-Horse-43",
            "claim_existing_data": False,
        },
    )
    assert second.status_code == 201, second.text
    assert client.get("/api/workspaces").json() == []
    denied_a = client.get(f"/api/workspaces/{workspace_a_id}")
    assert denied_a.status_code == 403
    assert denied_a.json()["error"]["code"] == "WORKSPACE_ACCESS_DENIED"
    for private_path in (
        f"/api/sources/{source_a_id}",
        "/api/plans/plan-account-a",
        "/api/sessions/session-account-a",
    ):
        private_response = client.get(private_path)
        assert private_response.status_code == 403, private_path
        assert private_response.json()["error"]["code"] == "WORKSPACE_ACCESS_DENIED"

    workspace_b = client.post(
        "/api/workspaces",
        json={"title": "B 的私有任务", "execution_mode": "demo"},
    )
    assert workspace_b.status_code == 200, workspace_b.text
    workspace_b_id = workspace_b.json()["id"]
    assert [item["id"] for item in client.get("/api/workspaces").json()] == [workspace_b_id]
    assert client.post("/api/auth/logout").status_code == 204

    relogin = client.post(
        "/api/auth/login",
        json={"username": "learner_a", "password": "Correct-Horse-42"},
    )
    assert relogin.status_code == 200, relogin.text
    assert [item["id"] for item in client.get("/api/workspaces").json()] == [workspace_a_id]
    assert client.get(f"/api/workspaces/{workspace_a_id}").status_code == 200
    assert client.get(f"/api/workspaces/{workspace_b_id}").status_code == 403


def test_auth_api_allows_additional_registration_but_rejects_duplicate_username(
    client, monkeypatch
):
    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "1")
    for username in ("Account_One", "Account_Two"):
        response = client.post(
            "/api/auth/register",
            json={
                "username": username,
                "display_name": username,
                "password": "Correct-Horse-42",
                "claim_existing_data": username == "Account_One",
            },
        )
        assert response.status_code == 201, response.text
    duplicate = client.post(
        "/api/auth/register",
        json={
            "username": "account_two",
            "display_name": "duplicate",
            "password": "Correct-Horse-42",
            "claim_existing_data": False,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "USERNAME_EXISTS"


def test_auth_api_phone_binding_and_reset_flow(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "1")
    sent: dict[str, str] = {}

    def fake_sms(**payload):
        sent.update({str(key): str(value) for key, value in payload.items()})

    monkeypatch.setattr("app.sms_gateway.send_verification_code", fake_sms)
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "Phone_Api_01",
            "display_name": "Phone API",
            "password": "Correct-Horse-42",
            "claim_existing_data": True,
        },
    )
    assert registered.status_code == 201
    binding = client.post(
        "/api/auth/phone/bind/request",
        json={"phone_e164": "+8613812345678", "current_password": "Correct-Horse-42"},
    )
    assert binding.status_code == 200
    confirmed = client.post(
        "/api/auth/phone/bind/confirm",
        json={"challenge_id": binding.json()["challenge_id"], "code": sent["code"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["bound"] is True
    assert client.post("/api/auth/logout").status_code == 204

    requested = client.post("/api/auth/password-reset/request", json={"username": "phone_api_01"})
    assert requested.status_code == 202
    reset = client.post(
        "/api/auth/password-reset/confirm",
        json={"username": "phone_api_01", "code": sent["code"], "new_password": "New-Password-43"},
    )
    assert reset.status_code == 200
    assert client.post(
        "/api/auth/login",
        json={"username": "phone_api_01", "password": "New-Password-43"},
    ).status_code == 200


def test_password_reset_request_hides_provider_failures(client, monkeypatch):
    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "1")

    def rejected(**_kwargs):
        raise local_auth.LocalAuthError(
            "SMS_PROVIDER_UNAVAILABLE", "短信服务当前不可用，请稍后重试。"
        )

    monkeypatch.setattr("app.local_auth.request_password_reset", rejected)
    response = client.post(
        "/api/auth/password-reset/request",
        json={"username": "tonyswift"},
    )
    assert response.status_code == 202
    assert response.json()["accepted"] is True


def test_qt_native_modules_import_without_creating_a_window(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from desktop import api, auth_window, main_window, runtime, style

    assert api.DesktopApi
    assert auth_window.AuthWindow
    assert main_window.MainWindow
    assert runtime.LocalService
    assert "QWidget" in style.STYLE

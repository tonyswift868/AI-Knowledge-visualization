"""Regression tests for account-profile If-Match version parsing (BUG-189)."""
import asyncio
import io

import httpx
from PIL import Image
import pytest
from sqlalchemy import create_engine, text

from refactor.server import app as account_core
from refactor.server.enterprise.account_profiles import AccountProfileService, AVATAR_PATH, PROFILE_PATH
from contract_support import ORIGIN, runtime


def _service(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'profile229.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE account_profiles (
            account_scope TEXT PRIMARY KEY, nickname TEXT NOT NULL DEFAULT '',
            signature TEXT NOT NULL DEFAULT '', avatar_bytes BLOB, avatar_etag TEXT,
            avatar_width INTEGER, avatar_height INTEGER, version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""))
    monkeypatch.setattr(account_core.app.state, "enterprise_identity_runtime",
                        runtime("refactor.server"), raising=False)
    monkeypatch.setattr(account_core.app.state, "account_profile_service",
                        AccountProfileService(engine), raising=False)
    return engine


def _png():
    output = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(output, "PNG")
    return output.getvalue()


def _invalid(response):
    assert response.status_code == 400, response.text
    assert response.json() == {
        "error": {
            "code": "ACCOUNT_PROFILE_IF_MATCH_INVALID",
            "message": "资料版本标记无效",
            "details": {},
            "request_id": response.headers["X-Request-ID"],
        },
    }


def test_account_profile229_redteam_5000_digit_patch_is_a_parameter_error(tmp_path, monkeypatch):
    async def run():
        engine = _service(tmp_path, monkeypatch)
        headers = {"Authorization": "Bearer token-a"}
        value = '"profile-' + "9" * 5000 + '"'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=account_core.app, raise_app_exceptions=False), base_url=ORIGIN) as client:
            before = await client.get(PROFILE_PATH, headers=headers)
            response = await client.patch(PROFILE_PATH,
                headers={**headers, "If-Match": value},
                json={"nickname": "ShouldNotPersist", "signature": "replay"})
            after = await client.get(PROFILE_PATH, headers=headers)
        with engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT nickname, signature, version FROM account_profiles")).fetchall()
        assert before.status_code == after.status_code == 200
        assert before.json() == after.json() == {
            "nickname": "", "signature": "", "version": 1, "avatar": None,
        }
        assert rows == []
        engine.dispose()
        _invalid(response)

        strict = httpx.ASGITransport(app=account_core.app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=strict, base_url=ORIGIN) as client:
            again = await client.patch(PROFILE_PATH,
                headers={**headers, "If-Match": value},
                json={"nickname": "ShouldNotPersist", "signature": "replay"})
        _invalid(again)

    asyncio.run(run())


@pytest.mark.parametrize("method,path,content_type", [
    ("PATCH", PROFILE_PATH, "application/json"),
    ("PUT", AVATAR_PATH, "image/png"),
    ("DELETE", AVATAR_PATH, None),
])
def test_account_profile229_all_mutations_share_5000_digit_validation(
        tmp_path, monkeypatch, method, path, content_type):
    async def run():
        engine = _service(tmp_path, monkeypatch)
        headers = {"Authorization": "Bearer token-a",
                   "If-Match": '"profile-' + "9" * 5000 + '"'}
        if content_type:
            headers["Content-Type"] = content_type
        kwargs = {"json": {"nickname": "No", "signature": ""}} if method == "PATCH" else {}
        if method == "PUT":
            kwargs["content"] = _png()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=account_core.app, raise_app_exceptions=False), base_url=ORIGIN) as client:
            response = await client.request(method, path, headers=headers, **kwargs)
        engine.dispose()
        _invalid(response)

    asyncio.run(run())


def test_account_profile229_if_match_numeric_and_cas_boundaries(tmp_path, monkeypatch):
    async def run():
        engine = _service(tmp_path, monkeypatch)
        headers = {"Authorization": "Bearer token-a"}
        invalid_values = (
            '"profile-x"', '"profile--1"', '"profile-0"',
            '"profile-9223372036854775808"', '"profile-' + "1" * 20 + '"',
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=account_core.app, raise_app_exceptions=False), base_url=ORIGIN) as client:
            for value in invalid_values:
                response = await client.patch(PROFILE_PATH,
                    headers={**headers, "If-Match": value},
                    json={"nickname": "No", "signature": ""})
                _invalid(response)

            max_version = '"profile-9223372036854775807"'
            max_result = await client.patch(PROFILE_PATH,
                headers={**headers, "If-Match": max_version},
                json={"nickname": "No", "signature": ""})
            assert max_result.status_code == 412

            first = await client.patch(PROFILE_PATH,
                headers={**headers, "If-Match": '"profile-1"'},
                json={"nickname": "Valid", "signature": ""})
            assert first.status_code == 200 and first.json()["version"] == 2
            stale = await client.patch(PROFILE_PATH,
                headers={**headers, "If-Match": '"profile-1"'},
                json={"nickname": "Stale", "signature": ""})
            assert stale.status_code == 412
            current = await client.get(PROFILE_PATH, headers=headers)
            assert current.json()["nickname"] == "Valid" and current.json()["version"] == 2
        engine.dispose()

    asyncio.run(run())

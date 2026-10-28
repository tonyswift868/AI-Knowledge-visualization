"""Account profile HTTP contract without a live identity provider or database."""
import asyncio
import io

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from refactor.server import app as account_core
from refactor.server.enterprise.account_profiles import (
    AccountProfileService, AVATAR_PATH, PROFILE_PATH, account_scope,
)
from contract_support import ORIGIN, runtime


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'profile.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE account_profiles (
            account_scope TEXT PRIMARY KEY, nickname TEXT NOT NULL DEFAULT '',
            signature TEXT NOT NULL DEFAULT '', avatar_bytes BLOB, avatar_etag TEXT,
            avatar_width INTEGER, avatar_height INTEGER, version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""))
    return engine


def _png():
    image = Image.new("RGBA", (64, 64), (20, 80, 140, 120))
    result = io.BytesIO()
    image.save(result, format="PNG")
    return result.getvalue()


def _jpeg_with_exif():
    image = Image.new("RGB", (64, 64), "teal")
    exif = Image.Exif()
    exif[270] = "private location metadata"
    result = io.BytesIO()
    image.save(result, format="JPEG", exif=exif)
    return result.getvalue()


@pytest.mark.parametrize("who", ["a", "b"])
def test_account_profile_owner_is_derived_from_bearer(tmp_path, monkeypatch, who):
    async def run():
        engine = _engine(tmp_path)
        app = account_core.app
        monkeypatch.setattr(app.state, "enterprise_identity_runtime", runtime("refactor.server"), raising=False)
        monkeypatch.setattr(app.state, "account_profile_service", AccountProfileService(engine), raising=False)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            headers = {"Authorization": f"Bearer token-{who}"}
            initial = await client.get(PROFILE_PATH, headers=headers)
            assert initial.status_code == 200
            assert initial.json() == {"nickname": "", "signature": "", "version": 1, "avatar": None}
            assert initial.headers["cache-control"] == "private, no-cache"
            changed = await client.patch(PROFILE_PATH, headers={**headers, "If-Match": '"profile-1"'},
                                         json={"nickname": "Display", "signature": "hello"})
            assert changed.status_code == 200
            assert changed.json()["nickname"] == "Display"
            assert changed.json()["version"] == 2
            assert changed.headers["etag"] == '"profile-2"'
            other = await client.get(PROFILE_PATH, headers={"Authorization": "Bearer token-b" if who == "a" else "Bearer token-a"})
            assert other.json()["nickname"] == ""
            assert account_scope("https://identity.example.test/realms/mentorflow", f"user-{who}") != account_scope(
                "https://identity.example.test/realms/mentorflow", "user-b" if who == "a" else "user-a")
        engine.dispose()
    asyncio.run(run())


def test_profile_rejects_anonymous_owner_claim_and_stale_revision(tmp_path, monkeypatch):
    async def run():
        engine = _engine(tmp_path)
        app = account_core.app
        monkeypatch.setattr(app.state, "enterprise_identity_runtime", runtime("refactor.server"), raising=False)
        monkeypatch.setattr(app.state, "account_profile_service", AccountProfileService(engine), raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            assert (await client.get(PROFILE_PATH)).status_code == 401
            headers = {"Authorization": "Bearer token-a", "If-Match": '"profile-1"'}
            missing = await client.patch(PROFILE_PATH, headers={"Authorization": "Bearer token-a"},
                                         json={"nickname": "x", "signature": ""})
            assert missing.status_code == 428
            for malformed in ('profile-1', '"profile-0"', '"profile-1", "profile-2"',
                              'W/"profile-1"', f'"profile-{1 << 63}"'):
                invalid = await client.patch(PROFILE_PATH,
                    headers={"Authorization": "Bearer token-a", "If-Match": malformed},
                    json={"nickname": "x", "signature": ""})
                assert invalid.status_code == 400
            spoof = await client.patch(PROFILE_PATH, headers=headers,
                                       json={"nickname": "x", "signature": "", "account_id": "someone-else"})
            assert spoof.status_code == 422
            assert (await client.patch(PROFILE_PATH, headers=headers,
                                      json={"nickname": "x", "signature": ""})).status_code == 200
            stale = await client.patch(PROFILE_PATH, headers=headers,
                                       json={"nickname": "y", "signature": ""})
            assert stale.status_code == 412
        engine.dispose()
    asyncio.run(run())


def test_profile_limits_text_by_unicode_codepoints_and_rejects_limits(tmp_path, monkeypatch):
    async def run():
        engine = _engine(tmp_path)
        app = account_core.app
        monkeypatch.setattr(app.state, "enterprise_identity_runtime", runtime("refactor.server"), raising=False)
        monkeypatch.setattr(app.state, "account_profile_service", AccountProfileService(engine), raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            headers = {"Authorization": "Bearer token-a", "If-Match": '"profile-1"'}
            ok = await client.patch(PROFILE_PATH, headers=headers,
                                    json={"nickname": "名" * 30, "signature": "字" * 60})
            assert ok.status_code == 200
            headers["If-Match"] = '"profile-2"'
            assert (await client.patch(PROFILE_PATH, headers=headers,
                                      json={"nickname": "名" * 31, "signature": ""})).status_code == 422
            assert (await client.patch(PROFILE_PATH, headers=headers,
                                      json={"nickname": "", "signature": "字" * 61})).status_code == 422
            control = await client.patch(PROFILE_PATH, headers=headers,
                                         json={"nickname": "line\nbreak", "signature": ""})
            assert control.status_code == 422
            surrogate = await client.patch(PROFILE_PATH,
                headers={**headers, "Content-Type": "application/json"},
                content=b'{"nickname":"\\ud800","signature":""}')
            assert surrogate.status_code == 422
            over = await client.patch(PROFILE_PATH, headers={**headers, "Content-Type": "application/json"},
                                      content=b" " * 4097)
            assert over.status_code == 413
        engine.dispose()
    asyncio.run(run())


def test_avatar_is_validated_reencoded_and_etag_is_private(tmp_path, monkeypatch):
    async def run():
        engine = _engine(tmp_path)
        app = account_core.app
        monkeypatch.setattr(app.state, "enterprise_identity_runtime", runtime("refactor.server"), raising=False)
        monkeypatch.setattr(app.state, "account_profile_service", AccountProfileService(engine), raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            headers = {"Authorization": "Bearer token-a", "If-Match": '"profile-1"', "Content-Type": "image/jpeg"}
            bad = await client.put(AVATAR_PATH, headers=headers, content=b"not a jpeg")
            assert bad.status_code == 422
            assert (await client.put(AVATAR_PATH, headers={**headers, "Content-Type": "image/svg+xml"},
                                     content=_png())).status_code == 415
            original = _jpeg_with_exif()
            assert Image.open(io.BytesIO(original)).getexif()[270] == "private location metadata"
            accepted = await client.put(AVATAR_PATH, headers=headers, content=original)
            assert accepted.status_code == 200
            profile = accepted.json()
            assert profile["avatar"]["width"] == profile["avatar"]["height"] == 64
            etag = profile["avatar"]["etag"]
            fetched = await client.get(AVATAR_PATH, headers={"Authorization": "Bearer token-a"})
            assert etag.startswith('"') and etag.endswith('"')
            assert fetched.status_code == 200 and fetched.headers["etag"] == etag
            assert fetched.headers["cache-control"] == "private, no-cache"
            normalized = Image.open(io.BytesIO(fetched.content))
            assert normalized.format == "JPEG" and normalized.size == (64, 64)
            assert not normalized.getexif()
            assert (await client.get(AVATAR_PATH, headers={"Authorization": "Bearer token-a",
                                                            "If-None-Match": etag})).status_code == 304
            other = await client.get(AVATAR_PATH, headers={"Authorization": "Bearer token-b",
                                                           "If-None-Match": f'W/{etag}, "other", *'})
            assert other.status_code == 404
            weak_list = await client.get(AVATAR_PATH, headers={"Authorization": "Bearer token-a",
                                                               "If-None-Match": f'"other", W/{etag}'})
            assert weak_list.status_code == 304
            wildcard = await client.get(AVATAR_PATH, headers={"Authorization": "Bearer token-a",
                                                               "If-None-Match": "*"})
            assert wildcard.status_code == 304
            assert (await client.delete(AVATAR_PATH, headers={"Authorization": "Bearer token-a",
                                                               "If-Match": '"profile-2"'})).json()["avatar"] is None
            too_large = await client.put(AVATAR_PATH, headers={**headers, "If-Match": '"profile-2"'},
                                         content=b"x" * (262_144 + 1))
            assert too_large.status_code == 413
        engine.dispose()
    asyncio.run(run())


def test_avatar_rejects_over_limit_and_non_square_or_oversized_image(tmp_path, monkeypatch):
    from refactor.server.enterprise.account_profiles import AccountProfileError, MAX_AVATAR_BYTES, _normalize_image

    with pytest.raises(AccountProfileError) as too_big:
        _normalize_image(b"x" * (MAX_AVATAR_BYTES + 1), "image/png")
    assert too_big.value.status == 413
    for size in ((20, 21), (513, 513)):
        image = Image.new("RGB", size, "red")
        data = io.BytesIO()
        image.save(data, format="PNG")
        with pytest.raises(AccountProfileError):
            _normalize_image(data.getvalue(), "image/png")


def test_profile226_postgres_migration_closure_and_downgrade(tmp_path, monkeypatch):
    """Exercise the real 0046 SQL, closure cleanup, and rollback on disposable PG."""
    import psycopg
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server_java"))
    import java_build156 as java_build
    import support_rag154 as support
    from sqlalchemy import create_engine
    from refactor.server.account_identity_contract import account_id

    ready, reason = java_build.ensure_java_build()
    assert ready, reason
    root = tmp_path / "postgres226"
    root.mkdir()
    # Keep only the disposable PostgreSQL instance under this test's tmp_path.
    # Java builds are shared by the server_java fixture and ensure_java_build()
    # caches its result for the pytest process; replacing its build/classpath
    # paths here can make this test request a classpath file that was never
    # generated when another fixture initialized the shared build first.
    monkeypatch.setattr(support, "TASK_ROOT", root)
    monkeypatch.setattr(support, "BUILD", java_build.BUILD)
    monkeypatch.setattr(support, "CLASSPATH_FILE", java_build.CLASSPATH_FILE)
    classpath_file = support._ensure_classpath()
    classpath = classpath_file.read_text(encoding="utf-8").strip()
    if not classpath:
        raise RuntimeError("Java embedded PostgreSQL classpath was empty")
    postgres = support._EmbeddedPostgres(classpath)
    issuer = "https://identity.example.test/realms/profile226"
    subject = "profile226-owner"
    other_subject = "profile226-other"
    admin_url = None
    app_engine = None
    try:
        app_url, admin_url, migration_url = support._prepare_database(
            postgres, "profile226", issuer)
        app_engine = create_engine(app_url)
        service = AccountProfileService(app_engine)
        pg_conninfo = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
        assert service.get(issuer, subject) == {
            "nickname": "", "signature": "", "version": 1, "avatar": None}
        service.update(issuer, subject, "Private name", "My signature", 1)
        service.update(issuer, other_subject, "Other name", "Other signature", 1)
        target_scope = account_scope(issuer, subject)
        other_scope = account_scope(issuer, other_subject)
        with psycopg.connect(pg_conninfo) as conn:
            assert conn.execute("SELECT count(*) FROM public.account_profiles").fetchone()[0] == 2
            operation, digest, claim = "a" * 32, "b" * 64, "c" * 32
            account = account_id(issuer, subject)
            prepared = conn.execute(
                "SELECT account_closure.prepare(%s,%s,%s,%s,%s)",
                (issuer, subject, account, operation, digest)).fetchone()[0]
            assert prepared["state"] == "PREPARED", prepared
            acquired = conn.execute(
                "SELECT account_closure.acquire(%s,%s,%s)",
                (operation, digest, claim)).fetchone()[0]
            assert acquired["state"] == "IN_PROGRESS", acquired
        with psycopg.connect(pg_conninfo) as conn:
            cleaned = conn.execute(
                "SELECT account_closure.clean(%s,%s,%s)",
                (operation, digest, claim)).fetchone()[0]
            assert cleaned["data_cleaned"] is True, cleaned
            assert conn.execute("SELECT count(*) FROM public.account_profiles WHERE account_scope=%s",
                                (target_scope,)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM public.account_profiles WHERE account_scope=%s",
                                (other_scope,)).fetchone()[0] == 1
            done = conn.execute(
                "SELECT account_closure.provider_done(%s,%s,%s)",
                (operation, digest, claim)).fetchone()[0]
            assert done["identity_deleted"] is True, done
            completed = conn.execute(
                "SELECT account_closure.complete(%s,%s,%s)",
                (operation, digest, claim)).fetchone()[0]
            assert completed["state"] == "COMPLETED", completed
        rollback_log = root / "downgrade-0045.log"
        with pytest.raises(RuntimeError, match="Alembic downgrade"):
            support._run_alembic(migration_url, ["downgrade", "0045_account_closure_rag"], rollback_log, issuer)
        rollback_text = rollback_log.read_text(encoding="utf-8")
        assert "Forward only" in rollback_text and "permanently delete user profile data" in rollback_text
        with psycopg.connect(pg_conninfo) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0046_account_profiles"
            assert conn.execute("SELECT to_regclass('public.account_profiles')").fetchone()[0] is not None
            clean_sql = conn.execute(
                "SELECT pg_get_functiondef('account_closure.clean(text,text,text)'::regprocedure)").fetchone()[0]
            complete_sql = conn.execute(
                "SELECT pg_get_functiondef('account_closure.complete(text,text,text)'::regprocedure)").fetchone()[0]
            assert "account_profiles" in clean_sql
            assert "account_profiles" in complete_sql
    finally:
        if app_engine is not None:
            app_engine.dispose()
        postgres.stop()

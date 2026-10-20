"""The real desktop account/sync clients across Python and Java HTTPS edges."""
from __future__ import annotations

import asyncio
import json
import os
import ssl
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from contract_support144 import OLD_ISSUER
import contract_support147 as c147
from cutover_support149 import ARTIFACTS, client_cutover149


def _config(base_url):
    from refactor.desktop.account_protocol import AccountConfig
    return AccountConfig(issuer=OLD_ISSUER, identity_issuer=OLD_ISSUER,
        client_id="mentorflow-desktop", audience="mentorflow-api", api_base_url=base_url,
        organization_id=c147.ORG, workspace_id=c147.WORKSPACE)


def _login(config, ca, username="client149a"):
    observed = []

    async def record(response):
        await response.aread()
        try:
            code = response.json().get("code")
        except Exception:
            code = None
        observed.append({"method": response.request.method,
                         "path": response.request.url.path,
                         "status": response.status_code, "code": code})

    async def run():
        from refactor.desktop.native_auth import NativeCredentialClient
        client = httpx.AsyncClient(verify=ssl.create_default_context(cafile=str(ca)),
            trust_env=False, timeout=25, event_hooks={"response": [record]})
        service = NativeCredentialClient(config, http_client=client)
        try:
            result = await service.login(username, "correct")
            return result
        finally:
            await client.aclose()

    try:
        return asyncio.run(run()), observed, None
    except Exception as exc:
        return None, observed, exc


def test_client_before_scheme_fix149_real_desktop_maps_java_403(
        client_cutover149, monkeypatch):
    fixture = client_cutover149
    # The original pre-fix jar was exercised and its result retained as an
    # artifact; keep this regression runnable after that temporary jar is
    # cleaned by using the current jar with the former `none` strategy.
    before_jar = Path(os.environ.get("JAVA149_FIXED_JAR",
        str(fixture["root_build"] / "mentorflow-server-0.1.0.jar")))
    assert before_jar.is_file(), "149 current Java jar must be prepared by the root build"
    py_config = _config(fixture["proxies"]["python"].base_url)
    py_login, py_observed, py_error = _login(py_config, fixture["tls"]["ca"], "client149a")
    assert py_error is None, repr(py_error)
    assert any(item["path"].endswith("/native/login") and item["status"] == 200
               for item in py_observed)

    java_endpoint, java_proxy = fixture["start_java"](
        "before", before_jar, forward_strategy="none")
    try:
        java_config = _config(java_proxy.base_url)
        java_login, java_observed, java_error = _login(java_config, fixture["tls"]["ca"], "client149b")
        assert java_login is None
        assert getattr(java_error, "code", None) == "NATIVE_AUTH_UNAVAILABLE"
        native_login = [item for item in java_observed if item["path"].endswith("/native/login")]
        assert len(native_login) == 1
        assert native_login[0]["status"] == 403
        # The specific server body is retained by the response hook, while the
        # actual desktop client deliberately maps this unrecognized 403 code.
        assert native_login[0]["code"] == "NATIVE_TRANSPORT_REQUIRED"
        direct = httpx.post(java_endpoint["base_url"] + "/api/enterprise/auth/native/login",
            json={"username": "client149b", "password": "correct"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            trust_env=False, timeout=10)
        assert direct.status_code == 403
        assert direct.json()["code"] == "NATIVE_TRANSPORT_REQUIRED"

        # Seed an actual Python-issued desktop credential, then exercise the
        # real LoginSession.restore path against the same Java HTTP edge.
        settings = _MemorySettings()
        from refactor.desktop.account_identity import AccountPreferenceStore
        from refactor.desktop.login_session import LoginSession
        preferences = AccountPreferenceStore(settings)
        credentials = _MemoryCredentialVault()
        session = LoginSession(preferences, credentials=credentials)
        session.accept(py_login, remember=True, automatic=True, remember_password=False,
                       username="client149a")
        _set_desktop_env(monkeypatch, java_proxy.base_url, fixture["tls"]["ca"])

        import refactor.desktop.native_auth as native_auth
        real_async_client = httpx.AsyncClient
        restore_observed = []

        async def record_restore(response):
            await response.aread()
            try:
                code = response.json().get("code")
            except Exception:
                code = None
            restore_observed.append({"method": response.request.method,
                "path": response.request.url.path, "status": response.status_code,
                "code": code})

        def recording_async_client(*args, **kwargs):
            hooks = dict(kwargs.get("event_hooks") or {})
            hooks["response"] = [*hooks.get("response", []), record_restore]
            kwargs["event_hooks"] = hooks
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(native_auth.httpx, "AsyncClient", recording_async_client)
        with pytest.raises(Exception) as restore_failure:
            session.restore(py_login.identity.profile(), threading.Event())
        assert getattr(restore_failure.value, "code", None) == "NATIVE_AUTH_UNAVAILABLE"
        refresh = [item for item in restore_observed
                   if item["path"].endswith("/native/refresh")]
        assert len(refresh) == 1
        assert refresh[0]["status"] == 403
        assert refresh[0]["code"] == "NATIVE_TRANSPORT_REQUIRED"
    finally:
        fixture["stop_java"]("before")


class _MemorySettings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=""):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)

    def sync(self):
        pass

    def status(self):
        return SimpleNamespace(value=0)

    def fileName(self):
        return "149-memory-settings"

    def allKeys(self):
        return list(self.values)


class _MemoryCredentialVault:
    def __init__(self):
        self.tokens = {}
        self.offline_identities = {}

    def store_oauth_tokens(self, identity, *, access_token, refresh_token):
        self.tokens[identity.profile().account_id] = (access_token, refresh_token)

    def load_oauth_tokens(self, profile):
        return self.tokens[profile.account_id]

    def logout(self, profile):
        self.tokens.pop(profile.account_id, None)
        self.offline_identities.pop(profile.account_id, None)

    def store_offline_identity(self, identity, value):
        self.offline_identities[identity.account_id] = value

    def load_offline_identity(self, profile):
        from refactor.desktop.account_protocol import AccountError
        try:
            return self.offline_identities[profile.account_id]
        except KeyError:
            raise AccountError('CREDENTIAL_NOT_FOUND', 'missing offline identity')

    def delete_offline_identity(self, profile):
        self.offline_identities.pop(profile.account_id, None)


def _set_desktop_env(monkeypatch, base_url, ca):
    for key, value in {
        "MENTORFLOW_DESKTOP_OIDC_ISSUER": OLD_ISSUER,
        "MENTORFLOW_DESKTOP_IDENTITY_ISSUER": OLD_ISSUER,
        "MENTORFLOW_DESKTOP_OIDC_CLIENT_ID": "mentorflow-desktop",
        "MENTORFLOW_DESKTOP_OIDC_AUDIENCE": "mentorflow-api",
        "MENTORFLOW_ENTERPRISE_API_BASE_URL": base_url,
        "MENTORFLOW_DESKTOP_ORGANIZATION_ID": c147.ORG,
        "MENTORFLOW_DESKTOP_WORKSPACE_ID": c147.WORKSPACE,
        "MENTORFLOW_DESKTOP_OIDC_HTTP_TIMEOUT_SECONDS": "10",
        "SSL_CERT_FILE": str(ca),
    }.items():
        monkeypatch.setenv(key, value)


def _desktop_flow(base_url, ca, username):
    """Login, actual LoginSession.restore, sync, remote logout, and closure."""
    from refactor.desktop.account_identity import AccountPreferenceStore
    from refactor.desktop.login_session import LoginSession
    from refactor.desktop.native_auth import NativeCredentialClient
    from refactor.learning.sync_transport import SyncTransport
    from refactor.desktop.account_closure_client import ClosureClient

    config = _config(base_url)
    verify = ssl.create_default_context(cafile=str(ca))
    async def login():
        client = httpx.AsyncClient(verify=verify, trust_env=False, timeout=25)
        service = NativeCredentialClient(config, http_client=client)
        try:
            return await service.login(username, "correct")
        finally:
            await client.aclose()
    first = asyncio.run(login())

    settings = _MemorySettings()
    preferences = AccountPreferenceStore(settings)
    credentials = _MemoryCredentialVault()
    profile = first.identity.profile()
    session = LoginSession(preferences, credentials=credentials)
    session.accept(first, remember=True, automatic=True, remember_password=False,
                   username=username)
    restored = session.restore(profile, threading.Event())

    transport = SyncTransport(account_id=restored.identity.account_id,
        session_provider=lambda: restored, api_base_url=base_url, ca_bundle=Path(ca))
    snapshot = transport.read_snapshot()
    assert snapshot["schema"] == "mentorflow.structured-sync.v1"
    import hashlib
    record_id = "client149:" + username
    entity_id = "refactor.v1:course_record:" + hashlib.sha256(record_id.encode()).hexdigest()
    values = {"schema": "mentorflow.course.module.v1", "plan_key": "client149",
              "entity": "module", "entity_id": record_id,
              "payload": {"title": "client sync", "nodes": []}}
    payload = {"schema": "mentorflow.refactor-sync.v1", "version": 1,
               "record_type": "course_record", "record_id": record_id, "values": values}
    written = transport.write("learning_run", entity_id,
        request_id="client149-write-" + username, expected_revision=0, payload=payload)
    assert written["revision"] == 1
    transport.close()

    logout_ok = session.logout(profile, remote=True)
    assert logout_ok is True

    # A separate re-login exercises the supplied ClosureClient's prepare/status/
    # commit path without relying on an HTTP request handcrafted by the fixture.
    closure_login = asyncio.run(login())
    closure_profile = closure_login.identity.profile()
    closure_credentials = _MemoryCredentialVault()
    closure_credentials.store_oauth_tokens(closure_login.identity,
        access_token=closure_login.access_token, refresh_token=closure_login.refresh_token)
    closure = ClosureClient(config, closure_profile, credentials=closure_credentials,
        http_factory=lambda **kwargs: httpx.Client(verify=verify, trust_env=False, **kwargs))
    prepared = closure.prepare("correct")
    assert closure.status({"account_id": closure_profile.account_id,
        "api_base_url": base_url, "operation_id": prepared["operation_id"]},
        prepared["recovery_token"]) == "PREPARED"
    closed = closure.status({"account_id": closure_profile.account_id,
        "api_base_url": base_url, "operation_id": prepared["operation_id"]},
        prepared["recovery_token"], commit=True)
    assert closed == "COMPLETED"
    return {"login": "200", "restore": "200", "sync_snapshot": "200",
            "sync_write": "200", "remote_logout": logout_ok,
            "account_closure": closed}


@pytest.mark.parametrize("backend,username", [("python", "client149c"), ("java", "client149d")])
def test_desktop_login_restore_sync_logout_and_closure149(
        backend, username, client_cutover149, monkeypatch):
    fixture = client_cutover149
    if backend == "python":
        proxy = fixture["proxies"]["python"]
    else:
        jar = Path(os.environ.get("JAVA149_FIXED_JAR",
            str(fixture["root_build"] / "mentorflow-server-0.1.0.jar")))
        assert jar.is_file(), "149 fixed Java jar must be prepared by the root build"
        _, proxy = fixture["start_java"]("fixed", jar)
    _set_desktop_env(monkeypatch, proxy.base_url, fixture["tls"]["ca"])
    if backend == "java":
        # Simulate the cutover direction Python -> Java with a real refresh
        # token issued by Python against the same PostgreSQL database. The
        # desktop restore path must rotate it at Java and then sync there.
        py_config = _config(fixture["proxies"]["python"].base_url)
        py_login, py_observed, py_error = _login(
            py_config, fixture["tls"]["ca"], "client149e")
        assert py_error is None, repr(py_error)
        assert any(item["path"].endswith("/native/login") and item["status"] == 200
                   for item in py_observed)
        from refactor.desktop.account_identity import AccountPreferenceStore
        from refactor.desktop.login_session import LoginSession
        settings = _MemorySettings()
        preferences = AccountPreferenceStore(settings)
        credentials = _MemoryCredentialVault()
        session = LoginSession(preferences, credentials=credentials)
        session.accept(py_login, remember=True, automatic=True, remember_password=False,
                       username="client149e")
        cross_profile = py_login.identity.profile()
        cross_restore = session.restore(cross_profile, threading.Event())
        from refactor.learning.sync_transport import SyncTransport
        transport = SyncTransport(account_id=cross_restore.identity.account_id,
            session_provider=lambda: cross_restore, api_base_url=proxy.base_url,
            ca_bundle=Path(fixture["tls"]["ca"]))
        cross_snapshot = transport.read_snapshot()
        assert cross_snapshot["schema"] == "mentorflow.structured-sync.v1"
        import hashlib
        record_id = "client149:python-to-java"
        entity_id = "refactor.v1:course_record:" + hashlib.sha256(
            record_id.encode()).hexdigest()
        values = {"schema": "mentorflow.course.module.v1", "plan_key": "client149",
                  "entity": "module", "entity_id": record_id,
                  "payload": {"title": "Python to Java refresh", "nodes": []}}
        payload = {"schema": "mentorflow.refactor-sync.v1", "version": 1,
                   "record_type": "course_record", "record_id": record_id,
                   "values": values}
        written = transport.write("learning_run", entity_id,
            request_id="client149-python-to-java", expected_revision=0, payload=payload)
        assert written["revision"] == 1
        transport.close()
    result = _desktop_flow(proxy.base_url, fixture["tls"]["ca"], username)
    assert result["remote_logout"] is True
    assert result["account_closure"] == "COMPLETED"

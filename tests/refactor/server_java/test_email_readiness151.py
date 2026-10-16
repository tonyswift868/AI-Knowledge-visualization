"""The real readiness endpoints observe the same identity-owned mail probe."""
import json
import ssl

import httpx
import pytest

from contract_support144 import (FakeKeycloak144, make_tls144, prepare_databases144,
    start_java144, start_postgres144, start_python144, stop_postgres144, stop_server144)


@pytest.fixture(scope="module")
def email_database151(tmp_path_factory):
    run = tmp_path_factory.mktemp("email151")
    pg = start_postgres144(run)
    try:
        database = prepare_databases144(pg["port"], ("email151",))["email151"]
        yield run, database
    finally:
        stop_postgres144(pg)


@pytest.mark.parametrize("value,expected", [
    ({"status": "READY", "code": "EMAIL_TLS_AUTH_READY"}, (True, "EMAIL_TLS_AUTH_READY")),
    ({"status": "UNAVAILABLE", "code": "EMAIL_CONFIGURATION_UNAVAILABLE"},
        (False, "EMAIL_CONFIGURATION_UNAVAILABLE")),
    ({"status": "READY"}, (False, "EMAIL_TRANSPORT_UNAVAILABLE")),
])
def test_email_readiness151_python_java_authority_and_cache(email_database151, tmp_path, value, expected):
    _, database = email_database151
    tls = make_tls144(tmp_path / "tls")
    identity = FakeKeycloak144(tls)
    operations = []
    # Replace only the fake SPI's mail-health operation. No SMTP send or real
    # identity is involved; both production backends still perform actual mTLS.
    def post(handler):
        if not handler._require_client_certificate():
            return
        body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", "0"))))
        operations.append(body)
        if body == {"operation": "email-transport-health"}:
            handler._reply(200, value)
        else:
            handler._reply(404, {})
    identity.server.RequestHandlerClass.do_POST = post
    servers = []
    try:
        for name, launch in (("python", start_python144), ("java", start_java144)):
            endpoint = launch(database, identity, tls, tmp_path / name)
            servers.append(endpoint)
            with httpx.Client(base_url=endpoint["base_url"], trust_env=False,
                    verify=ssl.create_default_context(cafile=str(tls["ca"])), timeout=20) as client:
                for _ in range(2):
                    response = client.get("/health/ready")
                    email = next(d for d in response.json()["dependencies"] if d["name"] == "email")
                    assert (email["ready"], email["code"]) == expected
                    assert email["required"] is False
                    assert email["duration_ms"] >= 0
        # Each backend sampled once, then reused its existing 60-second cache.
        assert operations == [{"operation": "email-transport-health"}] * 2
    finally:
        for endpoint in reversed(servers):
            stop_server144(endpoint)
        identity.close()

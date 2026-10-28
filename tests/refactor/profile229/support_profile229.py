"""Real loopback HTTP/PG fixtures reusing the existing server contract harness."""
from __future__ import annotations
from contextlib import ExitStack
import io
from pathlib import Path
import ssl
import sys
import uuid
import httpx
from PIL import Image
import pytest

_JAVA_TESTS = Path(__file__).resolve().parents[1] / "server_java"
if str(_JAVA_TESTS) not in sys.path:
    sys.path.insert(0, str(_JAVA_TESTS))


def png229(color="red", size=(32, 32)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


def new_account229(env, label: str) -> dict:
    from contract_support144 import seed_tenant144
    from test_account_profile226_http import _signed_token
    from refactor.server.account_identity_contract import account_id
    from refactor.server.enterprise.account_profiles import account_scope
    subject = f"profile229-{label}-{uuid.uuid4().hex}"
    seed_tenant144(env["seed_url"], env["issuer"], subject)
    return {"subject": subject, "issuer": env["issuer"],
            "scope": account_scope(env["issuer"], subject),
            "account_id": account_id(env["issuer"], subject),
            "headers": {"Authorization": "Bearer " + _signed_token(env["fake"], subject)}}


@pytest.fixture(scope="session")
def profile_env229(tmp_path_factory):
    import contract_support144 as c
    import java_build156 as jb
    from test_issuer_http144 import _java_truststore144
    ready, reason = jb.ensure_java_build()
    if not ready:
        pytest.skip(reason)
    run = tmp_path_factory.mktemp("profile229")
    scratch = run / "tmp"
    scratch.mkdir()
    with ExitStack() as stack:
        patch = stack.enter_context(pytest.MonkeyPatch.context())
        for name in ("TEMP", "TMP", "TMPDIR", "LOCALAPPDATA"):
            patch.setenv(name, str(scratch))
        patch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        patch.setenv("JAVA142_ARTIFACTS", str(jb.BUILD))
        patch.setattr(c, "LOCAL", run)
        patch.setattr(c, "CLASSPATH_FILE", jb.CLASSPATH_FILE)
        pg_root = run / "postgres"
        pg_root.mkdir()
        pg = c.start_postgres144(pg_root)
        stack.callback(c.stop_postgres144, pg)
        app_url = c.prepare_databases144(pg["port"], ("profile229",))["profile229"]
        tls = c.make_tls144(run / "tls")
        fake = c.FakeKeycloak144(tls)
        stack.callback(fake.close)
        trust_root = run / "truststore"
        trust_root.mkdir()
        trust, password = _java_truststore144(tls["ca"], trust_root)
        patch.setenv("SSL_CERT_FILE", str(tls["ca"]))
        patch.setenv("JAVA_TOOL_OPTIONS", " ".join((
            f'-Djavax.net.ssl.trustStore="{trust}"',
            f"-Djavax.net.ssl.trustStorePassword={password}",
            "-Djavax.net.ssl.trustStoreType=PKCS12",
            f'-Djava.io.tmpdir="{scratch}"', "-Djava.awt.headless=true")))
        clients, endpoints = {}, {}
        for name, launch in (("python", c.start_python144), ("java", c.start_java144)):
            endpoint = launch(app_url, fake, tls, run / name)
            stack.callback(c.stop_server144, endpoint)
            endpoints[name] = endpoint
            clients[name] = stack.enter_context(httpx.Client(
                base_url=endpoint["base_url"], verify=ssl.create_default_context(cafile=tls["ca"]),
                trust_env=False, timeout=45))
        yield {
            "clients": clients, "pg_admin": f"host=127.0.0.1 port={pg['port']} user=postgres dbname=profile229",
            "db_url": app_url, "seed_url": f"postgresql+psycopg://postgres@127.0.0.1:{pg['port']}/profile229",
            "fake": fake, "tls": tls, "run": run, "issuer": c.OLD_ISSUER, "endpoints": endpoints}

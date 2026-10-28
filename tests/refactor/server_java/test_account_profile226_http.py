"""Real local Java HTTP, bearer identity, PostgreSQL profile and ETag contract."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
import httpx

from contract_support144 import OLD_ISSUER, free_port, stop_server144, wait_server144
from java_build156 import BUILD, CLASSPATH_FILE, JAVA_HOME, ROOT
from test_issuer_http144 import _java_truststore144

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a"
    "3ioAAAAASUVORK5CYII="
)


def _signed_token(identity, subject: str) -> str:
    def segment(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()

    now = int(time.time())
    header = segment({"alg": "RS256", "typ": "JWT", "kid": identity.jwt_kid})
    claims = segment({
        "iss": OLD_ISSUER,
        "aud": "mentorflow-api", "azp": "mentorflow-desktop", "client_id": "mentorflow-desktop",
        "sub": subject, "jti": "profile226-" + subject,
        "iat": now, "exp": now + 300,
    })
    signing_input = (header + "." + claims).encode("ascii")
    signature = identity.jwt_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return header + "." + claims + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def _start_profile_java(fixture) -> dict:
    run = Path(fixture["run"]) / "profile226-java"
    run.mkdir(parents=True, exist_ok=True)
    truststore, password = _java_truststore144(fixture["tls"]["ca"], run)
    env = dict(fixture["java"]["env"])
    port = free_port()
    env["PORT"] = str(port)
    classpath = os.pathsep.join((str(BUILD / "classes"), str(BUILD / "test-classes"),
                                 CLASSPATH_FILE.read_text(encoding="utf-8").strip()))
    log_path = run / "java.log"
    log = log_path.open("wb")
    process = subprocess.Popen(
        [str(JAVA_HOME / "bin/java.exe"), "-Djavax.net.ssl.trustStore=" + str(truststore),
         "-Djavax.net.ssl.trustStorePassword=" + password, "-Djavax.net.ssl.trustStoreType=PKCS12",
         "-Djava.awt.headless=true", "-cp", classpath, "contract144.Contract144Main"],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    endpoint = {"base_url": f"https://localhost:{port}", "process": process,
                "log_path": log_path, "log": log, "env": env}
    wait_server144(endpoint)
    return endpoint


def test_java_profile226_http_auth_cas_avatar_etag_and_account_isolation(native_http144, request):
    fixture = native_http144
    endpoint = _start_profile_java(fixture)
    request.addfinalizer(lambda: stop_server144(endpoint))
    client = httpx.Client(base_url=endpoint["base_url"], verify=str(fixture["tls"]["ca"]),
                         timeout=30, trust_env=False)
    request.addfinalizer(client.close)
    identity = fixture["fake"]
    alice = _signed_token(identity, "subject144-alice")
    bob = _signed_token(identity, "subject144-bob")
    profile_url = "/api/enterprise/account/profile"
    avatar_url = profile_url + "/avatar"

    missing_auth = client.get(profile_url)
    assert missing_auth.status_code == 401, missing_auth.text

    # The account-only profile route succeeds without workspace/organization headers.
    headers = {"Authorization": "Bearer " + alice}
    initial = client.get(profile_url, headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json() == {"nickname": "", "signature": "", "version": 1, "avatar": None}
    assert initial.headers["cache-control"] == "private, no-cache"

    missing_version = client.patch(profile_url, headers=headers,
                                   json={"nickname": "学习者", "signature": "签名"})
    assert missing_version.status_code == 428, missing_version.text
    malformed_version = client.patch(profile_url, headers={**headers, "If-Match": 'W/"profile-1"'},
                                     json={"nickname": "学习者", "signature": "签名"})
    assert malformed_version.status_code == 400, malformed_version.text
    invalid_text = client.patch(profile_url, headers={**headers, "If-Match": '"profile-1"'},
                                json={"nickname": "字" * 31, "signature": "签名"})
    assert invalid_text.status_code == 422, invalid_text.text

    stale = client.delete(avatar_url, headers={**headers, "If-Match": '"profile-0"'})
    assert stale.status_code == 400, stale.text
    first = client.patch(profile_url, headers={**headers, "If-Match": '"profile-1"'},
                         json={"nickname": "学习者", "signature": "签名"})
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 2

    # A stale version conflicts even when no avatar exists; empty-profile no-op
    # is only valid while the row itself is still absent at version one.
    stale_delete = client.delete(avatar_url, headers={**headers, "If-Match": '"profile-1"'})
    assert stale_delete.status_code == 412, stale_delete.text

    uploaded = client.put(avatar_url, headers={**headers, "If-Match": '"profile-2"',
                                "Content-Type": "image/png"}, content=PNG_1X1)
    assert uploaded.status_code == 200, uploaded.text
    avatar = uploaded.json()["avatar"]
    assert uploaded.json()["version"] == 3
    assert avatar["width"] == avatar["height"] == 1
    assert avatar["etag"].startswith('"') and avatar["etag"].endswith('"')

    body = client.get(avatar_url, headers=headers)
    assert body.status_code == 200
    assert body.headers["content-type"].startswith("image/jpeg")
    assert body.headers["etag"] == avatar["etag"]
    assert body.headers["cache-control"] == "private, no-cache"
    for validator in (avatar["etag"], "W/" + avatar["etag"], '"unrelated", ' + avatar["etag"], "*"):
        conditional = client.get(avatar_url, headers={**headers, "If-None-Match": validator})
        assert conditional.status_code == 304, (validator, conditional.text)

    # A second login resolves to its own account scope; Alice's ETag cannot
    # produce a 304 for Bob and Bob cannot read Alice's profile.
    bob_profile = client.get(profile_url, headers={"Authorization": "Bearer " + bob})
    assert bob_profile.status_code == 200 and bob_profile.json()["nickname"] == ""
    bob_avatar = client.get(avatar_url, headers={"Authorization": "Bearer " + bob,
                                                 "If-None-Match": avatar["etag"]})
    assert bob_avatar.status_code == 404, bob_avatar.text

    deleted = client.delete(avatar_url, headers={**headers, "If-Match": '"profile-3"'})
    assert deleted.status_code == 200 and deleted.json()["version"] == 4
    final = client.get(profile_url, headers=headers)
    assert final.json()["nickname"] == "学习者" and final.json()["avatar"] is None

    assert alice != bob

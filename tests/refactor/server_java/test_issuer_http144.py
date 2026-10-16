"""HTTP issuer-migration tests over the production Python/Java application wiring.

All issuer/JWKS traffic stays on the ephemeral localhost Keycloak substitute.
The continuity signing key and Java truststore are generated for this test only.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from contract_support144 import (
    LOCAL,
    NEW_ISSUER,
    OLD_ISSUER,
    application_environment144,
    free_port,
    stop_server144,
    wait_server144,
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwt144(fake, subject: str) -> str:
    """Mint a short-lived NEW-issuer access JWT from the fake's local JWKS key."""
    now = int(time.time())
    header = {"alg": "RS256", "kid": fake.jwt_kid, "typ": "JWT"}
    claims = {
        "iss": NEW_ISSUER,
        "sub": subject,
        "aud": "mentorflow-api",
        "azp": "mentorflow-desktop",
        "iat": now,
        "exp": now + 240,
        "auth_time": now - 30,
        "jti": "issuer144-" + subject,
        "sid": "issuer144-session-" + subject,
        "preferred_username": "alice",
    }
    signed = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + _b64url(
        json.dumps(claims, separators=(",", ":")).encode()
    )
    signature = fake.jwt_key.sign(
        signed.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
    )
    return signed + "." + _b64url(signature)


def _local_jwks(fake) -> None:
    public = fake.jwt_key.public_key().public_numbers()
    fake.jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": fake.jwt_kid,
                "n": _b64url(public.n.to_bytes((public.n.bit_length() + 7) // 8, "big")),
                "e": _b64url(public.e.to_bytes((public.e.bit_length() + 7) // 8, "big")),
            }
        ]
    }


def _migration_window(active: bool) -> str:
    now = int(time.time())
    if active:
        before, after = now - 60, now + 3600
    else:
        before, after = now - 7200, now - 3600
    return json.dumps(
        {
            "not_before": before,
            "not_after": after,
            "authority_sha256": "5662cb790710dde77eaf1d8d49edd6e4d476426def0c2ded73146d38d8d0d550",
        },
        separators=(",", ":"),
    )


def _java_truststore144(ca_file: Path, directory: Path) -> tuple[Path, str]:
    java_home = Path(r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1")
    keytool = java_home / "bin" / "keytool.exe"
    if not keytool.exists():
        pytest.skip(f"test JDK keytool is unavailable: {keytool}")
    truststore = directory / "ephemeral-ca.p12"
    password = "local144trust"
    result = subprocess.run(
        [
            str(keytool),
            "-importcert",
            "-noprompt",
            "-alias",
            "auth144-ephemeral-ca",
            "-file",
            str(ca_file),
            "-keystore",
            str(truststore),
            "-storetype",
            "PKCS12",
            "-storepass",
            password,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return truststore, password


def _start_local144(fixture, backend: str, directory: Path, *, active_window: bool):
    """Launch an isolated server process with a controlled issuer window.

    This local launcher mirrors the fixture's production-app launch but only
    overrides the migration clock data and Java's test-only CA trust.
    """
    directory.mkdir(parents=True, exist_ok=True)
    source = fixture[backend]
    env = application_environment144(
        identity=fixture["fake"],
        tls=fixture["tls"],
        db_url=source["env"]["MENTORFLOW_DATABASE_URL"],
        directory=directory,
        port=free_port(),
        issuer=OLD_ISSUER,
        canonical=OLD_ISSUER,
    )
    env["MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON"] = _migration_window(active_window)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[3]), str(Path(__file__).parent), env.get("PYTHONPATH", "")]
    )
    log_path = directory / f"{backend}.log"
    log = log_path.open("wb")
    if backend == "python":
        env["SSL_CERT_FILE"] = str(fixture["tls"]["ca"])
        script = Path(__file__).with_name("python_server144.py")
        process = subprocess.Popen(
            [sys.executable, "-B", str(script)],
            cwd=Path(__file__).resolve().parents[3],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    else:
        truststore, password = _java_truststore144(fixture["tls"]["ca"], directory)
        java_home = Path(r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1")
        build = Path(os.environ.get("JAVA142_ARTIFACTS", str(LOCAL / "build")))
        classpath = str(build / "classes") + os.pathsep + str(build / "test-classes")
        classpath += os.pathsep + (LOCAL / "test-classpath.txt").read_text(encoding="utf-8").strip()
        env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
        env["JAVA_TOOL_OPTIONS"] = (
            f"-Djavax.net.ssl.trustStore={truststore} "
            f"-Djavax.net.ssl.trustStorePassword={password} "
            "-Djavax.net.ssl.trustStoreType=PKCS12"
        )
        process = subprocess.Popen(
            [str(java_home / "bin" / "java.exe"), "-cp", classpath, "contract144.Contract144Main"],
            cwd=Path(__file__).resolve().parents[3],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    endpoint = {
        "base_url": f"https://localhost:{env['PORT']}",
        "process": process,
        "log_path": log_path,
        "log": log,
        "env": env,
    }
    wait_server144(endpoint)
    return endpoint


def _context(fixture, endpoint, subject: str):
    token = _jwt144(fixture["fake"], subject)
    with httpx.Client(
        base_url=endpoint["base_url"],
        verify=str(fixture["tls"]["ca"]),
        timeout=12,
        trust_env=False,
    ) as client:
        return client.get(
            "/api/enterprise/contract144/context",
            headers={
                "Authorization": "Bearer " + token,
                "X-MentorFlow-Organization-ID": "org-144",
                "X-MentorFlow-Workspace-ID": "workspace-144",
                "X-Request-ID": "contract144-issuer",
            },
        )


@pytest.mark.parametrize("backend", ("python", "java"))
def test_issuer144_signed_continuity_and_migration_window(native_http144, backend):
    fixture = native_http144
    _local_jwks(fixture["fake"])
    run = fixture["run"] / "issuer-http144" / backend
    active = _start_local144(fixture, backend, run / "active", active_window=True)
    expired = None
    try:
        accepted = _context(fixture, active, "subject144-alice")
        assert accepted.status_code == 200, accepted.text
        actor = accepted.json()["actor"]
        assert actor["issuer"] == OLD_ISSUER
        assert actor["subject_id"] == "subject144-alice"

        unknown = _context(fixture, active, "unlisted-subject144")
        assert unknown.status_code == 401, unknown.text
        assert unknown.json()["error"]["code"] == "OIDC_IDENTITY_CONTINUITY_REQUIRED", unknown.text

        expired = _start_local144(fixture, backend, run / "expired", active_window=False)
        outside_window = _context(fixture, expired, "subject144-alice")
        assert outside_window.status_code == 401, outside_window.text
        assert outside_window.json()["error"]["code"] == "OIDC_ISSUER_INVALID", outside_window.text
        from test_native_contract144 import snapshot
        fixture["traces"]["issuer_" + backend] = [
            snapshot(response) for response in (accepted, unknown, outside_window)
        ]
    finally:
        if expired is not None:
            stop_server144(expired)
        stop_server144(active)


def test_issuer144_response_bytes_match(native_http144):
    traces = native_http144["traces"]
    assert traces["issuer_python"] == traces["issuer_java"]

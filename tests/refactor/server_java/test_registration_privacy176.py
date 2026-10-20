"""Registration email privacy contracts (task 176), local fixtures only."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from lifecycle_support146 import build_pair146, shutdown_pair146
from test_lifecycle_http146 import LIFECYCLE, NATIVE, _client, _http_projection, _json_body


ROOT = Path(__file__).resolve().parents[3]
JAVA_SOURCE = Path(__file__).parent / "java176" / "RegistrationCodesContract176.java"


def _java_tool(name: str) -> Path:
    java_home = Path(os.environ.get("JAVA_HOME", r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1"))
    candidate = java_home / "bin" / (name + ".exe")
    if not candidate.is_file():
        pytest.skip(f"Java 21 {name} is unavailable: {candidate}")
    return candidate


def test_registration176_real_spi_uses_uniform_ticket_and_distinct_mail(tmp_path):
    """Compile and call the real Keycloak SPI class with local API proxies."""
    jar_value = os.environ.get("SPI176_JAR")
    libs_value = os.environ.get("SPI176_LIBS")
    if not libs_value:
        pytest.skip("The task-local Keycloak SPI build is required for the real RegistrationCodes contract")
    libs = Path(libs_value)
    spi_jar = Path(jar_value) if jar_value else libs.parents[3] / "mentorflow-native-verifier.jar"
    if not spi_jar.is_file() or not libs.is_dir():
        pytest.skip("Task-local Keycloak SPI jar/dependencies are not available")
    if not any(libs.glob("*.jar")):
        pytest.skip("Task-local Keycloak API jars are not available")
    javac, java = _java_tool("javac"), _java_tool("java")
    classes = tmp_path / "classes"
    classes.mkdir()
    # Keep the classpath short enough for Windows command-line limits. Both
    # javac and java expand the standard directory wildcard for jar files.
    cp = os.pathsep.join((str(spi_jar), str(libs / "*")))
    subprocess.run([str(javac), "-encoding", "UTF-8", "-cp", cp, "-d", str(classes), str(JAVA_SOURCE)],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=180)
    result = subprocess.run([str(java), "-cp", os.pathsep.join((str(classes), cp)),
        "win.clannad.mentorflow.RegistrationCodesContract176"], cwd=ROOT,
        check=True, capture_output=True, text=True, timeout=30)
    match = re.search(r"REGISTRATION176=(\{.*\})", result.stdout)
    assert match, result.stdout
    import json
    observed = json.loads(match.group(1))
    assert observed == {
        "new_response_fields": 4,
        "existing_response_fields": 4,
        "check_email_schema_compatible": True,
        "new_mail_code": True,
        "existing_mail_guidance": True,
        "existing_resend_guidance": True,
        "existing_verify": "WRONG_CODE",
        "existing_account_mutations": 0,
        "policy_same": True,
        "username_conflict": "USERNAME_ALREADY_IN_USE",
        "username_conflict_mail_count": 0,
        "invalid_username": "INPUT_INVALID",
    }, observed


@pytest.fixture
def registration_http176(tmp_path_factory):
    pair = build_pair146(tmp_path_factory.mktemp("registration-http176"))
    try:
        yield pair
    finally:
        shutdown_pair146(pair)


def test_registration176_java_http_email_check_registration_and_recovery_compatibility(registration_http176):
    pair = registration_http176
    authority = pair["identities"]["java"]
    known_email = "registered176@fixture.invalid"
    authority.add_user("registered176", known_email, "valid-password-176")
    with _client(pair, "java") as client:
        # Email checks preserve the older response fields but no longer reveal availability.
        email_results = []
        for email in (known_email, "unused176@fixture.invalid"):
            response = client.post(LIFECYCLE + "/register/check", json={"field": "email", "value": email})
            assert response.status_code == 200
            assert _json_body(response) == {"field": "email", "available": True}
            email_results.append(_http_projection(response))
        assert email_results[0] == email_results[1]

        # Username lookup retains its original immediate-check behavior.
        for username, expected in (("registered176", False), ("unused176", True)):
            response = client.post(LIFECYCLE + "/register/check", json={"field": "username", "value": username})
            assert response.status_code == 200
            assert _json_body(response) == {"field": "username", "available": expected}

        # Old clients still receive the established CODE_SENT response shape.
        # The real SPI branch and its actual mail wording are checked above.
        starts = []
        for username, email in (("unused176a", known_email), ("unused176b", "fresh176@fixture.invalid")):
            response = client.post(LIFECYCLE + "/register", json={
                "username": username, "email": email, "password": "valid-password-176"})
            assert response.status_code == 201, response.text
            body = _json_body(response)
            assert set(body) == {"state", "credential", "expires_in", "resend_after"}
            assert body["state"] == "CODE_SENT"
            assert isinstance(body["credential"], str) and len(body["credential"]) == 43
            assert body["expires_in"] == 600 and body["resend_after"] == 60
            starts.append(body)
        assert starts[0].keys() == starts[1].keys()
        assert starts[0]["state"] == starts[1]["state"]
        assert starts[0]["expires_in"] == starts[1]["expires_in"]
        assert starts[0]["resend_after"] == starts[1]["resend_after"]

        # Mixed rollout with the older SPI: do not crash and do not translate its
        # EMAIL_ALREADY_IN_USE marker into a user-facing mailbox-exists message.
        authority.registration_start_override = lambda value: {"status": "EMAIL_ALREADY_IN_USE"}
        legacy = client.post(LIFECYCLE + "/register", json={
            "username": "legacy176", "email": known_email, "password": "valid-password-176"})
        authority.registration_start_override = None
        assert legacy.status_code == 503
        legacy_body = _json_body(legacy)
        assert legacy_body["code"] == "REGISTRATION_UNAVAILABLE"
        assert "已注册" not in legacy_body["message"] and "已被使用" not in legacy_body["message"]
        assert "credential" not in legacy_body and "expires_in" not in legacy_body

        # Password-recovery's anonymous response remains the same for a known and
        # unknown address; only the task-local SMTP sink can receive mail.
        reset_results = []
        for email in (known_email, "unknown176@fixture.invalid"):
            response = client.post(LIFECYCLE + "/reset-code/request", json={"email": email})
            assert response.status_code == 202
            body = _json_body(response)
            assert set(body) == {"state", "transaction_id", "expires_in", "resend_after", "message"}
            assert body["state"] == "CODE_SENT"
            reset_results.append(_http_projection(response))
        assert reset_results[0] == reset_results[1]

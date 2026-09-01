from __future__ import annotations

import hashlib
import json

from app import credential_store
from app.provider_runtime import create_provider_session, delete_provider_credential


def test_real_provider_secret_uses_credential_reference(monkeypatch):
    monkeypatch.setattr(
        "app.provider_runtime.get_provider",
        lambda config: object(),
    )
    credential_ref = credential_store.store_secret("credential-secret")
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://provider.invalid/v1",
            "model": "configured-model",
            "credential_ref": credential_ref,
        }
    )

    assert public["credential_ref"].startswith(credential_store.CREDENTIAL_PREFIX)
    assert "credential-secret" not in json.dumps(public)
    assert credential_store.get_secret(public["credential_ref"]) == "credential-secret"


def test_existing_credential_reference_survives_session_recreation(monkeypatch):
    monkeypatch.setattr(
        "app.provider_runtime.get_provider",
        lambda config: object(),
    )
    credential_ref = credential_store.store_secret("credential-secret")
    first = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://provider.invalid/v1",
            "model": "configured-model",
            "credential_ref": credential_ref,
        }
    )
    restored = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://provider.invalid/v1",
            "model": "configured-model",
            "credential_ref": first["credential_ref"],
        }
    )

    assert restored["credential_ref"] == first["credential_ref"]
    assert delete_provider_credential(first["credential_ref"]) is True


def test_raw_provider_secret_is_rejected_before_session_creation(monkeypatch):
    monkeypatch.setattr("app.provider_runtime.get_provider", lambda config: object())

    try:
        create_provider_session(
            {
                "provider": "openai-compatible",
                "base_url": "https://provider.invalid/v1",
                "model": "configured-model",
                "api_key": "must-not-flow",
            }
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "RAW_PROVIDER_CREDENTIAL_FORBIDDEN"
    else:
        raise AssertionError("raw Provider credentials must be rejected")


def test_cloud_environment_secret_store_reads_but_does_not_write(monkeypatch):
    monkeypatch.setenv("MENTORFLOW_CREDENTIAL_STORE", "environment")
    reference = "mfcred_cloud_provider"
    name = credential_store.environment_secret_name(reference)
    monkeypatch.setenv(name, "cloud-secret")
    assert credential_store.get_secret(reference) == "cloud-secret"
    try:
        credential_store.store_secret("new-secret", reference)
    except credential_store.CredentialStoreError as exc:
        assert exc.code == "CREDENTIAL_STORE_WRITE_FORBIDDEN"
    else:
        raise AssertionError("cloud secrets must be provisioned outside the business API")


def test_account_secret_namespace_isolates_issuer_subject_and_purpose():
    issuer_a = hashlib.sha256(b"https://login.example.test/a").hexdigest()
    issuer_b = hashlib.sha256(b"https://login.example.test/b").hexdigest()
    subject_a = hashlib.sha256(b"subject-a").hexdigest()
    subject_b = hashlib.sha256(b"subject-b").hexdigest()

    access_a = credential_store.store_account_secret(
        "access-a-secret",
        issuer_hash=issuer_a,
        subject_hash=subject_a,
        purpose="oauth-access-token",
    )
    refresh_a = credential_store.store_account_secret(
        "refresh-a-secret",
        issuer_hash=issuer_a,
        subject_hash=subject_a,
        purpose="oauth-refresh-token",
    )
    access_b = credential_store.store_account_secret(
        "access-b-secret",
        issuer_hash=issuer_b,
        subject_hash=subject_b,
        purpose="oauth-access-token",
    )

    assert len({access_a, refresh_a, access_b}) == 3
    assert access_a.startswith(credential_store.ACCOUNT_CREDENTIAL_PREFIX)
    assert issuer_a[:24] not in access_a
    assert subject_a[:24] not in access_a
    assert "subject-a" not in access_a
    assert "access-a-secret" not in access_a
    assert credential_store.get_account_secret(
        issuer_hash=issuer_a,
        subject_hash=subject_a,
        purpose="oauth-access-token",
    ) == "access-a-secret"
    assert credential_store.get_account_secret(
        issuer_hash=issuer_b,
        subject_hash=subject_b,
        purpose="oauth-access-token",
    ) == "access-b-secret"
    assert credential_store.delete_account_secret(
        issuer_hash=issuer_a,
        subject_hash=subject_a,
        purpose="oauth-access-token",
    ) is True
    assert credential_store.get_account_secret(
        issuer_hash=issuer_b,
        subject_hash=subject_b,
        purpose="oauth-access-token",
    ) == "access-b-secret"


def test_account_secret_namespace_rejects_unhashed_identity_and_bad_purpose():
    valid = hashlib.sha256(b"valid").hexdigest()
    for issuer, subject, purpose in (
        ("raw-issuer", valid, "oauth-access-token"),
        (valid, "raw-subject", "oauth-access-token"),
        (valid, valid, "oauth/access/token"),
    ):
        try:
            credential_store.account_credential_reference(
                issuer,
                subject,
                purpose,
            )
        except credential_store.CredentialStoreError as exc:
            assert exc.code == "CREDENTIAL_NAMESPACE_INVALID"
        else:
            raise AssertionError("invalid account credential namespace must fail closed")

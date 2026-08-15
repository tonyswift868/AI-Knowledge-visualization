from __future__ import annotations

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

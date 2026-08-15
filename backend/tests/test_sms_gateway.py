from __future__ import annotations

import json

import httpx
import pytest

from app import sms_gateway


def test_sms_gateway_config_persists_metadata_without_a_secret(tmp_path, monkeypatch):
    config_path = tmp_path / "sms_gateway.json"
    monkeypatch.setenv("MENTORFLOW_SMS_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("MENTORFLOW_SMS_GATEWAY_URL", raising=False)
    monkeypatch.delenv("MENTORFLOW_SMS_SEND_PATH", raising=False)
    monkeypatch.delenv("MENTORFLOW_SMS_CREDENTIAL_REF", raising=False)

    sms_gateway.save_config(
        base_url="https://sms.example.test",
        send_path="/v1/messages",
        credential_ref="mfcred_test_sms",
    )
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload == {
        "base_url": "https://sms.example.test",
        "send_path": "/v1/messages",
        "credential_ref": "mfcred_test_sms",
    }
    assert "secret" not in config_path.read_text(encoding="utf-8")
    assert sms_gateway.public_config() == {
        "configured": True,
        "base_url": "https://sms.example.test",
        "send_path": "/v1/messages",
        "credential_ref": "mfcred_test_sms",
    }


def test_sms_gateway_requires_explicit_acceptance(monkeypatch, tmp_path):
    config_path = tmp_path / "sms_gateway.json"
    monkeypatch.setenv("MENTORFLOW_SMS_CONFIG_PATH", str(config_path))
    sms_gateway.save_config(
        base_url="https://sms.example.test",
        send_path="/v1/messages",
        credential_ref="mfcred_test_sms",
    )
    monkeypatch.setattr("app.sms_gateway.get_secret", lambda _ref: "gateway-test-secret")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return httpx.Response(
            200,
            json={"accepted": True},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.sms_gateway.httpx.post", fake_post)
    sms_gateway.send_verification_code(
        phone_e164="+8613812345678",
        code="123456",
        purpose="绑定手机号",
        challenge_id="phonechallenge_test",
    )
    assert captured["json"]["idempotency_key"] == "phonechallenge_test"
    assert captured["json"]["to"] == "+8613812345678"


@pytest.mark.parametrize("body", [{}, {"accepted": False}, {"status": "queued"}, "not-json-object"])
def test_sms_gateway_rejects_ambiguous_success_body(monkeypatch, tmp_path, body):
    config_path = tmp_path / "sms_gateway.json"
    monkeypatch.setenv("MENTORFLOW_SMS_CONFIG_PATH", str(config_path))
    sms_gateway.save_config(
        base_url="https://sms.example.test",
        send_path="/v1/messages",
        credential_ref="mfcred_test_sms",
    )
    monkeypatch.setattr("app.sms_gateway.get_secret", lambda _ref: "gateway-test-secret")
    monkeypatch.setattr(
        "app.sms_gateway.httpx.post",
        lambda url, **_kwargs: httpx.Response(
            200,
            json=body,
            request=httpx.Request("POST", url),
        ),
    )
    with pytest.raises(sms_gateway.SmsGatewayError) as caught:
        sms_gateway.send_verification_code(
            phone_e164="+8613812345678",
            code="123456",
            purpose="绑定手机号",
            challenge_id="phonechallenge_test",
        )
    assert caught.value.code == "SMS_PROVIDER_REJECTED"

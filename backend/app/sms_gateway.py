"""Explicit, provider-neutral SMS gateway contract for local recovery.

The desktop executable never contains an SMS provider secret.  A deployment
must point this module at a separately operated HTTPS gateway and reference a
Windows Credential Locker secret with ``MENTORFLOW_SMS_CREDENTIAL_REF``.
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from .credential_store import CredentialStoreError, get_secret


class SmsGatewayError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class SmsGatewayConfig:
    base_url: str
    credential_ref: str
    send_path: str = "/v1/messages"


def _config_path() -> Path:
    configured = str(os.getenv("MENTORFLOW_SMS_CONFIG_PATH", "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return (local_app_data / "MentorFlow" / "sms_gateway.json").resolve()


def _file_config() -> dict[str, str]:
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): str(item).strip() for key, item in value.items() if item is not None}


def _config() -> SmsGatewayConfig:
    saved = _file_config()
    base_url = str(os.getenv("MENTORFLOW_SMS_GATEWAY_URL", saved.get("base_url", ""))).strip().rstrip("/")
    credential_ref = str(os.getenv("MENTORFLOW_SMS_CREDENTIAL_REF", saved.get("credential_ref", ""))).strip()
    send_path = str(os.getenv("MENTORFLOW_SMS_SEND_PATH", saved.get("send_path", "/v1/messages"))).strip() or "/v1/messages"
    if not base_url or not credential_ref:
        raise SmsGatewayError(
            "SMS_PROVIDER_NOT_CONFIGURED",
            "短信服务尚未配置；请先配置独立 HTTPS 短信网关和 Credential Locker 引用。",
        )
    if not base_url.lower().startswith("https://"):
        raise SmsGatewayError("SMS_GATEWAY_INSECURE", "短信网关必须使用 HTTPS。")
    return SmsGatewayConfig(base_url=base_url, credential_ref=credential_ref, send_path=send_path)


def public_config() -> dict[str, str | bool]:
    """Return non-secret gateway configuration for the local settings page."""

    saved = _file_config()
    base_url = str(os.getenv("MENTORFLOW_SMS_GATEWAY_URL", saved.get("base_url", ""))).strip().rstrip("/")
    credential_ref = str(os.getenv("MENTORFLOW_SMS_CREDENTIAL_REF", saved.get("credential_ref", ""))).strip()
    send_path = str(os.getenv("MENTORFLOW_SMS_SEND_PATH", saved.get("send_path", "/v1/messages"))).strip() or "/v1/messages"
    return {
        "configured": bool(base_url and credential_ref),
        "base_url": base_url,
        "send_path": send_path,
        "credential_ref": credential_ref,
    }


def save_config(*, base_url: str, send_path: str, credential_ref: str) -> None:
    """Persist only gateway metadata; the secret remains in Credential Locker."""

    normalized_url = str(base_url or "").strip().rstrip("/")
    normalized_path = str(send_path or "/v1/messages").strip() or "/v1/messages"
    normalized_ref = str(credential_ref or "").strip()
    if not normalized_url.lower().startswith("https://"):
        raise SmsGatewayError("SMS_GATEWAY_INSECURE", "短信网关必须使用 HTTPS。")
    if not normalized_ref.startswith("mfcred_") or len(normalized_ref) > 160:
        raise SmsGatewayError("SMS_CREDENTIAL_REFERENCE_INVALID", "短信网关凭据引用无效。")
    destination = _config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {"base_url": normalized_url, "send_path": normalized_path, "credential_ref": normalized_ref},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(destination)


def send_verification_code(*, phone_e164: str, code: str, purpose: str, challenge_id: str) -> None:
    if os.getenv("MENTORFLOW_SMS_PROVIDER", "").strip().lower() == "tencent":
        from .tencent_sms import TencentSmsError, send_verification_code as send_tencent

        try:
            send_tencent(
                phone_e164=phone_e164,
                code=code,
                purpose=purpose,
                challenge_id=challenge_id,
            )
        except TencentSmsError as exc:
            raise SmsGatewayError(exc.code, exc.message) from exc
        return
    config = _config()
    try:
        secret = get_secret(config.credential_ref)
    except CredentialStoreError as exc:
        raise SmsGatewayError(exc.code, exc.message) from exc
    payload = {
        "to": phone_e164,
        "message": f"MentorFlow 验证码：{code}。10分钟内有效，仅用于{purpose}。",
        "purpose": purpose,
        "idempotency_key": challenge_id,
    }
    try:
        response = httpx.post(
            config.base_url + (config.send_path if config.send_path.startswith("/") else "/" + config.send_path),
            headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SmsGatewayError("SMS_PROVIDER_REJECTED", "短信服务拒绝了发送请求。") from exc
    except httpx.HTTPError as exc:
        raise SmsGatewayError("SMS_PROVIDER_UNAVAILABLE", "短信服务当前不可用，请稍后重试。") from exc
    try:
        result = response.json()
    except ValueError as exc:
        raise SmsGatewayError("SMS_PROVIDER_REJECTED", "短信网关返回了无法识别的结果。") from exc
    if not isinstance(result, dict) or result.get("accepted") is not True:
        raise SmsGatewayError("SMS_PROVIDER_REJECTED", "短信网关未确认验证码已接受发送。")

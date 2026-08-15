"""Tencent Cloud SMS adapter using the official Tencent SDK.

The adapter is server-side only. SecretId and SecretKey are referenced by
Credential Store IDs (or deployment-managed environment secrets); they never
cross the desktop API boundary.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .credential_store import CredentialStoreError, get_secret


class TencentSmsError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise TencentSmsError("SMS_PROVIDER_NOT_CONFIGURED", f"缺少腾讯云短信配置：{name}")
    return value


def _secret(reference_name: str) -> str:
    reference = _required(reference_name)
    try:
        return get_secret(reference)
    except CredentialStoreError as exc:
        raise TencentSmsError(exc.code, exc.message) from exc


def send_verification_code(*, phone_e164: str, code: str, purpose: str, challenge_id: str) -> None:
    """Send one approved Tencent SMS template message.

    The approved template must accept two variables: the six-digit code and
    the validity period in minutes. ``purpose`` is retained for audit context
    but is not inserted into the template automatically.
    """

    try:
        from tencentcloud.common import credential
        from tencentcloud.sms.v20210111 import models, sms_client
    except ImportError as exc:
        raise TencentSmsError(
            "SMS_PROVIDER_SDK_MISSING",
            "云端部署缺少 tencentcloud-sdk-python，请先安装云端依赖。",
        ) from exc

    region = os.getenv("MENTORFLOW_TENCENT_SMS_REGION", "ap-guangzhou").strip()
    sdk_app_id = _required("MENTORFLOW_TENCENT_SMS_SDK_APP_ID")
    sign_name = _required("MENTORFLOW_TENCENT_SMS_SIGN_NAME")
    template_id = _required("MENTORFLOW_TENCENT_SMS_TEMPLATE_ID")
    secret_id = _secret("MENTORFLOW_TENCENT_SECRET_ID_REF")
    secret_key = _secret("MENTORFLOW_TENCENT_SECRET_KEY_REF")
    try:
        cred = credential.Credential(secret_id, secret_key)
        client = sms_client.SmsClient(cred, region)
        request = models.SendSmsRequest()
        request.from_json_string(
            json.dumps(
                {
                    "SmsSdkAppId": sdk_app_id,
                    "SignName": sign_name,
                    "TemplateId": template_id,
                    "TemplateParamSet": [str(code), "10"],
                    "PhoneNumberSet": [phone_e164],
                    "SessionContext": challenge_id,
                },
                ensure_ascii=False,
            )
        )
        response = client.SendSms(request)
    except TencentSmsError:
        raise
    except Exception as exc:
        raise TencentSmsError("SMS_PROVIDER_UNAVAILABLE", "腾讯云短信请求失败，请稍后重试。") from exc

    statuses: list[Any] = list(getattr(response, "SendStatusSet", None) or [])
    if not statuses or any(str(getattr(item, "Code", "")) != "Ok" for item in statuses):
        raise TencentSmsError("SMS_PROVIDER_REJECTED", "腾讯云短信未接受发送请求。")


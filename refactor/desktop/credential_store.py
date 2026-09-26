from __future__ import annotations

import secrets
import sys
import hashlib
from typing import Protocol


SERVICE_NAME = "MentorFlow.Provider.v1"
CREDENTIAL_PREFIX = "mfcred_"
ACCOUNT_CREDENTIAL_PREFIX = "mfcred_account_"




class CredentialStoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


def _get_keyring() -> KeyringBackend:
    if sys.platform != 'win32':
        raise CredentialStoreError('CREDENTIAL_STORE_UNAVAILABLE', '需要 Windows 凭据管理器。')
    try:
        from keyring.backends.Windows import WinVaultKeyring
    except ImportError as exc:
        raise CredentialStoreError(
            "CREDENTIAL_STORE_UNAVAILABLE",
            "Windows Credential Locker 组件尚未安装",
        ) from exc

    return WinVaultKeyring()


def _validate_reference(credential_ref: str) -> str:
    value = str(credential_ref or "").strip()
    if not value.startswith(CREDENTIAL_PREFIX) or len(value) > 160:
        raise CredentialStoreError("CREDENTIAL_REFERENCE_INVALID", "凭据引用无效")
    return value






def store_secret(secret: str, credential_ref: str | None = None) -> str:
    value = str(secret or "")
    if not value:
        raise CredentialStoreError("CREDENTIAL_REQUIRED", "账号凭据不能为空")
    reference = (
        _validate_reference(credential_ref)
        if credential_ref
        else CREDENTIAL_PREFIX + secrets.token_urlsafe(24)
    )
    try:
        _get_keyring().set_password(SERVICE_NAME, reference, value)
    except CredentialStoreError:
        raise
    except Exception as exc:
        raise CredentialStoreError(
            "CREDENTIAL_STORE_WRITE_FAILED",
            "无法写入 Windows Credential Locker",
        ) from exc
    return reference


def get_secret(credential_ref: str) -> str:
    reference = _validate_reference(credential_ref)
    try:
        value = _get_keyring().get_password(SERVICE_NAME, reference)
    except CredentialStoreError:
        raise
    except Exception as exc:
        raise CredentialStoreError(
            "CREDENTIAL_STORE_READ_FAILED",
            "无法读取 Windows Credential Locker",
        ) from exc
    if not value:
        raise CredentialStoreError(
            "CREDENTIAL_NOT_FOUND",
            "Windows Credential Locker 中不存在该凭据，请重新登录",
        )
    return value


def delete_secret(credential_ref: str) -> bool:
    reference = _validate_reference(credential_ref)
    backend = _get_keyring()
    if not backend.get_password(SERVICE_NAME, reference):
        return False
    try:
        backend.delete_password(SERVICE_NAME, reference)
    except Exception as exc:
        raise CredentialStoreError(
            "CREDENTIAL_STORE_DELETE_FAILED",
            "无法从 Windows Credential Locker 删除凭据",
        ) from exc
    return True


def _validate_account_hash(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise CredentialStoreError(
            "CREDENTIAL_NAMESPACE_INVALID",
            f"{field_name} 无效",
        )
    return normalized


def account_credential_reference(
    issuer_hash: str,
    subject_hash: str,
    purpose: str,
) -> str:
    """Return an opaque account-and-purpose scoped Locker reference."""

    issuer = _validate_account_hash(issuer_hash, "issuer_hash")
    subject = _validate_account_hash(subject_hash, "subject_hash")
    normalized_purpose = str(purpose or "").strip().casefold()
    if (
        not normalized_purpose
        or len(normalized_purpose) > 64
        or any(
            not (character.isalnum() or character in {"-", "_", "."})
            for character in normalized_purpose
        )
    ):
        raise CredentialStoreError(
            "CREDENTIAL_NAMESPACE_INVALID",
            "credential purpose 无效",
        )
    namespace = hashlib.sha256(
        f"{issuer}:{subject}:{normalized_purpose}".encode("utf-8")
    ).hexdigest()
    return _validate_reference(f"{ACCOUNT_CREDENTIAL_PREFIX}{namespace}")


def store_account_secret(
    secret: str,
    *,
    issuer_hash: str,
    subject_hash: str,
    purpose: str,
) -> str:
    reference = account_credential_reference(issuer_hash, subject_hash, purpose)
    return store_secret(secret, reference)


def get_account_secret(
    *,
    issuer_hash: str,
    subject_hash: str,
    purpose: str,
) -> str:
    return get_secret(
        account_credential_reference(issuer_hash, subject_hash, purpose)
    )


def delete_account_secret(
    *,
    issuer_hash: str,
    subject_hash: str,
    purpose: str,
) -> bool:
    return delete_secret(
        account_credential_reference(issuer_hash, subject_hash, purpose)
    )

"""Windows Locker operations extracted from backend/app/credential_store.py.

No environment fallback or old provider sessions. A small Locker index keeps
cleanup independent of the ordinary preset file. References bind account and
preset; caller-supplied references are never accepted.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys

from .errors import ModelError

SERVICE_NAME = "MentorFlow.Learning.Models.v1"


def _get_keyring():
    try:
        if sys.platform != "win32":
            raise RuntimeError()
        # Select the mature Windows backend explicitly: no plaintext plugins.
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()
    except Exception:
        raise ModelError("CREDENTIAL_STORE_UNAVAILABLE", "Windows 凭据库不可用，无法保存或使用 API Key。") from None


class AccountCredentials:
    def __init__(self, account_id):
        self._namespace = hashlib.sha256(("learning-models:v1:" + account_id).encode()).hexdigest()

    def _reference(self, purpose):
        return "mfcred_account_" + hashlib.sha256((self._namespace + ":" + purpose).encode()).hexdigest()

    def _read(self, purpose):
        try:
            return _get_keyring().get_password(SERVICE_NAME, self._reference(purpose))
        except ModelError:
            raise
        except Exception:
            raise ModelError("CREDENTIAL_READ_FAILED", "无法读取当前账号的 Windows 凭据。") from None

    def _write(self, purpose, value):
        try:
            _get_keyring().set_password(SERVICE_NAME, self._reference(purpose), value)
        except ModelError:
            raise
        except Exception:
            raise ModelError("CREDENTIAL_WRITE_FAILED", "无法保存当前账号的 Windows 凭据。") from None

    def _delete(self, purpose):
        try:
            backend = _get_keyring()
            reference = self._reference(purpose)
            if backend.get_password(SERVICE_NAME, reference) is not None:
                backend.delete_password(SERVICE_NAME, reference)
        except ModelError:
            raise
        except Exception:
            raise ModelError("CREDENTIAL_DELETE_FAILED", "当前账号凭据尚未全部清除，请重试。") from None

    @staticmethod
    def _id(preset_id):
        if not isinstance(preset_id, str) or not re.fullmatch(r"[0-9a-f]{32}", preset_id):
            raise ModelError("PRESET_INVALID", "模型预设无效。")
        return preset_id

    def _index(self):
        raw = self._read("index")
        try:
            result = json.loads(raw) if raw is not None else []
            if not isinstance(result, list) or len(result) > 32:
                raise ValueError()
            return {self._id(value) for value in result}
        except (ValueError, TypeError):
            raise ModelError("CREDENTIAL_INDEX_INVALID", "当前账号的凭据索引无效，不能跳过清理。") from None

    def get(self, preset_id):
        return self._read(self._id(preset_id))

    def put(self, preset_id, secret):
        preset_id = self._id(preset_id)
        if not isinstance(secret, str) or not secret or len(secret) > 1024 or any(not 33 <= ord(c) <= 126 for c in secret):
            raise ModelError("API_KEY_INVALID", "API Key 应为有效的英文字符，不能包含空白或控制字符。")
        index = self._index() | {preset_id}
        if len(index) > 32:
            raise ModelError("PRESET_LIMIT", "最多保存 32 个模型预设。")
        # Index first: even a later disk/write failure leaves the key discoverable.
        self._write("index", json.dumps(sorted(index), separators=(",", ":")))
        self._write(preset_id, secret)

    def delete(self, preset_id):
        preset_id = self._id(preset_id)
        index = self._index()
        self._delete(preset_id)
        index.discard(preset_id)
        if index:
            self._write("index", json.dumps(sorted(index), separators=(",", ":")))
        else:
            self._delete("index")

    def clear(self):
        for preset_id in self._index():
            self._delete(preset_id)
        self._delete("index")

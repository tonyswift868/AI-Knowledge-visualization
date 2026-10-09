"""Shared synchronous model client for plans, classroom and tools.

Call complete() outside the GUI thread. Construction and configuration APIs
never make model requests. A blank API key preserves the selected preset's
credential, provided its endpoint has not changed.
"""
from __future__ import annotations

import asyncio
import copy
import math
import threading
import uuid
import time

from ..observability import TelemetryBuffer, begin_model_call, finish_model_call
from ..preferences.storage import AccountJSON, StorageError
from .credentials import AccountCredentials
from .errors import ModelError, ModelCancelled
from . import transport


class ModelClient:
    supports_metadata = True
    # Classroom may reuse this provider for a second, role-specific Evaluator
    # call.  The prompt and parser remain separate; this flag only advertises
    # that the shared client can serve that default role when no dedicated
    # evaluator client is injected by the host.
    supports_evaluator = True

    def __init__(self, account_id, data_root, *, observability=None):
        self.account_id = account_id
        self._storage = AccountJSON(account_id, data_root, "connections.json")
        self.data_root = self._storage.data_root
        self._credentials = AccountCredentials(account_id)
        self._lock = self._storage.lock
        self._closed = False
        self._requests: set[threading.Event] = set()
        # Keep a bounded, privacy-safe sink in the default desktop path.  A
        # host may still inject its own callback/buffer, but ordinary product
        # construction must not silently disable model-call tracing.
        self._observability = observability if observability is not None else TelemetryBuffer()

    @property
    def observability(self):
        """Return the bounded call sink for diagnostics and the host UI."""
        return self._observability

    def _record_event(self, event):
        sink = self._observability
        if callable(sink):
            sink(event)
        else:
            record = getattr(sink, "record", None)
            if callable(record):
                record(event)

    def _ensure_open(self):
        if self._closed:
            raise ModelError("MODEL_CLIENT_CLOSED", "模型连接已经关闭。")

    @staticmethod
    def _fields(name, base_url, model, temperature, timeout):
        if any(not isinstance(v, str) or not v.strip() or len(v) > limit or any(ord(c) < 32 for c in v)
               for v, limit in [(name, 80), (model, 200)]):
            raise ModelError("MODEL_CONFIG_INVALID", "请填写有效的预设名称和模型名称。")
        if (type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 300):
            raise ModelError("MODEL_CONFIG_INVALID", "生成温度需为 0–2，请求时限需为 1–300 秒。")
        return dict(name=name.strip(), base_url=transport.normalize_url(base_url), model=model.strip(),
                    temperature=float(temperature), timeout=float(timeout))

    def _load(self):
        data = self._storage.read({"version": 1, "active_id": None, "presets": []})
        try:
            if set(data) != {"version", "active_id", "presets"} or data["version"] != 1 or not isinstance(data["presets"], list) or len(data["presets"]) > 32:
                raise ValueError()
            ids = set()
            for preset in data["presets"]:
                if not isinstance(preset, dict) or set(preset) != {"id", "name", "base_url", "model", "temperature", "timeout", "has_api_key"}:
                    raise ValueError()
                AccountCredentials._id(preset["id"])
                if preset["id"] in ids or type(preset["has_api_key"]) is not bool:
                    raise ValueError()
                ids.add(preset["id"])
                self._fields(*(preset[k] for k in ("name", "base_url", "model", "temperature", "timeout")))
            if data["active_id"] is not None and data["active_id"] not in ids:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ModelError("MODEL_CONFIG_INVALID", "模型配置文件无效，未使用其中的地址或凭据。") from None
        return data

    def list_presets(self):
        with self._lock:
            self._ensure_open()
            return copy.deepcopy(self._load()["presets"])

    def get_configuration(self):
        with self._lock:
            self._ensure_open()
            data = self._load()
            return copy.deepcopy(next((p for p in data["presets"] if p["id"] == data["active_id"]), None))

    def save_preset(self, name, base_url, model, *, api_key=None, preset_id=None, temperature=0.2, timeout=60):
        fields = self._fields(name, base_url, model, temperature, timeout)
        with self._lock:
            self._ensure_open()
            data = self._load()
            old = next((p for p in data["presets"] if p["id"] == preset_id), None)
            if preset_id is not None and old is None:
                raise ModelError("PRESET_NOT_FOUND", "模型预设已不存在，请重新选择。")
            if old is None and len(data["presets"]) >= 32:
                raise ModelError("PRESET_LIMIT", "最多保存 32 个模型预设。")
            secret_changed = api_key is not None and api_key != ""
            if old and old["has_api_key"] and not secret_changed and transport.completions_url(old["base_url"]) != transport.completions_url(fields["base_url"]):
                raise ModelError("API_KEY_CONFIRM_REQUIRED", "模型地址已改变，请到 设置 → 模型连接 重新输入该地址的 API Key 并保存。")
            identifier = old["id"] if old else uuid.uuid4().hex
            saved = dict(fields, id=identifier, has_api_key=secret_changed or bool(old and old["has_api_key"]))
            previous_secret = self._credentials.get(identifier) if secret_changed else None
            if secret_changed:
                self._credentials.put(identifier, api_key)
            data["presets"] = [saved if p["id"] == identifier else p for p in data["presets"]]
            if old is None:
                data["presets"].append(saved)
            data["active_id"] = identifier
            try:
                self._storage.write(data)
            except StorageError:
                if secret_changed:
                    if previous_secret is None:
                        self._credentials.delete(identifier)
                    else:
                        self._credentials.put(identifier, previous_secret)
                raise
            finally:
                previous_secret = api_key = None
            return copy.deepcopy(saved)

    def select_preset(self, preset_id):
        with self._lock:
            self._ensure_open()
            data = self._load()
            if not any(p["id"] == preset_id for p in data["presets"]):
                raise ModelError("PRESET_NOT_FOUND", "模型预设已不存在，请重新选择。")
            data["active_id"] = preset_id
            self._storage.write(data)

    def delete_preset(self, preset_id):
        with self._lock:
            self._ensure_open()
            data = self._load()
            if not any(p["id"] == preset_id for p in data["presets"]):
                return
            self._credentials.delete(preset_id)
            data["presets"] = [p for p in data["presets"] if p["id"] != preset_id]
            if data["active_id"] == preset_id:
                data["active_id"] = data["presets"][0]["id"] if data["presets"] else None
            self._storage.write(data)

    def complete(self, messages, *, cancel=None, metadata=None) -> str:
        if not isinstance(messages, list) or not 1 <= len(messages) <= 512:
            raise ModelError("MODEL_MESSAGES_INVALID", "模型消息格式无效。")
        copied = []
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {"role", "content"}
                    or message["role"] not in ("system", "developer", "user", "assistant")
                    or not isinstance(message["content"], str)):
                raise ModelError("MODEL_MESSAGES_INVALID", "模型消息应包含 role 和文字 content。")
            copied.append(dict(message))
        try:
            message_size = sum(len(m["content"].encode("utf-8")) for m in copied)
        except UnicodeError:
            raise ModelError("MODEL_MESSAGES_INVALID", "模型消息包含无效文字。") from None
        if message_size > 1024 * 1024:
            raise ModelError("MODEL_MESSAGES_TOO_LARGE", "消息过长，请缩小请求后重试。")
        if cancel is not None and not isinstance(cancel, threading.Event):
            raise ModelError("MODEL_CANCEL_INVALID", "取消标记应为 threading.Event。")
        if cancel is not None and cancel.is_set():
            raise ModelCancelled()
        stopped = threading.Event()
        with self._lock:
            self._ensure_open()
            config = self.get_configuration()
            if config is None:
                raise ModelError("MODEL_NOT_CONFIGURED", "还没有可用的模型：请到 设置 → 模型连接 选择厂商、填入 API Key 并保存。")
            secret = self._credentials.get(config["id"])
            if not secret:
                raise ModelError("MODEL_KEY_REQUIRED", "当前模型还没有 API Key：请到 设置 → 模型连接 填入 API Key 并保存。")
            self._requests.add(stopped)
        request_id, started_at, started_event = begin_model_call(
            account_id=self.account_id, metadata=metadata,
            provider=config["base_url"], model=config["model"],
        )
        self._record_event(started_event)
        try:
            # A stable request id is reused by transport retries.  It is safe
            # for providers that ignore the header and prevents duplicate
            # state writes for providers that honor it.
            result = asyncio.run(transport.request(config, secret, copied, cancel, stopped,
                                                   idempotency_key=request_id))
        except ModelCancelled:
            self._record_event(finish_model_call(started_event, elapsed=time.perf_counter() - started_at, state="cancelled", error_code="MODEL_CANCELLED"))
            raise
        except Exception as exc:
            self._record_event(finish_model_call(started_event, elapsed=time.perf_counter() - started_at, state="failed", error_code=getattr(exc, "code", None) or type(exc).__name__))
            raise
        else:
            self._record_event(finish_model_call(started_event, elapsed=time.perf_counter() - started_at, state="succeeded"))
            return result
        finally:
            secret = None
            copied.clear()
            with self._lock:
                self._requests.discard(stopped)

    def clear_account_credentials(self):
        """Idempotent account-only cleanup; also callable after close()."""
        with self._lock:
            for request in self._requests:
                request.set()
            self._credentials.clear()
            try:
                data = self._load()
            except (StorageError, ModelError):
                # The Locker index has already removed this account's keys,
                # even when its ordinary config is missing/corrupted.
                return
            for preset in data["presets"]:
                preset["has_api_key"] = False
            if data["presets"]:
                self._storage.write(data)

    def close(self):
        with self._lock:
            self._closed = True
            for request in self._requests:
                request.set()

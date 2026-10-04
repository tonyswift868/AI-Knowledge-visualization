"""Account-scoped entry point for the new UI; synchronous calls belong on workers.

save_preset(provider_id, ...) supplies registry defaults. list_presets() and
get_configuration() return metadata only; select_preset(id) selects explicitly.
Construction/configuration never contact a model or read a saved API key.

The installed catalogue is data_root/learning/model-prices.json; a host may also
inject price_catalog=PriceCatalog(...). Missing prices leave amounts unknown.
Usage lives in learning/accounts/<account_id>/model-usage.sqlite3. No messages,
model output, search text, endpoint or credentials are persisted in that journal.

Legacy connections.json is read once, never overwritten. Migrated presets borrow
their old credential until a key is edited, then acquire an independent reference.
Deletion of a migrated preset must not revoke the old page's key.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import math
import json
import logging
from logging.handlers import RotatingFileHandler
import threading
import time
from urllib.parse import urlsplit
import uuid

from ..preferences.storage import AccountJSON, StorageError
from .credentials import AccountCredentials
from .errors import ModelCancelled, ModelError
from . import providers, transport
from .pricing import PriceCatalog, timestamp
from .usage import UsageLog
from .balance import BalanceClient, supported as balance_supported
from .providers._failure_evidence import FailureEvidence
from refactor.runtime_logging import trace_model_call

_UNSET = object()
_PUBLIC = ('id', 'provider_id', 'display_name', 'base_url', 'model',
           'temperature', 'timeout', 'has_api_key')

# Program-owned settings (SYS-07, 2026-10-01): only the three metadata purposes
# disable thinking; all other purposes request high where the provider supports it.
# Profile replays contained an unsupported historical claim with thinking off;
# the task's quality fallback retains the provider default for that purpose.
PURPOSE_EFFORT = {
    'check_profile': 'high', 'point_materials': 'high', 'distractor_check': 'high',
    'source_identity': 'high', 'outline': 'high',
    'generate': 'high', 'plan_additions_generate': 'high',
    'source_scoring': 'high', 'research': 'high', 'teacher': 'high', 'coverage_check': 'high',
    'profile': None, 'sufficiency': 'none', 'plan_intent': 'none',
}

PURPOSE_MIN_OUTPUT = {
    'teacher': 8192, 'profile': 8192, 'check_profile': 8192, 'coverage_check': 8192,
    'point_materials': 24576, 'source_identity': 8192,
}
BACKGROUND_PURPOSES = frozenset({'point_materials', 'source_identity', 'profile', 'check_profile', 'coverage_check', 'outline', 'generate'})


def _storage_error():
    return ModelError('MODEL_CONFIG_INVALID', '无法读取或保存当前账号的模型设置，原配置未被替换。')


class _Cancellation(threading.Event):
    """One request's event, also observes its caller without mutating it."""
    def __init__(self, external):
        super().__init__()
        self.external = external

    def is_set(self):
        return super().is_set() or bool(self.external and self.external.is_set())

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                break
            super().wait(0.02 if remaining is None else min(0.02, remaining))
        return self.is_set()


class ModelGateway:
    def __init__(self, account_id, data_root, *, price_catalog=None):
        try:
            self._storage = AccountJSON(account_id, data_root, 'model-gateway.json')
            self._legacy = AccountJSON(account_id, data_root, 'connections.json')
            self.data_root = self._storage.data_root
            self.account_id = account_id
            self._credentials = AccountCredentials(account_id)
            self._lock = self._storage.lock
            self._closed = False
            self._requests = set()
            self._balance_client = BalanceClient()
            self._balance_revision = 0
            # Lock ordering is always gateway then legacy.
            with self._lock, self._legacy.lock:
                if not self._storage.path.exists():
                    self._storage.write(self._migrate())
                self._load()
        except StorageError:
            raise _storage_error() from None
        catalog_path = self.data_root / 'learning' / 'model-prices.json'
        self.price_catalog = (price_catalog if price_catalog is not None else
                              PriceCatalog(catalog_path) if catalog_path.exists() else PriceCatalog.empty())
        self.usage_log = UsageLog(self._storage.path.with_name('model-usage.sqlite3'), catalog=self.price_catalog)

    def _ensure_open(self):
        if self._closed:
            raise ModelError('MODEL_CLIENT_CLOSED', '模型连接已经关闭。')

    @staticmethod
    def _fields(provider_id, display_name, base_url, model, temperature, timeout):
        if provider_id is not None and (not isinstance(provider_id, str) or provider_id not in providers.REGISTRY):
            raise ModelError('MODEL_CONFIG_INVALID', '请选择支持的模型厂商。')
        if any(not isinstance(v, str) or not v.strip() or len(v) > limit or any(ord(c) < 32 for c in v)
               for v, limit in ((display_name, 80), (model, 256))):
            raise ModelError('MODEL_CONFIG_INVALID', '请填写有效的预设名称和模型名称。')
        if (temperature is not None and (type(temperature) not in (int, float)
                or not math.isfinite(temperature) or not 0 <= temperature <= 2)) or (
                type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 300):
            raise ModelError('MODEL_CONFIG_INVALID', '温度须为 0–2 或厂商默认，请求时限须为 1–300 秒。')
        return dict(provider_id=provider_id, display_name=display_name.strip(),
                    base_url=transport.normalize_url(base_url), model=model.strip(),
                    temperature=float(temperature) if temperature is not None else None, timeout=float(timeout))

    @staticmethod
    def _public(preset):
        return {k: copy.deepcopy(preset[k]) for k in _PUBLIC} if preset is not None else None

    def _read_legacy(self):
        try:
            data = self._legacy.read({'version': 1, 'active_id': None, 'presets': []})
            if (set(data) != {'version', 'active_id', 'presets'} or type(data['version']) is not int
                    or data['version'] != 1 or not isinstance(data['presets'], list) or len(data['presets']) > 32):
                raise ValueError()
            ids = set()
            for p in data['presets']:
                if not isinstance(p, dict) or set(p) != {'id', 'name', 'base_url', 'model', 'temperature', 'timeout', 'has_api_key'}:
                    raise ValueError()
                AccountCredentials._id(p['id'])
                if p['id'] in ids or type(p['has_api_key']) is not bool:
                    raise ValueError()
                ids.add(p['id'])
                self._fields(None, *(p[k] for k in ('name', 'base_url', 'model', 'temperature', 'timeout')))
            if data['active_id'] is not None and data['active_id'] not in ids:
                raise ValueError()
            return data
        except (StorageError, ValueError, KeyError, TypeError):
            raise ModelError('MODEL_CONFIG_INVALID', '旧模型配置无法读取，未跳过或覆盖旧预设。') from None

    def _migrate(self):
        data = self._read_legacy()
        migrated = []
        for old in data['presets']:
            fields = self._fields(providers.detect_provider(old['base_url']), old['name'], old['base_url'],
                                  old['model'], old['temperature'], old['timeout'])
            migrated.append(dict(fields, id=old['id'], has_api_key=old['has_api_key'],
                                 credential_id=old['id'], credential_owner='legacy',
                                 legacy_endpoint=transport.completions_url(old['base_url'])))
        return {'version': 1, 'active_id': data['active_id'], 'presets': migrated, 'retired_credentials': []}

    def _load(self):
        try:
            data = self._storage.read({})
            if (set(data) != {'version', 'active_id', 'presets', 'retired_credentials'} or type(data['version']) is not int
                    or data['version'] != 1 or not isinstance(data['presets'], list) or len(data['presets']) > 32):
                raise ValueError()
            ids, refs = set(), set()
            for p in data['presets']:
                if not isinstance(p, dict) or set(p) != {*_PUBLIC, 'credential_id', 'credential_owner', 'legacy_endpoint'}:
                    raise ValueError()
                AccountCredentials._id(p['id'])
                AccountCredentials._id(p['credential_id'])
                if (p['id'] in ids or p['credential_id'] in refs or type(p['has_api_key']) is not bool
                        or p['credential_owner'] not in {'legacy', 'owned'}):
                    raise ValueError()
                if p['credential_owner'] == 'legacy':
                    if p['credential_id'] != p['id'] or transport.completions_url(p['base_url']) != p['legacy_endpoint']:
                        raise ValueError()
                elif p['legacy_endpoint'] is not None:
                    raise ValueError()
                self._fields(*(p[k] for k in ('provider_id', 'display_name', 'base_url', 'model', 'temperature', 'timeout')))
                ids.add(p['id'])
                refs.add(p['credential_id'])
            if not isinstance(data['retired_credentials'], list) or len(data['retired_credentials']) > 32:
                raise ValueError()
            retired = {AccountCredentials._id(ref) for ref in data['retired_credentials']}
            if retired & refs or len(retired) != len(data['retired_credentials']):
                raise ValueError()
            if data['active_id'] is not None and data['active_id'] not in ids:
                raise ValueError()
            return data
        except (StorageError, ValueError, KeyError, TypeError):
            raise _storage_error() from None

    def _write(self, data):
        try:
            self._storage.write(data)
            self._balance_revision += 1
        except StorageError:
            raise _storage_error() from None

    def _retire(self, data):
        # Cross-endpoint changes publish the new reference before deleting the
        # old owned key. A crash/failure is retried on the next explicit edit;
        # construction and metadata reads never open Windows credentials.
        if data['retired_credentials']:
            for ref in data['retired_credentials']:
                self._credentials.delete(ref)
            data['retired_credentials'] = []
            self._write(data)

    def list_presets(self):
        with self._lock:
            self._ensure_open()
            return tuple(self._public(p) for p in self._load()['presets'])

    def get_configuration(self):
        with self._lock:
            self._ensure_open()
            data = self._load()
            return self._public(next((p for p in data['presets'] if p['id'] == data['active_id']), None))

    def save_preset(self, provider_id, *, display_name=None, base_url=None, model=None,
                    temperature=_UNSET, timeout=_UNSET, api_key=None, preset_id=None):
        """Create/update and select. None fields preserve existing same-provider values.

        None temperature means use provider default; omitted temperature preserves
        an existing value. Blank API key preserves the key only at the same address.
        """
        if not isinstance(provider_id, str) or provider_id not in providers.REGISTRY:
            raise ModelError('MODEL_CONFIG_INVALID', '请选择支持的模型厂商。')
        with self._lock:
            self._ensure_open()
            data = self._load()
            self._retire(data)
            old = next((p for p in data['presets'] if p['id'] == preset_id), None)
            if preset_id is not None and old is None:
                raise ModelError('PRESET_NOT_FOUND', '模型预设已不存在，请重新选择。')
            if old is None and len(data['presets']) >= 32:
                raise ModelError('PRESET_LIMIT', '最多保存 32 个模型预设。')
            spec = providers.REGISTRY[provider_id]
            prior = old if old and old['provider_id'] == provider_id else {}
            fields = self._fields(provider_id, display_name if display_name is not None else prior.get('display_name', spec.display_name),
                                  base_url if base_url is not None else prior.get('base_url', spec.default_base_url),
                                  model if model is not None else prior.get('model', spec.default_model),
                                  prior.get('temperature') if temperature is _UNSET else temperature,
                                  prior.get('timeout', 120.0) if timeout is _UNSET else timeout)
            changed = api_key is not None and api_key != ''
            if old and old['has_api_key'] and not changed and (
                    old['provider_id'] != provider_id or old['base_url'] != fields['base_url']):
                raise ModelError('API_KEY_CONFIRM_REQUIRED', '模型厂商或地址已改变，请重新输入该地址的 API Key。')
            identifier = old['id'] if old else uuid.uuid4().hex
            ref = old['credential_id'] if old else identifier
            owner = old['credential_owner'] if old else 'owned'
            rebind = old and (fields['base_url'] != old['base_url'] or provider_id != old['provider_id'])
            if (owner == 'legacy' and changed) or rebind:
                ref, owner = uuid.uuid4().hex, 'owned'
            saved = dict(fields, id=identifier, has_api_key=changed or bool(old and old['has_api_key']),
                         credential_id=ref, credential_owner=owner,
                         legacy_endpoint=old['legacy_endpoint'] if owner == 'legacy' else None)
            previous = self._credentials.get(ref) if changed else None
            try:
                if changed:
                    self._credentials.put(ref, api_key)
                data['presets'] = [saved if p['id'] == identifier else p for p in data['presets']]
                if old is None:
                    data['presets'].append(saved)
                elif old['credential_owner'] == 'owned' and old['credential_id'] != ref:
                    data['retired_credentials'].append(old['credential_id'])
                data['active_id'] = identifier
                self._write(data)
            except ModelError:
                if changed:
                    if previous is None:
                        self._credentials.delete(ref)
                    else:
                        self._credentials.put(ref, previous)
                raise
            finally:
                previous = api_key = None
            try:
                self._retire(data)
            except ModelError:
                raise ModelError('CREDENTIAL_DELETE_FAILED', '新预设已保存，旧凭据清理未完成；下次保存或删除时会重试清理。') from None
            return self._public(saved)

    def select_preset(self, preset_id):
        with self._lock:
            self._ensure_open()
            data = self._load()
            if not any(p['id'] == preset_id for p in data['presets']):
                raise ModelError('PRESET_NOT_FOUND', '模型预设已不存在，请重新选择。')
            data['active_id'] = preset_id
            self._write(data)

    def delete_preset(self, preset_id):
        with self._lock:
            self._ensure_open()
            data = self._load()
            self._retire(data)
            old = next((p for p in data['presets'] if p['id'] == preset_id), None)
            if old is None:
                return
            # Removing an owned key first leaves retryable config if disk save fails.
            # A borrowed reference belongs to the still-operational legacy page.
            if old['credential_owner'] == 'owned':
                self._credentials.delete(old['credential_id'])
            data['presets'] = [p for p in data['presets'] if p['id'] != preset_id]
            if data['active_id'] == preset_id:
                data['active_id'] = data['presets'][0]['id'] if data['presets'] else None
            self._write(data)

    def _secret(self, config):
        if config['credential_owner'] == 'legacy':
            with self._legacy.lock:
                old = next((p for p in self._read_legacy()['presets'] if p['id'] == config['id']), None)
                if old is None or not old['has_api_key']:
                    raise ModelError('MODEL_KEY_REQUIRED', '原预设凭据已移除，请保存当前模型的 API Key。')
                if transport.completions_url(old['base_url']) != config['legacy_endpoint']:
                    raise ModelError('API_KEY_CONFIRM_REQUIRED', '原预设地址已改变，请为当前地址重新保存 API Key。')
                return self._credentials.get(config['credential_id'])
        return self._credentials.get(config['credential_id'])

    def search_supported(self):
        config = self.get_configuration()
        spec = providers.REGISTRY.get(config['provider_id']) if config else None
        return bool(spec and spec.search is not None)

    def balance(self, preset_id, *, refresh=False, cancel=None):
        """Optional read-only vendor lookup; never selects a preset or logs usage."""
        token = _Cancellation(cancel)
        secret = None
        try:
            with self._lock:
                self._ensure_open()
                config = next((p for p in self._load()['presets'] if p['id'] == preset_id), None)
                if not config or not config['has_api_key'] or not balance_supported(config['provider_id'], config['base_url']):
                    return None
                self._requests.add(token)
                revision = self._balance_revision
                secret = self._secret(config)
            result = self._balance_client.query(config['provider_id'], config['base_url'], secret,
                                                refresh=refresh, cancel=token)
            with self._lock:
                self._ensure_open()
                current = next((p for p in self._load()['presets'] if p['id'] == preset_id), None)
                return result if current == config and revision == self._balance_revision and not token.is_set() else None
        except Exception:
            return None
        finally:
            secret = None
            with self._lock:
                self._requests.discard(token)

    @staticmethod
    def _price_context(config, tier=None):
        """Region and service tier used to pick price rows (calls and estimates alike)."""
        # Only protocol-known facts. A custom endpoint cannot inherit official
        # regional prices just because the user selected that provider's parser.
        host = urlsplit(config['base_url']).hostname
        region = 'unknown'
        if providers.detect_provider(config['base_url']) == config['provider_id']:
            region = ('cn-beijing' if host == 'dashscope.aliyuncs.com' else
                      'unknown' if config['provider_id'] == 'qwen' else 'global')
        # Caller cannot set batch/flex/priority through this gateway. Preserve a
        # nonstandard response tier when adapters publish one in raw usage.
        service_tier = ('standard' if tier == 'default' else tier or 'unknown'
                        if config['provider_id'] == 'openai' else 'standard')
        return dict(region=region, service_tier=service_tier)

    def estimate_context(self):
        """The price context a cost estimate for the configured model should use."""
        config = self.get_configuration()
        return self._price_context(config, 'default') if config else {}

    @staticmethod
    def _billable_usage(value, config, effort=None):
        raw = copy.deepcopy(value.raw)
        meta = raw.setdefault('_mentorflow', {})
        context = dict(ModelGateway._price_context(config, meta.get('service_tier')),
                       cache_mode='implicit', finished_at=timestamp().isoformat())
        if effort is not None:
            context['requested_reasoning_effort'] = effort
        sent = meta.get('sent_reasoning_parameters', {})
        if config['provider_id'] == 'deepseek' and sent.get('thinking', {}).get('type') in {'enabled', 'disabled'}:
            context['thinking_mode'] = 'non_thinking' if sent['thinking']['type'] == 'disabled' else 'thinking'
        if config['provider_id'] == 'qwen':
            if value.reasoning_tokens > 0:
                context['thinking_mode'] = 'thinking'
            elif sent.get('enable_thinking') is False or sent.get('parameters', {}).get('enable_thinking') is False:
                context['thinking_mode'] = 'non_thinking'
        meta['billing_context'] = context
        return replace(value, raw=raw)

    def complete(self, purpose, messages, *, system=None, enable_search=False,
                 on_text=None, cancel=None, plan_id='', max_tokens=4096, reasoning_effort=None):
        if type(max_tokens) is not int or not 1 <= max_tokens <= 32768:
            raise ModelError('MODEL_CONFIG_INVALID', '模型调用参数无效。')
        max_tokens = max(max_tokens, PURPOSE_MIN_OUTPUT.get(purpose, 0))
        return self._complete(purpose, messages, system=system, enable_search=enable_search,
                              on_text=on_text, cancel=cancel, plan_id=plan_id, max_tokens=max_tokens,
                              reasoning_effort=reasoning_effort)

    def test_connection(self):
        """Explicit user action only. No probe during construction/save/select."""
        return self._complete('connection_test', [{'role': 'user', 'content': 'Reply OK.'}],
                              system=None, enable_search=False, on_text=None, cancel=None, plan_id='', max_tokens=32,
                              reasoning_effort=None)

    def _log_failure(self, error, config, secret, messages, system, identifier, purpose):
        """Local account log, bounded; logging failure never replaces model error."""
        safe = FailureEvidence(config['provider_id'] if config else '', secret or '')
        safe.request(dict(messages=messages, system=system))
        diagnostic = getattr(error, 'diagnostics', {})
        diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
        safe.status = diagnostic.get('http_status') if type(diagnostic.get('http_status')) is int else error.status
        safe.content_type = safe.clean(diagnostic.get('content_type'))
        safe.vendor_type = safe.clean(diagnostic.get('vendor_error_type'))
        safe.vendor_message = safe.clean(diagnostic.get('vendor_error_message'))
        safe.stage = safe.clean(diagnostic.get('stage')) or 'gateway_failure'
        safe.events.extend(safe.clean(v) for v in diagnostic.get('last_sse_events', [])[-8:] if isinstance(v,str))
        record = dict(safe.snapshot(), at=timestamp().isoformat(), request_id=identifier,
                      purpose=safe.clean(purpose), code=error.code,
                      exception_type=safe.clean(diagnostic.get('exception_type')))
        try:
            with self._lock:
                handler = RotatingFileHandler(self.usage_log.path.with_name('model-failures.log'),
                                              maxBytes=1024*1024, backupCount=2, encoding='utf-8')
                try:
                    handler.emit(logging.LogRecord(__name__, logging.WARNING, '', 0,
                                 json.dumps(record, ensure_ascii=False), (), None))
                finally:
                    handler.close()
            error.diagnostic_log_written = True
        except (OSError, ValueError, AttributeError):
            error.diagnostic_log_written = False

    @trace_model_call
    def _complete(self, purpose, messages, *, system, enable_search, on_text, cancel, plan_id, max_tokens,
                  reasoning_effort):
        effort = reasoning_effort if reasoning_effort is not None else PURPOSE_EFFORT.get(purpose, 'high')
        config, setup_error = None, None
        token = _Cancellation(cancel if isinstance(cancel, threading.Event) else None)
        with self._lock:
            try:
                data = self._load()
                config = next((p for p in data['presets'] if p['id'] == data['active_id']), None)
                self._ensure_open()
            except ModelError as exc:
                setup_error = exc
            self._requests.add(token)
        identifier = None
        observed, result, error, secret = None, None, None, None
        attempted = False
        try:
            # Persist BEFORE key access/network. Crash/forced exit leaves an
            # explicit unfinished call, never a falsely complete cost summary.
            identifier = self.usage_log.begin(config['provider_id'] or '' if config else '',
                                              config['model'] if config else '', purpose, plan_id=plan_id)
            try:
                if setup_error:
                    raise setup_error
                if cancel is not None and not isinstance(cancel, threading.Event):
                    raise ModelError('MODEL_CANCEL_INVALID', '取消标记须为 threading.Event。')
                if token.is_set():
                    raise ModelCancelled()
                if not config:
                    raise ModelError('MODEL_NOT_CONFIGURED', '请先保存并选择模型预设。')
                spec = providers.REGISTRY.get(config['provider_id'])
                if spec is None:
                    raise ModelError('MODEL_CONFIG_INVALID', '旧预设厂商未识别，请选择对应厂商后保存。')
                if enable_search and spec.search is None:
                    raise ModelError('MODEL_SEARCH_UNSUPPORTED', '资料搜索需要支持搜索的厂商。')
                with self._lock:
                    secret = self._secret(config)
                if not secret:
                    raise ModelError('MODEL_KEY_REQUIRED', '请为当前模型预设保存 API Key。')
                attempted = True
                timeout = (min(300., max(240., config['timeout'])) if purpose in BACKGROUND_PURPOSES else config['timeout'])
                result = providers.complete(config['provider_id'], base_url=config['base_url'], model=config['model'],
                                            api_key=secret, messages=messages, system=system, enable_search=enable_search,
                                            temperature=config['temperature'], timeout=timeout,
                                            cancel=token, on_text=on_text, max_tokens=max_tokens,
                                            reasoning_effort=effort)
                if not isinstance(result, providers.ModelResult) or result.provider_id != config['provider_id']:
                    raise ModelError('MODEL_RESPONSE_INVALID', '模型响应厂商与当前预设不一致。')
                observed = self._billable_usage(result.usage, config, effort)
                if token.is_set():
                    exc = ModelCancelled()
                    exc.usage, exc.response_received = result.usage, True
                    exc.generated_text_length, exc.model = len(result.text), result.model
                    raise exc
            except ModelError as exc:
                error = exc
                if observed is None and isinstance(getattr(exc, 'usage', None), providers.Usage):
                    # An invalid answer can still have final, billable meters.
                    # Only the adapter knows whether a stream's usage is partial.
                    observed = self._billable_usage(exc.usage, config, effort)
                if observed is None and exc.code in {'MODEL_TIMEOUT', 'MODEL_CONNECTION_FAILED', 'MODEL_CANCELLED'}:
                    # A missing receipt is unknown, not zero. Preserve whether
                    # the provider was attempted; a pre-send cancel cannot bill.
                    unknown = providers.Usage(0, 0, 0, 0, 0, 0, {'_mentorflow': {
                        'unreported_fields': ['input_tokens', 'cached_input_tokens', 'uncached_input_tokens',
                                              'output_tokens', 'reasoning_tokens', 'search_requests'],
                        'request_attempted': getattr(exc, 'request_attempted', attempted),
                        'sent_reasoning_parameters': getattr(exc, 'sent_reasoning_parameters', {}),
                    }})
                    observed = self._billable_usage(unknown, config, effort) if config else unknown
            except Exception:
                error = ModelError('MODEL_RESPONSE_INVALID', '模型调用未返回可核对的完整结果。')
            try:
                self.usage_log.finish(identifier, observed, error_code=error.code if error else '',
                                      model=result.model if isinstance(result, providers.ModelResult)
                                      else getattr(error, 'model', None))
            except ModelError as exc:
                # Caller can keep an already received answer without a second
                # paid request; the durable unfinished row remains visible.
                exc.request_id, exc.result = identifier, result
                raise
            if error:
                error.request_id = identifier
                self._log_failure(error, config, secret, messages, system, identifier, purpose)
                raise error
            return result
        finally:
            secret = None
            with self._lock:
                self._requests.discard(token)

    def close(self):
        with self._lock:
            self._closed = True
            for token in tuple(self._requests):
                token.set()
            self._balance_client.clear()

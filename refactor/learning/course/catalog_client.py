"""Account-scoped, authenticated catalogs with nonblocking refresh and fallback.

load_lists()/load_prices() return validated cache/builtin data immediately and
schedule a refresh. refresh_background() returns a Future (or None during the
success cooldown or jittered failure backoff); status(kind) exposes auth, stale
fallback and disk failures to the UI.
No catalog request refreshes a token, retries a paid call or follows redirects.
The account shell owns the live session_provider and must close() on logout.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import random
from threading import RLock
from threading import Timer
import time
from uuid import uuid4

import httpx

from .source_filter import SourceLists, parse_lists
from ..file_io import assert_no_reparse_ancestors, canonical_io_path, _native_path
from refactor.learning.sync_identity import PlanError
from refactor.learning.sync_source import AuthenticatedPlanSource


MAX_CATALOG_BYTES = 1024 * 1024
REFRESH_SECONDS = 6 * 60 * 60
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 30 * 60
_KINDS = ('source-whitelist', 'model-prices')
_DATA = Path(__file__).with_name('data')


def _schedule_timer(delay, callback):
    timer = Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


class CatalogFormatError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogStatus:
    origin: str             # builtin | cache | server
    code: str
    message: str
    checked_at: float | None = None


def _object(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise CatalogFormatError(f'重复 JSON 字段：{key}')
        output[key] = value
    return output


def _constant(value):
    raise CatalogFormatError('JSON 不允许 NaN 或 Infinity')


def _decode(data):
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogFormatError('目录文件超过 1 MiB 上限')
    return json.loads(data, object_pairs_hook=_object, parse_constant=_constant)


def validate_prices(data):
    """Validate a price catalog with the same rules the pricing engine uses.

    ``connections.pricing.PriceCatalog`` owns the price format (tiers, periods,
    search fees, cache writes). Keeping a second hand-written schema here made
    valid catalogs from the pricing owner fail, so this only adapts its error.
    Never invent prices or convert currencies; the price consumer owns that.
    """
    from ..connections.pricing import PriceCatalog

    if not isinstance(data, dict):
        raise CatalogFormatError('价格表格式无效：根字段')
    try:
        PriceCatalog.from_dict(data)
    except Exception as exc:
        raise CatalogFormatError('价格表格式无效：' + (str(exc) or type(exc).__name__)) from None
    return deepcopy(data)


def _validate(kind, payload):
    if kind == 'source-whitelist':
        return parse_lists(payload, source='服务器来源名单', allow_candidates=False)
    return validate_prices(payload)


def _etag(value):
    if value is None or value == '':
        return ''
    if not isinstance(value, str) or not re.fullmatch(r'(?:W/)?"[\x21\x23-\x7e]{1,200}"', value):
        raise CatalogFormatError('ETag 无效')
    return value


class CatalogClient(AuthenticatedPlanSource):
    def __init__(self, *, account_id, data_root, session_provider, api_base_url, ca_bundle,
                 http_transport=None, clock=time.time, jitter=random.uniform,
                 scheduler=_schedule_timer, auto_refresh=True):
        if not isinstance(account_id, str) or not re.fullmatch(r'mfa1_[0-9a-f]{64}', account_id):
            raise ValueError('目录缓存需要有效的稳定账号 ID')
        super().__init__(account_id=account_id, session_provider=session_provider,
                         api_base_url=api_base_url, ca_bundle=ca_bundle, http_transport=http_transport)
        self.cache_dir = canonical_io_path(data_root) / 'learning' / 'accounts' / account_id / 'course' / 'catalog'
        self._clock, self._jitter, self._scheduler, self._auto_refresh = clock, jitter, scheduler, auto_refresh
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='catalog')
        self._future = None
        self._retry_timer = None
        self._retry_marker = None
        self._last_attempt = None
        self._retry_at, self._failures = 0, 0
        self._payloads, self._etags, self._statuses = {}, {}, {}
        for kind in _KINDS:
            filename = 'source_lists.json' if kind == 'source-whitelist' else 'model-prices.json'
            payload = _decode((_DATA / filename).read_bytes())
            if kind == 'source-whitelist':
                payload.pop('candidates', None)
            _validate(kind, payload)
            self._payloads[kind], self._etags[kind] = payload, ''
            self._statuses[kind] = CatalogStatus('builtin', 'BUILTIN', '使用随软件提供的内置目录。')
            self._read_cache(kind)
        try:
            state = self._read('refresh-state.json')
            if self._owned(state):
                if state.get('schema') == 2:
                    stamp, retry_at, failures = state.get('last_success'), state.get('retry_at', 0), state.get('failures', 0)
                    if type(stamp) in (float, int) and math.isfinite(stamp) and 0 <= stamp <= self._clock():
                        self._last_attempt = stamp
                    if (type(retry_at) in (float, int) and math.isfinite(retry_at)
                            and 0 <= retry_at <= self._clock() + RETRY_MAX_SECONDS * 2
                            and type(failures) is int and 0 <= failures <= 100):
                        self._retry_at, self._failures = retry_at, failures
                # A schema-1 last_attempt does not record whether the request
                # succeeded. Treating it as success could strand an upgraded
                # offline client for six hours, so discard that ambiguous throttle.
        except (OSError, ValueError, TypeError, RuntimeError):
            pass
        if self._retry_at:
            self._schedule_retry(max(0, self._retry_at - self._clock()))

    def _owned(self, data):
        return (isinstance(data, dict) and data.get('schema') in (1, 2)
                and data.get('account_id') == self.account_id and data.get('base_url') == self._base_url)

    def _envelope(self, **fields):
        schema = fields.pop('schema', 1)
        return dict(schema=schema, account_id=self.account_id, base_url=self._base_url, **fields)

    def _path(self, name):
        path = self.cache_dir / name
        assert_no_reparse_ancestors(path, Path(path.anchor))
        path = _native_path(path)
        if path.exists() and path.stat().st_nlink != 1:
            raise OSError('目录缓存不是独立文件')
        return path

    def _read(self, name):
        with self._path(name).open('rb') as stream:
            return _decode(stream.read(MAX_CATALOG_BYTES + 1))

    def _write(self, name, payload):
        destination = self._path(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = self._path(name)
        temporary = self._path('.' + uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_cache(self, kind):
        try:
            cached = self._read(kind + '.json')
            if not self._owned(cached) or cached.get('kind') != kind:
                raise CatalogFormatError('缓存账号、服务器或目录类型不匹配')
            _validate(kind, cached['payload'])
            etag = _etag(cached['etag'])
            self._payloads[kind], self._etags[kind] = cached['payload'], etag
            self._statuses[kind] = CatalogStatus('cache', 'CACHED', '使用已校验的本机目录缓存。')
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            self._statuses[kind] = CatalogStatus('builtin', 'CACHE_INVALID', '本机缓存无法验证，使用内置目录。')

    def _load(self, kind):
        with self._lock:
            if self._closed.is_set():
                raise PlanError('CANCELLED', '目录客户端已关闭。')
            value = _validate(kind, self._payloads[kind])
        if self._auto_refresh:
            self.refresh_background()
        return value

    def load_lists(self) -> SourceLists:
        return self._load('source-whitelist')

    def material_guidance(self, query, *, stage='', subject=''):
        """Planning-chat fallback for matched textbooks requiring manual upload."""
        from .research import directory_upload_guidance
        return directory_upload_guidance(query,stage=stage,subject=subject,lists=self.load_lists())

    def load_prices(self) -> dict:
        return self._load('model-prices')

    def status(self, kind):
        with self._lock:
            return self._statuses[kind]

    def refresh_background(self):
        """Allow one concurrent refresh; successful pairs cool down for six hours.

        Failures use a persisted exponential delay with jitter. Returns the existing
        in-flight Future, or None while the applicable cooldown is active.
        """
        with self._lock:
            if self._closed.is_set():
                return None
            if self._future is not None and not self._future.done():
                return self._future
            now = self._clock()
            if now < self._retry_at:
                return None
            if self._last_attempt is not None and now - self._last_attempt < REFRESH_SECONDS:
                return None
            self._future = self._executor.submit(self._refresh, now)
            return self._future

    def _schedule_retry(self, delay):
        with self._lock:
            if self._closed.is_set():
                return
            if self._retry_timer is not None:
                cancel = getattr(self._retry_timer, 'cancel', None)
                if cancel:
                    cancel()
            marker = object()
            self._retry_marker = marker
            self._retry_timer = marker
            handle = self._scheduler(max(0, delay), lambda: self._retry_due(marker))
            if self._retry_marker is marker:
                self._retry_timer = handle
            else:
                # A synchronous test scheduler may have fired and installed a
                # successor before this call returned; discard the old handle.
                handle.cancel()

    def _retry_due(self, marker):
        with self._lock:
            if self._closed.is_set():
                if self._retry_marker is marker:
                    self._retry_timer = None
                    self._retry_marker = None
                return
            if self._retry_marker is not marker:
                return
            # A scheduler may fire at the same instant the worker Future is
            # publishing its result. Keep the retry alive instead of losing it.
            if self._future is not None and not self._future.done():
                self._schedule_retry(0.1)
                return
            remaining = self._retry_at - self._clock()
            if remaining > 0:
                self._schedule_retry(remaining)
                return
            self._retry_timer = None
            self._retry_marker = None
            self.refresh_background()

    def _refresh(self, now):
        with self._lock:
            if self._closed.is_set():
                return {}
        for kind in _KINDS:
            if self._closed.is_set():
                break
            self._refresh_one(kind, now)
        with self._lock:
            statuses = dict(self._statuses)
            if all(statuses[kind].code in {'UPDATED', 'NOT_MODIFIED'} for kind in _KINDS):
                self._last_attempt, self._failures, self._retry_at = now, 0, 0
                if self._retry_timer is not None:
                    cancel = getattr(self._retry_timer, 'cancel', None)
                    if cancel:
                        cancel()
                    self._retry_timer = None
                    self._retry_marker = None
            else:
                self._failures = min(self._failures + 1, 100)
                delay = min(RETRY_BASE_SECONDS * (2 ** min(self._failures - 1, 6)), RETRY_MAX_SECONDS)
                factor = min(1.2, max(0.8, float(self._jitter(0.8, 1.2))))
                self._retry_at = now + delay * factor
                self._schedule_retry(self._retry_at - self._clock())
            try:
                self._write('refresh-state.json', self._envelope(schema=2,
                    last_success=self._last_attempt, retry_at=self._retry_at, failures=self._failures))
            except (OSError, ValueError, RuntimeError):
                # In-memory cooldown remains active when persistence is unavailable.
                pass
            return statuses

    def _refresh_one(self, kind, now):
        token, headers = '', {}
        try:
            scope, token = self._session()  # Never retain a token in this client.
            self._scope = scope
            with self._lock:
                sent_etag = self._etags[kind]
            headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                       'X-MentorFlow-Organization-ID': scope.organization_id,
                       'X-MentorFlow-Workspace-ID': scope.workspace_id}
            if sent_etag:
                headers['If-None-Match'] = sent_etag
            with httpx.Client(verify=self._ssl, timeout=15, follow_redirects=False,
                              transport=self._transport, trust_env=False) as client:
                with client.stream('GET', self._base_url + '/api/enterprise/catalog/' + kind, headers=headers) as response:
                    self.require_current()
                    if response.status_code in (401, 403):
                        raise PlanError('REAUTH_REQUIRED', '目录请求的登录状态已失效，请通过账号入口重新验证；继续使用原有目录。')
                    if response.status_code == 404:
                        raise PlanError('NOT_CONFIGURED', '服务器尚未配置此目录，继续使用本机缓存或内置目录。')
                    if response.status_code == 304:
                        if not sent_etag or _etag(response.headers.get('etag', sent_etag)) != sent_etag:
                            raise CatalogFormatError('304 没有匹配的有效缓存')
                        with self._lock:
                            self.require_current()
                            self._statuses[kind] = CatalogStatus('cache', 'NOT_MODIFIED', '服务器目录未变化，继续使用缓存。', now)
                        return
                    if response.status_code != 200:
                        raise PlanError('HTTP_FAILED', '服务器目录暂不可用，继续使用原有目录。')
                    media = response.headers.get('content-type', '').split(';', 1)[0].lower()
                    if media != 'application/json':
                        raise CatalogFormatError('目录响应必须为 JSON')
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=64*1024):
                        self.require_current()
                        raw.extend(chunk)
                        if len(raw) > MAX_CATALOG_BYTES:
                            raise CatalogFormatError('目录响应超过大小限制')
                    payload = _decode(bytes(raw))
                    _validate(kind, payload)
                    etag = _etag(response.headers.get('etag'))
            with self._lock:
                self.require_current()  # Fence logout/switch while the response was in flight.
                try:
                    self._write(kind + '.json', self._envelope(kind=kind, payload=payload, etag=etag, fetched_at=now))
                except (OSError, ValueError, RuntimeError):
                    raise PlanError('CACHE_WRITE_FAILED', '新目录无法写入本机缓存，继续使用原有目录。') from None
                self._payloads[kind], self._etags[kind] = payload, etag
                self._statuses[kind] = CatalogStatus('server', 'UPDATED', '已更新并缓存服务器目录。', now)
        except PlanError as exc:
            self._failure(kind, exc.code, str(exc), now)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            self._failure(kind, 'INVALID_CATALOG', '服务器目录格式不合格，保留原有目录。', now)
        except Exception:
            self._failure(kind, 'NETWORK_FAILED', '目录连接失败，继续使用本机缓存或内置目录。', now)
        finally:
            token = ''
            headers.clear()

    def _failure(self, kind, code, message, now):
        with self._lock:
            if not self._closed.is_set():
                self._statuses[kind] = CatalogStatus(self._statuses[kind].origin, code, message, now)

    def close(self):
        with self._lock:
            if self._retry_timer is not None:
                cancel = getattr(self._retry_timer, 'cancel', None)
                if cancel:
                    cancel()
                self._retry_timer = None
                self._retry_marker = None
            super().close()
            self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

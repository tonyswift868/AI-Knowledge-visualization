"""Account-scoped, authenticated catalogs with nonblocking refresh and fallback.

load_lists()/load_prices() return validated cache/builtin data immediately and
schedule a refresh. refresh_background() returns a Future (or None when within
six hours); status(kind) exposes 401, stale fallback and disk failures to the UI.
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
from threading import RLock
import time
from uuid import uuid4

import httpx

from .source_filter import SourceLists, parse_lists
from ..file_io import assert_no_reparse_ancestors, canonical_io_path, _native_path
from ..plan_models import PlanError
from ..plan_source import AuthenticatedPlanSource


MAX_CATALOG_BYTES = 1024 * 1024
REFRESH_SECONDS = 6 * 60 * 60
_KINDS = ('source-whitelist', 'model-prices')
_DATA = Path(__file__).with_name('data')


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
                 http_transport=None, clock=time.time, auto_refresh=True):
        if not isinstance(account_id, str) or not re.fullmatch(r'mfa1_[0-9a-f]{64}', account_id):
            raise ValueError('目录缓存需要有效的稳定账号 ID')
        super().__init__(account_id=account_id, session_provider=session_provider,
                         api_base_url=api_base_url, ca_bundle=ca_bundle, http_transport=http_transport)
        self.cache_dir = canonical_io_path(data_root) / 'learning' / 'accounts' / account_id / 'course' / 'catalog'
        self._clock, self._auto_refresh = clock, auto_refresh
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='catalog')
        self._future = None
        self._last_attempt = None
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
            if self._owned(state) and type(state.get('last_attempt')) in (float, int):
                stamp = state['last_attempt']
                if math.isfinite(stamp) and 0 <= stamp <= self._clock():
                    self._last_attempt = stamp
        except (OSError, ValueError, TypeError, RuntimeError):
            pass

    def _owned(self, data):
        return (isinstance(data, dict) and data.get('schema') == 1
                and data.get('account_id') == self.account_id and data.get('base_url') == self._base_url)

    def _envelope(self, **fields):
        return dict(schema=1, account_id=self.account_id, base_url=self._base_url, **fields)

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

    def load_prices(self) -> dict:
        return self._load('model-prices')

    def status(self, kind):
        with self._lock:
            return self._statuses[kind]

    def refresh_background(self):
        """At most one pair of requests per six hours, including failed attempts.

        The last attempt is persisted so reopening the app does not bypass the
        limit. Returns the existing in-flight Future, or None when not due.
        """
        with self._lock:
            if self._closed.is_set():
                return None
            if self._future is not None and not self._future.done():
                return self._future
            now = self._clock()
            if self._last_attempt is not None and now - self._last_attempt < REFRESH_SECONDS:
                return None
            self._last_attempt = now
            self._future = self._executor.submit(self._refresh, now)
            return self._future

    def _refresh(self, now):
        try:
            with self._lock:
                if self._closed.is_set():
                    return {}
                self._write('refresh-state.json', self._envelope(last_attempt=now))
        except (OSError, ValueError, RuntimeError):
            # In-memory throttling still applies; per-catalog writes report disk failures.
            pass
        for kind in _KINDS:
            if self._closed.is_set():
                break
            self._refresh_one(kind, now)
        with self._lock:
            return dict(self._statuses)

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
            super().close()
            self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

"""Optional vendor balances. GET only; failures are absence, never model errors.

Official contracts checked 2026-09-28 (only DeepSeek has live verification):
DeepSeek: https://api-docs.deepseek.com/zh-cn/api/get-user-balance/
Kimi CN: https://platform.kimi.com/docs/api/balance
SiliconFlow CN: https://github.com/siliconflow/siliconcloud/blob/main/openapi.yaml
CN currency: https://docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation
OpenRouter: https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits
  /credits requires a management key. Ordinary keys may yield None; /key's
  limit_remaining is a spending limit, NOT account funds, so it is not used.
Relay contracts and units (do not assume *_usd actually means USD):
https://github.com/songquanpeng/one-api/blob/main/controller/billing.go
https://github.com/QuantumNous/new-api/blob/main/controller/billing.go
https://github.com/QuantumNous/new-api/blob/main/controller/misc.go
https://github.com/QuantumNous/new-api/blob/main/router/dashboard.go

No ordinary inference-key balance contract verified for these providers:
Qwen: https://help.aliyun.com/zh/user-center/developer-reference/api-bssopenapi-2017-12-14-queryaccountbalance
  Cloud account signed credentials, not a DashScope API key.
OpenAI: https://developers.openai.com/api/docs/guides/admin-apis
Claude: https://platform.claude.com/docs/en/manage-claude/usage-cost-api
  Admin cost reports are past spend, not remaining funds.
Gemini: https://ai.google.dev/gemini-api/docs/billing/
GLM: https://docs.bigmodel.cn/cn/guide/platform/equity-explain
  Console billing documented; no verified inference-key balance endpoint.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
import threading
import time
import sqlite3
from urllib.parse import urlsplit

import httpx

from .providers.registry import detect_provider
from .transport import normalize_url

TTL = 180
TIMEOUT = 4.0  # whole operation, including all relay requests; not per-read only
MAX_BYTES = 64 * 1024


def _target(provider, base_url):
    base = normalize_url(base_url)
    url = urlsplit(base)
    host = url.hostname.lower()
    official = {
        'api.deepseek.com': ('deepseek', '/user/balance', {'', '/v1', '/anthropic', '/anthropic/v1', '/v1/messages', '/anthropic/v1/messages', '/chat/completions', '/v1/chat/completions'}),
        'api.moonshot.cn': ('kimi', '/v1/users/me/balance', {'', '/v1', '/v1/chat/completions'}),
        'api.siliconflow.cn': ('siliconflow', '/v1/user/info', {'', '/v1', '/v1/chat/completions'}),
        'openrouter.ai': ('openrouter', '/api/v1/credits', {'', '/api/v1', '/api/v1/chat/completions'}),
    }
    if host in official:
        kind, suffix, paths = official[host]
        if url.scheme != 'https' or url.port not in (None, 443) or url.path not in paths:
            return None
        return kind, f'https://{url.netloc}' + suffix
    if provider not in {'relay_openai', 'relay_anthropic'} or detect_provider(base):
        return None
    # Preserve the user-configured reverse-proxy prefix. Never probe another
    # origin or follow a redirect with the key.
    for suffix in ('/chat/completions', '/messages'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    if base.endswith('/v1'):
        base = base[:-3]
    return 'relay', base


def supported(provider, base_url):
    try:
        return _target(provider, base_url) is not None
    except Exception:
        return False


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or len(str(value)) > 60:
        raise ValueError()
    number = Decimal(str(value))
    # Bound formatted length too: a short "1e-999999999" must not allocate
    # an enormous decimal string when relayed to the page.
    if not number.is_finite() or abs(number.as_tuple().exponent) > 64 or abs(number) > Decimal('1e15'):
        raise ValueError()
    return number


def _entry(amount, currency):
    if currency not in {'CNY', 'USD'}:
        raise ValueError()
    return dict(amount=format(_number(amount), 'f'), currency=currency)


async def _fetch(target, secret, transport):
    kind, url = target
    async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                 timeout=httpx.Timeout(TIMEOUT)) as client:
        async def get(address, *, auth=True):
            headers = {'Accept': 'application/json'}
            if auth:
                headers['Authorization'] = 'Bearer ' + secret
            async with client.stream('GET', address, headers=headers, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise ValueError()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise ValueError()
                data = json.loads(raw, parse_float=Decimal)
                if not isinstance(data, dict) or 'error' in data:
                    raise ValueError()
                return data

        label = '账户余额'
        if kind == 'relay':
            # Public site configuration establishes the units. If missing or
            # token/custom currency mode, hide rather than label it dollars.
            status = await get(url + '/api/status', auth=False)
            if status.get('success') is not True:
                return None
            settings = status['data']
            currency = settings.get('quota_display_type')
            if currency is None and settings.get('display_in_currency') is True:
                currency = 'USD'  # legacy one-api documented currency mode
            if currency not in {'CNY', 'USD'}:
                return None
            sub = await get(url + '/v1/dashboard/billing/subscription')
            usage = await get(url + '/v1/dashboard/billing/usage')
            if sub.get('object') != 'billing_subscription' or usage.get('object') != 'list':
                return None
            limit, used = _number(sub['hard_limit_usd']), _number(usage['total_usage'])
            if limit >= 100000000 or limit < 0 or used < 0:
                return None  # unlimited sentinel is not money
            entries = [_entry(limit - used / 100, currency)]
            label = '站点剩余额度'  # site may report token quota or account quota
        else:
            data = await get(url)
            if kind == 'deepseek':
                if type(data.get('is_available')) is not bool:
                    return None
                entries = [_entry(v['total_balance'], v['currency']) for v in data['balance_infos']]
            elif kind == 'kimi':
                if data.get('status') is not True or type(data.get('code')) is not int or data['code'] != 0:
                    return None
                entries = [_entry(data['data']['available_balance'], 'CNY')]
            elif kind == 'siliconflow':
                if data.get('status') is not True or data.get('code') != 20000:
                    return None
                entries = [_entry(data['data']['totalBalance'], 'CNY')]
            else:
                values = data['data']
                entries = [_entry(_number(values['total_credits']) - _number(values['total_usage']), 'USD')]
        if not 1 <= len(entries) <= 2 or len({v['currency'] for v in entries}) != len(entries):
            return None
        return dict(label=label, balances=entries, updated_at=datetime.now(timezone.utc).isoformat())


async def _bounded(target, secret, transport, cancel, timeout):
    task = asyncio.create_task(_fetch(target, secret, transport))
    try:
        async def wait():
            while not task.done():
                if cancel is not None and cancel.is_set():
                    task.cancel()
                    return None
                await asyncio.sleep(.02)
            return await task
        return await asyncio.wait_for(wait(), timeout=timeout)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class BalanceClient:
    """Per-account in-memory cache, including negative results; never persisted."""
    def __init__(self, *, http_client=None, clock=time.monotonic):
        self._transport, self._clock = http_client, clock
        self._cache, self._flights = {}, {}
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self._cache.clear()

    def query(self, provider_id, base_url, api_key, *, refresh=False, cancel=None):
        try:
            target = _target(provider_id, base_url)
            if target is None or not isinstance(api_key, str) or not api_key or (cancel and cancel.is_set()):
                return None
            key = (target, hashlib.sha256(api_key.encode()).digest())
            with self._lock:
                cached = self._cache.get(key)
                if not refresh and cached and self._clock() < cached[0]:
                    return deepcopy(cached[1])
                flight = self._flights.get(key)
                owner = flight is None
                if owner:
                    flight = self._flights[key] = {'event': threading.Event(), 'value': None}

                    def fetch():
                        value = None
                        try:
                            # A caller owns only its wait, never the shared HTTP request.
                            value = asyncio.run(_bounded(target, api_key, self._transport, None, TIMEOUT))
                        except Exception:
                            pass  # do not log URLs, request/response objects or exceptions
                        finally:
                            with self._lock:
                                if len(self._cache) >= 64:
                                    self._cache.pop(next(iter(self._cache)))
                                self._cache[key] = (self._clock() + TTL, deepcopy(value))
                                flight['value'] = value
                                self._flights.pop(key, None)
                                flight['event'].set()

                    threading.Thread(target=fetch, daemon=True).start()
            # _bounded owns the shared deadline and closes the HTTP request
            # before signalling completion. A second caller-side deadline can
            # race that cleanup; cancellation still releases only this waiter.
            while not flight['event'].wait(.02):
                if cancel and cancel.is_set():
                    return None
            if cancel and cancel.is_set():
                return None
            return deepcopy(flight['value'])
        except Exception:
            return None


class BalanceHistory:
    """Small account-local journal, separated by preset and currency.

    Only observed decreases count. Attribute the whole interval to the later
    observation's local calendar day; offline intervals cannot be apportioned
    reliably. Weeks start Monday. No exchange-rate conversion is guessed.
    A changed key/endpoint starts a new series, rather than inventing spend.
    """

    def __init__(self, account_id, data_root):
        from ..preferences.storage import account_directory
        self.path = account_directory(account_id, data_root) / 'model-balances.sqlite3'
        self._lock = threading.RLock()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('''CREATE TABLE IF NOT EXISTS observations (
            preset_id TEXT NOT NULL, at TEXT NOT NULL, currency TEXT NOT NULL,
            amount TEXT NOT NULL, series TEXT NOT NULL,
            PRIMARY KEY (preset_id, at, currency, series))''')
        return db

    def record(self, preset_id, value, *, series='default'):
        """Cached duplicate observations and failed queries add no rows."""
        if not value:
            return False
        try:
            instant = datetime.fromisoformat(value['updated_at'].replace('Z', '+00:00'))
            if instant.tzinfo is None:
                return False
            at = instant.astimezone(timezone.utc).isoformat()
            entries = [_entry(v['amount'], v['currency']) for v in value['balances']]
            with self._lock, closing(self._connect()) as db, db:
                db.executemany('INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?, ?)',
                               [(preset_id, at, e['currency'], e['amount'], series) for e in entries])
            return True
        except Exception:
            return False  # optional accounting never interrupts the caller

    def summary(self, preset_id, *, now=None, tz=None):
        instant = now or datetime.now(timezone.utc)
        local = instant.astimezone(tz)
        today = local.date()
        first = today - timedelta(days=13)
        monday = today - timedelta(days=today.weekday())
        month = today.replace(day=1)
        result = dict(preset_id=preset_id, ready=False, observations=0, currencies=[], balance=None)
        try:
            if not self.path.exists():
                return result
            with self._lock, closing(self._connect()) as db:
                rows = db.execute('SELECT at, currency, amount, series FROM observations '
                                  'WHERE preset_id=? AND at<=? ORDER BY at',
                                  (preset_id, instant.astimezone(timezone.utc).isoformat())).fetchall()
            result['observations'] = len({r[0] for r in rows})
            currencies = sorted({r[1] for r in rows})
            balances = []
            for currency in currencies:
                previous = None
                daily = {}
                ready = False
                for at, _, amount, series in (r for r in rows if r[1] == currency):
                    amount = Decimal(amount)
                    if previous and previous[1] == series:
                        ready = True
                        day = datetime.fromisoformat(at).astimezone(tz).date()
                        daily[day] = daily.get(day, Decimal(0)) + max(Decimal(0), previous[0] - amount)
                    previous = (amount, series)
                money = lambda n: format(n, 'f')
                result['currencies'].append(dict(currency=currency, ready=ready,
                    today=money(daily.get(today, Decimal(0))),
                    week=money(sum((v for d, v in daily.items() if monday <= d <= today), Decimal(0))),
                    month=money(sum((v for d, v in daily.items() if month <= d <= today), Decimal(0))),
                    days=[dict(date=(first + timedelta(days=i)).isoformat(),
                               amount=money(daily.get(first + timedelta(days=i), Decimal(0)))) for i in range(14)]))
                result['ready'] |= ready
                balances.append(_entry(previous[0], currency))
            if rows:
                result['balance'] = dict(label='账户余额', balances=balances, updated_at=rows[-1][0])
            return result
        except Exception:
            result['unavailable'] = True
            return result


def register(bridge, services):
    """Window-lifetime hourly polling + operation completion hooks.

    Balance IO runs on its own worker, never Qt's UI thread or the classroom
    worker. Existing preparation notifications cover background module jobs.
    """
    from PySide6.QtCore import QObject, QTimer, Slot
    from ..app.bridge import BridgeError

    class Tracker(QObject):
        def __init__(self):
            super().__init__(bridge)
            self.cancel = threading.Event()
            self.lock = threading.Lock()
            self.busy = False
            self.pending = False
            self.bound = None
            self.next_poll = 0
            self.preparation = {}
            self.timer = QTimer(self)
            self.timer.setInterval(30_000)
            self.timer.timeout.connect(self.tick)
            self.timer.start()
            bridge.closing.connect(self.close)
            bridge.request_completed.connect(self.completed)
            bridge.event.connect(self.on_event)
            QTimer.singleShot(0, self.tick)

        @Slot()
        def close(self):
            self.cancel.set()
            self.timer.stop()

        @Slot()
        def tick(self):
            if self.cancel.is_set():
                return
            try:
                current = services()
                # Lightweight stand-ins without an account have no background IO.
                if not hasattr(current, 'account_id') or getattr(current, '_closed', False):
                    return
                if current is not self.bound:
                    self.bound, self.next_poll = current, 0
                    self.preparation.clear()
                if time.monotonic() >= self.next_poll:
                    self.next_poll = time.monotonic() + 3600
                    self.request()
            except Exception:
                pass

        @Slot(str)
        def completed(self, method):
            if method in {'classroom.leave', 'plan.generate', 'plan.add_generate', 'models.save', 'models.select'}:
                self.request()

        @Slot(str)
        def on_event(self, text):
            try:
                event = json.loads(text)
                if event.get('type') != 'event':
                    return
                if event.get('name') in {'ready', 'scope_changed', 'session_recovered'}:
                    self.next_poll = 0
                    self.tick()
                if event.get('name') in {'plan_preparation', 'classroom.materials_ready'}:
                    data = event['data']
                    key = (data['plan_id'], data.get('module_id'))
                    state = data.get('status')
                    previous = self.preparation.get(key)
                    self.preparation[key] = state
                    if state in {'ready', 'failed'} and state != previous:
                        self.request()
            except Exception:
                pass

        def request(self):
            if self.cancel.is_set():
                return
            with self.lock:
                if self.busy:
                    self.pending = True
                    return
                self.busy = True
            threading.Thread(target=self.work, name='mf-balance', daemon=True).start()

        def work(self):
            while True:
                try:
                    current = services()
                    if not self.cancel.is_set() and not getattr(current, '_closed', False) and hasattr(current, 'account_id'):
                        gateway = current.gateway
                        preset = gateway.get_configuration()
                        if preset and preset['has_api_key']:
                            gateway.balance(preset['id'], refresh=True, cancel=self.cancel)
                            if not self.cancel.is_set() and current is services():
                                bridge.push('balance_spend', {'preset_id': preset['id']})
                except Exception:
                    pass
                with self.lock:
                    if self.pending and not self.cancel.is_set():
                        self.pending = False
                        continue
                    self.busy = False
                    return

    def summary(params, ctx):
        refresh = params.get('refresh', False)
        identifier = params.get('preset_id')
        if type(refresh) is not bool or (identifier is not None and
                (not isinstance(identifier, str) or not identifier or len(identifier) > 128)):
            raise BridgeError('PARAM_INVALID', '花费统计参数无效。')
        gateway = services().gateway
        preset = next((p for p in gateway.list_presets() if p['id'] == identifier), None) if identifier else gateway.get_configuration()
        if preset is None:
            return {'preset': None, 'spend': None}
        if refresh:
            value = gateway.balance(preset['id'], refresh=True, cancel=ctx.cancel)
        spend = gateway.balance_history.summary(preset['id'])
        spend['supported'] = supported(preset['provider_id'], preset['base_url']) and preset['has_api_key']
        spend['query_failed'] = refresh and spend['supported'] and value is None
        return {'preset': preset, 'spend': spend}

    bridge.register('usage.spend', summary)
    return Tracker() if isinstance(bridge, QObject) else None

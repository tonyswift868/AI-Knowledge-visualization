"""Offline wire -> real adapter -> gateway -> isolated SQLite, no live keys."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import pytest

from refactor.learning.connections import providers
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.gateway import ModelGateway
from refactor.learning.connections.pricing import PriceCatalog
from tests.refactor.providers.failure_samples import OFFICIAL, expected_tokens, set_text, truncated
from tests.refactor.providers.samples import anthro, ordinary
from .test_gateway import END, KEY, MESSAGES, START

REAL_COMPLETE = providers.complete


def gateway(tmp_path, provider='deepseek'):
    # Deliberately synthetic tariffs for exact arithmetic, not vendor prices.
    rows = [dict(provider=p, model=providers.REGISTRY[p].default_model, currency='CNY',
                 verified=True, checked_on='2026-09-28', source_url='https://fixture.example.test/prices',
                 input_cache_hit_per_mtok='.2', input_cache_miss_per_mtok='2', output_per_mtok='8',
                 search_per_request='.01', conditions={'region': 'cn-beijing' if p == 'qwen' else 'global',
                                                       'service_tier': 'standard'}) for p in OFFICIAL]
    catalog = PriceCatalog.from_dict({'version': 'offline-test', 'unit': 'per_million_tokens', 'models': rows})
    g = ModelGateway('failure-test', tmp_path, price_catalog=catalog)
    g.save_preset(provider, api_key=KEY)
    return g


def connect(monkeypatch, handler):
    def complete(provider, **kwargs):
        def wire(request):
            return handler(request, kwargs)
        return REAL_COMPLETE(provider, http_client=httpx.MockTransport(wire), **kwargs)
    monkeypatch.setattr(providers, 'complete', complete)


def ledger_row(g):
    with sqlite3.connect(g.usage_log.path) as db:
        return db.execute('SELECT state,error_code,usage_json,model FROM calls').fetchone()


@pytest.mark.parametrize('provider', OFFICIAL)
@pytest.mark.parametrize('failure', ['parse', 'empty', 'truncated', 'cancel', 'timeout'])
def test_each_failed_vendor_call_records_final_meters_and_exact_cost(tmp_path, monkeypatch, provider, failure):
    g = gateway(tmp_path, provider)
    data = truncated(provider) if failure == 'truncated' else ordinary(provider)
    # Tier is a vendor fact, including on errors. Unknown tiers remain unpriced.
    if provider == 'openai':
        data['service_tier'] = 'default'
    if failure in {'parse', 'empty'}:
        set_text(data, provider, {'invalid': KEY} if failure == 'parse' else '')

    class AfterBodyTimeout(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps(data).encode()
            raise httpx.ReadTimeout(KEY)

    def handle(_, kwargs):
        if failure == 'cancel':
            kwargs['cancel'].set()
        return httpx.Response(200, stream=AfterBodyTimeout()) if failure == 'timeout' else httpx.Response(200, json=data)
    connect(monkeypatch, handle)
    with pytest.raises(ModelError) as exc:
        g.complete('score_sources', MESSAGES)
    code = {'truncated': 'MODEL_OUTPUT_TRUNCATED', 'cancel': 'MODEL_CANCELLED',
            'timeout': 'MODEL_TIMEOUT'}.get(failure, 'MODEL_RESPONSE_INVALID')
    assert exc.value.code == code and exc.value.request_id
    total, output = expected_tokens(provider)
    for dimension in ['purpose', 'model', 'day']:
        summary = g.usage_log.summary(START, END, dimension)['total']
        assert summary['calls'] == 1
        assert summary['cancelled' if failure == 'cancel' else 'failed'] == 1
        assert summary['errors'] == {code: 1}
        assert summary['input_tokens'] == total
        assert summary['cached_input_tokens'] == 40
        assert summary['uncached_input_tokens'] == total - 40
        assert summary['output_tokens'] == output
        assert summary['cache_hit_rate'] == Decimal(40) / total
        assert summary['amount'] == (Decimal(40) * Decimal('.2') + (total - 40) * 2 + output * 8) / 1_000_000
    state, error, stored, _ = ledger_row(g)
    assert state == ('cancelled' if failure == 'cancel' else 'failed') and error == code
    assert json.loads(stored)['raw']['_mentorflow']['response_received']
    assert KEY not in stored and 'invalid' not in stored


def test_search_excess_keeps_vendor_count_and_sums_failed_and_successful_costs(tmp_path, monkeypatch):
    g = gateway(tmp_path)
    data = anthro(True, provider='deepseek')
    data['usage']['server_tool_use']['web_search_requests'] = 10
    connect(monkeypatch, lambda *_: httpx.Response(200, json=data))
    with pytest.raises(ModelError) as exc:
        g.complete('search', MESSAGES, enable_search=True)
    assert exc.value.code == 'MODEL_RESPONSE_INVALID'
    data['usage'].update(input_tokens=50, cache_read_input_tokens=60)
    data['usage']['server_tool_use']['web_search_requests'] = 1
    g.complete('search', MESSAGES, enable_search=True)
    total = g.usage_log.summary(START, END)['total']
    assert total['calls'] == 2 and total['failed'] == total['succeeded'] == 1
    assert (total['input_tokens'], total['cached_input_tokens'], total['search_requests']) == (270, 100, 11)
    assert total['cache_hit_rate'] == Decimal(100) / 270
    assert total['amount'] == Decimal('.110680')


@pytest.mark.parametrize('second_response', [True, False])
def test_unreported_second_round_does_not_erase_known_first_round_or_claim_complete_cost(tmp_path, monkeypatch, second_response):
    g, requests = gateway(tmp_path), []
    def handle(*_):
        requests.append(True)
        if len(requests) == 1:
            return httpx.Response(200, json=anthro(True, provider='deepseek', reason='pause_turn'))
        if not second_response:
            raise httpx.ReadTimeout(KEY)
        return httpx.Response(200, json={'content': [], 'stop_reason': 'end_turn'})
    connect(monkeypatch, handle)
    with pytest.raises(ModelError):
        g.complete('search', MESSAGES, enable_search=True)
    total = g.usage_log.summary(START, END)['total']
    assert len(requests) == 2 and total['failed'] == 1
    assert total['input_tokens'] is total['cache_hit_rate'] is total['amount'] is None
    assert total['known_tokens']['input_tokens'] == 150
    assert total['known_tokens']['cached_input_tokens'] == 40
    assert total['known_tokens']['output_tokens'] == 20
    assert total['known_tokens']['search_requests'] == 1
    assert len(json.loads(ledger_row(g)[2])['raw']['calls']) == 2


@pytest.mark.parametrize('mode', ['no_headers', 'no_meter', 'bad_meter'])
def test_missing_receipt_is_explicitly_unknown_and_never_priced_zero(tmp_path, monkeypatch, mode):
    g = gateway(tmp_path)
    def handle(*_):
        if mode == 'no_headers':
            # A read failure before headers may have reached/billed the server.
            raise httpx.ReadError(KEY)
        data = anthro(provider='deepseek')
        if mode == 'no_meter':
            del data['usage']
        else:
            data['usage']['input_tokens'] = -1
        return httpx.Response(200, json=data)
    connect(monkeypatch, handle)
    with pytest.raises(ModelError):
        g.complete('plan', MESSAGES)
    stored = ledger_row(g)[2]
    assert stored is not None
    if mode == 'no_headers':
        meta = json.loads(stored)['raw']['_mentorflow']
        assert meta['request_attempted'] is True
        assert 'input_tokens' in meta['unreported_fields']
    total = g.usage_log.summary(START, END)['total']
    assert total['calls'] == total['failed'] == 1
    assert total['amount'] is total['input_tokens'] is None
    if mode == 'bad_meter':
        assert total['cached_input_tokens'] == 40
        assert total['output_tokens'] == 20


def test_concurrent_success_and_cancelled_receipts_are_not_mixed(tmp_path, monkeypatch):
    g = gateway(tmp_path)
    barrier = threading.Barrier(2)
    def handle(_, kwargs):
        barrier.wait(timeout=2)
        data = ordinary('deepseek')
        if kwargs['messages'][0]['content'] == 'cancel':
            data['usage']['input_tokens'] = 900
            kwargs['cancel'].set()
        return httpx.Response(200, json=data)
    connect(monkeypatch, handle)
    def run(label):
        try:
            return g.complete(label, [{'role': 'user', 'content': label}])
        except ModelError as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        result, cancelled = list(pool.map(run, ['success', 'cancel']))
    assert result.usage.input_tokens == 150
    assert cancelled.code == 'MODEL_CANCELLED' and cancelled.usage.input_tokens == 950
    groups = {item['key']: item for item in g.usage_log.summary(START, END)['groups']}
    assert groups['success']['input_tokens'] == 150 and groups['success']['succeeded'] == 1
    assert groups['cancel']['input_tokens'] == 950 and groups['cancel']['cancelled'] == 1


def test_planflow_retries_truncation_once_with_more_room_and_never_parses_it(tmp_path, monkeypatch):
    from refactor.learning.app.plan_flow import PlanFlow, TRANSIENT
    g, requests = gateway(tmp_path), []
    def handle(*_):
        requests.append(True)
        return httpx.Response(200, json=truncated('deepseek'))
    connect(monkeypatch, handle)
    host = SimpleNamespace(services=SimpleNamespace(gateway=g))
    ctx = SimpleNamespace(cancel=threading.Event())
    def parse(_):
        pytest.fail('Partial answer must not reach the plan parser')
    assert 'MODEL_OUTPUT_TRUNCATED' not in TRANSIENT
    with pytest.raises(ModelError) as exc:
        PlanFlow._ask(host, 'plan', (None, MESSAGES), parse, ctx, plan_id='test')
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED' and exc.value.generated_text_length == 4
    # One retry with a doubled output cap (BUG-15); both cut-off answers are billed, neither is parsed.
    assert len(requests) == 2 and g.usage_log.summary(START, END)['total']['calls'] == 2


def test_chat_propagates_truncation_without_saving_turn(tmp_path, monkeypatch):
    from refactor.learning.app import chat
    g, handlers, chunks = gateway(tmp_path), {}, []
    # Gemini's final callback also exercises the non-SSE chat path.
    g.save_preset('gemini', api_key=KEY)
    connect(monkeypatch, lambda *_: httpx.Response(200, json=truncated('gemini')))
    def commit(*args, **kwargs):
        pytest.fail('Truncated chat turn must not be saved')
    store = SimpleNamespace(get=lambda _: SimpleNamespace(messages=[], session_id='chat', version=1, archived=False), commit_turn=commit)
    services = SimpleNamespace(gateway=g, chat=store, settings=SimpleNamespace(load=lambda: {'chat_persona': ''}))
    chat.register(SimpleNamespace(register=lambda name, fn: handlers.__setitem__(name, fn)), lambda: services)
    with pytest.raises(ModelError) as exc:
        handlers['chat.send']({'session_id': 'chat', 'text': 'hello'}, SimpleNamespace(cancel=threading.Event(), stream=chunks.append))
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    assert chunks == ['半截😀{']  # partial stream can be shown, never committed as a complete answer
    assert g.usage_log.summary(START, END)['total']['failed'] == 1


@pytest.mark.parametrize('provider', ['qwen', 'zhipu'])
def test_unreported_search_counter_stays_unknown_on_failed_answer(tmp_path, monkeypatch, provider):
    g = gateway(tmp_path, provider)
    connect(monkeypatch, lambda *_: httpx.Response(200, json=truncated(provider)))
    with pytest.raises(ModelError) as exc:
        g.complete('search', MESSAGES, enable_search=True)
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    total = g.usage_log.summary(START, END)['total']
    assert total['input_tokens'] == 100 and total['cached_input_tokens'] == 40
    assert total['cache_hit_rate'] == Decimal('.4')
    assert total['search_requests'] is total['amount'] is None


@pytest.mark.parametrize('tier', ['priority', None])
def test_failed_response_does_not_inherit_standard_price_without_its_tier(tmp_path, monkeypatch, tier):
    g = gateway(tmp_path, 'openai')
    data = dict(truncated('openai'), service_tier=tier)
    connect(monkeypatch, lambda *_: httpx.Response(200, json=data))
    with pytest.raises(ModelError):
        g.complete('plan', MESSAGES)
    assert g.usage_log.summary(START, END)['total']['amount'] is None


def test_real_deepseek_fixture_truncation_is_billed_with_integrated_catalogue(tmp_path, monkeypatch):
    from refactor.learning.connections import gateway as gateway_module, usage as usage_module
    root = Path(__file__).resolve().parents[3]
    catalog = PriceCatalog(root / 'refactor/learning/course/data/model-prices.json')
    data = json.loads((Path(__file__).parents[1] / 'providers/fixtures/with_tool.json').read_text(encoding='utf-8'))['response']
    actual_timestamp = usage_module.timestamp
    def timestamp(value=None):
        return actual_timestamp(value if value is not None else '2026-09-27T04:00:00Z')
    monkeypatch.setattr(usage_module, 'timestamp', timestamp)
    monkeypatch.setattr(gateway_module, 'timestamp', timestamp)
    g = ModelGateway('fixture-replay', tmp_path, price_catalog=catalog)
    g.save_preset('deepseek', api_key=KEY)
    connect(monkeypatch, lambda *_: httpx.Response(200, json=data))
    with pytest.raises(ModelError) as exc:
        g.complete('search', MESSAGES, enable_search=True)
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    total = g.usage_log.summary(START, END)['total']
    assert (total['input_tokens'], total['cached_input_tokens'], total['uncached_input_tokens'],
            total['output_tokens'], total['search_requests']) == (84285, 38656, 45629, 4618, 8)
    assert total['failed'] == 1
    assert total['cache_hit_rate'] == Decimal(38656) / 84285
    assert total['amount'] == Decimal('.06487412') and total['currency'] == 'CNY'


def test_error_model_metadata_cannot_persist_an_echoed_credential(tmp_path, monkeypatch):
    g = gateway(tmp_path)
    data = dict(truncated('deepseek'), model=KEY)
    connect(monkeypatch, lambda *_: httpx.Response(200, json=data))
    with pytest.raises(ModelError) as exc:
        g.complete('plan', MESSAGES)
    assert exc.value.code == 'MODEL_OUTPUT_TRUNCATED'
    assert KEY not in str(vars(exc.value))
    assert KEY.encode() not in g.usage_log.path.read_bytes()
    assert ledger_row(g)[3] == ''

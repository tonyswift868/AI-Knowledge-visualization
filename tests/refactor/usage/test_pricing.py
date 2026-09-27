import copy
from dataclasses import asdict, replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.pricing import PriceCatalog, estimate_cost, estimate_tokens
from refactor.learning.connections.providers import REGISTRY, ModelResult
from refactor.learning.connections.providers.anthropic_messages import parse
from refactor.learning.connections.providers.base import combine
from .helpers import AT, catalog, row, units

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / 'deploy/enterprise/staging/rp05/catalog/model-prices.example.json'
GLOBAL = dict(region='global', service_tier='standard', cache_mode='implicit')


def test_components_are_exact_decimal_not_double_counted():
    quote = catalog().cost('test', 'model', units(searches=3), at=AT)
    assert quote.amount == Decimal('.030288')
    assert quote.components == dict(cached_input=Decimal('.000008'), uncached_input=Decimal('.00012'),
                                    cache_write_5m=0, cache_write_1h=0, output=Decimal('.00016'), search=Decimal('.03'))
    assert quote.currency == 'CNY'
    assert quote.catalog_version == 'test-v1'


@pytest.mark.parametrize('changes', [dict(verified=False), dict(input_cache_hit_per_mtok=None),
                                    dict(output_per_mtok=None), dict(input_cache_miss_per_mtok=None),
                                    dict(search_per_request=None)])
def test_missing_price_is_not_zero(changes):
    item = row()
    item.update(changes)
    assert catalog(item).cost('test', 'model', units(searches=1), at=AT) is None
    assert catalog().cost('unknown', 'model', units(), at=AT) is None
    assert catalog().cost('test', 'unknown', units(), at=AT) is None


@pytest.mark.parametrize('missing', ['input_tokens', 'cached_input_tokens', 'uncached_input_tokens',
                                    'output_tokens', 'search_requests'])
def test_missing_meter_does_not_quote_complete_amount(missing):
    usage = units(raw={'_mentorflow': {'unreported_fields': [missing]}})
    assert catalog().cost('test', 'model', usage, at=AT) is None
    # Reasoning is already part of output, so missing the breakdown is not an extra charge.
    assert catalog().cost('test', 'model', units(raw={'_mentorflow': {'unreported_fields': ['reasoning_tokens']}}), at=AT)


@pytest.mark.parametrize('mode', ['anthropic', 'kimi', 'openai'])
def test_write_meter_and_ttl_required(mode):
    item = row(cache_write_mode=mode, cache_write_rates={'5m': '2.5', '1h': '4'})
    raw = ({'cache_creation_input_tokens': 30} if mode == 'anthropic' else
           {('input_tokens_details' if mode == 'openai' else 'prompt_tokens_details'): {'cache_write_tokens': 30}})
    assert catalog(item).cost('test', 'model', units(), at=AT) is None
    quote = catalog(item).cost('test', 'model', units(raw=raw), at=AT, context={'cache_write_ttl': '1h'})
    assert quote.components['uncached_input'] == Decimal('.00006')
    assert quote.components['cache_write_1h' if mode != 'openai' else 'cache_write_5m'] == Decimal('.00012' if mode != 'openai' else '.000075')
    if mode != 'openai':
        assert catalog(item).cost('test', 'model', units(raw=raw), at=AT) is None


def test_anthropic_mixed_ttl_and_openai_nonsearch_web_calls():
    raw = {'cache_creation_input_tokens': 30, 'cache_creation': {'ephemeral_5m_input_tokens': 10, 'ephemeral_1h_input_tokens': 20}}
    quote = catalog(row(cache_write_mode='anthropic', cache_write_rates={'5m': 2.5, '1h': 4})).cost('test', 'model', units(raw=raw), at=AT)
    assert quote.components['cache_write_5m'] == Decimal('.000025')
    assert quote.components['cache_write_1h'] == Decimal('.00008')
    raw = {'_mentorflow': {'search_actions': [{'type': 'open_page'}, {'type': 'find_in_page'}]}}
    assert catalog(row(search_billing_unit='tool_calls')).cost('test', 'model', units(raw=raw), at=AT).components['search'] == Decimal('.02')


def test_free_search_quota_and_token_total_reconciliation():
    cat = catalog(row(search_billing_unit='billable_queries'))
    assert cat.cost('test', 'model', units(searches=4), at=AT) is None
    quote = cat.cost('test', 'model', units(searches=4), at=AT, context={'billable_search_requests': 1})
    assert quote.components['search'] == Decimal('.01')
    assert cat.cost('test', 'model', units(searches=4), at=AT, context={'billable_search_requests': 5}) is None
    assert cat.cost('test', 'model', units(raw={'_mentorflow': {'token_total_reconciled': False}}), at=AT) is None


def test_tiers_apply_per_round_not_to_sum_of_requests():
    low, high = row(input_tokens_max=100), row(input_tokens_min=100)
    high['output_per_mtok'] = '80'
    cat = catalog(low, high)
    def result(value):
        return ModelResult('x', value, 'test', 'model', (), (), (), 'stop')
    per_call = units(raw={'_mentorflow': {'unreported_fields': []}})
    combined = combine([result(per_call), result(per_call)]).usage
    assert combined.raw['_mentorflow']['call_usages'] == [asdict(per_call), asdict(per_call)]
    assert cat.cost('test', 'model', combined, at=AT).amount == 2 * cat.cost('test', 'model', per_call, at=AT).amount
    assert cat.cost('test', 'model', units(input=101), at=AT).components['output'] == Decimal('.0016')
    old = replace(combined, raw={'calls': combined.raw['calls']})
    assert cat.cost('test', 'model', old, at=AT) is None
    broken = copy.deepcopy(combined.raw)
    broken['_mentorflow']['call_usages'][0]['input_tokens'] += 1
    assert cat.cost('test', 'model', replace(combined, raw=broken), at=AT) is None


def test_validity_and_conditions_do_not_fallback_to_another_region():
    item = row(conditions={'region': 'cn-beijing'}, valid_from='2026-09-27', valid_through='2026-12-31')
    cat = catalog(item)
    for context, at in [({}, AT), ({'region': 'international'}, AT), ({'region': 'cn-beijing'}, '2027-01-01T00:00:00Z')]:
        assert cat.cost('test', 'model', units(), at=at, context=context) is None
    assert cat.cost('test', 'model', units(), at=AT, context={'region': 'cn-beijing'})


def test_retired_search_price_is_not_reused_for_replacement_api():
    cat = catalog(row(search_valid_through='2026-10-19'))
    future = '2026-10-20T00:00:00Z'
    assert cat.cost('test', 'model', units(), at=future)
    assert cat.cost('test', 'model', units(searches=1), at=future) is None
    assert cat.estimate_cost('test', 'model', input_tokens=100, search_requests=(1, 1), at=future) is None


def test_deepseek_period_unknown_holiday_and_cross_boundary():
    cat = PriceCatalog(CATALOG)
    args = ('deepseek', 'deepseek-v4-flash', units())
    assert cat.cost(*args, at=AT, context=GLOBAL)
    # Monday 10:00 China: no authoritative holiday calendar means no exact amount.
    assert cat.cost(*args, at='2026-09-28T02:00:00Z', context=GLOBAL) is None
    peak = cat.cost(*args, at='2026-09-28T02:00:00Z', context=dict(GLOBAL, price_period='peak'))
    assert peak.amount == 2 * cat.cost(*args, at=AT, context=GLOBAL).amount
    assert cat.cost(*args, at='2026-09-28T00:59:59Z', context=dict(GLOBAL, finished_at='2026-09-28T01:01:00Z')) is None
    data = json.loads(CATALOG.read_text(encoding='utf-8'))
    data['periods']['holiday_calendar'] = {'start': '2026-09-28', 'end': '2026-09-28', 'dates': ['2026-09-28']}
    assert PriceCatalog.from_dict(data).cost(*args, at='2026-09-28T02:00:00Z', context=GLOBAL).amount == peak.amount / 2


def test_actual_deepseek_fixture_quote_and_catalog_coverage():
    raw = json.loads((Path(__file__).parents[1] / 'providers/fixtures/with_tool.json').read_text(encoding='utf-8'))
    result = parse(raw['response'], 'deepseek', 'deepseek-v4-flash', True)
    value = result.usage
    assert (value.input_tokens, value.cached_input_tokens, value.uncached_input_tokens, value.output_tokens, value.search_requests) == (84285, 38656, 45629, 4618, 8)
    cat = PriceCatalog(CATALOG)
    assert cat.cost('deepseek', result.model, value, at='2026-09-26T04:00:00Z', context=GLOBAL).amount == Decimal('.06487412')
    assert cat.cost('deepseek', result.model, value, at=AT, context=dict(GLOBAL, price_period='peak')).amount == Decimal('.12974824')
    data = json.loads(CATALOG.read_text(encoding='utf-8'))
    for provider, spec in REGISTRY.items():
        if provider.startswith('relay_'):   # user-supplied endpoints have no official prices
            continue
        assert any(r['provider'] == provider and spec.default_model in {r['model'], *r.get('aliases', [])} for r in data['models'])
    assert all(not r['verified'] and r['input_cache_miss_per_mtok'] is None for r in data['models'] if r['provider'] in {'kimi', 'zhipu'})


def test_estimates_english_chinese_ranges_and_known_quote():
    assert estimate_tokens('abcd' * 10) == 10
    assert estimate_tokens('学习汉字') == 4
    cat = catalog()
    for text in ['abcd' * 10, '学习汉字']:
        est = estimate_cost(cat, 'test', 'model', text, output_tokens=(10, 20), at=AT)
        assert est.input_tokens_low <= estimate_tokens(text) <= est.input_tokens_high
        actual = cat.cost('test', 'model', units(input=estimate_tokens(text), cached=0, output=15), at=AT)
        assert est.low <= actual.amount <= est.high
    assert cat.estimate_cost('test', 'model', 'search may inject arbitrary content', search_requests=(0, 8), at=AT) is None
    est = cat.estimate_cost('test', 'model', input_tokens=(100, 200), search_requests=(0, 8), at=AT)
    assert est.high >= Decimal('.08')
    assert PriceCatalog.empty().estimate_cost('test', 'model', 'abc', at=AT) is None


def test_estimate_does_not_hide_tier_gaps_or_free_quota():
    cat = catalog(row(input_tokens_max=50), row(input_tokens_min=80))
    assert cat.estimate_cost('test', 'model', input_tokens=(10, 100), at=AT) is None
    cat = catalog(row(input_tokens_max=100), row(input_tokens_min=100))
    assert cat.estimate_cost('test', 'model', input_tokens=200, search_requests=(0, 2), at=AT) is None
    est = catalog(row(search_billing_unit='billable_queries')).estimate_cost('test', 'model', input_tokens=100, output_tokens=0, search_requests=(3, 3), at=AT)
    assert est.high - est.low == Decimal('.03')


@pytest.mark.parametrize('bad', [(2, 1), (-1, 2), (True, 2), 'bad'])
def test_invalid_estimate_bounds(bad):
    with pytest.raises(ModelError):
        catalog().estimate_cost('test', 'model', input_tokens=bad, at=AT)


@pytest.mark.parametrize('payload', ['{', '[]', '{"version":"x","version":"y"}',
                                    '{"version":"x","unit":"per_token","models":[]}'])
def test_invalid_price_file(tmp_path, payload):
    path = tmp_path / 'prices.json'
    path.write_text(payload)
    with pytest.raises(ModelError, match='价格表'):
        PriceCatalog(path)


@pytest.mark.parametrize('changes', [{'input_cache_hit_per_mtok': -1}, {'output_per_mtok': float('nan')},
                                    {'source_url': 'https:missing-host'}, {'checked_on': 'bad'}, {'valid_from': '20260927'},
                                    {'input_tokens_max': None}, {'input_tokens_min': 5, 'input_tokens_max': 5},
                                    {'cache_write_mode': 'guess'}, {'search_rates': {'unknown': 1}}])
def test_invalid_catalog_fields(changes):
    item = row()
    item.update(changes)
    with pytest.raises(ModelError):
        catalog(item)


def test_duplicate_model_alias_ranges_and_missing_file(tmp_path):
    with pytest.raises(ModelError):
        catalog(row(aliases=['alias']), dict(row(), model='alias'))
    with pytest.raises(ModelError):
        PriceCatalog(tmp_path / 'missing.json')
    with pytest.raises(ModelError):
        catalog(periods={'holiday_calendar': {'start': 'bad'}})

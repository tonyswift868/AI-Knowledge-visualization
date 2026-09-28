from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.usage import UsageLog
from .helpers import units

ROOT = Path(__file__).resolve().parents[3]
CAT = ROOT / 'refactor/learning/course/data/model-prices.json'
CONTEXT = dict(region='global', service_tier='standard')


@pytest.mark.parametrize('day', ['2026-01-01', '2026-02-16', '2026-02-23', '2026-04-06',
    '2026-05-05', '2026-06-19', '2026-09-25', '2026-10-07', '2026-01-04', '2026-02-14',
    '2026-02-28', '2026-05-09', '2026-09-20', '2026-10-10'])
def test_holidays_and_makeup_weekends_are_off_peak(day):
    cat = PriceCatalog(CAT)
    quote = cat.cost('deepseek', 'deepseek-flash', units(), at=day + 'T10:00:00+08:00', context=CONTEXT)
    assert cat._period(datetime.fromisoformat(day + 'T10:00:00+08:00')) == 'off_peak'
    if day >= '2026-09-10':  # current tariffs must not be applied before validity
        assert not quote.estimated
    else:
        assert quote is None


def test_unknown_calendar_only_falls_back_to_labelled_peak_bound():
    cat = PriceCatalog(CAT)
    args = ('deepseek', 'deepseek-flash', units())
    future = '2027-02-08T10:00:00+08:00'
    quote = cat.cost(*args, at=future, context=CONTEXT)
    peak = cat.cost(*args, at='2026-09-28T10:00:00+08:00', context=CONTEXT)
    assert quote.amount == peak.amount and quote.estimated and '高峰' in quote.estimation_reason
    assert cat.cost(*args, at=future) is None  # no invented region/tier
    bad = units(raw={'_mentorflow': {'unreported_fields': ['cached_input_tokens']}})
    assert cat.cost('deepseek', 'deepseek-flash', bad, at=future, context=CONTEXT) is None
    assert not cat.cost(*args, at='2027-02-07T10:00:00+08:00', context=CONTEXT).estimated
    assert not cat.cost(*args, at='2027-02-08T22:00:00+08:00', context=CONTEXT).estimated
    est = cat.estimate_cost('deepseek', 'deepseek-flash', input_tokens=100, output_tokens=10,
                            at=future, context=CONTEXT)
    assert est.peak_price_estimate and est.low == est.high == Decimal('.00028')
    assert '高峰价' in est.assumptions[-1]


def test_unknown_end_uses_peak_bound_but_known_cross_period_stays_unpriced():
    cat = PriceCatalog(CAT)
    args = ('deepseek', 'deepseek-flash', units())
    assert cat.cost(*args, at='2027-02-08T08:59:00+08:00',
                    context=dict(CONTEXT, finished_at='2027-02-08T09:01:00+08:00')).estimated
    assert cat.cost(*args, at='2026-09-28T08:59:00+08:00',
                    context=dict(CONTEXT, finished_at='2026-09-28T09:01:00+08:00')) is None


def test_summary_separates_three_states_and_read_only_backfill(tmp_path):
    data = json.loads(CAT.read_text(encoding='utf-8'))
    data['periods']['holiday_calendar'] = None
    path = tmp_path / 'usage.sqlite3'
    log = UsageLog(path, catalog=PriceCatalog.empty())
    value = units(raw={'_mentorflow': {'billing_context': CONTEXT}})
    log.record('deepseek', 'deepseek-flash', 'plan', value, at='2026-09-28T10:00:00+08:00', request_id='old')
    log.catalog = PriceCatalog(CAT)
    log.record('deepseek', 'deepseek-flash', 'plan', value, at='2027-02-08T10:00:00+08:00')
    log.record('deepseek', 'deepseek-flash', 'plan', None, at='2027-02-08T10:00:00+08:00', error_code='MODEL_TIMEOUT')
    for group_by in ('day', 'purpose', 'model'):
        total = log.summary('2026-01-01T00:00:00Z', '2028-01-01T00:00:00Z', group_by)['total']
        assert (total['priced_calls'], total['estimated_calls'], total['unpriced_calls']) == (1, 1, 1)
        assert total['known_amounts'] == total['estimated_amounts']
        assert not total['amount_complete'] and total['amount'] is None
        assert total['recalculated_calls'] == 1
        assert total['unpriced_reasons'] == {'调用没有返回用量，无法计价': 1}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT cost_json FROM calls WHERE request_id='old'").fetchone()[0] is None
    # An estimate saved before the calendar update must never lose its label.
    other = UsageLog(tmp_path / 'estimate.sqlite3', catalog=PriceCatalog.from_dict(data))
    other.record('deepseek', 'deepseek-flash', 'plan', value, at='2026-09-28T10:00:00+08:00')
    other.catalog = PriceCatalog(CAT)
    assert other.summary('2026-01-01T00:00:00Z', '2028-01-01T00:00:00Z')['total']['estimated_calls'] == 1


def test_catalogues_have_identical_sourced_calendar():
    bundled = json.loads(CAT.read_text(encoding='utf-8'))
    example = json.loads((ROOT / 'deploy/enterprise/staging/rp05/catalog/model-prices.example.json').read_text(encoding='utf-8'))
    assert bundled == example
    calendar = bundled['periods']['holiday_calendar']
    assert len(calendar['dates']) == len(set(calendar['dates'])) == 33
    assert calendar['start'] == '2026-01-01' and calendar['end'] == '2026-12-31'
    assert calendar['published_on'] == '2025-11-04' and 'gov.cn' in calendar['source_url']

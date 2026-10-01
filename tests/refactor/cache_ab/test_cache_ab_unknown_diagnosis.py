import json
import sys
from types import SimpleNamespace

from tests.refactor.cache_prefix.diagnose_prefix import run


def test_observed_unknown_usage_is_not_zero_hit_or_simulated_after(tmp_path, monkeypatch):
    # No tool installation needed for this unknown-data branch. The opt-in test
    # separately validates the real tool's parser; this row must be excluded.
    monkeypatch.setitem(sys.modules, 'prefixcash', SimpleNamespace(
        iter_calls=lambda _: iter(()), diagnose_log=lambda _: {},
        build_heatmap=lambda *_: (_ for _ in ()).throw(AssertionError('unknown usage'))))
    calls = tmp_path/'calls'
    calls.mkdir()
    (calls/'failed.json').write_text(json.dumps(dict(id='failure', purpose='teacher',
        system='fixed', messages=[dict(role='user', content='fixture')],
        status='failed', usage={'input_tokens': 0})), encoding='utf-8')
    out = tmp_path/'out'
    result = run(calls, out, observed_only=True)
    assert result['unknown_requests'] == 1 and result['known_requests'] == 0
    assert result['measured_cached'] is None and result['measured_input'] is None
    assert result['after'] == [] and result['after_summary'] == {}
    assert result['before_summary']['teacher']['measured_warm_rate'] is None
    assert result['before_summary']['teacher']['measured_input'] is None
    assert 'actual cache hit is unknown' in (out/'prefix-breaks.svg').read_text()

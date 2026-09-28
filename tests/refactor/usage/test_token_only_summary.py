import json

import pytest

from refactor.learning.connections.usage import UsageLog
from .helpers import units, START, END, AT, catalog


@pytest.mark.parametrize('group', ['purpose', 'model', 'day'])
def test_token_only_response_preserves_missing_usage_without_reading_prices(tmp_path, group):
    log = UsageLog(tmp_path / 'usage.sqlite3', catalog=catalog())
    log.record('test', 'model', 'teacher', units(), at=AT)
    log.record('test', 'model', 'teacher', None, error_code='MODEL_TIMEOUT', at=AT)
    log.catalog = object()  # token summary must never call prices/reprice history
    data = log.summary(START, END, group, tokens_only=True)
    for summary in [data['total'], *data['groups']]:
        assert summary['calls'] == 2 and summary['failed'] == 1
        assert summary['input_tokens'] is summary['cached_input_tokens'] is summary['uncached_input_tokens'] is None
        assert summary['search_requests'] is summary['cache_hit_rate'] is None
        assert summary['incomplete_reasons'] == {'调用未返回用量': 1}
    assert all(word not in json.dumps(data) for word in ('amount', 'currency', 'price', 'cost'))

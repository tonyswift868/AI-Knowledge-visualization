from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.providers import Usage

AT = '2026-09-27T04:00:00+00:00'
START, END = '2026-09-01T00:00:00Z', '2026-10-01T00:00:00Z'


def units(input=100, cached=40, output=20, searches=0, raw=None, reasoning=0):
    return Usage(input, cached, input-cached, output, reasoning, searches, raw or {})


def row(**kwargs):
    return dict(provider='test', model='model', currency='CNY', verified=True,
                source_url='https://pricing.example.test/current', checked_on='2026-09-27',
                input_cache_hit_per_mtok='.2', input_cache_miss_per_mtok='2', output_per_mtok='8',
                search_per_request='.01', **kwargs)


def catalog(*rows, **extra):
    return PriceCatalog.from_dict(dict(version='test-v1', unit='per_million_tokens',
                                       models=list(rows or [row()]), **extra))

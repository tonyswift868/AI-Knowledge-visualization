from refactor.learning.connections.providers import openai_responses
from .samples import responses


def test_responses_preserves_actual_tier_for_cost_accounting():
    data = responses()
    for tier in ['default', 'priority', 'flex', None]:
        if tier is None:
            data.pop('service_tier', None)
        else:
            data['service_tier'] = tier
        result = openai_responses.parse(data, 'gpt-6-astra', False)
        assert result.usage.raw['_mentorflow']['service_tier'] == tier

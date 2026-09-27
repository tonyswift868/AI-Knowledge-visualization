"""Relay stations: user-supplied address and model, parsed by the chosen protocol."""
import copy

import httpx
import pytest

from refactor.learning.connections.providers import REGISTRY, ModelError, complete
from .samples import KEY, MESSAGES, anthro, chat

RELAY = 'https://relay.example.test/v1'


def run(provider, response, **kwargs):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=copy.deepcopy(response))
    result = complete(provider, base_url=RELAY, model='relay-model', api_key=KEY, messages=MESSAGES,
                      http_client=httpx.MockTransport(handle), **kwargs)
    return result, requests


def test_relay_specs_have_no_official_defaults():
    for provider in ('relay_openai', 'relay_anthropic'):
        spec = REGISTRY[provider]
        assert (spec.default_base_url, spec.default_model) == ('', '')
    assert REGISTRY['relay_openai'].search is None
    assert REGISTRY['relay_anthropic'].search.verified is False


def test_openai_format_relay_uses_chat_completions_with_bearer():
    result, requests = run('relay_openai', chat(provider='zhipu'))
    request = requests[0]
    assert str(request.url) == RELAY + '/chat/completions'
    assert request.headers['authorization'] == 'Bearer ' + KEY and 'x-api-key' not in request.headers
    assert result.text == '答案' and result.provider_id == 'relay_openai'
    assert (result.usage.input_tokens, result.usage.cached_input_tokens) == (100, 40)


def test_anthropic_format_relay_uses_messages_with_api_key_header():
    result, requests = run('relay_anthropic', anthro(provider='anthropic'))
    request = requests[0]
    assert str(request.url).endswith('/messages')
    assert request.headers['x-api-key'] == KEY and 'authorization' not in request.headers
    # Native Claude usage semantics: cache reads/writes are added to input.
    assert result.usage.input_tokens == 150 and result.usage.cached_input_tokens == 40


def test_relay_without_address_is_rejected():
    with pytest.raises(ModelError):
        complete('relay_openai', base_url='', model='m', api_key=KEY, messages=MESSAGES)

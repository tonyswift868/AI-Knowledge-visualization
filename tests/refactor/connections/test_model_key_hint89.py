"""Missing-model hints must name the next UI step without making a request."""
import pytest

from refactor.learning.connections import client, gateway, providers, transport
from refactor.learning.connections.credentials import AccountCredentials
from refactor.learning.connections.errors import ModelError


NO_MODEL = "还没有可用的模型：请到 设置 → 模型连接 选择厂商、填入 API Key 并保存。"
NO_KEY = "当前模型还没有 API Key：请到 设置 → 模型连接 填入 API Key 并保存。"
MESSAGES = [{"role": "user", "content": "offline hint check"}]


@pytest.mark.parametrize("kind", ["gateway", "client"])
def test_model_key_hint89_missing_model_and_key(tmp_path, monkeypatch, kind):
    class EmptyCredentials(AccountCredentials):
        def __init__(self, account_id):
            pass

        def get(self, identifier):
            return None

    def forbidden(*args, **kwargs):
        raise AssertionError("No credentials backend or network request is allowed")

    module = gateway if kind == "gateway" else client
    monkeypatch.setattr(module, "AccountCredentials", EmptyCredentials)
    monkeypatch.setattr(providers, "complete", forbidden)
    monkeypatch.setattr(transport, "request", forbidden)
    model = (gateway.ModelGateway if kind == "gateway" else client.ModelClient)("hint89-test", tmp_path)
    try:
        def complete():
            return model.complete("hint89", MESSAGES) if kind == "gateway" else model.complete(MESSAGES)

        with pytest.raises(ModelError) as error:
            complete()
        assert (error.value.code, error.value.message) == ("MODEL_NOT_CONFIGURED", NO_MODEL)
        assert "设置 → 模型连接" in error.value.message
        if kind == "gateway":
            model.save_preset("deepseek")
        else:
            model.save_preset("offline", "https://example.invalid/v1", "offline-model")
        with pytest.raises(ModelError) as error:
            complete()
        assert (error.value.code, error.value.message) == ("MODEL_KEY_REQUIRED", NO_KEY)
        assert "设置 → 模型连接" in error.value.message
    finally:
        model.close()


def test_model_key_hint89_direct_provider_missing_key():
    with pytest.raises(ModelError) as error:
        providers.complete("deepseek", base_url="https://api.deepseek.com/anthropic",
                           model="deepseek-v4-flash", api_key="", messages=MESSAGES)
    assert (error.value.code, error.value.message) == ("MODEL_KEY_REQUIRED", NO_KEY)

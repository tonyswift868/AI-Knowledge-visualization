from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import threading

import pytest

from refactor.learning.connections import providers
from refactor.learning.connections.credentials import AccountCredentials
from refactor.learning.connections.errors import ModelCancelled, ModelError
from refactor.learning.connections.gateway import ModelGateway
from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.preferences.storage import AccountJSON, StorageError

START, END = '2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z'
KEY = 'sk-FAKE-OFFLINE-ONLY'
MESSAGES = [{'role': 'user', 'content': '固定资料'}, {'role': 'assistant', 'content': '已有对话'}, {'role': 'user', 'content': '本轮问题'}]


def answer(provider='deepseek', model=None, raw=None):
    return providers.ModelResult('你好', providers.Usage(100, 40, 60, 20, 0, 0, raw or {}),
                                 provider, model or providers.REGISTRY[provider].default_model, (), (), (), 'stop')


def configured(tmp_path, account='account-one', **kwargs):
    g = ModelGateway(account, tmp_path, **kwargs)
    p = g.save_preset('deepseek', api_key=KEY)
    return g, p


def legacy(tmp_path, *, url='https://api.deepseek.com', key=KEY):
    store = AccountJSON('account-one', tmp_path, 'connections.json')
    identifier = 'a' * 32
    data = {'version': 1, 'active_id': identifier, 'presets': [dict(
        id=identifier, name='DeepSeek V4 Flash', base_url=url, model='deepseek-v4-flash',
        temperature=.2, timeout=60., has_api_key=True)]}
    store.write(data)
    AccountCredentials('account-one').put(identifier, key)
    return store, data


@pytest.mark.parametrize('provider_id', [k for k in providers.REGISTRY if not k.startswith('relay_')])
def test_registry_defaults_crud_and_no_implicit_probe(tmp_path, vault, provider_id):
    g = ModelGateway('account-one', tmp_path)
    assert vault.reads == 0
    spec = providers.REGISTRY[provider_id]
    p = g.save_preset(provider_id, api_key=KEY)
    assert (p['provider_id'], p['display_name'], p['base_url'], p['model'], p['temperature'], p['timeout']) == (
        provider_id, spec.display_name, spec.default_base_url, spec.default_model, None, 120.)
    reads = vault.reads
    assert g.list_presets() == (p,) and g.search_supported()
    assert vault.reads == reads
    changed = g.save_preset(provider_id, preset_id=p['id'], display_name='新名称', model='new-model', timeout=40, temperature=.5, api_key='')
    assert changed['id'] == p['id'] and changed['model'] == 'new-model'
    assert g._credentials.get(p['id']) == KEY
    changed['model'] = 'should-not-mutate-file'
    assert g.get_configuration()['model'] == 'new-model'
    other = g.save_preset(provider_id, api_key='sk-second')
    g.select_preset(p['id'])
    assert g.get_configuration()['id'] == p['id']
    g.delete_preset(p['id'])
    assert g.get_configuration()['id'] == other['id']
    assert g._credentials.get(p['id']) is None
    g.delete_preset(p['id'])
    assert g.usage_log.summary(START, END)['total']['calls'] == 0


def test_key_address_binding_and_accounts(tmp_path):
    g, p = configured(tmp_path)
    with pytest.raises(ModelError) as exc:
        g.save_preset('deepseek', preset_id=p['id'], base_url='https://other.example.test')
    assert exc.value.code == 'API_KEY_CONFIRM_REQUIRED'
    with pytest.raises(ModelError):
        g.save_preset('kimi', preset_id=p['id'])
    with pytest.raises(ModelError):
        g.save_preset('deepseek', base_url='https://user:password@unsafe.example.test', api_key=KEY)
    other = ModelGateway('other-account', tmp_path)
    assert other.list_presets() == ()
    assert other._credentials.get(p['id']) is None
    assert g.usage_log.path != other.usage_log.path
    with pytest.raises(ModelError):
        ModelGateway('../traversal', tmp_path)


def test_legacy_migrates_metadata_and_borrows_original_key(tmp_path, monkeypatch, vault):
    store, data = legacy(tmp_path)
    original = store.path.read_bytes()
    reads = vault.reads
    g = ModelGateway('account-one', tmp_path)
    p = g.get_configuration()
    assert vault.reads == reads
    assert (p['provider_id'], p['id'], p['base_url'], p['model'], p['display_name']) == (
        'deepseek', data['active_id'], 'https://api.deepseek.com', 'deepseek-v4-flash', 'DeepSeek V4 Flash')
    calls = []
    def complete(provider, **kwargs):
        calls.append(kwargs)
        return answer(provider)
    monkeypatch.setattr(providers, 'complete', complete)
    assert g.complete('plan', MESSAGES).text == '你好'
    assert calls[0]['api_key'] == KEY
    assert store.path.read_bytes() == original
    assert ModelGateway('account-one', tmp_path).list_presets() == (p,)
    g.delete_preset(p['id'])
    assert AccountCredentials('account-one').get(p['id']) == KEY
    assert store.path.read_bytes() == original


def test_edit_borrowed_secret_detaches_without_breaking_old_page(tmp_path):
    store, data = legacy(tmp_path)
    original = store.path.read_bytes()
    g = ModelGateway('account-one', tmp_path)
    p = g.save_preset('deepseek', preset_id=data['active_id'], api_key='sk-NEW-FAKE')
    internal = g._load()['presets'][0]
    assert internal['credential_owner'] == 'owned' and internal['credential_id'] != p['id']
    assert g._credentials.get(internal['credential_id']) == 'sk-NEW-FAKE'
    assert g._credentials.get(p['id']) == KEY
    g.delete_preset(p['id'])
    assert g._credentials.get(internal['credential_id']) is None
    assert g._credentials.get(p['id']) == KEY
    assert store.path.read_bytes() == original


@pytest.mark.parametrize('action,code', [('rebind', 'API_KEY_CONFIRM_REQUIRED'), ('remove', 'MODEL_KEY_REQUIRED')])
def test_legacy_key_is_not_borrowed_after_old_page_rebind_or_delete(tmp_path, action, code):
    store, data = legacy(tmp_path)
    g = ModelGateway('account-one', tmp_path)
    if action == 'rebind':
        data['presets'][0]['base_url'] = 'https://another.example.test'
    else:
        data['presets'], data['active_id'] = [], None
    store.write(data)
    with pytest.raises(ModelError) as exc:
        g.complete('plan', MESSAGES)
    assert exc.value.code == code
    assert g.usage_log.summary(START, END)['total']['errors'] == {code: 1}


def test_unrecognized_or_corrupt_legacy_is_never_discarded(tmp_path):
    store, data = legacy(tmp_path, url='https://private.example.test/v1')
    g = ModelGateway('account-one', tmp_path)
    assert g.get_configuration()['provider_id'] is None
    assert len(g.list_presets()) == 1 and not g.search_supported()
    with pytest.raises(ModelError) as exc:
        g.complete('plan', MESSAGES)
    assert exc.value.code == 'MODEL_CONFIG_INVALID'
    other = tmp_path / 'other-root'
    broken, _ = legacy(other)
    broken.path.write_text('{corrupt')
    with pytest.raises(ModelError):
        ModelGateway('account-one', other)
    assert not broken.path.with_name('model-gateway.json').exists()
    assert broken.path.read_text() == '{corrupt'


@pytest.mark.parametrize('migrated', [False, True])
def test_failed_metadata_write_rolls_back_key(tmp_path, monkeypatch, migrated):
    if migrated:
        _, data = legacy(tmp_path)
        g = ModelGateway('account-one', tmp_path)
        p = g.get_configuration()
    else:
        g, p = configured(tmp_path)
    old_values = dict(g._load())
    vault_keys = set(g._credentials._index())
    def fail(*args):
        raise StorageError()
    monkeypatch.setattr(g._storage, 'write', fail)
    with pytest.raises(ModelError):
        g.save_preset('deepseek', preset_id=p['id'], api_key='sk-another')
    assert g._credentials.get(p['id']) == KEY
    assert g._credentials._index() == vault_keys
    assert g._load() == old_values


def test_complete_forwarding_recording_actual_model_and_no_message_changes(tmp_path, monkeypatch):
    g, _ = configured(tmp_path)
    calls, chunks = [], []
    def complete(provider, **kwargs):
        calls.append((provider, kwargs))
        kwargs['on_text']('你')
        kwargs['on_text']('好')
        return answer(provider, model='actual-model-snapshot')
    monkeypatch.setattr(providers, 'complete', complete)
    external = threading.Event()
    result = g.complete('teacher', MESSAGES, system='固定规则 / 计划 / 资料', enable_search=True,
                        on_text=chunks.append, cancel=external, plan_id='plan-one')
    assert ''.join(chunks) == result.text == '你好'
    provider, params = calls[0]
    assert provider == 'deepseek' and params['messages'] == MESSAGES
    assert params['system'] == '固定规则 / 计划 / 资料' and params['enable_search'] is True
    assert isinstance(params['cancel'], threading.Event) and params['cancel'] is not external
    assert params['api_key'] == KEY and not external.is_set()
    total = g.usage_log.summary(START, END, 'model')
    assert total['total']['calls'] == 1 and total['total']['input_tokens'] == 100
    assert total['groups'][0]['key'] == ('deepseek', 'actual-model-snapshot')
    assert total['total']['amount'] is None  # no catalogue installed
    assert KEY.encode() not in g.usage_log.path.read_bytes()
    assert '固定规则'.encode() not in g.usage_log.path.read_bytes()
    assert KEY not in g._storage.path.read_text(encoding='utf-8')


def test_failure_unknown_usage_and_unexpected_exception_redaction(tmp_path, monkeypatch):
    g, _ = configured(tmp_path)
    for failure in [ModelError('MODEL_INSUFFICIENT_BALANCE', '模型账户余额不足', 402), RuntimeError(KEY)]:
        def fail(*args, **kwargs):
            raise failure
        monkeypatch.setattr(providers, 'complete', fail)
        with pytest.raises(ModelError) as exc:
            g.complete('plan', MESSAGES)
        assert KEY not in str(exc.value)
        assert exc.value.request_id
    total = g.usage_log.summary(START, END)['total']
    assert total['calls'] == total['failed'] == 2
    assert total['input_tokens'] is total['amount'] is None
    assert total['errors'] == {'MODEL_INSUFFICIENT_BALANCE': 1, 'MODEL_RESPONSE_INVALID': 1}


def test_partial_usage_on_error_is_recorded(tmp_path, monkeypatch):
    g, _ = configured(tmp_path)
    def fail(*args, **kwargs):
        error = ModelError('MODEL_TIMEOUT', '模型请求超时。')
        error.usage = answer().usage
        raise error
    monkeypatch.setattr(providers, 'complete', fail)
    with pytest.raises(ModelError):
        g.complete('plan', MESSAGES)
    total = g.usage_log.summary(START, END)['total']
    assert total['known_tokens']['input_tokens'] == 100
    assert total['input_tokens'] is total['amount'] is None and not total['usage_complete']


def test_rebinding_key_never_visible_to_old_endpoint_during_save(tmp_path, monkeypatch):
    g, p = configured(tmp_path)
    original_write = g._storage.write
    def before_publish(data):
        assert g._credentials.get(p['id']) == KEY  # old metadata can only see old key
        assert g._load()['presets'][0]['base_url'] == providers.REGISTRY['deepseek'].default_base_url
        original_write(data)
    with monkeypatch.context() as patch:
        # Only the first metadata write is the publication; cleanup writes follow.
        def once(data):
            patch.setattr(g._storage, 'write', original_write)
            before_publish(data)
        patch.setattr(g._storage, 'write', once)
        g.save_preset('deepseek', preset_id=p['id'], base_url='https://new.example.test', api_key='sk-NEW-HOST')
    data = g._load()
    assert data['presets'][0]['credential_id'] != p['id']
    assert data['retired_credentials'] == []
    assert g._credentials.get(p['id']) is None
    assert g._credentials.get(data['presets'][0]['credential_id']) == 'sk-NEW-HOST'


def test_retired_credential_cleanup_survives_failure_and_reopen(tmp_path, monkeypatch):
    g, p = configured(tmp_path)
    def fail(ref):
        raise ModelError('CREDENTIAL_DELETE_FAILED', '测试清理失败')
    with monkeypatch.context() as patch:
        patch.setattr(g._credentials, 'delete', fail)
        with pytest.raises(ModelError) as exc:
            g.save_preset('deepseek', preset_id=p['id'], base_url='https://new.example.test', api_key='sk-NEW-HOST')
        assert exc.value.code == 'CREDENTIAL_DELETE_FAILED'
        assert g.get_configuration()['base_url'] == 'https://new.example.test'
        assert g._load()['retired_credentials'] == [p['id']]
    reopened = ModelGateway('account-one', tmp_path)
    assert reopened._credentials.get(p['id']) == KEY
    reopened.delete_preset(p['id'])
    assert reopened._load()['retired_credentials'] == []
    assert reopened._credentials._index() == set()


def test_setup_failures_and_unsupported_search_are_counted(tmp_path, monkeypatch):
    g = ModelGateway('account-one', tmp_path)
    cases = []
    def attempt(**kwargs):
        with pytest.raises(ModelError) as exc:
            g.complete('plan', MESSAGES, **kwargs)
        cases.append(exc.value.code)
    attempt()
    g.save_preset('deepseek')
    attempt()
    cancel = threading.Event()
    cancel.set()
    attempt(cancel=cancel)
    monkeypatch.setitem(providers.REGISTRY, 'deepseek', replace(providers.REGISTRY['deepseek'], search=None))
    assert not g.search_supported()
    attempt(enable_search=True)
    g.close()
    attempt()
    assert cases == ['MODEL_NOT_CONFIGURED', 'MODEL_KEY_REQUIRED', 'MODEL_CANCELLED', 'MODEL_SEARCH_UNSUPPORTED', 'MODEL_CLIENT_CLOSED']
    assert g.usage_log.summary(START, END)['total']['calls'] == 5


def test_connection_test_is_explicit_and_short(tmp_path, monkeypatch):
    g, _ = configured(tmp_path)
    calls = []
    def complete(provider, **kwargs):
        calls.append(kwargs)
        return answer(provider)
    monkeypatch.setattr(providers, 'complete', complete)
    assert g.usage_log.summary(START, END)['total']['calls'] == 0
    assert g.test_connection().text == '你好'
    assert len(calls) == 1 and calls[0]['max_tokens'] == 32 and not calls[0]['enable_search']
    assert len(calls[0]['messages'][0]['content']) < 20
    assert g.usage_log.summary(START, END)['groups'][0]['key'] == 'connection_test'


def test_parallel_calls_external_cancel_close_and_other_gateway(tmp_path, monkeypatch):
    g, _ = configured(tmp_path)
    other, _ = configured(tmp_path, 'account-two')
    barrier = threading.Barrier(4)
    external = threading.Event()
    def waiting(provider, **kwargs):
        barrier.wait(timeout=5)
        assert kwargs['cancel'].wait(5)
        raise ModelCancelled()
    monkeypatch.setattr(providers, 'complete', waiting)
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(g.complete, 'plan', MESSAGES, cancel=external if i == 0 else None) for i in range(3)]
        barrier.wait(timeout=5)  # all entered provider concurrently
        external.set()
        with pytest.raises(ModelCancelled):
            futures[0].result(timeout=5)
        g.close()
        for future in futures[1:]:
            with pytest.raises(ModelCancelled):
                future.result(timeout=5)
    assert g.usage_log.summary(START, END)['total']['cancelled'] == 3
    assert g._requests == set()
    monkeypatch.setattr(providers, 'complete', lambda provider, **kwargs: answer(provider))
    assert other.complete('plan', MESSAGES).text == '你好'
    assert other.usage_log.summary(START, END)['total']['calls'] == 1


def test_cancel_after_observed_response_retains_billable_usage(tmp_path, monkeypatch):
    g, _ = configured(tmp_path)
    event = threading.Event()
    def complete(provider, **kwargs):
        event.set()
        return answer(provider)
    monkeypatch.setattr(providers, 'complete', complete)
    with pytest.raises(ModelCancelled):
        g.complete('plan', MESSAGES, cancel=event)
    total = g.usage_log.summary(START, END)['total']
    assert total['cancelled'] == 1 and total['input_tokens'] == 100


def test_begin_failure_prevents_paid_request_and_finish_failure_keeps_pending(tmp_path, monkeypatch):
    g, _ = configured(tmp_path)
    def failure(*args, **kwargs):
        raise ModelError('USAGE_STORAGE_ERROR', '测试磁盘错误')
    with monkeypatch.context() as patch:
        patch.setattr(g.usage_log, 'begin', failure)
        with pytest.raises(ModelError) as exc:
            g.complete('plan', MESSAGES)  # default provider stub forbids any call
        assert exc.value.code == 'USAGE_STORAGE_ERROR'
    monkeypatch.setattr(providers, 'complete', lambda provider, **kwargs: answer(provider))
    monkeypatch.setattr(g.usage_log, 'finish', failure)
    with pytest.raises(ModelError) as exc:
        g.complete('plan', MESSAGES)
    assert exc.value.result.text == '你好' and exc.value.request_id
    total = g.usage_log.summary(START, END)['total']
    assert total['calls'] == total['in_progress'] == 1 and total['amount'] is None


def test_installed_catalog_is_loaded_not_the_deploy_example(tmp_path):
    path = tmp_path / 'learning/model-prices.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'version': 'downloaded-example', 'unit': 'per_million_tokens', 'models': []}))
    g = ModelGateway('account-one', tmp_path)
    assert g.price_catalog.version == 'downloaded-example'
    path.write_text('corrupt')
    with pytest.raises(ModelError) as exc:
        ModelGateway('account-one', tmp_path)
    assert exc.value.code == 'PRICE_CATALOG_INVALID'


def test_gateway_prices_only_known_endpoint_and_actual_service_tier(tmp_path, monkeypatch):
    def rate(provider):
        return dict(provider=provider, model=providers.REGISTRY[provider].default_model, currency='USD',
                    verified=True, checked_on='2026-09-27', source_url='https://prices.example.test',
                    input_cache_hit_per_mtok='.2', input_cache_miss_per_mtok='2', output_per_mtok='8',
                    search_per_request=0, conditions={'region': 'global', 'service_tier': 'standard'})
    cat = PriceCatalog.from_dict({'version': 'test', 'unit': 'per_million_tokens',
                                  'models': [rate('deepseek'), rate('openai')]})
    g, p = configured(tmp_path, price_catalog=cat)
    monkeypatch.setattr(providers, 'complete', lambda provider, **kwargs: answer(provider))
    g.complete('official', MESSAGES)
    assert g.usage_log.summary(START, END)['total']['amount'] is not None
    g.save_preset('deepseek', preset_id=p['id'], base_url='https://proxy.example.test', api_key='sk-custom')
    g.complete('custom', MESSAGES)
    groups = {v['key']: v for v in g.usage_log.summary(START, END)['groups']}
    assert groups['custom']['amount'] is None
    g.save_preset('openai', api_key=KEY)
    for tier in ['default', 'priority', None]:
        monkeypatch.setattr(providers, 'complete', lambda provider, **kwargs: answer(provider, raw={'_mentorflow': {'service_tier': tier}}))
        g.complete(str(tier), MESSAGES)
    groups = {v['key']: v for v in g.usage_log.summary(START, END)['groups']}
    assert groups['default']['amount'] is not None
    assert groups['priority']['amount'] is groups['None']['amount'] is None


def test_relay_preset_needs_address_and_model_and_has_no_search(tmp_path, vault):
    g = ModelGateway('account-one', tmp_path)
    with pytest.raises(ModelError):
        g.save_preset('relay_openai', api_key=KEY)
    p = g.save_preset('relay_openai', base_url='https://relay.example.test/v1', model='gpt-relay', api_key=KEY)
    assert (p['provider_id'], p['display_name'], p['model']) == ('relay_openai', '中转站 · OpenAI 格式', 'gpt-relay')
    assert not g.search_supported()
    with pytest.raises(ModelError) as err:
        g.complete('research', MESSAGES, enable_search=True)
    assert err.value.code == 'MODEL_SEARCH_UNSUPPORTED'
    a = g.save_preset('relay_anthropic', base_url='https://relay.example.test', model='claude-relay', api_key=KEY)
    assert a['id'] != p['id'] and g.search_supported()

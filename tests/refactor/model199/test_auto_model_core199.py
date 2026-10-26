import threading

import httpx
import pytest

from refactor.learning.app.services import AppServices
from refactor.learning.connections import credentials, providers
from refactor.learning.connections.gateway import ModelGateway


@pytest.fixture
def model199_vault(monkeypatch):
    class MemoryVault:
        def __init__(self):
            self.values = {}

        def get_password(self, service, username):
            return self.values.get((service, username))

        def set_password(self, service, username, value):
            self.values[(service, username)] = value

        def delete_password(self, service, username):
            self.values.pop((service, username), None)

    vault = MemoryVault()
    monkeypatch.setattr(credentials, '_get_keyring', lambda: vault)
    return vault


class FakeGateway:
    def __init__(self, *, configured=True, status=None, outcome='ok'):
        self.configured = configured
        self.status = status or {'state': 'unknown', 'short': '模型待验证'}
        self.outcome = outcome
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.cancelled = 0
        self.published = []
        self.closed = False
        self.status_read = threading.Event()
        self.status_published = threading.Event()
        self.second_probe_entered = threading.Event()
        self.revision = 1

    def model_status(self):
        self.status_read.set()
        return self.status

    def get_configuration(self):
        return {'has_api_key': self.configured, 'provider_id': 'fake'} if self.configured else None

    def get_configuration_snapshot(self):
        return self.get_configuration(), self.revision

    def configuration_version(self):
        return self.revision

    def _publish_status(self, value):
        self.status = value
        self.published.append(value)
        self.status_published.set()

    def _publish_status_for_revision(self, value, revision=None):
        if revision is not None and revision != self.revision:
            return False
        self._publish_status(value)
        return True

    def test_connection(self, *, cancel=None):
        self.calls += 1
        self.entered.set()
        if self.calls >= 2:
            self.second_probe_entered.set()
        if self.outcome == 'wait':
            while not self.release.wait(.005):
                if cancel is not None and cancel.is_set():
                    self.cancelled += 1
                    self.finished.set()
                    return None
        if self.outcome == 'ok':
            self.status = {'state': 'ok', 'short': '模型正常'}
        elif self.outcome == 'network':
            self.status = {'state': 'error', 'short': '模型连接失败',
                           'fault': {'category': 'network'}}
        elif self.outcome == 'key':
            self.status = {'state': 'error', 'short': '模型 Key 无效',
                           'fault': {'category': 'key'}}
        self.finished.set()

    def cancel_connection_checks(self):
        self.cancelled += 1
        self.revision += 1

    def report_error(self, error, **kwargs):
        self.status = {'state': 'error', 'short': '模型异常',
                       'fault': {'category': 'software'}}

    def close(self):
        self.closed = True


def _services(gateway, tmp_path):
    services = AppServices(account_id='model199-test', data_root=tmp_path,
                           session_provider=lambda: None, api_base_url='https://unused.invalid', ca_bundle=None)
    services._gateway = gateway
    return services


def test_auto_check_is_background_and_success_updates_status(tmp_path):
    gateway = FakeGateway(outcome='wait')
    services = _services(gateway, tmp_path)
    call_returned = threading.Event()
    accepted = []

    def start_check():
        accepted.append(services.start_model_auto_check())
        call_returned.set()

    caller = threading.Thread(target=start_check)
    caller.start()
    assert call_returned.wait(1), 'the UI-facing entry point must return while the probe is pending'
    caller.join(1)
    assert not caller.is_alive()
    assert accepted == [True]
    assert gateway.entered.wait(1)
    assert any(status.get('state') == 'checking' for status in gateway.published)
    # Unrelated window work remains usable while the provider check is pending.
    assert services.model_status()['state'] == 'checking'
    gateway.release.set()
    assert gateway.finished.wait(1)
    services.close()


def test_status_binding_and_duplicate_ready_do_not_wait_for_gateway_initialization(tmp_path):
    gateway = FakeGateway(outcome='wait')
    services = _services(gateway, tmp_path)
    lock_held = threading.Event()
    release_lock = threading.Event()
    operations_done = threading.Event()

    def hold_service_lock():
        with services._lock:
            lock_held.set()
            release_lock.wait(2)

    def account_ready_callbacks():
        services.bind_model_status(lambda _status: None)
        services.model_status()
        services.start_model_auto_check()
        services.start_model_auto_check()
        operations_done.set()

    holder = threading.Thread(target=hold_service_lock)
    caller = threading.Thread(target=account_ready_callbacks)
    holder.start()
    assert lock_held.wait(1)
    caller.start()
    try:
        assert operations_done.wait(1)
    finally:
        release_lock.set()
        holder.join(1)
        caller.join(1)
    assert not holder.is_alive() and not caller.is_alive()
    gateway.release.set()
    services.close()


def test_missing_key_publishes_setup_action_without_request(tmp_path):
    gateway = FakeGateway(configured=False)
    services = _services(gateway, tmp_path)
    assert services.start_model_auto_check()
    assert gateway.status_published.wait(1)
    assert gateway.calls == 0
    assert gateway.status['state'] == 'unconfigured'
    assert gateway.status['fault']['category'] == 'key'
    assert gateway.status['fault']['actions'] == [{'id': 'settings', 'label': '去设置'}]
    services.close()


def test_network_recovery_retries_only_network_failure(tmp_path):
    network = FakeGateway(status={'state': 'error', 'fault': {'category': 'network'}}, outcome='ok')
    services = _services(network, tmp_path)
    assert services.on_network_restored()
    assert network.entered.wait(1)
    assert network.finished.wait(1)
    assert network.status['state'] == 'ok'
    services.close()

    non_network = FakeGateway(status={'state': 'error', 'fault': {'category': 'key'}}, outcome='ok')
    services = _services(non_network, tmp_path)
    assert not services.on_network_restored()
    assert non_network.calls == 0
    non_network.status_read.clear()
    assert services.start_model_auto_check()
    assert non_network.status_read.wait(1)
    assert non_network.calls == 0
    services.close()


def test_configuration_change_cancels_old_probe_and_checks_new_configuration(tmp_path):
    gateway = FakeGateway(outcome='wait')
    services = _services(gateway, tmp_path)
    assert services.start_model_auto_check()
    assert gateway.entered.wait(1)
    assert services.model_configuration_changed()
    # The existing probe observes cancellation; a second probe is scheduled.
    assert gateway.second_probe_entered.wait(1)
    assert gateway.calls == 2
    assert gateway.cancelled >= 1
    services.close()


def test_successful_status_skips_duplicate_probe_and_close_cancels_pending_probe(tmp_path):
    success = FakeGateway(status={'state': 'ok', 'short': '模型正常'})
    services = _services(success, tmp_path)
    assert services.start_model_auto_check()
    assert success.status_read.wait(1)
    assert success.calls == 0
    services.close()

    pending = FakeGateway(outcome='wait')
    services = _services(pending, tmp_path)
    assert services.start_model_auto_check()
    assert pending.entered.wait(1)
    with services._lock:
        cancel = services._model_probe_cancel
    services.close()
    assert cancel.is_set()


def test_late_result_from_prior_configuration_cannot_publish_status(tmp_path):
    gateway = ModelGateway('model199-revision', tmp_path)
    received = []
    gateway.status_listener = received.append
    old_revision = gateway._configuration_revision
    with gateway._lock:
        gateway._configuration_changed_locked()
    gateway._publish_status({'state': 'unknown', 'short': '模型待验证'})

    published = gateway._publish_status_for_revision(
        {'state': 'ok', 'short': '模型正常'}, old_revision)
    assert not published
    assert gateway.model_status()['state'] == 'unknown'
    assert received == [{'state': 'unknown', 'short': '模型待验证'}]
    gateway.close()


def test_blocked_real_request_returns_but_cannot_publish_after_key_change(tmp_path, monkeypatch, model199_vault):
    gateway = ModelGateway('model199-blocked', tmp_path)
    preset = gateway.save_preset('deepseek', api_key='sk-old-test-only')
    request_entered = threading.Event()
    release_response = threading.Event()
    original_complete = providers.complete

    def delayed(provider_id, **kwargs):
        def respond(_request):
            request_entered.set()
            assert release_response.wait(5)
            return httpx.Response(200, json={
                'id': 'fixture', 'type': 'message', 'role': 'assistant',
                'model': 'deepseek-v4-flash',
                'content': [{'type': 'text', 'text': '旧预设的有效回复'}],
                'stop_reason': 'end_turn', 'stop_sequence': None,
                'usage': {'input_tokens': 4, 'output_tokens': 3},
            })
        return original_complete(provider_id, http_client=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(providers, 'complete', delayed)
    result = []
    failures = []

    def call_old_configuration():
        try:
            result.append(gateway.complete('teacher', [{'role': 'user', 'content': 'probe fixture'}]))
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=call_old_configuration)
    worker.start()
    assert request_entered.wait(2)
    gateway.save_preset('deepseek', preset_id=preset['id'], api_key='sk-new-test-only')
    gateway._publish_status({'state': 'unknown', 'short': '模型待验证'})
    release_response.set()
    worker.join(3)

    assert not worker.is_alive()
    assert not failures
    assert result[0].text == '旧预设的有效回复'
    assert gateway.model_status()['state'] == 'unknown'
    assert gateway.usage_log.summary('2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z')['total']['succeeded'] == 1
    gateway.close()


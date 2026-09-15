"""No external SMTP traffic: protocol doubles expose only allowed probe commands."""
import asyncio
import json
import ssl

import pytest

from app.observability import health
from app.enterprise.identity.admin_overview import AdminOverviewService
from test_admin_overview import Engine, Provider


@pytest.fixture
def relay(monkeypatch, tmp_path):
    config = dict(SMTP_MODE='REAL_RELAY', SMTP_HOST='relay.invalid', SMTP_PORT=587,
                  SMTP_USER='fixture', SMTP_FROM='fixture@example.invalid',
                  SMTP_STARTTLS=True, SMTP_SSL=False, SMTP_SECRET_ENV='TEST_RELAY_SECRET')
    path = tmp_path / 'smtp.json'
    path.write_text(json.dumps(config))
    monkeypatch.setenv('MENTORFLOW_SMTP_CONFIG', str(path))
    monkeypatch.setenv('TEST_RELAY_SECRET', 'test-only-not-a-real-credential')
    calls = []
    class SMTP:
        def __init__(self, **kwargs):
            assert kwargs['timeout'] == 1
        def set_debuglevel(self, value): assert value == 0
        def ehlo(self): calls.append('ehlo'); return 250, b''
        def has_extn(self, name): return name == 'starttls'
        def starttls(self, context):
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            calls.append('tls'); return 220, b''
        def login(self, user, password):
            assert calls == ['ehlo', 'tls', 'ehlo']
            calls.append('auth'); return 235, b''
        def noop(self): calls.append('noop'); return 250, b''
        def close(self): calls.append('close')
    monkeypatch.setattr(health.smtplib, 'SMTP', SMTP)
    return SMTP, calls, path


def test_configured_healthy_dependency(relay):
    assert health._email_probe() == (True, 'EMAIL_TLS_AUTH_READY')
    assert relay[1] == ['ehlo', 'tls', 'ehlo', 'auth', 'noop', 'close']


def test_configured_unavailable_dependency(relay, monkeypatch):
    def fail(*args): raise RuntimeError('test-only-not-a-real-credential')
    monkeypatch.setattr(relay[0], 'login', fail)
    assert health._email_probe() == (False, 'EMAIL_TRANSPORT_UNAVAILABLE')
    assert relay[1][-1] == 'close'


def test_missing_authority(monkeypatch):
    monkeypatch.delenv('MENTORFLOW_SMTP_CONFIG', raising=False)
    assert health._email_probe() == (False, 'EMAIL_AUTHORITY_NOT_CONFIGURED')


def test_missing_secret(relay, monkeypatch):
    monkeypatch.delenv('TEST_RELAY_SECRET')
    assert health._email_probe() == (False, 'EMAIL_CONFIGURATION_UNAVAILABLE')
    assert relay[1] == []


def test_existing_file_secret_reference(relay, monkeypatch, tmp_path):
    monkeypatch.delenv('TEST_RELAY_SECRET')
    secret = tmp_path / 'secret'
    secret.write_text('test-only-not-a-real-credential')
    secret.chmod(0o600)
    value = json.loads(relay[2].read_text())
    value['SMTP_SECRET_FILE'] = str(secret)
    relay[2].write_text(json.dumps(value))
    assert health._email_probe() == (True, 'EMAIL_TLS_AUTH_READY')


def test_missing_starttls_never_authenticates(relay, monkeypatch):
    monkeypatch.setattr(relay[0], 'has_extn', lambda self, name: False)
    assert health._email_probe() == (False, 'EMAIL_TRANSPORT_UNAVAILABLE')
    assert relay[1] == ['ehlo', 'close']


def test_insecure_transport_rejected(relay):
    value = json.loads(relay[2].read_text())
    value['SMTP_STARTTLS'] = False
    relay[2].write_text(json.dumps(value))
    assert health._email_probe() == (False, 'EMAIL_CONFIGURATION_UNAVAILABLE')
    assert relay[1] == []


@pytest.mark.parametrize('ready,expected', [(True, 'healthy'), (False, 'unavailable')])
def test_overview_consumes_registry(ready, expected, monkeypatch):
    monkeypatch.setattr(health, '_email_probe', lambda: (ready, 'EMAIL_TEST_DEPENDENCY'))
    registry = health.configure_default_health_probes(runtime_profile='ACCOUNT_CORE_MVP', registry=health.HealthRegistry())
    registry.register('postgres', lambda: (True, 'TEST'))
    registry.register('keycloak_discovery', lambda: (True, 'TEST'))
    registry.started = True
    service = AdminOverviewService(Engine(), 'issuer', Provider([]), registry)
    result = asyncio.run(service.collect())
    assert result['services']['email'] == dict(status=expected, code='EMAIL_TEST_DEPENDENCY')


def test_overview_without_authority_fails_closed():
    result = asyncio.run(AdminOverviewService(Engine(), 'issuer', Provider([])).collect())
    assert result['services']['email'] == dict(status='unavailable', code='EMAIL_AUTHORITY_UNAVAILABLE')


def test_dependency_observation_failure_preserves_backend_startup_authority():
    class Registry:
        started = True
        def readiness(self):
            raise TimeoutError('dependency observation unavailable')
    result = asyncio.run(AdminOverviewService(Engine(), 'issuer', Provider([]), Registry()).collect())
    assert result['services']['backend'] == dict(status='healthy', code='RUNTIME_STARTED')
    assert result['services']['email']['status'] == 'unavailable'

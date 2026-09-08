"""The local user-test publisher cannot weaken production transport policy."""
from types import SimpleNamespace
import pytest
from scripts import build_release as publisher


def options(**changes):
    return SimpleNamespace(**(dict(uat_loopback=True, signing_key='external-test.pem',
        development_signing=False, certificate_thumbprint=None,
        origin='http://127.0.0.1:48218') | changes))


def test_reusable_test_key_with_compiled_uat_loopback(monkeypatch):
    monkeypatch.setattr(publisher, 'APP_ID', 'MentorFlow.UAT.E108Assembly1')
    assert publisher.publication_origin(options()) == 'http://127.0.0.1:48218'


def test_production_identity_cannot_enable_uat_transport(monkeypatch):
    monkeypatch.setattr(publisher, 'APP_ID', 'MentorFlow')
    with pytest.raises(ValueError, match='compiled UAT'):
        publisher.publication_origin(options())


@pytest.mark.parametrize('origin', ['http://localhost:48218', 'http://192.0.2.1',
    'http://127.0.0.1.evil.example', 'http://user:password@127.0.0.1',
    'http://127.0.0.1/path', 'https://example.com'])
def test_uat_rejects_nonliteral_remote_or_credentialed_origins(monkeypatch, origin):
    monkeypatch.setattr(publisher, 'APP_ID', 'MentorFlow.UAT.E108Assembly1')
    with pytest.raises(ValueError): publisher.publication_origin(options(origin=origin))


@pytest.mark.parametrize('change', [dict(signing_key=None), dict(development_signing=True),
                                  dict(certificate_thumbprint='certificate')])
def test_uat_does_not_mix_ephemeral_or_production_signing(monkeypatch, change):
    monkeypatch.setattr(publisher, 'APP_ID', 'MentorFlow.UAT.E108Assembly1')
    with pytest.raises(ValueError): publisher.publication_origin(options(**change))


def test_external_production_key_still_requires_https():
    with pytest.raises(ValueError): publisher.publication_origin(options(uat_loopback=False))
    assert publisher.publication_origin(options(uat_loopback=False, origin='https://updates.example.com')) == 'https://updates.example.com'

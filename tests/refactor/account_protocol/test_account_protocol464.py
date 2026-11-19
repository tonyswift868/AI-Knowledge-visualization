"""BUG-292 account timeout defaults; no network or credentials."""

from refactor.desktop.account_protocol import AccountConfig


def test_account_protocol464_default_timeout_matches_sync_budget():
    config = AccountConfig(
        'https://identity.example.test/realms/mentorflow',
        'mentorflow-desktop', 'mentorflow-api', 'https://api.example.test',
    )
    assert config.request_timeout_seconds == 20.0


def test_account_protocol464_environment_can_keep_explicit_timeout(monkeypatch):
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_ISSUER', 'https://identity.example.test/realms/mentorflow')
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_CLIENT_ID', 'mentorflow-desktop')
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_AUDIENCE', 'mentorflow-api')
    monkeypatch.setenv('MENTORFLOW_ENTERPRISE_API_BASE_URL', 'https://api.example.test')
    monkeypatch.delenv('MENTORFLOW_DESKTOP_OIDC_HTTP_TIMEOUT_SECONDS', raising=False)
    assert AccountConfig.from_env().request_timeout_seconds == 20.0
    monkeypatch.setenv('MENTORFLOW_DESKTOP_OIDC_HTTP_TIMEOUT_SECONDS', '10')
    assert float(AccountConfig.from_env().request_timeout_seconds) == 10.0

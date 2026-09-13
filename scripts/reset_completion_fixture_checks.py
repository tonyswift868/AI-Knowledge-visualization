"""Actual Desktop -> HTTPS Backend -> mTLS Keycloak completion, without SMTP."""
import asyncio
import secrets
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from scripts.action_token_fixture_checks import Page


def run(identity, issuer, tls, api, runtime, checks):
    from desktop.enterprise_oidc import DesktopOidcConfig
    from desktop.reset_completion import ResetCompletionClient

    def control(operation, name, **extra):
        response = identity.post(issuer + '/mf-native-fixture-control/action', json={
            'operation': operation, 'username': name, 'purpose': 'reset', **extra})
        assert response.status_code == 200, 'fixture setup failed'
        return response.json()

    def follow(browser, response):
        for _ in range(5):
            if response.status_code not in (302, 303): return response
            target = urljoin(str(response.url), response.headers['location'])
            assert urlsplit(target).netloc == urlsplit(issuer).netloc
            response = browser.get(target)
        raise AssertionError('fixture redirect limit')

    async def exercise():
        async with httpx.AsyncClient(verify=tls, trust_env=False, timeout=12, follow_redirects=False) as transport:
            desktop = ResetCompletionClient(DesktopOidcConfig(issuer=issuer, client_id='mentorflow-desktop',
                audience='mentorflow-api', api_base_url=api), http_client=transport)
            state = secrets.token_urlsafe(48)
            ticket = await desktop.begin(state)
            name = 'action-' + secrets.token_hex(8)
            control('create', name, password=secrets.token_urlsafe(26))
            token = control('mint', name, reset_state=state)
            checks['real_desktop_backend_reset_pending'] = await desktop.status(ticket) == 'PENDING'
            sessions_before = len(runtime.native_sessions.families)
            with httpx.Client(verify=tls, trust_env=False, timeout=12, follow_redirects=False) as browser:
                response = follow(browser, browser.get(issuer + '/login-actions/action-token?' + urlencode({
                    'key': token['token'], 'client_id': 'mentorflow-desktop', 'tab_id': token['tab']})))
                page = Page(response.text)
                assert 'password-new' in page.inputs and page.forms, 'native reset form missing'
                assert urlsplit(page.forms[0]).netloc == urlsplit(issuer).netloc
                password = secrets.token_urlsafe(26)
                browser.post(page.forms[0], data={'password-new': password, 'password-confirm': password})
                # The HTML response is deliberately discarded. Only the canonical
                # backend poll and an independent credential oracle prove success.
            checks['real_desktop_backend_reset_completed'] = await desktop.status(ticket) == 'COMPLETED'
            checks['real_reset_credential_committed'] = control('check-password', name, password=password)['valid']
            checks['real_reset_no_auto_login'] = len(runtime.native_sessions.families) == sessions_before
            newer = await desktop.begin(secrets.token_urlsafe(48))
            checks['real_backend_new_watch_pending'] = await desktop.status(newer) == 'PENDING'
            checks['real_backend_unknown_watch_invalid'] = await desktop.status(secrets.token_urlsafe(32)) == 'INVALID'
    asyncio.run(exercise())

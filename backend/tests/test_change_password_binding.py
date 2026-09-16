"""Authenticated actor gate, independent of optional browser session storage."""
import asyncio
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.enterprise.identity import lifecycle_api as api
from app.enterprise.identity.native import NativeSessions, denied, PREFIX
from app.enterprise.identity.native import router as native_router
from app.enterprise.identity.oidc import IdentityError
from test_native_auth import principal


@pytest.mark.parametrize('case', ['sessionless', 'session', 'wrong', 'invalid',
    'expired', 'revoked', 'missing_actor', 'subject', 'issuer', 'body_subject', 'same_password'])
def test_change_password_binding(case):
    async def run():
        writes = []
        password = 'synthetic-old-password'
        class Verifier:
            async def authenticate(self, username, supplied):
                if username != 'fixture-user' or supplied != password: raise denied()
                return principal('other' if case == 'subject' else 'user-a',
                    **({'issuer': 'https://other.invalid/realm'} if case == 'issuer' else {}))
            async def aclose(self): pass
        class Admin:
            async def username_for_subject(self, subject):
                assert subject == 'user-a'
                return 'fixture-user'
            async def change_password(self, subject, new):
                nonlocal password
                writes.append(subject)
                password = new
        sessions = NativeSessions(Verifier(), principal().issuer)
        # The gate itself is exercised with all optional-session variants; actual
        # token-store validation is still executed, not inferred from a header.
        token = await sessions.login('fixture-user', password, 'fixture') if case not in ('subject', 'issuer') else None
        if token is None:
            sessions.verifier = Verifier()
            token = {'access_token': 'mfn1_a_fixture'}
        async def revalidate(request):
            if case in ('subject', 'issuer'):
                pass  # Principal mismatch is injected only at reauthentication.
            else:
                sessions.authenticate(request.headers['Authorization'][7:])
            return NS(session=NS() if case == 'session' else None,
                      context=NS(actor=None if case == 'missing_actor' else
                                 NS(issuer=principal().issuer, subject_id='user-a')))
        if case == 'expired': sessions.clock = lambda: 10**12
        if case == 'revoked': sessions.logout(token['refresh_token'])
        app = FastAPI(); app.include_router(api.router); app.include_router(native_router)
        app.state.enterprise_identity_runtime = NS(lifecycle=NS(active=True, admin=Admin()),
            native_sessions=sessions, revalidate_product_identity=revalidate)
        @app.exception_handler(IdentityError)
        async def error(request, exc):
            return JSONResponse({'code': exc.code}, status_code=exc.status)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://api.invalid') as client:
            headers = {'Authorization': 'Bearer ' + (token['access_token'] if case != 'invalid' else 'mfn1_a_unknown')}
            payload = {'current_password': 'wrong' if case == 'wrong' else password, 'new_password': 'synthetic-new-password'}
            if case == 'body_subject': payload['subject'] = 'other'
            if case == 'same_password': payload['new_password'] = password
            response = await client.post(api.PREFIX+'/change-password', headers=headers, json=payload)
            if case in ('session', 'sessionless'):
                assert response.status_code == 200
                assert writes == ['user-a']
                for supplied, expected in [('synthetic-old-password', 401), ('synthetic-new-password', 200)]:
                    result = await client.post(PREFIX+'/login', json={'username': 'fixture-user', 'password': supplied})
                    assert result.status_code == expected
            else:
                assert response.status_code == (400 if case in ('wrong', 'body_subject', 'same_password') else 401)
                if case == 'same_password': assert response.json()['code'] == 'NEW_PASSWORD_SAME_AS_CURRENT'
                if case == 'wrong': assert response.json()['code'] == 'CURRENT_PASSWORD_INVALID'
                assert not writes
        await sessions.aclose()
    asyncio.run(run())


def test_actual_runtime_revalidation_accepts_native_actor_without_session():
    from fastapi.testclient import TestClient
    from test_enterprise_identity_runtime import _runtime, _app, ISSUER, ORIGIN
    runtime, _ = _runtime()
    password = 'synthetic-old-password'
    writes = []
    class Verifier:
        async def authenticate(self, username, supplied):
            if (username, supplied) != ('fixture-user', password): raise denied()
            return principal('runtime-user-001', issuer=ISSUER)
        async def aclose(self): pass
    class Admin:
        async def username_for_subject(self, subject):
            assert subject == 'runtime-user-001'
            return 'fixture-user'
        async def change_password(self, subject, new):
            nonlocal password
            writes.append(subject); password = new
    runtime.native_sessions = NativeSessions(Verifier(), ISSUER)
    runtime.lifecycle = NS(active=True, admin=Admin())
    bindings = []
    original = runtime.revalidate_product_identity
    async def observe(request):
        binding = await original(request)
        bindings.append((binding.authentication_mode, binding.session, binding.context.actor.subject_id))
        return binding
    runtime.revalidate_product_identity = observe
    with TestClient(_app(runtime), base_url=ORIGIN) as client:
        logged = client.post(PREFIX+'/login', json={'username':'fixture-user','password':password})
        assert logged.status_code == 200
        headers = {'Authorization': 'Bearer '+logged.json()['access_token'],
                   'X-MentorFlow-Organization-ID':'org-a', 'X-MentorFlow-Workspace-ID':'workspace-a'}
        assert client.get('/api/enterprise/auth/me', headers=headers).status_code == 200
        response = client.post(api.PREFIX+'/change-password', headers=headers,
            json={'current_password':password, 'new_password':'synthetic-new-password'})
        assert response.status_code == 200
        assert bindings == [('BEARER', None, 'runtime-user-001')]
        assert writes == ['runtime-user-001']
        for supplied, expected in [('synthetic-old-password',401),('synthetic-new-password',200)]:
            assert client.post(PREFIX+'/login', json={'username':'fixture-user','password':supplied}).status_code == expected

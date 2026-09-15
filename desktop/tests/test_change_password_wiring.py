"""Settings callback uses the existing account-bound native transport."""
import json
from types import SimpleNamespace

import httpx
import pytest

from desktop.main_window import MainWindow
from desktop.rehydration_transport import RehydrationTransportError, invalidate_active
from desktop.tests.test_rehydration_transport import transport


def test_settings_change_uses_bearer_context_and_rejects_client_subject():
    calls = []
    def handler(request):
        assert request.headers['authorization'].startswith('Bearer mfn1_a_')
        assert request.headers['x-mentorflow-organization-id'] == 'org-a'
        assert request.headers['x-mentorflow-workspace-id'] == 'ws-a'
        assert request.url.path == '/api/enterprise/auth/lifecycle/change-password'
        assert json.loads(request.content) == {'current_password':'synthetic-old', 'new_password':'synthetic-new'}
        calls.append(request.url.path)
        return httpx.Response(200, json={'state':'PASSWORD_CHANGED'})
    session = transport(handler)
    try:
        MainWindow._change_enterprise_password(SimpleNamespace(_rehydration_session=session),
                                              'synthetic-old', 'synthetic-new')
        with pytest.raises(RehydrationTransportError, match='CONTEXT_OVERRIDE_REJECTED'):
            session.post('/api/enterprise/auth/lifecycle/change-password', json={
                'current_password':'synthetic-old', 'new_password':'synthetic-new', 'subject':'other'})
        assert len(calls) == 1
    finally:
        invalidate_active()

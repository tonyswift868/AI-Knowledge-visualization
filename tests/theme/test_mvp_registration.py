"""Offline configuration/rollback contracts; never connects to Keycloak."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
spec = importlib.util.spec_from_file_location('mvp_registration', ROOT / 'scripts/enterprise/configure_mvp_registration.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def policy():
    return m.read_json(m.POLICY)


@pytest.fixture
def before():
    return {
        'realm': {'realm': 'mentorflow', 'registrationAllowed': False,
                  'registrationEmailAsUsername': True, 'verifyEmail': True,
                  'resetPasswordAllowed': True, 'bruteForceProtected': True,
                  'sslRequired': 'external', 'defaultRole': {'name': 'default-roles-mentorflow'}},
        'profile': {'attributes': [
            {'name': name, 'required': {'roles': ['user']},
             'validations': {'length': {'max': 255}},
             'permissions': {'view': ['admin', 'user'], 'edit': ['admin', 'user']},
             'annotations': {'inputType': 'text'}}
            for name in ['username', 'email', 'firstName', 'lastName']
        ] + [{'name': 'customDepartment', 'permissions': {'view': ['admin'], 'edit': ['admin']}}],
                    'groups': [{'name': 'custom-group', 'displayHeader': 'Preserve'}]},
        'verify_email': {'alias': 'VERIFY_EMAIL', 'providerId': 'VERIFY_EMAIL',
                         'name': 'Verify Email', 'enabled': True, 'defaultAction': True,
                         'priority': 50, 'config': {}},
        'other_actions_sha256': m.digest([{'alias': 'VERIFY_PROFILE', 'enabled': True,
                                          'defaultAction': True}, {'alias': 'CONFIGURE_TOTP', 'enabled': True}]),
    }


class FakeClient:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.writes = []

    def snapshot(self):
        return copy.deepcopy(self.state)

    def put(self, key, value, policy):
        self.writes.append(key)
        self.state[key] = copy.deepcopy(value)


def test_minimal_merge_preserves_validators_permissions_security_and_custom_fields(before, policy):
    original = copy.deepcopy(before)
    after = m.merge_policy(before, policy)
    assert before == original
    expected = copy.deepcopy(before)
    expected['realm'].update(registrationAllowed=True, registrationEmailAsUsername=False,
                             verifyEmail=False, resetPasswordAllowed=False)
    for a in expected['profile']['attributes'][1:4]:
        del a['required']
    expected['verify_email']['defaultAction'] = False
    assert after == expected
    assert m.merge_policy(after, policy) == after
    assert 'passwordPolicy' not in after['realm']
    before['realm']['passwordPolicy'] = 'length(14) and notUsername(undefined)'
    assert m.merge_policy(before, policy)['realm']['passwordPolicy'] == before['realm']['passwordPolicy']


@pytest.mark.parametrize('required', [{'roles': ['user']}, {'scopes': ['profile']}])
def test_extra_required_attribute_fails_instead_of_weakening_policy(before, policy, required):
    before['profile']['attributes'][-1]['required'] = required
    with pytest.raises(RuntimeError, match='Additional required'):
        m.merge_policy(before, policy)


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'realm', 'action'])
def test_invalid_profile_and_target_fail_closed(before, policy, mutation):
    if mutation == 'duplicate':
        before['profile']['attributes'].append(copy.deepcopy(before['profile']['attributes'][0]))
    elif mutation == 'missing':
        before['profile']['attributes'].pop(1)
    elif mutation == 'realm':
        before['realm']['realm'] = 'enterprise-production'
    else:
        before['verify_email']['alias'] = 'UPDATE_PASSWORD'
    with pytest.raises(RuntimeError):
        m.merge_policy(before, policy)


def test_apply_repeat_and_exact_rollback(before, policy):
    after = m.merge_policy(before, policy)
    client = FakeClient(before)
    assert m.execute(client, before, after, policy) == 'APPLIED_VERIFIED'
    assert client.writes == ['profile', 'verify_email', 'realm']
    assert m.execute(client, before, after, policy) == 'ALREADY_MATCHED'
    assert len(client.writes) == 3
    assert m.execute(client, before, after, policy, rollback=True) == 'ROLLED_BACK_VERIFIED'
    assert client.writes[3:] == ['realm', 'verify_email', 'profile']
    assert client.state == before
    assert m.execute(client, before, after, policy, rollback=True) == 'ALREADY_MATCHED'


@pytest.mark.parametrize('rollback', [False, True])
def test_interrupted_apply_can_resume_or_rollback(before, policy, rollback):
    after = m.merge_policy(before, policy)
    partial = copy.deepcopy(before)
    partial['profile'] = after['profile']
    client = FakeClient(partial)
    m.execute(client, before, after, policy, rollback=rollback)
    assert client.state == (before if rollback else after)


def test_external_security_change_never_overwritten(before, policy):
    after = m.merge_policy(before, policy)
    client = FakeClient(before)
    client.state['other_actions_sha256'] = 'new-mfa-policy'
    with pytest.raises(RuntimeError, match='drift'):
        m.execute(client, before, after, policy)
    assert client.writes == []


def test_failed_readback_stops_before_registration_enable(before, policy):
    after = m.merge_policy(before, policy)
    client = FakeClient(before)
    client.put = lambda *args: None
    with pytest.raises(RuntimeError, match='Readback'):
        m.execute(client, before, after, policy)
    assert client.state == before


def test_concurrent_admin_change_stops_before_first_put(before, policy):
    after = m.merge_policy(before, policy)
    client = FakeClient(before)
    count = 0
    def snapshot():
        nonlocal count
        count += 1
        if count == 2:
            client.state['realm']['passwordPolicy'] = 'length(30)'
        return copy.deepcopy(client.state)
    client.snapshot = snapshot
    with pytest.raises(RuntimeError, match='Concurrent'):
        m.execute(client, before, after, policy)
    assert client.writes == []


def test_snapshot_limits_realm_fields_and_preserves_other_required_actions(monkeypatch, before):
    client = m.Kcadm(['kcadm.sh'], '/private/session')
    actions = [{'alias': 'VERIFY_PROFILE', 'enabled': True, 'defaultAction': True},
               {'alias': 'UPDATE_PASSWORD', 'enabled': True, 'defaultAction': False},
               before['verify_email']]
    calls = []
    def call(method, endpoint, payload=None, fields=None):
        calls.append((method, endpoint, fields))
        return copy.deepcopy({'realms/mentorflow': before['realm'],
                              'users/profile': before['profile'],
                              'authentication/required-actions/VERIFY_EMAIL': before['verify_email'],
                              'authentication/required-actions': actions}[endpoint])
    monkeypatch.setattr(client, 'call', call)
    first = client.snapshot()
    assert calls[0][2] == m.REALM_FIELDS
    assert 'smtpServer' not in m.REALM_FIELDS
    assert all(method == 'get' for method, _, _ in calls)
    actions[1]['defaultAction'] = True
    assert client.snapshot()['other_actions_sha256'] != first['other_actions_sha256']
    actions.pop(0)
    with pytest.raises(RuntimeError, match='VERIFY_PROFILE'):
        client.snapshot()


@pytest.mark.parametrize('credential', ['--password', '--token=not-a-real-token', '--secret=not-a-secret'])
def test_cli_rejects_credential_arguments(credential):
    with pytest.raises(RuntimeError, match='credentials'):
        m.Kcadm(['kcadm.sh', credential], '/private/session')


def test_cli_uses_explicit_partial_realm_and_full_profile_over_stdin(monkeypatch, before, policy):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{}')
    monkeypatch.setattr(m.subprocess, 'run', run)
    client = m.Kcadm(['kcadm.sh'], '/private/session')
    after = m.merge_policy(before, policy)
    for key in ['realm', 'profile', 'verify_email']:
        client.put(key, after[key], policy)
    assert json.loads(calls[0][1]['input']) == policy['realm_patch']
    assert json.loads(calls[1][1]['input']) == after['profile']
    assert json.loads(calls[2][1]['input']) == after['verify_email']
    assert all(argv[-3:] == ['-f', '-', '-n'] for argv, _ in calls)
    assert all('shell' not in options for _, options in calls)
    assert [argv[2] for argv, _ in calls] == ['realms/mentorflow', 'users/profile', 'authentication/required-actions/VERIFY_EMAIL']


def test_cli_plan_apply_verify_rollback_and_tamper(monkeypatch, tmp_path, before):
    client = FakeClient(before)
    monkeypatch.setattr(m, 'Kcadm', lambda *args: client)
    prefix = tmp_path / 'prefix.json'
    prefix.write_text('["kcadm.sh"]', encoding='utf-8')
    plan = tmp_path / 'new-private-plan'
    common = ['--profile', 'account-core-mvp', '--confirm-exclusive-mvp-realm',
              '--prefix-file', str(prefix), '--session-config', '/private/session', '--plan-dir', str(plan)]
    for mode in ['plan', 'apply', 'verify', 'apply', 'rollback']:
        monkeypatch.setattr(sys, 'argv', ['configure', mode, *common])
        m.main()
        if mode == 'plan':
            assert client.writes == []
    assert client.state == before
    data = m.read_json(plan / 'after.json')
    data['realm']['bruteForceProtected'] = False
    (plan / 'after.json').write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['configure', 'apply', *common])
    with pytest.raises(RuntimeError, match='binding'):
        m.main()


def test_other_profiles_and_security_code_identical_to_accepted_base():
    base = '1531c65116063ec606ed1c623acd29fb68e9e8b7'
    paths = ['deploy/enterprise/config/keycloak-realm.json',
             'deploy/enterprise/staging/rp05/compose.account-core-mvp.yml',
             'backend/app/enterprise/identity', 'backend/app/enterprise/contracts_v1.py',
             'backend/alembic', 'desktop', 'deploy/enterprise/themes']
    assert subprocess.check_output(['git', 'diff', base, '--', *paths], cwd=ROOT) == b''
    realm = json.loads((ROOT / paths[0]).read_text(encoding='utf-8'))
    assert realm.get('registrationAllowed', False) is False
    assert realm['verifyEmail'] is True
    client = next(c for c in realm['clients'] if c['clientId'] == 'mentorflow-desktop')
    assert client['directAccessGrantsEnabled'] is False
    assert client['attributes']['pkce.code.challenge.method'] == 'S256'


def test_real_signed_username_only_token_retains_subject_and_display_fallback():
    # Reuse the strict verifier's existing synthetic cryptographic fixtures.
    sys.path.insert(0, str(ROOT / 'backend/tests'))
    import test_enterprise_identity_oidc as fixtures
    from cryptography.hazmat.primitives.asymmetric import rsa
    from desktop.enterprise_oidc import BackendOidcTokenValidator
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = fixtures._claims(preferred_username='synthetic-mvp-learner')
    del claims['email']
    del claims['name']
    principal = asyncio.run(fixtures._adapter(key.public_key()).validate_access_token(fixtures._token(key, claims=claims)))
    assert principal.email is None and principal.display_name is None
    identity = BackendOidcTokenValidator._identity(principal)
    assert identity.display_name == 'synthetic-mvp-learner'
    assert identity.subject == claims['sub'] and identity.issuer == claims['iss']
    generic = BackendOidcTokenValidator._identity(SimpleNamespace(issuer=claims['iss'], subject=claims['sub']))
    assert generic.display_name == 'Enterprise Account' and generic.subject == claims['sub']

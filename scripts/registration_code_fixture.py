"""Local MF-U11 rehearsal: real Keycloak/H2/mTLS/API, in-memory loopback SMTP only."""
import argparse
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
from scripts.email_code_reset_fixture import run


def configure(realm):
    realm['users'][-1]['emailVerified'] = False
    realm['requiredActions'] = [
        dict(alias=name, providerId=name, name=name, enabled=enabled, defaultAction=default, priority=priority)
        for name, enabled, default, priority in (
            ('VERIFY_EMAIL', True, True, 20), ('UPDATE_PASSWORD', True, False, 30),
            ('VERIFY_PROFILE', False, False, 100))]


def scenario(c, identity, issuer, users, password, smtp, runtime):
    from app.enterprise.identity import reset_completion
    root = '/api/enterprise/auth/lifecycle'
    checks = {}
    def post(path, payload):
        # Rate-limiter contracts are covered separately. Each fixture action is
        # a distinct episode; authority attempt/send quotas are never cleared.
        with reset_completion._lock: reset_completion._rates.clear()
        return c.post(root + path, json=payload)
    def control(operation, username):
        r = identity.post(issuer + '/mf-native-fixture-control/registration', json=dict(operation=operation, username=username))
        assert r.status_code == 200, ('fixture_control', r.status_code)
        return r.json()
    def clock(offset):
        r = identity.post(issuer + '/mf-native-fixture-control/action', json=dict(
            operation='clock-offset', username=users[0]['username'], purpose='reset', seconds=str(offset)))
        assert r.status_code == 200
    def register():
        name = 'action-' + secrets.token_hex(8)
        payload = dict(username=name, email=name+'@fixture.invalid', password=password)
        r = post('/register', payload)
        assert r.status_code == 201, ('registration', r.status_code, r.json())
        return payload, r.json()['credential'], smtp.messages[-1][1]
    def verify(ticket, code): return post('/register/verify', dict(credential=ticket, code=code))
    def resend(ticket): return post('/register/resend', dict(credential=ticket))
    def login(payload): return c.post('/api/enterprise/auth/native/login', json={k:payload[k] for k in ('username','password')})

    payload, ticket, code = register()
    state = control('state', payload['username'])
    checks['created_unverified_no_password_action'] = not state['verified'] and state['actions'] == ['VERIFY_EMAIL']
    checks['opaque_credential'] = len(ticket) == 43 and state['subject'] not in ticket
    checks['unverified_cannot_login'] = login(payload).status_code != 200
    checks['resend_cooldown'] = resend(ticket).json()['code'] == 'REGISTRATION_COOLDOWN'
    wrong = '000000' if code != '000000' else '999999'
    checks['wrong_code'] = verify(ticket, wrong).json()['code'] == 'REGISTRATION_WRONG_CODE'
    r = verify(ticket, code)
    assert r.status_code == 200, ('verify', r.status_code, r.json())
    checks['verified_once'] = r.json()['state'] == 'VERIFIED' and verify(ticket, code).status_code == 400
    checks['direct_login'] = login(payload).status_code == 200
    checks['never_restart_verified'] = post('/register', payload).status_code == 409
    checks['no_subject_input'] = post('/register/verify', dict(credential=ticket,code=code,subject=state['subject'])).status_code == 400

    exhausted, ticket, code = register()
    wrong = '000000' if code != '000000' else '999999'
    for _ in range(4): assert verify(ticket, wrong).json()['code'] == 'REGISTRATION_WRONG_CODE'
    checks['five_wrong_invalidate'] = verify(ticket, wrong).json()['code'] == 'REGISTRATION_EXHAUSTED' and verify(ticket, code).status_code == 400
    control('cooldown-elapsed', exhausted['username'])
    r = resend(ticket); new_ticket = r.json()['credential']; new_code = smtp.messages[-1][1]
    checks['resend_rotates'] = ticket != new_ticket and verify(ticket, code).status_code == 400
    checks['resend_after_exhaustion'] = verify(new_ticket, new_code).status_code == 200

    expired, ticket, code = register(); old_id = control('state', expired['username'])['subject']
    clock(901)
    try:
        checks['expired_code'] = verify(ticket, code).json()['code'] == 'REGISTRATION_EXPIRED'
        checks['expired_resend'] = resend(ticket).json()['code'] == 'REGISTRATION_EXPIRED'
        control('cooldown-elapsed', expired['username'])
        r = post('/register', expired); assert r.status_code == 201, ('restart', r.status_code, r.json())
        new_ticket = r.json()['credential']; new_code = smtp.messages[-1][1]
        checks['restart_same_identity'] = old_id == control('state', expired['username'])['subject']
        checks['restart_verify'] = verify(new_ticket, new_code).status_code == 200
    finally: clock(0)

    limited, ticket, code = register()
    for _ in range(9):
        control('cooldown-elapsed', limited['username'])
        r = resend(ticket); assert r.status_code == 200
        ticket = r.json()['credential']
    control('cooldown-elapsed', limited['username'])
    checks['rolling_hour_limit'] = resend(ticket).json()['code'] == 'REGISTRATION_SEND_LIMIT'

    forced, ticket, code = register(); control('force-password', forced['username'])
    assert verify(ticket, code).status_code == 200
    checks['admin_forced_password_preserved'] = 'UPDATE_PASSWORD' in control('state', forced['username'])['actions'] and login(forced).status_code != 200
    checks['existing_accounts_untouched'] = post('/register', dict(username=users[0]['username'],email=users[0]['email'],password=password)).status_code == 409
    clock(901)
    try:
        checks['unmarked_unverified_never_reused'] = post('/register', dict(username=users[-1]['username'],email=users[-1]['email'],password=password)).status_code == 409
    finally: clock(0)
    raced, ticket, code = register()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:
        outcomes=list(pool.map(lambda _: verify(ticket, code).status_code, range(2)))
    checks['concurrent_verify_once'] = sorted(outcomes) == [200, 400]
    count = len(smtp.messages)
    checks['legacy_forgot_no_link'] = post('/forgot', {'account':payload['username']}).status_code == 410 and len(smtp.messages) == count
    # Existing recovery remains a code -> proof -> new password flow.
    r = post('/reset-code/request', {'email':payload['email']})
    import time
    until = time.monotonic()+10
    while len(smtp.messages) == count and time.monotonic() < until: time.sleep(.05)
    r = post('/reset-code/verify', dict(transaction_id=r.json()['transaction_id'],code=smtp.messages[-1][1]))
    new = secrets.token_urlsafe(24)
    r = post('/reset-code/complete', dict(reset_proof=r.json()['reset_proof'],new_password=new,confirm_password=new))
    checks['recovery_complete_and_login'] = r.status_code == 200 and login({**payload,'password':new}).status_code == 200
    checks['chinese_mail_code_ttl_and_in_app_instruction'] = all(
        row['ten_minutes'] and row['no_link'] and row['registration_instruction' if row['registration'] else 'recovery_instruction']
        for row in smtp.receipts)
    return checks


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--keycloak-zip', type=Path, required=True)
    parser.add_argument('--jdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); args.configure = configure; args.scenario = scenario
    run(args)

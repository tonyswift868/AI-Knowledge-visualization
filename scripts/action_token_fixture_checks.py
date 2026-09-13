"""Concurrent HTTPS requests to real Keycloak handlers; native committed-event oracles.

The mTLS fixture control only creates synthetic users, signs native action tokens and
reads committed Keycloak state. It never consumes a token or supplies a success result.
No token, password, confirmation URL or cookie is written to the receipt.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from html.parser import HTMLParser
import secrets
import threading
import time
from urllib.parse import urlencode, urljoin, urlsplit

import httpx


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.forms, self.inputs = [], [], set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href'): self.links.append(attrs['href'])
        if tag == 'form' and attrs.get('action'): self.forms.append(attrs['action'])
        if tag == 'input' and attrs.get('name'): self.inputs.add(attrs['name'])


def run(identity, issuer, tls, receipt, restart=None):
    details = receipt['action_tokens'] = {}
    checks = receipt['checks']
    control = issuer + '/mf-native-fixture-control/action'
    consumed_tokens = []
    watches = {}
    watch_completed = {}
    restarted = False

    def watch_status(ticket):
        result = identity.post(issuer + '/mentorflow-native-identity/v1', json={
            'operation': 'reset-status', 'transaction_id': ticket})
        assert result.status_code == 200, 'reset status unavailable'
        value = result.json()
        assert set(value) == {'schema', 'transaction_id', 'status'} and value['transaction_id'] == ticket
        return value['status']

    def command(operation, name, purpose, **extra):
        ticket = None
        if operation == 'mint' and purpose == 'reset':
            state = secrets.token_urlsafe(48)
            response = identity.post(issuer + '/mentorflow-native-identity/v1', json={'operation': 'reset-watch', 'state': state})
            assert response.status_code == 200 and response.json()['status'] == 'PENDING', 'watch registration failed'
            ticket = response.json()['transaction_id']
            extra['reset_state'] = state
            watches[name], watch_completed[name] = ticket, False
        response = identity.post(control, json=dict(operation=operation, username=name, purpose=purpose, **extra))
        assert response.status_code == 200, 'fixture action control status ' + str(response.status_code)
        value = response.json()
        if ticket: value['transaction_id'] = ticket
        return value

    def create(purpose, **extra):
        name = 'action-' + secrets.token_hex(8)
        command('create', name, purpose, password=secrets.token_urlsafe(26), **extra)
        return name

    def client():
        return httpx.Client(verify=tls, trust_env=False, timeout=30, follow_redirects=False)

    def safe(url):
        assert urlsplit(url).netloc == urlsplit(issuer).netloc, 'unexpected action redirect authority'
        return url

    def first(browser, token, extra=''):
        response = browser.get(issuer + '/login-actions/action-token?' + urlencode(
            dict(key=token['token'], client_id='mentorflow-desktop', tab_id=token['tab'])) + extra)
        return follow(browser, response)

    def follow(browser, response):
        for _ in range(5):
            if response.status_code not in (302, 303) or 'location' not in response.headers: return response
            target = urljoin(str(response.url), response.headers['location'])
            if urlsplit(target).netloc != urlsplit(issuer).netloc: return response
            response = browser.get(safe(target))
        raise AssertionError('action redirect loop')

    def confirm(response):
        return next((url for url in Page(response.text).links if '/login-actions/action-token?' in url), None)

    def reset_form(response):
        page = Page(response.text)
        return page.forms[0] if 'password-new' in page.inputs and page.forms else None

    def finish(browser, response, purpose):
        if purpose == 'verification':
            for _ in range(4):
                link = confirm(response)
                if not link: return response
                response = browser.get(safe(link))
            raise AssertionError('verification confirmation loop')
        form = reset_form(response)
        if form:
            password = secrets.token_urlsafe(26)
            return browser.post(safe(form), data={'password-new': password, 'password-confirm': password})
        return response

    def observation(response):
        lower = response.text.lower()
        return {'status': response.status_code, 'password_form': bool(reset_form(response)),
                'controlled_expiry': any(x in lower for x in ('has expired', 'action expired', 'invalid code', 'invalid request', 'no longer valid')),
                'internal_exception': any(x in lower for x in ('optimisticlockexception', 'stalestateexception',
                    'staleobjectstateexception', 'jakarta.persistence', 'org.hibernate', 'stack trace'))}

    def record(label, name, purpose, before, responses, expected, native_session_expiry=False):
        state = command('state', name, purpose)
        count = state['verify_events' if purpose == 'verification' else 'reset_events'] - before
        passed = count == expected and all(r['status'] < 500 and not r['internal_exception'] for r in responses)
        if purpose == 'reset':
            if count: watch_completed[name] = True
            canonical = watch_status(watches[name])
            expected_status = 'INVALID' if restarted else 'COMPLETED' if watch_completed[name] else 'PENDING'
            checks[label + '_canonical_completion'] = canonical == expected_status
            details[label + '_completion'] = {'status': canonical, 'expected': expected_status}
        if expected == 0: passed &= all(r['status'] == 400 and not r['password_form'] for r in responses)
        if expected == 1 and len(responses) > 1:
            passed &= sum(r['status'] == 400 or (native_session_expiry and r['controlled_expiry']
                          and not r['password_form']) for r in responses) == len(responses)-1
        if purpose == 'verification' and expected == 1: passed &= state['verified']
        details[label] = {'success_count': count, 'responses': responses, 'pass': bool(passed)}
        if purpose == 'reset' and label == 'reset_sequential': details['actual_reset_events'] = state['observations']
        checks[label] = bool(passed)
        print('ACTION_CHECK ' + label + '=' + ('PASS' if passed else 'FAIL'), flush=True)
        return state

    for purpose in ('verification', 'reset'):
        event = 'verify_events' if purpose == 'verification' else 'reset_events'
        name = create(purpose)
        state = command('state', name, purpose)
        details['selected_handlers'] = {k: state[k] for k in ('verify_handler', 'reset_handler', 'password_action')}
        checks['atomic_handlers_selected'] = all('Atomic' in state[k] for k in ('verify_handler', 'reset_handler', 'password_action'))
        token = command('mint', name, purpose)
        with client() as browser:
            response = finish(browser, first(browser, token), purpose)
        state = record(purpose+'_sequential', name, purpose, 0, [observation(response)], 1)
        with client() as browser:
            response = finish(browser, first(browser, token), purpose)
        record(purpose+'_replay', name, purpose, state[event], [observation(response)], 0)
        command('rearm', name, purpose)
        new_token = command('mint', name, purpose)
        assert token['token'] != new_token['token'], 'resend reused old token'
        with client() as browser:
            response = finish(browser, first(browser, token), purpose)
        record(purpose+'_old_after_rearm', name, purpose, state[event], [observation(response)], 0)
        with client() as browser:
            response = finish(browser, first(browser, new_token), purpose)
        record(purpose+'_new_after_resend', name, purpose, state[event], [observation(response)], 1)
        consumed_tokens.append((name, purpose, new_token))

        for n in (2, 5, 10):
            name = create(purpose)
            token = command('mint', name, purpose)
            barrier = threading.Barrier(n)
            confirm_url, cookies = None, None
            if purpose == 'verification':
                with client() as browser:
                    response = first(browser, token)
                    confirm_url = confirm(response)
                    assert confirm_url, 'missing native verification confirmation'
                    cookies = [deepcopy(c) for c in browser.cookies.jar]
            def redeem(_):
                with client() as browser:
                    if cookies:
                        for cookie in cookies: browser.cookies.jar.set_cookie(deepcopy(cookie))
                    barrier.wait(timeout=30)
                    if purpose == 'verification':
                        response = browser.get(safe(confirm_url))
                    else:
                        response = first(browser, token)
                        # All actual concurrent redemption requests finish before password POST.
                        barrier.wait(timeout=30)
                        response = finish(browser, response, purpose)
                    return observation(response)
            with ThreadPoolExecutor(max_workers=n) as pool:
                responses = list(pool.map(redeem, range(n)))
            record(purpose+'_concurrent_'+str(n), name, purpose, 0, responses, 1)

        if purpose == 'reset':
            # Match 04's users with missing profile attributes: required-action
            # evaluation can write user state during reset-link redemption itself.
            for n in (2, 5):
                name = create(purpose, incomplete_profile='true')
                token = command('mint', name, purpose)
                barrier = threading.Barrier(n)
                def redeem_incomplete(_):
                    with client() as browser:
                        barrier.wait(timeout=30)
                        response = first(browser, token)
                        barrier.wait(timeout=30)
                        return observation(finish(browser, response, purpose))
                with ThreadPoolExecutor(max_workers=n) as pool:
                    responses = list(pool.map(redeem_incomplete, range(n)))
                record('reset_incomplete_profile_concurrent_'+str(n), name, purpose, 0, responses, 1)
            name = create(purpose)
            token = command('mint', name, purpose)
            corrections = []
            with client() as browser:
                response = first(browser, token)
                for new, confirmation in (('', ''), ('mismatch-first', 'mismatch-second'), ('short', 'short')):
                    form = reset_form(response)
                    assert form, 'missing reset form during correction'
                    response = browser.post(safe(form), data={'password-new': new, 'password-confirm': confirmation})
                    corrections.append(response.status_code == 200 and bool(reset_form(response)))
                unchanged = command('state', name, purpose)['reset_events'] == 0
                checks['failed_policy_no_completion'] = watch_status(watches[name]) == 'PENDING'
                response = finish(browser, response, purpose)
            checks['reset_validation_retry'] = all(corrections) and unchanged
            record('reset_after_validation_correction', name, purpose, 0, [observation(response)], 1)
            # A copied, already-issued reset form must not bypass token single-use.
            for n in (2, 5, 10):
                name = create(purpose)
                token = command('mint', name, purpose)
                with client() as browser:
                    response = first(browser, token)
                    form = reset_form(response)
                    assert form, 'missing native reset form'
                    cookies = [deepcopy(c) for c in browser.cookies.jar]
                barrier = threading.Barrier(n)
                def submit(_):
                    with client() as browser:
                        for cookie in cookies: browser.cookies.jar.set_cookie(deepcopy(cookie))
                        password = secrets.token_urlsafe(26)
                        barrier.wait(timeout=30)
                        return observation(follow(browser, browser.post(safe(form), data={
                            'password-new': password, 'password-confirm': password})))
                with ThreadPoolExecutor(max_workers=n) as pool:
                    responses = list(pool.map(submit, range(n)))
                record('reset_same_form_concurrent_'+str(n), name, purpose, 0, responses, 1, native_session_expiry=True)

        for scenario in ('expired', 'wrong_email', 'wrong_signature', 'malformed'):
            name = create(purpose)
            extra = {'ttl': '-30'} if scenario == 'expired' else {'wrong_email': 'true'} if scenario == 'wrong_email' else {}
            token = command('mint', name, purpose, **extra)
            if scenario == 'malformed': token['token'] = 'malformed-input'
            if scenario == 'wrong_signature':
                parts = token['token'].split('.')
                parts[2] = ('A' if parts[2][0] != 'A' else 'B') + parts[2][1:]
                token['token'] = '.'.join(parts)
            with client() as browser:
                response = finish(browser, first(browser, token), purpose)
            record(purpose+'_'+scenario, name, purpose, 0, [observation(response)], 0)

    cross = []
    for purpose in ('verification', 'reset'):
        name = create(purpose)
        token = command('mint', name, purpose)
        other = 'UPDATE_PASSWORD' if purpose == 'verification' else 'VERIFY_EMAIL'
        with client() as browser:
            response = first(browser, token, '&execution='+other)
            cross.append(not reset_form(response) if purpose == 'verification' else True)
            wrong_path = 'reset-credentials' if purpose == 'verification' else 'required-action'
            password = secrets.token_urlsafe(26)
            response = browser.post(issuer+'/login-actions/'+wrong_path, params=dict(
                key=token['token'], client_id='mentorflow-desktop', tab_id=token['tab'], execution=other),
                data={'password-new': password, 'password-confirm': password})
            cross.append(response.status_code < 500)
        state = command('state', name, purpose)
        cross.append(state['reset_events' if purpose == 'verification' else 'verify_events'] == 0)
    checks['action_cross_purpose'] = all(cross)
    details['cross_purpose'] = {'pass': all(cross)}
    # Roll back a real native credential write after the observed success event.
    old_password = secrets.token_urlsafe(26)
    # A fresh named user with a known original credential for the rollback oracle.
    name = 'action-' + secrets.token_hex(8)
    command('create', name, 'reset', password=old_password)
    token = command('mint', name, 'reset')
    command('rollback', name, 'reset')
    with client() as browser:
        finish(browser, first(browser, token), 'reset')
    state = command('state', name, 'reset')
    checks['rollback_credential_unchanged'] = command('check-password', name, 'reset', password=old_password)['valid']
    checks['rollback_no_completion'] = watch_status(watches[name]) == 'PENDING' and state['reset_events'] == 0
    checks['rollback_event_observed'] = any(e['phase'] == 'AFTER_ROLLBACK' and e['type'] == 'UPDATE_CREDENTIAL' for e in state['observations'])
    details['rollback_observations'] = state['observations']

    name = 'action-' + secrets.token_hex(8)
    command('create', name, 'reset', password=old_password)
    token = command('mint', name, 'reset')
    command('fail-commit', name, 'reset')
    with client() as browser:
        finish(browser, first(browser, token), 'reset')
    state = command('state', name, 'reset')
    checks['failed_commit_no_completion'] = watch_status(watches[name]) == 'PENDING' and state['reset_events'] == 0
    checks['failed_commit_credential_unchanged'] = command('check-password', name, 'reset', password=old_password)['valid']

    # A valid older action must still bind its original flow if a redemption URL
    # supplies the newer request's state. It cannot complete the new observer.
    name = create('reset')
    old = command('mint', name, 'reset')
    old_ticket = watches[name]
    newer = command('mint', name, 'reset')
    with client() as browser:
        finish(browser, first(browser, old, '&state=' + secrets.token_urlsafe(48)), 'reset')
    checks['old_unredeemed_action_cannot_complete_new_watch'] = watch_status(old_ticket) == 'COMPLETED' and watch_status(watches[name]) == 'PENDING'

    name = create('reset')
    token = command('mint', name, 'reset')
    command('pause-commit', name, 'reset')
    with client() as browser:
        response = first(browser, token)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(finish, browser, response, 'reset')
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = command('state', name, 'reset')
                if any(e['type'] == 'UPDATE_CREDENTIAL' for e in state['observations']): break
                time.sleep(.05)
            checks['completion_pending_before_commit'] = not future.done() and watch_status(watches[name]) == 'PENDING'
            command('release-commit', name, 'reset')
            future.result(timeout=20)
    checks['completion_after_commit'] = watch_status(watches[name]) == 'COMPLETED'
    checks['completion_idempotent_read'] = all(watch_status(watches[name]) == 'COMPLETED' for _ in range(3))
    previous = watches[name]
    command('mint', name, 'reset')
    checks['old_event_cannot_satisfy_new_request'] = watch_status(previous) == 'COMPLETED' and watch_status(watches[name]) == 'PENDING'
    checks['unknown_watch_invalid'] = watch_status(secrets.token_urlsafe(32)) == 'INVALID'
    try:
        command('clock-offset', name, 'reset', seconds='901')
        checks['watch_lease_expired'] = watch_status(watches[name]) == 'EXPIRED'
        checks['completed_fact_stable_after_action_expiry'] = watch_status(previous) == 'COMPLETED'
    finally:
        command('clock-offset', name, 'reset', seconds='0')
    if restart:
        restart()
        restarted = True
        for name, purpose, token in consumed_tokens:
            event = 'verify_events' if purpose == 'verification' else 'reset_events'
            state = command('state', name, purpose)
            command('rearm', name, purpose)
            with client() as browser:
                response = finish(browser, first(browser, token), purpose)
            record(purpose+'_consumed_after_restart', name, purpose, state[event], [observation(response)], 0)

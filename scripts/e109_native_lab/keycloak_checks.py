"""Native action handlers and native event/state oracles; no mail transport."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from html.parser import HTMLParser
import json
import os
import secrets
import threading
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from e109_native_lab.run import ROOT


class Page(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.forms, self.links, self.inputs = [], [], set()
        self.feed(content)
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form' and a.get('action'):
            self.forms.append(a['action'])
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        if tag == 'input' and a.get('name'):
            self.inputs.add(a['name'])


class Tokens:
    def __init__(self, lab):
        self.lab = lab
        self.prefix = '/realms/' + lab.identity
    def user(self, purpose):
        name = 'mf-e109-sec04-' + secrets.token_hex(6)
        password = secrets.token_urlsafe(26)
        self.lab.api(self.lab.realm_path + '/users', 'POST', dict(username=name,
            enabled=True, email=name+'@invalid.example', emailVerified=purpose=='reset',
            requiredActions=['VERIFY_EMAIL'] if purpose=='verification' else [],
            credentials=[dict(type='password',value=password,temporary=False)]))
        data = self.lab.api(self.lab.realm_path + '/users?username='+name+'&exact=true')
        assert len(data) == 1
        return name, data[0]['id']
    def mint(self, name, purpose, **kwargs):
        response = self.lab.http.post(self.prefix + '/mf-e109-sec04-fixture/mint',
            params=dict(user=name,purpose=purpose,**kwargs),headers={'X-Lab-Lease':self.lab.lease})
        assert response.status_code == 200, 'NATIVE_MINT_FAILED'
        return response.json()
    def browser(self, token):
        browser = httpx.Client(timeout=15, trust_env=False, follow_redirects=False,verify=self.lab.tls_context)
        # Start like a fresh browser opening an email link. Keycloak owns cookie
        # encoding/path; a hand-written cookie would invalidate this evidence.
        return browser
    def events(self, user, event):
        events = self.lab.api(self.lab.realm_path + '/events?' + urlencode(dict(user=user,type=event,max=100)))
        return sum(item['type']==event and not item.get('error') for item in events)
    def state(self, user):
        return self.lab.api(self.lab.realm_path + '/users/' + user)['emailVerified']
    def first(self, browser, token, extra=''):
        query = urlencode(dict(key=token['token'],client_id='mf-e109-sec04-client',tab_id=token['tab']))
        response = browser.get(self.lab.base+self.prefix+'/login-actions/action-token?'+query+extra)
        for _ in range(5):
            if response.status_code not in (302,303) or 'location' not in response.headers:
                return response
            target = urljoin(str(response.url),response.headers['location'])
            if urlsplit(target).netloc != urlsplit(self.lab.base).netloc:
                return response
            response = browser.get(target)
        raise AssertionError('NATIVE_REDIRECT_LOOP')
    def finish_verify(self, browser, response):
        pages = []
        for _ in range(4):
            page = Page(response.text)
            pages.append(dict(status=response.status_code, forms=len(page.forms), links=len(page.links),
                cookies=[dict(name=c.name,secure=c.secure,path=c.path,domain=c.domain) for c in browser.cookies.jar],
                request_had_cookie='cookie' in response.request.headers,
                messages=[m for m in ('already verified','has been verified','Invalid','expired','confirm','proceed','authenticate','Update password') if m.lower() in response.text.lower()]))
            link = next((v for v in page.links if '/login-actions/action-token?' in v), None)
            if link is None:
                return response
            assert urlsplit(link).netloc == urlsplit(self.lab.base).netloc
            response = browser.get(link)
        self.lab.report['verification_page_diagnostics'] = pages
        raise AssertionError('VERIFICATION_CONFIRM_LOOP')
    def reset_form(self, response):
        page = Page(response.text)
        return page.forms[0] if 'password-new' in page.inputs and page.forms else None
    def finish_reset(self, browser, response, password):
        form = self.reset_form(response)
        if form is None:
            return response
        assert urlsplit(form).netloc == urlsplit(self.lab.base).netloc
        return browser.post(form, data={'password-new':password,'password-confirm':password})


def run(lab):
    checks = lab.report['checks']
    tokens = Tokens(lab)
    denied = lab.http.post(tokens.prefix+'/mf-e109-sec04-fixture/mint',
        params={'user':'absent','purpose':'verification'},headers={'X-Lab-Lease':'invalid-lab-lease'})
    assert denied.status_code == 404, 'UNPROTECTED_FIXTURE_SIGNER'
    checks['fixture_signer_access_guard'] = 'PASS'
    details = lab.report['token_results'] = {}
    for purpose, event in (('verification','VERIFY_EMAIL'),('reset','UPDATE_PASSWORD')):
        for scenario in ('valid','wrong','wrong_signature','malformed','expired','replay','used_after_rearm','concurrent'):
            name, uid = tokens.user(purpose)
            native = tokens.mint(name,purpose, **({'ttl':-30} if scenario=='expired' else {'wrongEmail':'true'} if scenario=='wrong' else {}))
            if scenario=='malformed':
                native['token'] = 'malformed-lab-input'
            if scenario=='wrong_signature':
                pieces = native['token'].split('.')
                pieces[2] = ('A' if pieces[2][0]!='A' else 'B') + pieces[2][1:]
                native['token'] = '.'.join(pieces)
            before = tokens.events(uid,event)
            if scenario == 'concurrent':
                barrier = threading.Barrier(2)
                confirm_url = None
                cookies = None
                if purpose=='verification':
                    with tokens.browser(native) as prepared:
                        initial = tokens.first(prepared,native)
                        confirm_url = next(v for v in Page(initial.text).links if '/login-actions/action-token?' in v)
                        cookies = [deepcopy(c) for c in prepared.cookies.jar]
                # Two independent browser clients carry the same token/session;
                # barriers release both redemption requests (and password POSTs).
                def redeem(_):
                    with tokens.browser(native) as browser:
                        if cookies is not None:
                            for cookie in cookies:
                                browser.cookies.jar.set_cookie(deepcopy(cookie))
                        barrier.wait(timeout=15)
                        if purpose=='verification':
                            # Both threads GET the identical native confirmation
                            # URL with copies of the authentic native cookie jar.
                            response = browser.get(confirm_url)
                        else:
                            response = tokens.first(browser,native)
                            form_ready = tokens.reset_form(response) is not None
                            barrier.wait(timeout=15)
                            response = tokens.finish_reset(browser,response,secrets.token_urlsafe(26))
                            return {'status':response.status_code,'challenge':form_ready}
                        return {'status':response.status_code}
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(redeem,range(2)))
            else:
                with tokens.browser(native) as browser:
                    response = tokens.first(browser,native)
                    if scenario in ('valid','replay','used_after_rearm'):
                        if purpose=='verification':
                            response = tokens.finish_verify(browser,response)
                        else:
                            response = tokens.finish_reset(browser,response,secrets.token_urlsafe(26))
                    responses = [{'status':response.status_code,'password_form':tokens.reset_form(response) is not None}]
                    if scenario in ('replay','used_after_rearm'):
                        initial_success = tokens.events(uid,event)-before
                        assert initial_success == 1, 'REPLAY_POSITIVE_CONTROL_FAILED'
                        before = tokens.events(uid,event)
                if scenario == 'used_after_rearm':
                    # A later legitimate required-action request must never make
                    # an already consumed token spendable again.
                    lab.api(lab.realm_path+'/users/'+uid,'PUT',{'requiredActions':[
                        'VERIFY_EMAIL' if purpose=='verification' else 'UPDATE_PASSWORD']})
                if scenario in ('replay','used_after_rearm'):
                    with tokens.browser(native) as browser:
                        response = tokens.first(browser,native)
                        response = tokens.finish_verify(browser,response) if purpose=='verification' else tokens.finish_reset(browser,response,secrets.token_urlsafe(26))
                        responses.append({'status':response.status_code,'password_form':tokens.reset_form(response) is not None})
            successes = tokens.events(uid,event)-before
            expected = 1 if scenario in ('valid','concurrent') else 0
            passed = successes == expected
            if purpose=='verification' and expected==1:
                passed = passed and tokens.state(uid)
            if scenario in ('wrong','wrong_signature','malformed','expired'):
                passed = passed and not responses[0]['password_form']
                if purpose=='verification':
                    passed = passed and not tokens.state(uid)
            details[purpose+'_'+scenario] = dict(result='PASS' if passed else 'FAIL',
                native_success_events=successes,responses=responses)
            if scenario=='concurrent':
                details[purpose+'_'+scenario]['same_token_concurrent_requests'] = 2
                details[purpose+'_'+scenario]['server_errors'] = sum(r['status']>=500 for r in responses)
                checks[purpose+'_concurrent_graceful_rejection'] = (
                    'FAIL' if any(r['status']>=500 for r in responses) else 'PASS')
            checks[purpose+'_'+scenario] = 'PASS' if passed else 'FAIL'
            print(purpose.upper()+'_'+scenario.upper()+'='+checks[purpose+'_'+scenario],flush=True)
    # Signed purpose dispatch must ignore attacker-controlled execution hints.
    outcomes = []
    for purpose in ('verification','reset'):
        name,uid = tokens.user(purpose)
        if purpose=='reset':
            lab.api(lab.realm_path+'/users/'+uid,'PUT',{'emailVerified':False})
        native = tokens.mint(name,purpose)
        with tokens.browser(native) as browser:
            response = tokens.first(browser,native,extra='&execution='+('UPDATE_PASSWORD' if purpose=='verification' else 'VERIFY_EMAIL'))
            outcomes.append(not tokens.reset_form(response) if purpose=='verification' else tokens.events(uid,'VERIFY_EMAIL')==0 and not tokens.state(uid))
            wrong_path = '/login-actions/reset-credentials' if purpose=='verification' else '/login-actions/required-action'
            password = secrets.token_urlsafe(26)
            browser.post(lab.base+tokens.prefix+wrong_path,
                params={'key':native['token'],'client_id':'mf-e109-sec04-client','tab_id':native['tab'],
                        'execution':'UPDATE_PASSWORD' if purpose=='verification' else 'VERIFY_EMAIL'},
                data={'password-new':password,'password-confirm':password})
            outcomes.append(tokens.events(uid,'UPDATE_PASSWORD' if purpose=='verification' else 'VERIFY_EMAIL')==0)
    checks['token_cross_purpose'] = 'PASS' if all(outcomes) else 'FAIL'
    anti_enumeration(lab,tokens)
    rate_limit(lab)
    caps = json.loads((ROOT/'release/account-capabilities.json').read_text())['account_capabilities']
    assert caps['register'] is False and caps['forgot_password'] is False, 'CAPABILITY_ENABLED'
    checks['release_capabilities_fail_closed'] = 'PASS'


def anti_enumeration(lab,tokens):
    name,uid = tokens.user('reset')
    roles = {item['name'] for item in lab.api(lab.realm_path+'/users/'+uid+'/role-mappings/realm/composite')}
    assert 'LEARNER' in roles and not roles.intersection({'OWNER','ADMIN','TEACHER'}), 'DEFAULT_ROLE_NOT_LEARNER'
    lab.report['checks']['default_role_learner_native'] = 'PASS'
    results = []
    # Native credential validation for a stored synthetic user and absent user.
    for username in (name,'mf-e109-sec04-absent'):
        response = lab.http.post(tokens.prefix+'/protocol/openid-connect/token', data=dict(
            grant_type='password',client_id='mf-e109-sec04-client',username=username,
            password=secrets.token_urlsafe(24)))
        results.append((response.status_code,response.json()))
    known_messages = {'Invalid user credentials','Invalid username or password.',
        'Account is not fully set up','Account temporarily disabled, contact admin or try again later.',
        'Account disabled','Invalid client credentials','Client not allowed for direct access grants'}
    lab.report['anti_enumeration_observation'] = [dict(status=status,
        error=data.get('error'), description=data.get('error_description') if data.get('error_description') in known_messages else 'UNRECOGNIZED_REDACTED') for status,data in results]
    lab.report['checks']['anti_enumeration_native_credentials'] = (
        'PASS' if results[0] == results[1] and results[0][0] == 400 else 'FAIL')
    boundary = []
    for _ in range(5):
        response = lab.http.post(tokens.prefix+'/protocol/openid-connect/token', data=dict(
            grant_type='password',client_id='mf-e109-sec04-client',username=name,
            password=secrets.token_urlsafe(24)))
        boundary.append((response.status_code,response.json())==results[0])
    state = lab.api(lab.realm_path+'/attack-detection/brute-force/users/'+uid)
    passed = all(boundary) and state['disabled']
    lab.report['brute_force_observation'] = {'disabled':state['disabled'], 'num_failures':state['numFailures'],
        'failure_factor':5,'external_response_unchanged':all(boundary)}
    lab.report['checks']['brute_force_native_boundary'] = 'PASS' if passed else 'FAIL'


def rate_limit(lab):
    # Reuse exact repository maps/zones/thresholds and native nginx lifecycle.
    import sys
    sys.path.insert(0,str(ROOT/'backend/tests'))
    os.environ['E109_TEST_NGINX_EXE'] = str(lab.args.nginx_exe)
    from test_e109_security_native_stage2 import native_nginx, request
    prefix = lab.home/'rate-check'
    prefix.mkdir()
    with native_nginx(prefix,lab.kcport,health_path='/realms/'+lab.identity) as (edge,rejected,bounds):
        path = '/realms/'+lab.identity+'/login-actions/required-action'
        statuses = [request(edge,path+'?execution=VERIFY_EMAIL',method='POST')[0] for _ in range(5)]
        assert statuses[-1] == 429 and all(s != 429 for s in statuses[:3]), 'NATIVE_RATE_BOUNDARY_FAILED'
        variants = ('%56ERIFY_EMAIL','VERIFY%5FEMAIL','UPDATE_PASSWORD','VERIFY_EMAIL&execution=ignored')
        bypass = [request(edge,path+'?execution='+v,method='POST',
            supplied_headers={'X-Forwarded-For':'192.0.2.1'})[0] for v in variants]
        assert bypass == [429]*len(variants), 'RATE_LIMIT_ENCODING_BYPASS'
        assert request(edge,path+'?execution=VERIFY_EMAIL',method='POST',source='127.0.0.2')[0] != 429
        lab.report['native_rate_observation'] = dict(statuses=statuses,encoded_statuses=bypass,
            upstream='NATIVE_KEYCLOAK_26.7.3',email_rate_per_minute=bounds['email'][0],email_burst=bounds['email'][1])
    lab.report['checks']['rate_limit_encoding_bypass_native'] = 'PASS'

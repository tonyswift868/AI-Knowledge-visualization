"""Live native Keycloak/Mailpit UAT. Credentials and action links never printed."""
import base64
import hashlib
import html
from html.parser import HTMLParser
import http.cookiejar
import json
import pathlib
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from admin import api, BASE, docker_inspect

PUBLIC='https://89.45.45.28'
REALM='realms/mentorflow'
PREFIX='e109-uat-'+str(int(time.time()))
EMAIL=PREFIX+'@e109-uat.invalid'
checks={}
created=[]
mail_ip=next(iter(docker_inspect('mentorflow-e109-mailpit')['NetworkSettings']['Networks'].values()))['IPAddress']
MAIL='http://'+mail_ip+':8025'
passwords=[secrets.token_urlsafe(24),secrets.token_urlsafe(48)]

class Page(HTMLParser):
    def __init__(self,body):
        super().__init__(); self.forms=[]; self.links={}; self.current=None; self.text=[]; self.ignored=0
        self.feed(body)
    def handle_starttag(self,tag,attributes):
        a=dict(attributes)
        if tag in ['script','style']: self.ignored+=1
        if tag=='form':
            self.current={'action':a.get('action',''),'id':a.get('id',''),'fields':{}}; self.forms.append(self.current)
        if tag=='input' and self.current is not None and a.get('name'):
            self.current['fields'][a['name']]=a.get('value','')
        if tag=='a' and a.get('href'):
            self.links[a.get('id','link'+str(len(self.links)))]=a['href']
    def handle_endtag(self,tag):
        if tag=='form': self.current=None
        if tag in ['script','style']: self.ignored=max(0,self.ignored-1)
    def handle_data(self,data):
        if not self.ignored: self.text.append(data)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args): return None

class Browser:
    def __init__(self):
        self.cookies=http.cookiejar.CookieJar()
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(self.cookies),NoRedirect())
        self.last=None
    def request(self,url,data=None,follow=True,headers=None):
        if url.startswith('/'):
            url=PUBLIC+url
        parsed=urllib.parse.urlsplit(url)
        if parsed.path=='/oauth/callback':
            self.last=(302,Page(''),url); return self.last
        assert parsed.path.startswith('/realms/mentorflow/') or parsed.path.startswith('/resources/')
        url=PUBLIC+urllib.parse.urlunsplit(('', '',parsed.path,parsed.query,''))
        h={'User-Agent':'MentorFlow-E109-security-UAT','Host':'localhost:18080'}
        if headers: h.update(headers)
        payload=None
        if data is not None:
            payload=urllib.parse.urlencode(data,doseq=True).encode(); h['Content-Type']='application/x-www-form-urlencoded'
        for attempt in range(5):
            try: r=self.opener.open(urllib.request.Request(url,data=payload,headers=h),timeout=25)
            except urllib.error.HTTPError as e: r=e
            status=r.code; body=r.read().decode(errors='replace'); target=r.headers.get('Location')
            if status!=429: break
            time.sleep(21)
        if follow and status in [301,302,303,307,308] and target:
            return self.request(urllib.parse.urljoin(url,target))
        self.last=(status,Page(body),target or url)
        return self.last
    def submit(self,values,form=None):
        assert self.last is not None
        page=self.last[1]
        selected=next((f for f in page.forms if f['id']==form),None) if form else next(iter(page.forms),None)
        assert selected, 'EXPECTED_FORM_MISSING'
        data=dict(selected['fields']); data.update(values)
        return self.request(selected['action'],data)

def mark(name,condition):
    if not condition: raise AssertionError(name)
    checks[name]='PASS'; print(name+'=PASS',flush=True)
    (BASE/'evidence/uat-progress.json').write_text(json.dumps(checks,indent=2))

def auth(browser,endpoint='auth',**extra):
    verifier=secrets.token_urlsafe(48)
    query={'client_id':'mentorflow-desktop','redirect_uri':'http://127.0.0.1/oauth/callback','response_type':'code','scope':'openid email profile','state':secrets.token_urlsafe(16),'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),'code_challenge_method':'S256'}
    query.update(extra)
    return browser.request('/realms/mentorflow/protocol/openid-connect/'+endpoint+'?'+urllib.parse.urlencode(query))

def user(email=EMAIL):
    users=api(REALM+'/users?email='+urllib.parse.quote(email)+'&exact=true')
    return next((u for u in users if u.get('email')==email),None)

def messages():
    return json.load(urllib.request.urlopen(MAIL+'/api/v1/messages',timeout=10)).get('messages',[])

def latest_link(email=EMAIL,exclude=None):
    for _ in range(20):
        for m in messages():
            if m['ID']==exclude: continue
            if email not in [a.get('Address') for a in m.get('To',[])]: continue
            msg=json.load(urllib.request.urlopen(MAIL+'/api/v1/message/'+m['ID'],timeout=10))
            text=msg.get('Text','')+' '+html.unescape(msg.get('HTML',''))
            match=re.search(r'https?://[^\s<>"\']+/login-actions/action-token\?[^\s<>"\']+',text)
            if match: return m['ID'],html.unescape(match.group(0))
        time.sleep(1)
    raise AssertionError('MAIL_DELIVERY_TIMEOUT')

def save_result(status,error=None):
    result={'status':status,'checks':checks,'test_user_ids':created,'error':error,'passwords_logged':False,'action_links_logged':False}
    (BASE/'evidence/uat-email.json').write_text(json.dumps(result,indent=2))

def run():
    api(REALM,'PUT',{'registrationAllowed':True})
    missing=Browser(); auth(missing,prompt='create')
    missing.submit({'username':PREFIX+'-no-email','email':''})
    missing_users=api(REALM+'/users?username='+PREFIX+'-no-email&exact=true')
    created.extend(item['id'] for item in missing_users)
    mark('EMAIL_REQUIRED_NO_BLANK_BYPASS',not missing_users)
    b=Browser(); status,page,_=auth(b,prompt='create')
    mark('REGISTRATION_EMAIL_BEFORE_PASSWORD_FORM',status==200 and any('email' in f['fields'] for f in page.forms) and not any('password' in f['fields'] for f in page.forms))
    status,page,location=b.submit({'username':EMAIL,'email':EMAIL,'firstName':'UAT','lastName':'Synthetic','realmRoles':'ADMIN','clientRoles':'realm-management','user.attributes.role':'superadmin','groups':'/administrators','emailVerified':'true','password':passwords[0],'password-confirm':passwords[0]})
    u=user(); assert u, 'TEST_USER_NOT_CREATED'; created.append(u['id'])
    mark('EMAIL_UNVERIFIED_CANNOT_USE_ACCOUNT',not u['emailVerified'] and 'VERIFY_EMAIL' in u.get('requiredActions',[]) and not api(REALM+'/users/'+u['id']+'/sessions') and not api(REALM+'/users/'+u['id']+'/credentials'))
    roles=api(REALM+'/users/'+u['id']+'/role-mappings/realm/composite')
    mark('DEFAULT_ROLE_NOT_ADMIN','LEARNER' in {r['name'] for r in roles} and not ({'OWNER','ADMIN','admin','superadmin'} & {r['name'] for r in roles}))
    mappings=api(REALM+'/users/'+u['id']+'/role-mappings')
    attrs=u.get('attributes',{})
    mark('ROLE_ESCALATION_PAYLOAD_REJECT',not any(k in attrs for k in ['role','realmRoles','clientRoles','groups']) and 'realm-management' not in mappings.get('clientMappings',{}))
    msg_id,link=latest_link()
    status,page,_=b.request(link)
    # Cross-browser verification may first ask for a confirmation click.
    if not any('password-new' in f['fields'] for f in page.forms):
        candidates=[v for v in page.links.values() if '/login-actions/' in v]
        if candidates: status,page,_=b.request(candidates[0])
    mark('EMAIL_VERIFIED_BEFORE_SET_PASSWORD',user()['emailVerified'] and any('password-new' in f['fields'] for f in page.forms))
    status,page,_=b.submit({'password-new':passwords[0],'password-confirm':passwords[0]})
    mark('VERIFIED_EMAIL_CAN_COMPLETE_REGISTRATION',not user().get('requiredActions') and len(api(REALM+'/users/'+u['id']+'/credentials'))==1)
    cred=api(REALM+'/users/'+u['id']+'/credentials')[0]
    mark('PASSWORD_HASH_ARGON2',json.loads(cred.get('credentialData','{}')).get('algorithm')=='argon2')
    # Independent login; this never follows the desktop callback into backend.
    login=Browser(); auth(login); status,page,where=login.submit({'username':EMAIL,'password':passwords[0]})
    mark('LOGIN_VERIFIED_USER',status==302 and '/oauth/callback' in where)
    forgot=Browser(); auth(forgot,'forgot-credentials'); status,page,_=forgot.submit({'username':EMAIL})
    known=' '.join(page.text)
    mark('FORGOT_PASSWORD_FLOW',status==200 and ('email' in known.lower() or '邮件' in known))
    reset_id,reset_link=latest_link(exclude=msg_id)
    unknown=Browser(); auth(unknown,'forgot-credentials'); us,up,_=unknown.submit({'username':PREFIX+'-absent@e109-uat.invalid'})
    generic=lambda p:' '.join(' '.join(p.text).split())
    if generic(up)!=generic(page):
        (BASE/'evidence/generic-response-diagnostic.json').write_text(json.dumps({'known_visible_text':generic(page),'unknown_visible_text':generic(up)},indent=2))
    mark('UNKNOWN_EMAIL_GENERIC_RESPONSE',us==status and generic(up)==generic(page))
    reset=Browser(); rs,rp,_=reset.request(reset_link)
    if not any('password-new' in f['fields'] for f in rp.forms):
        candidates=[v for v in rp.links.values() if '/login-actions/' in v]
        if candidates: rs,rp,_=reset.request(candidates[0])
    mark('RESET_EMAIL_LINK_OPENS_PASSWORD_FORM',any('password-new' in f['fields'] for f in rp.forms))
    reset.submit({'password-new':passwords[1],'password-confirm':passwords[1]})
    replay=Browser(); rs,rp,_=replay.request(reset_link)
    mark('RESET_TOKEN_SINGLE_USE',not any('password-new' in f['fields'] for f in rp.forms) and not (rs==302 and '/oauth/callback' in replay.last[2]))
    login2=Browser(); auth(login2); ls,lp,lw=login2.submit({'username':EMAIL,'password':passwords[1]})
    mark('RESET_PASSWORD_64_CHARACTERS_ACCEPTED',ls==302 and '/oauth/callback' in lw and len(passwords[1])>=64)
    # A short-lived test token is minted with native expiry, then policy restored.
    api(REALM,'PUT',{'actionTokenGeneratedByUserLifespan':2})
    try:
        expired=Browser(); auth(expired,'forgot-credentials'); expired.submit({'username':EMAIL})
        expiry_id,expiry_link=latest_link(exclude=reset_id)
    finally: api(REALM,'PUT',{'actionTokenGeneratedByUserLifespan':600})
    token=urllib.parse.parse_qs(urllib.parse.urlsplit(expiry_link).query)['key'][0]
    claims=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'==='))
    assert claims['exp']-claims['iat']<=5, 'SHORT_NATIVE_TOKEN_NOT_MINTED'
    time.sleep(max(0,claims['exp']-time.time()+2))
    expiry_browser=Browser(); es,ep,_=expiry_browser.request(expiry_link)
    mark('RESET_TOKEN_EXPIRES',not any('password-new' in f['fields'] for f in ep.forms) and ('expired' in ' '.join(ep.text).lower() or es in [400,403]))
    bound_email=PREFIX+'-bound@e109-uat.invalid'
    binding=Browser(); auth(binding,kc_action='UPDATE_EMAIL')
    if any('password' in f['fields'] for f in binding.last[1].forms):
        binding.submit({'username':EMAIL,'password':passwords[1]})
    assert any('email' in f['fields'] for f in binding.last[1].forms), 'EMAIL_BINDING_FORM_MISSING'
    binding.submit({'email':bound_email})
    mark('EMAIL_BINDING_REQUIRES_CONFIRMATION',api(REALM+'/users/'+u['id'])['email']==EMAIL)
    bind_id,bind_link=latest_link(email=bound_email)
    binding.request(bind_link)
    changed=api(REALM+'/users/'+u['id'])
    mark('EMAIL_BINDING_VERIFIED',changed['email']==bound_email and changed['emailVerified'])
    # Lockout tests touch only the just-created synthetic account.
    for attempt in range(6):
        failed=Browser(); auth(failed)
        failed.submit({'username':bound_email,'password':secrets.token_urlsafe(24)})
        time.sleep(1.2)
        lock=api(REALM+'/attack-detection/brute-force/users/'+u['id'])
        if lock.get('disabled'): break
    mark('BRUTE_FORCE_LOCKOUT',lock.get('disabled') and lock.get('numFailures')>=5)
    locked=Browser(); auth(locked)
    ls,lp,lw=locked.submit({'username':bound_email,'password':passwords[1]})
    mark('CORRECT_PASSWORD_DENIED_DURING_LOCKOUT',not (ls==302 and '/oauth/callback' in lw))
    absent=Browser(); auth(absent)
    us,up,_=absent.submit({'username':PREFIX+'-absent','password':secrets.token_urlsafe(24)})
    visible=lambda p:' '.join(' '.join(p.text).split())
    mark('LOGIN_UNKNOWN_AND_LOCKED_GENERIC',us==ls and visible(up)==visible(lp))
    for name,value in [('PASSWORD_UNDER_15_REJECTED',secrets.token_urlsafe(6)),('COMMON_PASSWORD_REJECTED','password'*2)]:
        try: api(REALM+'/users/'+u['id']+'/reset-password','PUT',{'type':'password','value':value,'temporary':False})
        except RuntimeError as e: mark(name,'HTTP_400' in str(e))
        else: raise AssertionError(name)
    letters=''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(64))
    api(REALM+'/users/'+u['id']+'/reset-password','PUT',{'type':'password','value':letters,'temporary':False})
    mark('PASSWORD_64_NO_COMPOSITION_REQUIRED',True)
    source=docker_inspect('mentorflow-e1-preview-keycloak-1')
    secret_path=pathlib.Path(next(m['Source'] for m in source['Mounts'] if m['Destination']=='/run/secrets'))
    secret_values=[(BASE/'secrets/SMTP_SECRET').read_bytes().strip()]
    secret_values.extend((secret_path/n).read_bytes().strip() for n in ['keycloak_admin_password','keycloak_db_password','bff_client_secret','worker_client_secret'] if (secret_path/n).exists())
    log_data=b''
    for name in ['mentorflow-e1-preview-keycloak-1','mentorflow-e1-preview-edge-1','mentorflow-e109-mailpit']:
        output=subprocess.run(['docker','logs','--since','30m',name],capture_output=True)
        log_data+=output.stdout+output.stderr
    mark('SMTP_SECRET_NOT_LOGGED',not any(v and v in log_data for v in secret_values))
    save_result('PASS')

if __name__=='__main__':
    edge=docker_inspect('mentorflow-e1-preview-edge-1')
    edge_path=pathlib.Path(next(m['Source'] for m in edge['Mounts'] if m['Destination']=='/etc/nginx/nginx.conf'))
    assert 'if ($e109_registration_denied) { return 403; }' in edge_path.read_text(), 'PUBLIC_UAT_GATE_REQUIRED'
    assert 'mentorflow-e109-mailpit' == api(REALM).get('smtpServer',{}).get('host'), 'UAT_MAILPIT_ONLY'
    try: run()
    except BaseException as e:
        save_result('FAIL',str(e) if isinstance(e,AssertionError) else type(e).__name__)
        print('UAT_FAILED '+(str(e) if isinstance(e,AssertionError) else type(e).__name__),flush=True)
        raise SystemExit(1) from None
    finally:
        api(REALM,'PUT',{'registrationAllowed':False,'actionTokenGeneratedByUserLifespan':600})
        for uid in created:
            synthetic=api(REALM+'/users/'+uid)
            assert synthetic.get('username','').startswith(PREFIX)
            assert synthetic.get('email','').endswith('@e109-uat.invalid') or (synthetic.get('username')==PREFIX+'-no-email' and not synthetic.get('email'))
            api(REALM+'/users/'+uid,'DELETE')

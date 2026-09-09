"""External Windows HTTPS checks; no credentials embedded or printed."""
import concurrent.futures
import json
import pathlib
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

BASE='https://89.45.45.28'
OUT=pathlib.Path(__file__).parent
marker='e109-proxy-'+str(int(time.time()))
context=ssl.create_default_context()

def request(path,body=None,forged=None):
    headers={'User-Agent':'MentorFlow-E109-Windows-security-UAT'}
    if forged:
        headers.update({'X-Forwarded-For':forged,'Forwarded':'for='+forged+';host=evil.invalid;proto=http','X-Real-IP':forged,'X-Forwarded-Host':'evil.invalid','X-Forwarded-Port':'80','X-Forwarded-Proto':'http','X-Forwarded-Prefix':'/evil'})
    if body is not None:
        body=urllib.parse.urlencode(body).encode(); headers['Content-Type']='application/x-www-form-urlencoded'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
    try: r=opener.open(urllib.request.Request(BASE+path,data=body,headers=headers),timeout=20)
    except urllib.error.HTTPError as e: r=e
    return r.status,r.read(),dict(r.headers)

checks={}
status,body,_=request('/realms/mentorflow/.well-known/openid-configuration',forged='203.0.113.88')
discovery=json.loads(body)
assert status==200 and discovery['issuer']=='https://localhost:18080/realms/mentorflow'
checks['OIDC_DISCOVERY_AND_FORGED_HOST_REJECT']='PASS'
for suffix,ip in [('a','203.0.113.88'),('b','198.51.100.33')]:
    status,body,_=request('/realms/mentorflow/protocol/openid-connect/token',{'grant_type':'password','client_id':'admin-cli','username':marker+'-'+suffix,'password':secrets.token_urlsafe(24)},ip)
    assert status in [400,401] and json.loads(body).get('error')=='invalid_grant', (status,json.loads(body).get('error'))
checks['FORGED_IP_REQUESTS_SUBMITTED']='PASS'
def burst(i):
    status,body,_=request('/realms/mentorflow/protocol/openid-connect/token',{'grant_type':'authorization_code','client_id':'mentorflow-desktop','code':'invalid'},'203.0.113.'+str(i+1))
    return status
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    statuses=list(pool.map(burst,range(40)))
assert 429 in statuses and any(s!=429 for s in statuses)
checks['IP_RATE_LIMIT_WITH_ROTATING_FORGED_XFF']='PASS'
for label,path,count in [('LOGIN','/realms/mentorflow/login-actions/authenticate',20),('RESET','/realms/mentorflow/login-actions/reset-credentials',10)]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        values=list(pool.map(lambda i:request(path,{'username':marker+'-absent'},'198.51.100.'+str(i+1))[0],range(count)))
    assert 429 in values and any(s!=429 for s in values)
    checks[label+'_POST_RATE_LIMIT']='PASS'
assert request('/realms/mentorflow/login-actions/required-action?execution=VERIFY_EMAIL',{},'127.0.0.1')[0]==429
checks['VERIFY_EMAIL_RESEND_RATE_LIMIT']='PASS'
statuses_get=[request('/realms/mentorflow/.well-known/openid-configuration')[0] for _ in range(8)]
assert set(statuses_get)=={200}
checks['ORDINARY_GET_NOT_RATE_LIMITED']='PASS'
auth_query=urllib.parse.urlencode({'client_id':'mentorflow-desktop','redirect_uri':'http://127.0.0.1/oauth/callback','response_type':'code','scope':'openid','code_challenge':'A'*43,'code_challenge_method':'S256'})
auth_status,auth_body,_=request('/realms/mentorflow/protocol/openid-connect/auth?'+auth_query)
assert auth_status==200
checks['LOGIN_PAGE_GET_NOT_RATE_LIMITED']='PASS'
import re
css=re.search(r'href="([^\"]+/resources/[^\"]+\.css[^\"]*)"',auth_body.decode(errors='replace'))
if not css: css=re.search(r'href="(/resources/[^\"]+\.css[^\"]*)"',auth_body.decode(errors='replace'))
assert css, 'LOGIN_CSS_LINK_MISSING'
css_url=urllib.parse.urlsplit(css.group(1))
assert request(css_url.path+('?' + css_url.query if css_url.query else ''))[0]==200
checks['LOGIN_CSS_NOT_RATE_LIMITED']='PASS'
for path in ['/realms/mentorflow/protocol/openid-connect/registrations?client_id=mentorflow-desktop','/realms/mentorflow/protocol/openid-connect/auth?prompt=create&client_id=mentorflow-desktop','/realms/mentorflow/login-actions/registration']:
    assert request(path,forged='127.0.0.1')[0]==403
checks['PUBLIC_REGISTRATION_OFF_SPOOF_CANNOT_BYPASS']='PASS'
assert request('/mentorflow/updates/e108/endpoint-check.txt')[0]==200
checks['E108_UPDATE_ENDPOINT_PRESERVED']='PASS'
result={'status':'PASS','marker':marker,'checks':checks,'token_burst_status_counts':{str(s):statuses.count(s) for s in set(statuses)},'tls_validation':'system default, no bypass'}
(OUT/'windows-proxy-uat.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))

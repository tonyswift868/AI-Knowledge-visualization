"""Real local Keycloak 26.7.3/mTLS email binding; in-memory loopback SMTP only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import secrets
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend'),str(ROOT/'backend/tests')]
from scripts.email_code_reset_fixture import run


def configure(payload):
    # Existing native sessions may have no bound email; login policy is unchanged
    # in production. Fixture disables mandatory verification, not TLS or identity.
    payload['verifyEmail']=False
    # Model a realm which permits an authenticated account without email. The
    # default UPDATE_PROFILE trigger otherwise requires email before login;
    # product native verifier policy is not bypassed or changed for this test.
    payload['requiredActions']=[{'alias':'UPDATE_PROFILE','providerId':'UPDATE_PROFILE',
        'name':'Update Profile','enabled':False,'defaultAction':False,'priority':40}]
    for u in payload['users'][:6]:u['emailVerified']=False
    payload['users'][0].pop('email')


def scenario(c,identity,issuer,users,password,smtp,runtime):
    checks={};prefix='/api/enterprise/auth/lifecycle'
    endpoint=issuer+'/mentorflow-native-identity/v1'
    def private(operation,user=users[0],**fields):
        return identity.post(endpoint,json={'operation':operation,'subject':user['id'],**fields}).json()
    def control(operation,user=users[0],**fields):
        r=identity.post(issuer+'/mf-native-fixture-control/action',json={'operation':operation,
            'username':user['username'],'purpose':'reset',**fields})
        assert r.status_code==200,'fixture control failed'
        return r.json()
    def login(user):
        r=c.post('/api/enterprise/auth/native/login',json={'username':user['username'],'password':password})
        assert r.status_code==200,('native login failed',r.status_code,r.json().get('code'))
        return {'Authorization':'Bearer '+r.json()['access_token'],
            'X-MentorFlow-Organization-ID':'existing-org',
            'X-MentorFlow-Workspace-ID':'existing-workspace'},r.json()['refresh_token']
    def send(user,target=None,headers=None):
        from app.enterprise.identity import reset_completion
        with reset_completion._lock:reset_completion._rates.clear()
        count=len(smtp.messages);ticket=secrets.token_urlsafe(32)
        target=target or user['email']
        if headers:
            r=c.post(prefix+'/email-code/request',headers=headers,json={'email':target})
            assert r.status_code==202,('authenticated code request failed',r.status_code,r.json().get('code'))
            ticket=r.json()['transaction_id']
        else:
            assert private('email-binding-request',user,transaction_id=ticket,email=target)=={'status':'SENT'}
        assert len(smtp.messages)==count+1,'local SMTP not received'
        recipient,code=smtp.messages[-1];assert target in recipient
        return ticket,code
    def verify(user,ticket,code):return private('email-binding-verify',user,transaction_id=ticket,code=code)
    h,refresh=login(users[0])
    me=c.get('/api/enterprise/auth/me',headers=h);assert me.status_code==200
    before=me.json()
    profile=c.get(prefix+'/email',headers=h)
    checks['authenticated_empty_email']=profile.status_code==200 and profile.json()=={
        'email':None,'email_verified':False,'can_bind':True,'can_verify':False}
    # Current-password validation needs no email. Read-only fixture admin resolves
    # the exact subject; the real verifier still validates the password over mTLS.
    class Admin:
        async def username_for_subject(self,subject):
            assert subject==users[0]['id'];return users[0]['username']
        async def change_password(self,*args):raise AssertionError('same password must not mutate')
    runtime.lifecycle.admin=Admin()
    same=c.post(prefix+'/change-password',headers=h,json={'current_password':password,'new_password':password})
    checks['current_password_without_email']=same.status_code==400 and same.json()['code']=='NEW_PASSWORD_SAME_AS_CURRENT'
    ticket,code=send(users[0],'initial@fixture.invalid',h)
    checks['wrong_subject']=verify(users[1],ticket,code)=={'status':'INVALID'}
    checks['binding_not_reset']=identity.post(endpoint,json={'operation':'email-code-verify','transaction_id':ticket,'code':code}).json()=={'status':'INVALID'}
    done=c.post(prefix+'/email-code/verify',headers=h,json={'transaction_id':ticket,'code':code})
    checks['initial_binding_committed']=done.status_code==200 and done.json()['email']=='initial@fixture.invalid' and done.json()['email_verified']
    checks['canonical_identity_unchanged']=c.get('/api/enterprise/auth/me',headers=h).json()==before
    checks['replay_rejected']=verify(users[0],ticket,code)['status'] in ('INVALID','EMAIL_ALREADY_VERIFIED')
    checks['bound_email_cannot_overwrite']=private('email-binding-request',transaction_id=secrets.token_urlsafe(32),email='other@fixture.invalid')=={'status':'EMAIL_ALREADY_BOUND'}
    ticket,code=send(users[1])
    checks['existing_unverified_profile']=private('email-binding-profile',users[1])['email_verified']=='false'
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:verify(users[1],ticket,code)['status'],range(2)))
    checks['concurrent_single_completion']=results.count('PENDING_COMMIT')==1 and all(r in ('PENDING_COMMIT','INVALID','EMAIL_ALREADY_VERIFIED') for r in results)
    checks['existing_email_verified']=private('email-binding-status',users[1],transaction_id=ticket)['email_verified']=='true'
    ticket,code=send(users[2]);wrong='000000' if code!='000000' else '999999'
    for _ in range(5):assert verify(users[2],ticket,wrong)=={'status':'INVALID'}
    checks['attempt_limit']=verify(users[2],ticket,code)=={'status':'INVALID'}
    ticket,code=send(users[3]);control('clock-offset',seconds='901')
    try:checks['expired_code']=verify(users[3],ticket,code)=={'status':'INVALID'}
    finally:control('clock-offset',seconds='0')
    ticket,code=send(users[4]);control('rollback',users[4]);verify(users[4],ticket,code)
    checks['rollback_no_completion']=private('email-binding-status',users[4],transaction_id=ticket)=={'status':'INVALID'} and private('email-binding-profile',users[4])['email_verified']=='false'
    ticket,code=send(users[5]);control('pause-commit',users[5])
    with ThreadPoolExecutor(2) as pool:
        pending=pool.submit(verify,users[5],ticket,code)
        # Wait for fixture observer to reach its commit gate, without reading secrets.
        until=time.monotonic()+10
        while time.monotonic()<until:
            state=control('state',users[5])
            if any(e.get('type')=='VERIFY_EMAIL' and e.get('phase')=='EVENT_BEFORE_COMMIT' for e in state['observations']):break
            time.sleep(.05)
        checks['no_completion_before_commit']=private('email-binding-status',users[5],transaction_id=ticket)=={'status':'INVALID'}
        control('release-commit',users[5]);assert pending.result()=={'status':'PENDING_COMMIT'}
    checks['completion_after_commit']=private('email-binding-status',users[5],transaction_id=ticket)['email_verified']=='true'
    ticket=secrets.token_urlsafe(32)
    sent=identity.post(endpoint,json={'operation':'email-code-request','transaction_id':ticket,'email':users[6]['email']}).json()
    assert sent.get('delivery')=='SUBMITTED';code=smtp.messages[-1][1]
    checks['reset_not_binding']=verify(users[6],ticket,code)=={'status':'INVALID'}
    # Log out our controlled verification session; no GUI or real data involved.
    checks['no_auto_login_on_verification']='access_token' not in done.json()
    checks['logout']=c.post('/api/enterprise/auth/native/logout',json={'refresh_token':refresh}).status_code==200
    return checks


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--keycloak-zip',type=Path,required=True)
    p.add_argument('--jdk',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.configure=configure;args.scenario=scenario;run(args)

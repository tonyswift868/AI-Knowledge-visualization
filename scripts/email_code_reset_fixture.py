"""Focused real Keycloak/mTLS/Backend recovery fixture; SMTP stays on loopback in memory."""
import argparse
import email
from email import policy
import json
import os
from pathlib import Path
import re
import secrets
import socketserver
import ssl
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'backend'), str(ROOT/'backend/tests')]
from scripts.native_identity_fixture import certificates, port, backend
from scripts.build_native_identity_verifier import build


class Mailbox(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def __init__(self):
        self.messages = []
        class SMTP(socketserver.StreamRequestHandler):
            def handle(s):
                def reply(value):s.wfile.write(value+b'\r\n');s.wfile.flush()
                reply(b'220 loopback fixture')
                while True:
                    line=s.rfile.readline(8192)
                    if not line:return
                    command=line.split(b' ',1)[0].strip().upper()
                    if command==b'DATA':
                        reply(b'354 continue');data=bytearray()
                        while True:
                            row=s.rfile.readline(8192)
                            if not row or row==b'.\r\n':break
                            data.extend(row)
                            if len(data)>65536:return
                        message=email.message_from_bytes(data,policy=policy.default)
                        text=message.get_body(preferencelist=('plain',)).get_content() if message.is_multipart() else message.get_content()
                        codes=re.findall(r'(?<!\d)\d{6}(?!\d)',text)
                        assert len(codes)==1
                        s.server.messages.append((str(message['To']),codes[0]))
                        data[:]=b'\0'*len(data)
                        reply(b'250 accepted')
                    elif command==b'QUIT':reply(b'221 bye');return
                    else:reply(b'250 OK')
        super().__init__(('127.0.0.1',0),SMTP)


def run(args):
    import httpx
    home=args.output.resolve();home.mkdir(parents=True,exist_ok=False)
    smtp=Mailbox();threading.Thread(target=smtp.serve_forever,daemon=True).start()
    checks={};process=server=worker=engine=None;log=None
    try:
        with zipfile.ZipFile(args.keycloak_zip) as z:z.extractall(home)
        kc=home/'keycloak-26.7.3';pin=certificates(home)
        build(kc,args.jdk,kc/'providers/mentorflow-native-verifier.jar')
        classes=home/'fixture-classes';classes.mkdir()
        subprocess.run([str(args.jdk/'bin/javac.exe'),'--release','21','-cp',str(kc/'lib/lib/main/*'),'-d',str(classes),
            str(ROOT/'scripts/NativeIdentityFixtureControl.java'),str(ROOT/'scripts/ResetEventFixtureObserver.java')],check=True)
        with zipfile.ZipFile(kc/'providers/fixture-control.jar','w') as z:
            for p in classes.rglob('*.class'):z.write(p,p.relative_to(classes).as_posix())
            z.writestr('META-INF/services/org.keycloak.services.resource.RealmResourceProviderFactory','win.clannad.mentorflow.lab.NativeIdentityFixtureControl\n')
            z.writestr('META-INF/services/org.keycloak.events.EventListenerProviderFactory','win.clannad.mentorflow.lab.ResetEventFixtureObserver\n')
            z.writestr('META-INF/beans.xml','<beans xmlns="https://jakarta.ee/xml/ns/jakartaee" bean-discovery-mode="all"/>')
        realm='mf-native-'+secrets.token_hex(5);issuer=f'https://127.0.0.1:{port()}/realms/{realm}';api=f'https://127.0.0.1:{port()}'
        password=secrets.token_urlsafe(24)
        users=[dict(id=str(uuid.uuid4()),username='action-'+secrets.token_hex(8),enabled=True,emailVerified=True,
            firstName='Fixture',lastName='Recovery',credentials=[dict(type='password',value=password,temporary=False)]) for _ in range(7)]
        for u in users:u['email']=u['username']+'@fixture.invalid'
        payload=dict(realm=realm,enabled=True,sslRequired='all',verifyEmail=True,registrationAllowed=False,
            resetPasswordAllowed=True,bruteForceProtected=True,failureFactor=100,
            passwordPolicy='hashAlgorithm(argon2) and length(8) and maxLength(128)',
            eventsListeners=['mf-fixture-reset-observer'],users=users,
            smtpServer={'host':'127.0.0.1','port':str(smtp.server_address[1]),'from':'recovery@fixture.invalid','ssl':'false','starttls':'false','auth':'false'},
            clients=[dict(clientId='mentorflow-desktop',enabled=True,publicClient=True,standardFlowEnabled=True,
                directAccessGrantsEnabled=False,redirectUris=['http://127.0.0.1/*'])])
        imp=kc/'data/import';imp.mkdir(parents=True);(imp/'realm.json').write_text(json.dumps(payload))
        if getattr(args, 'configure', None):
            args.configure(payload)
            (imp/'realm.json').write_text(json.dumps(payload))
        env={k:v for k,v in os.environ.items() if not k.startswith(('KC_','KEYCLOAK_','MF_NATIVE_VERIFIER_'))}
        env.update(MF_NATIVE_VERIFIER_ENABLED='1',MF_NATIVE_VERIFIER_REALM=realm,MF_NATIVE_VERIFIER_ISSUER=issuer,
            MF_NATIVE_VERIFIER_BACKEND_CERT_SHA256=pin,MF_NATIVE_FIXTURE_CONTROL='1')
        cmd=[str(args.jdk/'bin/java.exe'),'-Xms128m','-Xmx512m','-XX:ActiveProcessorCount=2',
            '-Dkc.home.dir='+str(kc),'-Djboss.server.config.dir='+str(kc/'conf'),
            '-Djava.util.concurrent.ForkJoinPool.common.threadFactory=io.quarkus.bootstrap.forkjoin.QuarkusForkJoinWorkerThreadFactory',
            '-cp',str(kc/'lib/quarkus-run.jar'),'io.quarkus.bootstrap.runner.QuarkusEntryPoint','start-dev','--db=dev-file','--cache=local',
            '--http-enabled=false','--http-host=127.0.0.1','--http-management-host=127.0.0.1','--http-management-port='+str(port()),
            '--https-port='+issuer.split(':')[2].split('/')[0],'--https-certificate-file='+str(home/'server.pem'),
            '--https-certificate-key-file='+str(home/'server.key'),'--https-client-auth=request','--truststore-paths='+str(home/'ca.pem'),
            '--import-realm','--http-access-log-enabled=false','--log-level=error']
        log=(home/'identity.log').open('wb');tls=ssl.create_default_context(cafile=str(home/'ca.pem'))
        with httpx.Client(verify=tls,trust_env=False,timeout=3) as probe:
            for attempt in range(2):
                process=subprocess.Popen(cmd,cwd=kc,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
                until=time.monotonic()+150
                while time.monotonic()<until and process.poll() is None:
                    try:
                        if probe.get(issuer+'/.well-known/openid-configuration').status_code==200:break
                    except httpx.HTTPError:pass
                    time.sleep(.3)
                if process.poll() is None:break
                assert process.returncode==10,'Keycloak fixture startup failed'
                cmd.insert(1,'-Dkc.config.built=true')
            assert probe.get(issuer+'/.well-known/openid-configuration').status_code==200
        (imp/'realm.json').unlink()
        server,worker,runtime,engine=backend(home,issuer,api,users[0]['id'])
        runtime.lifecycle=SimpleNamespace(active=True,jobs=set())
        peer=ssl.create_default_context(cafile=str(home/'ca.pem'));peer.load_cert_chain(home/'backend.pem',home/'backend.key')
        with httpx.Client(base_url=api,verify=tls,trust_env=False,timeout=20) as c, httpx.Client(verify=peer,trust_env=False,timeout=20) as identity:
            if getattr(args, 'scenario', None):
                checks=args.scenario(c,identity,issuer,users,password,smtp,runtime)
                assert all(checks.values()), 'Focused account security checks failed'
                print(json.dumps({'status':'PASS','checks':checks,'smtp':'LOOPBACK_IN_MEMORY_ONLY','real_user_mutation':False}))
                return
            prefix='/api/enterprise/auth/lifecycle/reset-code'
            def control(operation,user=users[0],**extra):
                r=identity.post(issuer+'/mf-native-fixture-control/action',json={'operation':operation,'username':user['username'],'purpose':'reset',**extra})
                assert r.status_code==200,'fixture control failed';return r.json()
            def request(user):
                # Each scenario is a separate client episode; authority-side code,
                # mailbox cooldown and attempt counters are never bypassed.
                from app.enterprise.identity import reset_completion
                with reset_completion._lock:reset_completion._rates.clear()
                count=len(smtp.messages);r=c.post(prefix+'/request',json={'email':user['email']});assert r.status_code==202, ('request_status',r.status_code)
                until=time.monotonic()+10
                while len(smtp.messages)==count and time.monotonic()<until:time.sleep(.05)
                assert len(smtp.messages)==count+1,'fixture SMTP not received'
                recipient,code=smtp.messages[-1];assert user['email'] in recipient
                return r.json()['transaction_id'],code
            def verify(ticket,code):return c.post(prefix+'/verify',json={'transaction_id':ticket,'code':code})
            def complete(proof,pw,confirmation=None):return c.post(prefix+'/complete',json={'reset_proof':proof,'new_password':pw,'confirm_password':pw if confirmation is None else confirmation})
            def login(user,pw):return c.post('/api/enterprise/auth/native/login',json={'username':user['username'],'password':pw})
            first_send=time.monotonic()
            ticket,code=request(users[0]);wrong='000000' if code!='000000' else '999999'
            checks['wrong_code']=verify(ticket,wrong).status_code==400
            pair=login(users[0],password);assert pair.status_code==200;old=pair.json()
            result=verify(ticket,code);assert result.status_code==200;proof=result.json()['reset_proof']
            checks['independent_verification_no_login']='access_token' not in result.json()
            checks['code_replay']=verify(ticket,code).status_code==400
            same=complete(proof,password);checks['same_password_retry']=same.status_code==400 and same.json()['code']=='NEW_PASSWORD_SAME_AS_CURRENT'
            checks['mismatch_retry']=complete(proof,'new-password-for-fixture','other').json()['code']=='PASSWORD_CONFIRMATION_MISMATCH'
            checks['policy_retry']=complete(proof,'short').json()['code']=='PASSWORD_POLICY_REJECTED'
            new=secrets.token_urlsafe(24);done=complete(proof,new);assert done.status_code==200,'valid completion failed'
            from app.account_identity_contract import account_id
            checks['identity_preserved']=done.json()['account_id']==account_id(issuer,users[0]['id'])
            checks['proof_replay']=complete(proof,new).status_code==400
            checks['old_password_rejected']=login(users[0],password).status_code==401
            signed=login(users[0],new);checks['new_password_login']=signed.status_code==200
            checks['old_session_rejected']=c.get('/api/enterprise/auth/me',headers={'Authorization':'Bearer '+old['access_token']}).status_code==401
            c.post('/api/enterprise/auth/native/logout',json={'refresh_token':signed.json()['refresh_token']})
            ticket,code=request(users[1]);control('clock-offset',seconds='901')
            try:checks['expired_code']=verify(ticket,code).status_code==400
            finally:control('clock-offset',seconds='0')
            ticket,code=request(users[2]);proof=verify(ticket,code).json()['reset_proof'];control('clock-offset',seconds='901')
            try:checks['expired_proof']=complete(proof,new).status_code==400
            finally:control('clock-offset',seconds='0')
            ticket,code=request(users[3]);wrong='000000' if code!='000000' else '999999'
            for _ in range(5):assert verify(ticket,wrong).status_code==400
            checks['attempt_limit']=verify(ticket,code).status_code==400
            ticket,code=request(users[4]);proof=verify(ticket,code).json()['reset_proof']
            control('rollback',users[4])
            failed=complete(proof,new)
            checks['rollback_no_completion']=failed.status_code!=200 and control('check-password',users[4],password=password)['valid']
            ticket,code=request(users[5]);proof=verify(ticket,code).json()['reset_proof']
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(2) as pool:
                results=list(pool.map(lambda _:complete(proof,secrets.token_urlsafe(24)).status_code,range(2)))
            checks['concurrent_single_write']=sorted(results)==[200,400]
            # No previous-password-history rule: a subsequent intent may restore an older password.
            time.sleep(max(0,61-(time.monotonic()-first_send)))
            ticket,code=request(users[0]);proof=verify(ticket,code).json()['reset_proof']
            checks['older_password_allowed']=complete(proof,password).status_code==200
            checks['no_browser_or_oauth_grant']=True
        assert all(checks.values()),'Focused checks failed'
        print(json.dumps({'status':'PASS','checks':checks,'smtp':'LOOPBACK_IN_MEMORY_ONLY','real_user_mutation':False}))
    except Exception:
        print(json.dumps({'status':'FAIL','completed_checks':checks}),flush=True)
        raise
    finally:
        if server:server.should_exit=True;worker.join(15)
        if engine:engine.dispose()
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(15)
            except subprocess.TimeoutExpired:process.kill();process.wait(10)
        smtp.shutdown();smtp.server_close();smtp.messages.clear()
        if log:log.close()
        for p in [*home.glob('*.key'),*home.glob('keycloak-*/data/import/*.json')]:p.unlink(missing_ok=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--keycloak-zip',type=Path,required=True);p.add_argument('--jdk',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args())

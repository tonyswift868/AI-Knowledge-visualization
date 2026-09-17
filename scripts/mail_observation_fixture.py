"""Read-only SMTP observation: local Keycloak + mTLS + STARTTLS/AUTH/NOOP.

No login, email send, GUI, real account or deployed configuration is accessed.
"""
import argparse
import base64
from pathlib import Path
import socketserver
import ssl
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend'),str(ROOT/'backend/tests')]
from scripts.email_code_reset_fixture import run


class Relay(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True
    def __init__(self,home):
        self.commands=[];self.messages=[];self.mode='normal'
        class Handler(socketserver.BaseRequestHandler):
            def handle(s):
                connection=s.request;connection.settimeout(4);stream=None
                if self.mode=='drop':return
                if self.mode=='stall':time.sleep(2);return
                def reply(value):connection.sendall(value+b'\r\n')
                try:
                    reply(b'220 fixture');stream=connection.makefile('rb');tls=False
                    while True:
                        line=stream.readline(4097)
                        if not line or len(line)>4096:return
                        command=line.split(b' ',1)[0].strip().upper()
                        self.commands.append(command.decode('ascii'))
                        if command==b'EHLO':
                            reply(b'250-fixture')
                            if tls and self.mode!='no-auth':reply(b'250-AUTH PLAIN LOGIN')
                            if not tls and self.mode!='no-starttls':reply(b'250-STARTTLS')
                            reply(b'250 OK')
                        elif command==b'STARTTLS':
                            reply(b'220 upgrade');stream.close()
                            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                            context.load_cert_chain(home/'server.pem',home/'server.key')
                            connection=context.wrap_socket(connection,server_side=True);stream=connection.makefile('rb');tls=True
                        elif command==b'AUTH':
                            assert tls,'AUTH without TLS'
                            parts=line.strip().split(b' ')
                            if parts[1]==b'PLAIN':
                                if len(parts)<3:reply(b'334 ');encoded=stream.readline(4097).strip()
                                else:encoded=parts[2]
                                credentials=base64.b64decode(encoded).split(b'\0')[-2:]
                            else:
                                if len(parts)>2:user=base64.b64decode(parts[2])
                                else:
                                    reply(b'334 VXNlcm5hbWU6');user=base64.b64decode(stream.readline(4097).strip())
                                reply(b'334 UGFzc3dvcmQ6');password=base64.b64decode(stream.readline(4097).strip())
                                credentials=[user,password]
                            valid=credentials==[b'fixture',b'synthetic-relay-only'] and self.mode!='auth-fail'
                            credentials.clear();line=b''
                            reply(b'235 authenticated' if valid else b'535 rejected')
                        elif command==b'NOOP':reply(b'250 OK' if self.mode!='noop-fail' else b'451 unavailable')
                        elif command==b'QUIT':reply(b'221 bye');return
                        else:
                            self.messages.append('FORBIDDEN_COMMAND');reply(b'550 forbidden');return
                except (ConnectionError,TimeoutError,ssl.SSLError):pass
                finally:
                    if stream:stream.close()
                    connection.close()
        super().__init__(('127.0.0.1',0),Handler)


def configure(payload):
    payload['users']=[]  # No identity account is needed for this read-only operation.
    payload['smtpServer'].update(starttls='true',auth='true',user='fixture',password='synthetic-relay-only')


def scenario(c,identity,issuer,users,password,smtp,runtime):
    import httpx
    checks={};endpoint=issuer+'/mentorflow-native-identity/v1'
    def probe(**extra):return identity.post(endpoint,json={'operation':'email-transport-health',**extra})
    def mode(value):
        assert identity.post(issuer+'/mf-native-fixture-control/mail-health',json={'mode':value}).status_code==200
    result=probe();checks['realm_sender_connection']=result.status_code==200 and result.json()=={'status':'READY','code':'EMAIL_TLS_AUTH_READY'}
    assert checks['realm_sender_connection'], (result.status_code,result.json().get('code'),smtp.commands)
    checks['tls_auth_noop']=all(cmd in smtp.commands for cmd in ('STARTTLS','AUTH','NOOP'))
    checks['bounded_response']=set(result.json())=={'status','code'}
    count=len(smtp.commands)
    checks['no_destination_override']=probe(host='outside.invalid').status_code==400 and len(smtp.commands)==count
    checks['wrong_realm']=identity.post(issuer.split('/realms/')[0]+'/realms/master/mentorflow-native-identity/v1',json={'operation':'email-transport-health'}).status_code==404
    home=scenario.home
    for label in ('none','other'):
        context=ssl.create_default_context(cafile=str(home/'ca.pem'))
        if label=='other':context.load_cert_chain(home/'other.pem',home/'other.key')
        with httpx.Client(verify=context,trust_env=False) as untrusted:
            checks['mtls_'+label+'_rejected']=untrusted.post(endpoint,json={'operation':'email-transport-health'}).status_code==404
    checks['denied_requests_no_smtp']=len(smtp.commands)==count
    observer=runtime.native_sessions.verifier
    checks['backend_bridge_ready']=observer.email_transport_health()==(True,'EMAIL_TLS_AUTH_READY')
    count=len(smtp.commands)
    checks['backend_cache']=observer.email_transport_health()==(True,'EMAIL_TLS_AUTH_READY') and len(smtp.commands)==count
    for failure in ('auth-fail','noop-fail','no-auth','no-starttls','drop','stall'):
        smtp.mode=failure;started=time.monotonic();result=probe()
        checks[failure]=result.json()=={'status':'UNAVAILABLE','code':'EMAIL_TRANSPORT_UNAVAILABLE'}
        if failure=='stall':checks['socket_timeout']=time.monotonic()-started<3
    observer._email_deadline=0
    checks['backend_failure_refresh']=observer.email_transport_health()==(False,'EMAIL_TRANSPORT_UNAVAILABLE')
    smtp.mode='normal';mode('insecure');count=len(smtp.commands)
    checks['insecure_config_rejected']=probe().json()=={'status':'UNAVAILABLE','code':'EMAIL_CONFIGURATION_UNAVAILABLE'} and len(smtp.commands)==count
    mode('normal');mode('wrong-hostname')
    tls_count=smtp.commands.count('STARTTLS');auth_count=smtp.commands.count('AUTH')
    checks['hostname_verification']=(probe().json()=={'status':'UNAVAILABLE','code':'EMAIL_TRANSPORT_UNAVAILABLE'}
        and smtp.commands.count('STARTTLS')==tls_count+1 and smtp.commands.count('AUTH')==auth_count)
    mode('normal');checks['recovered']=probe().json()=={'status':'READY','code':'EMAIL_TLS_AUTH_READY'}
    checks['no_send']=not smtp.messages and set(smtp.commands)<={'EHLO','STARTTLS','AUTH','NOOP','QUIT'}
    return checks


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--keycloak-zip',type=Path,required=True)
    parser.add_argument('--jdk',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.configure=configure;args.scenario=scenario;args.mailbox_factory=Relay
    scenario.home=args.output.resolve();run(args)

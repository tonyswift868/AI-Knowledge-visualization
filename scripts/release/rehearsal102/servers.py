from pathlib import Path
import ssl,sys,json,time,threading,os
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler,BaseHTTPRequestHandler
from common import TASK as B, ROOT, environment
environment();sys.path.insert(0,str(ROOT))
from refactor.desktop.account_identity import AccountIdentity
identity=AccountIdentity('https://localhost:18764/test-realm','test-update-user','更新排练账号')
class Files(SimpleHTTPRequestHandler):
    def __init__(self,*a,**k): super().__init__(*a,directory=str(B/'webroot'),**k)
    def do_GET(self):
        # Reversible offline switch for the update-feed routes only; the HTTPS
        # account substitute remains available to the preview application.
        if (B/'logs/update-feed-offline').exists() and self.path.startswith('/updates/app/'):
            self.send_error(503, 'local UAT update feed disabled')
            return
        # Optional, task-local delay used to press Login while a signed update
        # check is genuinely pending. A missing/invalid file means no delay.
        # The default behavior of this local-only test server is unchanged.
        try:
            delay_file=B/'logs/metadata-delay.json'
            delay=json.loads(delay_file.read_text(encoding='utf8')).get('seconds',0) if delay_file.exists() else 0
            if self.path.endswith(('/latest.json','/stable.json')) and isinstance(delay,(int,float)) and 0<delay<=20:
                time.sleep(delay)
        except (OSError,ValueError,TypeError):
            pass
        return super().do_GET()
    def log_message(self,fmt,*a):
        with (B/'logs/http.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps({'time':time.time(),'path':self.path,'status':a[1] if len(a)>1 else '', 'request':fmt%a},ensure_ascii=False)+'\n')
class API(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def respond(self,value,status=200):
        raw=json.dumps(value,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        with (B/'logs/api.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps({'time':time.time(),'method':self.command,'path':self.path,'status':status})+'\n')
    def do_GET(self):
        if self.path.endswith('/lifecycle/capabilities'):return self.respond({'register':True,'forgot_password':True})
        if self.path.endswith('/account/workspaces'):return self.respond({'workspaces':[{'id':'ws-test','organization_id':'org-test','status':'active','initialization_status':'READY'}],'initialization_required':False,'default_workspace_id':'ws-test'})
        if self.path.endswith('/auth/me'):return self.respond({'authenticated':True,'role':'LEARNER','account':{'subject_id':identity.subject,'account_id':identity.account_id,'display_name':identity.display_name,'preferred_username':'update-user'},'context':{'organization_id':'org-test','workspace_id':'ws-test'}})
        return self.respond({'code':'NOT_CONFIGURED'},404)
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}')
        if self.path.endswith('/register/check'):return self.respond({'field':body['field'],'available':True})
        if self.path.endswith('/register/verify'):return self.respond({'state':'VERIFIED'})
        if self.path.endswith('/register'):return self.respond({'state':'CODE_SENT','credential':'C'*43,'expires_in':600,'resend_after':1},201)
        if self.path.endswith('/native/login') or self.path.endswith('/native/refresh'):return self.respond({'schema':'mentorflow.native-session.v1','access_token':'mfn1_a_'+'A'*43,'refresh_token':'mfn1_r_'+'R'*43,'expires_in':300,'identity':{'issuer':identity.issuer,'subject':identity.subject,'display_name':identity.display_name}})
        if self.path.endswith('/native/logout'):return self.respond({'logged_out':True})
        return self.respond({'code':'NOT_CONFIGURED'},404)
http=ThreadingHTTPServer(('127.0.0.1',18763),Files); threading.Thread(target=http.serve_forever,daemon=True).start()
api=ThreadingHTTPServer(('127.0.0.1',18764),API);ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(B/'local-api.pem',B/'local-api-key.pem');api.socket=ctx.wrap_socket(api.socket,server_side=True)
(B/'server.pid').write_text(str(os.getpid()));api.serve_forever()

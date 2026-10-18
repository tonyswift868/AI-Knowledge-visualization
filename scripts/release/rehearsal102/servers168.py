"""Local-only update feed and HTTPS account substitute for 168 rehearsal."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import ssl
import sys
import threading
import time

from common import TASK as B, ROOT, environment
environment()
sys.path.insert(0, str(ROOT))
from refactor.desktop.account_identity import AccountIdentity

identity = AccountIdentity('https://localhost:18764/test-realm', 'test-update-user', '更新排练账号')


class Files(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(B/'webroot'), **kwargs)

    def do_GET(self):
        if (B/'logs/update-feed-offline').exists() and self.path.startswith('/updates/app/'):
            self.send_error(503, 'local UAT update feed disabled')
            return
        # Hold only the complete signed manifest. latest.json and
        # login-policy.json stay fast so the optional-hint path is measurable.
        try:
            delay_path = B/'logs/metadata-delay.json'
            seconds = json.loads(delay_path.read_text(encoding='utf8')).get('seconds', 0) if delay_path.exists() else 0
            if self.path.split('?', 1)[0].endswith('/stable.json') and isinstance(seconds, (int, float)) and 0 < seconds <= 30:
                time.sleep(seconds)
        except (OSError, ValueError, TypeError):
            pass
        return super().do_GET()

    def log_message(self, fmt, *args):
        row = {'time': time.time(), 'path': self.path,
               'status': args[1] if len(args) > 1 else '', 'request': fmt % args}
        with (B/'logs/http.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False)+'\n')


class API(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, value, status=200):
        raw = json.dumps(value, ensure_ascii=False).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        with (B/'logs/api.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps({'time': time.time(), 'method': self.command,
                'path': self.path, 'status': status}, ensure_ascii=False)+'\n')

    def do_GET(self):
        if self.path.endswith('/lifecycle/capabilities'):
            return self.respond({'register': True, 'forgot_password': True})
        if self.path.endswith('/account/workspaces'):
            return self.respond({'workspaces': [{'id': 'ws-test', 'organization_id': 'org-test',
                'status': 'active', 'initialization_status': 'READY'}],
                'initialization_required': False, 'default_workspace_id': 'ws-test'})
        if self.path.endswith('/auth/me'):
            return self.respond({'authenticated': True, 'role': 'LEARNER',
                'account': {'subject_id': identity.subject, 'account_id': identity.account_id,
                    'display_name': identity.display_name, 'preferred_username': 'update-user'},
                'context': {'organization_id': 'org-test', 'workspace_id': 'ws-test'}})
        return self.respond({'code': 'NOT_CONFIGURED'}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')
        if self.path.endswith('/register/check'):
            return self.respond({'field': body['field'], 'available': True})
        if self.path.endswith('/register/verify'):
            return self.respond({'state': 'VERIFIED'})
        if self.path.endswith('/register'):
            return self.respond({'state': 'CODE_SENT', 'credential': 'C'*43,
                'expires_in': 600, 'resend_after': 1}, 201)
        if self.path.endswith('/native/login') or self.path.endswith('/native/refresh'):
            return self.respond({'schema': 'mentorflow.native-session.v1',
                'access_token': 'mfn1_a_'+'A'*43, 'refresh_token': 'mfn1_r_'+'R'*43,
                'expires_in': 300, 'identity': {'issuer': identity.issuer,
                    'subject': identity.subject, 'display_name': identity.display_name}})
        if self.path.endswith('/native/logout'):
            return self.respond({'logged_out': True})
        return self.respond({'code': 'NOT_CONFIGURED'}, 404)


http = ThreadingHTTPServer(('127.0.0.1', 18763), Files)
threading.Thread(target=http.serve_forever, daemon=True).start()
api = ThreadingHTTPServer(('127.0.0.1', 18764), API)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(B/'local-api.pem', B/'local-api-key.pem')
api.socket = context.wrap_socket(api.socket, server_side=True)
(B/'server.pid').write_text(str(os.getpid()), encoding='ascii')
api.serve_forever()

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtWidgets import QApplication

from refactor.learning.app import handlers
from refactor.learning.app.bridge import AppBridge
from refactor.learning.connections import credentials
from refactor.learning.connections.gateway import ModelGateway
from refactor.learning.connections.pricing import PriceCatalog


def _pump(app, predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(.01)
    return False


def test_models_test_cancellation_reaches_gateway_before_held_response(tmp_path, monkeypatch):
    class MemoryVault:
        def __init__(self):
            self.values = {}

        def get_password(self, service, name):
            return self.values.get((service, name))

        def set_password(self, service, name, value):
            self.values[service, name] = value

        def delete_password(self, service, name):
            self.values.pop((service, name), None)

    monkeypatch.setattr(credentials, '_get_keyring', lambda: vault)
    vault = MemoryVault()
    entered, release = threading.Event(), threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, self.headers.get('Authorization'), body['model']))
            encoded = response_body(body['model'])
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            if len(requests) == 1:
                entered.set()
                if not release.wait(5):
                    return
            try:
                self.wfile.write(encoded)
            except ConnectionError:
                pass  # The cancelled client can close before this held body is released.

    def response_body(model):
        return json.dumps({
            'id': 'local-cancel-regression', 'object': 'chat.completion', 'model': model,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12,
                      'prompt_tokens_details': {'cached_tokens': 0}},
        }).encode()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    gateway = ModelGateway('mf04-models216-cancel', tmp_path, price_catalog=PriceCatalog.empty())
    gateway.save_preset('relay_openai', display_name='local regression',
                        base_url=f'http://127.0.0.1:{server.server_port}/v1',
                        model='local-test-model', api_key='synthetic-local-only', timeout=5)

    class Window:
        session_notice = None

        def __init__(self):
            self.services = type('Services', (), {'gateway': gateway, 'store': type(
                'Store', (), {'list_plans': lambda self: []})()})()

        def account_info(self):
            return {'name': 'test', 'role_label': 'test'}

        def request_account_action(self, action):
            pass

        def window_action(self, action):
            return False

        def settings_changed(self, settings):
            pass

    app = QApplication.instance() or QApplication([])
    bridge = AppBridge()
    sent = []
    bridge.event.connect(lambda raw: sent.append(json.loads(raw)))
    handlers.register(bridge, Window())
    try:
        bridge.call(json.dumps({'id': 'cancel-target', 'method': 'models.test', 'params': {}}))
        assert entered.wait(2), 'real gateway request did not reach the held local HTTP response'
        assert requests[0] == ('/v1/chat/completions', 'Bearer synthetic-local-only', 'local-test-model')
        bridge.call(json.dumps({'id': 'cancel-command', 'method': 'cancel',
                                'params': {'target': 'cancel-target'}}))
        assert _pump(app, lambda: any(m.get('id') == 'cancel-target' and m.get('type') == 'response'
                                      for m in sent), timeout=3), \
            'cancelled models.test must finish while the local server still holds its response'
        cancelled = [m for m in sent if m.get('id') == 'cancel-target' and m.get('type') == 'response']
        assert len(cancelled) == 1
        assert cancelled[0]['ok'] is False and cancelled[0]['error']['code'] == 'CANCELLED'
        with sqlite3.connect(gateway.usage_log.path) as db:
            assert db.execute('SELECT state, error_code FROM calls').fetchall() == [('cancelled', 'MODEL_CANCELLED')]
        release.set()
        time.sleep(.1)
        app.processEvents()
        assert len([m for m in sent if m.get('id') == 'cancel-target' and m.get('type') == 'response']) == 1

        bridge.call(json.dumps({'id': 'normal-control', 'method': 'models.test', 'params': {}}))
        assert _pump(app, lambda: any(m.get('id') == 'normal-control' and m.get('type') == 'response'
                                      for m in sent))
        normal = next(m for m in sent if m.get('id') == 'normal-control' and m.get('type') == 'response')
        assert normal['ok'] is True and normal['result']['reply'] == 'OK'
        assert requests[1] == ('/v1/chat/completions', 'Bearer synthetic-local-only', 'local-test-model')
    finally:
        release.set()
        bridge.close()
        gateway.close()
        server.shutdown()
        server.server_close()
        serving.join(2)
        assert not serving.is_alive()

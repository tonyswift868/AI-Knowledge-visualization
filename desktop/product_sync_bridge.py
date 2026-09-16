"""Private parent/child product-sync port. Credentials stay in the login process."""
import json
import threading
from .structured_sync import StructuredSyncClient


class ProductSyncBridgeError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _ProductResponse:
    def __init__(self, response):
        self.response = response
    def raise_for_status(self):
        status = self.response.status_code
        if status in (401,403):
            raise ProductSyncBridgeError('PRODUCT_SYNC_DENIED')
        if status in (409,410):
            raise ProductSyncBridgeError('PRODUCT_SYNC_CONFLICT')
        if status == 422:
            raise ProductSyncBridgeError('PRODUCT_SYNC_CONTRACT_REJECTED')
        if status >= 400:
            raise ProductSyncBridgeError('PRODUCT_SYNC_PENDING')
        self.response.raise_for_status()
    def json(self):
        self.raise_for_status()
        return self.response.json()


class _ProductTransport:
    def __init__(self, transport): self.transport = transport
    def put(self, path, *, json): return _ProductResponse(self.transport.put(path,json=json))
    def post(self, path, *, json): return _ProductResponse(self.transport.post(path,json=json))


class ProductSyncRelay:
    def __init__(self, transport):
        self.transport = transport
        self.client = StructuredSyncClient(_ProductTransport(transport))
        identity = transport._identity
        context = transport.context
        self.scope = dict(issuer=identity.issuer,subject_id=context.subject,
                          organization_id=context.organization_id,workspace_id=context.workspace_id)

    def call(self, message):
        operation = message.get('operation')
        if operation == 'authorize':
            self.transport.account_metadata().raise_for_status()
            return {'authorized': True}
        if operation == 'snapshot':
            return self.transport.bootstrap().publish(lambda payload, context: payload)
        if operation not in ('write','delete'):
            raise ProductSyncBridgeError('PRODUCT_SYNC_DENIED')
        args = dict(request_id=message['request_id'],expected_revision=message['expected_revision'])
        if operation == 'write': args['payload'] = message['payload']
        return getattr(self.client, operation)(message['kind'],message['entity_id'],**args)

    def serve(self, pipe, stopped):
        try:
            while not stopped.is_set():
                if not pipe.poll(.2): continue
                message = json.loads(pipe.recv_bytes(9*1024*1024))
                try:
                    value = {'result':self.call(message)}
                except Exception as exc:
                    code = getattr(exc,'code','PRODUCT_SYNC_PENDING')
                    if code in ('UNAUTHENTICATED','SESSION_STALE'): code='PRODUCT_SYNC_DENIED'
                    if code not in ('PRODUCT_SYNC_CONFLICT','PRODUCT_SYNC_DENIED','PRODUCT_SYNC_CONTRACT_REJECTED'): code='PRODUCT_SYNC_PENDING'
                    value = {'error':code}
                pipe.send_bytes(json.dumps(value,allow_nan=False,ensure_ascii=False).encode())
        except (EOFError,OSError):
            pass
        finally:
            pipe.close()


class ChildProductSyncPort:
    def __init__(self, pipe):
        self.pipe, self.lock = pipe, threading.Lock()
    def __call__(self, message):
        with self.lock:
            self.pipe.send_bytes(json.dumps(message,allow_nan=False,ensure_ascii=False).encode())
            if not self.pipe.poll(35):
                # Close a timed-out channel; late ACKs must not bind to a later request.
                self.pipe.close()
                raise ProductSyncBridgeError('PRODUCT_SYNC_PENDING')
            value = json.loads(self.pipe.recv_bytes(160*1024*1024))
            if 'error' in value: raise ProductSyncBridgeError(value['error'])
            return value['result']

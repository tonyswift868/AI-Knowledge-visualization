"""Local test child: actual app/lifespan/PG; only identity authority is synthetic."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sys

# A rehearsal must never contact the real identity authority or any remote host.
_connect, _resolve = socket.socket.connect, socket.getaddrinfo


def resolve(host, *args, **kwargs):
    if host not in ('127.0.0.1', 'localhost', '::1', None):
        raise RuntimeError('rehearsal forbids non-loopback DNS/network')
    return _resolve(host, *args, **kwargs)


def connect(sock, address):
    if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1'):
        raise RuntimeError('rehearsal forbids non-loopback network')
    return _connect(sock, address)


socket.getaddrinfo = resolve
socket.socket.connect = connect

from app.enterprise.identity import native
from app.enterprise.identity.models import AuthenticatedPrincipal


class SyntheticAuthority:
    def principal(self, subject):
        now = datetime.now(timezone.utc)
        return AuthenticatedPrincipal(issuer=os.environ['MENTORFLOW_KEYCLOAK_ISSUER'],
            subject=subject, audiences=('mentorflow-api',), client_id='mentorflow-desktop',
            token_id='local-rehearsal', issued_at=now, expires_at=now + timedelta(minutes=5),
            authentication_time=now, authentication_strength='password_verified')

    async def authenticate(self, username, password):
        if password != 'local-synthetic-password':
            raise native.denied()
        return self.principal(username)

    async def revalidate(self, previous):
        return self.principal(previous.subject)

    async def aclose(self):
        pass


native.from_env = lambda env, issuer: native.NativeSessions(SyntheticAuthority(), issuer)

from app.account_core_mvp import app
import uvicorn


def trace():
    root = Path.cwd().resolve()
    files = sorted({Path(module.__file__).resolve().relative_to(root).as_posix()
        for name, module in tuple(sys.modules.items())
        if (name == 'app' or name.startswith(('app.', 'refactor.server')))
        and getattr(module, '__file__', None)})
    target = Path(os.environ['MFU10_TRACE'])
    if target.is_file():
        files = sorted(set(files) | set(json.loads(target.read_text(encoding='utf-8'))))
    target.write_text(json.dumps(files, indent=2), encoding='utf-8')


@app.middleware('http')
async def record_loaded_sources(request, call_next):
    response = await call_next(request)
    trace()
    return response


if __name__ == '__main__':
    try:
        uvicorn.run(app, host='127.0.0.1', port=int(os.environ['MFU10_PORT']),
            ssl_certfile=os.environ['MFU10_CERT'], ssl_keyfile=os.environ['MFU10_KEY'],
            log_level='warning', access_log=False)
    finally:
        trace()

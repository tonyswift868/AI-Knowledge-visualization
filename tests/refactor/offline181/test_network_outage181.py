"""Real loopback sockets for the outage paths; no public account endpoint."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading

import httpx
import pytest

from refactor.desktop.account_protocol import AccountConfig, AccountError
from refactor.desktop.native_auth import NativeCredentialClient
from test_offline_login181 import make_session


class LoopbackOnly(httpx.AsyncBaseTransport):
    """TLS is not under test: route the configured HTTPS test URL to local HTTP."""
    def __init__(self):
        self.wire = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        assert request.url.host == '127.0.0.1'
        request.url = request.url.copy_with(scheme='http')
        return await self.wire.handle_async_request(request)

    async def aclose(self):
        await self.wire.aclose()


@pytest.mark.parametrize('failure', ['refused', 'unresponsive', '503'])
def test_network181_offline_identity_available_before_server_failure(tmp_path, failure):
    stopped = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if failure == 'unresponsive':
                stopped.wait()  # only teardown ends this synthetic hung request
                return
            self.send_response(503)
            self.send_header('Content-Length', '0')
            self.end_headers()

    server = None
    thread = None
    if failure == 'refused':
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
    else:
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()

    session, profile, _ = make_session(tmp_path)
    config = AccountConfig(issuer='https://identity.example.test/realms/mf',
        client_id='mentorflow-desktop', audience='mentorflow-api',
        api_base_url=f'https://127.0.0.1:{port}', request_timeout_seconds=1)
    async def refresh():
        async with httpx.AsyncClient(transport=LoopbackOnly(), timeout=1, trust_env=False) as wire:
            client = NativeCredentialClient(config, http_client=wire)
            with pytest.raises(AccountError) as caught:
                await client._session_request('refresh', {'refresh_token': 'synthetic-only'})
            return caught.value.code
    try:
        # Local entry is independent of any response or timeout of this server.
        local = session.load_offline_snapshot(profile)
        assert local and local.identity.account_id == profile.account_id
        code = asyncio.run(refresh())
        assert code == ('NATIVE_AUTH_UNAVAILABLE' if failure == '503' else 'NATIVE_NETWORK_UNAVAILABLE')
        assert session.load_offline_snapshot(profile) is not None
        assert session.credentials.load_oauth_tokens(profile) == ('access-secret', 'refresh-secret')
    finally:
        stopped.set()
        if server:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

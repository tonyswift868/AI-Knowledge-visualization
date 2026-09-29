"""Keycloak client credentials; secret references remain Backend-only."""
import asyncio
import json
from pathlib import Path
import time
import httpx
from .account_lifecycle import LifecycleError, _identifier


class ServiceAccountTokenResolver:
    def __init__(self, origin, realm, client_id, secret_file, *, verify=True, transport=None):
        from urllib.parse import urlsplit
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username or verify is False:
            raise ValueError('Exact verified HTTPS service account origin required')
        self.endpoint = origin.rstrip('/') + '/realms/' + _identifier(realm) + '/protocol/openid-connect/token'
        self.client_id, self.secret_file = _identifier(client_id), Path(secret_file)
        self.http = httpx.AsyncClient(verify=verify, transport=transport, trust_env=False, timeout=10, follow_redirects=False)
        self.lock = asyncio.Lock()
        self.token, self.deadline = None, 0

    async def __call__(self):
        async with self.lock:
            if self.token and time.monotonic() < self.deadline:
                return self.token
            payload = {}
            try:
                if self.secret_file.is_symlink() or self.secret_file.stat().st_size > 4096:
                    raise ValueError()
                secret = self.secret_file.read_text(encoding='utf-8').strip()
                if not 16 <= len(secret) <= 4096:
                    raise ValueError()
                payload = {'grant_type': 'client_credentials', 'client_id': self.client_id, 'client_secret': secret}
                secret = None
                async with self.http.stream('POST', self.endpoint, data=payload) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw)+len(chunk) > 32768: raise ValueError()
                        raw.extend(chunk)
                    if response.status_code != 200: raise ValueError()
                    value = json.loads(raw)
                token, ttl = value.get('access_token'), value.get('expires_in')
                if not isinstance(token, str) or not token or '\r' in token or '\n' in token or type(ttl) is not int or not 10 <= ttl <= 3600:
                    raise ValueError()
                self.token, self.deadline = token, time.monotonic()+ttl-5
                return token
            except Exception:
                self.token, self.deadline = None, 0
                raise LifecycleError('ADMIN_AUTHORITY_UNAVAILABLE') from None
            finally:
                payload.clear()

    async def aclose(self):
        self.token = None
        await self.http.aclose()

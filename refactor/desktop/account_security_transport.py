"""Authenticated account settings requests; no learning transport or retry of writes."""
import threading

import httpx

from .account_identity import issuer_hash
from .account_protocol import AccountError
from .login_session import account_session_lock


class AccountSecurityTransport:
    ROUTES = {
        ('GET', '/api/enterprise/auth/lifecycle/email'),
        ('POST', '/api/enterprise/auth/lifecycle/email-code/request'),
        ('POST', '/api/enterprise/auth/lifecycle/email-code/verify'),
        ('POST', '/api/enterprise/auth/lifecycle/change-password'),
        ('POST', '/api/enterprise/auth/lifecycle/revoke-sessions'),
    }

    def __init__(self, config, identity, credentials, *, http_factory=httpx.Client):
        if (identity.issuer_hash != issuer_hash(config.canonical_identity_issuer)
                or not identity.organization_id or not identity.workspace_id):
            raise AccountError('UNAUTHENTICATED', '请重新登录后打开账号设置。')
        self.config, self.identity, self.credentials = config, identity, credentials
        self._http_factory = http_factory
        self._closed = threading.Event()

    def close(self):
        self._closed.set()

    def get(self, path):
        return self._request('GET', path)

    def post(self, path, *, json):
        return self._request('POST', path, json)

    def _request(self, method, path, payload=None):
        if (method, path) not in self.ROUTES:
            raise ValueError('Unsupported account settings operation')
        profile = self.identity.profile()
        with account_session_lock(profile):
            if self._closed.is_set():
                raise AccountError('ACCOUNT_ACTION_CANCELLED', '账号操作已取消。')
            try:
                access, _ = self.credentials.load_oauth_tokens(profile)
            except Exception as exc:
                if isinstance(exc, KeyError) or getattr(exc, 'code', '') == 'CREDENTIAL_NOT_FOUND':
                    raise AccountError('UNAUTHENTICATED', '登录已失效，请重新登录。') from None
                raise
            if not isinstance(access, str) or not access.startswith('mfn1_a_'):
                raise AccountError('UNAUTHENTICATED', '登录已失效，请重新登录。')
            headers = {
                'Accept': 'application/json', 'Authorization': 'Bearer ' + access,
                'X-MentorFlow-Organization-ID': self.identity.organization_id,
                'X-MentorFlow-Workspace-ID': self.identity.workspace_id,
            }
            with self._http_factory(timeout=float(self.config.request_timeout_seconds), follow_redirects=False) as client:
                request = client.build_request(method, self.config.api_base_url + path, headers=headers, json=payload)
                for key in ('Cookie', 'Origin'):
                    request.headers.pop(key, None)
                response = client.send(request, stream=True, auth=None, follow_redirects=False)
                try:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if len(raw) + len(chunk) > 8192:
                            raise AccountError('ACCOUNT_RESULT_UNCERTAIN', '操作结果无法确认，请刷新状态。')
                        raw.extend(chunk)
                    return httpx.Response(response.status_code, content=bytes(raw), request=request)
                finally:
                    response.close()

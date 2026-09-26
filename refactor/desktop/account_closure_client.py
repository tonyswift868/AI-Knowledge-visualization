"""The supplied closure protocol. No ordinary session authority after prepare."""
import re
from urllib.parse import quote

import httpx

from .account_identity import issuer_hash
from .account_protocol import AccountError, _json_object

PREFIX = '/api/enterprise/account/closure'
STATES = {'PREPARED', 'IN_PROGRESS', 'RETRYABLE_FAILURE', 'COMPLETED'}
MESSAGES = {
    'CURRENT_PASSWORD_INVALID': '当前密码不正确；尚未提交注销。',
    'CLOSURE_SCOPE_UNRESOLVED': '服务器尚不能确认资料归属；尚未提交注销。',
    'UNAUTHENTICATED': '登录已失效；尚未提交新的注销，请重新登录。',
    'CLOSURE_UNCONFIRMED': '未能确认注销状态。不会当作完成，也不会重新准备；请查询原操作。',
    'CLOSURE_IDENTITY_MISMATCH': '注销响应的账号或操作不匹配，已停止处理。',
    'CLOSURE_STORAGE_FAILED': '恢复记录或 Windows 恢复凭据未能完整保存；不会发起新的提交。请保留窗口重试保存或查询原操作。',
    'CLOSURE_RECOVERY_UNAVAILABLE': '原操作的恢复凭据不可用，无法确认结果；不会重新准备另一项注销。',
    'CLOSURE_PREPARE_UNKNOWN': '准备结果未知，本机尚未提交删除，也未取得操作 ID。可使用当前同一账号的登录状态，重新输入密码后主动重新准备。服务器只会替换尚未提交的准备记录；成功后仍需最终确认，不会自动提交删除。',
    'CLOSURE_ALREADY_COMMITTED': '服务器拒绝重新准备：该账号已有已提交的注销操作，不能替换。请使用原操作记录查询；本机未取得该操作 ID，不能凭空查询。此提示不代表注销完成，恢复记录会保留。',
    'CLOSURE_LOCAL_CLEANUP_FAILED': '服务器已确认注销完成，但本机清理未完成。请关闭占用资料的窗口后继续清理。',
    'CLOSURE_CANCELLED': '已取消本次提交；已准备的操作记录会保留。',
}


def failure(code='CLOSURE_UNCONFIRMED'):
    return AccountError(code, MESSAGES.get(code, MESSAGES['CLOSURE_UNCONFIRMED']))


def operation_id(value):
    # IDs are opaque, but must be a single bounded URL path segment.
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value):
        raise failure()
    return value


class ClosureClient:
    def __init__(self, config, profile, credentials=None, *, http_factory=httpx.Client):
        if profile.issuer_hash != issuer_hash(config.canonical_identity_issuer):
            raise failure('CLOSURE_IDENTITY_MISMATCH')
        self.config, self.profile, self.credentials = config, profile, credentials
        self._http_factory = http_factory

    def _request(self, method, suffix, *, token, recovery, body=None):
        headers = {'Accept': 'application/json'}
        if not isinstance(token, str) or not token or len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise failure('CLOSURE_RECOVERY_UNAVAILABLE' if recovery else 'UNAUTHENTICATED')
        headers['X-MentorFlow-Closure-Token' if recovery else 'Authorization'] = token if recovery else 'Bearer ' + token
        try:
            with self._http_factory(timeout=float(self.config.request_timeout_seconds), follow_redirects=False) as client:
                request = client.build_request(method, self.config.api_base_url + PREFIX + suffix, headers=headers, json=body)
                for name in ('Cookie', 'Origin', 'X-MentorFlow-Organization-ID', 'X-MentorFlow-Workspace-ID',
                             'Authorization' if recovery else 'X-MentorFlow-Closure-Token'):
                    request.headers.pop(name, None)
                response = client.send(request, stream=True, auth=None, follow_redirects=False)
                try:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if len(raw) + len(chunk) > 16384:
                            raise failure()
                        raw.extend(chunk)
                    value = _json_object(httpx.Response(response.status_code, content=bytes(raw)), '注销')
                    return response.status_code, value
                finally:
                    response.close()
                    # Do not retain credentials in a request object or exception.
                    request.headers.pop('Authorization', None)
                    request.headers.pop('X-MentorFlow-Closure-Token', None)
        except Exception:
            raise failure() from None
        finally:
            if body is not None:
                body.clear()
            token = None

    def prepare(self, password):
        try:
            access, _ = self.credentials.load_oauth_tokens(self.profile)
        except Exception:
            raise failure('UNAUTHENTICATED') from None
        if not isinstance(access, str) or not access.startswith('mfn1_a_'):
            raise failure('UNAUTHENTICATED')
        status, value = self._request('POST', '/prepare', token=access, recovery=False, body={'current_password': password})
        password = access = None
        if status != 200:
            error = value.get('error', value)
            code = error.get('code') if isinstance(error, dict) else None
            if status in (400, 401, 403) and code == 'CURRENT_PASSWORD_INVALID':
                raise failure(code)
            if status == 409 and code in {'CLOSURE_SCOPE_UNRESOLVED', 'CLOSURE_ALREADY_COMMITTED'}:
                raise failure(code)
            if status in (401, 403):
                raise failure('UNAUTHENTICATED')
            raise failure('CLOSURE_PREPARE_UNKNOWN')
        if value.get('account_id') != self.profile.account_id:
            raise failure('CLOSURE_IDENTITY_MISMATCH')
        operation_id(value.get('operation_id'))
        token = value.get('recovery_token')
        if value.get('state') != 'PREPARED' or not isinstance(token, str) or not token or len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise failure()
        return value

    def status(self, record, token, *, commit=False):
        op = operation_id(record['operation_id'])
        if record['account_id'] != self.profile.account_id or record['api_base_url'] != self.config.api_base_url:
            raise failure('CLOSURE_IDENTITY_MISMATCH')
        status, value = self._request('POST' if commit else 'GET', '/' + quote(op, safe='') + ('/commit' if commit else ''),
                                     token=token, recovery=True, body={'confirmed': True} if commit else None)
        allowed = {(200, 'COMPLETED'), (202, 'IN_PROGRESS'), (503, 'RETRYABLE_FAILURE')} if commit else {(200, state) for state in STATES}
        if (status, value.get('state')) not in allowed:
            raise failure()
        if value.get('operation_id') != op or value.get('account_id') != self.profile.account_id:
            raise failure('CLOSURE_IDENTITY_MISMATCH')
        if any(key not in value or (value[key] is not None and not isinstance(value[key], str)) for key in ('code', 'message')):
            raise failure()
        # Only allowlisted state reaches the UI; server messages may be sensitive.
        return value['state']

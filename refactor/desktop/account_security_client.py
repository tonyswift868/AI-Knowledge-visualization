"""Email and password contracts extracted from the existing lifecycle client."""
import re
import threading
import time
from .account_protocol import AccountError

SECURITY_MESSAGES = {
    'EMAIL_INPUT_INVALID': '请输入有效的邮箱地址。',
    'EMAIL_CODE_INVALID_OR_EXPIRED': '验证码错误或已过期，请重新获取验证码。',
    'EMAIL_ALREADY_BOUND': '账号已经绑定邮箱，请刷新后验证该邮箱。',
    'EMAIL_ALREADY_VERIFIED': '邮箱已经验证，请刷新状态。',
    'EMAIL_CODE_RATE_LIMITED': '请求过于频繁，请稍后再获取验证码。',
    'RESET_RATE_LIMITED': '操作过于频繁，请稍后重试。',
    'ACCOUNT_ACTION_UNAVAILABLE': '账号服务暂不可用，请稍后刷新；不会自动重试。',
    'CURRENT_PASSWORD_INVALID': '当前密码不正确。',
    'NEW_PASSWORD_SAME_AS_CURRENT': '新密码不能与当前密码相同',
    'PASSWORD_INPUT_INVALID': '请使用 8–128 个字符的新密码。',
    'UNAUTHENTICATED': '登录状态已失效，请重新登录。',
    'ACCOUNT_RESULT_UNCERTAIN': '暂时无法确认操作结果，请刷新状态；不会自动重试。',
    'ACCOUNT_ACTION_CANCELLED': '操作已取消。',
    'LOCAL_REAUTH_STATE_UNAVAILABLE': '无法保存重新登录状态，尚未提交修改，请稍后重试。',
}


class AccountSecurityClient:
    """Window-owned adapter over the existing authenticated session transport.

    Email transaction references stay in memory and are fenced on cancellation.
    No user/account scope is accepted from form values.
    """
    PREFIX = '/api/enterprise/auth/lifecycle'

    def __init__(self, transport, *, clock=time.monotonic):
        self.transport, self.clock = transport, clock
        self._lock = threading.Lock()
        self._generation = 0
        self._transaction = None
        self._expires = self._resend = 0

    def cancel(self):
        with self._lock:
            self._generation += 1
            self._transaction = None
            self._expires = 0

    @staticmethod
    def _error(code):
        return AccountError(code, SECURITY_MESSAGES.get(code, SECURITY_MESSAGES['ACCOUNT_RESULT_UNCERTAIN']))

    def _call(self, suffix, payload=None, *, status=200, outcomes=None):
        try:
            response = (self.transport.get(self.PREFIX + suffix) if payload is None
                        else self.transport.post(self.PREFIX + suffix, json=payload))
            if response.status_code in (401, 403):
                raise self._error('UNAUTHENTICATED')
            value = response.json()
            if not isinstance(value, dict): raise ValueError()
            if outcomes and (response.status_code, value.get('state')) in outcomes:
                return value
            if outcomes and response.status_code == status:
                raise self._error('ACCOUNT_RESULT_UNCERTAIN')
            if response.status_code != status:
                error = value.get('error')
                if error is None: error = value
                code = error.get('code') if isinstance(error, dict) else None
                raise self._error(code if code in SECURITY_MESSAGES else 'ACCOUNT_RESULT_UNCERTAIN')
            return value
        except AccountError:
            raise
        except Exception as exc:
            code = 'UNAUTHENTICATED' if getattr(exc, 'code', None) == 'UNAUTHENTICATED' else 'ACCOUNT_RESULT_UNCERTAIN'
            raise self._error(code) from None
        finally:
            if payload is not None: payload.clear()

    @staticmethod
    def _profile(value):
        email = value.get('email')
        if email is not None and (not isinstance(email, str) or len(email) > 320): raise ValueError()
        if any(type(value.get(key)) is not bool for key in ('email_verified', 'can_bind', 'can_verify')): raise ValueError()
        verified = value['email_verified']
        if (not email and verified or value['can_bind'] != (not bool(email))
                or value['can_verify'] != (bool(email) and not verified)): raise ValueError()
        return dict(email=email or '', email_verified=verified, can_bind=value['can_bind'], can_verify=value['can_verify'])

    def read(self):
        with self._lock: generation = self._generation
        try: value = self._profile(self._call('/email'))
        except ValueError: raise self._error('ACCOUNT_RESULT_UNCERTAIN') from None
        with self._lock:
            if generation != self._generation: raise self._error('ACCOUNT_RESULT_UNCERTAIN')
        return value

    def request_code(self, email):
        with self._lock:
            if self.clock() < self._resend: raise self._error('EMAIL_CODE_RATE_LIMITED')
            generation = self._generation
            self._transaction = None
            # A timeout may follow submission: do not immediately resend.
            self._resend = self.clock() + 60
        value = self._call('/email-code/request', {'email': email}, status=202)
        if (value.get('state') != 'CODE_SENT' or not isinstance(value.get('transaction_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value['transaction_id'])
                or type(value.get('expires_in')) is not int or value['expires_in'] != 600
                or type(value.get('resend_after')) is not int or value['resend_after'] != 60):
            raise self._error('ACCOUNT_RESULT_UNCERTAIN')
        with self._lock:
            if generation != self._generation: raise self._error('ACCOUNT_RESULT_UNCERTAIN')
            self._transaction = value['transaction_id']
            self._expires = self.clock() + value['expires_in']
        return dict(expires_in=600, resend_after=60)

    def verify_code(self, code):
        with self._lock:
            if not self._transaction or self.clock() >= self._expires or not re.fullmatch(r'[0-9]{6}', code):
                raise self._error('EMAIL_CODE_INVALID_OR_EXPIRED')
            transaction, generation = self._transaction, self._generation
        value = self._call('/email-code/verify', {'transaction_id': transaction, 'code': code})
        try: profile = self._profile(value)
        except ValueError: raise self._error('ACCOUNT_RESULT_UNCERTAIN') from None
        if value.get('state') != 'EMAIL_VERIFIED' or not profile['email_verified']:
            raise self._error('ACCOUNT_RESULT_UNCERTAIN')
        with self._lock:
            if generation != self._generation: raise self._error('ACCOUNT_RESULT_UNCERTAIN')
            self._transaction = None
        return profile

    def change_password(self, current, new):
        outcomes = {(200, 'PASSWORD_CHANGED'), (503, 'PASSWORD_CHANGED_REVOCATION_PENDING'),
                    (503, 'PASSWORD_CHANGE_OUTCOME_UNKNOWN')}
        value = self._call('/change-password', {'current_password': current, 'new_password': new}, outcomes=outcomes)
        if value.get('state') not in {state for _, state in outcomes}: raise self._error('ACCOUNT_RESULT_UNCERTAIN')
        return value['state']

    def revoke_sessions(self):
        value = self._call('/revoke-sessions', {}, outcomes={(200, 'SESSION_REVOCATION_CONFIRMED'), (503, 'SESSION_REVOCATION_PENDING')})
        if value.get('state') not in {'SESSION_REVOCATION_CONFIRMED', 'SESSION_REVOCATION_PENDING'}:
            raise self._error('ACCOUNT_RESULT_UNCERTAIN')
        return value['state']


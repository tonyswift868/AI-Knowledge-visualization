"""Native transport for account capabilities, registration and email-code recovery."""
import json
import re
import threading
import time
from .native_auth import NativeCredentialClient
from .account_protocol import AccountError

RESET_MESSAGES = {
    'RESET_EMAIL_INVALID': '请输入有效的绑定邮箱。',
    'RESET_CODE_INVALID_OR_EXPIRED': '验证码错误或已过期，请重新获取验证码。',
    'RESET_PROOF_INVALID_OR_EXPIRED': '验证已过期，请重新获取验证码。',
    'PASSWORD_CONFIRMATION_MISMATCH': '两次输入的密码不一致，请重新确认。',
    'PASSWORD_POLICY_REJECTED': '密码不符合规则，请使用 8–128 个字符并重试。',
    'NEW_PASSWORD_SAME_AS_CURRENT': '新密码不能与当前密码相同',
    'RESET_RATE_LIMITED': '操作过于频繁，请稍后重试。',
    'RESET_SERVICE_UNAVAILABLE': '密码重置服务暂不可用，请稍后重试。',
    'RESET_RESULT_UNCERTAIN': '暂时无法确认操作结果。请返回登录尝试，或重新获取验证码；不会自动重试。',
}

REGISTRATION_MESSAGES = {
    'USERNAME_ALREADY_IN_USE': '这个账号名已被使用，请换一个账号名。',
    'EMAIL_ALREADY_IN_USE': '这个邮箱已被使用。请更换邮箱，或返回登录、找回密码。',
    'REGISTRATION_CONFLICT_UNRESOLVED': '注册信息存在冲突，但暂时无法确认具体字段。请稍后重试。',
    'ACCOUNT_ALREADY_IN_USE': '账号名或邮箱已被使用。请更换后重试；已有账号可返回登录或找回密码。',
    'REGISTRATION_INPUT_INVALID': '请检查账号名、邮箱地址和密码（8–128 个字符）。',
    'REGISTRATION_CHECK_UNAVAILABLE': '暂时无法检查可用性，注册时会再次确认。',
    'RESET_RATE_LIMITED': '操作过于频繁，请稍后重试。',
    'CREATED_ACCOUNT_RECONCILIATION_REQUIRED': '账号可能已创建，但未能确认注册结果。请先返回登录尝试，不要重复注册。',
    'KEYCLOAK_OPERATION_REJECTED': '账号服务未能完成注册或验证邮件发送。账号可能已创建，请先返回登录查看提示。',
    'KEYCLOAK_OUTCOME_UNKNOWN': '暂时无法确认注册或发信结果。账号可能已创建，请先返回登录尝试。',
    'REGISTRATION_RESULT_UNCERTAIN': '暂时无法确认注册或发信结果。账号可能已创建，请先返回登录尝试。',
    'ACCOUNT_ACTION_UNAVAILABLE': '注册服务暂不可用，请稍后重试。',
    'LIFECYCLE_TRANSPORT_REQUIRED': '账号连接未通过验证，请关闭并重新打开软件后重试。',
}
REGISTRATION_CHECK_MESSAGES = {
    'REGISTRATION_INPUT_INVALID': '请检查账号名或邮箱格式。',
    'REGISTRATION_CHECK_UNAVAILABLE': '暂时无法检查可用性，注册时会再次确认。',
}


def _error_code(value):
    """Accept the route and shared exception envelopes, never provider text."""
    if not isinstance(value, dict):
        return None
    code = value.get('code')
    if isinstance(code, str):
        return code
    error = value.get('error')
    return error.get('code') if isinstance(error, dict) and isinstance(error.get('code'), str) else None


class LifecycleClient(NativeCredentialClient):
    async def check_registration(self, field, value):
        if field not in ('username', 'email') or not isinstance(value, str) or not value.strip() or len(value) > 320:
            raise AccountError('REGISTRATION_INPUT_INVALID', REGISTRATION_CHECK_MESSAGES['REGISTRATION_INPUT_INVALID'])
        payload = {'field': field, 'value': value.strip()}
        try:
            async with self._http.stream('POST', self.config.api_base_url + '/api/enterprise/auth/lifecycle/register/check',
                    json=payload) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > 4096: raise ValueError()
                    raw.extend(chunk)
                result = json.loads(raw)
                if response.status_code != 200:
                    code = _error_code(result)
                    if response.status_code == 400:
                        code = 'REGISTRATION_INPUT_INVALID'
                    elif response.status_code == 503:
                        code = 'REGISTRATION_CHECK_UNAVAILABLE'
                    if code not in ('REGISTRATION_INPUT_INVALID', 'REGISTRATION_CHECK_UNAVAILABLE'):
                        code = 'REGISTRATION_CHECK_UNAVAILABLE'
                    raise AccountError(code, REGISTRATION_CHECK_MESSAGES[code])
                if (not isinstance(result, dict) or result.get('field') != field
                        or type(result.get('available')) is not bool):
                    raise ValueError()
                return {'field': field, 'available': result['available']}
        except AccountError:
            raise
        except Exception:
            raise AccountError('REGISTRATION_CHECK_UNAVAILABLE', REGISTRATION_CHECK_MESSAGES['REGISTRATION_CHECK_UNAVAILABLE']) from None
        finally:
            payload.clear()

    async def reset_code(self, operation, payload):
        if operation not in ('request', 'verify', 'complete'):
            raise ValueError('Invalid reset operation')
        try:
            request = self._http.build_request('POST', self.config.api_base_url +
                '/api/enterprise/auth/lifecycle/reset-code/' + operation, json=payload)
            for header in ('Authorization', 'Cookie', 'Origin'):
                request.headers.pop(header, None)
            response = await self._http.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > 4096: raise ValueError()
                    raw.extend(chunk)
                value = json.loads(raw)
                if not isinstance(value, dict): raise ValueError()
                if response.status_code != (202 if operation == 'request' else 200):
                    code = value.get('code')
                    if code not in RESET_MESSAGES: code = 'RESET_RESULT_UNCERTAIN'
                    raise AccountError(code, RESET_MESSAGES[code])
                expected = {'request': 'CODE_SENT', 'verify': 'VERIFIED', 'complete': 'COMPLETED'}[operation]
                if value.get('state') != expected: raise ValueError()
                if operation == 'complete':
                    if not re.fullmatch(r'mfa1_[0-9a-f]{64}', value.get('account_id', '')): raise ValueError()
                else:
                    field = 'transaction_id' if operation == 'request' else 'reset_proof'
                    if not isinstance(value.get(field), str) or not 1 <= len(value[field]) <= 1024: raise ValueError()
                    maximum = 600 if operation == 'request' else 300
                    if type(value.get('expires_in')) is not int or not 0 < value['expires_in'] <= maximum: raise ValueError()
                    if operation == 'request' and (type(value.get('resend_after')) is not int or not 0 < value['resend_after'] <= 3600): raise ValueError()
                return value
            finally:
                await response.aclose()
        except AccountError: raise
        except Exception:
            raise AccountError('RESET_RESULT_UNCERTAIN', RESET_MESSAGES['RESET_RESULT_UNCERTAIN']) from None
        finally:
            payload.clear()

    async def action(self, action, payload=None):
        if action not in ('capabilities','register'): raise ValueError('Invalid lifecycle action')
        try:
            async with self._http.stream('GET' if action=='capabilities' else 'POST',
                    self.config.api_base_url+'/api/enterprise/auth/lifecycle/'+action, json=payload) as response:
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw)+len(chunk)>4096: raise ValueError()
                    raw.extend(chunk)
                value=json.loads(raw)
                if response.status_code not in (200,201,202):
                    code=_error_code(value)
                    if code not in REGISTRATION_MESSAGES:
                        code = 'REGISTRATION_RESULT_UNCERTAIN' if action == 'register' else 'ACCOUNT_ACTION_UNAVAILABLE'
                    raise AccountError(code, REGISTRATION_MESSAGES[code])
                if not isinstance(value,dict): raise ValueError()
                if action == 'register' and value.get('state') != 'VERIFY_EMAIL_PENDING':
                    raise AccountError('REGISTRATION_RESULT_UNCERTAIN', REGISTRATION_MESSAGES['REGISTRATION_RESULT_UNCERTAIN'])
                return value
        except AccountError: raise
        except Exception:
            code = 'REGISTRATION_RESULT_UNCERTAIN' if action == 'register' else 'ACCOUNT_ACTION_UNAVAILABLE'
            raise AccountError(code, REGISTRATION_MESSAGES[code]) from None
        finally:
            if isinstance(payload,dict): payload.clear()

COMPLETED_MESSAGE = '密码已重置，请使用新密码登录 MentorFlow。'

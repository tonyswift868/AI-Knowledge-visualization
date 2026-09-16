"""Native transport for candidate lifecycle capabilities and official mail actions."""
import json
import re
from .native_auth import NativeCredentialClient
from .enterprise_oidc import EnterpriseOidcError

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

class LifecycleClient(NativeCredentialClient):
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
                    raise EnterpriseOidcError(code, RESET_MESSAGES[code])
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
        except EnterpriseOidcError: raise
        except Exception:
            raise EnterpriseOidcError('RESET_RESULT_UNCERTAIN', RESET_MESSAGES['RESET_RESULT_UNCERTAIN']) from None
        finally:
            payload.clear()

    async def action(self, action, payload=None):
        if action not in ('capabilities','register','forgot','change-password'): raise ValueError('Invalid lifecycle action')
        try:
            async with self._http.stream('GET' if action=='capabilities' else 'POST',
                    self.config.api_base_url+'/api/enterprise/auth/lifecycle/'+action, json=payload) as response:
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw)+len(chunk)>4096: raise ValueError()
                    raw.extend(chunk)
                value=json.loads(raw)
                if response.status_code not in (200,201,202):
                    code=value.get('code') if isinstance(value,dict) else None
                    raise EnterpriseOidcError('ACCOUNT_ALREADY_IN_USE' if code=='ACCOUNT_ALREADY_IN_USE' else 'ACCOUNT_ACTION_UNAVAILABLE',
                        '这个账号已被使用，请换一个。' if code=='ACCOUNT_ALREADY_IN_USE' else '账号操作暂未完成，请稍后重试。')
                if not isinstance(value,dict): raise ValueError()
                return value
        except EnterpriseOidcError: raise
        except Exception: raise EnterpriseOidcError('ACCOUNT_ACTION_UNAVAILABLE','账号操作暂未完成，请稍后重试。') from None
        finally:
            if isinstance(payload,dict): payload.clear()

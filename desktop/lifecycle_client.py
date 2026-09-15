"""Native transport for candidate lifecycle capabilities and official mail actions."""
import json
from .native_auth import NativeCredentialClient
from .enterprise_oidc import EnterpriseOidcError

class LifecycleClient(NativeCredentialClient):
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

"""Optional native password material in Windows Credential Manager only."""
import json
import sys
import re
from refactor.desktop.credential_store import CredentialStoreError, SERVICE_NAME


class SavedPasswordVault:
    @staticmethod
    def _backend():
        if sys.platform!='win32': raise CredentialStoreError('CREDENTIAL_STORE_UNAVAILABLE','需要 Windows 凭据管理器。')
        # Explicit canonical backend also makes the frozen dependency visible.
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()

    @staticmethod
    def _key(profile):
        if not isinstance(profile.account_id,str) or not re.fullmatch(r'mfa1_[0-9a-f]{64}',profile.account_id):
            raise ValueError('ACCOUNT_REQUIRED')
        return 'native-password:'+profile.account_id

    def store(self,profile,username,password):
        if not isinstance(username,str) or not username or len(username)>320 or not isinstance(password,str) or not password or len(password)>1024:
            raise ValueError('NATIVE_CREDENTIALS_REQUIRED')
        try: self._backend().set_password(SERVICE_NAME+'.NativePassword.v1',self._key(profile),json.dumps([username,password]))
        except Exception: raise CredentialStoreError('CREDENTIAL_STORE_WRITE_FAILED','密码无法保存到 Windows 凭据管理器。') from None

    def load(self,profile):
        try:
            raw=self._backend().get_password(SERVICE_NAME+'.NativePassword.v1',self._key(profile))
            if raw is None: raise CredentialStoreError('CREDENTIAL_NOT_FOUND','没有保存此账号的密码。')
            value=json.loads(raw)
            if not isinstance(value,list) or len(value)!=2 or any(not isinstance(x,str) or not x for x in value): raise ValueError()
            return tuple(value)
        except CredentialStoreError: raise
        except Exception: raise CredentialStoreError('CREDENTIAL_STORE_READ_FAILED','无法读取 Windows 凭据管理器。') from None

    def delete(self,profile):
        try:
            backend=self._backend();service=SERVICE_NAME+'.NativePassword.v1';key=self._key(profile)
            if backend.get_password(service,key) is not None: backend.delete_password(service,key)
        except Exception: raise CredentialStoreError('CREDENTIAL_STORE_DELETE_FAILED','无法删除本机保存的密码。') from None

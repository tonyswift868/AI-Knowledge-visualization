"""Disposable real WinVault qualification; no production account enumeration.

Freeze this small script with PyInstaller --onefile --paths <repo>. Run the EXE
on Windows. It prints only backend identity, booleans and an error type.
"""
import json
import secrets
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

if not getattr(sys,'frozen',False): sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.account_identity_contract import account_id
from desktop import saved_password


def run():
    service='MentorFlow.DisposableAccountSpike.'+uuid.uuid4().hex
    # Isolate this probe from every ordinary credential. The real backend and
    # production adapter methods are unchanged; only the test service differs.
    saved_password._service_name=lambda:service
    vault=saved_password.SavedPasswordVault()
    profiles=[SimpleNamespace(account_id=account_id('https://spike.invalid',uuid.uuid4().hex)) for _ in range(2)]
    result={'frozen':bool(getattr(sys,'frozen',False)),'password_or_token_logged':False}
    try:
        backend=vault._backend()
        result['backend']=type(backend).__module__+'.'+type(backend).__name__
        first,second=secrets.token_urlsafe(24),secrets.token_urlsafe(24)
        a,b=profiles
        try:
            vault.store(a,'same-username',first)
            vault.store(b,'same-username',second)
            assert vault.load(a)==('same-username',first)
            assert vault.load(b)==('same-username',second)
            vault.store(a,'same-username',second)
            assert vault.load(a)==('same-username',second)
            vault.delete(a)
            try: vault.load(a)
            except saved_password.CredentialStoreError as exc: assert exc.code=='CREDENTIAL_NOT_FOUND'
            else: raise AssertionError('DELETE_NOT_EFFECTIVE')
            assert vault.load(b)==('same-username',second)
            result['status']='PASS_SET_GET_OVERWRITE_DELETE_AB_ISOLATION'
        finally:
            for profile in profiles: vault.delete(profile)
    except Exception as exc:
        result.update(status='FAIL',error_type=type(exc).__name__)
    print(json.dumps(result))
    return 0 if result.get('status','').startswith('PASS') else 1


if __name__=='__main__':sys.exit(run())

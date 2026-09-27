import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace as NS


ROOT=Path(__file__).resolve().parents[3]


def test_registration_presentation_correlates_resend_and_expiry():
    source=ROOT/'refactor/desktop/resources/account-lifecycle/resources/js/account-lifecycle-state.js'
    uri='data:text/javascript;base64,'+__import__('base64').b64encode(source.read_bytes()).decode()
    script=f"""
import assert from 'node:assert/strict';
const {{AccountLifecycleState}}=await import({json.dumps(uri)});
let now=0; const m=new AccountLifecycleState({{capabilities:{{register:true}},now:()=>now}});
m.navigate('REGISTER');m.requestRegistration();const id=m.requestId;
assert.equal(m.registrationResult('verification_pending',{{requestId:id-1,email:'one@example.invalid'}}),false);
m.registrationResult('verification_pending',{{requestId:id,email:'someone@example.invalid'}});
assert.equal(m.maskedEmail,'s****ne@example.invalid'); assert.equal(m.requestResend(),false);
now=60001;assert.equal(m.requestResend(),true);assert.equal(m.requestRegistrationVerify(),false);
m.registrationCodeResult({{kind:'registration_resend',requestId:m.requestId,resendAfter:60}});
assert.equal(m.requestRegistrationVerify(),true);
m.registrationCodeResult({{kind:'registration_verify',requestId:m.requestId,code:'REGISTRATION_WRONG_CODE',message:'验证码错误'}});
assert.equal(m.screen,'VERIFY_PENDING'); assert.equal(m.registrationCodeValid,true);
m.registrationCodeResult({{kind:'registration_expired',code:'REGISTRATION_EXPIRED'}});
assert.equal(m.requestRegistrationVerify(),false);assert.equal(m.requestResend(),false);
m.registrationCodeResult({{kind:'registration_restart'}});assert.equal(m.screen,'REGISTER');
"""
    subprocess.run([shutil.which('node') or 'node','--input-type=module','-e',script],check=True,capture_output=True,text=True)


def test_registered_username_returns_to_login_without_remembering_password():
    from refactor.desktop.account_shell_main import AccountShell
    from unittest.mock import Mock
    gate=Mock()
    pane=NS(bridge=NS(completed_username='created-user'),dispose=Mock())
    shell=NS(_generation=3,_closing=False,_pane=pane,gate=gate)
    AccountShell._return_from_account_action(shell,3,completed=True,registration=True)
    gate.username_field.setText.assert_called_once_with('created-user')
    gate.remember_password_box.setChecked.assert_called_with(False)
    assert gate.set_state.call_args.kwargs['detail']=='注册成功，请登录'
    pane.dispose.assert_called_once()

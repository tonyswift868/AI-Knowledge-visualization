import {mountAccountLifecycle} from './account-lifecycle.js';
const root=document.getElementById('mf-account-lifecycle');
new QWebChannel(qt.webChannelTransport, channel => {
    const bridge=channel.objects.lifecycle;
    const instance=mountAccountLifecycle(root,{capabilities:{register:true,forgot_password:true}});
    window.mfStartNative=()=>instance.model.navigate(window.mfNativeIntent==='register'?'REGISTER':'FORGOT_REQUEST');
    if(window.mfNativeIntent) window.mfStartNative();
    root.addEventListener('mentorflow-account-intent',event=>{
        const {intent,requestId}=event.detail;
        if(intent==='register') {
            const form=root.querySelector('#mf-register-form');
            const first=form.querySelector('[data-mf-new-password]');
            bridge.register(form.querySelector('[name=username]').value,first.value,form.querySelector('[name=email]').value,requestId);
            first.value='';form.querySelector('[data-mf-confirm-password]').value='';
        } else if(intent==='forgot_password') bridge.requestCode(root.querySelector('#mf-forgot-identity').value,requestId);
        else if(intent==='verify_code') bridge.verifyCode(root.querySelector('#mf-reset-code').value,requestId);
        else if(intent==='resend_code') bridge.resendCode(requestId);
        else if(intent==='restart_reset') bridge.restartReset();
        else if(intent==='reset_password') {
            const form=root.querySelector('#mf-reset-form');
            bridge.completeReset(form.querySelector('[data-mf-new-password]').value,form.querySelector('[data-mf-confirm-password]').value,requestId);
            form.reset();
        }
        else if(intent==='login')bridge.returnLogin();
    });
    root.querySelectorAll('[data-navigate=LOGIN]').forEach(button=>button.addEventListener('click',()=>bridge.returnLogin()));
    root.querySelector('#mf-resend').hidden=true;
    bridge.result.connect(raw=>{
        const value=JSON.parse(raw);
        if(value.kind==='register')instance.model.registrationResult(value.outcome,{requestId:value.requestId,email:value.email||''});
        if(value.kind!=='register') instance.model.codeResult(value);
    });
});

import {mountAccountLifecycle} from './account-lifecycle.js';
const root=document.getElementById('mf-account-lifecycle');
new QWebChannel(qt.webChannelTransport, channel => {
    const bridge=channel.objects.lifecycle;
    const instance=mountAccountLifecycle(root,{capabilities:{register:true,forgot_password:true},canResendVerification:true});
    window.mfFocusNative=()=>{
        const selector=window.mfNativeIntent==='register'?'#mf-register-username':'#mf-forgot-identity';
        root.querySelector(selector)?.focus({preventScroll:true});
    };
    window.mfStartNative=()=>{
        const dark=window.mfNativeTheme==='aurora';
        document.documentElement.style.colorScheme=dark?'dark':'light';
        const colors=dark?{'--mf-bg':'#181b34','--mf-panel':'#222541','--mf-text':'#eef0ff',
            '--mf-text-2':'#b3b7de','--mf-line':'#343956','--mf-accent':'#9b8cff','--mf-accent-2':'#b3a7ff',
            '--mf-focus':'#9b8cff','--mf-soft':'#343956','--mf-gradient':'linear-gradient(135deg,#9b8cff,#7460d0)'}:{};
        for(const key of ['--mf-bg','--mf-panel','--mf-text','--mf-text-2','--mf-line','--mf-accent','--mf-accent-2','--mf-focus','--mf-soft','--mf-gradient'])
            if(colors[key])document.documentElement.style.setProperty(key,colors[key]);
            else document.documentElement.style.removeProperty(key);
        let style=document.getElementById('mf-native-focus-style');
        if(!style){style=document.createElement('style');style.id='mf-native-focus-style';document.head.append(style);}
        style.textContent='html.login-pf #mf-account-lifecycle input.pf-c-form-control:focus{background:var(--mf-panel);color:var(--mf-text)}';
        instance.model.navigate(window.mfNativeIntent==='register'?'REGISTER':'FORGOT_REQUEST');
        root.hidden=false;
    };
    if(window.mfNativeIntent) window.mfStartNative();
    root.addEventListener('mentorflow-account-intent',event=>{
        const {intent,requestId}=event.detail;
        if(intent==='register') {
            const form=root.querySelector('#mf-register-form');
            const first=form.querySelector('[data-mf-new-password]');
            bridge.register(form.querySelector('[name=username]').value.trim(),first.value,form.querySelector('[name=email]').value.trim(),requestId);
        } else if(intent==='check_registration') {
            bridge.checkRegistration(event.detail.field,event.detail.value,requestId);
        } else if(intent==='verify_registration') {
            bridge.verifyRegistration(root.querySelector('#mf-registration-code').value,requestId);
        } else if(intent==='resend_verification') {
            bridge.resendRegistration(requestId);
        } else if(intent==='restart_registration') {
            bridge.restartRegistration();
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
    bridge.result.connect(raw=>{
        const value=JSON.parse(raw);
        if(value.kind==='register') instance.model.registrationResult(value.outcome,{requestId:value.requestId,email:value.email||'',message:value.message||'',retryAfter:value.resendAfter});
        else if(value.kind==='registration_check') instance.model.registrationCheckResult(value.field,value);
        else if(value.kind.startsWith('registration_')) instance.model.registrationCodeResult(value);
        else instance.model.codeResult(value);
    });
    bridge.presentationReady?.();
});

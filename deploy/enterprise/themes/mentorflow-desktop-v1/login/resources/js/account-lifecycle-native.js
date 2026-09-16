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
        } else if(intent==='forgot_password') bridge.forgot(root.querySelector('#mf-forgot-identity').value,requestId);
        else if(intent==='login')bridge.returnLogin();
    });
    root.querySelectorAll('[data-navigate=LOGIN]').forEach(button=>button.addEventListener('click',()=>bridge.returnLogin()));
    root.querySelector('#mf-resend').hidden=true;
    bridge.result.connect(raw=>{
        const value=JSON.parse(raw);
        if(value.kind==='register')instance.model.registrationResult(value.outcome,{requestId:value.requestId,email:value.email||''});
        if(value.kind==='forgot') {
            if(value.accepted) instance.model.forgotResult(value.requestId);
            else if(value.stage==='request') {
                if(!instance.model.forgotResult(value.requestId)) return;
                instance.model.navigate('FORGOT_REQUEST');
                root.querySelector('#mf-forgot-status').textContent='暂时无法请求重置邮件，请检查网络或稍后重试。';
            } else {
                root.querySelector('#mf-forgot-receipt').textContent=value.expired
                    ? '重置状态已失效，请返回并重新发起密码找回。'
                    : '请求已受理，但暂时无法确认重置结果。请查看邮箱，完成后返回登录使用新密码。';
            }
        }
    });
});

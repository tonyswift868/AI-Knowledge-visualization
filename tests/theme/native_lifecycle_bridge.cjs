// Native adapter forwards fields only to the local bridge; opaque state stays in Python.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let receive, intent;
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{value:'fixture',hidden:false,reset:()=>{}});return elements.get(id)};
const root={querySelector:element,querySelectorAll:()=>[],addEventListener:(_,fn)=>intent=fn};
const calls=[];
const model={navigate:screen=>calls.push(screen),codeResult:value=>calls.push(value)};
const bridge={result:{connect:fn=>receive=fn},returnLogin:()=>calls.push('login'),
 requestCode:(email,id)=>calls.push(['request',email,id]),verifyCode:(code,id)=>calls.push(['verify',code,id]),
 resendCode:id=>calls.push(['resend',id]),restartReset:()=>calls.push('restart')};
const context={document:{getElementById:()=>root},window:{},qt:{webChannelTransport:{}},
 QWebChannel:function(_,ready){ready({objects:{lifecycle:bridge}})},mountAccountLifecycle:()=>({model})};
const source=fs.readFileSync('deploy/enterprise/themes/mentorflow-desktop-v1/login/resources/js/account-lifecycle-native.js','utf8');
vm.runInNewContext(source.replace(/^import[^\n]+\n/,''),context);
intent({detail:{intent:'forgot_password',requestId:1}});
assert.deepEqual(calls.pop(),['request','fixture',1]);
intent({detail:{intent:'verify_code',requestId:2}});
assert.deepEqual(calls.pop(),['verify','fixture',2]);
intent({detail:{intent:'resend_code',requestId:3}});
assert.deepEqual(calls.pop(),['resend',3]);
for(const value of [
 {kind:'request',requestId:1,stage:'code'},
 {kind:'verify',requestId:2,stage:'expired',code:'RESET_CODE_INVALID_OR_EXPIRED'},
 {kind:'complete',requestId:3,stage:'password',code:'NEW_PASSWORD_SAME_AS_CURRENT'},
 {kind:'completed',requestId:4,stage:'completed'},
]) {receive(JSON.stringify(value));assert.deepEqual(JSON.parse(JSON.stringify(calls.pop())),value);}
console.log('NATIVE_EMAIL_CODE_BRIDGE=PASS');

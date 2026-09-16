// Execute the existing native bridge with synthetic channel/model/DOM seams.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let receive;
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{textContent:'',hidden:false});return elements.get(id)};
const root={querySelector:element,querySelectorAll:()=>[],addEventListener:()=>{}};
const calls=[];
const model={navigate:screen=>calls.push(screen),forgotResult:id=>{calls.push(id);return true}};
const bridge={result:{connect:fn=>receive=fn},returnLogin:()=>{}};
const context={document:{getElementById:()=>root},window:{},qt:{webChannelTransport:{}},
 QWebChannel:function(_,ready){ready({objects:{lifecycle:bridge}})},mountAccountLifecycle:()=>({model})};
const source=fs.readFileSync('deploy/enterprise/themes/mentorflow-desktop-v1/login/resources/js/account-lifecycle-native.js','utf8');
vm.runInNewContext(source.replace(/^import[^\n]+\n/,''),context);
receive(JSON.stringify({kind:'forgot',requestId:1,accepted:false,stage:'request'}));
assert.equal(calls.at(-1),'FORGOT_REQUEST');
assert.match(element('#mf-forgot-status').textContent,/无法请求/);
receive(JSON.stringify({kind:'forgot',requestId:2,accepted:true}));
assert.equal(calls.at(-1),2);
receive(JSON.stringify({kind:'forgot',requestId:2,accepted:false,stage:'observation'}));
assert.match(element('#mf-forgot-receipt').textContent,/请求已受理/);
receive(JSON.stringify({kind:'forgot',requestId:2,accepted:false,stage:'observation',expired:true}));
assert.match(element('#mf-forgot-receipt').textContent,/已失效/);
console.log('NATIVE_FORGOT_BRIDGE_ERROR_STATES=PASS');

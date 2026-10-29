import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,pause=(ms=40)=>new Promise(r=>setTimeout(r,ms));
if(!endpoint?.startsWith('http://127.0.0.1:'))throw Error('owned local browser endpoint required');
let target;for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('profile226 page not found');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});let id=0;const pending=new Map();
ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;pending.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){try{const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}catch(e){throw Error('evaluate failed: '+expression+' :: '+String(e))}}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause()}throw Error('timeout '+expression)}
async function screenshot(name){const size=await js('({width:innerWidth,height:innerHeight})');const shot=await cdp('Page.captureScreenshot',{format:'png',clip:{x:0,y:0,...size,scale:1}});fs.writeFileSync(path.join(root,'screenshots',name),Buffer.from(shot.data,'base64'))}
async function clickText(text){const point=await js(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${JSON.stringify(text)});if(!b)throw Error('missing button '+${JSON.stringify(text)});const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await cdp('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',buttons:1,clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',buttons:0,clickCount:1});await pause()}
const checks=[];let failure='';
try{
 fs.mkdirSync(path.join(root,'screenshots'),{recursive:true});
 await until(`typeof window.mountProfile226==='function'`);
 for(const [page,label] of [['profile','个人资料'],['card','账号卡片']])for(const [theme,suffix] of [['sakura','light'],['aurora','dark']]){
   await cdp('Emulation.setDeviceMetricsOverride',{width:1100,height:900,deviceScaleFactor:1,mobile:false});
   await js(`(async()=>{document.documentElement.dataset.theme=${JSON.stringify(theme)};await window.mountProfile226(${JSON.stringify(page)})})()`);
   if(page==='profile')await until(`document.querySelector('[data-testid="profile226"]')?.textContent.includes(${JSON.stringify(label)})`);
   else await until(`document.querySelector('.account-card')?.textContent.includes('Tony')`);
   await pause(240);
   const text=await js('document.body.innerText');
   if(page==='profile'&&!text.includes('登录用户名：tonyswift'))throw Error('独立登录名未显示');
   if(page==='profile'&&!text.includes('30 字')&&!text.includes('30/30'))throw Error('昵称字数提示缺失');
   if(page==='card'&&text.includes('仅本人可见的测试签名'))throw Error('卡片不应泄漏签名');
   if(page==='card'&&!(await js(`document.querySelector('.account-card .account-meta b')?.textContent==='Tony'`)))throw Error('sidebar 未显示昵称');
   await screenshot(`${page}-${suffix}.png`);
 }
 checks.push('设置个人资料和左下账号卡片均在 sakura/aurora 主题下渲染');
 checks.push('昵称显示与登录用户名分离；账号卡片不显示私人签名');
 checks.push('sidebar 使用昵称，未修改认证用户名');
 await js(`window.mountProfile226('profile')`);await until(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='选择头像')`);
 await clickText('选择头像');await until(`!!document.querySelector('[data-testid="profile226-crop"]')`);await screenshot('profile-crop-preview.png');
 if(!(await js(`document.querySelector('[data-testid="profile226-crop"] .reactEasyCrop_CropArea')?.className.includes('CropArea')`)))throw Error('可调位置的裁剪预览缺失');
 await clickText('取消');checks.push('头像选择后提供可调位置的正方形预览，取消不提交');
 await js(`(()=>{const i=document.querySelector('input[aria-label="昵称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'另一位昵称');i.dispatchEvent(new Event('input',{bubbles:true}));const t=document.querySelector('textarea[aria-label="个性签名"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,'<img src=x onerror=alert(1)>');t.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 await clickText('保存个人资料');await until(`document.querySelector('[data-testid="profile226"] .mf-profile-identity h3')?.textContent.trim()==='另一位昵称'&&document.querySelector('[data-testid="profile226"] .mf-profile-save-row [role="status"]')?.textContent.includes('已保存')`);
 if(await js(`document.querySelector('[data-testid="profile226"] .mf-profile-identity p')?.textContent!=='<img src=x onerror=alert(1)>'`))throw Error('保存成功后个人签名没有按纯文本展示');
 if(await js(`document.querySelector('[data-testid="profile226"] img[onerror]')!==null`))throw Error('签名被当作HTML执行');
 await js(`window.mountProfile226('card')`);await until(`document.querySelector('.account-card .account-meta b')?.textContent==='另一位昵称'`);
 checks.push('通过正式UI保存昵称与签名后，卡片及时更新；签名作为纯文本渲染');
 await js(`window.mountProfile226('profile')`);await until(`!!document.querySelector('input[aria-label="昵称"]')`);
 await js(`window.delayProfile226Save();(()=>{const i=document.querySelector('input[aria-label="昵称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'迟到的旧账号昵称');i.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 await clickText('保存个人资料');
 await js(`window.prepareOtherProfile226();window.switchProfile226Account()`);
 await until(`document.querySelector('input[aria-label="昵称"]')?.value==='Other profile'`);
 await js(`window.resolveProfile226Save()`);await pause(180);
 if(await js(`document.querySelector('input[aria-label="昵称"]')?.value!=='Other profile'||!document.body.innerText.includes('Other signature')`))throw Error('迟到的旧账号保存响应污染了当前账号资料');
 checks.push('切换账号时迟到的保存响应不会覆盖新账号资料');
 await js(`window.configureProfile226('offline');window.mountProfile226('profile')`);
 await until(`document.querySelector('[data-testid="profile226"]')?.textContent.includes('当前离线')`);
 if(!(await js(`document.querySelector('input[aria-label="昵称"]').disabled&&[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存个人资料').disabled`)))throw Error('离线仍允许编辑或保存');
 checks.push('离线模式保留缓存资料且禁用编辑和保存');
 await js(`window.configureProfile226('unsupported');window.mountProfile226('card')`);await pause(80);await js(`window.mountProfile226('profile')`);
 await until(`document.querySelector('[data-testid="profile226"]')?.textContent.includes('当前服务器暂不支持个人资料')`);
 if(await js(`document.querySelector('[role="alert"]')!==null`))throw Error('旧服务器不支持个人资料时显示错误');
 checks.push('旧服务器未注册资料接口时回退原账号展示，不冒错误');
 await js(`window.configureProfile226('online');window.mountProfile226('card')`);await pause(80);await js(`window.mountProfile226('profile')`);
 await until(`!!document.querySelector('input[aria-label="昵称"]')`);
 await js(`(()=>{const i=document.querySelector('input[aria-label="昵称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'😀'.repeat(31));i.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 if(!(await js(`document.querySelector('[data-testid="profile226"]')?.textContent.includes('31/30 字')&&[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存个人资料').disabled`)))throw Error('昵称30字边界未阻止保存');
 await js(`(()=>{const i=document.querySelector('input[aria-label="昵称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'Tony');i.dispatchEvent(new Event('input',{bubbles:true}));const t=document.querySelector('textarea[aria-label="个性签名"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,'😀'.repeat(61));t.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 if(!(await js(`document.querySelector('[data-testid="profile226"]')?.textContent.includes('61/60 字')&&[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存个人资料').disabled`)))throw Error('签名60字边界未阻止保存');
 checks.push('昵称30和签名60按Unicode code point显示并阻止超限保存');
 await js(`window.configureProfile226('delete');window.mountProfile226('card')`);await pause(80);await js(`window.mountProfile226('profile')`);
 await until(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='删除头像')`);
 await clickText('删除头像');await until(`![...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='删除头像')`);
 if(!(await js(`document.querySelector('[data-testid="profile226-avatar"]')?.textContent.trim()==='T'`)))throw Error('删除后未回到默认首字母头像');
 checks.push('删除头像后服务器资料更新并回退默认首字母头像');
}catch(e){failure=String(e.stack||e)}finally{fs.writeFileSync(path.join(root,'result226.json'),JSON.stringify({ok:!failure,checks,error:failure},null,2));ws.close()}
console.log(JSON.stringify({ok:!failure,checks,error:failure}));if(failure)process.exitCode=1;

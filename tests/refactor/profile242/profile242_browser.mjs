import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const root=path.resolve(process.argv[2]),endpoint=process.env.MF_CDP_ENDPOINT,pause=(ms=40)=>new Promise(r=>setTimeout(r,ms));
if(!endpoint?.startsWith('http://127.0.0.1:'))throw Error('owned local browser endpoint required');
let target;for(let i=0;i<150;i++){try{target=(await(await fetch(endpoint+'/json/list')).json()).find(t=>t.type==='page'&&t.url.startsWith('file:')&&path.resolve(fileURLToPath(t.url)).toLowerCase().startsWith(root.toLowerCase()+path.sep));if(target)break}catch{}await pause(100)}
if(!target)throw Error('profile242 page not found');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});let id=0;const pending=new Map();
const events=[];ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);d.error?p.reject(Error(JSON.stringify(d.error))):p.resolve(d.result)}else if(d.method)events.push({method:d.method,params:d.params})};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const k=++id;const timer=setTimeout(()=>{pending.delete(k);reject(Error('CDP timeout '+method))},8000);pending.set(k,{resolve:v=>{clearTimeout(timer);resolve(v)},reject:e=>{clearTimeout(timer);reject(e)}});ws.send(JSON.stringify({id:k,method,params}))});
async function js(expression){try{const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}catch(e){throw Error('evaluate failed: '+expression+' :: '+String(e))}}
async function until(expression){for(let i=0;i<300;i++){if(await js(expression))return;await pause()}const state=await js(`({input:document.querySelector('input[aria-label="昵称"]')?.value,text:document.querySelector('[data-testid="profile226"]')?.innerText,account:window.getAccount242?.(),bridge:window.getBridgeProfile242?.()})`);throw Error('timeout '+expression+' state='+JSON.stringify(state))}
async function screenshot(name){const size=await js('({width:innerWidth,height:innerHeight})');const shot=await cdp('Page.captureScreenshot',{format:'png',clip:{x:0,y:0,...size,scale:1}});fs.writeFileSync(path.join(root,'screenshots',name),Buffer.from(shot.data,'base64'))}
async function clickText(text){const point=await js(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${JSON.stringify(text)});if(!b)throw Error('missing button '+${JSON.stringify(text)}+'; have='+JSON.stringify([...document.querySelectorAll('button')].map(x=>x.textContent.trim())));const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);await cdp('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',buttons:1,clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',buttons:0,clickCount:1});await pause()}
async function clickAvatarChoice(){const label=await js(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='选择头像')?'选择头像':'重新选择原图'`);await clickText(label)}
const checks=[];let failure='';
try{
 fs.mkdirSync(path.join(root,'screenshots'),{recursive:true});
 await cdp('Page.enable');await cdp('Runtime.enable');await cdp('Log.enable');
 await until(`typeof window.mountProfile242==='function'`);
 for(const [page,label] of [['profile','个人资料'],['card','账号卡片']])for(const [theme,suffix] of [['sakura','light'],['aurora','dark']]){
   await cdp('Emulation.setDeviceMetricsOverride',{width:1760,height:1050,deviceScaleFactor:1,mobile:false});
   await js(`(async()=>{document.documentElement.dataset.theme=${JSON.stringify(theme)};await window.mountProfile242(${JSON.stringify(page)})})()`);
   if(page==='profile'){await until(`document.querySelector('[data-testid="profile226"]')?.textContent.includes(${JSON.stringify(label)})`);await until(`!!document.querySelector('input[aria-label="昵称"]')`);if(!(await js(`document.querySelector('[data-testid="profile226"]').getBoundingClientRect().width>=800`)))throw Error('1760宽窗口个人资料仍受窄版夹具限制')}
   else await until(`document.querySelector('.account-card')?.textContent.includes('Tony')`);
   await pause(240);
   const text=await js('document.body.innerText');
   if(page==='profile'&&!text.includes('登录用户名：mira'))throw Error('独立登录名未显示');
   if(page==='profile'){const sync=await js(`({profile:document.querySelector('.mf-profile-avatar img')?.src,account:window.getAccount242()?.avatar_url})`);if(!sync.profile||sync.account!==sync.profile)throw Error('个人资料头像未同步到账号状态: '+JSON.stringify(sync))}
   if(page==='profile'&&!text.includes('30 字')&&!text.includes('30/30'))throw Error('昵称字数提示缺失');
   if(page==='card'&&text.includes('仅本人可见的测试签名'))throw Error('卡片不应泄漏签名');
   if(page==='card'&&!(await js(`document.querySelector('.account-card .account-meta b')?.textContent==='Tony'`)))throw Error('sidebar 未显示昵称');
   if(page==='card'){const side=await js(`({src:document.querySelector('.account-card .avatar img')?.src,text:document.querySelector('.account-card .avatar')?.textContent.trim(),radius:getComputedStyle(document.querySelector('.account-card .avatar')).borderRadius})`);if(!side.src||side.radius!=='50%')throw Error('侧栏没有同步圆形照片头像: '+JSON.stringify(side))}
   if(page==='card')await screenshot(`${page}-${suffix}.png`);
 }
 checks.push('设置个人资料和左下账号卡片均在 sakura/aurora 主题下渲染');
 checks.push('昵称显示与登录用户名分离；账号卡片不显示私人签名');
 checks.push('sidebar 使用昵称，未修改认证用户名');
 await cdp('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
 await js(`window.mountProfile242('profile')`);await until(`[...document.querySelectorAll('button')].some(b=>['选择头像','重新选择原图'].includes(b.textContent.trim()))`);
 if(!(await js(`getComputedStyle(document.querySelector('.mf-profile')).animationName!=='none'`)))throw Error('个人资料进入动效缺失');
 for(let frame=1;frame<=3;frame++){await screenshot('motion-frame-'+frame+'.png');await pause(70)}
 await cdp('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 if(!(await js(`getComputedStyle(document.querySelector('.mf-profile')).animationName==='none'`)))throw Error('系统减少动态效果设置未关闭个人资料动画');
 await js(`document.documentElement.classList.add('reduce-motion')`);if(!(await js(`getComputedStyle(document.querySelector('.mf-profile')).animationName==='none'`)))throw Error('应用内减少动态效果设置未生效');await js(`document.documentElement.classList.remove('reduce-motion')`);
 await cdp('Emulation.setDeviceMetricsOverride',{width:1100,height:650,deviceScaleFactor:1,mobile:false});await pause(180);
 if(!(await js(`document.documentElement.scrollWidth<=innerWidth&&document.querySelector('.content').scrollWidth<=document.querySelector('.content').clientWidth`)))throw Error('矮窗设置页出现横向溢出');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1760,height:1050,deviceScaleFactor:1,mobile:false});
 for(const [width,height,size] of [[1760,1050,'wide'],[1100,650,'short']])for(const [theme,suffix] of [['sakura','light'],['aurora','dark']]){
   await cdp('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});await js(`document.documentElement.dataset.theme=${JSON.stringify(theme)};window.mountProfile242('profile')`);await until(`!!document.querySelector('[data-testid="profile226"]')`);await until(`!!document.querySelector('input[aria-label="昵称"]')`);await pause(120);await screenshot(`profile-${size}-${suffix}.png`);
   await clickAvatarChoice();await until(`!!document.querySelector('[data-testid="profile226-crop"] .reactEasyCrop_Image')`);await until(`document.querySelector('.reactEasyCrop_Image')?.naturalWidth===320`);await until(`(()=>{const a=document.querySelector('.reactEasyCrop_CropArea')?.getBoundingClientRect(),c=document.querySelector('.avatar-crop-circle-preview'),x=c?.getContext('2d');return !!a&&a.width>=150&&a.height>=150&&Math.abs(a.width-a.height)<=1&&!!x&&x.getImageData(56,56,1,1).data[3]>0})()`);await screenshot(`crop-${size}-${suffix}.png`);await clickText('取消');await until(`[...document.querySelectorAll('button')].some(b=>['选择头像','重新选择原图'].includes(b.textContent.trim()))`);
 }
 await js(`window.setProfile242({nickname:'Tony',signature:'仅本人可见',version:8,avatar_url:null,available:true,offline:false});window.mountProfile242('card')`);await pause(80);await js(`window.mountProfile242('profile')`);await until(`!!document.querySelector('input[aria-label="昵称"]')`);await js(`window.mountProfile242('card')`);await until(`!!document.querySelector('.account-card')`);if(!(await js(`!document.querySelector('.account-card .avatar img')&&document.querySelector('.account-card .avatar')?.textContent.trim()==='M'`)))throw Error('无头像默认首字母缺失');await screenshot('card-no-avatar.png');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1760,height:1050,deviceScaleFactor:1,mobile:false});await js(`document.documentElement.dataset.theme='sakura';window.mountProfile242('profile')`);await until(`!!document.querySelector('[data-testid="profile226"]')`);await until(`!!document.querySelector('input[aria-label="昵称"]')`);
 await clickAvatarChoice();await until(`!!document.querySelector('[data-testid="profile226-crop"]')`);
 await until(`document.querySelector('.reactEasyCrop_Image')?.naturalWidth===320&&document.querySelector('.reactEasyCrop_Image')?.naturalHeight===200`);
 const colors=await js(`(()=>{const i=document.querySelector('.reactEasyCrop_Image'),c=document.createElement('canvas');c.width=320;c.height=200;const x=c.getContext('2d');x.drawImage(i,0,0);return {left:[...x.getImageData(40,30,1,1).data].slice(0,3),right:[...x.getImageData(180,30,1,1).data].slice(0,3)}})()`);if(!(colors.left[0]>colors.left[2]&&colors.right[2]>colors.right[0]))throw Error('非对称预览原图方向或颜色不符: '+JSON.stringify(colors));
 const cropArea=await js(`(()=>{const a=document.querySelector('.reactEasyCrop_CropArea')?.getBoundingClientRect(),i=document.querySelector('.reactEasyCrop_Image');return a&&i?{width:a.width,height:a.height,imageWidth:i.getBoundingClientRect().width,naturalWidth:i.naturalWidth}:null})()`);if(!(cropArea&&cropArea.width>=150&&cropArea.height>=150&&Math.abs(cropArea.width-cropArea.height)<=1))throw Error('圆形裁剪区域尺寸非正常方形: '+JSON.stringify(cropArea));
 await clickText('取消');await until(`[...document.querySelectorAll('button')].some(b=>['选择头像','重新选择原图'].includes(b.textContent.trim()))`);if(await js(`window.getProfile242Crop()!==null`))throw Error('取消裁剪仍提交了头像');checks.push('320×200不对称原图按真实尺寸预览；取消不提交');
 await clickAvatarChoice();await until(`!!document.querySelector('[data-testid="profile226-crop"] .reactEasyCrop_CropArea')`);
 await js(`(()=>{const r=document.querySelector('.reactEasyCrop_CropArea').getBoundingClientRect();window.__cropPoint={x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 const drag=await js('window.__cropPoint');await cdp('Input.dispatchMouseEvent',{type:'mousePressed',...drag,button:'left',buttons:1});await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',x:drag.x+28,y:drag.y+15,button:'left',buttons:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:drag.x+28,y:drag.y+15,button:'left',buttons:0});
 await js(`(()=>{const z=document.querySelector('[aria-label="缩放照片"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(z,'1.5');z.dispatchEvent(new Event('input',{bubbles:true}));z.dispatchEvent(new Event('change',{bubbles:true}))})()`);await pause(120);
 const cropMetrics=await js(`(()=>{const a=document.querySelector('.reactEasyCrop_CropArea').getBoundingClientRect(),i=document.querySelector('.reactEasyCrop_Image');return {width:a.width,height:a.height,imageWidth:i.getBoundingClientRect().width,naturalWidth:i.naturalWidth}})()`);const expectedCropSize=Math.round(cropMetrics.width*cropMetrics.naturalWidth/cropMetrics.imageWidth);
 await clickText('保存头像');await until(`!document.querySelector('[data-testid="profile226-crop"]')`);
 const crop=await js('window.getProfile242Crop()');if(!(crop&&crop.x>=0&&crop.y>=0&&crop.size>0&&crop.x+crop.size<=320&&crop.y+crop.size<=200))throw Error('裁剪坐标越界或无效: '+JSON.stringify(crop));if(!(crop.size>0&&Math.abs(crop.size-expectedCropSize)<=1&&crop.x+crop.size<=320&&crop.y+crop.size<=200&&(crop.x!==60||crop.y!==0)))throw Error('提交的裁剪像素与圆形预览尺寸/位置不匹配: '+JSON.stringify({crop,expectedCropSize,cropMetrics}));
 await js(`window.mountProfile242('card')`);await until(`!!document.querySelector('.account-card')`);if(!(await js(`!!document.querySelector('.account-card img[src*="data:image"]')&&getComputedStyle(document.querySelector('.account-card .avatar')).borderRadius==='50%'`)))throw Error('头像保存后账号卡未同步圆形新头像');checks.push('拖动缩放后回传实际裁剪像素，头像单独保存并同步账号卡');
 await js(`window.mountProfile242('profile')`);await until(`!!document.querySelector('input[aria-label="昵称"]')`);
 await js(`(()=>{const i=document.querySelector('input[aria-label="昵称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'头像操作保留草稿');i.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 await clickAvatarChoice();await until(`!!document.querySelector('[data-testid="profile226-crop"]')`);await clickText('保存头像');await until(`!document.querySelector('[data-testid="profile226-crop"]')`);
 if(await js(`document.querySelector('input[aria-label="昵称"]')?.value!=='头像操作保留草稿'`))throw Error('保存头像清除了未保存昵称草稿');
 await clickText('删除头像');await until(`![...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='删除头像')`);if(await js(`document.querySelector('input[aria-label="昵称"]')?.value!=='头像操作保留草稿'`))throw Error('删除头像清除了未保存昵称草稿');checks.push('保存头像和删除头像均保留尚未保存的昵称草稿');
 await js(`(()=>{const i=document.querySelector('input[aria-label="昵称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'另一位昵称');i.dispatchEvent(new Event('input',{bubbles:true}));const t=document.querySelector('textarea[aria-label="个性签名"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,'<img src=x onerror=alert(1)>');t.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 await clickText('保存个人资料');await until(`document.querySelector('[data-testid="profile226"]')?.textContent.includes('另一位昵称')`);
 if(await js(`document.querySelector('[data-testid="profile226"] img[onerror]')!==null`))throw Error('签名被当作HTML执行');
 await js(`window.mountProfile242('card')`);await until(`document.querySelector('.account-card .account-meta b')?.textContent==='另一位昵称'`);
 checks.push('通过正式UI保存昵称与签名后，卡片及时更新；签名作为纯文本渲染');
 await js(`window.mountProfile242('profile')`);await until(`!!document.querySelector('input[aria-label="昵称"]')`);
 await js(`window.delayProfile242Save();(()=>{const i=document.querySelector('input[aria-label="昵称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'迟到的旧账号昵称');i.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 await clickText('保存个人资料');
 await until(`window.hasPendingProfile242Save?.()`);await js(`window.prepareOtherProfile242();window.switchProfile242Account()`);
 await until(`document.querySelector('input[aria-label="昵称"]')?.value==='Other profile'`);
 await js(`window.resolveProfile242Save()`);await pause(180);
 if(await js(`document.querySelector('input[aria-label="昵称"]')?.value!=='Other profile'||!document.body.innerText.includes('Other signature')`))throw Error('迟到的旧账号保存响应污染了当前账号资料');
 checks.push('切换账号时迟到的保存响应不会覆盖新账号资料');
 await js(`window.setProfile242({nickname:'Stale get profile',signature:'stale get signature',version:20,avatar_url:null,available:true,offline:false});window.delayProfile242Get();window.mountProfile242('profile')`);await until(`window.hasPendingProfile242Get?.()`);
 await js(`window.setProfile242({nickname:'Fresh get profile',signature:'fresh signature',version:21,avatar_url:null,available:true,offline:false});window.switchProfile242Account()`);await until(`document.querySelector('input[aria-label="昵称"]')?.value==='Fresh get profile'`);
 await js(`window.resolveProfile242Get()`);await pause(150);if(await js(`document.querySelector('input[aria-label="昵称"]')?.value!=='Fresh get profile'||!document.body.innerText.includes('fresh signature')`))throw Error('迟到的旧账号资料读取响应污染当前账号');checks.push('切换账号后迟到的get响应不会污染新账号资料');
 await js(`window.configureProfile242('offline');window.mountProfile242('profile')`);
 await until(`document.querySelector('[data-testid="profile226"]')?.textContent.includes('当前离线')`);
 if(!(await js(`document.querySelector('input[aria-label="昵称"]').disabled&&[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存个人资料').disabled`)))throw Error('离线仍允许编辑或保存');
 checks.push('离线模式保留缓存资料且禁用编辑和保存');
 await js(`window.configureProfile242('unsupported');window.mountProfile242('card')`);await pause(80);await js(`window.mountProfile242('profile')`);
 await until(`document.querySelector('[data-testid="profile226"]')?.textContent.includes('当前服务器暂不支持个人资料')`);
 if(await js(`document.querySelector('[role="alert"]')!==null`))throw Error('旧服务器不支持个人资料时显示错误');
 checks.push('旧服务器未注册资料接口时回退原账号展示，不冒错误');
 await js(`window.configureProfile242('online');window.mountProfile242('card')`);await pause(80);await js(`window.mountProfile242('profile')`);
 await until(`!!document.querySelector('input[aria-label="昵称"]')`);
 await js(`(()=>{const i=document.querySelector('input[aria-label="昵称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'😀'.repeat(31));i.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 if(!(await js(`document.querySelector('[data-testid="profile226"]')?.textContent.includes('31/30 字')&&[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存个人资料').disabled`)))throw Error('昵称30字边界未阻止保存');
 await js(`(()=>{const i=document.querySelector('input[aria-label="昵称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'Tony');i.dispatchEvent(new Event('input',{bubbles:true}));const t=document.querySelector('textarea[aria-label="个性签名"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,'😀'.repeat(61));t.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 if(!(await js(`document.querySelector('[data-testid="profile226"]')?.textContent.includes('61/60 字')&&[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='保存个人资料').disabled`)))throw Error('签名60字边界未阻止保存');
 checks.push('昵称30和签名60按Unicode code point显示并阻止超限保存');
 await js(`window.setProfile242({nickname:'',signature:'',version:10,avatar_url:null,available:true,offline:false});window.mountProfile242('profile')`);await until(`!!document.querySelector('[data-testid="profile226-avatar"]')`);if(await js(`document.querySelector('[data-testid="profile226-avatar"]')?.textContent.trim()!=='M'`))throw Error('空昵称时头像首字母没有回退到登录名');checks.push('昵称首字母与登录名不同；空昵称时回退到登录名首字母');
 await js(`window.configureProfile242('delete');window.mountProfile242('card')`);await pause(80);await js(`window.mountProfile242('profile')`);
 await until(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='删除头像')`);
 await clickText('删除头像');await until(`![...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='删除头像')`);
 if(!(await js(`document.querySelector('[data-testid="profile226-avatar"]')?.textContent.trim()==='M'`)))throw Error('删除后未回到默认首字母头像');
 checks.push('删除头像后服务器资料更新并回退默认首字母头像');
}catch(e){failure=String(e.stack||e)+'\nEVENTS '+JSON.stringify(events.slice(-25))}finally{fs.writeFileSync(path.join(root,'result242.json'),JSON.stringify({ok:!failure,checks,error:failure},null,2));ws.close()}
console.log(JSON.stringify({ok:!failure,checks,error:failure}));if(failure)process.exitCode=1;

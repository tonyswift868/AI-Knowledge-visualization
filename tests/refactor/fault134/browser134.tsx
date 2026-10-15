import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {App} from '../../../refactor/learning/web/src/App'
import {connect} from '../../../refactor/learning/web/src/bridge'
import {useApp} from '../../../refactor/learning/web/src/store'
import initial from './state134.json'
import '../../../refactor/learning/web/src/theme.css'
let sink:(wire:string)=>void,number=0,room:any=structuredClone(initial),fail=true
const settings={version:1,theme:'sakura',reduce_motion:true,font_scale:1,analogy_domains:[],teacher_style:'warm',detail:'normal',examples:'normal'}
const preset={id:'p',provider_id:'deepseek',display_name:'DeepSeek',model:'deepseek-v4-flash',has_api_key:true,base_url:'http://127.0.0.1',timeout:120}
function status(ok=false){const fault={id:`fault134-${++number}`,category:'busy',code:'MODEL_FIRST_EVENT_TIMEOUT',short:'模型暂不可用',message:'模型暂时无法使用（DeepSeek 服务繁忙），请稍后再试',actions:[{id:'retry',label:'重试'}]};
 const value=ok?{state:'ok',short:'模型正常'}:{state:'error',short:fault.short,fault};sink(JSON.stringify({type:'event',name:'model_status',data:value}));return fault}
;(window as any).qt={webChannelTransport:{}}
;(window as any).QWebChannel=class{constructor(_:any,ready:any){ready({objects:{mf:{event:{connect:(callback:any)=>sink=callback},call:(wire:string)=>{
 const r=JSON.parse(wire);const respond=(result:any)=>sink(JSON.stringify({type:'response',id:r.id,ok:true,result}));
 setTimeout(()=>{
  if(r.method==='cancel')return
  if(r.method==='app.bootstrap')return respond({account:{name:'离线验证',role_label:'测试'},settings,options:{},notice:null,ready:true})
  if(r.method==='models.status')return respond({state:'unknown',short:'模型待验证'})
  if(r.method==='sync.status')return respond(null)
  if(r.method==='settings.get')return respond({settings,options:{}})
  if(r.method==='models.providers')return respond({providers:[{id:'deepseek',name:'DeepSeek',default_base_url:'http://127.0.0.1',default_model:'deepseek-v4-flash'}]})
  if(r.method==='models.list')return respond({presets:[preset],active_id:'p'})
  if(r.method==='models.test'){
   if(fail){const fault=status();return sink(JSON.stringify({type:'response',id:r.id,ok:false,error:{code:fault.code,message:fault.message,model_fault:fault}}))}
   status(true);return respond({latency_ms:120,model:'DeepSeek'})
  }
  if(r.method==='speech.status')return respond({ready:false,components:[],download:null})
  if(r.method==='classroom.next'||r.method==='classroom.retry'){
    if(fail){const fault=status();room.failure={message:fault.message,model_fault:fault}}
    else{status(true);room.failure=null}
    return respond(structuredClone(room))
  }
  if(r.method==='classroom.state'||r.method==='classroom.enter')return respond(structuredClone(room))
  return respond({})
 },40)
}}}})}}
;(window as any).scenario134=(theme:string)=>{settings.theme=theme;room=structuredClone(initial);fail=true;useApp.setState({route:'classroom',planId:'a'.repeat(32),settings:structuredClone(settings) as any,modelStatus:{state:'unknown',short:'模型待验证'},modelFault:null})}
;(window as any).success134=()=>{fail=false;status(true)}
;(window as any).fail134=()=>{fail=true;status()}
;(window as any).settings134=()=>useApp.getState().go('settings','models')
;(window as any).ready134=connect().then(()=>{(window as any).scenario134('sakura');createRoot(document.getElementById('root')!).render(<App/>);return true})

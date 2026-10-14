import * as React from 'react'
export {MicButton} from './MicButton133'
export function useVoiceInput(){
 const [state,setState]=React.useState('idle')
 React.useEffect(()=>{const fn=(e:Event)=>setState((e as CustomEvent).detail);window.addEventListener('voice133',fn);return()=>window.removeEventListener('voice133',fn)},[])
 return {state,supported:true,disabled:false,model:null,error:null,notice:state==='idle'?'': '正在处理本轮语音',downloadOpen:false,
  cancel:()=>setState('idle'),start:()=>setState('recording'),stop:()=>setState('finishing')}
}

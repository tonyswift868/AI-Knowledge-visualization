import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {Classroom} from '../../../refactor/learning/web/src/pages/Classroom'
import {useApp} from '../../../refactor/learning/web/src/store'
import {calls,scenario129} from './bridge129'
import '../../../refactor/learning/web/src/theme.css'
const root=createRoot(document.getElementById('root')!)
let generation=0
const pause=()=>new Promise(r=>setTimeout(r,20))
async function until(fn:()=>boolean){for(let i=0;i<300;i++){if(fn())return;await pause()}throw Error('mount129 timed out')}
useApp.setState({planId:'a'.repeat(32),settings:{reduce_motion:false} as any})
;(window as any).calls129=calls
;(window as any).reduced129=(value:boolean)=>useApp.setState({settings:{...useApp.getState().settings,reduce_motion:value} as any})
;(window as any).mount129=async(mode='normal')=>{
 scenario129(mode);const own=++generation
 root.render(<div style={{height:'100vh'}} data-generation={own}><Classroom key={own}/></div>)
 await until(()=>document.querySelector('[data-generation]')?.getAttribute('data-generation')===String(own)&&!!document.querySelector('.skip127, .class-wait129'))
 return true
}
;(window as any).ready129=(window as any).mount129()

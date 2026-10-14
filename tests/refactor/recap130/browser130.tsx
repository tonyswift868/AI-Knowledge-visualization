import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {Classroom} from './Classroom130'
import {useApp} from '../../../refactor/learning/web/src/store'
import {calls,scenario130} from './bridge130'
import '../../../refactor/learning/web/src/theme.css'
const root=createRoot(document.getElementById('root')!)
let generation=0
const pause=()=>new Promise(r=>setTimeout(r,20))
async function until(fn:()=>boolean){for(let i=0;i<300;i++){if(fn())return;await pause()}throw Error('mount130 timed out')}
useApp.setState({planId:'a'.repeat(32),settings:{reduce_motion:true} as any})
;(window as any).calls130=calls
;(window as any).mount130=async(mode='normal')=>{
 scenario130(mode);const own=++generation
 root.render(<div style={{height:'100vh'}} data-generation={own}><Classroom key={own}/></div>)
 await until(()=>document.querySelector('[data-generation]')?.getAttribute('data-generation')===String(own)&&!!document.querySelector('.class-subpoint129'))
 return true
}
;(window as any).ready130=(window as any).mount130()

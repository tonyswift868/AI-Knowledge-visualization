import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {Classroom} from './Classroom133'
import {useApp} from '../../../refactor/learning/web/src/store'
import {scenario133} from './bridge133'
import '../../../refactor/learning/web/src/theme.css'
let generation=0
const root=createRoot(document.getElementById('root')!)
;(window as any).mount133=async(mode='teacher',reduced=false)=>{
 scenario133(mode);useApp.setState({planId:'a'.repeat(32),settings:{reduce_motion:reduced} as any})
 const own=++generation
 root.render(<div style={{height:'100vh'}} data-generation={own}><Classroom key={own}/></div>)
 for(let i=0;i<300;i++){if(document.querySelector('[data-generation]')?.getAttribute('data-generation')===String(own)&&document.querySelector('[data-turn-key="old133-11"]'))return true;await new Promise(r=>setTimeout(r,20))}
 throw Error('mount timeout')
}
;(window as any).ready133=(window as any).mount133()

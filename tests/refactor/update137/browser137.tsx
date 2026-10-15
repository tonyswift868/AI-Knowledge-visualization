import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {Settings} from '../../../refactor/learning/web/src/pages/Settings'
import {UpdateNotice,useUpdates} from '../../../refactor/learning/web/src/updates/UpdateNotice'
import {Loading,MODEL_SLOW_MESSAGE} from '../../../refactor/learning/web/src/loading/Loading'
import {useApp} from '../../../refactor/learning/web/src/store'
import {fixture} from '../app/update37_browser_bridge'
import '../../../refactor/learning/web/src/theme.css'
useApp.setState({ready:true,route:'settings',settingsTab:'time',settings:{theme:'sakura',refresh_time:'04:00',reminder_enabled:false} as any})
;(window as any).phase137=(phase:string,theme='sakura')=>{
  useApp.setState({settings:{theme,refresh_time:'04:00',reminder_enabled:false} as any})
  document.documentElement.dataset.theme=theme
  const status={available:true,busy:false,state:'UPDATE_AVAILABLE',version:'9.137.1',current:'9.137.1',latest:'9.137.2',release_notes:'本机测试：先显示版本，再下载。',message:'发现新版本 9.137.2',
    prepared:phase==='ready',background_downloading:phase!=='ready'&&phase!=='offer',download_state:phase==='download'?'DOWNLOADING':phase==='prepare'?'PREPARING':phase==='ready'?'READY_TO_APPLY':'',
    downloaded_files:phase==='download'?2:5,total_download_files:5,downloaded_bytes:phase==='download'?3*1048576:10*1048576,expected_download_bytes:10*1048576}
  fixture.status=status
  useUpdates.setState({status,open:true,manual:false,later:false})
}
createRoot(document.getElementById('root')!).render(<><Settings/><UpdateNotice/>
  <div id="slow-generic"><Loading label="正在检查更新…" started={Date.now()-16000}/></div>
  <div id="slow-model"><Loading label="老师正在想…" started={Date.now()-16000} slowMessage={MODEL_SLOW_MESSAGE}/></div>
</>)
;(window as any).ready137=true
;(window as any).initial137=()=>fixture.checks>0&&fixture.status.state!=='CHECKING'

"""UIA native-window helpers: Invoke/Value/Toggle/Close, never input emulation."""
from pathlib import Path
import subprocess, json, time, shutil, os
from common import TASK, environment
HERE=Path(__file__).parent
bundled=Path(os.environ.get('USERPROFILE',''))/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
PS=os.environ.get('MF_POWERSHELL') or shutil.which('pwsh') or (str(bundled) if bundled.is_file() else None)
if PS is None: raise RuntimeError('Set MF_POWERSHELL to the existing PowerShell 7 executable')

def uia(pid, action='tree', name='', value='', out='', wait=True):
    command=[PS,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(HERE/'uia.ps1'),
        '-TargetPid',str(pid),'-Action',action,'-Name',name,'-Value',value,'-Out',str(out)]
    if not wait:
        log=(TASK/'logs'/('uia-'+str(pid)+'-'+str(time.time_ns())+'.log')).open('w',encoding='utf8')
        try:return subprocess.Popen(command,env=environment(),stdout=log,stderr=log)
        finally:log.close()
    result=subprocess.run(command,env=environment(),capture_output=True,encoding='utf-8',timeout=35)
    if result.returncode:raise RuntimeError(result.stderr)
    return json.loads(result.stdout) if result.stdout.strip() else None

def controls(pid):
    rows=uia(pid)
    return [r for r in rows if r.get('enabled') and not r.get('offscreen')]

def wait_control(pid,name,timeout=240):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if any(r.get('name')==name for r in controls(pid)):return
        time.sleep(1)
    raise TimeoutError('UIA control not available: '+name)

def choose_folder(pid,parent):
    parent=Path(parent).resolve()
    if not parent.is_relative_to(TASK.resolve()):raise ValueError('owned folder required')
    parent.mkdir(parents=True,exist_ok=True)
    uia(pid,'invoke','选择其他位置',wait=False)
    wait_control(pid,'文件夹:',30)
    uia(pid,'value','文件夹:',str(parent))
    uia(pid,'invoke','选择文件夹')

def install(pid,parent,shot):
    choose_folder(pid,parent)
    primary=next((r['name'] for r in controls(pid) if r.get('type')=='ControlType.Button' and r['name'] in ('安装','更新','修复')),None)
    if not primary: raise RuntimeError('installation action unavailable')
    uia(pid,'invoke',primary,wait=False)
    wait_control(pid,'启动 MentorFlow',240)
    uia(pid,'shot',out=TASK/'screenshots'/shot)
    # Finish checkbox options are deliberately inspected by the caller.
    return controls(pid)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('pid',type=int);parser.add_argument('parent',type=Path);parser.add_argument('--shot',default='installer-finish.png');args=parser.parse_args()
    print(json.dumps(install(args.pid,args.parent,args.shot),ensure_ascii=False),flush=True)

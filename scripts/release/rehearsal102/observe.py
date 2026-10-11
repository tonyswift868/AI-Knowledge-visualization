"""Observe real update transactions without replacing any update function."""
from pathlib import Path
import argparse, json, time, ctypes, subprocess
from datetime import datetime
from common import TASK, environment
from native import uia
parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);parser.add_argument('old_pid',type=int);parser.add_argument('--label',default='main');parser.add_argument('--target',default='9.102.6');parser.add_argument('--timeout',type=int,default=240);args=parser.parse_args()
root=args.root.resolve()
if not root.is_relative_to(TASK.resolve()):raise ValueError('owned root required')
start=time.time();events=[];seen=set();old_exited=False;shot=False;coordinator=None
kernel=ctypes.windll.kernel32
kernel.OpenProcess.restype=ctypes.c_void_p
kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
kernel.CloseHandle.argtypes=[ctypes.c_void_p]
def exited(pid):
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return True
    code=ctypes.c_ulong();kernel.GetExitCodeProcess(handle,ctypes.byref(code));kernel.CloseHandle(handle)
    return code.value!=259
def add(kind,**fields):
    events.append(dict(event=kind,observed=time.time(),**fields))
    (TASK/'logs'/(args.label+'-observations.json')).write_text(json.dumps(events,indent=2),encoding='utf8')
while time.time()-start<args.timeout:
    for ready in root.glob('one-click-*.ready.json'):
        try:
            data=json.loads(ready.read_bytes())
            if data.get('app_pid')==args.old_pid and 'ack' not in seen:
                seen.add('ack');coordinator=data['coordinator_pid'];add('ack',file_time=ready.stat().st_mtime,**data)
        except (OSError,ValueError):pass
    if not old_exited and exited(args.old_pid):
        old_exited=True;add('old_app_exit')
    try:
        path=root/'per-user-transaction.json';journal=json.loads(path.read_bytes())
        if journal['target']==args.target:
            phase=journal['phase']
            if phase not in seen:
                seen.add(phase);add('transaction_'+phase,file_time=path.stat().st_mtime,attempt=journal['attempt'])
            sentinel=root/('staging-'+journal['attempt'])/'App/SELF-CHECK/package-manifest.json'
            if 'copy_complete' not in seen and sentinel.is_file():
                size=sentinel.stat().st_size
                if size>10000:
                    try:
                        manifest=json.loads(sentinel.read_bytes())
                        seen.add('copy_complete');add('transaction_copy_complete',file_time=sentinel.stat().st_mtime,
                            files=len(manifest['files'])+1,bytes=sum(r['size'] for r in manifest['files'])+size)
                    except (ValueError,KeyError):pass
    except (OSError,ValueError,KeyError):journal={}
    try:
        for line in (root/'Data/logs/launcher-startup.jsonl').read_text(encoding='utf8').splitlines():
            row=json.loads(line);stamp=datetime.fromisoformat(row['at']).timestamp()
            if stamp<start or row['pid']==args.old_pid:continue
            if row['phase']=='native_startup_window_visible':
                coordinator=row['pid']
                if 'native_update_window' not in seen:
                    seen.add('native_update_window');add('native_update_window',**row)
            if row['phase']=='login_window_input_ready' and old_exited:
                if 'new_window' not in seen:
                    seen.add('new_window');add('new_window_input_ready',**row)
        if coordinator and old_exited and not shot:
            shot=True
            try:uia(coordinator,'shot',out=TASK/'screenshots'/(args.label+'-updating.png'))
            except Exception as error:add('screenshot_error',detail=str(error))
        if old_exited and journal.get('phase')=='HEALTHY' and 'new_window' in seen:break
    except (OSError,ValueError,KeyError):pass
    time.sleep(.02)
print(json.dumps(events,ensure_ascii=False),flush=True)

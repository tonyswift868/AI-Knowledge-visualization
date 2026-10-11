"""Launch only the rehearsal's installed Updater target (never App directly)."""
import argparse, subprocess, json
from common import TASK, environment
parser=argparse.ArgumentParser();parser.add_argument('root');args=parser.parse_args()
from pathlib import Path
root=Path(args.root).resolve()
if not root.is_relative_to(TASK.resolve()):raise ValueError('owned preview root required')
env=environment();env.update(LOCALAPPDATA=str(TASK/'launcher-local'),
    QTWEBENGINE_REMOTE_DEBUGGING='127.0.0.1:18861',
    QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu',MF_UAT_ROOT=str(TASK))
(TASK/'launcher-local').mkdir(exist_ok=True)
p=subprocess.Popen([str(root/'Updater/UserRuntime.exe'),'launch','--root',str(root)],cwd=root,env=env)
print(json.dumps({'launcher_pid':p.pid,'root':str(root)}))

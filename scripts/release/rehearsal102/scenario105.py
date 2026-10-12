"""Real Inno/launcher/CDP: legacy A, ready B and next-start C. No OS input."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from common import TASK, APP_ID, environment
from scenario103 import snapshot, HERE
from native import uia
import scenario as s

ENV = environment()
VERSION = '9.105.2'

def read(path): return json.loads(path.read_bytes())

def offer(root):
    shutil.copy2(TASK/'release-X/stable.json', TASK/'webroot/updates/app/stable/stable.json')
    s.cdp('click', '设置')
    began = time.time()
    s.cdp('click', '检查更新')
    latency = []
    until = time.monotonic()+180
    while time.monotonic() < until:
        start = time.perf_counter()
        available = s.cdp('evaluate', "[...document.querySelectorAll('button')].some(e=>e.textContent.includes('立即更新并重启')&&!e.disabled)")
        latency.append(round(time.perf_counter()-start, 3))
        if available: break
        time.sleep(.5)
    else: raise TimeoutError('background-ready offer did not appear')
    pending = read(root/'pending-update.json')
    assert pending['target'] == VERSION
    assert ( __import__('pathlib').Path(pending['stage']).parent/'prepared-tree.json').is_file()
    return dict(seconds=round(time.time()-began, 3), cdp_response_seconds=latency)

def run_case(label, run_id=None):
    root = TASK/(run_id or label)/'MentorFlow'
    old = 'O' if label == 'A' else 'P'
    result = dict(route=label, root=str(root), source='10e625f4' if old=='O' else 'final', app_id=APP_ID)
    shutil.copy2(TASK/f'release-{old}/stable.json', TASK/'webroot/updates/app/stable/stable.json')
    try:
        s.install_setup(TASK/f'setup-{old}/MentorFlow-Setup.exe', root.parent, label)
        s.launch(root); s.login(root)
        s.cdp('click', '设置')
        s.cdp('input', 'input[type=password]', 'sk-uat-not-a-real-deepseek-key-105')
        s.cdp('click', '保存并使用'); s.cdp('wait-text', 'Key 已保存')
        s.cdp('click', '外观')
        s.cdp('evaluate', "[...document.querySelectorAll('.theme-card')].find(e=>e.textContent.includes('极光夜')).click()")
        if old == 'P': result['background'] = offer(root)
        if label != 'NEXT':
            s.update(root, TASK/'release-X/stable.json', VERSION, wait_updater=True, already_offered=old=='P')
            shutil.copy2(TASK/'logs/scenario-observations.json', TASK/f'logs/{label}-observations.json')
        else:
            # No apply API shortcut: close the real App with its signed pending
            # download, then invoke the actual installed desktop launcher.
            s.close(root)
            begin = time.time()
            s.launch(root)
            s.wait_for(lambda: read(root/'per-user-transaction.json')['phase']=='HEALTHY', 100)
            result['next_start_seconds'] = round(time.time()-begin, 3)
            s.login(root)
        s.wait_for(lambda: read(root/'updater-release.json')['signed']['version']==VERSION, 100)
        # Binding is committed before refresh's final verification/result file.
        # Do not mistake the old App's UP_TO_DATE result for this new attempt.
        s.wait_for(lambda: read(root/'updater-refresh.json').get('status')=='UPDATED'
                   and read(root/'updater-refresh.json').get('version')==VERSION,100)
        result['new'] = s.run('check.py', root, '--version', VERSION)
        result['first_refresh'] = read(root/'updater-refresh.json')
        if label != 'NEXT':
            clicks=[json.loads(line) for line in (TASK/'logs/scenarios.jsonl').read_text(encoding='utf8').splitlines()
                    if json.loads(line)['action']=='update-click-begin']
            result['click_at']=clicks[-1]['at']
            result['click_to_updater_seconds']=round((root/'updater-release.json').stat().st_mtime-result['click_at'],3)
        assert result['first_refresh']['status']=='UPDATED',result
        assert hashlib.sha256((root/'Updater/UserRuntime.exe').read_bytes()).digest()==hashlib.sha256((root/'App/UserRuntime/UserRuntime.exe').read_bytes()).digest()
        s.cdp('wait-text', '更新排练账号'); s.cdp('click', '设置'); s.cdp('wait-text', 'Key 已保存')
        result['key_retained'] = True
        result['theme_retained'] = s.cdp('evaluate', "document.documentElement.dataset.theme==='aurora'")
        assert result['theme_retained'],result
        s.cdp('shot', str(TASK/f'screenshots/{label}-new.png'))
        (TASK/f'logs/{label}-first-startup.jsonl').write_bytes((root/'Data/logs/launcher-startup.jsonl').read_bytes())
        s.close(root)
        restart = time.time(); s.launch(root)
        s.wait_for(lambda: (root/'updater-refresh.json').stat().st_mtime>restart and read(root/'updater-refresh.json')['status']=='UP_TO_DATE',100)
        s.wait_for(lambda: any(json.loads(line)['phase']=='cleanup_complete' and __import__('datetime').datetime.fromisoformat(json.loads(line)['at']).timestamp()>restart for line in (root/'Data/logs/launcher-startup.jsonl').read_text(encoding='utf8').splitlines()),100)
        result['second'] = s.run('check.py',root,'--version',VERSION)
        assert not result['second']['junk'],result
        uia(s.app_pid(root),'shot',out=TASK/f'screenshots/{label}-second.png')
        result['complete'] = True
    finally:
        if (root/'Maintenance/unins000.exe').is_file():
            subprocess.run([sys.executable,str(HERE/'scenario.py'),'uninstall','--root',str(root)],env=ENV,check=True)
        (TASK/f'logs/result105-{label}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('route',choices=['A','B','NEXT'])
    p.add_argument('--run-id',help='New isolated installation parent when resuming after a driver fix')
    args=p.parse_args()
    if args.run_id and (not args.run_id.replace('-','').isalnum()): raise ValueError('plain owned run name required')
    before = snapshot('before-'+args.route)
    log=(TASK/f'logs/servers-{args.route}.out').open('w',encoding='utf8')
    server=subprocess.Popen([sys.executable,str(HERE/'servers.py')],env=ENV,stdout=log,stderr=log)
    try: result=run_case(args.route,args.run_id)
    finally:
        server.terminate();server.wait(15);log.close()
        after=snapshot('after-'+args.route)
        assert before==after
        assert read(TASK/('before-'+args.route+'.json'))==read(TASK/('after-'+args.route+'.json'))
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__': main()

"""BUG-79: independently frozen 2.0.13 -> new App -> new Updater, then restart."""
import json
import hashlib
import shutil
import subprocess
import sys
import time
from common import TASK, APP_ID, environment
import scenario as s
from scenario103 import snapshot, HERE

env = environment()
root = TASK/'verified/MentorFlow'
version = '9.104.14'

def read(path):
    return json.loads(path.read_bytes())

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer-pid',type=int,help='Resume only this round\'s still-open native folder picker')
    args = parser.parse_args()
    before = snapshot('before')
    result = {'source_O': '10e625f4', 'new_version': version, 'app_id': APP_ID}
    log = (TASK/'logs/servers.out').open('w',encoding='utf8')
    server = subprocess.Popen([sys.executable,str(HERE/'servers.py')],env=env,stdout=log,stderr=log)
    try:
        shutil.copy2(TASK/'release-O/stable.json',TASK/'webroot/updates/app/stable/stable.json')
        if args.installer_pid:
            from native import uia,wait_control
            uia(args.installer_pid,'value','文件夹:',str(root.parent))
            uia(args.installer_pid,'invoke','选择文件夹',wait=False)
            wait_control(args.installer_pid,'安装',30)
            uia(args.installer_pid,'invoke','安装',wait=False)
            wait_control(args.installer_pid,'启动 MentorFlow',240)
            uia(args.installer_pid,'shot',out=TASK/'screenshots/old-installed.png')
            uia(args.installer_pid,'invoke','关闭')
        else:
            s.install_setup(TASK/'setup-O/MentorFlow-Setup.exe',root.parent,'old')
        s.launch(root); s.login(root)
        s.cdp('click','设置')
        s.cdp('input','input[type=password]','sk-uat-not-a-real-deepseek-key-104')
        s.cdp('click','保存并使用'); s.cdp('wait-text','Key 已保存')
        s.cdp('shot',str(TASK/'screenshots/old-key.png'))
        result['old'] = s.run('check.py',root,'--version','2.0.13')
        s.cdp('click','外观')
        s.cdp('evaluate',"[...document.querySelectorAll('.theme-card')].find(e=>e.textContent.includes('极光夜')).click()")
        s.update(root,TASK/'release-X/stable.json',version,wait_updater=True)
        result['new'] = s.run('check.py',root,'--version',version)
        s.wait_for(lambda: (root/'updater-refresh.json').is_file() and read(root/'updater-refresh.json')['status']=='UPDATED',10)
        result['first_refresh'] = read(root/'updater-refresh.json')
        assert result['first_refresh']['status']=='UPDATED',result
        result['updater_exe_matches_app'] = hashlib.sha256((root/'Updater/UserRuntime.exe').read_bytes()).digest() == hashlib.sha256((root/'App/UserRuntime/UserRuntime.exe').read_bytes()).digest()
        assert result['updater_exe_matches_app'],result
        clicks = [r for r in (json.loads(x) for x in (TASK/'logs/scenarios.jsonl').read_text(encoding='utf8').splitlines()) if r['action']=='update-click-begin']
        result['click_to_updater_seconds'] = round((root/'updater-release.json').stat().st_mtime-clicks[-1]['at'],3)
        s.cdp('wait-text','更新排练账号')
        s.cdp('click','设置'); s.cdp('wait-text','Key 已保存')
        result['key_retained'] = True
        result['theme_retained'] = s.cdp('evaluate',"document.documentElement.dataset.theme==='aurora'")
        assert result['theme_retained'],result
        s.cdp('shot',str(TASK/'screenshots/new-key.png'))
        first_logs = (root/'Data/logs/launcher-startup.jsonl').read_text(encoding='utf8')
        (TASK/'logs/first-startup.jsonl').write_text(first_logs,encoding='utf8')
        result['first_maintenance'] = [json.loads(x) for x in first_logs.splitlines() if json.loads(x)['phase'].startswith(('updater_refresh','cleanup_'))]
        s.close(root)
        restarted = time.time()
        s.launch(root)
        s.wait_for(lambda: (root/'updater-refresh.json').stat().st_mtime > restarted and read(root/'updater-refresh.json')['status']=='UP_TO_DATE',100)
        s.wait_for(lambda: any(json.loads(x)['phase']=='cleanup_complete' and __import__('datetime').datetime.fromisoformat(json.loads(x)['at']).timestamp()>restarted for x in (root/'Data/logs/launcher-startup.jsonl').read_text(encoding='utf8').splitlines()),100)
        result['second'] = s.run('check.py',root,'--version',version)
        assert not result['second']['junk'],result
        result['second_refresh'] = read(root/'updater-refresh.json')
        result['second_start_seconds'] = round(time.time()-restarted,3)
        from native import uia
        uia(s.app_pid(root),'shot',out=TASK/'screenshots/second-start.png')
        (TASK/'logs/final-startup.jsonl').write_bytes((root/'Data/logs/launcher-startup.jsonl').read_bytes())
        result['complete'] = True
    finally:
        if (root/'Maintenance/unins000.exe').exists():
            subprocess.run([sys.executable,str(HERE/'scenario.py'),'uninstall','--root',str(root)],env=env,check=True)
            result['uninstalled'] = True
        server.terminate(); server.wait(15); log.close()
        after = snapshot('after')
        result['formal_files_unchanged'] = before==after
        result['registration_shortcuts_unchanged'] = read(TASK/'before.json')==read(TASK/'after.json')
        (TASK/'logs/result104.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    assert result['formal_files_unchanged'] and result['registration_shortcuts_unchanged'],result
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__': main()

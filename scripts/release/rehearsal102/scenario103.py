"""Batch 103: real installed 2.0.13 O -> independently frozen final X.

Set MF_UAT_ROOT first. Every child inherits isolated LOCALAPPDATA/TEMP/TMP.
No bridge update shortcuts, model calls, OS input, focus or production writes.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import time

from common import TASK, environment
from native import PS
import scenario as s

ENV=environment()
HERE=Path(__file__).parent
ROOT=TASK/'verified4/MentorFlow'


def snapshot(label):
    subprocess.run([PS,'-NoProfile','-File',str(HERE/'snapshot.ps1'),'-Out',str(TASK/(label+'.json'))],env=ENV,check=True)
    paths=[Path('D:/MentorFlowApp/MentorFlow')/name for name in
           ('current-release.json','Updater/UserRuntime.exe','App/mentorflow-version.json')]
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--installer-pid',type=int);parser.add_argument('--resume',action='store_true')
    parser.add_argument('--verify-current',action='store_true',help='Inspect the already self-restarted candidate, then uninstall; never trigger a second update')
    args=parser.parse_args()
    before=snapshot('before') if not args.resume else json.loads((TASK/'formal-hashes.json').read_text(encoding='utf8'))
    (TASK/'formal-hashes.json').write_text(json.dumps(before),encoding='utf8')
    log=(TASK/'logs/servers.out').open('w',encoding='utf8')
    server=subprocess.Popen([sys.executable,str(HERE/'servers.py')],env=ENV,stdout=log,stderr=log)
    started=time.monotonic()
    result=json.loads((TASK/'logs/result103.json').read_text(encoding='utf8')) if args.verify_current else {'source_O':'10e625f4','new_version':'9.103.14','app_id':'MentorFlow.UAT.class103'}
    try:
        # Publish the matching O manifest for the first startup.
        import shutil
        shutil.copy2(TASK/('release-N' if args.verify_current else 'release-O')/'stable.json',TASK/'webroot/updates/app/stable/stable.json')
        if args.resume or args.verify_current:
            pass
        elif args.installer_pid:
            # Resume this round's still-open, not-yet-installed native wizard.
            # Its owned picker is already open after UIA discovery recovered.
            from native import uia,wait_control
            uia(args.installer_pid,'invoke','确定')
            uia(args.installer_pid,'value','文件夹:',str(ROOT.parent))
            uia(args.installer_pid,'invoke','选择文件夹',wait=False)
            wait_control(args.installer_pid,'安装',30)
            uia(args.installer_pid,'invoke','安装',wait=False)
            wait_control(args.installer_pid,'启动 MentorFlow',240)
            uia(args.installer_pid,'shot',out=TASK/'screenshots/old-installed.png')
            uia(args.installer_pid,'invoke','关闭')
        else:
            s.install_setup(TASK/'setup-O/MentorFlow-Setup.exe',ROOT.parent,'old')
        if not s.app_pid(ROOT):
            if args.verify_current:raise RuntimeError('self-restarted candidate must still be running')
            s.launch(ROOT)
        if args.resume or args.verify_current:
            try:s.cdp('wait-text','更新排练账号')
            except RuntimeError:s.login(ROOT)
        else:s.login(ROOT)
        if not args.verify_current:
            s.cdp('click','设置')
            s.cdp('input','input[type=password]','sk-uat-not-a-real-deepseek-key-103')
            s.cdp('click','保存并使用');s.cdp('wait-text','Key 已保存')
            s.cdp('shot',str(TASK/'screenshots/old-key-settings.png'))
            result['old']=s.run('check.py',ROOT,'--version','2.0.13')
            result['install_login_seconds']=round(time.monotonic()-started,2)
            old_updater=hashlib.sha256((ROOT/'Updater/UserRuntime.exe').read_bytes()).hexdigest()
            result['old_updater_sha256']=old_updater
            (TASK/'logs/result103.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            s.wait_for(lambda:(TASK/'release-N/stable.json').is_file(),240)
            update_start=time.monotonic()
            subprocess.run([sys.executable,str(HERE/'scenario.py'),'main-update','--root',str(ROOT),
                '--release',str(TASK/'release-N/stable.json'),'--target','9.103.14'],env=ENV,check=True)
            result['update_seconds']=round(time.monotonic()-update_start,2)
        result['new']=s.run('check.py',ROOT)
        assert result['new']['version']=='9.103.14' and result['new']['phase']=='HEALTHY' and not result['new']['pending'],result
        s.cdp('wait-text','更新排练账号')
        s.cdp('click','设置');s.cdp('wait-text','Key 已保存')
        result['key_retained']=True
        result['theme_retained']=s.cdp('evaluate',"document.documentElement.dataset.theme==='aurora'")
        assert result['theme_retained'],result
        result['new_ui_loaded']=s.cdp('script-contains','对照原文，自己判断理解得对不对')
        assert result['new_ui_loaded']['found'],result
        result['updater_replaced']=result['new']['updater']=='9.103.14'
        refresh=ROOT/'updater-refresh.json'
        result['updater_refresh']=json.loads(refresh.read_bytes()) if refresh.exists() else None
        s.cdp('shot',str(TASK/'screenshots/new-key-settings.png'))
        s.cdp('click','外观');s.cdp('shot',str(TASK/'screenshots/new-theme.png'))
        result['verified']=True
        result['complete']=result['updater_replaced']
    finally:
        try:
            if result.get('verified') and (ROOT/'Maintenance/unins000.exe').exists():
                subprocess.run([sys.executable,str(HERE/'scenario.py'),'uninstall','--root',str(ROOT)],env=ENV,check=True)
                result['uninstalled']=True
                result.pop('retained_for_resume',None)
            elif (ROOT/'Maintenance/unins000.exe').exists():
                result['retained_for_resume']=str(ROOT)
        finally:
            server.terminate();server.wait(timeout=15);log.close()
            after=snapshot('after')
            result['formal_files_unchanged']=before==after
            result['registration_shortcuts_unchanged']=json.loads((TASK/'before.json').read_text(encoding='utf-8-sig'))==json.loads((TASK/'after.json').read_text(encoding='utf-8-sig'))
            (TASK/'logs/result103.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    assert result['formal_files_unchanged'] and result['registration_shortcuts_unchanged'],result
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()

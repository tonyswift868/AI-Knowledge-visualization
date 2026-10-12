"""107 final gate. Real Inno, installed launcher, UIA and CDP only."""
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from common import TASK, APP_ID, environment
from native import uia, controls, wait_control, PS
from scenario103 import snapshot, HERE
import scenario as s

ENV=environment()
RESULT=TASK/'logs/result107.json'

def read(path): return json.loads(path.read_bytes())
def save(result): RESULT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
def publish(label): shutil.copy2(TASK/f'release-{label}/stable.json',TASK/'webroot/updates/app/stable/stable.json')

def setup(label,root,tag,cover=False):
    process=subprocess.Popen([str(TASK/f'setup-{label}/MentorFlow-Setup.exe'),
        '/LOG='+str(TASK/f'logs/{tag}-setup.log')],env=ENV,cwd=TASK)
    pid=s.wait_for(lambda:next((p['ProcessId'] for p in s.processes()
        if p['ParentProcessId']==process.pid and p['Name'].endswith('.tmp')),None),30)
    wait_control(pid,'选择其他位置',30)
    rows=s.wait_for(lambda:(r if any(e.get('class')=='TNewEdit' and 'value' in e for e in r) else None)
        if (r:=controls(pid)) else None,30)
    path=next(r['value'] for r in rows if r.get('class')=='TNewEdit' and 'value' in r)
    uia(pid,'shot',out=TASK/f'screenshots/{tag}-location.png')
    s.record(tag+'-location',dict(path=path,controls=rows))
    if cover:
        assert Path(path).resolve()==root.resolve(),(path,root)
        modes=[r.get('name','') for r in rows if '已安装，可' in r.get('name','')]
        assert modes,modes
        primary=next(r['name'] for r in rows if r['name'] in ('更新','修复','安装'))
        uia(pid,'invoke',primary,wait=False)
        wait_control(pid,'启动 MentorFlow',240)
        rows=controls(pid)
    else:
        assert Path(path).name=='MentorFlow' and 'Programs' in Path(path).parts,path
        rows=__import__('native').install(pid,root.parent,tag+'-installed.png')
    uia(pid,'shot',out=TASK/f'screenshots/{tag}-finish.png')
    option=next(r for r in rows if r.get('name')=='启动 MentorFlow')
    if option.get('toggle')=='On': uia(pid,'toggle','启动 MentorFlow')
    uia(pid,'invoke','关闭')
    process.wait(30)
    assert process.returncode==0,process.returncode
    return dict(default_path=path,cover=cover,mode=modes if cover else [],
        expected_upgrade_label=(any('已安装，可更新应用' in text for text in modes) if cover else None))

def key_and_theme():
    s.cdp('click','设置')
    s.cdp('input','input[type=password]','sk-uat-not-a-real-deepseek-key-107')
    s.cdp('click','保存并使用');s.cdp('wait-text','Key 已保存')
    s.cdp('click','外观')
    s.cdp('evaluate',"[...document.querySelectorAll('.theme-card')].find(e=>e.textContent.includes('极光夜')).click()")

def db_info(root):
    files=list((root/'Data').rglob('course.sqlite3'))
    assert len(files)==1,files
    with sqlite3.connect(f'{files[0].as_uri()}?mode=ro',uri=True) as conn:
        version=conn.execute('SELECT version FROM schema_version WHERE singleton=1').fetchone()[0]
        tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return dict(version=version,tables=tables)

def second_start(root,version,tag):
    s.close(root);started=time.time();s.launch(root)
    s.wait_for(lambda:(root/'updater-refresh.json').stat().st_mtime>started
        and read(root/'updater-refresh.json')['status']=='UP_TO_DATE',100)
    s.wait_for(lambda:any(json.loads(line)['phase']=='cleanup_complete'
        and datetime.fromisoformat(json.loads(line)['at']).timestamp()>started
        for line in (root/'Data/logs/launcher-startup.jsonl').read_text(encoding='utf8').splitlines()),100)
    result=s.run('check.py',root,'--version',version)
    assert not result['junk'],result
    uia(s.app_pid(root),'shot',out=TASK/f'screenshots/{tag}-second.png')
    return result

def ready(root,label,version):
    publish(label);s.cdp('click','设置');started=time.time();s.cdp('click','检查更新')
    s.wait_for(lambda:s.cdp('evaluate',"[...document.querySelectorAll('button')].some(e=>e.textContent.includes('立即更新并重启')&&!e.disabled)"),180)
    pending=read(root/'pending-update.json')
    assert pending['target']==version,pending
    assert (Path(pending['stage']).parent/'prepared-tree.json').is_file()
    return round(time.time()-started,4)

def update(root,label,version,tag,offered=False):
    s.update(root,TASK/f'release-{label}/stable.json',version,wait_updater=True,already_offered=offered)
    s.wait_for(lambda:read(root/'updater-refresh.json').get('status')=='UPDATED'
        and read(root/'updater-refresh.json').get('version')==version,100)
    events=read(TASK/'logs/scenario-observations.json')
    shutil.copy2(TASK/'logs/scenario-observations.json',TASK/f'logs/{tag}-observations.json')
    click=next(json.loads(line)['at'] for line in reversed((TASK/'logs/scenarios.jsonl').read_text(encoding='utf8').splitlines()) if json.loads(line)['action']=='update-click-begin')
    timing={e['event']:round((e.get('file_time') or (datetime.fromisoformat(e['at']).timestamp() if 'at' in e else e['observed']))-click,4) for e in events}
    s.cdp('wait-text','更新排练账号');s.cdp('click','设置');s.cdp('wait-text','Key 已保存')
    assert s.cdp('evaluate',"document.documentElement.dataset.theme==='aurora'")
    s.cdp('shot',str(TASK/f'screenshots/{tag}-new.png'))
    db=db_info(root);assert db['version']==10,db
    result=dict(check=s.run('check.py',root,'--version',version),refresh=read(root/'updater-refresh.json'),
        database=db,timing=timing,key_retained=True,theme_retained=True)
    (TASK/f'logs/{tag}-startup.jsonl').write_bytes((root/'Data/logs/launcher-startup.jsonl').read_bytes())
    return result

def uninstall(root):
    subprocess.run([sys.executable,str(HERE/'scenario.py'),'uninstall','--root',str(root)],env=ENV,check=True)

def inventory(label):
    subprocess.run([PS,'-NoProfile','-File',str(HERE.parent/'installer105/snapshot105.ps1'),
        '-Out',str(TASK/f'{label}-inventory.json')],env=ENV,check=True)
    return read(TASK/f'{label}-inventory.json')

def main():
    assert APP_ID=='MentorFlow.UAT.gate107'
    result=dict(baseline='b88ad324',app_id=APP_ID)
    before=snapshot('before');full_before=inventory('before');save(result)
    log=(TASK/'logs/servers.out').open('w',encoding='utf8')
    server=subprocess.Popen([sys.executable,str(HERE/'servers.py')],env=ENV,stdout=log,stderr=log)
    current=None
    try:
        fresh=TASK/'fresh/MentorFlow';current=fresh;publish('P')
        result['fresh_setup']=setup('P',fresh,'fresh');save(result)
        started=time.time();s.launch(fresh);wait_control(s.app_pid(fresh),'登录 MentorFlow',45)
        result['fresh_login_seconds']=round(time.time()-started,3)
        result['fresh_login_window']=uia(s.app_pid(fresh),'list')
        uia(s.app_pid(fresh),'shot',name=result['fresh_login_window'][0]['name'],out=TASK/'screenshots/fresh-login.png')
        s.register(fresh);s.login(fresh);key_and_theme()
        s.cdp('click','新建计划');s.cdp('wait-text','想学什么')
        s.cdp('shot',str(TASK/'screenshots/fresh-new-plan.png'))
        result['fresh_register_login_key_newplan']=True;save(result)
        uninstall(fresh);current=None
        root=TASK/'user/MentorFlow';current=root;publish('O')
        result['old_setup']=setup('O',root,'old');s.launch(root);s.login(root);key_and_theme()
        result['old_database']=db_info(root);save(result)
        result['old_to_P']=update(root,'P','9.107.14','old-P');save(result)
        result['P_second']=second_start(root,'9.107.14','P');s.close(root)
        # Closed SQLite + one user-owned marker prove program upgrade preserves data.
        marker=root/'Data/uat-gate107.txt';marker.write_text('gate107 learning data stays',encoding='utf8')
        fixture={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (marker,*list((root/'Data').rglob('course.sqlite3')))}
        result['cover']=setup('P',root,'cover',cover=True)
        result['cover_data_unchanged']=all(hashlib.sha256((root/p).read_bytes()).hexdigest()==sha for p,sha in fixture.items())
        assert result['cover_data_unchanged']
        result['cover_check']=s.run('check.py',root,'--version','9.107.14');save(result)
        s.launch(root);s.login(root)
        result['background_Q_seconds']=ready(root,'Q','9.107.15');save(result)
        result['P_to_Q']=update(root,'Q','9.107.15','P-Q',offered=True);save(result)
        result['Q_second']=second_start(root,'9.107.15','Q');save(result)
        assert result['P_to_Q']['timing']['new_window_input_ready']<=30,result['P_to_Q']['timing']
        result['complete']=True
    finally:
        if current and (current/'Maintenance/unins000.exe').is_file():
            try:uninstall(current)
            except Exception as error:result['uninstall_error']=str(error)
        server.terminate();server.wait(15);log.close()
        after=snapshot('after')
        full_after=inventory('after')
        result['formal_full_inventory_unchanged']=full_before==full_after
        result['formal_file_count']=len(full_before['formalFiles'])
        result['default_location_unchanged']=full_before['defaultFiles']==full_after['defaultFiles']
        result['formal_files_unchanged']=before==after
        result['registry_shortcuts_unchanged']=read(TASK/'before.json')==read(TASK/'after.json')
        save(result)
    assert result.get('complete') and result['cover']['expected_upgrade_label'] and result['formal_files_unchanged'] and result['registry_shortcuts_unchanged'] and result['formal_full_inventory_unchanged'],result
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()

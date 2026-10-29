"""Final signed localhost 239-MF04 consent and pending-resume rehearsal."""
from pathlib import Path
import argparse, hashlib, json, os, subprocess, sys, time
from common import TASK, APP_ID, environment
environment()
HERE=Path(__file__).parent
sys.path.insert(0,str(HERE))
import scenario as base
from native import controls, uia, wait_control

RELEASES={'X':'X239-lock-final','O':'Y-optional239-lock-final','M':'Y-required239-lock-final'}
SETUP=TASK/'setup-X239-lock-final/MentorFlow-Setup.exe'

def log(event,**data):
    row={'at':time.time(),'event':event,**data}
    with (TASK/'logs/scenario239.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')

def assert_local():
    hits=[]
    for p in TASK.rglob('mentorflow.log'):
        if p.is_file():
            with p.open(encoding='utf8',errors='replace') as stream:
                if any('UAT_LOCAL_ONLY' in line for line in stream):hits.append(str(p))
    if hits:raise RuntimeError('localhost boundary blocked unexpected request: '+repr(hits))

def publish(kind):
    label=RELEASES[kind];raw=(TASK/f'release-{label}/stable.json').read_bytes();e=json.loads(raw);s=e['signed']
    d=TASK/'webroot/updates/app/stable';d.mkdir(parents=True,exist_ok=True)
    pointer={'version':s['version'],'metadata_version':s['metadata_version'],'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}
    for p,b in ((d/'stable.json',raw),(d/'latest.json',json.dumps(pointer,separators=(',',':')).encode())):
        q=p.with_suffix(p.suffix+'.new');q.write_bytes(b);os.replace(q,p)
    log('publish',label=label,version=s['version'],mandatory=s['mandatory'])
    return s

def install_x(root):
    root=root.resolve()
    if root.name!='MentorFlow' or not root.is_relative_to(TASK.resolve()) or root.exists():raise ValueError('fresh task-owned install root required')
    payload=TASK/'freeze-X239-lock-final/app/MentorFlow';release=TASK/'release-X239-lock-final/stable.json'
    root.parent.mkdir(parents=True,exist_ok=True);result_file=TASK/'logs'/(root.parent.name+'-install239.json')
    p=subprocess.run([str(payload/'UserRuntime/UserRuntime.exe'),'install','--root',str(root),'--source',str(payload),'--release',str(release),'--close-after-health','--result',str(result_file)],env=environment(),cwd=TASK,capture_output=True,timeout=600)
    (TASK/'logs'/(root.parent.name+'-install239.stdout.txt')).write_text((p.stdout or b'').decode('utf8','replace'),encoding='utf8')
    (TASK/'logs'/(root.parent.name+'-install239.stderr.txt')).write_text((p.stderr or b'').decode('utf8','replace'),encoding='utf8')
    if p.returncode:raise RuntimeError('real UserRuntime install failed: '+(p.stderr or b'').decode('utf8','replace')[-2000:])
    result=json.loads(result_file.read_bytes())
    if result.get('status')!='HEALTHY':raise RuntimeError('X install not HEALTHY: '+repr(result))
    log('installed',root=str(root),result=result)

def logrows(root):
    p=root/'Data/logs/launcher-startup.jsonl';rows=[]
    if p.is_file():
        with p.open(encoding='utf8',errors='replace') as stream:
            for line in stream:
                try:rows.append(json.loads(line))
                except ValueError:pass
    return rows

def product_events(root):
    p=root/'Data/logs/mentorflow.log';rows=[]
    if p.is_file():
        with p.open(encoding='utf8',errors='replace') as stream:
            for line in stream:
                try:rows.append(json.loads(line))
                except ValueError:pass
    return rows

def wait_main_or_login(root,pid,timeout=120):
    return base.wait_for(lambda: (
        {'kind':'login','controls':[x.get('name') for x in controls(pid)]}
        if any(x.get('name')=='登录 MentorFlow' for x in controls(pid)) else
        {'kind':'main','targets':base.cdp('targets')}
        if any('index.html' in x.get('url','') for x in base.cdp('targets')) else None),timeout)

def wait_target(root,target,old=None,timeout=300,wall_start=None):
    start=time.monotonic(); wall_start=wall_start if wall_start is not None else time.time()
    base.wait_for(lambda:base.app_pid(root) not in (None,old),timeout)
    pid=base.app_pid(root)
    base.wait_for(lambda:(root/'per-user-transaction.json').is_file() and json.loads((root/'per-user-transaction.json').read_bytes()).get('phase')=='HEALTHY',90)
    base.wait_for(lambda:not (root/'pending-update.json').exists(),30)
    base.wait_for(lambda:json.loads((root/'current-release.json').read_bytes()).get('signed',{}).get('version')==target,30)
    ready=wait_main_or_login(root,pid,120)
    stages=[r for r in product_events(root) if r.get('event')=='update' and r.get('phase')=='install_stage_finished']
    startup=[]
    for row in logrows(root):
        if row.get('phase') not in ('login_window_input_ready','app_foreground_requested','foreground_attention_requested','native_startup_window_visible'):
            continue
        try:
            stamp=__import__('datetime').datetime.fromisoformat(row['at']).timestamp()
        except (KeyError,TypeError,ValueError):continue
        if stamp>=wall_start-1 and row.get('pid')==pid:
            startup.append(row)
    attention=any(r.get('phase')=='foreground_attention_requested' for r in startup) or any(r.get('phase')=='app_foreground_requested' and (r.get('set_foreground_result') or r.get('foreground_after')==r.get('hwnd')) for r in startup)
    assert attention,{'new_pid':pid,'startup_events':startup}
    log('update_healthy',old_pid=old,new_pid=pid,target=target,seconds=time.monotonic()-start,new_window_ready=ready,stage_rows=stages,startup_attention=startup,attention_observed=attention)
    return pid

def ensure_main(root):
    pid=base.launch(root)
    if any(r.get('name')=='登录 MentorFlow' for r in controls(pid)):base.login(root)
    else:base.cdp('wait-text','更新排练账号',target='learning')
    return pid

def optional(root, resume=False):
    publish('X')
    if resume:
        actual=json.loads((root/'current-release.json').read_bytes())['signed']['version']
        if actual!='9.239.1':raise RuntimeError('resume root is not untouched X: '+actual)
    else:install_x(root)
    pid=ensure_main(root)
    publish('O')
    base.cdp('click','设置');base.cdp('click','检查更新')
    base.wait_for(lambda:(root/'pending-update.json').is_file(),240)
    pending=json.loads((root/'pending-update.json').read_bytes())
    assert pending.get('target')=='9.239.2' and 'user_authorization' not in pending,pending
    base.cdp('wait-text','发现新版本 9.239.2',target='learning')
    ui_state=base.cdp('evaluate',"({modal:!!document.querySelector('[role=dialog][aria-modal=\"true\"]'),inline:(()=>{const e=document.querySelector('aside[aria-label=\"软件更新提示\"]');if(!e)return false;const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth})()})",target='learning')
    assert ui_state=={'modal':False,'inline':True},ui_state
    base.cdp('shot',str(TASK/'screenshots/optional-inline-before-reopen.png'),target='learning')
    base.close(root)
    pid=base.launch(root)
    new_state=wait_main_or_login(root,pid,120)
    if new_state['kind']=='login':base.login(root)
    base.cdp('wait-text','发现新版本 9.239.2',target='learning')
    ui_state=base.cdp('evaluate',"({modal:!!document.querySelector('[role=dialog][aria-modal=\"true\"]'),inline:(()=>{const e=document.querySelector('aside[aria-label=\"软件更新提示\"]');if(!e)return false;const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth})()})",target='learning')
    assert ui_state=={'modal':False,'inline':True},ui_state
    current=json.loads((root/'current-release.json').read_bytes())['signed']['version']
    assert current=='9.239.1',current
    pending_after_reopen=json.loads((root/'pending-update.json').read_bytes())
    assert pending_after_reopen.get('target')=='9.239.2' and 'user_authorization' not in pending_after_reopen,pending_after_reopen
    base.cdp('shot',str(TASK/'screenshots/optional-inline-after-reopen.png'),target='learning')
    began=time.monotonic();clicked=base.cdp('click','立即更新',target='learning')
    newpid=wait_target(root,'9.239.2',pid,wall_start=clicked['at']/1000)
    ready_rows=[r for r in logrows(root) if r.get('pid')==newpid and r.get('phase')=='login_window_input_ready']
    click_to_ready=(__import__('datetime').datetime.fromisoformat(ready_rows[-1]['at']).timestamp()-clicked['at']/1000) if ready_rows else None
    auth=json.loads((root/'pending-update.json').read_bytes()) if (root/'pending-update.json').exists() else None
    assert_local();return {'old_version_before_consent':current,'no_click_reopen_kept_old':True,'prepared_without_authorization':True,'pending_after_reopen_without_authorization':pending_after_reopen,'no_modal_inline_before_and_after_reopen':True,'seconds_after_explicit_consent':time.monotonic()-began,'cdp_click_to_new_login_ready_seconds':click_to_ready,'post_update_pending':auth}

def mandatory(root,block=False):
    publish('X');install_x(root);publish('M')
    delay=TASK/'logs/metadata-delay.json';delay.write_text('{"seconds":3}',encoding='utf8')
    base.record('launch',base.run('launch.py',root));pid=base.wait_for(lambda:base.app_pid(root))
    started=time.monotonic()
    base.wait_control(pid,'立即更新',120)
    names=[x.get('name') for x in controls(pid)]
    assert '稍后' not in names and ('立即更新' in names or '立即更新并重启' in names),names
    all_controls=uia(pid,'tree')
    login_buttons=[x for x in all_controls if x.get('name')=='登录 MentorFlow']
    assert login_buttons and all(not x.get('enabled') for x in login_buttons),login_buttons
    base.uia(pid,'shot',out=TASK/('screenshots/mandatory-'+('c' if block else 'b')+'.png'))
    gate_seconds=time.monotonic()-started
    log('mandatory_gate_ready',pid=pid,seconds=gate_seconds,actions=names)
    delay.unlink(missing_ok=True)
    pending_ready=None;prepared_seconds=None
    if block:
        prepare_started=time.monotonic()
        base.wait_for(lambda:(root/'pending-update.json').is_file() and
            'user_authorization' not in json.loads((root/'pending-update.json').read_bytes()),240)
        pending_ready=json.loads((root/'pending-update.json').read_bytes())
        assert pending_ready.get('target')=='9.239.2' and pending_ready.get('state')=='UPDATE_DOWNLOADED',pending_ready
        prepared_seconds=time.monotonic()-prepare_started
        log('mandatory_offer_prepared_unconsented',pid=pid,seconds=prepared_seconds,pending=pending_ready)
        base.uia(pid,'shot','MentorFlow 更新',out=TASK/'screenshots/mandatory-prepared-c.png')
    else:
        # Preserve the real race: click while signed background preparation is
        # still running, as soon as the mandatory login gate becomes actionable.
        base.uia(pid,'shot','MentorFlow 更新',out=TASK/'screenshots/mandatory-prepared-b.png')
    if not block:
        btn='立即更新并重启' if '立即更新并重启' in names else '立即更新'
        click_wall=time.time();began=time.monotonic();base.uia(pid,'invoke',btn,wait=False);newpid=wait_target(root,'9.239.2',pid,wall_start=click_wall)
        ready_rows=[r for r in logrows(root) if r.get('pid')==newpid and r.get('phase')=='login_window_input_ready']
        click_to_ready=(__import__('datetime').datetime.fromisoformat(ready_rows[-1]['at']).timestamp()-click_wall) if ready_rows else None
        assert_local();return {'gate_seconds':gate_seconds,'prepared_after_gate_seconds':prepared_seconds,'click_to_healthy_seconds':time.monotonic()-began,'click_to_new_login_ready_seconds':click_to_ready,'buttons':names,'mandatory_no_defer':True}
    click_wall=time.time();click_started=time.monotonic();(TASK/'save-block-once').write_text('test-only exit fault',encoding='utf8')
    btn='立即更新并重启' if '立即更新并重启' in names else '立即更新'
    base.uia(pid,'invoke',btn,wait=False)
    # The local test hook blocks one shutdown attempt; wait for the product's real close affordance.
    try:
        base.wait_control(pid,'关闭软件',100);base.uia(pid,'shot','MentorFlow 更新',out=TASK/'screenshots/pending-cancelled239.png')
        base.uia(pid,'invoke','关闭软件',wait=False)
    except TimeoutError:
        # Observe the real app state; if product exposes a normal native close, use that user action.
        if base.app_pid(root)!=pid:raise
        log('blocked_shutdown_close_control_absent',controls=[x.get('name') for x in controls(pid)])
        raise
    base.wait_for(lambda:base.app_pid(root) is None,60)
    assert (root/'pending-update.json').is_file(),'pending update was lost when shutdown was blocked'
    relaunch_wall=time.time();relaunch_started=time.monotonic();base.launch(root)
    newpid=wait_target(root,'9.239.2',None,wall_start=relaunch_wall)
    attention=[r for r in logrows(root) if r.get('pid')==newpid and r.get('phase') in ('foreground_attention_requested','app_foreground_requested')]
    attention_ok=any(r.get('phase')=='foreground_attention_requested' for r in attention) or any(r.get('phase')=='app_foreground_requested' and (r.get('set_foreground_result') or r.get('foreground_after')==r.get('hwnd')) for r in attention)
    assert attention_ok,attention
    ready_rows=[r for r in logrows(root) if r.get('pid')==newpid and r.get('phase')=='login_window_input_ready']
    click_to_ready=(__import__('datetime').datetime.fromisoformat(ready_rows[-1]['at']).timestamp()-click_wall) if ready_rows else None
    relaunch_to_ready=(__import__('datetime').datetime.fromisoformat(ready_rows[-1]['at']).timestamp()-relaunch_wall) if ready_rows else None
    assert_local();return {'pending_survived_blocked_shutdown':True,'pending_before_explicit_consent':pending_ready,'new_pid':newpid,'gate_seconds':gate_seconds,'prepared_after_gate_seconds':prepared_seconds,'blocked_attempt_seconds':relaunch_started-click_started,'relaunch_to_healthy_seconds':time.monotonic()-relaunch_started,'relaunch_to_new_login_ready_seconds':relaunch_to_ready,'click_to_relaunch_login_ready_seconds':click_to_ready,'attention_events':attention,'install_stage_events':[r for r in product_events(root) if r.get('event')=='update' and r.get('phase') in ('install_stage_started','install_stage_finished')]}

def main():
    p=argparse.ArgumentParser();p.add_argument('case',choices=('a','b','c'));p.add_argument('--resume-a',action='store_true');p.add_argument('--run-id',default='0');args=p.parse_args()
    root=TASK/(args.case+args.run_id)/'MentorFlow'
    if args.case=='a':result=optional(root,resume=args.resume_a)
    elif args.case=='b':result=mandatory(root)
    else:result=mandatory(root,block=True)
    print(json.dumps({'case':args.case,'result':result},ensure_ascii=False))
if __name__=='__main__':main()

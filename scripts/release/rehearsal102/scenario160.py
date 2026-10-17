"""Real frozen-package UAT for BUG-109; UIA Invoke and local CDP only."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

if not os.environ.get('MF_UAT_ROOT'):
    raise SystemExit('MF_UAT_ROOT is required for 160 rehearsal; refusing the legacy default directory')
if os.environ.get('MF_UAT_APP_ID') != 'MentorFlow.UAT.gate160':
    raise SystemExit('MF_UAT_APP_ID must be MentorFlow.UAT.gate160')

from common import TASK, APP_ID, environment

environment()
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import scenario as base
import native as ui

NODE = Path('D:/MentorFlow/.local/node/node.exe')
UPDATE_ROOT = TASK/'webroot/updates/app'
STABLE_DIR = UPDATE_ROOT/'stable'


def log(event, **details):
    row = {'at': time.time(), 'event': event, **details}
    with (TASK/'logs/scenario160.jsonl').open('a', encoding='utf8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False)+'\n')


def assert_local_only():
    for path in TASK.rglob('mentorflow.log'):
        if 'UAT_LOCAL_ONLY' in path.read_text(encoding='utf8', errors='replace'):
            raise RuntimeError('non-local HTTP was blocked by the frozen rehearsal hook: '+str(path))


def publish(label):
    release_label = {
        'X': 'X-release160b',
        'Y-required': 'Y-required-release160',
        'Y-classroom': 'Y-required-release160',
        'Y-classroom3': 'Y-required-release160',
        'Y-optional': 'Y-optional-release160',
    }.get(label, label)
    raw = (TASK/('release-'+release_label)/'stable.json').read_bytes()
    envelope = json.loads(raw)
    signed = envelope['signed']
    STABLE_DIR.mkdir(parents=True, exist_ok=True)
    pointer = {'version': signed['version'], 'metadata_version': signed['metadata_version'],
               'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
    for target, content in ((STABLE_DIR/'stable.json', raw),
                            (STABLE_DIR/'latest.json', json.dumps(pointer, separators=(',', ':')).encode())):
        temporary = target.with_name(target.name+'.new')
        temporary.write_bytes(content)
        os.replace(temporary, target)
    log('publish', label=label, version=signed['version'], mandatory=signed['mandatory'])
    return signed


def set_delay(seconds=0):
    delay = TASK/'logs/metadata-delay.json'
    if seconds:
        delay.write_text(json.dumps({'seconds': seconds}), encoding='utf8')
    else:
        delay.unlink(missing_ok=True)


def app_cache_witness(root):
    identity = hashlib.sha256(os.path.normcase(str(root.resolve())).encode('utf8')).hexdigest()
    path = root.parent/'.MentorFlow-runtime'/APP_ID/'Updates'/identity/'highest-metadata-stable.json'
    return path if path.is_file() and path.stat().st_size < 3_000_000 else None


def wait_for_cache(root):
    return base.wait_for(lambda: app_cache_witness(root), 90)


def ensure_install(root, setup):
    root = root.resolve()
    if not root.is_relative_to(TASK.resolve()) or root.name != 'MentorFlow':
        raise ValueError('task-owned installation path required')
    if not root.exists():
        base.install_setup(setup, root.parent, 'scenario160-'+root.parent.name, linger_ok=True)
    return root


def cache_x(root, setup):
    publish('X')
    ensure_install(root, setup)
    pid = base.launch(root)
    witness = wait_for_cache(root)
    cached = json.loads(witness.read_bytes())
    log('verified_x_cache', root=str(root), witness=str(witness), signed_version=cached.get('signed', {}).get('version'))
    base.close(root)
    assert_local_only()


def startup_log(root):
    path = root/'Data/logs/launcher-startup.jsonl'
    if not path.is_file():
        return []
    values = []
    for line in path.read_text(encoding='utf8', errors='replace').splitlines():
        try:
            values.append(json.loads(line))
        except ValueError:
            continue
    return values


def wait_log(root, predicate, timeout=90):
    return base.wait_for(lambda: next((row for row in startup_log(root) if predicate(row)), None), timeout)


def action_names(pid):
    return [row.get('name') for row in base.controls(pid)]


def invoke_login_while_pending(root, *, optional=False):
    existing = base.app_pid(root)
    if existing:
        raise RuntimeError(f'task-owned App is already running before login scenario: {existing}')
    base.record('launch', base.run('launch.py', root))
    pid = base.wait_for(lambda: base.app_pid(root))
    driver_started = time.monotonic()
    action = ui.login(pid, 'uat_runtime_102c')
    log('login_submit_ui_action', pid=pid, requested_at=time.time(),
        uia_submitted_epoch_ms=(action or {}).get('time'),
        driver_elapsed_ms=(time.monotonic()-driver_started)*1000,
        startup_rows=[row for row in startup_log(root) if row.get('pid') == pid])
    started = time.monotonic()
    checking = base.wait_for(lambda: any('检查' in row.get('name', '') for row in base.controls(pid)), 12)
    base.uia(pid, 'shot', out=TASK/'screenshots/login-checking160.png')
    log('login_submitted_while_check_pending', pid=pid, seconds_to_checking=time.monotonic()-started)
    if optional:
        base.wait_control(pid, '稍后', 45)
        names = action_names(pid)
        assert '稍后' in names, names
        base.uia(pid, 'shot', 'MentorFlow 更新', out=TASK/'screenshots/optional-update-login160.png')
        base.uia(pid, 'invoke', '稍后')
        base.cdp('wait-text', '更新排练账号', target='learning')
        result = {'pid': pid, 'elapsed_to_choice_seconds': time.monotonic()-started,
                  'login_continued_after_later': True, 'dialog_actions': names}
        log('optional_deferred_then_login', **result)
        return pid, result
    base.wait_control(pid, '立即更新', 90)
    names = action_names(pid)
    assert '稍后' not in names, names
    assert any('必须' in x or '数据兼容' in x or '更新' in x for x in names), names
    base.uia(pid, 'shot', 'MentorFlow 更新', out=TASK/'screenshots/mandatory-login160.png')
    result = {'pid': pid, 'elapsed_to_mandatory_seconds': time.monotonic()-started,
              'login_checking_seen': bool(checking), 'later_absent': True, 'dialog_actions': names}
    log('mandatory_gate_shown', **result)
    return pid, result


def update_via_login(root, target='9.160.2'):
    old = base.app_pid(root)
    if not old:
        raise RuntimeError('owned app is not running')
    began = time.monotonic()
    observer_log = TASK/'logs'/'login-update-observer160.out'
    with observer_log.open('w', encoding='utf8') as output:
        observer = subprocess.Popen([sys.executable, str(HERE/'observe.py'), str(root), str(old),
            '--label', 'login160', '--target', target, '--timeout', '300'], env=environment(),
            stdout=output, stderr=output)
        base.uia(old, 'invoke', '立即更新', wait=False)
        base.wait_for(lambda: base.app_pid(root) not in (None, old), 300)
        new_pid = base.app_pid(root)
        base.wait_control(new_pid, '登录 MentorFlow', 120)
        base.wait_for(lambda: json.loads((root/'per-user-transaction.json').read_bytes()).get('phase') == 'HEALTHY', 60)
        base.wait_for(lambda: not (root/'pending-update.json').exists(), 30)
        result = {'target': target, 'old_pid': old, 'new_pid': new_pid,
                  'seconds_to_new_login': time.monotonic()-began,
                  'transaction': json.loads((root/'per-user-transaction.json').read_bytes())}
        log('login_gate_update_complete', **result)
        observer.wait(timeout=310)
    assert_local_only()
    return result


def offline_cached(root, setup):
    witness = app_cache_witness(root)
    if not witness:
        raise RuntimeError('offline test requires a previously verified signed cache')
    if base.app_pid(root):
        base.close(root)
    (TASK/'logs/update-feed-offline').write_text('offline', encoding='utf8')
    try:
        pid = base.launch(root)
        base.wait_control(pid, '登录 MentorFlow', 45)
        began = time.monotonic()
        base.uia(pid, 'value', '账号', 'uat_runtime_102c')
        base.uia(pid, 'value', '密码', 'UAT-only-password102c')
        base.uia(pid, 'invoke', '登录 MentorFlow', wait=False)
        result = wait_log(root, lambda row: row.get('pid') == pid and row.get('phase') == 'startup_login_gate'
                          and row.get('outcome') == 'offline_fallback', 35)
        base.cdp('wait-text', '更新排练账号', target='learning')
        base.cdp('shot', str(TASK/'screenshots/offline-cached-login160.png'))
        row = {'elapsed_to_offline_decision': result.get('elapsed_ms'),
               'seconds_to_main': time.monotonic()-began,
               'cached_state': result.get('state'), 'login_allowed': True}
        log('offline_cached_policy_allowed_login', **row)
        base.close(root)
        assert_local_only()
        return row
    finally:
        (TASK/'logs/update-feed-offline').unlink(missing_ok=True)


def request_check_through_settings_then_return_to_classroom():
    """Start the check through Settings' real React action, then return during
    the localhost-only metadata delay so its eventual result lands in class.

    No second QWebChannel is created: the page's own bridge/checkUpdate path
    owns requests and feeds the result into UpdateNotice's Zustand store.
    """
    base.cdp('click', '设置', target='learning')
    base.cdp('wait-text', '检查更新', target='learning')
    base.cdp('click', '检查更新', target='learning')
    base.cdp('click', '首页', target='learning')
    base.cdp('wait-text', '今天全部计划', target='learning')
    base.cdp('click', '我的计划', target='learning')
    base.cdp('wait-text', '离线重切验证', target='learning')
    base.cdp('click', '开课', target='learning')
    base.cdp('wait-text', '展开知识点', target='learning')


def classroom_mandatory(root, setup):
    pid = base.app_pid(root)
    if pid:
        base.close(root)
    # A failed artificial-delay run must not leak its feed policy or delay into
    # the next launch and occupy the update worker before the intended check.
    set_delay(0)
    publish('X')
    import scenario132
    from refactor.desktop.account_identity import AccountIdentity
    from refactor.learning.course.store import CourseStore
    identity = AccountIdentity('https://localhost:18764/test-realm', 'test-update-user', '更新排练账号')
    plan_id = '13200000000000000000000000000001'
    with CourseStore(identity.account_id, root/'Data') as store:
        try:
            existing_plan = store.get_plan(plan_id)
        except Exception as error:
            if getattr(error, 'code', None) != 'NOT_FOUND':
                raise
            existing_plan = None
    fixture = ({'existing': True, 'plan_id': plan_id} if existing_plan else
               scenario132.seed(root, restructured=False))
    # Seed before the fresh App process mounts Home, so its first plan query
    # sees the fixture rather than a stale empty dashboard snapshot.
    pid = base.launch(root)
    if any(row.get('name') == '登录 MentorFlow' for row in base.controls(pid)):
        base.login(root)
    else:
        base.cdp('wait-text', '更新排练账号', target='learning')
    wait_log(root, lambda row: row.get('pid') == pid
             and row.get('phase') == 'startup_login_gate'
             and row.get('outcome') == 'ready', 90)
    # The app's initial Home page has already queried an empty plan list before
    # this fixture is inserted. Start the real update check from Settings, then
    # enter the seeded plan through the actual Plans page while the localhost
    # metadata response is delayed.
    publish('Y-classroom')
    # Keep each request below the existing five-second pointer/idle limit;
    # two metadata requests still leave time to return to the actual class UI.
    set_delay(3)
    began = time.monotonic()
    request_check_through_settings_then_return_to_classroom()
    base.cdp('shot', str(TASK/'screenshots/classroom-before-mandatory160.png'))
    base.cdp('input', 'textarea[aria-label="课堂输入"]', '160-MF04 未发送课堂草稿')
    draft_key = "'mf.classroom.unsent.'+encodeURIComponent('更新排练账号')+'.'+encodeURIComponent('"+plan_id+"')"
    draft_before = base.cdp('evaluate', 'localStorage.getItem('+draft_key+')', target='learning')
    assert draft_before == '160-MF04 未发送课堂草稿', draft_before
    base.cdp('shot', str(TASK/'screenshots/classroom-draft160.png'))

    base.cdp('wait-text', 'MentorFlow 更新', target='learning')
    elapsed = time.monotonic()-began
    draft_after = base.cdp('evaluate', 'localStorage.getItem('+draft_key+')', target='learning')
    assert draft_after == draft_before, (draft_before, draft_after)
    dialog = base.cdp('evaluate', "(()=>{const d=[...document.querySelectorAll('[role=dialog]')].find(x=>x.innerText.includes('必须安装'));return d?.innerText||''})()", target='learning')
    assert '稍后' not in dialog and '立即更新' in dialog, dialog
    base.cdp('shot', str(TASK/'screenshots/classroom-mandatory160.png'))
    record = {'plan_id': plan_id, 'seed': fixture, 'seconds_to_guard_and_prompt': elapsed,
              'draft_preserved': True, 'mandatory_prompt': dialog[:700]}
    log('classroom_progress_saved_before_mandatory_prompt', **record)
    base.cdp('click', '立即更新', target='learning')
    old = pid
    base.wait_for(lambda: base.app_pid(root) not in (None, old), 300)
    new_pid = base.app_pid(root)
    base.wait_for(lambda: json.loads((root/'per-user-transaction.json').read_bytes()).get('phase') == 'HEALTHY', 60)
    base.wait_for(lambda: not (root/'pending-update.json').exists(), 30)
    # The packaged Qt WebEngine profile is off-the-record, so a localStorage
    # comparison before process exit is insufficient. Re-enter the same local
    # classroom after the real app restart and verify the durable account draft
    # was restored into its textarea.
    if any(row.get('name') == '登录 MentorFlow' for row in base.controls(new_pid)):
        base.login(root)
    else:
        base.cdp('wait-text', '更新排练账号', target='learning')
    base.cdp('wait-text', '今天全部计划', target='learning')
    base.cdp('click', '开课', target='learning')
    base.cdp('wait-text', '展开知识点', target='learning')
    restored_draft = base.cdp('evaluate', "document.querySelector('textarea')?.value || ''", target='learning')
    assert restored_draft == '160-MF04 未发送课堂草稿', restored_draft
    result = {**record, 'update_completed': True, 'seconds_to_new_window': time.monotonic()-began,
              'new_pid': new_pid, 'draft_restored_after_restart': True}
    log('classroom_mandatory_update_complete', **result)
    assert_local_only()
    return result


def no_update_timing(root):
    publish('X')
    pid = base.launch(root)
    wait_for_cache(root)
    base.close(root)
    pid = base.launch(root)
    base.wait_control(pid, '登录 MentorFlow', 45)
    base.wait_for(lambda: any(r.get('pid') == pid and r.get('phase') == 'update_check_stage'
        and r.get('stage') == 'check_total' for r in startup_log(root)), 90)
    stages = [r for r in startup_log(root)
        if r.get('pid') == pid and r.get('phase') == 'update_check_stage']
    result = {'stages': stages, 'latest_pointer_requested': any(
        r.get('path', '').endswith('/latest.json') for r in http_rows())}
    log('no_update_timing', **result)
    base.close(root)
    return result


def http_rows():
    path = TASK/'logs/http.jsonl'
    if not path.is_file(): return []
    rows=[]
    for line in path.read_text(encoding='utf8', errors='replace').splitlines():
        try: rows.append(json.loads(line))
        except ValueError: pass
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('core-install', 'warm-cache', 'mandatory-login', 'offline-cache',
        'classroom-mandatory', 'optional-login', 'no-update-timing'))
    parser.add_argument('--root', type=Path, default=TASK/'install160/MentorFlow')
    parser.add_argument('--setup', type=Path, default=TASK/'setup-X-release160b/MentorFlow-Setup.exe')
    args = parser.parse_args()
    root = args.root.resolve()
    setup = args.setup.resolve()
    result = None
    if args.action == 'core-install':
        publish('X')
        root.parent.mkdir(parents=True, exist_ok=True)
        source = TASK/'freeze-X-release160b/app/MentorFlow'
        release = TASK/'release-X-release160b/stable.json'
        result_path = TASK/'logs'/(root.parent.name+'-install.json')
        result = subprocess.run([str(source/'UserRuntime/UserRuntime.exe'), 'install',
            '--root', str(root), '--source', str(source), '--release', str(release),
            '--close-after-health', '--result', str(result_path)], env=environment(),
            cwd=TASK, capture_output=True, timeout=600)
        stdout = (result.stdout or b'').decode('utf8', errors='replace')
        stderr = (result.stderr or b'').decode('utf8', errors='replace')
        (TASK/'logs'/(root.parent.name+'-install.stdout.txt')).write_text(stdout, encoding='utf8')
        (TASK/'logs'/(root.parent.name+'-install.stderr.txt')).write_text(stderr, encoding='utf8')
        if result.returncode:
            raise RuntimeError(f'core install failed ({result.returncode}): {stderr[-2000:]}')
        install = json.loads(result_path.read_bytes())
        if install.get('status') != 'HEALTHY':
            raise RuntimeError(f'core install did not reach HEALTHY: {install}')
        log('core_install_healthy', root=str(root), version=install.get('version'),
            updater=install.get('updater'))
        result = {'status': install.get('status'), 'version': install.get('version'),
                  'updater': install.get('updater'), 'root': str(root)}
    elif args.action == 'warm-cache': cache_x(root, setup)
    elif args.action == 'mandatory-login':
        publish('Y-required'); set_delay(3)
        _pid, evidence = invoke_login_while_pending(root)
        result = update_via_login(root)
        result['gate'] = evidence
    elif args.action == 'offline-cache': result = offline_cached(root, setup)
    elif args.action == 'classroom-mandatory': result = classroom_mandatory(root, setup)
    elif args.action == 'optional-login':
        if not app_cache_witness(root):
            raise RuntimeError('optional update test requires a verified signed cache')
        if base.app_pid(root):
            base.close(root)
        publish('Y-optional'); set_delay(5)
        _pid, result = invoke_login_while_pending(root, optional=True)
        assert_local_only()
    elif args.action == 'no-update-timing': result = no_update_timing(root)
    set_delay(0)
    print(json.dumps({'action': args.action, 'result': result}, ensure_ascii=False))


if __name__ == '__main__': main()





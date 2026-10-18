"""Real frozen App/Updater login-gate scenarios for BUG-114/110/111."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

if not os.environ.get('MF_UAT_ROOT'):
    raise SystemExit('MF_UAT_ROOT is required for 168 rehearsal')
if os.environ.get('MF_UAT_APP_ID') != 'MentorFlow.UAT.gate168':
    raise SystemExit('MF_UAT_APP_ID must be MentorFlow.UAT.gate168')

from common import APP_ID, ROOT, TASK, environment
environment()
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import scenario as base
from native import controls, uia, wait_control, login as native_login


def record(event, **values):
    path = TASK/'logs/scenario168.jsonl'
    with path.open('a', encoding='utf8') as stream:
        stream.write(json.dumps({'at': time.time(), 'event': event, **values}, ensure_ascii=False)+'\n')


def publish(label, *, hint_mandatory=None, hint_floor=None):
    command = [sys.executable, '-B', str(HERE/'publish168.py'), label]
    if hint_mandatory is not None:
        command += ['--hint-mandatory', 'true' if hint_mandatory else 'false']
    if hint_floor is not None:
        command += ['--hint-floor', hint_floor]
    result = subprocess.run(command, env=environment(), cwd=TASK,
                            capture_output=True, encoding='utf8', timeout=30)
    if result.returncode:
        raise RuntimeError('publish168 failed: '+result.stderr.strip())
    value = json.loads(result.stdout)
    record('published', **value)
    return value


def set_manifest_delay(seconds=0):
    path = TASK/'logs/metadata-delay.json'
    if seconds:
        path.write_text(json.dumps({'seconds': seconds}), encoding='utf8')
    else:
        path.unlink(missing_ok=True)


def assert_local_only():
    violations = []
    for path in TASK.rglob('mentorflow.log'):
        text = path.read_text(encoding='utf8', errors='replace')
        if 'UAT_LOCAL_ONLY' in text:
            violations.append(str(path))
    if violations:
        raise RuntimeError('frozen localhost boundary blocked a non-local request: '+', '.join(violations))


def check_bundle():
    result = subprocess.run([sys.executable, '-B', str(HERE/'refreeze103.py'),
        'X-final3', '--check-only', '--source-root', str(ROOT)], env=environment(), cwd=ROOT,
        capture_output=True, encoding='utf8', timeout=180)
    if result.returncode:
        raise RuntimeError('refreeze103 check failed; refusing to start frozen App: '+
                           result.stdout+'\n'+result.stderr)
    record('localhost_bundle_check_passed', output=result.stdout.strip())


def app_cache_witness(root):
    import hashlib
    identity = hashlib.sha256(os.path.normcase(str(root.resolve())).encode('utf8')).hexdigest()
    path = root.parent/'.MentorFlow-runtime'/APP_ID/'Updates'/identity/'highest-metadata-stable.json'
    return path if path.is_file() and path.stat().st_size < 3_000_000 else None


def install_x(root):
    root = root.resolve()
    if not root.is_relative_to(TASK.resolve()) or root.name != 'MentorFlow':
        raise ValueError('task-owned install path required')
    payload = TASK/'freeze-X-final/app/MentorFlow'
    release = TASK/'release-X-final3/stable.json'
    if root.exists():
        raise ValueError('refusing to overwrite an existing rehearsal root')
    root.parent.mkdir(parents=True, exist_ok=True)
    result_file = TASK/'logs'/(root.parent.name+'-install168.json')
    result = subprocess.run([str(payload/'UserRuntime/UserRuntime.exe'), 'install',
        '--root', str(root), '--source', str(payload), '--release', str(release),
        '--close-after-health', '--result', str(result_file)], env=environment(), cwd=TASK,
        capture_output=True, timeout=600)
    stdout = (result.stdout or b'').decode('utf8', errors='replace')
    stderr = (result.stderr or b'').decode('utf8', errors='replace')
    (TASK/'logs'/(root.parent.name+'-install168.stdout.txt')).write_text(stdout, encoding='utf8')
    (TASK/'logs'/(root.parent.name+'-install168.stderr.txt')).write_text(stderr, encoding='utf8')
    if result.returncode:
        raise RuntimeError(f'UserRuntime install failed ({result.returncode}): {stderr[-1600:]}')
    installed = json.loads(result_file.read_bytes())
    if installed.get('status') != 'HEALTHY':
        raise RuntimeError(f'X install did not reach HEALTHY: {installed}')
    record('x_installed', root=str(root), version=installed.get('version'), updater=installed.get('updater'))


def warm_x(root):
    check_bundle()
    set_manifest_delay(0)
    publish('X-final3')
    install_x(root)
    pid = base.launch(root)
    witness = base.wait_for(lambda: app_cache_witness(root), 90)
    cached = json.loads(witness.read_bytes())
    record('x_manifest_cache_verified', root=str(root), cached_version=cached.get('signed', {}).get('version'))
    base.close(root)
    assert_local_only()


def wait_login_window(root, timeout=90):
    pid = base.wait_for(lambda: base.app_pid(root), timeout)
    # A mandatory hint deliberately disables credentials. Native.wait_control
    # filters disabled controls, so it cannot detect this successful gate.
    base.wait_for(lambda: any(row.get('name') == '登录 MentorFlow'
        and not row.get('offscreen') for row in uia(pid)), timeout)
    return pid


def wait_login_editable(pid, timeout=30):
    def ready():
        rows = controls(pid)
        controls_by_name = {r.get('name'): r for r in rows}
        login = controls_by_name.get('登录 MentorFlow')
        account = controls_by_name.get('账号')
        password = controls_by_name.get('密码')
        return bool(login and account and password and login.get('enabled') and
                    account.get('enabled') and password.get('enabled'))
    return base.wait_for(ready, timeout)


def gate_rows(root, pid):
    path = root/'Data/logs/launcher-startup.jsonl'
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding='utf8', errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('pid') == pid and row.get('phase') == 'startup_login_gate':
            rows.append(row)
    return rows


def wait_gate(root, pid, outcome, timeout):
    return base.wait_for(lambda: next((row for row in reversed(gate_rows(root, pid))
                                      if row.get('outcome') == outcome), None), timeout)


def epoch(iso_value):
    return datetime.fromisoformat(iso_value.replace('Z', '+00:00')).timestamp()


def assert_manifest_still_pending(root, since):
    rows = []
    path = TASK/'logs/http.jsonl'
    if path.is_file():
        for line in path.read_text(encoding='utf8', errors='replace').splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('path', '').split('?', 1)[0].endswith('/stable.json'):
                rows.append(row)
    # The local server logs a response only after its intentional delay. A
    # still-pending request proves the login decision preceded the signed body.
    if any(float(row.get('time', 0)) >= since and str(row.get('status')) == '200'
           for row in rows):
        raise AssertionError('signed stable.json already returned before the pointer-gate observation')


def complete_update(root, old_pid, target, action_name='立即更新', *, web=False):
    started = time.monotonic()
    if web:
        base.cdp('click', action_name)
    else:
        uia(old_pid, 'invoke', action_name, wait=False)
    base.wait_for(lambda: base.app_pid(root) not in (None, old_pid), 300)
    new_pid = base.app_pid(root)
    wait_control(new_pid, '登录 MentorFlow', 120)
    transaction_path = root/'per-user-transaction.json'
    base.wait_for(lambda: transaction_path.is_file() and
        json.loads(transaction_path.read_bytes()).get('phase') == 'HEALTHY', 90)
    transaction = json.loads(transaction_path.read_bytes())
    pending = root/'pending-update.json'
    installed = json.loads((root/'current-release.json').read_bytes())['signed']['version']
    if pending.exists() or transaction.get('target') != target or installed != target:
        raise AssertionError(f'preview update did not settle on {target}: {transaction}')
    seconds = time.monotonic()-started
    record('required_update_applied_for_cleanup', old_pid=old_pid, new_pid=new_pid,
           target=target, seconds_to_new_login=seconds, transaction=transaction)
    base.close(root)
    return {'new_pid': new_pid, 'target': target, 'seconds_to_new_login': seconds,
            'transaction': transaction, 'pending_absent': True}


def optional(root, delay=15):
    warm_x(root)
    publish('Y-optional-final3')
    set_manifest_delay(delay)
    began = time.monotonic()
    began_wall = time.time()
    record('scenario_begin', scenario='optional', began_wall=began_wall,
           manifest_delay_seconds=delay)
    pid = base.launch(root)
    wait_login_window(root)
    gate = wait_gate(root, pid, 'optional_pointer', max(1, delay-1))
    assert_manifest_still_pending(root, began_wall)
    editable = wait_login_editable(pid, min(delay-1, 10))
    seconds_to_editable = time.monotonic()-began
    if seconds_to_editable >= delay:
        raise AssertionError(f'optional login did not become editable before delayed manifest: {seconds_to_editable:.3f}s')
    record('optional_login_gate_released_before_signed_manifest', pid=pid,
           gate=gate, fields_editable=editable,
           seconds_from_start_to_gate_log=epoch(gate['at'])-began_wall,
           ui_control_observation_upper_bound=seconds_to_editable,
           control_observation_wall=time.time())
    uia(pid, 'shot', out=TASK/'screenshots/optional-final3-fast-login168.png')
    wait_control(pid, '稍后', delay+60)
    rows = controls(pid)
    names = [row.get('name', '') for row in rows]
    if not any('更新' in name for name in names) or not any('稍后' in name for name in names):
        raise AssertionError('optional update dialog missing expected actions: '+repr(names))
    uia(pid, 'shot', 'MentorFlow 更新', out=TASK/'screenshots/optional-final3-update168.png')
    dialog_text = '\n'.join(names)
    if any(token in dialog_text.lower() for token in ('failed', 'error', '无法完成')):
        raise AssertionError('background update failure leaked to the optional prompt: '+dialog_text)
    seconds_to_prompt = time.monotonic()-began
    uia(pid, 'invoke', '稍后')
    record('optional_pointer_fast_manifest_prompt_later', pid=pid,
        began_wall=began_wall, seconds_to_editable=seconds_to_editable, manifest_delay_seconds=delay,
        seconds_to_optional_prompt=seconds_to_prompt, dialog_actions=names)
    set_manifest_delay(0)
    base.close(root)
    assert_local_only()
    return {'seconds_to_editable': seconds_to_editable,
            'seconds_to_optional_prompt': seconds_to_prompt,
            'manifest_delay_seconds': delay, 'dialog_actions': names}


def required(root, delay=15, *, observe_only=False):
    warm_x(root)
    publish('Y-required-final3')
    set_manifest_delay(delay)
    began = time.monotonic()
    began_wall = time.time()
    record('scenario_begin', scenario='required', began_wall=began_wall,
           manifest_delay_seconds=delay)
    pid = base.launch(root)
    wait_login_window(root)
    # A mandatory hint cannot release this login gate. Verify the decision
    # while stable.json is deliberately unanswered, before accepting any
    # later modal as evidence.
    gate_pending = base.wait_for(lambda: next((row for row in reversed(gate_rows(root, pid))
        if row.get('outcome') in ('pointer_wait_manifest', 'awaiting_signed_manifest')), None),
        max(1, delay-1))
    assert_manifest_still_pending(root, began_wall)
    rows_before = uia(pid)
    names_before = [row.get('name', '') for row in rows_before]
    login_before = next((row for row in rows_before
        if row.get('name') == '登录 MentorFlow' and row.get('type') == 'ControlType.Button'), None)
    if login_before is None:
        raise AssertionError('login control missing before the signed manifest returned')
    if login_before.get('enabled'):
        raise AssertionError('login remained enabled before the signed mandatory manifest returned')
    record('mandatory_gate_waited_for_signed_manifest', pid=pid, pending_gate=gate_pending,
           began_wall=began_wall,
           seconds_from_start_to_pending_gate=epoch(gate_pending['at'])-began_wall,
           login_enabled_before_manifest=bool(login_before and login_before.get('enabled')),
           login_control_type=login_before.get('type'),
           controls_before=names_before)
    wait_control(pid, '立即更新', delay+90)
    rows = controls(pid)
    names = [row.get('name', '') for row in rows]
    if any('稍后' in name for name in names):
        raise AssertionError('mandatory update unexpectedly offered a later action: '+repr(names))
    uia(pid, 'shot', 'MentorFlow 更新', out=TASK/'screenshots/required-final3-mandatory168.png')
    seconds_to_block = time.monotonic()-began
    record('mandatory_pointer_waited_and_blocked', pid=pid, seconds_to_block=seconds_to_block,
           began_wall=began_wall,
           manifest_delay_seconds=delay, dialog_actions=names,
           pending_gate=gate_pending)
    set_manifest_delay(0)
    update_result = ({'observe_only': True, 'pid': pid} if observe_only else
                     complete_update(root, pid, '9.168.13'))
    assert_local_only()
    return {'seconds_to_block': seconds_to_block, 'manifest_delay_seconds': delay,
            'dialog_actions': names, 'update': update_result}


def mismatch(root, delay=25, *, observe_only=False):
    warm_x(root)
    published = publish('Y-required-final3', hint_mandatory=False, hint_floor='9.168.11')
    if published['manifest_mandatory'] is not True or published['hint_mandatory'] is not False:
        raise AssertionError('mismatch fixture did not pair an optional hint with a signed mandatory manifest')
    set_manifest_delay(delay)
    began = time.monotonic()
    began_wall = time.time()
    record('scenario_begin', scenario='mismatch', began_wall=began_wall,
           manifest_delay_seconds=delay)
    pid = base.launch(root)
    wait_login_window(root)
    gate = wait_gate(root, pid, 'optional_pointer', max(1, delay-1))
    assert_manifest_still_pending(root, began_wall)
    wait_login_editable(pid, min(delay-1, 10))
    record('mismatch_optional_gate_released_pre_manifest', pid=pid, gate=gate,
           began_wall=began_wall,
           seconds_from_start_to_gate_log=epoch(gate['at'])-began_wall,
           ui_control_observation_upper_bound=time.monotonic()-began,
           control_observation_wall=time.time())
    # Submit real test-stub credentials while the signed manifest is delayed.
    # Reuse the existing single-call native Value/Invoke helper. Re-discovering
    # PID and invoking three separate PowerShell processes can consume the
    # delayed-manifest interval without ever submitting this login.
    native_login(pid, 'uat_runtime_102c')
    base.cdp('wait-text', '更新排练账号')
    assert_manifest_still_pending(root, began_wall)
    record('mismatch_home_visible_before_manifest', pid=pid,
           seconds_from_start=time.time()-began_wall)
    # After authentication the mandatory notice is a web modal in the home
    # shell, not a native login-window control. Observe and operate it only via
    # the local CDP endpoint.
    base.cdp('wait-text', 'MentorFlow 更新')
    modal = base.cdp('evaluate', "(()=>{const d=document.querySelector('[role=dialog][aria-label=\"MentorFlow 更新\"]');return d?{text:d.innerText,buttons:[...d.querySelectorAll('button')].map(b=>({text:b.innerText.trim(),disabled:b.disabled}))}:null})()")
    if not modal:
        raise AssertionError('signed mandatory UpdateNotice dialog was not visible in the home page')
    names = [item['text'] for item in modal['buttons']]
    if any(name == '稍后' for name in names) or not any(name == '立即更新' for name in names):
        raise AssertionError('signed mandatory update was bypassable after optional hint: '+repr(modal))
    if '必须安装' not in modal['text']:
        raise AssertionError('home-page update notice did not identify the signed release as mandatory')
    base.cdp('shot', str(TASK/'screenshots/mismatch-final3-signed-mandatory168.png'))
    seconds_to_signed_block = time.monotonic()-began
    record('optional_hint_signed_mandatory_still_blocked', pid=pid,
        began_wall=began_wall, seconds_to_signed_block=seconds_to_signed_block, manifest_delay_seconds=delay,
        dialog_actions=names, modal_text=modal['text'])
    set_manifest_delay(0)
    update_result = ({'observe_only': True, 'pid': pid} if observe_only else
                     complete_update(root, pid, '9.168.13', web=True))
    assert_local_only()
    return {'seconds_to_signed_block': seconds_to_signed_block,
            'manifest_delay_seconds': delay, 'dialog_actions': names, 'update': update_result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('scenario', choices=('optional', 'required', 'mismatch'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--observe-only', action='store_true',
        help='Verify the gate/modal only; leave the owned App open for explicit teardown')
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to(TASK.resolve()) or root.name != 'MentorFlow':
        raise ValueError('task-owned install path required')
    if args.scenario == 'optional':
        result = optional(root)
    elif args.scenario == 'required':
        result = required(root, observe_only=args.observe_only)
    else:
        result = mismatch(root, observe_only=args.observe_only)
    print(json.dumps({'scenario': args.scenario, 'result': result}, ensure_ascii=False))


if __name__ == '__main__':
    main()

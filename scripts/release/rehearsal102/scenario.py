"""Real preview scenario driver. UI actions use UIA/CDP, never bridge shortcuts."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

from common import TASK, environment
from native import PS, controls, install, uia, wait_control

HERE = Path(__file__).parent
NODE = 'D:/MentorFlow/.local/node/node.exe'
ENV = environment()
ENV['MF_UAT_ROOT'] = str(TASK)


def record(name, value):
    with (TASK / 'logs/scenarios.jsonl').open('a', encoding='utf8') as stream:
        stream.write(json.dumps(dict(at=time.time(), action=name, result=value), ensure_ascii=False) + '\n')


def run(script, *args):
    result = subprocess.run([sys.executable, str(HERE / script), *map(str, args)],
                            env=ENV, capture_output=True, encoding='utf8')
    if result.returncode:
        raise RuntimeError(script + ': ' + result.stderr.strip())
    return json.loads(result.stdout)


def cdp(action, selector='', value='', target='learning'):
    deadline=time.monotonic()+40
    while True:
        result = subprocess.run([NODE, str(HERE / 'cdp.mjs'), action, selector, value, target],
                                env=ENV, capture_output=True, encoding='utf8', timeout=40)
        if result.returncode==0:break
        # Login can finish before Qt has created its learning page/debug target.
        # Retry read-only discovery, never repeat a click or a form submission.
        if action=='wait-text' and time.monotonic()<deadline and any(t in result.stderr for t in ('preview page not found','ECONNREFUSED','Execution context was destroyed')):
            time.sleep(.2);continue
        raise RuntimeError(result.stderr)
    value = json.loads(result.stdout)
    # Deliberately do not log text entered into a password/Key field.
    record('cdp-' + action, value if action != 'inspect' else {'page_read': True})
    return value


def processes():
    result = subprocess.run([PS, '-NoProfile', '-File', str(HERE / 'processes.ps1')],
                            env=ENV, check=True, capture_output=True, encoding='utf8')
    return json.loads(result.stdout)


def wait_for(predicate, timeout=120):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.5)
    raise TimeoutError('owned preview condition did not become true')


def app_pid(root):
    image = str(root / 'App/MentorFlow.exe').casefold()
    return next((p['ProcessId'] for p in processes()
                 if p['ExecutablePath'].casefold() == image), None)


def launch(root):
    record('launch', run('launch.py', root))
    pid = wait_for(lambda: app_pid(root))
    wait_for(lambda: any(r.get('name') == 'MentorFlow' for r in controls(pid)))
    return pid


def close(root):
    pid = app_pid(root)
    if not pid:
        return
    # WindowPattern.Close is intentionally guarded by the product. Invoke the
    # actual native caption Close button, as a user choosing to exit would do.
    rows = controls(pid)
    if not any(r.get('name') in ('Close', '关闭') for r in rows):
        # A reused frameless account host may have no native caption controls.
        # Exit through the real account menu, then the login window's close
        # button; no WM_CLOSE/kill shortcut is used by the test driver.
        cdp('evaluate', "[...document.querySelectorAll('button')].find(e=>e.textContent.includes('更新排练账号')).click()")
        cdp('click', '退出登录')
        wait_control(pid, '关闭', 30)
        rows = controls(pid)
    name = 'Close' if any(r.get('name') == 'Close' for r in rows) else '关闭'
    try:
        uia(pid, 'invoke', name)
    except RuntimeError as error:
        # Qt can destroy its caption before UIA returns. Observe the actual
        # owned process exit; never turn a live/blocked app into a fake pass.
        if app_pid(root) is None:
            record('close-confirmed-after-uia-error', dict(pid=pid))
            return
        # A frameless owner can advertise a stale OS Close button. Use the
        # existing real account-menu exit, then its stable native login Close.
        record('close-caption-unavailable', dict(pid=pid))
        cdp('evaluate', "[...document.querySelectorAll('button')].find(e=>e.textContent.includes('更新排练账号')).click()")
        cdp('click', '退出登录')
        wait_control(pid, '关闭', 30)
        uia(pid, 'invoke', '关闭')
        wait_for(lambda: app_pid(root) is None, 45)
        record('close-confirmed-via-account-menu', dict(pid=pid))
        return
    wait_for(lambda: app_pid(root) is None, 45)


def login(root):
    pid = wait_for(lambda: app_pid(root))
    wait_control(pid, '登录 MentorFlow', 45)
    uia(pid, 'value', '账号', 'uat_runtime_102c')
    uia(pid, 'value', '密码', 'UAT-only-password102c')
    uia(pid, 'invoke', '登录 MentorFlow')
    cdp('wait-text', '更新排练账号')


def register(root):
    pid = wait_for(lambda: app_pid(root))
    uia(pid, 'invoke', '注册账号')
    cdp('wait-text', '注册账号', target='lifecycle')
    for selector, value in (
        ('#mf-register-username', 'uat_runtime_102c'),
        ('#mf-register-email', 'uat102c@example.test'),
        ('#register-password-new', 'UAT-only-password102c'),
        ('#register-password-confirm', 'UAT-only-password102c'),
    ):
        cdp('input', selector, value, 'lifecycle')
    cdp('shot', str(TASK / 'screenshots/registration-filled.png'), target='lifecycle')
    cdp('click', '注册账号', target='lifecycle')
    cdp('wait-text', '验证你的邮箱', target='lifecycle')
    cdp('input', '#mf-registration-code', '123456', 'lifecycle')
    cdp('click', '验证并完成注册', target='lifecycle')
    wait_control(pid, '登录 MentorFlow', 30)


def install_setup(setup, parent, label, *, linger_ok=False):
    parent = parent.resolve()
    if not parent.is_relative_to(TASK.resolve()):
        raise ValueError('owned installation folder required')
    process = subprocess.Popen([str(setup), '/LOG=' + str(TASK / ('logs/' + label + '-setup.log'))],
                               env=ENV, cwd=TASK)
    pid = wait_for(lambda: next((p['ProcessId'] for p in processes()
        if p['ParentProcessId'] == process.pid and p['Name'].endswith('.tmp')), None), 30)
    rows = install(pid, parent, label + '-installed.png')
    record('installer-finish', rows)
    # Native finish defaults: desktop shortcut on, launch off. Do not start App
    # from Inno: every scenario must start at the actual Updater shortcut target.
    launch_option = next(r for r in rows if r.get('name') == '启动 MentorFlow')
    if launch_option.get('toggle') == 'On':
        uia(pid, 'toggle', '启动 MentorFlow')
    uia(pid, 'invoke', '关闭')
    if linger_ok:
        # On this Windows build the Inno finish page can remain open after its
        # enabled UIA Close InvokePattern; successful installation is already
        # proven by the finish page and installed runtime. Keep the native
        # window untouched while continuing the isolated UAT.
        if not (parent/'MentorFlow/current-release.json').is_file():
            raise RuntimeError('installer finish page appeared without installed current-release.json')
        record('installer-finish-process-still-open', {'pid':pid,'path':str(parent/'MentorFlow')})
    else:
        wait_for(lambda: process.poll() is not None, 30)
    if process.returncode:
        raise RuntimeError('real preview installer failed')
    return parent / 'MentorFlow'


def update(root, release, target, *, login_entry=False, blocked=False, wait_updater=False, already_offered=False):
    old = app_pid(root)
    if not old:
        raise RuntimeError('launch X first')
    shutil.copy2(release, TASK / 'webroot/updates/app/stable/stable.json')
    if blocked:
        (TASK / 'save-block-once').write_text('test-only exit fault', encoding='utf8')
    if already_offered:
        pass
    elif login_entry:
        wait_control(old, '立即更新并重启', 45)
    else:
        cdp('click', '设置')
        cdp('click', '检查更新')
        # Reconstruction/background preparation can exceed the text helper's
        # 30s window. Observe the real enabled action, never guess with sleep.
        wait_for(lambda: cdp('evaluate', "[...document.querySelectorAll('button')].some(e=>e.textContent.trim()==='立即更新并重启'&&!e.disabled&&e.getClientRects().length)"), 180)
    with (TASK / 'logs/update-observer.out').open('w', encoding='utf8') as output:
        observer = subprocess.Popen([sys.executable, str(HERE / 'observe.py'), str(root), str(old),
            '--label', 'scenario', '--target', target, '--timeout', '240'], env=ENV, stdout=output, stderr=output)
    try:
        record('update-click-begin', {'target': target})
        if login_entry:
            uia(old, 'invoke', '立即更新并重启', wait=False)
        else:
            cdp('click', '立即更新并重启')
        if blocked:
            wait_control(old, '关闭软件', 100)
            uia(old, 'shot', out=TASK / 'screenshots/pending-install.png')
            uia(old, 'invoke', '关闭软件', wait=False)
            wait_for(lambda: app_pid(root) is None, 45)
            launch(root)
        wait_for(lambda: app_pid(root) not in (None, old), 240)
        wait_for(lambda: json.loads((root / 'per-user-transaction.json').read_bytes())['phase'] == 'HEALTHY', 60)
        if wait_updater:
            wait_for(lambda: json.loads((root/'updater-release.json').read_bytes())['signed']['version'] == target, 100)
        record('update-check', run('check.py', root, '--version', target))
    finally:
        observer.wait(timeout=250)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('fresh', 'cover', 'main-update', 'login-update',
        'blocked-update', 'next-start', 'verify', 'close', 'uninstall'))
    parser.add_argument('--root', type=Path, default=TASK / 'fresh/MentorFlow')
    parser.add_argument('--x-setup', type=Path, default=TASK / 'setup-X/MentorFlow-Setup.exe')
    parser.add_argument('--o-setup', type=Path, default=TASK / 'setup-O/MentorFlow-Setup.exe')
    parser.add_argument('--release', type=Path, default=TASK / 'release-Y/stable.json')
    parser.add_argument('--target', default='9.102.6')
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to(TASK.resolve()) or root.name != 'MentorFlow':
        raise ValueError('owned MentorFlow installation root required')
    if args.action == 'fresh':
        if root.exists():
            raise ValueError('fresh requires an empty owned parent')
        install_setup(args.x_setup, root.parent, 'fresh')
        launch(root)
        register(root)
        login(root)
        cdp('click', '设置')
        cdp('input', 'input[type=password]', 'sk-uat-not-a-real-deepseek-key-102c')
        cdp('click', '保存并使用')
        cdp('wait-text', 'Key 已保存')
        close(root)
        launch(root)
        login(root)
        cdp('click', '设置')
        cdp('wait-text', 'Key 已保存')
        cdp('shot', str(TASK / 'screenshots/key-after-restart.png'))
    elif args.action == 'cover':
        if root.exists():
            raise ValueError('cover requires a new owned installation parent')
        install_setup(args.o_setup, root.parent, 'old')
        launch(root)
        close(root)
        record('dirty-fixture', run('dirty.py', root))
        install_setup(args.x_setup, root.parent, 'cover')
        for _ in range(2):
            launch(root)
            wait_for(lambda: not (root / 'pending-update.json').exists(), 30)
            time.sleep(5)  # product cleanup is scheduled after the visible window
            record('cover-check', run('check.py', root, '--version', '9.102.5', '--fixtures'))
            close(root)
    elif args.action.endswith('update'):
        if args.action == 'main-update':
            cdp('click', '设置')
            cdp('click', '外观')
            cdp('evaluate', "[...document.querySelectorAll('.theme-card')].find(e=>e.textContent.includes('极光夜')).click()")
        update(root, args.release, args.target, login_entry=args.action == 'login-update',
               blocked=args.action == 'blocked-update')
    elif args.action == 'next-start':
        # Use after downloading Y and choosing the product's pending-install
        # action; do not manufacture pending or call apply_update directly.
        close(root)
        launch(root)
        record('next-start-check', run('check.py', root, '--version', args.target))
    elif args.action == 'close':
        close(root)
    elif args.action == 'uninstall':
        close(root)
        uninstaller = root / 'Maintenance/unins000.exe'
        process = subprocess.Popen([str(uninstaller), '/LOG=' + str(TASK / 'logs/uninstall.log')], env=ENV, cwd=TASK)
        pid = wait_for(lambda: next((p['ProcessId'] for p in processes()
            if p['ParentProcessId'] == process.pid and p['Name'].endswith('.tmp')), None), 30)
        wait_control(pid, '是(Y)', 30)
        uia(pid, 'invoke', '是(Y)', wait=False)
        wait_control(pid, '确定', 120)
        uia(pid, 'invoke', '确定')
        process.wait(timeout=30)
        if process.returncode:
            raise RuntimeError('owned preview uninstall failed')
    else:
        record('verify', run('check.py', root, '--version', args.target))


if __name__ == '__main__':
    main()

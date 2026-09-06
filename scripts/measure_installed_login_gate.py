"""Isolated packaged LoginGate observation; no login clicks or credentials.

Uses already-installed psutil only in this diagnostic harness, not the product.
Refuses an automatic remembered session. No production shortcut is modified.
"""
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import winreg
import psutil
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v
from scripts.test_windows_install_trust import normal_token


def probe_oidc(root):
    from scripts.windows_installed_launch import ISSUER
    response = subprocess.run([
        str(Path(os.environ['SystemRoot']) / 'System32/curl.exe'),
        '--silent', '--fail', '--noproxy', '*', '--connect-timeout', '2',
        '--max-time', '4', '--cacert', str(root / 'App/Config/edge-tls-ca.crt'),
        ISSUER + '/.well-known/openid-configuration'],
        capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        ready = response.returncode == 0 and json.loads(response.stdout).get('issuer') == ISSUER
    except (ValueError, AttributeError):
        ready = False
    return response.returncode, ready


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--resume-test-root', type=Path)
    parser.add_argument('--form-probe', type=Path)
    parser.add_argument('--observe-seconds', type=float, default=0)
    parser.add_argument('--probe-oidc', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.observe_seconds <= 30:
        parser.error('--observe-seconds must be between 0 and 30')
    if args.form_probe and args.observe_seconds:
        parser.error('Use either a form probe or a passive dwell control, not both')
    # Read only the boolean. Never dump profile labels, tokens or preferences.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\MentorFlow\MentorFlow\account') as key:
            automatic = str(winreg.QueryValueEx(key, 'auto_login')[0]) == '1'
    except FileNotFoundError:
        automatic = False
    if automatic:
        raise RuntimeError('Automatic session enabled; packaged observation refused')
    source = Path('E:/Downloads/MentorFlow-Login-Final-20260906')
    if v.sha256_file(source / 'MentorFlow/MentorFlow.exe') != '4235ab853c4eb45f848ad92f8f87943c064b0b3be280ea2556700c6e9d98c496':
        raise RuntimeError('Retained EXE identity changed')
    container = Path('E:/MentorFlow-TrustTest-20260906')
    if args.resume_test_root:
        import re
        root = args.resume_test_root
        if root.parent != container or not re.fullmatch(r'login-[0-9a-f]{12}', root.name):
            raise RuntimeError('Only an owned isolated login test may resume')
        trust.assert_protected(root)
        identity = root.name[6:]
    else:
        identity = uuid.uuid4().hex[:12]
        root = container / ('login-' + identity)
    data = Path('E:/Codex/Temp') / ('e107-installed-login-' + identity)
    data.mkdir(exist_ok=bool(args.resume_test_root))
    trust.assert_safe_parents(root)
    if not args.resume_test_root:
        trust._create_protected(root, directory=True)
        shutil.copytree(source / 'MentorFlow', root / 'App')
        (root / 'App/Config').mkdir()
        shutil.copyfile(source / 'Config/edge-tls-ca.crt', root / 'App/Config/edge-tls-ca.crt')
    original_manifest = source / 'MentorFlow/SELF-CHECK/package-manifest.json'
    if v.sha256_file(original_manifest) != 'a786bd894abea739b3b3f1a8a797b160cc8b56e88cf4f40ce580d8ad78ba9380':
        raise RuntimeError('Original manifest pin mismatch')
    original = json.loads(original_manifest.read_bytes())
    expected = {row['path']: row for row in original['files']}
    actual = {key[4:] for _, _, isdir, key in trust._walk(root) if not isdir and key.startswith('App/')}
    if actual != set(expected) | {'SELF-CHECK/package-manifest.json', 'Config/edge-tls-ca.crt'}:
        raise RuntimeError('Copy differs from original file set plus explicit public CA')
    for key, digest, _ in v._hash_gate_entries(root / 'App', sorted(expected), 4):
        if digest != expected[key]['sha256']:
            raise RuntimeError('Original payload copy hash mismatch: ' + key)
    from scripts.windows_installed_launch import CA_SHA
    if v.sha256_file(root / 'App/Config/edge-tls-ca.crt') != CA_SHA:
        raise RuntimeError('Public CA pin mismatch')
    print('ORIGINAL_PAYLOAD_COPY_HASHES=PASS', flush=True)
    manifest_path = root / 'App' / v.MANIFEST_RELATIVE
    # A resumed prepared fixture must already have the exact augmented file
    # set; full verification below rejects any incomplete/stale manifest.
    pin = v.sha256_file(manifest_path if args.resume_test_root else v.build_manifest(root / 'App'))
    trust.protect_installation(root)
    # Synthetic receipt provenance, not a claim of a new production build.
    kw = dict(manifest_sha256=pin, source_sha='f' * 40, tree_sha='e' * 40)
    full = trust.verify_installation(root, activate=True, **kw)
    print(json.dumps(dict(phase='prepared', root=str(root), data=str(data), full=full)), flush=True)
    helper = args.helper
    trust.assert_safe_parents(helper)
    trust.assert_protected(helper)
    adv, kernel = trust._api()
    primary = w.HANDLE()
    with normal_token() as token:
        adv.RevertToSelf()
        if not adv.DuplicateTokenEx(token, 0xF01FF, None, 2, 1, c.byref(primary)):
            raise RuntimeError('Cannot duplicate normal token')
    class Startup(c.Structure):
        _fields_ = [('cb', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR), ('title', w.LPWSTR), ('x', w.DWORD), ('y', w.DWORD), ('xs', w.DWORD), ('ys', w.DWORD), ('xc', w.DWORD), ('yc', w.DWORD), ('fill', w.DWORD), ('flags', w.DWORD), ('show', w.WORD), ('reserved_size', w.WORD), ('reserved_ptr', c.c_void_p), ('stdin', w.HANDLE), ('stdout', w.HANDLE), ('stderr', w.HANDLE)]
    class Process(c.Structure):
        _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]
    adv.CreateProcessWithTokenW.argtypes = [w.HANDLE, w.DWORD, w.LPCWSTR, w.LPWSTR, w.DWORD, c.c_void_p, w.LPCWSTR, c.POINTER(Startup), c.POINTER(Process)]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    # Native helper inherits this isolated data root; QSettings are read-only
    # during the non-authenticated show/close flow inspected before this test.
    previous = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = str(data)
    process, startup = Process(), Startup(cb=c.sizeof(Startup))
    command = c.create_unicode_buffer('"' + str(helper) + '" launch --install-root "' + str(root) + '" --manifest-sha256 ' + pin + ' --source-sha ' + kw['source_sha'] + ' --tree-sha ' + kw['tree_sha'])
    start = time.perf_counter()
    try:
        if not adv.CreateProcessWithTokenW(primary, 0, str(helper), command, 0x08000000, None, str(root / 'App'), c.byref(startup), c.byref(process)):
            raise RuntimeError('Normal launcher creation failed: ' + str(c.get_last_error()))
    finally:
        kernel.CloseHandle(primary)
        if previous is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = previous
    user = c.WinDLL('user32', use_last_error=True)
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user.IsWindowVisible.argtypes = [w.HWND]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    children, gate_hwnd = {}, None
    report = dict(root=str(root), data_root=str(data), helper=str(helper), helper_sha256=v.sha256_file(helper), manifest_sha256=pin, full=full, normal_token=True, login_visible_seconds=None, main_visible_before_login=False, qtcore_loaded=False, qtcore_paths=[])
    try:
        while time.perf_counter() - start < 45:
            try:
                children.update({p.pid: p for p in psutil.Process(process.pid).children(recursive=True)})
            except psutil.NoSuchProcess:
                pass
            for child in list(children.values()):
                try:
                    if Path(child.exe()) != root / 'App/MentorFlow.exe':
                        continue
                    if not report['qtcore_loaded']:
                        paths = [Path(m.path) for m in child.memory_maps() if Path(m.path).name.lower() == 'qt6core.dll']
                        report['qtcore_paths'] = [str(path) for path in paths]
                        report['qtcore_loaded'] = bool(paths) and all(path.is_relative_to(root / 'App') for path in paths)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            def inspect(hwnd, _):
                nonlocal gate_hwnd
                pid = w.DWORD()
                user.GetWindowThreadProcessId(hwnd, c.byref(pid))
                if pid.value not in children or not user.IsWindowVisible(hwnd):
                    return True
                title = c.create_unicode_buffer(256)
                user.GetWindowTextW(hwnd, title, len(title))
                if title.value == '登录 MentorFlow':
                    gate_hwnd = hwnd
                    if report['login_visible_seconds'] is None:
                        report['login_visible_seconds'] = time.perf_counter() - start
                elif title.value:
                    # Store only whether another titled window appeared, not
                    # potentially sensitive window text or account labels.
                    report['main_visible_before_login'] = True
                return True
            user.EnumWindows(callback_type(inspect), 0)
            if (gate_hwnd and report['qtcore_loaded'] and
                    time.perf_counter() - start - report['login_visible_seconds'] >= args.observe_seconds):
                report['passive_observe_seconds'] = args.observe_seconds
                if args.probe_oidc:
                    report['oidc_probe_exit'], report['oidc_issuer_ready'] = probe_oidc(root)
                if args.form_probe:
                    gate_pid = w.DWORD()
                    user.GetWindowThreadProcessId(gate_hwnd, c.byref(gate_pid))
                    probe = subprocess.run([str(args.form_probe), str(gate_hwnd), str(gate_pid.value)],
                        capture_output=True, text=True, timeout=35,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    report['form_probe_exit'] = probe.returncode
                    report['form_probe_result'] = probe.stdout.strip()
                    report['form_controls_focus_ready_seconds'] = (
                        time.perf_counter() - start if probe.returncode == 0 else None)
                user.PostMessageW(gate_hwnd, 0x10, 0, 0)
                break
            if kernel.WaitForSingleObject(process.process, 0) == 0:
                break
            time.sleep(0.1)
        close_start = time.perf_counter()
        exited = kernel.WaitForSingleObject(process.process, 15000) == 0
        code = w.DWORD()
        report.update(close_exit=exited, close_seconds=time.perf_counter() - close_start)
        if exited:
            kernel.GetExitCodeProcess(process.process, c.byref(code))
            report['exit_code'] = code.value
        report['owned_children_still_running'] = [pid for pid, p in children.items() if p.is_running()]
        (data / ('observation-' + str(process.pid) + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    finally:
        # Only the process tree started by this harness is eligible for cleanup.
        for child in reversed(list(children.values())):
            try:
                if child.is_running():
                    child.terminate()
                    child.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        if kernel.WaitForSingleObject(process.process, 0) != 0:
            owned = psutil.Process(process.pid)
            owned.terminate()
            owned.wait(timeout=5)
        kernel.CloseHandle(process.thread)
        kernel.CloseHandle(process.process)
    if report['login_visible_seconds'] is None or report['main_visible_before_login'] or not report['qtcore_loaded'] or not report['close_exit'] or report.get('exit_code') != 0 or report['owned_children_still_running']:
        raise RuntimeError('Installed LoginGate observation did not qualify; see recorded evidence')
    if args.form_probe and report.get('form_controls_focus_ready_seconds') is None:
        raise RuntimeError('Form controls readiness not observed; see recorded evidence')
    if args.probe_oidc and not report.get('oidc_issuer_ready'):
        raise RuntimeError('OIDC readiness not verified during owned launch; see recorded evidence')


if __name__ == '__main__':
    main()

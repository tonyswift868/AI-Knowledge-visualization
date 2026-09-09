"""Stable shortcut adapter for the existing signed consumer lifecycle."""
from scripts import per_user_install as install
from scripts import per_user_update_core as core
from desktop.update_protocol import strict_json, RecoverableError, SecurityError


def _recovery_health(root):
    """A writable recovery hint is usable only after native peer validation."""
    from desktop.windows_process_peer import ProcessPeer
    release = install.installed(root)
    doc = strict_json(install.read(root / 'last-recovery.json'))
    journal = strict_json(install.read(root / install.JOURNAL))
    proof = doc.get('health', {})
    if (doc.get('attempt') != journal.get('attempt') or
            journal.get('phase') != 'ROLLED_BACK' or
            doc.get('version') != release.version or
            proof.get('version') != release.version or proof.get('ready') is not True):
        raise SecurityError('recovery health does not match the current transaction')
    with ProcessPeer(proof['pid']) as peer:
        peer.require(executable=root / 'App/MentorFlow.exe', integrity_rid=8192)
        peer.visible_responsive_window(proof['hwnd_hex'])
    return proof


def launch(root):
    from desktop.update_download import InstallUpdateLock
    root = install.target(root)
    if not install.is_install(root):
        try: core.bootstrap_legacy_identity(root)
        except (OSError, RuntimeError, ValueError, ImportError):
            return repair_update_shell(root)
    try:
        install.verify_recovery_runtime(root)
    except (OSError, RuntimeError, ValueError):
        return repair_update_shell(root)
    with InstallUpdateLock(root):
        install.require_app_stopped(root)
        if core.unsafe_teardown('next_launch'):
            raise RecoverableError('session ending: launch and update deferred')
        # Recovery shares the core's writer mutex. Release it before calling
        # apply, which acquires the same non-reentrant mutex itself.
        install.recover(root)
    # A crash may leave App temporarily missing between the owned swaps.
    # Restore the verified previous generation BEFORE classifying its GUI.
    state = core.get_startup_update_state(root, check_feed=False, full=False)
    if state['state'] in ('REPAIR_REQUIRED', 'FATAL_UNRECOVERABLE'):
        return repair_update_shell(root)
    if (root / core.PENDING).exists():
        pending = core.read_pending(root)
        if pending['state'] not in ('CURRENT_HEALTHY', 'PREVIOUS_HEALTHY'):
            try:
                # A successful apply already launches/health-checks TARGET.
                result = core.resume_pending_on_normal_launch(root, reason='next_launch')
                if result.get('state') == 'CURRENT_HEALTHY':
                    return result
            except (OSError, RuntimeError, ValueError):
                # Offline/withdrawn/invalid pending content cannot block the
                # signed current App. A rolled-back App is already running;
                # validate its live identity instead of launching a duplicate.
                if core.read_pending(root)['state'] == 'PREVIOUS_HEALTHY':
                    return dict(status='PREVIOUS_HEALTHY', health=_recovery_health(root))
    with InstallUpdateLock(root):
        if core.unsafe_teardown('next_launch'):
            raise RecoverableError('session ending: launch and update deferred')
        install.require_app_stopped(root)
        return dict(status='HEALTHY', health=install.launch_health(root, install.installed(root)))


def repair_update_shell(root):
    """OS-native recovery UI in the independent signed runtime; no App/Qt/login.

    This is an adapter over the SAME core, not another validator or repair engine.
    All network/transaction work stays on a bounded worker; Close cancels download.
    """
    import sys
    if not getattr(sys, 'frozen', False):
        raise RecoverableError('native repair shell requires the packaged runtime')
    import ctypes as c
    from ctypes import wintypes as w
    import threading
    root = install.target(root)
    cancel = threading.Event()
    completed = threading.Event()
    result = {'state':'REPAIR_REQUIRED', 'message':'检查安装、修复或更新，无需登录。'}
    running = [None]
    user = c.WinDLL('user32', use_last_error=True)
    user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.SendMessageW.restype = c.c_ssize_t
    callback_type = c.WINFUNCTYPE(c.c_long, w.HWND, w.UINT, w.WPARAM, w.LPARAM, c.c_ssize_t)
    class Button(c.Structure):
        _pack_ = 1
        _fields_ = [('id',c.c_int),('text',w.LPCWSTR)]
    class Config(c.Structure):
        _pack_ = 1
        _fields_ = [('size',w.UINT),('parent',w.HWND),('instance',w.HINSTANCE),('flags',w.UINT),('common',w.UINT),
            ('title',w.LPCWSTR),('icon',c.c_void_p),('instruction',w.LPCWSTR),('content',w.LPCWSTR),
            ('count',w.UINT),('buttons',c.POINTER(Button)),('default',c.c_int),('radio_count',w.UINT),
            ('radios',c.POINTER(Button)),('radio_default',c.c_int),('verification',w.LPCWSTR),
            ('expanded',w.LPCWSTR),('expanded_control',w.LPCWSTR),('collapsed_control',w.LPCWSTR),
            ('footer_icon',c.c_void_p),('footer',w.LPCWSTR),('callback',callback_type),('data',c.c_ssize_t),('width',w.UINT)]
    def set_text(hwnd, message):
        text = c.create_unicode_buffer(message)
        user.SendMessageW(hwnd, 0x400+114, 0, c.addressof(text))  # TDM_SET_ELEMENT_TEXT / content
    def work(action):
        try:
            if not install.is_install(root): core.bootstrap_legacy_identity(root)
            if action in (100,101):
                value = core.get_startup_update_state(root, check_feed=action==101)
            else:
                # Updating a corrupt App and same-version repair both need the
                # full signed reconstruction, never bytes from the broken App.
                value = core.repair_installation(root, cancel=cancel.is_set)
            result.clear(); result.update(value)
        except Exception:
            result.clear(); result.update(state='FAILED_RECOVERABLE', message='暂时无法取得可信修复文件。请检查网络后重试，学习资料未更改。')
        finally: completed.set()
    @callback_type
    def callback(hwnd, notification, wp, lp, data):
        if notification == 2:  # TDN_BUTTON_CLICKED
            if wp == 2:
                cancel.set()
                return 1 if running[0] is not None and running[0].is_alive() else 0
            if running[0] is None or not running[0].is_alive():
                completed.clear(); cancel.clear()
                set_text(hwnd, '正在检查并准备安全修复，请稍候…')
                running[0] = threading.Thread(target=work,args=(int(wp),),daemon=True,name='mentorflow-repair')
                running[0].start()
            return 1  # keep this shell open while work runs
        if notification == 4 and completed.is_set():  # TDN_TIMER
            completed.clear()
            set_text(hwnd, result.get('message', '修复完成，MentorFlow 已重新启动。' if result.get('state')=='CURRENT_HEALTHY' else '操作未完成，请重试。'))
            if result.get('state') == 'CURRENT_HEALTHY':
                user.SendMessageW(hwnd,0x400+102,2,0)
        return 0
    buttons = (Button*4)(Button(100,'检查安装'),Button(101,'检查更新'),Button(102,'修复安装'),Button(103,'更新到最新版本'))
    config = Config(size=c.sizeof(Config), flags=0x800|0x8, common=0x20,
        title='MentorFlow 修复与更新', instruction='修复与更新', content=result['message'],
        count=4,buttons=buttons,callback=callback,width=320)
    native = c.WinDLL('comctl32',use_last_error=True)
    native.TaskDialogIndirect.argtypes = [c.POINTER(Config),c.POINTER(c.c_int),c.c_void_p,c.c_void_p]
    native.TaskDialogIndirect.restype = c.c_long
    button = c.c_int()
    if native.TaskDialogIndirect(c.byref(config),c.byref(button),None,None) < 0:
        from scripts.windows_installed_launch import show_repair
        show_repair('native recovery unavailable')
        return dict(state='FATAL_UNRECOVERABLE')
    return dict(state='REPAIR_SHELL_CLOSED')

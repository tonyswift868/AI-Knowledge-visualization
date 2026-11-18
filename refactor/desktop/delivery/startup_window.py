"""Startup diagnostics and foreground handoff, with no Qt import at module load."""
from pathlib import Path
import json
import os
import time
from datetime import datetime, timezone

PROCESS_STARTED = time.perf_counter()
_native = None
_paint = None
_gdi_objects = []
_class_instance = None
_startup_text = '正在启动…'


def native_splash(text='正在启动…'):
    """A tiny Win32 splash before Qt loads; QApplication later pumps its messages."""
    global _native, _startup_text
    _startup_text = text
    if os.name != 'nt': return
    import ctypes as c
    from ctypes import wintypes as w
    user = c.windll.user32
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
        c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p]
    user.CreateWindowExW.restype = w.HWND
    user.ShowWindow.argtypes = [w.HWND, c.c_int]
    user.SetForegroundWindow.argtypes = [w.HWND]
    user.UpdateWindow.argtypes = [w.HWND]
    gdi = c.windll.gdi32
    gdi.CreateSolidBrush.argtypes = [w.DWORD]; gdi.CreateSolidBrush.restype = w.HBRUSH
    gdi.SelectObject.argtypes = [w.HDC, w.HANDLE]; gdi.SelectObject.restype = w.HANDLE
    gdi.CreateFontW.argtypes = [c.c_int]*5 + [w.DWORD]*8 + [w.LPCWSTR]
    gdi.CreateFontW.restype = w.HFONT
    gdi.SetBkMode.argtypes = [w.HDC, c.c_int]
    gdi.SetTextColor.argtypes = [w.HDC, w.DWORD]
    user.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, c.c_int, c.POINTER(w.RECT), w.UINT]
    class Paint(c.Structure):
        _fields_ = [('dc', w.HDC), ('erase', w.BOOL), ('rect', w.RECT),
                    ('restore', w.BOOL), ('incremental', w.BOOL), ('reserved', c.c_byte*32)]
    user.BeginPaint.argtypes = [w.HWND, c.POINTER(Paint)]; user.BeginPaint.restype = w.HDC
    user.EndPaint.argtypes = [w.HWND, c.POINTER(Paint)]
    user.DefWindowProcW.argtypes = [w.HWND,w.UINT,w.WPARAM,w.LPARAM]
    user.DefWindowProcW.restype = c.c_ssize_t
    brush = gdi.CreateSolidBrush(0xF7F3FC)  # Existing sakura dialog background.
    fonts = [gdi.CreateFontW(size,0,0,0,weight,0,0,0,1,0,0,5,0,'Microsoft YaHei UI')
             for size,weight in [(-26,700),(-15,400)]]
    global _paint
    callback = c.WINFUNCTYPE(c.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)
    @callback
    def paint(hwnd,message,wp,lp):
        if message == 0xF:  # WM_PAINT
            area=Paint(); dc=user.BeginPaint(hwnd,c.byref(area))
            gdi.SetBkMode(dc,1); gdi.SetTextColor(dc,0x382F28)
            previous=gdi.SelectObject(dc,fonts[0]); rect=w.RECT(32,28,390,78)
            user.DrawTextW(dc,'MentorFlow',-1,c.byref(rect),0)
            gdi.SelectObject(dc,fonts[1]); gdi.SetTextColor(dc,0x806D84)
            rect=w.RECT(32,110,390,174); user.DrawTextW(dc,_startup_text,-1,c.byref(rect),0x10)
            gdi.SelectObject(dc,previous); user.EndPaint(hwnd,c.byref(area)); return 0
        return user.DefWindowProcW(hwnd,message,wp,lp)
    _paint=paint
    class WindowClass(c.Structure):
        _fields_=[('style',w.UINT),('proc',callback),('class_extra',c.c_int),('window_extra',c.c_int),
            ('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),('background',w.HBRUSH),
            ('menu',w.LPCWSTR),('name',w.LPCWSTR)]
    global _class_instance
    kernel=c.windll.kernel32; kernel.GetModuleHandleW.restype=w.HINSTANCE
    _class_instance=kernel.GetModuleHandleW(None)
    cls=WindowClass(); cls.proc=paint; cls.background=brush; cls.name='MentorFlowStartup'; cls.instance=_class_instance
    user.RegisterClassW.argtypes=[c.POINTER(WindowClass)]
    if not user.RegisterClassW(c.byref(cls)): return
    _gdi_objects.extend([brush,*fonts])
    width, height = 420, 220
    x, y = (user.GetSystemMetrics(0) - width)//2, (user.GetSystemMetrics(1) - height)//2
    # WS_EX_APPWINDOW; a normal captioned, centered window, never a tool window.
    hwnd = user.CreateWindowExW(0x40000, cls.name, 'MentorFlow', 0x80C00000,
                                x, y, width, height, None, None, _class_instance, None)
    if not hwnd: return
    user.ShowWindow(hwnd, 1)
    first_minimized = bool(user.IsIconic(c.c_void_p(hwnd)))
    user.ShowWindow(hwnd, 9)
    user.GetForegroundWindow.restype = w.HWND
    foreground_before = user.GetForegroundWindow()
    foreground_result = bool(user.SetForegroundWindow(hwnd))
    user.UpdateWindow(hwnd)
    _native = hwnd
    if '--root' in __import__('sys').argv:
        args = __import__('sys').argv
        user.GetForegroundWindow.restype = w.HWND
        log_phase(args[args.index('--root')+1], 'native_startup_window_visible',
                  visible=bool(user.IsWindowVisible(c.c_void_p(hwnd))),
                  first_show_minimized=first_minimized,
                  minimized=bool(user.IsIconic(c.c_void_p(hwnd))),
                  set_foreground_result=foreground_result,
                  foreground_before=int(foreground_before or 0),
                  foreground_after=int(user.GetForegroundWindow() or 0),
                  foreground=user.GetForegroundWindow() == hwnd)
    if not foreground_result:
        flash_taskbar(hwnd, root=runtime_root())


def set_startup_text(text, *, root=None):
    """Update the existing splash from the install worker, without activating it."""
    global _startup_text
    _startup_text = text
    if _native is not None and os.name == 'nt':
        import ctypes
        user = ctypes.windll.user32
        # Invalidate queues WM_PAINT on the UI thread; do not use cross-thread
        # SendMessage/activation while the worker holds the update lock.
        user.InvalidateRect(ctypes.c_void_p(_native), None, True)
    if root is not None:
        log_phase(root, 'startup_activity', text=text)


def current_startup_text():
    return _startup_text


def run_with_native_splash(action):
    """Keep the native update window responsive until real App health returns."""
    import threading
    from contextvars import copy_context
    done = threading.Event()
    outcome = {}
    context = copy_context()
    def work():
        try: outcome['result'] = action()
        except BaseException as error: outcome['error'] = error
        finally: done.set()
    threading.Thread(target=lambda: context.run(work), daemon=True,
                     name='mf-update-coordinator').start()
    try:
        while not done.is_set():
            pump_native_messages()
            done.wait(.01)
    finally:
        close_native_splash()
    if 'error' in outcome: raise outcome['error']
    return outcome['result']


def close_native_splash():
    global _native
    if _native is not None:
        import ctypes
        ctypes.windll.user32.DestroyWindow(ctypes.c_void_p(_native))
        ctypes.windll.user32.UnregisterClassW.argtypes=[ctypes.c_wchar_p,ctypes.c_void_p]
        ctypes.windll.user32.UnregisterClassW('MentorFlowStartup', _class_instance)
        gdi=ctypes.windll.gdi32
        gdi.DeleteObject.argtypes=[ctypes.c_void_p]
        for obj in _gdi_objects: gdi.DeleteObject(obj)
        _gdi_objects.clear()
        _native = None


def has_native_splash():
    return _native is not None


def pump_native_messages():
    import ctypes as c
    from ctypes import wintypes as w
    user = c.windll.user32
    user.PeekMessageW.argtypes = [c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
    user.TranslateMessage.argtypes = [c.POINTER(w.MSG)]
    user.DispatchMessageW.argtypes = [c.POINTER(w.MSG)]
    user.DispatchMessageW.restype = c.c_ssize_t
    message = w.MSG()
    while user.PeekMessageW(c.byref(message), None, 0, 0, 1):
        user.TranslateMessage(c.byref(message)); user.DispatchMessageW(c.byref(message))


def window_state(window):
    state = dict(visible=window.isVisible(), minimized=window.isMinimized())
    if os.name == 'nt':
        import ctypes
        user = ctypes.windll.user32
        user.GetForegroundWindow.restype = ctypes.c_void_p
        state['foreground'] = user.GetForegroundWindow() == int(window.winId())
    else:
        state['foreground'] = window.isActiveWindow()
    return state


def foreground(window):
    window.showNormal()
    if os.name == 'nt':
        import ctypes
        user = ctypes.windll.user32
        hwnd = ctypes.c_void_p(int(window.winId()))
        # The first ShowWindow honors the parent's STARTUPINFO, even when Qt
        # requests normal. A second explicit restore consumes SW_SHOWMINIMIZED.
        user.ShowWindow(hwnd, 9)  # SW_RESTORE
        user.GetForegroundWindow.restype = ctypes.c_void_p
        before = user.GetForegroundWindow()
        result = bool(user.SetForegroundWindow(hwnd))
        root = runtime_root()
        if root is not None:
            log_phase(root, 'app_foreground_requested', hwnd=int(window.winId()),
                      set_foreground_result=result, foreground_before=int(before or 0),
                      foreground_after=int(user.GetForegroundWindow() or 0))
        if not result:
            flash_taskbar(int(window.winId()), root=root)
    window.raise_(); window.activateWindow()


def flash_taskbar(hwnd, *, root=None):
    """Notify after denied foreground activation; never steal input focus."""
    if os.name != 'nt': return
    import ctypes
    from ctypes import wintypes
    class FlashInfo(ctypes.Structure):
        _fields_ = [('size', wintypes.UINT), ('hwnd', wintypes.HWND),
                    ('flags', wintypes.DWORD), ('count', wintypes.UINT),
                    ('timeout', wintypes.DWORD)]
    info = FlashInfo(ctypes.sizeof(FlashInfo), hwnd, 2, 3, 0)  # FLASHW_TRAY, three flashes.
    call = ctypes.windll.user32.FlashWindowEx
    call.argtypes = [ctypes.POINTER(FlashInfo)]
    call.restype = wintypes.BOOL
    # The API return is the prior active state, NOT whether flashing succeeded.
    prior_active = bool(call(ctypes.byref(info)))
    if root is not None:
        log_phase(root, 'foreground_attention_requested', hwnd=hwnd, flashes=3,
                  previous_active=prior_active)


def runtime_root():
    import sys
    for flag in ('--install-root', '--root'):
        if flag in sys.argv and sys.argv.index(flag) + 1 < len(sys.argv):
            return Path(sys.argv[sys.argv.index(flag) + 1])
    return None


def allow_foreground(pid, *, root=None, stage='updater_to_app'):
    """Pass normal Windows foreground permission; never attach input threads."""
    if os.name != 'nt': return False
    import ctypes
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = ctypes.c_void_p
    before = user.GetForegroundWindow()
    result = bool(user.AllowSetForegroundWindow(ctypes.c_ulong(pid)))
    root = root if root is not None else runtime_root()
    if root is not None:
        log_phase(root, 'foreground_permission', stage=stage, target_pid=pid,
                  granted=result, foreground_before=int(before or 0),
                  foreground_after=int(user.GetForegroundWindow() or 0))
    return result


def log_phase(root, phase, **values):
    try:
        process_pid = values.pop('pid', os.getpid())
        path = Path(root) / 'Data/logs/launcher-startup.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),
                pid=process_pid, phase=phase,
                process_seconds=round(time.perf_counter() - PROCESS_STARTED, 6), **values), ensure_ascii=False) + '\n')
    except (OSError, ValueError):
        pass

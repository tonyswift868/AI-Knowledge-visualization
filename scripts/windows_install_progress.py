"""Windows-owned installation feedback; no Qt, browser or application imports."""
import ctypes as c
from ctypes import wintypes as w


class InstallCancelled(RuntimeError):
    pass


class InstallProgress:
    def __init__(self):
        self.api = c.WinDLL("user32", use_last_error=True)
        self.api.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p]
        self.api.CreateWindowExW.restype = w.HWND
        self.api.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
        self.api.IsWindow.argtypes = [w.HWND]
        self.api.IsWindowVisible.argtypes = [w.HWND]
        self.api.UpdateWindow.argtypes = [w.HWND]
        self.api.DestroyWindow.argtypes = [w.HWND]
        self.api.PeekMessageW.argtypes = [c.c_void_p, w.HWND, w.UINT, w.UINT, w.UINT]
        self.api.TranslateMessage.argtypes = [c.c_void_p]
        self.api.DispatchMessageW.argtypes = [c.c_void_p]
        self.api.DispatchMessageW.restype = c.c_ssize_t
        self.window = self.api.CreateWindowExW(0, "STATIC", "MentorFlow — 安装与完整验证", 0x10C80000, 160, 160, 540, 160, None, None, None, None)
        if not self.window:
            raise RuntimeError("cannot show installation status")
        self.label = self.api.CreateWindowExW(0, "STATIC", "正在准备受保护安装。关闭此窗口将取消。", 0x50000000, 20, 25, 480, 60, self.window, None, None, None)
        if not self.label:
            self.close()
            raise RuntimeError("cannot show installation message")
        self.update("正在准备受保护安装。关闭此窗口将取消。")

    def update(self, message):
        class Point(c.Structure):
            _fields_ = [("x", w.LONG), ("y", w.LONG)]
        class Message(c.Structure):
            _fields_ = [("window", w.HWND), ("message", w.UINT), ("wparam", c.c_size_t), ("lparam", c.c_ssize_t), ("time", w.DWORD), ("point", Point), ("private", w.DWORD)]
        event = Message()
        while self.api.PeekMessageW(c.byref(event), None, 0, 0, 1):
            self.api.TranslateMessage(c.byref(event))
            self.api.DispatchMessageW(c.byref(event))
        if not self.api.IsWindow(self.window):
            raise InstallCancelled("installation cancelled; no new activation receipt")
        self.api.SetWindowTextW(self.label, message)
        self.api.UpdateWindow(self.window)
        self.api.UpdateWindow(self.label)

    def close(self):
        if self.window and self.api.IsWindow(self.window):
            self.api.DestroyWindow(self.window)

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.close()

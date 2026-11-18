"""Kernel-backed process/window evidence; no self-reported integrity claims."""
import ctypes as c
import os
from ctypes import wintypes as w
from pathlib import Path


def own_token_level():
    """(integrity RID, elevated) of this process. Our processes must match each other,
    whether the user runs MentorFlow normally or as administrator ([BUG-281])."""
    with ProcessPeer(os.getpid()) as me:
        info=me.inspect()
    return info['integrity_rid'],info['elevated']


class ProcessPeer:
    def __init__(self,pid):
        if type(pid) is not int or not 0 < pid < 2**32: raise ValueError('invalid peer PID')
        self.kernel=c.WinDLL('kernel32',use_last_error=True)
        self.kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
        self.kernel.OpenProcess.restype=w.HANDLE
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
        self.handle=self.kernel.OpenProcess(0x1000|0x100000,False,pid)
        if not self.handle: raise c.WinError(c.get_last_error())
        self.pid=pid

    def close(self):
        if self.handle: self.kernel.CloseHandle(self.handle);self.handle=None
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    def alive(self):return self.kernel.WaitForSingleObject(self.handle,0)==258

    def inspect(self):
        self.kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)]
        size=w.DWORD(32768);name=c.create_unicode_buffer(size.value)
        if not self.kernel.QueryFullProcessImageNameW(self.handle,0,name,c.byref(size)):
            raise c.WinError(c.get_last_error())
        times=[w.FILETIME() for _ in range(4)]
        self.kernel.GetProcessTimes.argtypes=[w.HANDLE]+[c.POINTER(w.FILETIME)]*4
        if not self.kernel.GetProcessTimes(self.handle,*(c.byref(t) for t in times)):
            raise c.WinError(c.get_last_error())
        adv=c.WinDLL('advapi32',use_last_error=True)
        adv.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]
        adv.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)]
        adv.GetSidSubAuthorityCount.argtypes=[c.c_void_p];adv.GetSidSubAuthorityCount.restype=c.POINTER(c.c_ubyte)
        adv.GetSidSubAuthority.argtypes=[c.c_void_p,w.DWORD];adv.GetSidSubAuthority.restype=c.POINTER(w.DWORD)
        token=w.HANDLE();needed=w.DWORD();elevated=w.DWORD()
        if not adv.OpenProcessToken(self.handle,8,c.byref(token)):raise c.WinError(c.get_last_error())
        try:
            if not adv.GetTokenInformation(token,20,c.byref(elevated),4,c.byref(needed)):
                raise c.WinError(c.get_last_error())
            adv.GetTokenInformation(token,25,None,0,c.byref(needed))
            buffer=c.create_string_buffer(needed.value)
            if not adv.GetTokenInformation(token,25,buffer,len(buffer),c.byref(needed)):
                raise c.WinError(c.get_last_error())
            sid=c.cast(buffer,c.POINTER(c.c_void_p)).contents.value
            rid=adv.GetSidSubAuthority(sid,adv.GetSidSubAuthorityCount(sid).contents.value-1).contents.value
        finally:self.kernel.CloseHandle(token)
        return dict(pid=self.pid,executable=str(Path(name.value).absolute()),
            created_filetime_hex=f'{(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime:016x}',
            integrity_rid=rid,elevated=bool(elevated.value),alive=self.alive())

    def require(self,*,executable,not_before_hex=None):
        evidence=self.inspect()
        if (Path(evidence['executable'])!=Path(executable).absolute() or
                (evidence['integrity_rid'],evidence['elevated'])!=own_token_level() or not evidence['alive'] or
                (not_before_hex is not None and int(evidence['created_filetime_hex'],16)<int(not_before_hex,16))):
            raise ValueError('native process identity/token/freshness mismatch')
        return evidence

    def visible_responsive_window(self,hwnd_hex):
        import re
        if not isinstance(hwnd_hex,str) or re.fullmatch('[0-9a-f]{16}',hwnd_hex) is None:
            raise ValueError('invalid window wire handle')
        hwnd=int(hwnd_hex,16);user=c.WinDLL('user32',use_last_error=True)
        user.IsWindowVisible.argtypes=[w.HWND];user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
        user.SendMessageTimeoutW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM,w.UINT,w.UINT,c.POINTER(c.c_size_t)]
        user.SendMessageTimeoutW.restype=w.LPARAM
        pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,c.byref(pid))
        reply=c.c_size_t()
        if (pid.value!=self.pid or not self.alive() or not user.IsWindowVisible(hwnd) or
                not user.SendMessageTimeoutW(hwnd,0,0,0,2|32,2000,c.byref(reply))):
            raise ValueError('target window absent, foreign or unresponsive')
        return dict(hwnd_hex=hwnd_hex,window_pid=pid.value,visible=True,responsive=True)

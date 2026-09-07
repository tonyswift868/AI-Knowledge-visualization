"""Owned Medium child starts suspended until helper captures its kernel identity.

Uses CPython's existing Windows CreateProcess binding, not token replacement or
Explorer impersonation. The coordinator must itself already have a Medium token.
"""
import ctypes as c
from ctypes import wintypes as w
import os
import subprocess
import _winapi


class SuspendedChild:
    def __init__(self,args,*,cwd):
        from .windows_process_peer import ProcessPeer
        with ProcessPeer(os.getpid()) as self_peer:
            info=self_peer.inspect()
            if info['integrity_rid']!=8192 or info['elevated']:
                raise PermissionError('only Medium coordinator may launch application')
        hp,ht,pid,_=_winapi.CreateProcess(args[0],subprocess.list2cmdline(args),None,None,False,
            4,None,str(cwd),subprocess.STARTUPINFO())
        self.handle=hp;self.thread=ht;self.pid=pid;self.returncode=None
    def resume(self):
        if self.thread is None:raise RuntimeError('target already resumed')
        kernel=c.WinDLL('kernel32',use_last_error=True)
        kernel.ResumeThread.argtypes=[w.HANDLE];kernel.ResumeThread.restype=w.DWORD
        if kernel.ResumeThread(self.thread)!=1:raise RuntimeError('target suspension count changed')
        _winapi.CloseHandle(self.thread);self.thread=None
    def poll(self):
        if _winapi.WaitForSingleObject(self.handle,0)==0:
            self.returncode=_winapi.GetExitCodeProcess(self.handle)
        return self.returncode
    def wait(self,timeout):
        if _winapi.WaitForSingleObject(self.handle,int(timeout*1000))!=0:
            raise subprocess.TimeoutExpired('owned update target',timeout)
        self.returncode=_winapi.GetExitCodeProcess(self.handle);return self.returncode
    def terminate(self):
        if self.poll() is None:_winapi.TerminateProcess(self.handle,23)
    def close(self):
        if self.thread is not None:_winapi.CloseHandle(self.thread);self.thread=None
        if self.handle is not None:_winapi.CloseHandle(self.handle);self.handle=None

"""Bounded local message IPC, kernel-identified peers, no pickle/deserialization.

First-instance reservation prevents silently joining a precreated pipe. Remote
clients are rejected. A nonce names each attempt; it is correlation, not trust.
Consumers must inspect peer PID/token/image through ProcessPeer.
"""
import ctypes as c
from ctypes import wintypes as w
import re
import time

from refactor.desktop.delivery.update_protocol import canonical
from refactor.desktop.delivery.update_protocol import strict_json
from refactor.desktop.delivery.update_protocol import SecurityError

MAX_MESSAGE=16384
PIPE_SDDL='D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)S:(ML;;NW;;;ME)'


def name(attempt,role):
    if not isinstance(attempt,str) or re.fullmatch('[0-9a-f]{32}',attempt) is None or role not in ('control','health'):
        raise ValueError('invalid attempt pipe name')
    return '\\\\.\\pipe\\MentorFlow.Update.v2.'+attempt+'.'+role


def kernel():
    k=c.WinDLL('kernel32',use_last_error=True)
    k.CreateNamedPipeW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,w.DWORD,c.c_void_p]
    k.CreateNamedPipeW.restype=w.HANDLE
    k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    k.CreateFileW.restype=w.HANDLE
    k.CloseHandle.argtypes=[w.HANDLE]
    k.ConnectNamedPipe.argtypes=[w.HANDLE,c.c_void_p]
    k.DisconnectNamedPipe.argtypes=[w.HANDLE]
    k.SetNamedPipeHandleState.argtypes=[w.HANDLE,c.POINTER(w.DWORD),c.c_void_p,c.c_void_p]
    k.GetNamedPipeClientProcessId.argtypes=[w.HANDLE,c.POINTER(w.DWORD)]
    k.GetNamedPipeServerProcessId.argtypes=[w.HANDLE,c.POINTER(w.DWORD)]
    k.ReadFile.argtypes=[w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p]
    k.WriteFile.argtypes=[w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p]
    return k


class Channel:
    def __init__(self,k,handle,*,server):self.k=k;self.handle=handle;self.server=server
    def close(self):
        if self.handle:self.k.CloseHandle(self.handle);self.handle=None
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    def peer_pid(self):
        pid=w.DWORD()
        query=self.k.GetNamedPipeClientProcessId if self.server else self.k.GetNamedPipeServerProcessId
        if not query(self.handle,c.byref(pid)):raise c.WinError(c.get_last_error())
        return pid.value
    def send(self,doc,*,timeout=5):
        raw=canonical(doc)
        if len(raw)>MAX_MESSAGE:raise SecurityError('oversized IPC message')
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            sent=w.DWORD()
            okay=self.k.WriteFile(self.handle,raw,len(raw),c.byref(sent),None)
            if okay and sent.value==len(raw):return
            if sent.value or not okay and c.get_last_error() not in (232,536):
                raise SecurityError('IPC write interrupted')
            time.sleep(.02)
        raise TimeoutError('bounded IPC write expired')
    def receive(self,*,timeout=60):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            buffer=c.create_string_buffer(MAX_MESSAGE);received=w.DWORD()
            okay=self.k.ReadFile(self.handle,buffer,len(buffer),c.byref(received),None)
            if okay and received.value:
                return strict_json(buffer.raw[:received.value])
            if not okay and c.get_last_error()!=232:raise SecurityError('IPC read interrupted/oversized')
            time.sleep(.02)
        raise TimeoutError('bounded IPC read expired')


class Listener(Channel):
    def __init__(self,attempt,role):
        k=kernel();adv=c.WinDLL('advapi32',use_last_error=True)
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,c.POINTER(c.c_void_p),c.POINTER(w.DWORD)]
        k.LocalFree.argtypes=[c.c_void_p]
        class SA(c.Structure):_fields_=[('size',w.DWORD),('descriptor',c.c_void_p),('inherit',w.BOOL)]
        sd=c.c_void_p();size=w.DWORD()
        # Local authenticated interactive users can exchange data only. No
        # impersonation, anonymous/remote access or arbitrary command messages.
        # Explicit Medium object label permits the intended Medium→High
        # request channel without relying on a creator's token default label.
        # Low-integrity writes remain denied; native peer checks still govern
        # every operation. This label applies to the pipe, not to program files.
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(PIPE_SDDL,1,c.byref(sd),c.byref(size)):
            raise c.WinError(c.get_last_error())
        try:
            sa=SA(c.sizeof(SA),sd,False)
            handle=k.CreateNamedPipeW(name(attempt,role),3|0x80000,4|2|1|8,1,MAX_MESSAGE,MAX_MESSAGE,0,c.byref(sa))
            if handle==c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
        finally:k.LocalFree(sd)
        super().__init__(k,handle,server=True)
    def reset(self):
        if not self.k.DisconnectNamedPipe(self.handle) and c.get_last_error()!=233:
            raise c.WinError(c.get_last_error())
    def accept(self,*,timeout=60):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            okay=self.k.ConnectNamedPipe(self.handle,None)
            if okay or c.get_last_error()==535:return self.peer_pid()
            if c.get_last_error()!=536:raise c.WinError(c.get_last_error())
            time.sleep(.02)
        raise TimeoutError('bounded IPC connection expired')


def connect(attempt,role,*,timeout=60):
    k=kernel();deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        # SECURITY_SQOS_PRESENT|SECURITY_IDENTIFICATION: server cannot
        # impersonate a more privileged client through this IPC channel.
        handle=k.CreateFileW(name(attempt,role),0xc0000000,0,None,3,0x00110000,None)
        if handle!=c.c_void_p(-1).value:
            mode=w.DWORD(2|1)
            if not k.SetNamedPipeHandleState(handle,c.byref(mode),None,None):
                k.CloseHandle(handle);raise c.WinError(c.get_last_error())
            return Channel(k,handle,server=False)
        if c.get_last_error() not in (2,231):raise c.WinError(c.get_last_error())
        time.sleep(.02)
    raise TimeoutError('bounded IPC server wait expired')

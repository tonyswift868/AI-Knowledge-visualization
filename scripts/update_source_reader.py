"""Read-only lease on untrusted incremental files; never execute from staging.

Hardlinks are permitted only for this signed-target copy path. The destination
is a NEW protected file, byte-hashed before commit. No ACL/owner/attribute is
ever changed on the source/shared file object.
"""
from contextlib import contextmanager
import ctypes as c
import msvcrt
import os

from desktop.update_tree_security import DirectoryPins, Info, normal
from desktop.update_protocol import SecurityError


@contextmanager
def open_source(path):
    path=normal(path)
    with DirectoryPins() as pins:
        pins.check(path.parent,path.parent,directory=True,missing=False)
        kernel=pins.kernel
        handle=kernel.CreateFileW(str(path),0x80000000,1|4,None,3,0x00200000,None)
        if handle==c.c_void_p(-1).value:
            raise SecurityError('staged input is missing or has an active writer')
        try:
            info=Info(); name=c.create_unicode_buffer(32768)
            length=kernel.GetFinalPathNameByHandleW(handle,name,len(name),0)
            if (not length or length>=len(name) or normal(name.value)!=path or
                    not kernel.GetFileInformationByHandle(handle,c.byref(info)) or
                    info.attributes & (0x400|0x10)):
                raise SecurityError('staged input alias/reparse/non-file rejected')
            # Ownership of the native handle passes to the CRT/file object.
            fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
            handle=None
            with os.fdopen(fd,'rb') as stream:
                yield stream
                # Detect a renamed/replaced parent even when original handles
                # still identify the old directory. Hashes bind file contents.
                pins.pin(path.parent)
        finally:
            if handle is not None: kernel.CloseHandle(handle)

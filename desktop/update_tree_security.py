"""Operation-scoped Windows directory pins and immutable source acceptance.

Directory handles retain object identity. A fresh no-follow parent handle is
checked against that identity and its final path before using a cached verdict;
this replaces repeated Python ancestor walks with constant native queries.
Files are checked afresh. Accepted
source payload handles deny data writes for their entire lease. No ACL, chmod,
timestamps or attributes are mutated on a shared file object.
"""
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import stat

from .update_protocol import SecurityError, safe_path, MANIFEST_PATH


def normal(path):
    value = str(Path(path).absolute()).removeprefix('\\\\?\\')
    candidate = Path(value)
    if not candidate.is_absolute() or len(candidate.drive) != 2 or '..' in candidate.parts:
        raise SecurityError('unsafe local update path')
    return candidate


class Info(c.Structure):
    _fields_ = [('attributes',w.DWORD),('created',w.FILETIME),('accessed',w.FILETIME),
        ('modified',w.FILETIME),('volume',w.DWORD),('size_high',w.DWORD),('size_low',w.DWORD),
        ('links',w.DWORD),('index_high',w.DWORD),('index_low',w.DWORD)]


def api():
    kernel = c.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [w.HANDLE,c.POINTER(Info)]
    kernel.GetFinalPathNameByHandleW.argtypes = [w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    return kernel


def filesystem(path):
    kernel=api(); name=c.create_unicode_buffer(32)
    kernel.GetVolumeInformationW.argtypes=[w.LPCWSTR,w.LPWSTR,w.DWORD,c.c_void_p,c.c_void_p,c.c_void_p,w.LPWSTR,w.DWORD]
    if not kernel.GetVolumeInformationW(normal(path).anchor,None,0,None,None,None,name,len(name)):
        raise OSError(c.get_last_error(),'cannot inspect update volume')
    return name.value


class DirectoryPins:
    def __init__(self):
        self.kernel=api(); self.handles={}; self.identities={}; self.calls=0
        self.allowed_links=set()

    def close(self):
        for handle in reversed(list(self.handles.values())):
            self.kernel.CloseHandle(handle)
        self.handles.clear()

    def __enter__(self): return self
    def __exit__(self,*_): self.close()

    def pin(self,path):
        path=normal(path)
        if path in self.handles:
            # Open the current name, not just the old retained handle: an
            # ancestor junction/rename must not make a stale verdict reusable.
            self.calls+=4
            handle=self.kernel.CreateFileW(str(path),0x80,7,None,3,0x02200000,None)
            if handle==c.c_void_p(-1).value: raise SecurityError('cached update directory disappeared')
            try:
                name=c.create_unicode_buffer(32768); info=Info()
                length=self.kernel.GetFinalPathNameByHandleW(handle,name,len(name),0)
                if not length or length>=len(name) or normal(name.value)!=path or not self.kernel.GetFileInformationByHandle(handle,c.byref(info)) or info.attributes & 0x400 or (info.volume,info.index_high,info.index_low)!=self.identities[path]:
                    raise SecurityError('cached update directory changed identity/path')
                return True
            finally: self.kernel.CloseHandle(handle)
        if path.parent != path and not self.pin(path.parent): return False
        self.calls+=1
        handle=self.kernel.CreateFileW(str(path),0x80,7,None,3,0x02200000,None)
        if handle == c.c_void_p(-1).value:
            code=c.get_last_error()
            if code in (2,3): return False
            raise SecurityError('directory cannot be safely pinned: '+str(code))
        info=Info()
        if not self.kernel.GetFileInformationByHandle(handle,c.byref(info)) or info.attributes & 0x400 or not info.attributes & 0x10:
            self.kernel.CloseHandle(handle)
            raise SecurityError('reparse/non-directory update ancestor')
        name=c.create_unicode_buffer(32768)
        length=self.kernel.GetFinalPathNameByHandleW(handle,name,len(name),0)
        if not length or length>=len(name) or normal(name.value)!=path:
            self.kernel.CloseHandle(handle)
            raise SecurityError('update ancestor resolved through an alias')
        self.handles[path]=handle
        self.identities[path]=(info.volume,info.index_high,info.index_low)
        return True

    def check(self,path,root,*,directory=False,missing=True):
        path,root=normal(path),normal(root)
        if not path.is_relative_to(root): raise SecurityError('update root escape')
        if directory:
            exists=self.pin(path)
            if not exists and not missing: raise SecurityError('missing update directory')
            return exists
        if not self.pin(path.parent):
            if missing: return False
            raise SecurityError('missing update parent')
        self.calls+=1
        try: info=path.lstat()
        except FileNotFoundError:
            if missing: return False
            raise SecurityError('missing update file')
        if info.st_file_attributes & 0x400 or not stat.S_ISREG(info.st_mode):
            raise SecurityError('reparse/non-regular update file')
        if info.st_nlink != 1 and path not in self.allowed_links:
            raise SecurityError('unaccepted update hardlink')
        return True


def immutable_path(path):
    parts={p.casefold() for p in Path(path).parts}
    return not parts & {'config','logs','cache','temp','tmp','data','workspace','uploads',
        'plans','account','receipts','receipt','pending','state','update-uat'} and not (
        Path(path).name.casefold().endswith(('.log','.tmp','.pending','.lock')) or
        Path(path).name.casefold() in {'state.json','receipt.json','installation-receipt.json'})


class AcceptedApp:
    """Live immutable content lease, never constructed from size/mtime alone.

    Source manifest is pinned by the caller's accepted identity, all contents
    are verified, and unchanged payload handles deny writes until close. A
    production caller additionally proves the existing protected receipt.
    Non-Windows/unsupported filesystems use the existing independent-copy path.
    """
    def __init__(self,app,rows,manifest_sha256):
        self.root=normal(app); self.rows=dict(rows); self.pins=DirectoryPins()
        self.files={}; self.identities={}; self.closed=False
        try:
            self.pins.check(self.root,self.root,directory=True,missing=False)
            for path,row in self.rows.items():
                safe_path(path)
                if row['path'] != path: raise SecurityError('accepted row/path mismatch')
                item=self.root/path
                self.pins.check(item,self.root,missing=False)
                if immutable_path(path):
                    handle=self.pins.kernel.CreateFileW(str(item),0x80000000,1|4,None,3,0x00200000,None)
                    if handle == c.c_void_p(-1).value: raise SecurityError('immutable source has a writer')
                    self.files[path]=handle
                    info=Info()
                    if not self.pins.kernel.GetFileInformationByHandle(handle,c.byref(info)):
                        raise SecurityError('cannot identify immutable source')
                    self.identities[path]=(info.volume,info.index_high,info.index_low)
                with item.open('rb') as stream:
                    if item.stat().st_size != row['size'] or hashlib.file_digest(stream,'sha256').hexdigest()!=row['sha256']:
                        raise SecurityError('accepted source content mismatch')
            raw=(self.root/MANIFEST_PATH).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=manifest_sha256:
                raise SecurityError('accepted manifest pin mismatch')
            declared={r['path']:r for r in json.loads(raw)['files']}
            declared[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(raw),sha256=manifest_sha256)
            if declared != self.rows: raise SecurityError('accepted manifest rows mismatch')
        except BaseException:
            self.close(); raise

    @classmethod
    def protected(cls,install_root):
        from scripts.windows_install_transaction import identities_from_receipt
        from scripts.windows_install_trust import assert_protected
        root=normal(install_root); ids=identities_from_receipt(root)
        app=root/'App'; assert_protected(app)
        raw=(app/MANIFEST_PATH).read_bytes()
        rows={r['path']:r for r in json.loads(raw)['files']}
        rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(raw),sha256=ids['manifest_sha256'])
        for path in rows: assert_protected(app/path)
        return cls(app,rows,ids['manifest_sha256'])

    def close(self):
        for handle in self.files.values(): self.pins.kernel.CloseHandle(handle)
        self.files.clear(); self.pins.close(); self.closed=True
    def __enter__(self): return self
    def __exit__(self,*_): self.close()

    def matches(self,path,row,app):
        if self.closed or normal(app)!=self.root or path not in self.files or self.rows.get(path)!=row:
            return False
        handle=self.pins.kernel.CreateFileW(str(self.root/path),0x80,7,None,3,0x00200000,None)
        if handle==c.c_void_p(-1).value: raise SecurityError('accepted source disappeared')
        try:
            info=Info()
            if not self.pins.kernel.GetFileInformationByHandle(handle,c.byref(info)) or info.attributes & 0x400 or (info.volume,info.index_high,info.index_low)!=self.identities[path]:
                raise SecurityError('accepted source file identity changed')
            return True
        finally: self.pins.kernel.CloseHandle(handle)

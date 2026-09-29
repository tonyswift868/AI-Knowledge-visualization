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
import time

from refactor.desktop.delivery.update_protocol import SecurityError
from refactor.desktop.delivery.update_protocol import safe_path
from refactor.desktop.delivery.update_protocol import MANIFEST_PATH
from refactor.desktop.delivery.update_protocol import strict_json
from refactor.desktop.delivery.update_protocol import version_key


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


def hardlink_eligible(current, target):
    """One operation-wide decision, independent of payload size/manifest rows."""
    return normal(current).drive.casefold()==normal(target).drive.casefold() and filesystem(target)=='NTFS'


def receipt_file_identity(kernel,handle):
    # Python 3.13 installation receipts use the full 64-bit volume serial and
    # 128-bit FileId. BY_HANDLE_FILE_INFORMATION truncates the volume to DWORD.
    from refactor.desktop.delivery.windows_identity import from_handle
    from refactor.desktop.delivery.windows_identity import decode
    volume, file_id = decode(from_handle(kernel,handle))
    # In-process legacy inventory adapter only, never a transport producer.
    return [volume, int.from_bytes(file_id,'little')]


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
    """Accepted-current identity lease; final target SHA is content authority.

    Production uses protected(), anchored in the existing admin receipt. The
    direct constructor is for an explicitly pinned UAT/current identity, not a
    replacement production trust root. No payload pre-hash: every hardlinked
    target is still fully read/hashed before READY. Retained read-data handles
    deny in-place writes through verification until this lease closes.
    """
    def __init__(self,app,rows,manifest_sha256,*,receipt=None,protected=False):
        started=time.perf_counter()
        self.root=normal(app); self.rows=dict(rows); self.pins=DirectoryPins()
        self.files={}; self.identities={}; self.closed=False
        self.profile={}; self.lease={}
        def phase(name,start): self.profile[name]=(time.perf_counter()-start)*1000
        phase('SOURCE_ROOT_PATH_SAFETY_MS',started)
        try:
            start=time.perf_counter()
            self.pins.check(self.root,self.root,directory=True,missing=False)
            root_id=receipt_file_identity(self.pins.kernel,self.pins.handles[self.root])
            install_id=receipt_file_identity(self.pins.kernel,self.pins.handles[self.root.parent])
            phase('SOURCE_ROOT_IDENTITY_MS',start)
            start=time.perf_counter()
            self.pins.check(self.root/MANIFEST_PATH,self.root,missing=False)
            raw=(self.root/MANIFEST_PATH).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=manifest_sha256:
                raise SecurityError('accepted manifest pin mismatch')
            manifest=strict_json(raw)
            if manifest.get('schema')!='mentorflow.windows-package-manifest.v1' or manifest.get('product')!='MentorFlow' or manifest.get('executable')!='MentorFlow.exe':
                raise SecurityError('accepted manifest identity mismatch')
            version_key(manifest.get('version'))
            declared={r['path']:r for r in manifest['files']}
            if len(declared)!=len(manifest['files']) or MANIFEST_PATH in declared:
                raise SecurityError('accepted duplicate/self manifest row')
            declared[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(raw),sha256=manifest_sha256)
            if declared != self.rows: raise SecurityError('accepted manifest rows mismatch')
            phase('SOURCE_MANIFEST_PARSE_MS',start)
            start=time.perf_counter()
            if receipt is not None:
                from refactor.desktop.delivery.windows_install_trust import SCHEMA
                from refactor.desktop.delivery.windows_install_trust import LEGACY_SCHEMA
                from refactor.desktop.delivery.windows_identity import from_legacy
                from refactor.desktop.delivery.windows_identity import decode
                binding=receipt.get('binding',{})
                expected_identity=from_legacy(install_id) if binding.get('schema')==SCHEMA else install_id
                if (receipt.get('full_verified') is not True or binding.get('schema') not in (SCHEMA,LEGACY_SCHEMA) or
                    binding.get('manifest_sha256')!=manifest_sha256 or binding.get('version')!=manifest['version'] or
                    binding.get('root')!=str(self.root.parent) or
                    binding.get('root_identity')!=expected_identity):
                    raise SecurityError('accepted receipt binding/root identity mismatch')
                app_inventory=receipt.get('inventory',{}).get('App',[])
                wire_root=from_legacy(root_id)
                expected_app_identity=([wire_root['volume_hex'],wire_root['file_id_le_hex']]
                    if binding.get('schema')==SCHEMA else root_id)
                if len(app_inventory)!=7 or app_inventory[0]!='dir' or app_inventory[4:6]!=expected_app_identity:
                    raise SecurityError('accepted receipt App identity mismatch')
            elif protected:
                raise SecurityError('protected source requires accepted receipt')
            from refactor.desktop.delivery.windows_identity import from_legacy
            self.lease=dict(schema_version=2,app_id='MentorFlow',installed_version=manifest['version'],
                installed_manifest_sha256=manifest_sha256,install_root_normalized=str(self.root.parent),
                install_identity=from_legacy(install_id),app_identity=from_legacy(root_id),
                receipt_schema=receipt['binding']['schema'] if receipt else 'explicit-pinned-uat-only',
                immutable_tree=True,protected_contract=protected,uat_writable_root=not protected)
            phase('SOURCE_RECEIPT_BINDING_MS',start)
            start=time.perf_counter(); directories={self.root}; folded=set()
            for path,row in self.rows.items():
                safe_path(path)
                if row['path'] != path or path.casefold() in folded:
                    raise SecurityError('accepted row/path mismatch or alias')
                folded.add(path.casefold())
                parent=(self.root/path).parent
                while parent!=self.root:
                    directories.add(parent); parent=parent.parent
            phase('SOURCE_FILE_PATH_VALIDATION_MS',start)
            start=time.perf_counter()
            for directory in sorted(directories,key=lambda p:len(p.parts)):
                self.pins.check(directory,self.root,directory=True,missing=False)
            phase('SOURCE_UNIQUE_DIR_SECURITY_MS',start)
            start=time.perf_counter()
            def acquire(entry):
                path,row=entry
                item=self.root/path
                immutable=immutable_path(path)
                handle=self.pins.kernel.CreateFileW(str(item),0x80000000 if immutable else 0x80,1|4,None,3,0x00200000,None)
                if handle == c.c_void_p(-1).value:
                    return path,None,None,SecurityError('immutable source missing or has a writer')
                try:
                    info=Info(); name=c.create_unicode_buffer(32768)
                    length=self.pins.kernel.GetFinalPathNameByHandleW(handle,name,len(name),0)
                    if (not length or length>=len(name) or normal(name.value)!=item or
                        not self.pins.kernel.GetFileInformationByHandle(handle,c.byref(info)) or
                        info.attributes & (0x400|0x10) or info.links!=1 or
                        ((info.size_high<<32)|info.size_low) != row['size']):
                        raise SecurityError('accepted file identity/path/metadata mismatch')
                    identity=(info.volume,info.index_high,info.index_low)
                except BaseException as error:
                    self.pins.kernel.CloseHandle(handle)
                    return path,None,None,error
                if not immutable:
                    self.pins.kernel.CloseHandle(handle); handle=None
                return path,handle,identity,None
            # Cold native read-data opens dominate (117s measured serial), not
            # hashing. Bounded concurrency preserves exactly the same access,
            # no-follow, identity, and deny-write checks. Drain ALL results on
            # failure so successful sibling handles never leak.
            from concurrent.futures import ThreadPoolExecutor
            failure=None
            with ThreadPoolExecutor(max_workers=16,thread_name_prefix='source-lease') as pool:
                for path,handle,identity,error in pool.map(acquire,self.rows.items()):
                    if handle is not None:
                        self.files[path]=handle; self.identities[path]=identity
                    if error is not None and failure is None: failure=error
            if failure is not None: raise failure
            phase('SOURCE_FILE_METADATA_MS',start)
            self.profile.update(SOURCE_FILES=len(self.rows),SOURCE_DIRS=len(directories),
                SOURCE_BYTES_HASHED=len(raw),SOURCE_HASH_COUNT=1,SOURCE_PAYLOAD_HASH_COUNT=0,
                SOURCE_FILE_ENUM_MS=0,SOURCE_FILE_HASH_MS=0,SOURCE_REPARSE_CHECK_MS=0,
                SOURCE_OPEN_COUNT=len(self.rows)+1,SOURCE_STAT_COUNT=1,
                SOURCE_REALPATH_OR_RESOLVE_COUNT=0,SOURCE_ANCESTOR_WALK_COUNT=0,
                INITIAL_SOURCE_ACCEPTANCE_MS=(time.perf_counter()-started)*1000,
                phase_note='manifest hash included in parse; native file reparse checks included in metadata; unique-directory pin includes bounded ancestor admission, no per-file ancestor walk')
        except BaseException:
            self.close(); raise

    @classmethod
    def protected(cls,install_root):
        from refactor.desktop.delivery.windows_install_trust import assert_protected
        from refactor.desktop.delivery.windows_install_trust import assert_safe_parents
        from refactor.desktop.delivery.windows_install_trust import RECEIPT
        from refactor.desktop.delivery.windows_install_trust import _check_entry_acls
        start=time.perf_counter(); root=normal(install_root); app=root/'App'
        assert_safe_parents(root)
        for path in (root,root/RECEIPT,app,app/MANIFEST_PATH): assert_protected(path)
        with DirectoryPins() as pins:
            pins.check(root/RECEIPT,root,missing=False)
            if (root/RECEIPT).stat().st_size>16*1024**2: raise SecurityError('oversized source receipt')
            receipt=strict_json((root/RECEIPT).read_bytes())
        receipt_ms=(time.perf_counter()-start)*1000
        raw=(app/MANIFEST_PATH).read_bytes(); pin=receipt['binding']['manifest_sha256']
        rows={r['path']:r for r in strict_json(raw)['files']}
        rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(raw),sha256=pin)
        accepted=cls(app,rows,pin,receipt=receipt,protected=True)
        try:
            # Keep fresh per-object ACL verification: a protected parent alone
            # cannot prove that a child has no explicit writable ACL.
            acl_start=time.perf_counter()
            _check_entry_acls([app/path for path in rows]+[p for p in accepted.pins.handles if p.is_relative_to(app)])
            accepted.profile['SOURCE_ACL_MS']=(time.perf_counter()-acl_start)*1000
            accepted.profile['SOURCE_RECEIPT_LOAD_MS']=receipt_ms
            accepted.profile['INITIAL_SOURCE_ACCEPTANCE_MS']=(time.perf_counter()-start)*1000
            return accepted
        except BaseException:
            accepted.close(); raise

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

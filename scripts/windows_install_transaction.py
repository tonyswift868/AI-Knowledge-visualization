"""Installer-only cooperative transaction; normal application never imports it.

The outer NSIS pins the manifest and owns UI/UAC. No server or user DB access.
Every destructive path is a validated, journal-owned program/staging path.
"""
from __future__ import annotations
import argparse
import base64
import configparser
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v

REG_PRODUCT = r'Software\MentorFlow'
REG_UNINSTALL = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\MentorFlow'
REG_FIELDS = {REG_PRODUCT: ('InstallLocation', 'Version'), REG_UNINSTALL:
    ('DisplayName','DisplayVersion','Publisher','InstallLocation','UninstallString','NoModify','NoRepair')}
STAGING = '.install-staging'
FAILED_ROOT = Path('E:/MentorFlow')
FAILED_MANIFEST_SHA = 'a5a42fbfdabba7f1f000a3fcef5ac2ce8d3d665090e4c52f642594536d2f2e4a'

class Cancelled(RuntimeError): pass

@contextmanager
def installer_lock():
    kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[c.c_void_p,w.BOOL,w.LPCWSTR]
    kernel.CreateMutexW.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.CreateMutexW(None,False,'Global\\MentorFlow-Installer-Transaction-v1')
    if not handle: raise OSError(c.get_last_error(),'cannot acquire installer lock')
    already=c.get_last_error()==183
    try:
        if already: raise ValueError('另一个安装或卸载事务正在运行。')
        yield
    finally: kernel.CloseHandle(handle)

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def relative(key):
    parts=key.split('/')
    if not key or '\\' in key or ':' in key or any(x in ('', '.', '..') for x in parts):
        raise ValueError('unsafe manifest path')
    for part in parts:
        if part.endswith(('.', ' ')) or any(ch in part for ch in '<>"|?*'):
            raise ValueError('unsafe manifest component')
    return parts

def load_manifest(source, pin):
    path=Path(source)/v.MANIFEST_RELATIVE
    v._validate_package_entry(path, Path(source), directory=False)
    if sha(path)!=pin: raise ValueError('manifest identity mismatch')
    doc=json.loads(path.read_bytes())
    if doc.get('schema')!=v.MANIFEST_SCHEMA or doc.get('product')!='MentorFlow':
        raise ValueError('manifest schema mismatch')
    rows={}
    seen=set()
    for row in doc['files']:
        key=row['path']; relative(key)
        if key.casefold() in seen: raise ValueError('duplicate manifest path')
        seen.add(key.casefold())
        if not isinstance(row['size'],int) or not 0<=row['size']<=8*1024**3:
            raise ValueError('invalid manifest length')
        if not v.SHA256_RE.fullmatch(row['sha256']): raise ValueError('invalid file digest')
        rows[key]=row
    if not 1 <= len(rows) <= 50000: raise ValueError('manifest file count')
    rows[v.MANIFEST_RELATIVE.as_posix()]={'path':v.MANIFEST_RELATIVE.as_posix(),
        'size':path.stat().st_size,'sha256':pin}
    return doc,rows

def assert_no_alias(path):
    st=Path(path).lstat()
    if st.st_file_attributes & 0x400 or (stat.S_ISREG(st.st_mode) and st.st_nlink!=1):
        raise ValueError('link/reparse entry preserved: '+str(path))
    return st

def validate_target(root):
    root=Path(os.path.abspath(root))
    if root==Path(root.anchor) or not root.anchor or str(root).startswith('\\\\'):
        raise ValueError('请选择本地程序文件夹，而不是磁盘根目录。')
    windows=Path(os.environ['SystemRoot'])
    excluded=[windows,Path('E:/MentorFlowData')]
    if os.environ.get('LOCALAPPDATA'): excluded.append(Path(os.environ['LOCALAPPDATA'])/'MentorFlow')
    for protected in excluded:
        if root==protected or root.is_relative_to(protected) or protected.is_relative_to(root):
            raise ValueError('安装目录与系统或用户数据目录重叠。')
    v._validate_existing_parent_chain(root.parent,label='installation parent')
    fs=c.create_unicode_buffer(32)
    if not trust._api()[1].GetVolumeInformationW(root.anchor,None,0,None,None,None,fs,len(fs)) or fs.value!='NTFS':
        raise ValueError('请选择本地 NTFS 磁盘。')
    if root.exists(): v._validate_package_root(root)
    trust.assert_safe_parents(root)
    if root.exists():
        assert_no_alias(root)
        if not root.is_dir(): raise ValueError('目标不是文件夹。')
    return root

def identities_from_receipt(root):
    path=Path(root)/trust.RECEIPT
    assert_no_alias(path); trust.assert_protected(path)
    if path.stat().st_size > 16*1024**2: raise ValueError('receipt too large')
    binding=json.loads(path.read_bytes())['binding']
    if binding['root']!=str(Path(root)): raise ValueError('receipt root mismatch')
    return {key:binding[key] for key in ('manifest_sha256','source_sha','tree_sha')}

def recognised(root):
    try:
        ids=identities_from_receipt(root)
        return sha(Path(root)/'App'/v.MANIFEST_RELATIVE)==ids['manifest_sha256']
    except (OSError,ValueError,KeyError,TypeError,RuntimeError): return False

CLEANUP_LEDGER = 'failed-install-cleanup.json'

def failed_owned(root=None, *, cancelled=lambda:None):
    """Prove every remaining file before deletion, including resumed cleanup."""
    root=Path(root) if root is not None else FAILED_ROOT
    if not root.exists(): return []
    if root != FAILED_ROOT or (root/trust.RECEIPT).exists(): return []
    assert_no_alias(root); trust.assert_protected(root)
    if any(p.name not in ('App',CLEANUP_LEDGER) for p in root.iterdir()):
        raise ValueError('旧失败目录有未知项目，全部保留。')
    app=root/'App'; ledger=root/CLEANUP_LEDGER
    resumed=ledger.exists()
    if resumed:
        assert_no_alias(ledger); trust.assert_protected(ledger)
        if ledger.stat().st_size>16*1024**2: raise ValueError('oversized cleanup ledger')
        state=json.loads(ledger.read_bytes())
        if state.get('schema')!='mentorflow.failed-cleanup.v1' or state.get('manifest_sha256')!=FAILED_MANIFEST_SHA:
            raise ValueError('unknown cleanup ledger preserved')
        expected=state['files']
        if not isinstance(expected,dict) or not 1<=len(expected)<=50000: raise ValueError('invalid cleanup inventory')
        for key,row in expected.items():
            relative(key)
            if not v.SHA256_RE.fullmatch(row['sha256']): raise ValueError('invalid cleanup digest')
    else:
        if not app.exists(): return []
        if sha(app/v.MANIFEST_RELATIVE)!=FAILED_MANIFEST_SHA:
            raise ValueError('旧失败安装清单不同，全部保留。')
        _,expected=load_manifest(app,FAILED_MANIFEST_SHA)
    directories={Path(key).parent.as_posix() for key in expected}
    directories.update(parent.as_posix() for key in expected for parent in Path(key).parents)
    if resumed:
        for key in state.get('directories',[]):
            relative(key); directories.add(key)
    files=[]
    if app.exists():
        assert_no_alias(app); trust.assert_protected(app)
        for path in app.rglob('*'):
            cancelled(); assert_no_alias(path); trust.assert_protected(path)
            key=path.relative_to(app).as_posix()
            if path.is_dir():
                if key not in directories: raise ValueError('unknown cleanup directory preserved')
            elif path.is_file():
                if key not in expected or sha(path)!=expected[key]['sha256']:
                    raise ValueError('旧失败安装有未知或已修改文件，全部保留。')
                files.append(path)
    missing=set(expected)-{p.relative_to(app).as_posix() for p in files}
    if not resumed and (not missing or any(Path(key).name!='INSTALLER' for key in missing)):
        raise ValueError('旧目录不符合已审计的失败形态，全部保留。')
    return files

def cleanup_failed(*,cancelled=lambda:None):
    files=failed_owned(cancelled=cancelled)
    ledger=FAILED_ROOT/CLEANUP_LEDGER; app=FAILED_ROOT/'App'
    if not files and not ledger.exists(): return 0
    if not ledger.exists():
        # Protected durable all-before-delete proof; future missing files are
        # permitted only when this record already exists and matches the pin.
        state=dict(schema='mentorflow.failed-cleanup.v1',manifest_sha256=FAILED_MANIFEST_SHA,
            files={p.relative_to(app).as_posix():dict(size=p.stat().st_size,sha256=sha(p)) for p in files},
            directories=[p.relative_to(app).as_posix() for p in app.rglob('*') if p.is_dir()])
        with trust._create_protected(ledger,directory=False) as stream:
            stream.write(json.dumps(state).encode()); stream.flush(); os.fsync(stream.fileno())
    total=sum(p.stat().st_size for p in files)
    for path in files:
        cancelled(); assert_no_alias(path); path.unlink()
    if app.exists():
        for directory in sorted((p for p in app.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
            cancelled(); directory.rmdir() # only previously proved, empty dirs
        app.rmdir()
    ledger.unlink() # last: interrupted cleanup retains its proof
    return total

def remove_attempt(path):
    """Only call for a created/recognised journal-owned attempt, never a root."""
    path=Path(path)
    if path.parent.name!=STAGING or not (path/'journal.json').is_file():
        raise ValueError('unowned staging cleanup refused')
    entries=list(path.rglob('*'))
    for entry in entries: assert_no_alias(entry)
    for entry in sorted(entries,key=lambda p:len(p.parts),reverse=True):
        if entry==path/'journal.json': continue
        if entry.is_dir(): entry.rmdir()
        else: entry.unlink()
    (path/'journal.json').unlink()
    path.rmdir()

class Registry:
    def snapshot(self):
        import winreg as r
        result={}
        for key,names in REG_FIELDS.items():
            values={}; result[key]=values
            for name in names:
                try:
                    with r.OpenKey(r.HKEY_LOCAL_MACHINE,key,0,r.KEY_READ|r.KEY_WOW64_64KEY) as handle:
                        values[name]=list(r.QueryValueEx(handle,name))
                except FileNotFoundError: values[name]=None
        return result
    def restore(self,snapshot):
        import winreg as r
        for key,values in snapshot.items():
            with r.CreateKeyEx(r.HKEY_LOCAL_MACHINE,key,0,r.KEY_WRITE|r.KEY_READ|r.KEY_WOW64_64KEY) as handle:
                for name,item in values.items():
                    if item is None:
                        try: r.DeleteValue(handle,name)
                        except FileNotFoundError: pass
                    else: r.SetValueEx(handle,name,0,item[1],item[0])
                empty=r.QueryInfoKey(handle)[:2]==(0,0)
            if empty:
                try: r.DeleteKeyEx(r.HKEY_LOCAL_MACHINE,key,r.KEY_WOW64_64KEY)
                except FileNotFoundError: pass
    def publish(self,root,version):
        import winreg as r
        values={REG_PRODUCT:{'InstallLocation':str(root),'Version':version},REG_UNINSTALL:{
            'DisplayName':'MentorFlow','DisplayVersion':version,'Publisher':'MentorFlow',
            'InstallLocation':str(root),'UninstallString':'"'+str(root/'Uninstall.exe')+'"',
            'NoModify':1,'NoRepair':1}}
        for key,rows in values.items():
            with r.CreateKeyEx(r.HKEY_LOCAL_MACHINE,key,0,r.KEY_WRITE|r.KEY_WOW64_64KEY) as handle:
                for name,value in rows.items(): r.SetValueEx(handle,name,0,r.REG_DWORD if isinstance(value,int) else r.REG_SZ,value)

class Shortcut:
    def __init__(self,path): self.path=Path(path)
    def snapshot(self):
        v._validate_existing_parent_chain(self.path,label='desktop shortcut')
        if not self.path.exists(): return None
        assert_no_alias(self.path)
        if self.path.stat().st_size>65536: raise ValueError('unknown oversized shortcut preserved')
        return base64.b64encode(self.path.read_bytes()).decode('ascii')
    def restore(self,value):
        if value is None:
            self.path.unlink(missing_ok=True)
        else:
            temporary=self.path.with_name('mf-rollback-'+uuid.uuid4().hex+'.lnk')
            temporary.write_bytes(base64.b64decode(value)); temporary.replace(self.path)
    def publish(self,root,ids,*,before_replace=lambda value:None):
        from scripts.windows_install_shortcut import _save_link,read_link
        arguments='--install-root "'+str(root)+'" --manifest-sha256 '+ids['manifest_sha256']+' --source-sha '+ids['source_sha']+' --tree-sha '+ids['tree_sha']
        temporary=self.path.with_name('mf-install-'+uuid.uuid4().hex+'.lnk')
        try:
            _save_link(temporary,root/'App/MentorFlow.exe',arguments,root/'App',root/'App/MentorFlow.exe')
            actual=read_link(temporary)
            if actual != dict(target=str(root/'App/MentorFlow.exe'),arguments=arguments,
                              workdir=str(root/'App'),icon=str(root/'App/MentorFlow.exe')):
                raise ValueError('shortcut readback mismatch')
            # Durable intent must exist BEFORE replacing the public shortcut.
            before_replace(base64.b64encode(temporary.read_bytes()).decode('ascii'))
            temporary.replace(self.path)
        finally: temporary.unlink(missing_ok=True)
    def points_to(self,root):
        from scripts.windows_install_shortcut import read_link
        return self.path.exists() and Path(read_link(self.path)['target'])==root/'App/MentorFlow.exe'

class Transaction:
    def __init__(self,source,root,identities,uninstaller,*,cancel=lambda:False,status=lambda stage,percent:None,
                 registry=None,shortcut=None,fault=lambda point:None,retain_previous=False):
        self.source=Path(source); self.root=Path(root); self.ids=identities; self.uninstaller=Path(uninstaller)
        self.cancel=cancel; self.status=status; self.registry=registry or Registry()
        self.shortcut=shortcut or Shortcut(Path(os.environ['USERPROFILE'])/'Desktop/MentorFlow.lnk')
        self.fault=fault; self.attempt=None; self.journal=None
        self.retain_previous=retain_previous
    def checkpoint(self,point):
        self.fault(point)
        if self.cancel(): raise Cancelled('安装已取消，正在回滚。')
    def save(self):
        target=self.attempt/'journal.json'; temp=self.attempt/'journal.pending'
        try:
            with trust._create_protected(temp,directory=False) as stream:
                stream.write(json.dumps(self.journal).encode('utf-8')); stream.flush(); os.fsync(stream.fileno())
            temp.replace(target)
        finally: temp.unlink(missing_ok=True)
    def copy_file(self,source,destination,expected=None):
        self.checkpoint('copy_begin'); assert_no_alias(source)
        parent=destination.parent
        pending=[]
        while not parent.exists(): pending.append(parent); parent=parent.parent
        for directory in reversed(pending): trust._create_protected(directory,directory=True)
        digest=hashlib.sha256()
        with source.open('rb') as incoming,trust._create_protected(destination,directory=False) as outgoing:
            while block:=incoming.read(65536):
                self.checkpoint('copy_chunk'); outgoing.write(block); digest.update(block)
            outgoing.flush(); os.fsync(outgoing.fileno())
        if expected and digest.hexdigest()!=expected: raise ValueError('staging file hash mismatch: '+str(destination))
        return digest.hexdigest()
    def preflight(self,check_space=True):
        self.root=validate_target(self.root)
        doc,rows=load_manifest(self.source,self.ids['manifest_sha256'])
        if self.root==self.source or self.root.is_relative_to(self.source) or self.source.is_relative_to(self.root):
            raise ValueError('源程序与安装目录不能重叠。')
        if self.root.exists() and any(self.root.iterdir()) and not recognised(self.root):
            pending=any((self.root/STAGING).glob('*/journal.json')) if (self.root/STAGING).is_dir() else False
            failed=(self.root==FAILED_ROOT and not (self.root/trust.RECEIPT).exists() and (self.root/'App'/v.MANIFEST_RELATIVE).is_file()
                    and sha(self.root/'App'/v.MANIFEST_RELATIVE)==FAILED_MANIFEST_SHA)
            if self.root==FAILED_ROOT and not (self.root/trust.RECEIPT).exists() and (self.root/CLEANUP_LEDGER).is_file():
                failed_owned(self.root) # full remaining-file proof, not name-only admission
                failed=True
            if not pending and not failed:
                raise ValueError('目标目录包含未识别内容，请选择空文件夹。')
        parent=self.root
        while not parent.exists(): parent=parent.parent
        sectors,sector_bytes,free_clusters,total_clusters=w.DWORD(),w.DWORD(),w.DWORD(),w.DWORD()
        kernel=c.WinDLL('kernel32',use_last_error=True)
        kernel.GetDiskFreeSpaceW.argtypes=[w.LPCWSTR]+[c.POINTER(w.DWORD)]*4
        if not kernel.GetDiskFreeSpaceW(self.root.anchor,c.byref(sectors),c.byref(sector_bytes),c.byref(free_clusters),c.byref(total_clusters)):
            raise OSError('cannot determine target allocation size')
        cluster=sectors.value*sector_bytes.value
        parents={str(Path(key).parent) for key in rows}
        required=sum(((r['size']+cluster-1)//cluster)*cluster for r in rows.values())
        # Old committed App is renamed, never copied. Its bytes already count
        # against reported free space; reserve only new allocations + controls.
        required+=(len(parents)+4)*cluster+64*1024**2+self.uninstaller.stat().st_size
        free=shutil.disk_usage(parent).free
        if check_space and free<required: raise ValueError('磁盘空间不足，尚未开始复制文件。')
        return doc,rows,required,free
    def verify(self,root,controls,activate,receipt_destination=None):
        # Discard the entire interrupted snapshot, as install_package does.
        # NTFS can deliver the final copy/rename notification after handles close.
        for attempt in range(3):
            self.checkpoint('verify')
            try:
                return trust.verify_installation(root,**self.ids,activate=activate,control_hashes=controls,
                    workers=1,cancelled=lambda:self.checkpoint('verify'),progress=lambda stage:self.status('正在验证安装',80),
                    receipt_destination=receipt_destination)
            except v.PackageError as error:
                if str(error) != 'package changed during security verification' or attempt == 2:
                    raise
                time.sleep(0.1)
    def run(self):
        if not c.windll.shell32.IsUserAnAdmin(): raise PermissionError('installer elevation required')
        self.status('正在检查和清理已确认的失败安装',0)
        # Updating an existing valid install must not clean any failed first
        # installation elsewhere (especially the default E:\MentorFlow root).
        if not self.retain_previous:
            cleanup_failed(cancelled=lambda:self.checkpoint('cleanup'))
        self.root=validate_target(self.root)
        self.recover()
        doc,rows,_,_=self.preflight()
        self.checkpoint('preflight')
        old=recognised(self.root)
        if self.retain_previous and not old:
            raise ValueError('update requires a verified existing installation')
        if old: trust.verify_installation(self.root,**identities_from_receipt(self.root),workers=1,
            cancelled=lambda:self.checkpoint('verify_old'))
        registry=self.registry.snapshot(); shortcut=self.shortcut.snapshot()
        created=not self.root.exists()
        stagebase=self.root/STAGING
        self.attempt=stagebase/uuid.uuid4().hex
        self.journal={'schema':'mentorflow.install-transaction.v1','root':str(self.root),'created_root':created,
            'phase':'COPYING','old':old,'registry':registry,'shortcut':shortcut,'moved':[], 'metadata_started':False}
        if self.retain_previous:
            self.journal['retain_previous']=True
        setup_owned=[]
        try:
            if created:
                trust._create_protected(self.root,directory=True); setup_owned.append(self.root)
            else: trust._set_protected(self.root)
            if not stagebase.exists():
                trust._create_protected(stagebase,directory=True); setup_owned.append(stagebase)
            elif any(stagebase.iterdir()): raise ValueError('存在可恢复的安装事务，保留现场，拒绝覆盖。')
            trust._create_protected(self.attempt,directory=True); setup_owned.append(self.attempt)
            self.save()
        except BaseException:
            for directory in reversed(setup_owned):
                try: directory.rmdir() # exclusively empty paths created above
                except OSError: pass
            raise
        new=self.attempt/'new'; backup=self.attempt/'prior-program'
        try:
            trust._create_protected(new,directory=True); trust._create_protected(new/'App',directory=True)
            trust._create_protected(backup,directory=True)
            for index,(key,row) in enumerate(rows.items()):
                self.status('正在准备程序文件',int(index*70/len(rows)))
                self.copy_file(self.source.joinpath(*relative(key)),new/'App'/key,row['sha256'])
            controls={'Uninstall.exe':self.copy_file(self.uninstaller,new/'Uninstall.exe')}
            self.checkpoint('before_verify')
            self.verify(new,controls,True)
            self.checkpoint('before_commit')
            self.journal['phase']='COMMITTING'; self.save()
            # Each completed rename is journalled. New verified App is never
            # paired with an accepted old receipt: launch fails closed in transit.
            for name in ('App',trust.RECEIPT,'Uninstall.exe'):
                if (self.root/name).exists():
                    self.journal['moved'].append(name); self.save()
                    (self.root/name).rename(backup/name)
            self.journal['new_app']=True; self.save()
            (new/'App').rename(self.root/'App')
            (new/'Uninstall.exe').rename(self.root/'Uninstall.exe')
            self.checkpoint('after_swap')
            staged_receipt=self.attempt/'validated-receipt.json'
            self.verify(self.root,controls,True,staged_receipt)
            self.checkpoint('before_metadata')
            self.journal['metadata_started']=True; self.save()
            self.registry.publish(self.root,doc['version']); self.checkpoint('registry')
            if self.shortcut.snapshot()!=shortcut:
                raise ValueError('桌面快捷方式已被其他操作修改，保留该修改。')
            self.journal['shortcut_started']=True; self.save()
            def remember_shortcut(value):
                self.journal['new_shortcut']=value; self.save()
            self.shortcut.publish(self.root,self.ids,before_replace=remember_shortcut)
            self.journal['new_shortcut']=self.shortcut.snapshot(); self.save()
            self.checkpoint('shortcut')
            self.verify(self.root,controls,False,staged_receipt)
            self.checkpoint('before_receipt_publish')
            self.journal['commit_receipt_sha256']=sha(staged_receipt); self.save()
            staged_receipt.replace(self.root/trust.RECEIPT)
            self.checkpoint('receipt_published')
            self.journal['phase']='COMMITTED'; self.save()
            self.status('安装成功',100)
        except BaseException:
            self.rollback()
            raise
        self.complete_transaction()
        try: stagebase.rmdir()
        except OSError: pass
        return {'status':'PASS','root':str(self.root)}
    def complete_transaction(self):
        if self.journal.get('retain_previous') and self.journal.get('old'):
            previous=self.root/'.update-previous'
            if not previous.exists(): trust._create_protected(previous,directory=True)
            assert_no_alias(previous); trust.assert_protected(previous)
            self.journal['phase']='COMMITTED'
            self.journal['pending_update_health']=True
            self.save()
            # Entire journal-owned prior tree is retained. Never delete previous
            # immediately after commit or conflate installed with healthy.
            self.attempt.rename(previous/self.attempt.name)
            self.attempt=previous/self.attempt.name
        else:
            remove_attempt(self.attempt)
    def recover(self):
        base=self.root/STAGING
        if not base.exists(): return
        assert_no_alias(base); trust.assert_protected(base)
        for attempt in list(base.iterdir()):
            assert_no_alias(attempt); trust.assert_protected(attempt)
            if len(attempt.name)==32 and all(ch in '0123456789abcdef' for ch in attempt.name) and not any(attempt.iterdir()):
                attempt.rmdir(); continue
            path=attempt/'journal.json'; assert_no_alias(path); trust.assert_protected(path)
            if path.stat().st_size>1024*1024: raise ValueError('oversized transaction journal')
            journal=json.loads(path.read_bytes())
            if (journal.get('schema')!='mentorflow.install-transaction.v1' or journal.get('root')!=str(self.root)
                    or set(journal.get('moved',[]))-{'App',trust.RECEIPT,'Uninstall.exe'}):
                raise ValueError('unknown transaction preserved')
            self.attempt=attempt; self.journal=journal
            # Receipt publication is the durable commit boundary, after all
            # metadata and verification. A kill before COMMITTED journal save
            # must finalize that commit, not restore a now obsolete old version.
            published=(not journal.get('rollback_requested') and journal.get('commit_receipt_sha256') and (self.root/trust.RECEIPT).is_file()
                and sha(self.root/trust.RECEIPT)==journal['commit_receipt_sha256'])
            if journal['phase']=='COMMITTED' or published:
                self.complete_transaction()
            elif journal['phase'] in ('ROLLED_BACK','UNINSTALLED'):
                remove_attempt(attempt)
            else: self.rollback()
        if base.exists():
            try: base.rmdir()
            except OSError: pass
    def rollback(self):
        journal=self.journal; backup=self.attempt/'prior-program'
        if journal.get('metadata_started'):
            self.registry.restore(journal['registry'])
            if journal.get('shortcut_started'):
                current=self.shortcut.snapshot()
                if current==journal.get('new_shortcut'):
                    self.shortcut.restore(journal['shortcut'])
                elif current!=journal['shortcut']:
                    raise ValueError('快捷方式状态已变化，保留可恢复事务，拒绝覆盖。')
        if journal.get('new_app'):
            failed=self.attempt/'rejected-App'
            if (self.root/'App').exists(): (self.root/'App').rename(failed)
            (self.root/trust.RECEIPT).unlink(missing_ok=True)
            (self.root/'Uninstall.exe').unlink(missing_ok=True)
            # Persist BEFORE restoring any old names. Recovery must never
            # mistake an already restored old App for the failed new App.
            journal['new_app']=False; self.save()
        for name in reversed(journal['moved']):
            if (backup/name).exists(): (backup/name).rename(self.root/name)
        journal['phase']='ROLLED_BACK'; self.save()
        remove_attempt(self.attempt)
        try: (self.root/STAGING).rmdir()
        except OSError: pass
        if journal['created_root']:
            try: self.root.rmdir()  # only empty, installer-created root
            except OSError: pass


def uninstall(root,registry=None,shortcut=None):
    if not c.windll.shell32.IsUserAnAdmin(): raise PermissionError('uninstall elevation required')
    root=validate_target(root); registry=registry or Registry()
    shortcut=shortcut or Shortcut(Path(os.environ['USERPROFILE'])/'Desktop/MentorFlow.lnk')
    ids=identities_from_receipt(root)
    trust.verify_installation(root,**ids)
    if (root/STAGING).exists() and any((root/STAGING).iterdir()):
        raise ValueError('安装事务尚未恢复，保留安装并退出。')
    txn=Transaction(root/'App',root,ids,root/'Uninstall.exe',registry=registry,shortcut=shortcut)
    base=root/STAGING
    if not base.exists(): trust._create_protected(base,directory=True)
    txn.attempt=base/uuid.uuid4().hex; trust._create_protected(txn.attempt,directory=True)
    backup=txn.attempt/'prior-program'; trust._create_protected(backup,directory=True)
    txn.journal={'schema':'mentorflow.install-transaction.v1','root':str(root),'created_root':False,
        'phase':'UNINSTALLING','moved':[],'registry':registry.snapshot(),'shortcut':shortcut.snapshot(),
        'metadata_started':False}
    txn.save()
    try:
        owns_shortcut=shortcut.points_to(root)
        for name in ('App',trust.RECEIPT,'Uninstall.exe'):
            txn.journal['moved'].append(name); txn.save(); (root/name).rename(backup/name)
        txn.journal['metadata_started']=True; txn.save()
        empty=registry.snapshot()
        for key,values in empty.items():
            location=values.get('InstallLocation')
            if location and location[0]==str(root):
                empty[key]={name:None for name in values}
        registry.restore(empty)
        if owns_shortcut:
            txn.journal['shortcut_started']=True; txn.journal['new_shortcut']=None; txn.save()
            shortcut.restore(None)
        txn.journal['phase']='UNINSTALLED'; txn.save()
    except BaseException:
        txn.rollback(); raise
    remove_attempt(txn.attempt)
    base.rmdir()
    try: root.rmdir() # empty program-only root; never recursive
    except OSError: pass

def write_status(path,stage,percent,error=''):
    if not path: return
    parser=configparser.ConfigParser(); parser['install']={'stage':stage,'percent':str(percent),'error':error}
    temp=path.with_suffix('.pending')
    with temp.open('w',encoding='utf-16') as stream: parser.write(stream)
    temp.replace(path)

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('transaction','preflight','uninstall-transaction','recognise'))
    parser.add_argument('--install-root',type=Path,required=True)
    parser.add_argument('--payload-source',type=Path)
    parser.add_argument('--manifest-sha256'); parser.add_argument('--source-sha'); parser.add_argument('--tree-sha')
    parser.add_argument('--uninstaller-source',type=Path)
    parser.add_argument('--cancel-file',type=Path); parser.add_argument('--status-file',type=Path)
    parser.add_argument('--shortcut-path',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.command=='recognise':
            return 0 if recognised(validate_target(args.install_root)) else 3
        if args.command=='uninstall-transaction':
            with installer_lock():
                uninstall(args.install_root,shortcut=Shortcut(args.shortcut_path) if args.shortcut_path else None)
            return 0
        ids={key:getattr(args,key) for key in ('manifest_sha256','source_sha','tree_sha')}
        txn=Transaction(args.payload_source,args.install_root,ids,args.uninstaller_source,
            shortcut=Shortcut(args.shortcut_path) if args.shortcut_path else None,
            cancel=lambda:args.cancel_file is not None and args.cancel_file.exists(),
            status=lambda stage,percent:write_status(args.status_file,stage,percent))
        if args.command=='preflight':
            _,_,required,free=txn.preflight()
            write_status(args.status_file,str(required),free)
        else:
            with installer_lock(): txn.run()
        return 0
    except Exception as error:
        write_status(args.status_file,'安装未完成',0,str(error)[:2000])
        return 1223 if isinstance(error,Cancelled) else 3

if __name__=='__main__': raise SystemExit(main())

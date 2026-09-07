"""Verify distribution payload and replace only the installer gate + manifest.

Installer/ is a distribution sibling namespace, never a recursive basename
exclusion. Program inventory remains exact. No application or DB is launched.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import shutil
from scripts import verify_windows_package as v
from scripts.windows_install_transaction import load_manifest, assert_no_alias, sha

def verify(root,pin):
    doc,rows=load_manifest(root,pin)
    actual=set()
    for child in root.iterdir():
        assert_no_alias(child)
        if child.name=='Installer':
            if not child.is_dir(): raise ValueError('Installer must be a distribution directory')
            continue
        entries=[child] if child.is_file() else child.rglob('*')
        for path in entries:
            assert_no_alias(path)
            v._validate_package_entry(path,root,directory=path.is_dir())
            if path.is_file(): actual.add(path.relative_to(root).as_posix())
    if actual!=set(rows): raise ValueError('distribution program file set mismatch')
    for key,row in rows.items():
        path=root/key
        if path.stat().st_size!=row['size'] or sha(path)!=row['sha256']:
            raise ValueError('distribution hash mismatch: '+key)
    return doc,rows

def allocation(path):
    class Info(c.Structure):
        _fields_=[('allocated',c.c_longlong),('length',c.c_longlong),('links',w.DWORD),
                  ('deleted',c.c_ubyte),('directory',c.c_ubyte)]
    kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype=w.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.CreateFileW(str(path),0x80,7,None,3,0,None)
    if handle==w.HANDLE(-1).value: raise OSError(c.get_last_error(),'allocation open failed')
    try:
        info=Info()
        if not kernel.GetFileInformationByHandleEx(handle,1,c.byref(info),c.sizeof(info)):
            raise OSError(c.get_last_error(),'allocation query failed')
        return info.allocated
    finally: kernel.CloseHandle(handle)

def audit(root,rows):
    groups={}
    for key,row in rows.items():
        parts=key.split('/'); component='/'.join(parts[:2]) if parts[0]=='_internal' else parts[0]
        group=groups.setdefault(component,dict(component=component,allocated_bytes=0,logical_bytes=0))
        group['allocated_bytes']+=allocation(root/key); group['logical_bytes']+=row['size']
    return sorted(groups.values(),key=lambda item:item['allocated_bytes'],reverse=True)[:20]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--payload',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--new-helper',type=Path)
    parser.add_argument('--evidence',type=Path)
    args=parser.parse_args()
    doc,rows=verify(args.payload,args.manifest_sha256)
    if not args.new_helper:
        print(json.dumps(dict(status='PASS',files=len(rows),bytes=sum(r['size'] for r in rows.values())))); return
    evidence=args.evidence
    if evidence is None or evidence.exists(): raise ValueError('new evidence directory required')
    assert_no_alias(args.new_helper)
    evidence.mkdir(parents=True)
    before=dict(manifest_sha256=args.manifest_sha256,bytes=sum(r['size'] for r in rows.values()),
        largest20=audit(args.payload,rows),main_exe_sha256=sha(args.payload/'MentorFlow.exe'))
    gate=args.payload/'MentorFlowInstallGate.exe'; manifest=args.payload/v.MANIFEST_RELATIVE
    shutil.copy2(gate,evidence/'previous-install-gate.bin')
    shutil.copy2(manifest,evidence/'previous-package-manifest.json')
    pending=gate.with_suffix('.pending')
    try:
        shutil.copyfile(args.new_helper,pending); pending.replace(gate)
        for row in doc['files']:
            if row['path']=='MentorFlowInstallGate.exe':
                row['size']=gate.stat().st_size; row['sha256']=sha(gate)
        temporary=manifest.with_suffix('.pending')
        temporary.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temporary.replace(manifest)
        _,newrows=verify(args.payload,sha(manifest))
    except BaseException:
        shutil.copyfile(evidence/'previous-install-gate.bin',gate)
        shutil.copyfile(evidence/'previous-package-manifest.json',manifest)
        raise
    result=dict(before=before,after=dict(manifest_sha256=sha(manifest),
        bytes=sum(r['size'] for r in newrows.values()),largest20=audit(args.payload,newrows),
        main_exe_sha256=sha(args.payload/'MentorFlow.exe'),gate_sha256=sha(gate)))
    (evidence/'payload-audit.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))

if __name__=='__main__': main()

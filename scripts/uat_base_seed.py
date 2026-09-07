"""Bounded first BASE seed into an already ACL-bootstrapped UAT root.

Called only by a sealed, elevated native UAT launcher. Not a general install,
update, shell or registry API. No existing receipt is required for pre-seed.
"""
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from contextlib import contextmanager

from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v
from scripts.install_receipt_state import inspect_root, read_document, validate_installed_record, ReceiptStateError

REQUEST_FIELDS={'schema_version','record_type','attempt_id','nonce','created_at','expires_at',
    'install_root','source_root','root_identity','bootstrap_sha256','manifest_sha256','app_exe_sha256','source_sha','tree_sha','version'}


@contextmanager
def source_stream(path):
    """No-follow handle, deny write/delete, check its actual final name/link count."""
    from ctypes import wintypes as w
    import msvcrt
    from desktop.update_tree_security import api,Info,normal
    kernel=api()
    handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,0x00200000,None)
    if handle==c.c_void_p(-1).value:raise ReceiptStateError('cannot lock seed source')
    try:
        info=Info();name=c.create_unicode_buffer(32768)
        n=kernel.GetFinalPathNameByHandleW(handle,name,len(name),0)
        if not n or n>=len(name) or normal(name.value)!=normal(path) or not kernel.GetFileInformationByHandle(handle,c.byref(info)) or info.attributes & (0x400|0x10) or info.links!=1:raise ReceiptStateError('unsafe seed source handle')
        stream=os.fdopen(msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY),'rb')
        handle=None
        with stream:yield stream
    finally:
        if handle is not None:kernel.CloseHandle(handle)


def check_request(doc, authority, *, now=None):
    now=time.time() if now is None else now
    if not isinstance(doc,dict) or set(doc)!=REQUEST_FIELDS or type(doc['schema_version']) is not int or doc['schema_version']!=1 or doc['record_type']!='mentorflow-uat-base-seed-request':raise ReceiptStateError('invalid seed request schema')
    for key in ('install_root','source_root','root_identity','bootstrap_sha256','manifest_sha256','app_exe_sha256','source_sha','tree_sha','version'):
        if doc[key]!=authority[key]:raise ReceiptStateError('seed authority mismatch: '+key)
    if not re.fullmatch('[0-9a-f]{32}',doc['attempt_id']) or not re.fullmatch('[0-9a-f]{64}',doc['nonce']):raise ReceiptStateError('invalid seed attempt/nonce')
    if type(doc['created_at']) not in (int,float) or type(doc['expires_at']) not in (int,float) or not doc['created_at']<=now<=doc['expires_at'] or not 0<doc['expires_at']-doc['created_at']<=900:raise ReceiptStateError('expired seed request')
    return doc


def preflight(doc,authority):
    check_request(doc,authority)
    root=Path(doc['install_root']);source=Path(doc['source_root'])
    state=inspect_root(root)
    if state['state']!='ACL_READY_NO_BASE' or state['root_identity']!=doc['root_identity'] or state['bootstrap_sha256']!=doc['bootstrap_sha256']:raise ReceiptStateError('seed pre-state mismatch')
    v._validate_package_root(source)
    if root==source or root.is_relative_to(source) or source.is_relative_to(root):raise ReceiptStateError('seed source/root overlap')
    entries=list(v._iter_package_entries(source))
    manifest_path=source/v.MANIFEST_RELATIVE
    if v.sha256_file(manifest_path)!=doc['manifest_sha256'] or v.sha256_file(source/'MentorFlow.exe')!=doc['app_exe_sha256']:raise ReceiptStateError('seed source pins mismatch')
    manifest=read_document(manifest_path)
    if manifest.get('schema')!=v.MANIFEST_SCHEMA or manifest.get('product')!='MentorFlow' or manifest.get('version')!=doc['version']:raise ReceiptStateError('seed manifest identity mismatch')
    rows={r['path']:r for r in manifest['files']}
    if len(rows)!=len(manifest['files']):raise ReceiptStateError('duplicate seed manifest rows')
    rows[v.MANIFEST_RELATIVE.as_posix()]={'path':v.MANIFEST_RELATIVE.as_posix(),'sha256':doc['manifest_sha256'],'size':manifest_path.stat().st_size}
    if {p.relative_to(source).as_posix() for p,directory in entries if not directory}!=set(rows):raise ReceiptStateError('seed source file set mismatch')
    from scripts.runtime_path_budget import budget
    budget(root,rows)
    if shutil.disk_usage(root).free<sum(r['size'] for r in rows.values())+128*1024**2:raise ReceiptStateError('seed disk space insufficient')
    return state,entries,rows


def atomic_record(path,doc):
    data=json.dumps(doc,sort_keys=True,separators=(',',':')).encode()
    temp=path.with_name(path.name+'.pending')
    if path.exists() or temp.exists():raise ReceiptStateError('seed evidence already exists')
    with trust._create_protected(temp,directory=False) as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    temp.replace(path)


def seed(doc,authority):
    if not c.WinDLL('shell32').IsUserAnAdmin():raise PermissionError('elevated sealed seed helper required')
    state,entries,rows=preflight(doc,authority)
    root,source=Path(doc['install_root']),Path(doc['source_root'])
    before_acl={str(p):trust.security_descriptor(p) for p in (root,root/'App',root/'.uat-control')}
    stagebase=root/'.install-staging'
    if not stagebase.exists():trust._create_protected(stagebase,directory=True)
    trust.assert_protected(stagebase)
    # CREATE_NEW protected one-shot admission; no replay/retry UAC after failure.
    with trust._create_protected(stagebase/'base-seed-once.claim',directory=False) as claim:
        claim.write(doc['nonce'].encode());claim.flush();os.fsync(claim.fileno())
    attempt=stagebase/doc['attempt_id'];trust._create_protected(attempt,directory=True)
    app=attempt/'App';trust._create_protected(app,directory=True)
    preserved=attempt/'preseed-App'
    result=dict(schema_version=1,record_type='mentorflow-base-seed-result',attempt_id=doc['attempt_id'],nonce=doc['nonce'],
        BASE_SEED_RESULT='FAIL',CANONICAL_RECEIPT_CREATED=False,BASE_QT_READY='NOT_RUN',FORMAL_USER_DATA_WRITES=0,FORMAL_INSTALL_WRITES=0)
    moved=False;copied=0
    try:
        # Source is untrusted. Only bytes are copied; no source code executes.
        # Each destination object is protected from its first creation, and the
        # entire final App is rehashed by the existing receipt verifier below.
        for path,directory in sorted(entries,key=lambda row:len(row[0].parts)):
            destination=app/path.relative_to(source)
            v._validate_package_entry(path,source,directory=directory)
            if directory:trust._create_protected(destination,directory=True)
            else:
                row=rows[path.relative_to(source).as_posix()]
                with source_stream(path) as incoming:
                    # Validate before publishing even temporary copied bytes;
                    # a raced untrusted source cannot copy privileged secrets.
                    if hashlib.file_digest(incoming,'sha256').hexdigest()!=row['sha256']:raise ReceiptStateError('seed source content changed')
                    incoming.seek(0)
                    with trust._create_protected(destination,directory=False) as outgoing:
                        shutil.copyfileobj(incoming,outgoing,1024*1024)
                        outgoing.flush();os.fsync(outgoing.fileno())
                if destination.stat().st_size!=row['size'] or v.sha256_file(destination)!=row['sha256']:raise ReceiptStateError('copied BASE hash mismatch')
                copied+=1
        # Recheck exact pre-state before replacing the sentinel-only App. The
        # entire prior directory is retained inside protected transaction state.
        preflight(doc,authority)
        (root/'App').rename(preserved);moved=True
        app.rename(root/'App')
        ids={k:doc[k] for k in ('manifest_sha256','source_sha','tree_sha')}
        for count in range(3):
            try:
                verified=trust.verify_installation(root,activate=True,**ids)
                break
            except v.PackageError as error:
                if str(error)!='package changed during security verification' or count==2:raise
                time.sleep(.1)
        canonical=root/trust.RECEIPT
        validate_installed_record(read_document(canonical),root)
        result.update(BASE_SEED_RESULT='PASS_REAL_PROTECTED_UAT',BASE_FILES_MATERIALIZED=copied,
            BASE_MANIFEST_VERIFY=verified,CANONICAL_RECEIPT_CREATED=True,CANONICAL_RECEIPT_VALID=True,
            CANONICAL_RECEIPT_PATH=str(canonical),BASE_EXE=str(root/'App/MentorFlow.exe'),BASE_VERSION=doc['version'],
            BASE_STATE='BASE_INSTALLED_HEALTHY',SENTINEL_PRESERVED=(preserved/'payload-sentinel.txt').read_bytes()==b'MentorFlow UAT elevated controlled write PASS\n',
            ACL_UNCHANGED=all(trust.security_descriptor(Path(p))==acl for p,acl in before_acl.items() if p!=str(root/'App')),
            ACL_BOOTSTRAP_EVIDENCE_PRESERVED=v.sha256_file(root/'.uat-control/acl-bootstrap-result.json')==doc['bootstrap_sha256'])
    except Exception as error:
        result['error']=type(error).__name__+': '+str(error)
        # No recursive deletion. Retain failed owned App for inspection and put
        # the sentinel-only original back if receipt publication never happened.
        if moved and not (root/trust.RECEIPT).exists():
            if (root/'App').exists():(root/'App').rename(attempt/'failed-App')
            preserved.rename(root/'App')
        result['BASE_FILES_MATERIALIZED']=copied
        raise
    finally:
        atomic_record(attempt/'seed-result.json',result)
    return result


def main():
    if sys.argv[1:] not in (['--preflight'],['--seed']):return 87
    from mentorflow_seed_authority import AUTHORITY,REQUEST_PATH
    doc=read_document(REQUEST_PATH,65536)
    if sys.argv[1]=='--preflight':
        print(json.dumps(preflight(doc,AUTHORITY)[0]));return 0
    seed(doc,AUTHORITY);return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(type(error).__name__+': '+str(error));raise SystemExit(1)

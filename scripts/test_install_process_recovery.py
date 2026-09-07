"""Process-kill qualification on real D: files; NOT elevated ACL or GUI proof.

Only this harness supplies normal-file ACL adapters and file-backed registry/
shortcut namespaces. Production CLI exposes no bypass or fault injection flags.
"""
import argparse
from contextlib import ExitStack
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from scripts import windows_install_transaction as tx
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v

def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def pause(base,point,target):
    if point==target:
        dump(base/'checkpoint.json',dict(pid=os.getpid(),point=point))
        time.sleep(45) # parent kills this exact owned subprocess; no except runs
        raise RuntimeError('parent did not terminate owned checkpoint process')

class Registry:
    def __init__(self,base): self.path=base/'registry.json'
    def snapshot(self): return json.loads(self.path.read_bytes()) if self.path.exists() else {}
    def restore(self,value): dump(self.path,value)
    def publish(self,root,version): self.restore(dict(InstallLocation=str(root),Version=version))

class Shortcut:
    def __init__(self,base,target=''): self.base=base; self.path=base/'shortcut.json'; self.target=target
    def snapshot(self): return self.path.read_text() if self.path.exists() else None
    def restore(self,value):
        if value is None: self.path.unlink(missing_ok=True)
        else: self.path.write_text(value)
    def publish(self,root,ids,**kwargs):
        value=json.dumps(dict(root=str(root),ids=ids),sort_keys=True)
        if kwargs.get('before_replace'): kwargs['before_replace'](value)
        self.restore(value)
        pause(self.base,'shortcut_published',self.target)

def adapters(stack):
    def create(path,*,directory):
        if directory: path.mkdir(); return None
        return path.open('xb')
    for name,value in {'assert_protected':lambda p:None,'assert_safe_parents':lambda p:None,
        '_root':lambda p:Path(p),'_set_protected':lambda p:None,'_create_protected':create}.items():
        stack.enter_context(patch.object(trust,name,value))
    original=ctypes.WinDLL
    stack.enter_context(patch.object(ctypes,'WinDLL',side_effect=lambda name,*a,**kw:
        SimpleNamespace(IsUserAnAdmin=lambda:True) if name=='shell32' else original(name,*a,**kw)))
    stack.enter_context(patch.object(ctypes.windll.shell32,'IsUserAnAdmin',return_value=True))

def source(base,label):
    root=base/label; root.mkdir()
    for key in ['MentorFlow.exe','SELF-CHECK/MentorFlowSelfCheck.exe',
        'SELF-CHECK/verify_windows_package.py','SELF-CHECK/mentorflow-check.bat',
        'SELF-CHECK/mentorflow-update.bat','_internal/demo.dist-info/INSTALLER']:
        path=root/key; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(label.encode())
    dump(root/v.VERSION_FILE_NAME,dict(schema=v.VERSION_SCHEMA,product='MentorFlow',version='1.1.0'))
    pin=tx.sha(v.build_manifest(root))
    return root,dict(manifest_sha256=pin,source_sha='a'*40,tree_sha='b'*40)

def child(base,command,target):
    with ExitStack() as stack:
        adapters(stack)
        if command=='cleanup':
            root=base/'failed'; pin=(base/'cleanup-pin.txt').read_text()
            original=tx.failed_owned
            stack.enter_context(patch.object(tx,'FAILED_ROOT',root))
            stack.enter_context(patch.object(tx,'FAILED_MANIFEST_SHA',pin))
            stack.enter_context(patch.object(tx,'failed_owned',lambda *a,**kw:original(root,*a,**kw)))
            unlink=Path.unlink
            def delete(path,*a,**kw):
                result=unlink(path,*a,**kw)
                if path.name=='MentorFlow.exe': pause(base,'cleanup_deleted',target)
                return result
            stack.enter_context(patch.object(Path,'unlink',delete))
            tx.cleanup_failed(); return dict(status='PASS')
        stack.enter_context(patch.object(tx,'cleanup_failed',return_value=0))
        if target=='staging_cleanup':
            unlink=Path.unlink
            def remove(path,*a,**kw):
                result=unlink(path,*a,**kw)
                journals=list((base/'installed'/tx.STAGING).glob('*/journal.json'))
                committed=any(json.loads(p.read_bytes()).get('phase')=='COMMITTED' for p in journals)
                if committed and path.is_relative_to(base/'installed'/tx.STAGING) and path.name not in ('journal.json','journal.pending'):
                    pause(base,'staging_cleanup',target)
                return result
            stack.enter_context(patch.object(Path,'unlink',remove))
        ids=json.loads((base/'new-ids.json').read_bytes())
        active={'large':False,'chunks':0}
        if command=='cancel-large':
            copy=tx.Transaction.copy_file
            def copying(instance,source,destination,expected=None):
                active['large']=source.name=='large-runtime.bin'
                return copy(instance,source,destination,expected)
            stack.enter_context(patch.object(tx.Transaction,'copy_file',copying))
        def fault(point):
            pause(base,point,target)
            signal=False
            if command=='cancel-large' and point=='copy_chunk' and active['large']:
                active['chunks']+=1; signal=active['chunks']==128
            if command=='tamper-stage' and point=='before_verify': signal=True
            if signal:
                dump(base/'checkpoint.json',dict(pid=os.getpid(),point=point))
                deadline=time.monotonic()+20
                while not (base/'resume.signal').exists() and time.monotonic()<deadline: time.sleep(.005)
                if not (base/'resume.signal').exists(): raise RuntimeError('missing parent signal')
        txn=tx.Transaction(base/'new-source',base/'installed',ids,base/'Uninstall.exe',
            registry=Registry(base),shortcut=Shortcut(base,target),
            cancel=lambda:(base/'cancel.signal').exists(),fault=fault)
        if command in ('install','cancel-large','tamper-stage'): return txn.run()
        if command=='recover': txn.recover(); return dict(status='PASS')
        if command=='gate':
            return txn.verify(base/'installed',None,False)
        raise ValueError(command)

def call(base,command,target=''):
    # Windows venv python.exe may be a redirector; use the actual interpreter
    # so the parent owns the process whose checkpoint PID it verifies.
    args=[getattr(sys,'_base_executable',sys.executable),'-B','-m','scripts.test_install_process_recovery','--base',str(base),
        '--child',command,'--target',target]
    if not target:
        run=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=30)
        return dict(exit_code=run.returncode,output=run.stdout.strip(),error=run.stderr.strip())
    marker=base/'checkpoint.json'; marker.unlink(missing_ok=True)
    run=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    deadline=time.monotonic()+20
    while not marker.exists() and run.poll() is None and time.monotonic()<deadline: time.sleep(.02)
    if not marker.exists():
        run.kill(); out,err=run.communicate()
        raise RuntimeError('checkpoint not reached: '+repr((out,err)))
    reached=json.loads(marker.read_bytes())
    assert reached['pid']==run.pid
    run.kill(); run.communicate(timeout=10)
    return dict(killed_pid=run.pid,exit_code=run.returncode,checkpoint=target)

def suite(base):
    assert base.is_absolute() and base.as_posix().startswith('D:/CodexScratch/MentorFlow')
    base.mkdir(parents=True,exist_ok=False); results={}
    for point in ['after_swap','registry','shortcut_published','before_receipt_publish','receipt_published','staging_cleanup']:
        case=base/point; case.mkdir()
        old,oldids=source(case,'old-source'); new,newids=source(case,'new-source')
        (case/'Uninstall.exe').write_bytes(b'MZ fixture')
        with ExitStack() as stack:
            adapters(stack); stack.enter_context(patch.object(tx,'cleanup_failed',return_value=0))
            tx.Transaction(old,case/'installed',oldids,case/'Uninstall.exe',
                registry=Registry(case),shortcut=Shortcut(case)).run()
        old_receipt=(case/'installed'/trust.RECEIPT).read_bytes()
        dump(case/'new-ids.json',newids)
        killed=call(case,'install',point)
        gate=call(case,'gate')
        recovery=call(case,'recover')
        preserved=((case/'installed/App/MentorFlow.exe').read_bytes()==b'old-source' and
            (case/'installed'/trust.RECEIPT).read_bytes()==old_receipt)
        committed=point in ('receipt_published','staging_cleanup')
        new_preserved=(case/'installed/App/MentorFlow.exe').read_bytes()==b'new-source'
        passed=(recovery['exit_code']==0 and ((committed and gate['exit_code']==0 and new_preserved)
            or (not committed and gate['exit_code']!=0 and preserved)))
        results[point]=dict(status='PASS' if passed else 'FAIL',kill=killed,gate_before_recovery=gate,
            recovery=recovery,old_preserved=preserved,new_committed=committed and new_preserved)
    case=base/'cleanup'; case.mkdir(); root=case/'failed'; root.mkdir()
    payload,ids=source(root,'App'); (payload/'_internal/demo.dist-info/INSTALLER').unlink()
    (case/'cleanup-pin.txt').write_text(ids['manifest_sha256'])
    killed=call(case,'cleanup','cleanup_deleted'); recovery=call(case,'cleanup')
    results['cleanup']=dict(kill=killed,recovery=recovery)
    for kind in ('unknown','modified'):
        case=base/('cleanup-'+kind); case.mkdir(); root=case/'failed'; root.mkdir()
        payload,ids=source(root,'App'); (payload/'_internal/demo.dist-info/INSTALLER').unlink()
        (case/'cleanup-pin.txt').write_text(ids['manifest_sha256'])
        killed=call(case,'cleanup','cleanup_deleted')
        victim=payload/('user-note.txt' if kind=='unknown' else 'SELF-CHECK/MentorFlowSelfCheck.exe')
        victim.write_bytes(b'preserve this changed data')
        before={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
        outcome=call(case,'cleanup')
        after={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
        results['cleanup-'+kind]=dict(status='PASS' if outcome['exit_code']!=0 and before==after else 'FAIL',
            recovery=outcome,all_remaining_bytes_preserved=before==after)
    dump(base/'results.json',results); print(json.dumps(results,ensure_ascii=False))
    assert recovery['exit_code']==0 and all(r.get('status','PASS')=='PASS' for r in results.values())

def signals(base):
    assert base.as_posix().startswith('D:/CodexScratch/MentorFlow')
    base.mkdir(parents=True,exist_ok=False); results={}
    for command in ('cancel-large','tamper-stage'):
        case=base/command; case.mkdir()
        old,oldids=source(case,'old-source'); new,newids=source(case,'new-source')
        if command=='cancel-large':
            with (new/'large-runtime.bin').open('wb') as stream:
                for _ in range(32): stream.write(b'L'*1024**2)
            newids['manifest_sha256']=tx.sha(v.build_manifest(new))
        (case/'Uninstall.exe').write_bytes(b'MZ fixture')
        with ExitStack() as stack:
            adapters(stack); stack.enter_context(patch.object(tx,'cleanup_failed',return_value=0))
            tx.Transaction(old,case/'installed',oldids,case/'Uninstall.exe',registry=Registry(case),shortcut=Shortcut(case)).run()
        old_receipt=(case/'installed'/trust.RECEIPT).read_bytes(); old_registry=Registry(case).snapshot()
        old_shortcut=Shortcut(case).snapshot(); dump(case/'new-ids.json',newids)
        child_args=[getattr(sys,'_base_executable',sys.executable),'-B','-m','scripts.test_install_process_recovery',
            '--base',str(case),'--child',command]
        process=subprocess.Popen(child_args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        marker=case/'checkpoint.json'; deadline=time.monotonic()+20
        while not marker.exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.01)
        if not marker.exists():
            process.kill(); process.communicate(); raise RuntimeError('signal checkpoint missing')
        assert json.loads(marker.read_bytes())['pid']==process.pid
        started=time.perf_counter()
        if command=='cancel-large': (case/'cancel.signal').write_text('cancel')
        else:
            staged=list((case/'installed'/tx.STAGING).glob('*/new/App/MentorFlow.exe'))
            assert len(staged)==1; staged[0].write_bytes(b'forced staged corruption')
        (case/'resume.signal').write_text('resume')
        out,err=process.communicate(timeout=20); elapsed=time.perf_counter()-started
        preserved=((case/'installed'/trust.RECEIPT).read_bytes()==old_receipt and Registry(case).snapshot()==old_registry
            and Shortcut(case).snapshot()==old_shortcut and (case/'installed/App/MentorFlow.exe').read_bytes()==b'old-source')
        results[command]=dict(status='PASS' if process.returncode!=0 and preserved else 'FAIL',
            old_preserved=preserved,signal_to_exit_seconds=elapsed,exit_code=process.returncode,
            output=out.decode('utf-8'),error=err.decode('utf-8'),gui_click=False)
    dump(base/'results.json',results); print(json.dumps(results,ensure_ascii=False))
    assert all(row['status']=='PASS' for row in results.values())

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--child'); parser.add_argument('--target',default=''); parser.add_argument('--signals',action='store_true')
    args=parser.parse_args()
    try:
        if args.child: print(json.dumps(child(args.base,args.child,args.target),ensure_ascii=False))
        elif args.signals: signals(args.base)
        else: suite(args.base)
    except Exception as error:
        print(json.dumps(dict(status='FAIL',type=type(error).__name__,error=str(error)),ensure_ascii=False)); return 1
    return 0

if __name__=='__main__': raise SystemExit(main())

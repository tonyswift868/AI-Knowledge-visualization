"""Installed onedir coordinator/helper entry. No production bypass/test flags."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from desktop.update_protocol import canonical,strict_json,digest,verify_release,SecurityError
from desktop import update_request
from desktop.windows_process_peer import ProcessPeer
from desktop.windows_update_pipe import Listener,connect
from scripts.install_product import configured
from scripts import windows_install_trust as trust
from scripts import windows_install_transaction as tx
from scripts import update_health,update_handoff,update_preflight

CONTROL='mentorflow.update-control.v2'


def now_hex():
    kernel=c.WinDLL('kernel32');value=w.FILETIME()
    kernel.GetSystemTimeAsFileTime.argtypes=[c.POINTER(w.FILETIME)]
    kernel.GetSystemTimeAsFileTime(c.byref(value))
    return f'{(value.dwHighDateTime<<32)|value.dwLowDateTime:016x}'


def require_self(root,high):
    root=tx.validate_target(root)
    if not getattr(sys,'frozen',False):raise SecurityError('installed frozen update runtime required')
    with ProcessPeer(os.getpid()) as peer:
        peer.require(executable=root/'UpdateRuntime/UpdateRuntime.exe',integrity_rid=12288 if high else 8192)
    trust.assert_safe_parents(root/'UpdateRuntime/UpdateRuntime.exe')
    trust.assert_protected(root/'UpdateRuntime/UpdateRuntime.exe')
    return root


def message(doc,kind,**values):
    return dict(schema=CONTROL,attempt_id=doc['attempt_id'],nonce=doc['nonce'],kind=kind,payload=values)


def receive(channel,doc,*,timeout):
    item=channel.receive(timeout=timeout)
    if (not isinstance(item,dict) or set(item)!={'schema','attempt_id','nonce','kind','payload'} or
            item['schema']!=CONTROL or item['attempt_id']!=doc['attempt_id'] or item['nonce']!=doc['nonce'] or
            not isinstance(item['kind'],str) or not isinstance(item['payload'],dict)):
        raise SecurityError('update control peer/correlation/schema mismatch')
    return item['kind'],item['payload']


class HelperTransport:
    def __init__(self,root,doc,control,health):
        self.root=root;self.doc=doc;self.control=control;self.health_channel=health
        self.not_before=now_hex();self.reported_launch_failure=False
    def send(self,kind,**values):
        if kind in ('APPLY_COMMITTED','ROLLBACK_COMMITTED'):self.not_before=now_hex()
        self.control.send(message(self.doc,kind,**values))
    def started(self,binding,*,timeout):
        kind,payload=receive(self.control,self.doc,timeout=timeout)
        if kind=='TARGET_START_FAILED' and payload=={}:
            self.reported_launch_failure=True
            raise RuntimeError('coordinator could not start target')
        if kind!='TARGET_STARTED' or set(payload)!={'pid'}:raise SecurityError('target start acknowledgement required')
        peer=ProcessPeer(payload['pid'])
        # Retain even a quickly exited bad target; its handle must remain owned
        # through the failure decision, not replaced by an unrelated reused PID.
        try:
            peer.require(executable=self.root/'App/MentorFlow.exe',integrity_rid=8192,not_before_hex=self.not_before)
            self.send('RESUME_TARGET',pid=peer.pid)
        except BaseException:
            peer.close();raise
        return peer
    def health(self,peer,binding,attempt_id,nonce,*,timeout):
        try:
            actual=self.health_channel.accept(timeout=timeout)
            if actual!=peer.pid:raise SecurityError('health IPC client is not the launched target')
            signal=self.health_channel.receive(timeout=5)
            evidence=update_health.attest(self.root,signal,peer=peer,attempt_id=attempt_id,nonce=nonce,
                version=binding['version'],manifest_sha256=binding['manifest_sha256'],not_before_hex=self.not_before)
            self.health_channel.send({'health_received':True})
            return evidence
        finally:self.health_channel.reset()
    def stop_owned(self,peer,*,timeout):
        self.send('STOP_TARGET',pid=peer.pid)
        kind,payload=receive(self.control,self.doc,timeout=timeout)
        if kind!='TARGET_STOPPED' or payload!={'pid':peer.pid} or peer.alive():
            raise SecurityError('failed target has not exited; pending transaction preserved')
    def confirm_no_unreported_target(self,*,timeout):
        if not self.reported_launch_failure:
            raise SecurityError('coordinator lost target ownership; pending transaction preserved')


def apply(root,path,pin):
    started=time.monotonic()
    root=require_self(root,True)
    doc,release=update_preflight.accept(root,path,pin,verify_staging=False)
    helper_preflight_ms=(time.monotonic()-started)*1000
    with Listener(doc['attempt_id'],'control') as control,Listener(doc['attempt_id'],'health') as health:
        pid=control.accept(timeout=30)
        if pid!=doc['coordinator_pid']:raise SecurityError('wrong coordinator IPC peer')
        with ProcessPeer(pid) as coordinator:
            info=coordinator.require(executable=root/'UpdateRuntime/UpdateRuntime.exe',integrity_rid=8192)
            if info['created_filetime_hex']!=doc['coordinator_created_hex']:
                raise SecurityError('coordinator PID was reused')
            metadata=root/tx.STAGING/tx.UPDATE_METADATA
            for folder in (root/tx.STAGING,metadata):
                if not folder.exists():trust._create_protected(folder,directory=True)
                trust.assert_protected(folder);tx.assert_no_alias(folder)
            claim=metadata/(doc['attempt_id']+'.claim.json')
            with trust._create_protected(claim,directory=False) as stream:
                stream.write(canonical({'schema':'mentorflow.update-claim.v2','request_sha256':pin,'attempt_id':doc['attempt_id']}))
                stream.flush();os.fsync(stream.fileno())
            transport=HelperTransport(root,doc,control,health)
            from scripts import windows_update_apply as engine
            engine.PRODUCTION_RELEASE_KEYS=update_preflight.trusted_keys()
            return engine.apply_update(root,Path(doc['staged_app']),release.envelope,
                on_committed=lambda transaction,result:update_handoff.committed(transaction,{**result,'helper_preflight_ms':helper_preflight_ms},
                    attempt_id=doc['attempt_id'],nonce=doc['nonce'],transport=transport))


def launch_app(root,binding,*,doc=None,suspended=False):
    args=[str(root/'App/MentorFlow.exe'),'--install-root',str(root)]
    for key in ('manifest_sha256','source_sha','tree_sha'):args+=['--'+key.replace('_','-'),binding[key]]
    if doc:args+=['--update-attempt',doc['attempt_id'],'--update-nonce',doc['nonce']]
    if suspended:
        from desktop.windows_update_child import SuspendedChild
        return SuspendedChild(args,cwd=root)
    return subprocess.Popen(args,cwd=root,close_fds=True)


def runas_once(bridge,path,pin,*,session=None):
    # ShellExecuteEx may wait for Secure Desktop. Bound that wait with an
    # independent thread; there is exactly ONE call and no cancellation retry.
    class SEI(c.Structure):
        _fields_=[('size',w.DWORD),('mask',w.ULONG),('hwnd',w.HWND),('verb',w.LPCWSTR),('file',w.LPCWSTR),
            ('parameters',w.LPCWSTR),('directory',w.LPCWSTR),('show',c.c_int),('instance',w.HINSTANCE),
            ('idlist',c.c_void_p),('className',w.LPCWSTR),('classKey',w.HKEY),('hotkey',w.DWORD),('icon',w.HANDLE),('process',w.HANDLE)]
    done=threading.Event();result={}
    def launch():
        shell=c.WinDLL('shell32',use_last_error=True);ole=c.WinDLL('ole32')
        shell.ShellExecuteExW.argtypes=[c.POINTER(SEI)]
        ole.CoInitializeEx(None,2)
        try:
            info=SEI();info.size=c.sizeof(info);info.mask=0x40|0x100;info.verb='runas';info.file=str(bridge)
            info.parameters=subprocess.list2cmdline(['--apply',str(path),pin]);info.directory=str(bridge.parent);info.show=0
            if session is not None:session.require_running()
            if not shell.ShellExecuteExW(c.byref(info)):
                result['error']=c.get_last_error()
            else:result['handle']=info.process
        except Exception as error:
            result['exception']=error
        finally:ole.CoUninitialize();done.set()
    threading.Thread(target=launch,daemon=True).start()
    if not done.wait(120):raise TimeoutError('UAC consent session expired; not retried')
    if 'exception' in result:raise result['exception']
    if 'error' in result:raise OSError(result['error'],'UAC not accepted or launch failed; not retried')
    return result['handle']


def coordinate(root,intent_path,pin):
    root=require_self(root,False);raw=Path(intent_path).read_bytes()
    from desktop.update_bridge import FIELDS
    if len(raw)>65536 or digest(raw)!=pin:raise SecurityError('update intent changed')
    intent=strict_json(raw)
    if (set(intent)!=FIELDS or intent['schema']!='mentorflow.update-intent.v1' or
            intent['app_id']!=configured().app_id or Path(intent['install_root'])!=root):
        raise SecurityError('unknown update intent')
    stage=Path(intent['staged_app']);envelope=(stage.parent/'release.json').read_bytes()
    if digest(envelope)!=intent['release_sha256']:raise SecurityError('prepared release changed')
    receipt=(root/trust.RECEIPT).read_bytes();binding=strict_json(receipt)['binding']
    release=verify_release(envelope,update_preflight.trusted_keys(),current=binding['version'])
    request_path,request_pin=update_request.produce(stage.parent,app_id=configured().app_id,
        install_root=root,staged_app=stage,base_receipt=receipt,release=release,coordinator_pid=os.getpid())
    doc,_=update_preflight.accept(root,request_path,request_pin,verify_staging=False)
    evidence_path=request_path.with_suffix('.coordinator.json')
    state=dict(schema='mentorflow.coordinator-result.v2',attempt_id=doc['attempt_id'],UPDATE_UAC_PROMPT_COUNT=0,
               status='PREFLIGHT_PASS',request_sha256=request_pin,events=[])
    def mark(event):state['events'].append(dict(event=event,monotonic=time.monotonic()))
    mark('COORDINATOR_PREFLIGHT_COMPLETE')
    def save():
        temp=evidence_path.with_suffix('.pending')
        with temp.open('wb') as stream:stream.write(canonical(state));stream.flush();os.fsync(stream.fileno())
        temp.replace(evidence_path)
    save();child=None;helper=None;base_closed=False
    # UI session signal survives BASE exit; deny only, never release authority.
    from desktop.update_session import UpdateSession, SessionEnding, record_session_deferred
    session=UpdateSession(pid=intent['base_pid'])
    try:
        session.require_running()
        with ProcessPeer(intent['base_pid']) as base:
            base.require(executable=root/'App/MentorFlow.exe',integrity_rid=8192)
            base.visible_responsive_window(intent['base_hwnd_hex'])
            user=c.WinDLL('user32');user.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
            if not user.PostMessageW(int(intent['base_hwnd_hex'],16),0x10,0,0):raise OSError('cannot request BASE close')
            deadline=time.monotonic()+15
            while base.alive() and time.monotonic()<deadline:time.sleep(.05)
            if base.alive():raise RuntimeError('BASE did not close; no UAC requested')
            base_closed=True
        session.require_running()
        state['UPDATE_UAC_PROMPT_COUNT']=1;state['status']='UAC_REQUESTED';mark('UAC_REQUESTED');save()
        helper=runas_once(root/'UpdateBridge.exe',request_path,request_pin,session=session)
        mark('UAC_LAUNCH_RETURNED');save()
        with connect(doc['attempt_id'],'control',timeout=120) as control:
            with ProcessPeer(control.peer_pid()) as peer:
                peer.require(executable=root/'UpdateRuntime/UpdateRuntime.exe',integrity_rid=12288)
                while True:
                    kind,payload=receive(control,doc,timeout=600)
                    if kind in ('APPLY_COMMITTED','ROLLBACK_COMMITTED'):
                        # Read the protected canonical binding independently;
                        # never execute an arbitrary command from IPC.
                        actual=strict_json((root/trust.RECEIPT).read_bytes())['binding']
                        if payload!={'binding':actual}:raise SecurityError('committed binding differs from canonical receipt')
                        if child is not None:child.close();child=None
                        session.require_running()
                        try:child=launch_app(root,actual,doc=doc,suspended=True)
                        except OSError:
                            control.send(message(doc,'TARGET_START_FAILED'));continue
                        state['target_pid']=child.pid;state['status']=kind;mark(kind);save()
                        control.send(message(doc,'TARGET_STARTED',pid=child.pid))
                    elif kind=='RESUME_TARGET':
                        if child is None or payload!={'pid':child.pid}:raise SecurityError('foreign target resume refused')
                        session.require_running()
                        child.resume()
                        mark('TARGET_RESUMED');save()
                    elif kind=='STOP_TARGET':
                        if child is None or payload!={'pid':child.pid}:raise SecurityError('foreign target stop refused')
                        if child.poll() is None:child.terminate()
                        child.wait(timeout=8)
                        control.send(message(doc,'TARGET_STOPPED',pid=child.pid))
                    elif kind in ('TARGET_HEALTHY','PREVIOUS_HEALTHY'):
                        if payload!={'receipt_sha256':tx.sha(root/trust.RECEIPT)}:raise SecurityError('health receipt acknowledgement mismatch')
                        state['status']=kind;mark(kind);save();return state
                    else:raise SecurityError('unknown helper operation')
    except Exception as error:
        if isinstance(error,SessionEnding):
            record_session_deferred(stage,intent['base_pid'],intent['release_sha256'])
        state['status']='FAILED';state['error_type']=type(error).__name__;save()
        if child is not None and child.poll() is None:
            published=strict_json((root/trust.RECEIPT).read_bytes())
            health=published.get('health',{})
            if not (published.get('state')=='HEALTHY' and health.get('attempt_id')==doc['attempt_id'] and
                    health.get('nonce')==doc['nonce'] and health.get('process',{}).get('pid')==child.pid):
                child.terminate();child.wait(timeout=8)
        if base_closed and helper is None and not isinstance(error,SessionEnding):
            # Denied/expired consent is never retried. Restore ordinary BASE
            # usability, only if the unchanged protected receipt still matches.
            session.require_running()
            if (root/trust.RECEIPT).read_bytes()==receipt:launch_app(root,binding)
        raise
    finally:
        session.close()
        if child is not None:child.close()
        if helper:
            kernel=c.WinDLL('kernel32');kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle(helper)


def initial_health(root):
    root=require_self(root,True);record_path=root/tx.STAGING/tx.UPDATE_METADATA/'first-launch.json'
    if record_path.exists():raise SecurityError('first-launch session already issued; not retried')
    receipt=strict_json((root/trust.RECEIPT).read_bytes())
    if receipt.get('state')!='PENDING_HEALTH':raise SecurityError('first-launch receipt is not pending')
    attempt=uuid.uuid4().hex;nonce=os.urandom(32).hex();not_before=now_hex()
    with Listener(attempt,'health') as channel:
        update_health.atomic_record(record_path,dict(schema='mentorflow.first-launch.v1',attempt_id=attempt,nonce=nonce))
        pid=channel.accept(timeout=600)
        with ProcessPeer(pid) as peer:
            signal=channel.receive(timeout=5)
            binding=receipt['binding']
            proof=update_health.attest(root,signal,peer=peer,attempt_id=attempt,nonce=nonce,
                version=binding['version'],manifest_sha256=binding['manifest_sha256'],not_before_hex=not_before)
            result=update_health.finalize(root,proof)
            channel.send({'health_received':True})
            update_health.atomic_record(record_path.with_name('first-launch-result.json'),result)
            return result


def main():
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group(required=True)
    for flag in ('coordinate','apply','initial-health'):mode.add_argument('--'+flag,action='store_true')
    parser.add_argument('--install-root',type=Path,required=True)
    parser.add_argument('--request',type=Path);parser.add_argument('--request-sha256')
    args=parser.parse_args()
    if args.initial_health:return initial_health(args.install_root)
    if args.request is None or args.request_sha256 is None:parser.error('frozen request required')
    return (coordinate if args.coordinate else apply)(args.install_root,args.request,args.request_sha256)


if __name__=='__main__':
    try:print(canonical(main()).decode(),flush=True)
    except Exception as error:
        print(type(error).__name__,file=sys.stderr,flush=True);raise SystemExit(3)

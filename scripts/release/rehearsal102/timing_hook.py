"""Test build only: time real functions without changing their results."""
import os, sys, time, json, functools
from contextlib import contextmanager
from threading import Lock, local
from pathlib import Path

_rehearsal_state = local()
_rehearsal_counts = {}
_rehearsal_counts_lock = Lock()

def record(name, start, outcome, **values):
    try:
        base=Path(os.environ.get('MF_UAT_ROOT','D:/MentorFlow/.local/test-tmp/inapp102c'))
        with (base/'logs'/'stages.jsonl').open('a',encoding='utf8') as stream:
            stream.write(json.dumps(dict(at=time.time(),pid=os.getpid(),exe=sys.executable,
                stage=name,seconds=round(time.perf_counter()-start,4),outcome=outcome,**values))+'\n')
    except Exception: pass

def wrap(module, name):
    original=getattr(module,name,None)
    if original is None:return
    @functools.wraps(original)
    def timed(*args,**kwargs):
        start=time.perf_counter();outcome='ok'
        values={}
        release=next((a for a in args if hasattr(a,'rows')),None)
        if release is not None:
            values=dict(files=len(release.rows),bytes=sum(r['size'] for r in release.rows.values()))
        try:return original(*args,**kwargs)
        except BaseException:
            outcome='error';raise
        finally:record(module.__name__+'.'+name,start,outcome,**values)
    setattr(module,name,timed)

if getattr(sys,'frozen',False):
    # Rehearsal-only network boundary. A stale resource configuration must
    # fail locally rather than ever contact production account/model hosts.
    import httpx
    _send=httpx.Client.send
    _async_send=httpx.AsyncClient.send
    def _local(request):
        if request.url.host not in ('127.0.0.1','localhost'):
            raise RuntimeError('UAT_LOCAL_ONLY: non-local HTTP request blocked')
    def _send_local(self,request,*args,**kwargs):
        _local(request);return _send(self,request,*args,**kwargs)
    async def _async_send_local(self,request,*args,**kwargs):
        _local(request);return await _async_send(self,request,*args,**kwargs)
    httpx.Client.send=_send_local
    httpx.AsyncClient.send=_async_send_local
    from refactor.desktop.delivery import per_user_install as install
    from refactor.desktop.delivery import per_user_update_core as core
    from refactor.desktop.delivery import update_download
    from refactor.desktop.delivery import prepared_update
    original_reconstruct=update_download.ObjectCache.reconstruct
    def reconstruct(self,*args,**kwargs):
        try:return original_reconstruct(self,*args,**kwargs)
        finally:
            try:
                base=Path(os.environ['MF_UAT_ROOT'])
                (base/'logs'/('reconstruction-'+str(os.getpid())+'.json')).write_text(
                    json.dumps(self.telemetry,indent=2),encoding='utf8')
            except (OSError,AttributeError,KeyError):pass
    update_download.ObjectCache.reconstruct=reconstruct

    # Count the cache's optimistic ready probe separately from each lease
    # entered by the install transaction. The transaction's direct lease call
    # occurs under InstallUpdateLock; the ready probe is intentionally outside
    # that lock. These wrappers time context-manager entry only, leaving the
    # original handle lifetime and exceptions untouched.
    def next_rehearsal_call(name):
        with _rehearsal_counts_lock:
            value = _rehearsal_counts.get(name, 0) + 1
            _rehearsal_counts[name] = value
            return value

    original_ready = prepared_update.ready
    @functools.wraps(original_ready)
    def timed_ready(*args, **kwargs):
        start = time.perf_counter()
        _rehearsal_state.ready_depth = getattr(_rehearsal_state, 'ready_depth', 0) + 1
        call = next_rehearsal_call('ready')
        outcome = 'ok'
        try:
            result = original_ready(*args, **kwargs)
            return result
        except BaseException:
            outcome = 'error'
            raise
        finally:
            _rehearsal_state.ready_depth -= 1
            record('prepared_update.ready', start, outcome, call=call,
                   result=bool(locals().get('result', False)))
    prepared_update.ready = timed_ready

    def timed_context(name, original):
        @functools.wraps(original)
        @contextmanager
        def wrapped(*args, **kwargs):
            start = time.perf_counter()
            origin = 'ready' if getattr(_rehearsal_state, 'ready_depth', 0) else 'transaction'
            call = next_rehearsal_call(name)
            outcome = 'ok'
            try:
                with original(*args, **kwargs) as value:
                    # original.__enter__ has completed the file walk/receipt
                    # check at this point; yielding it preserves the caller's
                    # exact read/write handle scope.
                    record('prepared_update.'+name+'_enter', start, outcome,
                           call=call, caller=origin,
                           files=len(value) if isinstance(value, dict) else None,
                           valid=value if isinstance(value, bool) else None)
                    try:
                        yield value
                    except BaseException:
                        outcome = 'error'
                        raise
            except BaseException:
                if outcome != 'error':
                    outcome = 'error'
                    record('prepared_update.'+name+'_enter', start, outcome,
                           call=call, caller=origin)
                raise
        return wrapped

    prepared_update.inventory = timed_context('inventory', prepared_update.inventory)
    prepared_update.lease = timed_context('lease', prepared_update.lease)
    for name in ('_copy','verify_tree','refresh_updater','replace_updater','launch_health','coordinate','transact','handoff_pending'):
        wrap(install,name)
    for name in ('prepare_update','apply_update','resume_pending_on_normal_launch'):
        wrap(core,name)
    # Fault injection is confined to a preview runtime hook and an owned marker.
    if 'MentorFlow.exe' == Path(sys.executable).name:
        from refactor.learning.connections import credentials
        credentials.SERVICE_NAME = os.environ.get('MF_UAT_APP_ID', 'MentorFlow.UAT.fast105') + '.Learning.Models.v1'
        from refactor.desktop import account_shell_main
        original=getattr(account_shell_main.AccountShell,'prepare_update_shutdown',None)
        def shutdown(self,*a,**k):
            marker=Path(os.environ['MF_UAT_ROOT'])/'save-block-once'
            if marker.exists():
                marker.unlink();time.sleep(33);raise OSError('UAT save blocked')
            return original(self,*a,**k)
        if original is not None: account_shell_main.AccountShell.prepare_update_shutdown=shutdown

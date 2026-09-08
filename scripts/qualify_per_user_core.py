"""Real packaged core E2E on a NEVER-activated private Windows desktop.

No installer, UI input, UAC, server account or formal-data access. Native job
ownership bounds the complete child process tree. Release HTTP is loopback-only.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from desktop.update_protocol import canonical, digest, strict_json
from scripts.verify_windows_package import sha256_file


def object_name(user, handle):
    size=w.DWORD()
    user.GetUserObjectInformationW(handle,2,None,0,c.byref(size))
    value=c.create_unicode_buffer(max(2,size.value//2))
    if not user.GetUserObjectInformationW(handle,2,value,c.sizeof(value),c.byref(size)):raise c.WinError()
    return value.value


class PrivateDesktop:
    def __init__(self):
        self.user=c.WinDLL('user32',use_last_error=True);self.kernel=c.WinDLL('kernel32',use_last_error=True)
        self.user.CreateDesktopW.argtypes=[w.LPCWSTR,w.LPCWSTR,c.c_void_p,w.DWORD,w.DWORD,c.c_void_p]
        self.user.CreateDesktopW.restype=w.HANDLE
        self.user.OpenInputDesktop.argtypes=[w.DWORD,w.BOOL,w.DWORD];self.user.OpenInputDesktop.restype=w.HANDLE
        self.user.CloseDesktop.argtypes=[w.HANDLE]
        self.user.GetUserObjectInformationW.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)]
        self.user.GetThreadDesktop.argtypes=[w.DWORD];self.user.GetThreadDesktop.restype=w.HANDLE
        self.user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
        self.name='MF108Core_'+uuid.uuid4().hex
        self.input_before=self.input_name()
        if self.input_before.casefold()!='default':raise RuntimeError('interactive desktop is not normal; do not run')
        self.desktop=self.user.CreateDesktopW(self.name,None,None,0,0x01FF,None)
        if not self.desktop:raise c.WinError(c.get_last_error())
        # Never call SwitchDesktop or change the user's input desktop.
        self.kernel.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];self.kernel.CreateJobObjectW.restype=w.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.job=self.kernel.CreateJobObjectW(None,None)
        class Basic(c.Structure):
            _fields_=[('per_process',c.c_longlong),('per_job',c.c_longlong),('flags',w.DWORD),
                ('min',c.c_size_t),('max',c.c_size_t),('active',w.DWORD),('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(c.Structure):_fields_=[('v'+str(i),c.c_ulonglong) for i in range(6)]
        class Extended(c.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',c.c_size_t),('job_memory',c.c_size_t),('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
        limits=Extended();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.job,9,c.byref(limits),c.sizeof(limits)):raise c.WinError()
        self.children=[]

    def input_name(self):
        handle=self.user.OpenInputDesktop(0,False,1)
        if not handle:raise c.WinError(c.get_last_error())
        try:return object_name(self.user,handle)
        finally:self.user.CloseDesktop(handle)

    def start(self,args,*,cwd,env=None):
        if self.input_name()!=self.input_before:raise RuntimeError('input desktop changed; stopping')
        # CPython's subprocess STARTUPINFO does NOT marshal an arbitrary
        # lpDesktop attribute. Use the real native structure and verify with a
        # no-window child+grandchild probe before any App launch.
        class Startup(c.Structure):
            _fields_=[('cb',w.DWORD),('reserved',w.LPWSTR),('desktop',w.LPWSTR),('title',w.LPWSTR),
                ('x',w.DWORD),('y',w.DWORD),('xs',w.DWORD),('ys',w.DWORD),('xc',w.DWORD),('yc',w.DWORD),
                ('fill',w.DWORD),('flags',w.DWORD),('show',w.WORD),('size',w.WORD),('bytes',c.c_void_p),
                ('stdin',w.HANDLE),('stdout',w.HANDLE),('stderr',w.HANDLE)]
        class Info(c.Structure):_fields_=[('process',w.HANDLE),('thread',w.HANDLE),('pid',w.DWORD),('tid',w.DWORD)]
        self.kernel.CreateProcessW.argtypes=[w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.POINTER(Startup),c.POINTER(Info)]
        self.kernel.ResumeThread.argtypes=[w.HANDLE]
        self.kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
        self.kernel.GetExitCodeProcess.argtypes=[w.HANDLE,c.POINTER(w.DWORD)]
        self.kernel.TerminateProcess.argtypes=[w.HANDLE,w.UINT]
        info=Startup();info.cb=c.sizeof(info);info.desktop='WinSta0\\'+self.name
        child=Info();command=c.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in args]))
        block=c.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted((env or os.environ).items(),key=lambda kv:kv[0].upper()))+'\0\0')
        if not self.kernel.CreateProcessW(str(args[0]),command,None,None,False,0x08000000|0x400|4,block,str(cwd),c.byref(info),c.byref(child)):
            raise c.WinError(c.get_last_error())
        kernel=self.kernel
        class Process:
            pid=child.pid
            _handle=child.process
            def poll(self):
                code=w.DWORD()
                if not kernel.GetExitCodeProcess(self._handle,c.byref(code)):raise c.WinError()
                return None if code.value==259 else code.value
            def wait(self,timeout):
                if kernel.WaitForSingleObject(self._handle,int(timeout*1000))==258:raise subprocess.TimeoutExpired(args,timeout)
                return self.poll()
            def terminate(self):kernel.TerminateProcess(self._handle,3)
        proc=Process()
        try:
            if not kernel.AssignProcessToJobObject(self.job,proc._handle):raise c.WinError(c.get_last_error())
            if kernel.ResumeThread(child.thread)==0xffffffff:raise c.WinError(c.get_last_error())
        except BaseException:
            proc.terminate();kernel.CloseHandle(child.process);raise
        finally:kernel.CloseHandle(child.thread)
        self.children.append(proc)
        return proc

    def verify_routing(self,work):
        self.work=work
        output=work/('desktop-probe-'+self.name+'.json')
        probe=("import ctypes as c,json;from ctypes import wintypes as w;from pathlib import Path;"
            "u=c.WinDLL('user32');k=c.WinDLL('kernel32');u.GetThreadDesktop.argtypes=[w.DWORD];u.GetThreadDesktop.restype=w.HANDLE;"
            "u.GetUserObjectInformationW.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)];"
            "b=c.create_unicode_buffer(256);n=w.DWORD();assert u.GetUserObjectInformationW(u.GetThreadDesktop(k.GetCurrentThreadId()),2,b,c.sizeof(b),c.byref(n));"
            f"Path({str(output)!r}).write_text(json.dumps({{'actual_desktop':b.value,'has_windows':False}}))")
        # A grandchild also tests the inheritance used by the real runtime.
        command=f"import subprocess,sys;subprocess.run([sys.executable,'-c',{probe!r}],check=True,creationflags=subprocess.CREATE_NO_WINDOW)"
        process=self.start([sys.executable,'-c',command],cwd=work)
        assert process.wait(timeout=20)==0
        observed=strict_json(output.read_bytes())
        assert observed['actual_desktop']==self.name and self.input_name()==self.input_before,observed
        return observed

    def inspect_window(self,health,root):
        # HWND lookup is desktop-relative. The observer must share the private
        # desktop; do not mistake inaccessible cross-desktop handles for a
        # failed real runtime health signal or activate the desktop to inspect.
        output=self.work/('window-probe-'+uuid.uuid4().hex+'.json')
        code=("import json;from pathlib import Path;from scripts.qualify_per_user_core import probe_window;"
              f"Path({str(output)!r}).write_text(json.dumps(probe_window({health!r},Path({str(root)!r}))))")
        process=self.start([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1])
        assert process.wait(timeout=20)==0
        evidence=strict_json(output.read_bytes())
        assert evidence['desktop']==self.name and evidence['desktop']!=self.input_before,evidence
        assert self.input_name()==self.input_before
        return evidence

    def close(self):
        self.kernel.CloseHandle(self.job)
        for child in self.children:self.kernel.CloseHandle(child._handle)
        time.sleep(.2)
        self.user.CloseDesktop(self.desktop)


def probe_window(health,root):
    from desktop.windows_process_peer import ProcessPeer
    import psutil
    user=c.WinDLL('user32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
    user.GetThreadDesktop.argtypes=[w.DWORD];user.GetThreadDesktop.restype=w.HANDLE
    user.GetUserObjectInformationW.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)]
    pid=w.DWORD();thread=user.GetWindowThreadProcessId(int(health['hwnd_hex'],16),c.byref(pid))
    assert pid.value==health['pid'] and thread
    name=object_name(user,user.GetThreadDesktop(thread))
    with ProcessPeer(pid.value) as peer:
        process=peer.require(executable=root/'App/MentorFlow.exe',integrity_rid=8192)
        peer.visible_responsive_window(health['hwnd_hex'])
    modules=[m.path for m in psutil.Process(pid.value).memory_maps() if Path(m.path).name.casefold()=='qt6core.dll']
    assert modules and all(Path(path).is_relative_to(root/'App') for path in modules)
    return dict(**health,desktop=name,user_foreground=False,qtcore=modules,process=process)


def run(args):
    work=args.work.absolute();parent=args.parent.absolute()
    allowed=Path('E:/Codex/Temp').resolve()
    if not parent.resolve().is_relative_to(allowed) or (parent.exists() and not args.resume_base):raise ValueError('fresh task-owned E: parent required')
    if not parent.parent.is_dir():raise ValueError('harness parent container missing')
    root=parent/'MentorFlow';payload=work/'release/App'
    report_path=work/('core-e2e-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'.json')
    report=dict(status='RUNNING',parent=str(parent),root=str(root),steps=[],user_foreground_windows_launched=0,
        install_uac_count=0,update_uac_count=0,source=strict_json((work/'build-evidence.json').read_bytes())['source'])
    def save():report_path.write_bytes(canonical(report))
    save();print(str(report_path),flush=True)
    desktop=PrivateDesktop()
    try:
        report['desktop_routing_probe']=desktop.verify_routing(work);save()
        report['private_desktop']=desktop.name;report['input_desktop']=desktop.input_before;save()
        if not args.resume_base:parent.mkdir()  # harness establishes existing parent
        data=parent/'user-data-fixture';sentinel=data/'keep.json'
        if args.resume_base:
            assert sentinel.read_bytes()==canonical(dict(original='retained',plans=[1,2,3]))
            assert (root/'current-release.json').read_bytes()==(work/'release/base.json').read_bytes()
            report['resumed_verified_base']=True
        else:
            data.mkdir();sentinel.write_bytes(canonical(dict(original='retained',plans=[1,2,3])))
        sentinel_hash=sha256_file(sentinel)
        app_env={**os.environ,'LOCALAPPDATA':str(data),'TEMP':str(work),'TMP':str(work)}
        runtime=payload/'UserRuntime/UserRuntime.exe'
        def command(label,action,*extra,expect=0,timeout=180):
            result=work/(report_path.stem+'-'+label+'.json')
            before=time.monotonic()
            proc=desktop.start([runtime,action,'--root',root,'--result',result,*extra],cwd=work,env=app_env)
            code=proc.wait(timeout=timeout)
            doc=strict_json(result.read_bytes()) if result.exists() else {'missing_result':True}
            step=dict(label=label,exit_code=code,seconds=round(time.monotonic()-before,3),result=doc)
            report['steps'].append(step);save();print(canonical(step).decode(),flush=True)
            assert code==expect,(label,code,doc)
            assert desktop.input_name()==desktop.input_before
            return doc
        # This preflight must fail without manufacturing a missing parent/drive.
        result=work/(report_path.stem+'-missing-parent.json')
        proc=desktop.start([runtime,'preflight','--root',parent/'absent/MentorFlow','--result',result],cwd=work)
        assert proc.wait(timeout=30)==3 and not (parent/'absent').exists()
        report['nonexistent_parent_reject']='PASS';save()
        unused=next(letter for letter in 'ZYXWVUTSRQPONMLKJIHGF' if not Path(letter+':/').exists())
        proc=desktop.start([runtime,'preflight','--root',unused+':/MF108/MentorFlow'],cwd=work)
        assert proc.wait(timeout=30)==3
        report['nonexistent_drive_reject']='PASS'
        unknown=parent/'unknown-parent'/'MentorFlow'
        if not args.resume_base:
            unknown.mkdir(parents=True);(unknown/'keep.txt').write_bytes(b'unknown-preserved')
        proc=desktop.start([runtime,'preflight','--parent',unknown.parent],cwd=work)
        assert proc.wait(timeout=30)==3 and (unknown/'keep.txt').read_bytes()==b'unknown-preserved'
        report['unknown_nonempty_reject']='PASS';save()
        assert command('preflight','preflight')['status']=='PASS'
        if not args.resume_base:assert not root.exists()
        before_children={p.name for p in parent.iterdir()}
        if args.resume_base:
            base=command('base-launch','launch')
            report['only_final_child_created']='SEE_ORIGINAL_BASE_INSTALL_EVIDENCE'
        else:
            base=command('base-install','install','--source',payload,'--release',work/'release/base.json',timeout=240)
            assert {p.name for p in parent.iterdir()}-before_children=={'MentorFlow'}
            report['only_final_child_created']='PASS'
        report['base']=desktop.inspect_window(base['health'],root);save()
        runtime=root/'Updater/UserRuntime.exe'  # actual installed stable coordinator
        assert command('existing-install','preflight')['existing'] is True
        # Loopback feed has a finite lifetime and belongs to the same job.
        (work/'feed-selection.json').write_bytes(canonical(dict(release='target')))
        server=desktop.start([sys.executable,'-B','-m','scripts.serve_per_user_fixture','--work',work,'--seconds','1800'],
            cwd=Path(__file__).resolve().parents[1])
        import socket
        deadline=time.monotonic()+10
        while True:
            if server.poll() is not None:raise RuntimeError('loopback fixture failed to start')
            try:
                with socket.create_connection(('127.0.0.1',48208),timeout=.2):break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
        prepared=command('prepare-target','prepare-update',timeout=240)
        assert prepared['state']=='UPDATE_DOWNLOADED'
        assert prepared['update_path']=='FILE_LEVEL_INCREMENTAL'
        assert prepared['actual_downloaded_bytes']<prepared['full_bytes']/10
        pending=strict_json((root/'pending-update.json').read_bytes())
        staged=Path(pending['stage']);fixture=staged/'mentorflow-version.json';original=fixture.read_bytes()
        fixture.write_bytes(b'corrupted-owned-test-asset')
        try:
            refused=command('corrupt-cache','apply-pending','--pid',str(base['health']['pid']),
                            '--hwnd-hex',base['health']['hwnd_hex'],expect=3)
            assert refused['error']=='SecurityError'
            desktop.inspect_window(base['health'],root)
            report['corrupt_cache_reject']='PASS'
        finally:fixture.write_bytes(original)
        for reason in ('shutdown','logoff','unsafe'):
            deferred=command('defer-'+reason,'apply-pending','--exit-reason',reason)
            assert deferred['state']=='PENDING_INSTALL' and not deferred['destructive_apply']
        # Restore a normal next-launch path with the real BASE safely closed.
        command('close-base','shutdown','--pid',str(base['health']['pid']),'--hwnd-hex',base['health']['hwnd_hex'])
        good=command('resume-good','apply-pending','--exit-reason','next_launch',timeout=240)
        assert good['version']=='1.3.1' and good['health']['ready']
        report['target']=desktop.inspect_window(good['health'],root)
        assert (root/'previous/App/MentorFlow.exe').exists()
        assert strict_json((root/'previous/release.json').read_bytes())['signed']['version']=='1.3.0'
        report['good_update_e2e']='PASS';save()
        (work/'feed-selection.json').write_bytes(canonical(dict(release='bad')))
        badprep=command('prepare-bad','prepare-update',timeout=240)
        assert badprep['state']=='UPDATE_DOWNLOADED'
        command('apply-bad','apply-pending','--pid',str(good['health']['pid']),'--hwnd-hex',good['health']['hwnd_hex'],expect=3,timeout=240)
        recovery=strict_json((root/'last-recovery.json').read_bytes())
        assert recovery['version']=='1.3.1' and recovery['health']['ready']
        report['recovery']=desktop.inspect_window(recovery['health'],root)
        assert command('recovery-state','update-status')['state']=='PREVIOUS_HEALTHY'
        report['bad_update_recovery_e2e']='PASS'
        assert sha256_file(sentinel)==sentinel_hash
        report['user_data_fixture_preserved']='PASS';report['status']='PASS';save()
        command('close-restored','shutdown','--pid',str(recovery['health']['pid']),'--hwnd-hex',recovery['health']['hwnd_hex'])
        report['all_owned_apps_closed']=True;save()
    except BaseException as error:
        report['status']='FAIL';report['error']=type(error).__name__+': '+str(error);save();raise
    finally:
        desktop.close()
    return report_path


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--work',type=Path,required=True);parser.add_argument('--parent',type=Path,required=True)
    parser.add_argument('--resume-base',action='store_true',help='Resume only a signed identical BASE and exact owned fixture sentinel')
    print(run(parser.parse_args()),flush=True)

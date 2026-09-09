"""One-click packaged fixture E2E; never activate a desktop or start an installer.

The loopback fixture is explicitly test-only and not static-feed qualification.
All child processes inherit a verified private desktop and kill-on-close job.
"""
import argparse
import os
from pathlib import Path
import sys
import time

from desktop.update_protocol import canonical, strict_json, digest
from scripts.qualify_per_user_core import PrivateDesktop


def run(args):
    work = args.work.absolute(); parent = args.parent.absolute()
    allowed = Path('E:/Codex/Temp').resolve()
    if not parent.resolve().is_relative_to(allowed) or parent.exists():
        raise ValueError('fresh owned parent below E:/Codex/Temp required')
    report_path = work / ('one-click-e2e-' + time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()) + '.json')
    report = dict(status='RUNNING', steps=[], static_feed_binding='PENDING_04',
        fixture_only=True, foreground_windows=0, uac_count=0, installer_count=0,
        browser_launch_count=0, source=strict_json((work/'build-evidence.json').read_bytes())['source'])
    def save(): report_path.write_bytes(canonical(report))
    save(); print(str(report_path), flush=True)
    desktop = PrivateDesktop()
    try:
        report['routing'] = desktop.verify_routing(work)
        report['desktop'] = desktop.name; report['input_desktop'] = desktop.input_before; save()
        parent.mkdir()
        data = parent / 'fixture-data'; data.mkdir()
        sentinel = data/'plans-fixture.json'; sentinel.write_bytes(canonical(dict(plans=[1,2,3], owned='test-only')))
        before = digest(sentinel.read_bytes())
        env = {**os.environ, 'LOCALAPPDATA':str(data), 'TEMP':str(work), 'TMP':str(work)}
        payload = work/'release/App'; runtime = payload/'UserRuntime/UserRuntime.exe'
        def command(label, root, action, *extra, timeout=420):
            result = work / (report_path.stem + '-' + label + '.json')
            started = time.monotonic()
            process = desktop.start([runtime,action,'--root',root,'--result',result,*extra],cwd=work,env=env)
            code = process.wait(timeout=timeout)
            doc = strict_json(result.read_bytes()) if result.exists() else {'missing_result':True}
            step = dict(label=label,code=code,seconds=round(time.monotonic()-started,3),result=doc)
            report['steps'].append(step); save(); print(canonical(step).decode(),flush=True)
            assert code == 0, step
            assert desktop.input_name() == desktop.input_before
            return doc
        root = parent/'MentorFlow'
        base = command('base-install', root, 'install','--source',payload,'--release',work/'release/base.json')
        report['base'] = desktop.inspect_window(base['health'],root); save()
        runtime = root/'Updater/UserRuntime.exe'
        offline = command('offline',root,'update-to-latest','--pid',str(base['health']['pid']),'--hwnd-hex',base['health']['hwnd_hex'])
        assert offline['state'] == 'FAILED_RECOVERABLE'
        desktop.inspect_window(base['health'],root)
        (work/'feed-selection.json').write_bytes(canonical(dict(release='target')))
        server = desktop.start([sys.executable,'-B','-m','scripts.serve_per_user_fixture','--work',work,'--seconds','1800'],
                               cwd=Path(__file__).resolve().parents[1])
        import socket
        deadline = time.monotonic()+10
        while True:
            if server.poll() is not None: raise RuntimeError('fixture server stopped')
            try:
                with socket.create_connection(('127.0.0.1',48208),timeout=.2): break
            except OSError:
                if time.monotonic()>deadline: raise
                time.sleep(.1)
        checked = command('check-latest',root,'check-latest')
        assert checked['state'] == 'UPDATE_AVAILABLE' and checked['latest'] == '1.4.1'
        cancel = work / (report_path.stem+'-cancel.flag'); cancel.write_bytes(b'test cancel')
        cancelled = command('cancel',root,'update-to-latest','--pid',str(base['health']['pid']),
                            '--hwnd-hex',base['health']['hwnd_hex'],'--cancel-file',cancel)
        assert cancelled['state'] == 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
        desktop.inspect_window(base['health'],root)
        good = command('one-click-good',root,'update-to-latest','--pid',str(base['health']['pid']),'--hwnd-hex',base['health']['hwnd_hex'])
        assert good['state'] == 'CURRENT_HEALTHY'
        assert good['update_path'] == 'FILE_LEVEL_INCREMENTAL'
        assert 0 < good['actual_downloaded_bytes'] < good['full_bytes']/10
        report['good'] = desktop.inspect_window(good['health'],root)
        assert (root/'previous/App/MentorFlow.exe').is_file(); save()
        (work/'feed-selection.json').write_bytes(canonical(dict(release='bad')))
        bad = command('one-click-bad',root,'update-to-latest','--pid',str(good['health']['pid']),'--hwnd-hex',good['health']['hwnd_hex'])
        assert bad['state'] == 'FAILED_RECOVERABLE'
        recovery = strict_json((root/'last-recovery.json').read_bytes())
        assert recovery['version'] == '1.4.1'
        report['recovered'] = desktop.inspect_window(recovery['health'],root); save()
        command('close-recovered',root,'shutdown','--pid',str(recovery['health']['pid']),'--hwnd-hex',recovery['health']['hwnd_hex'])
        # Separate signed BASE tests deferred application without replaying the
        # first installation's higher bad-target metadata witness.
        second_parent = parent/'next-launch'; second_parent.mkdir()
        second_root = second_parent/'MentorFlow'
        runtime = payload/'UserRuntime/UserRuntime.exe'
        second = command('second-base',second_root,'install','--source',payload,'--release',work/'release/base.json')
        report['second_base'] = desktop.inspect_window(second['health'],second_root); save()
        runtime = second_root/'Updater/UserRuntime.exe'
        (work/'feed-selection.json').write_bytes(canonical(dict(release='target')))
        pending = command('shutdown-defer',second_root,'update-to-latest','--pid',str(second['health']['pid']),
                          '--hwnd-hex',second['health']['hwnd_hex'],'--exit-reason','shutdown')
        assert pending['state'] == 'PENDING_INSTALL' and not pending['destructive_apply']
        desktop.inspect_window(second['health'],second_root)
        command('close-second-base',second_root,'shutdown','--pid',str(second['health']['pid']),'--hwnd-hex',second['health']['hwnd_hex'])
        resumed = command('next-normal-launch',second_root,'launch')
        assert resumed['state'] == 'CURRENT_HEALTHY'
        report['next_launch'] = desktop.inspect_window(resumed['health'],second_root)
        command('close-next-target',second_root,'shutdown','--pid',str(resumed['health']['pid']),'--hwnd-hex',resumed['health']['hwnd_hex'])
        assert digest(sentinel.read_bytes()) == before
        report['user_fixture_preserved'] = True
        report['status'] = 'PASS'
    except BaseException as error:
        report['status'] = 'FAIL'; report['error'] = dict(type=type(error).__name__,message=str(error))
        raise
    finally:
        desktop.close(); report['owned_job_closed'] = True; save()
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--parent',type=Path,required=True)
    run(parser.parse_args())

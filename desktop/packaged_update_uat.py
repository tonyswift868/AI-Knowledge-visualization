"""Finite UAT-only packaged check/download measurement; no apply authority."""
import base64
import ctypes
import json
import os
from pathlib import Path
import sys
import time


def fast_materialization():
    """Frozen real-payload performance only; no network/apply/install action."""
    if not getattr(sys,'frozen',False) or ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError('standard-user frozen UAT required')
    config=json.loads((Path(sys._MEIPASS)/'update-uat/public.json').read_bytes())
    root=Path(config['root'])
    if root not in (Path('D:/MFU7'),Path('D:/MFU8')) or Path(sys.executable).absolute()!=root/'base/App/MentorFlow.exe':
        raise RuntimeError('dispatch short isolated root required')
    from desktop.update_protocol import verify_release,make_plan,MANIFEST_PATH,digest,canonical
    from desktop.update_download import ObjectCache,UpdateTelemetry
    from desktop.update_tree_security import AcceptedApp
    from scripts.runtime_path_budget import budget
    app=root/'base/App'; raw=(app/MANIFEST_PATH).read_bytes()
    rows={r['path']:r for r in json.loads(raw)['files']}
    rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(raw),sha256=digest(raw))
    release=verify_release((root/'release-happy.json').read_bytes(),
        {config['key_id']:base64.b64decode(config['public_key'])},current='1.1.0')
    admission=budget(app.parent,release.rows)
    evidence=root/'fast-result.json'
    if evidence.exists(): raise RuntimeError('existing fast evidence preserved')
    accepted_start=time.monotonic()
    receipt_start=time.monotonic()
    receipt=json.loads((app.parent/'installation-receipt.json').read_bytes()) if root==Path('D:/MFU8') else None
    receipt_ms=(time.monotonic()-receipt_start)*1000
    with AcceptedApp(app,rows,digest(raw),receipt=receipt) as accepted:
        acceptance_ms=(time.monotonic()-accepted_start)*1000
        cache=ObjectCache(root/'c',None,runtime_root=app.parent/'.u',accepted_app=accepted)
        plan=make_plan(release,rows,'1.1.0'); trace=UpdateTelemetry()
        start=time.monotonic()
        target=cache.reconstruct(release,plan,app,telemetry=trace)
        trace.mark('T14_READY_TO_APPLY')
        elapsed=(time.monotonic()-start)*1000
        samples=[]
        for path in ('MentorFlow.exe','_internal/PySide6/QtCore.pyd','_internal/python313.dll'):
            original=app/path; dest=target/path
            if original.exists() and dest.exists():
                samples.append(dict(path=path,samefile=original.samefile(dest),
                    source_inode=original.stat().st_ino,target_inode=dest.stat().st_ino,
                    source_links=original.stat().st_nlink,target_links=dest.stat().st_nlink,
                    source_sha256=digest(original.read_bytes()),target_sha256=digest(dest.read_bytes())))
        changed=[p for p in release.rows if rows.get(p)!=release.rows[p]]
        independent=all(not (app/p).samefile(target/p) for p in changed if (app/p).exists())
        evidence.write_bytes(canonical(dict(packaged=True,pid=os.getpid(),state='READY_TO_APPLY',
            target=str(target),acceptance_ms=acceptance_ms,rebuild_ms=elapsed,
            source_profile=dict(accepted.profile,SOURCE_RECEIPT_LOAD_MS=receipt_ms),
            source_identity_lease=accepted.lease,
            target_manifest_verify='PASS_REAL_PACKAGED',telemetry=trace.report(),
            samples=samples,changed_independent=independent,path_budget=admission,
            current_manifest_sha256=digest(raw),source_sha=config['source_sha'],tree_sha=config['tree_sha'])))
    return 0


def startup(python_entry):
    """UAT-only event-loop health, not an install receipt/integrity authority.

    Full target verification remains in reconstruct and pre-apply. Repeating
    the 2 GB scan before QApplication is neither startup health nor useful
    evidence of a DLL/Qt failure.
    """
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('real frozen executable required')
    config = json.loads((Path(sys._MEIPASS)/'update-uat/public.json').read_bytes())
    root = Path(config['root']).absolute()
    if root not in (Path('D:/MFU7'),Path('D:/MFU8')) and not root.is_relative_to(Path('D:/CodexScratch/MentorFlow')):
        raise RuntimeError('isolated UAT root required')
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError('standard user required')
    executable = Path(str(Path(sys.executable).absolute()).removeprefix('\\\\?\\'))
    if not executable.is_relative_to(root) or executable.name != 'MentorFlow.exe':
        raise RuntimeError('unexpected UAT executable location')
    request = json.loads((root/'startup-request.json').read_bytes())
    if set(request) != {'case'} or request['case'] not in {'base', 'direct', 'reconstructed', 'near-limit'}:
        raise RuntimeError('invalid startup request')
    output = root/('startup-'+request['case']+'.json')
    if output.exists():
        raise RuntimeError('existing startup evidence preserved')
    marks = dict(PYTHON_ENTRY_READY=python_entry,
                 PYINSTALLER_BOOTSTRAP_READY=python_entry)
    # Python entry is the first observable post-bootloader point; do not claim
    # an independently measured internal C bootloader timestamp.
    result = dict(packaged=True, pid=os.getpid(), executable=str(executable),
        sys_meipass=sys._MEIPASS, internal_root=str(executable.parent/'_internal'),
        real_product_health=False, full_package_verify=False, marks=marks)
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        marks['QT_IMPORT_READY'] = time.monotonic()
        from desktop.update_panel import UpdatePanel
        from desktop.version import VERSION_MANIFEST
        if request['case'] != 'base':
            from desktop.update_protocol import verify_release
            release = verify_release((root/'release-happy.json').read_bytes(),
                {config['key_id']:base64.b64decode(config['public_key'],validate=True)},current='1.1.0')
            if VERSION_MANIFEST['version'] != release.version:
                raise RuntimeError('runtime version differs from signed target')
        app = QApplication([str(executable)])
        marks['QAPPLICATION_READY'] = time.monotonic()
        panel = UpdatePanel(service=None)
        marks['WINDOW_CONSTRUCTED'] = time.monotonic()
        panel.show()
        def ready():
            if not panel.isVisible():
                result['fatal_error'] = 'window not visible'
            else:
                marks['WINDOW_READY'] = time.monotonic()
                result.update(version=VERSION_MANIFEST['version'],
                    version_label=panel.version_label.text(), qt_event_loop_ready=True,
                    qtcore_module_path=sys.modules['PySide6.QtCore'].__file__)
            output.write_text(json.dumps(result,indent=2),encoding='utf-8')
            panel.shutdown(); panel.close(); app.quit()
        QTimer.singleShot(200, ready)
        return app.exec()
    except Exception as exc:
        result['fatal_error'] = type(exc).__name__ + ': ' + str(exc)
        output.write_text(json.dumps(result,indent=2),encoding='utf-8')
        raise


def main():
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('real frozen executable required')
    config = json.loads((Path(sys._MEIPASS)/'update-uat/public.json').read_bytes())
    root = Path(config['root']).absolute()
    if root != Path('D:/MFU7') and not root.is_relative_to(Path('D:/CodexScratch/MentorFlow')):
        raise RuntimeError('isolated UAT root required')
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError('UAT check/download must not be elevated')
    from desktop.update_download import HTTPTransport
    from desktop.update_service import MatureUpdateService
    from desktop.update_protocol import digest, canonical
    from scripts import windows_update_apply
    from desktop.system_page import SystemPage
    from desktop.update_panel import UpdatePanel
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    # This marker cannot enable this mode in a production build: only the
    # separate build-selected entry handles the qualification argument.
    executable = Path(sys.executable).absolute()
    if executable != root/'base/App/MentorFlow.exe':
        # The reconstructed target is a complete second packaged version, not
        # another source-Python fixture. This mode proves frozen loading only;
        # it does NOT publish an installation receipt or report product health.
        normalized = Path(str(executable).removeprefix('\\\\?\\'))
        if not normalized.is_relative_to(root/'cache-happy') or executable.name != 'MentorFlow.exe':
            raise RuntimeError('unexpected UAT executable location')
        from desktop.update_protocol import verify_release
        signed = verify_release((root/'release-happy.json').read_bytes(),
            {config['key_id']:base64.b64decode(config['public_key'],validate=True)},current='1.1.0')
        # Explicit updater/pre-apply verification owns complete integrity.
        # This finite frozen health probe only checks runtime/Qt/version READY.
        from desktop.version import VERSION_MANIFEST
        if VERSION_MANIFEST['version'] != signed.version:
            raise RuntimeError('target visible version differs from signed release')
        target_app=QApplication([str(executable)])
        target_panel=UpdatePanel(service=None); target_panel.show()
        target_result=root/'target-start.json'
        if target_result.exists(): raise RuntimeError('existing target evidence preserved')
        def target_ready():
            target_result.write_bytes(canonical(dict(packaged=True,pid=os.getpid(),
                elevation='STANDARD_USER',version=VERSION_MANIFEST['version'],
                version_label=target_panel.version_label.text(),qtcore_module_path=sys.modules['PySide6.QtCore'].__file__,
                executable=str(executable),app_exe_sha256=digest(executable.read_bytes()),
                real_product_health=False,scope='signed reconstructed target frozen UpdatePanel startup')))
            target_panel.shutdown(); target_panel.close(); target_app.quit()
        QTimer.singleShot(200,target_ready)
        return target_app.exec()
    request = json.loads((root/'request.json').read_bytes())
    if set(request) != {'port', 'case'} or type(request['port']) is not int or not 1024 <= request['port'] <= 65535:
        raise RuntimeError('invalid local UAT request')
    case = request['case']
    if case not in {'happy', 'tampered', 'expired', 'replay', 'corrupt', 'cancel', 'resume', 'offline', 'fallback'}:
        raise RuntimeError('invalid UAT case')
    origin = 'http://127.0.0.1:' + str(request['port'])
    transport = HTTPTransport(origin, fixture_http=True, connect=2, read=3, total=30)
    current = json.loads((executable.parent/'mentorflow-version.json').read_bytes())['version']
    service = MatureUpdateService(install_root=executable.parent.parent,
        cache_root=root/('cache-'+('happy' if case == 'replay' else 'cancel' if case == 'resume' else case)),
        metadata_url=origin+'/stable.json', public_keys={config['key_id']:base64.b64decode(config['public_key'],validate=True)},
        current=current, transport=transport)
    app = QApplication([str(executable)])
    app.setApplicationName('MentorFlow Packaged Update UAT')
    # Reuse the exact production Settings card. No parallel updater page is
    # added to the product. SystemPage module import also proves frozen closure.
    panel = UpdatePanel(service=service, auto_delay_ms=600000)
    panel.setWindowTitle('MentorFlow — isolated packaged updater qualification')
    panel.show()
    started=time.monotonic(); ticks=0; phase='check'; check_total=0
    output=root/('result-'+case+'.json')
    if output.exists(): raise RuntimeError('existing UAT evidence preserved')
    result=dict(packaged=True, executable=str(executable), pid=os.getpid(),
        qtcore_module_path=sys.modules['PySide6.QtCore'].__file__,
        elevation='STANDARD_USER', case=case, main_requested_execution_level='asInvoker',
        ui_scope='real frozen UpdatePanel; not authenticated product main window',
        system_page_import=SystemPage.__name__, apply_module_import=windows_update_apply.__name__,
        source_sha=config['source_sha'], tree_sha=config['tree_sha'])
    timer=QTimer(); timer.setInterval(20)

    def finish(reason):
        timer.stop(); service.cancel_download()
        result.update(reason=reason, snapshot=dict(service.snapshot), qt_event_ticks=ticks,
            telemetry=service.telemetry.report() if getattr(service,'telemetry',None) else None,
            total_http_body_bytes=transport.bytes_received, metadata_body_bytes=check_total,
            elapsed_seconds=round(time.monotonic()-started,3),
            staged=str(service.staged) if service.staged else None,
            packaged_ui_created=panel.isVisible(), main_window_qualified=False,
            app_exe_sha256=digest(executable.read_bytes()))
        output.write_bytes(canonical(result))
        panel.shutdown(); panel.close(); app.quit()

    def tick():
        nonlocal ticks, phase, check_total
        ticks+=1
        if time.monotonic()-started>240:
            finish('BOUNDED_TIMEOUT'); return
        running=panel._thread and panel._thread.is_alive()
        if phase=='check' and not running:
            check_total=transport.bytes_received
            if service.snapshot['state']=='UPDATE_AVAILABLE' and case not in {'tampered','expired','replay','offline'}:
                panel.refresh()  # publish enabled state before clicking the real button
                phase='download'; panel.download_button.click()
            else: finish('CHECK_FINISHED')
        elif phase=='download':
            if case=='cancel' and service.snapshot.get('actual_downloaded_bytes',0)>65536:
                panel.cancel_button.click()
            if not running: finish('DOWNLOAD_FINISHED')
    panel.check_button.click(); timer.timeout.connect(tick); timer.start()
    return app.exec()


def cross_volume():
    """Real frozen engine, D current/cache -> E temp/final; no apply authority."""
    if not getattr(sys,'frozen',False) or ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError('standard-user frozen UAT required')
    config=json.loads((Path(sys._MEIPASS)/'update-uat/public.json').read_bytes())
    root=Path(config['root']); output=root/'cross-volume-result.json'
    if root!=Path('D:/MFU8') or Path(sys.executable).absolute()!=root/'base/App/MentorFlow.exe' or output.exists():
        raise RuntimeError('dispatch-08 isolated executable/evidence required')
    from desktop.update_protocol import verify_release,make_plan,MANIFEST_PATH,digest,canonical
    from desktop.update_download import ObjectCache,UpdateTelemetry
    from desktop.update_tree_security import AcceptedApp,hardlink_eligible,normal
    app=root/'subset/install/App'; raw=(app/MANIFEST_PATH).read_bytes()
    rows={r['path']:r for r in json.loads(raw)['files']}
    rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(raw),sha256=digest(raw))
    release=verify_release((root/'subset/release.json').read_bytes(),
        {config['key_id']:base64.b64decode(config['public_key'])},current='1.1.0')
    stage=Path('E:/Codex/Temp/MFU8-cross-volume')
    if stage.exists(): raise RuntimeError('existing cross-volume fixture preserved')
    stage.mkdir(parents=True)
    before={p:digest((app/p).read_bytes()) for p in rows}
    renames=[]; original_rename=Path.rename; original_link=os.link
    def checked_rename(source,destination):
        source=normal(source); destination=normal(destination)
        if source.suffix=='.part':
            assert source.is_relative_to(stage) and destination.is_relative_to(stage)
            assert source.stat().st_dev==destination.parent.stat().st_dev==stage.stat().st_dev
            renames.append(dict(temp=str(source),target=str(destination),volume=source.stat().st_dev))
        return original_rename(source,destination)
    def forbidden_link(*args,**kwargs): raise AssertionError('cross-volume hardlink attempted')
    try:
        Path.rename=checked_rename; os.link=forbidden_link
        with AcceptedApp(app,rows,digest(raw)) as accepted:
            cache=ObjectCache(root/'subset/cache',None,runtime_root=stage,accepted_app=accepted)
            trace=UpdateTelemetry()
            target=cache.reconstruct(release,make_plan(release,rows,'1.1.0'),app,telemetry=trace)
            full=json.loads((root/'release-happy.json').read_bytes())['signed']['file_manifest']['files']
            assert not hardlink_eligible(root/'base/App',stage)
            assert len(renames)==len(release.rows)
            assert not cache._hardlinks
            assert all(before[p]==digest((app/p).read_bytes()) for p in rows)
            output.write_bytes(canonical(dict(packaged=True,qualification='PASS_REAL_PACKAGED_SUBSET',
                source_sha=config['source_sha'],exe_sha256=digest(Path(sys.executable).read_bytes()),
                target=str(target),source_volume=app.stat().st_dev,target_volume=stage.stat().st_dev,
                full_manifest_files=len(full),full_manifest_strategy='COPY_FALLBACK',
                hardlink_attempts=0,renames=renames,telemetry=trace.report(),previous_hash_preserved=True,
                target_manifest_verify='PASS_REAL_PACKAGED_SUBSET',temp_on_target_volume=True,
                final_rename_same_volume=True)))
    finally:
        Path.rename=original_rename; os.link=original_link
    return 0

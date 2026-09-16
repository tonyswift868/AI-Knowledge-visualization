"""Real pre-login About/update entry and event-loop health notification."""
import os
from pathlib import Path
import threading


def add_installed_release_details(layout, application, manifest):
    """Render installed information offline; copy only the bounded public text."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QPushButton
    from desktop.version import installed_release_details
    details=installed_release_details(manifest)
    if not details: return None
    heading=QLabel('本版本更新');heading.setObjectName('installedReleaseHeading')
    layout.addWidget(heading)
    summary=QLabel(details);summary.setObjectName('installedReleaseDetails')
    summary.setTextFormat(Qt.TextFormat.PlainText);summary.setWordWrap(True)
    summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(summary)
    copy=QPushButton('复制版本信息');copy.setObjectName('copyInstalledVersion')
    def copy_version():
        application.clipboard().setText(details)
        copy.setText('已复制版本信息')
    copy.clicked.connect(copy_version);layout.addWidget(copy)
    return copy


def attach_update_entry(application, gate, *, defer_service=False):
    """One assembly path for packaged and ordinary Desktop login shells."""
    if hasattr(gate, 'update_entry'):
        return gate.update_entry
    from desktop.update_controller import application_updater
    from desktop.update_panel import UpdatePanel
    updater = application_updater((lambda: None) if defer_service else None)
    gate.bind_update_controller(updater)
    from desktop.version import account_capabilities
    gate.set_account_capabilities(None if defer_service else account_capabilities())
    entry = UpdatePanel(gate, controller=updater, compact=True, login_summary=True,
                        auto_delay_ms=60000 if defer_service else 5000)
    gate.update_entry = entry
    gate.update_entry_layout.addWidget(entry)
    entry.setEnabled(not defer_service)
    application.aboutToQuit.connect(entry.shutdown)
    return entry


def attach_updates(application,gate,controller,root,*,attempt_id=None,nonce=None,account_capabilities=None):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel
    from desktop.version import VERSION_MANIFEST,version_display
    from scripts.install_product import configured
    from desktop.update_panel import UpdatePanel
    previous = getattr(application, '_mentorflow_updater', None)
    needs_service = previous is None or previous._closed
    entry = attach_update_entry(application, gate, defer_service=True)
    gate.set_account_capabilities(None)  # No account actions before verified startup.
    updater = entry.controller
    gate.about_button.setText('关于 MentorFlow 与更新')
    gate.about_button.setAccessibleName('关于 MentorFlow 与更新')
    dialogs=[]
    from scripts.per_user_install import is_install
    if is_install(root):
        from scripts.per_user_update_core import attach_shutdown_guard
        attach_shutdown_guard(application)

    def request_update():
        if not controller.results.get('SECURITY_GATE'): return
        if (getattr(controller,'startup_policy',None) or {}).get('state') == 'REPAIR_REQUIRED':
            request_repair()
            return
        from desktop.update_controller import application_updater
        updater = application_updater()
        updater.bind_window(gate)
        updater.run('download')  # existing one-consent download/apply chain

    def request_repair():
        if not controller.results.get('SECURITY_GATE'): return
        updater.bind_window(gate)
        updater.run('repair')  # Same 03 adapter used by the compact panel.

    gate.update_requested.connect(request_update)
    gate.repair_requested.connect(request_repair)

    def show_about():
        if 'SECURITY_GATE' not in controller.results:return
        if dialogs:
            dialogs[0].show();dialogs[0].raise_();return
        about=QDialog(gate);about.setWindowTitle('关于 MentorFlow 与更新')
        about.setObjectName('mentorflowRealAboutUpdates');about.resize(780,440)
        layout=QVBoxLayout(about);layout.addWidget(QLabel(version_display()))
        notice=VERSION_MANIFEST.get('desktop_notice')
        if isinstance(notice,str) and 0<len(notice)<=200:
            label=QLabel(notice);label.setObjectName('desktopReleaseNotice');label.setWordWrap(True)
            layout.addWidget(label)
        add_installed_release_details(layout, application, VERSION_MANIFEST)
        if configured().uat:
            layout.addWidget(QLabel('预览版：账号服务暂未连接，您仍可查看软件版本。'))
        panel=UpdatePanel(about,controller=updater,auto_delay_ms=60000);layout.addWidget(panel)
        if panel.service is not None:
            panel.controller.bind_window(gate)
            panel.refresh()
            if configured().uat:
                attach_uat_measurements(panel)
        about.finished.connect(panel.shutdown)
        about.finished.connect(lambda _:dialogs.clear())
        application.aboutToQuit.connect(panel.shutdown)
        dialogs.append(about);about.show()

    gate.about_requested.connect(show_about)
    timer=QTimer(gate);timer.setInterval(100)
    ready_once = [False]
    service_initialized = [not needs_service]
    def ready():
        if 'SECURITY_GATE' not in controller.results or not gate.isVisible():return
        verified = controller.results['SECURITY_GATE'] is True
        if not verified:
            gate.set_account_capabilities(None)
        updater.set_installation_verified(verified)
        entry.setEnabled(True)
        gate.about_button.setEnabled(True)
        updater.bind_window(gate)
        entry.refresh()
        if not verified or ready_once[0]:return
        ready_once[0] = True
        from desktop.version import account_capabilities as configured_account_capabilities
        gate.set_account_capabilities(configured_account_capabilities()
                                      if account_capabilities is None else account_capabilities)
        if not service_initialized[0]:
            from desktop.update_lifecycle import configured_update_service
            try: service = configured_update_service(install_root=root)
            except Exception: service = None
            updater.configure_service(service, factory=lambda: configured_update_service(install_root=root))
            entry.service = service
            service_initialized[0] = True
        policy = getattr(controller, 'startup_policy', None)
        if policy is not None:
            gate.set_startup_policy(policy)
            entry.refresh()
        else:
            QTimer.singleShot(5000, updater.start)
        if configured().uat:
            if not (getattr(controller,'startup_policy',None) or {}).get('state') in ('MANDATORY_UPDATE','REPAIR_REQUIRED'):
                gate.setWindowTitle('MentorFlow · '+VERSION_MANIFEST['version'])
            # Only an explicitly compiled isolated UAT build honours this
            # signed, manifest-governed bad release. No environment/CLI hook;
            # production ignores it. Exit before issuing any health message.
            if VERSION_MANIFEST.get('uat_recovery_fixture')=='exit-before-health-v1':
                os._exit(86)
        # A queued callback runs after the real shell is visible and verified.
        QTimer.singleShot(100,lambda:notify_health(gate,root,attempt_id=attempt_id,nonce=nonce)
            if gate.isVisible() and controller.results.get('SECURITY_GATE') is True else None)
    timer.timeout.connect(ready);timer.start()
    application.aboutToQuit.connect(timer.stop)
    gate.closed.connect(timer.stop)


def attach_uat_measurements(panel):
    """Bounded local diagnostics, never receipt/health authority or account I/O."""
    import time
    import uuid
    from mentorflow_delivery_authority import WORK_ROOT
    from desktop.update_protocol import canonical
    from scripts import verify_windows_package as v
    directory=Path(WORK_ROOT)/'measurements'
    v._validate_existing_parent_chain(directory,label='isolated update measurements')
    directory.mkdir(exist_ok=True)
    path=directory/(f'update-ui-{os.getpid()}-'+uuid.uuid4().hex+'.json')
    last=[None,0.0]
    def record():
        service=panel.service;now=time.monotonic();state=service.snapshot.get('state')
        if state==last[0] and now-last[1]<1:return
        last[:]=[state,now]
        fields=('state','latest','full_bytes','expected_download_bytes','actual_downloaded_bytes','update_path')
        doc=dict(schema='mentorflow.uat-update-ui-measurement.v1',pid=os.getpid(),current_version=service.current,
            observed_monotonic=now,snapshot={key:service.snapshot[key] for key in fields if key in service.snapshot})
        if getattr(service,'telemetry',None):doc['trace']=service.telemetry.report()
        if service.cache.accepted_app is not None:doc['source_acceptance']=dict(service.cache.accepted_app.profile)
        if getattr(service.cache,'telemetry',None):doc['reconstruction']=service.cache.telemetry
        try:
            temporary=path.with_suffix('.pending')
            v._validate_staging_entry(temporary,directory,directory=False,allow_missing=True)
            temporary.write_bytes(canonical(doc));temporary.replace(path)
        except OSError:pass  # Missing diagnostics can never fabricate health.
    panel.timer.timeout.connect(record)
    record()


def notify_health(gate,root,*,attempt_id=None,nonce=None,_tries=0):
    from scripts.per_user_install import is_install
    if is_install(root):
        from scripts.per_user_install import notify_health as notify_per_user
        return notify_per_user(Path(root),gate,attempt_id,nonce)
    from desktop.update_protocol import strict_json,digest
    from desktop.windows_identity import read_directory
    from scripts import windows_install_trust as trust
    from scripts.update_health import SCHEMA
    root=Path(root)
    if attempt_id is None:
        receipt=strict_json((root/trust.RECEIPT).read_bytes())
        if receipt.get('state')!='PENDING_HEALTH':return
        # Initial installation helper publishes exactly this protected record;
        # no search for receipt-shaped JSON or stale user-writable marker.
        path=root/'.install-staging/update-metadata/first-launch.json'
        if not path.exists():
            if _tries<40 and gate.isVisible():
                from PySide6.QtCore import QTimer
                QTimer.singleShot(500,lambda:notify_health(gate,root,_tries=_tries+1))
            return
        from scripts.verify_windows_package import _validate_package_entry
        _validate_package_entry(path,root,directory=False);trust.assert_protected(path)
        if path.stat().st_size>4096:raise ValueError('oversized first-launch record')
        initial=strict_json(path.read_bytes())
        if set(initial)!={'schema','attempt_id','nonce'} or initial['schema']!='mentorflow.first-launch.v1':
            raise ValueError('unknown first-launch authority')
        attempt_id,nonce=initial['attempt_id'],initial['nonce']
    import re
    if re.fullmatch('[0-9a-f]{32}',attempt_id or '') is None or re.fullmatch('[0-9a-f]{64}',nonce or '') is None:
        raise ValueError('invalid health correlation')
    manifest=(root/'App/SELF-CHECK/package-manifest.json').read_bytes()
    version=strict_json((root/'App/mentorflow-version.json').read_bytes())['version']
    doc=dict(schema=SCHEMA,attempt_id=attempt_id,nonce=nonce,version=version,
        manifest_sha256=digest(manifest),install_identity=read_directory(root),pid=os.getpid(),
        hwnd_hex=f'{int(gate.winId()):016x}')
    def publish():
        from desktop.windows_update_pipe import connect
        from desktop.windows_process_peer import ProcessPeer
        try:
            with connect(attempt_id,'health',timeout=10) as channel:
                with ProcessPeer(channel.peer_pid()) as peer:
                    info=peer.inspect()
                    runtime=Path(info['executable'])
                    if (info['integrity_rid']!=12288 or not info['elevated'] or
                            not runtime.is_relative_to(root/'UpdateRuntime') or
                            runtime.name!='UpdateRuntime.exe'):
                        return
                    trust.assert_safe_parents(runtime);trust.assert_protected(runtime)
                    channel.send(doc)
                    channel.receive(timeout=5)
        except (OSError,RuntimeError,ValueError,TimeoutError):
            # Ordinary startup remains usable when no pending helper exists;
            # absence is never converted into a protected health PASS.
            return
    threading.Thread(target=publish,daemon=True,name='real-window-health').start()

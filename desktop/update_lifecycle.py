"""UI adapter to frozen per-user/protected lifecycle interfaces; no transaction."""
import os
from pathlib import Path
import subprocess


def remember_download(service):
    from scripts.per_user_install import is_install
    if is_install(service.install_root):
        from scripts.per_user_update_core import remember_download as remember
        return remember(service.install_root, service.staged, service.release.envelope)


def handoff(root, stage, envelope, *, hwnd_hex):
    from scripts import per_user_install as install
    if not install.is_install(root):
        from .update_bridge import handoff as protected_handoff
        return protected_handoff(root, stage, envelope, hwnd_hex=hwnd_hex)
    from scripts import per_user_update_core as core
    from .update_session import SessionEnding
    from .windows_process_peer import ProcessPeer
    root = install.target(root)
    core.remember_download(root, stage, envelope)
    if core.unsafe_teardown():
        raise SessionEnding('Update deferred until next normal launch')
    with ProcessPeer(os.getpid()) as peer:
        peer.require(executable=root/'App/MentorFlow.exe', integrity_rid=8192)
        peer.visible_responsive_window(hwnd_hex)
    runtime = install.plain(Path(root)/'Updater/UserRuntime.exe')
    # Never run a transaction in the App worker: normal App exit would kill it.
    # The frozen CLI repeats discovery/hash checks and owns apply/health/relaunch.
    process = subprocess.Popen([str(runtime), 'apply-pending', '--root', str(root),
        '--pid', str(os.getpid()), '--hwnd-hex', hwnd_hex], cwd=root,
        close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return dict(coordinator_started=True, bridge_pid=process.pid, update_uac_count=0)

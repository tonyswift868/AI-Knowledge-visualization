"""UI handoff to the installed, stable Medium coordinator (no direct UAC)."""
import os
from pathlib import Path
import subprocess
import uuid

from desktop.update_protocol import canonical,digest,strict_json,SecurityError
from scripts.install_product import configured

FIELDS={'schema','app_id','install_root','staged_app','release_sha256','base_pid','base_hwnd_hex'}


def handoff(root,stage,envelope,*,hwnd_hex):
    from scripts import windows_install_trust as trust
    from scripts.windows_install_transaction import sha,assert_no_alias
    from desktop.windows_process_peer import ProcessPeer
    from desktop.update_request import local_path
    root,stage=Path(root).absolute(),Path(stage).absolute()
    local_path(str(root));local_path(str(stage));configured().validate_root(root)
    with ProcessPeer(os.getpid()) as peer:
        peer.require(executable=root/'App/MentorFlow.exe',integrity_rid=8192)
        peer.visible_responsive_window(hwnd_hex)
    bridge=root/'UpdateBridge.exe'
    assert_no_alias(bridge);trust.assert_safe_parents(bridge);trust.assert_protected(bridge)
    receipt=strict_json((root/trust.RECEIPT).read_bytes())
    if sha(bridge)!=receipt.get('control_hashes',{}).get('UpdateBridge.exe'):
        raise SecurityError('installed coordinator bridge changed')
    intent=dict(schema='mentorflow.update-intent.v1',app_id=configured().app_id,
        install_root=str(root),staged_app=str(stage),release_sha256=digest(envelope),
        base_pid=os.getpid(),base_hwnd_hex=hwnd_hex)
    path=stage.parent/(uuid.uuid4().hex+'.intent.json')
    raw=canonical(intent)
    with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    process=subprocess.Popen([str(bridge),'--coordinate',str(path),digest(raw)],
        cwd=root,close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW)
    return dict(coordinator_started=True,bridge_pid=process.pid,intent=str(path),update_uac_not_yet_requested=True)

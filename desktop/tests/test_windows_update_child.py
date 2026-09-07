import sys
from pathlib import Path
from desktop.windows_update_child import SuspendedChild
from desktop.windows_process_peer import ProcessPeer


def test_actual_identity_captured_before_immediate_failure(tmp_path):
    runtime=getattr(sys,'_base_executable',sys.executable)
    child=SuspendedChild([runtime,'-c','raise SystemExit(23)'],cwd=tmp_path)
    try:
        assert child.poll() is None
        with ProcessPeer(child.pid) as peer:
            info=peer.require(executable=runtime,integrity_rid=8192)
            assert info['alive'] and not info['elevated']
            child.resume()
            assert child.wait(timeout=5)==23
            assert not peer.alive()
    finally:
        if child.poll() is None:child.terminate();child.wait(timeout=5)
        child.close()

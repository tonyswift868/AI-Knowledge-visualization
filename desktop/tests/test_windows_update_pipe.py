import os
from pathlib import Path
import subprocess
import sys
import uuid
import pytest

from desktop.windows_update_pipe import Listener, connect
from desktop.windows_process_peer import ProcessPeer


def test_real_pipe_peer_pid_and_lossless_messages():
    attempt=uuid.uuid4().hex
    with Listener(attempt,'control') as server:
        runtime=str(Path(getattr(sys,'_base_executable',sys.executable)).resolve())
        child=subprocess.Popen([runtime,'-B','-m','desktop.tests.test_windows_update_pipe',attempt],
            cwd=Path(__file__).resolve().parents[2],creationflags=subprocess.CREATE_NO_WINDOW,
            env={**os.environ,'PYTHONPATH':os.pathsep.join(p for p in sys.path if p)},
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            assert server.accept(timeout=10)==child.pid
            doc=server.receive(timeout=5)
            assert doc=={'pid':child.pid,'identity':{'volume_hex':'ffffffffffffffff','file_id_le_hex':'000102030405060708090a0b0c0d0e0f'}}
            with ProcessPeer(child.pid) as peer:
                evidence=peer.require(executable=runtime,integrity_rid=8192)
                assert evidence['pid']==child.pid and not evidence['elevated']
                with pytest.raises(ValueError):peer.require(executable='D:/Wrong.exe',integrity_rid=8192)
                with pytest.raises(ValueError):peer.require(executable=runtime,integrity_rid=12288)
            server.send({'server_pid':os.getpid(),'ack':True})
            stdout,stderr=child.communicate(timeout=10)
            assert child.returncode==0,(stdout,stderr)
        finally:
            if child.poll() is None:child.kill();child.communicate(timeout=5)


def test_pipe_squatting_and_bounded_wait():
    attempt=uuid.uuid4().hex
    with Listener(attempt,'control') as server:
        with pytest.raises(OSError):Listener(attempt,'control')
        with pytest.raises(TimeoutError):server.accept(timeout=.05)
    with pytest.raises(TimeoutError):connect(uuid.uuid4().hex,'control',timeout=.05)


if __name__=='__main__':
    with connect(sys.argv[1],'control',timeout=10) as client:
        server_pid=client.peer_pid()
        client.send({'pid':os.getpid(),'identity':{'volume_hex':'ffffffffffffffff','file_id_le_hex':'000102030405060708090a0b0c0d0e0f'}})
        assert client.receive(timeout=5)=={'server_pid':server_pid,'ack':True}

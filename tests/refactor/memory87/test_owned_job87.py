"""Force only a private test parent; verify its Windows-owned job kills the child."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from process_probe import snapshot

@pytest.mark.skipif(os.name != 'nt', reason='Windows job ownership')
def test_memory87_force_killed_parent_leaves_no_owned_child(tmp_path):
    ready=tmp_path/'owned-child.json'
    script=tmp_path/'owned_parent87.py'
    script.write_text('''import json,subprocess,sys,time
from pathlib import Path
from refactor.learning.voice.tts.runtime import _windows_job
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
job=_windows_job(child)
Path(sys.argv[1]).write_text(json.dumps({'pid':child.pid}),encoding='utf-8')
time.sleep(120)
''',encoding='utf-8')
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[3]),PYTHONDONTWRITEBYTECODE='1')
    parent=subprocess.Popen([sys.executable,'-B',str(script),str(ready)],env=env,creationflags=0x08000000)
    try:
        until=time.monotonic()+10
        while not ready.exists() and time.monotonic()<until:
            assert parent.poll() is None
            time.sleep(.02)
        child=json.loads(ready.read_text(encoding='utf-8'))['pid']
        assert not snapshot(child).get('exited')
        parent.kill();parent.wait(timeout=5)
        until=time.monotonic()+5
        while not snapshot(child).get('exited') and time.monotonic()<until:time.sleep(.02)
        assert snapshot(child).get('exited'), 'private job must close even on TerminateProcess'
    finally:
        if parent.poll() is None:parent.kill();parent.wait(timeout=5)

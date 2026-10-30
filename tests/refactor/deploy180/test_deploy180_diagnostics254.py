import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from tests.refactor.deploy180 import test_deploy180_scripts as deployment


@pytest.mark.parametrize('explicit_root', [False, True])
def test_deploy254_evidence_default_and_explicit_survive_cleanup(tmp_path, monkeypatch, explicit_root):
    sandbox = tmp_path / 'case'
    sandbox.mkdir()
    (sandbox / 'docker.log').write_text('compose up\n', encoding='utf-8')
    (sandbox / 'http-events.jsonl').write_text('{"phase":"response_sent"}\n', encoding='utf-8')
    if explicit_root:
        monkeypatch.setenv('DEPLOY180_DIAGNOSTICS_ROOT', str(tmp_path / 'evidence'))
    else:
        monkeypatch.delenv('DEPLOY180_DIAGNOSTICS_ROOT', raising=False)
    exc = subprocess.TimeoutExpired(['bash', 'deploy.sh'], 60, output=b'partial\xff', stderr=b'error')
    evidence = Path(deployment._write_timeout_evidence(sandbox, exc.cmd, 60, 0, exc, pid=123))
    shutil.rmtree(sandbox)
    assert evidence.is_dir() and (evidence / 'stdout.txt').read_bytes() == b'partial\xff'
    assert (evidence / 'stderr.txt').read_bytes() == b'error'
    assert (evidence / 'docker.log').read_text(encoding='utf-8') == 'compose up\n'
    assert 'response_sent' in (evidence / 'http-events.jsonl').read_text(encoding='utf-8')
    assert json.loads((evidence / 'metadata.json').read_text(encoding='utf-8'))['process_pid'] == 123


@pytest.mark.skipif(os.name != 'nt', reason='Windows process-tree termination regression')
def test_deploy254_real_timeout_stops_owned_descendants_and_keeps_output(tmp_path, monkeypatch):
    monkeypatch.setenv('DEPLOY180_DIAGNOSTICS_ROOT', str(tmp_path / 'evidence'))
    sandbox = tmp_path / 'case'
    sandbox.mkdir()
    child = sandbox / 'child.py'
    pid_file = sandbox / 'pids.json'
    child.write_text(
        "import json,os,subprocess,sys,time\n"
        "p=subprocess.Popen([sys.executable,'-u','-c',\"import time;print('grandchild-ready',flush=True);time.sleep(30)\"])\n"
        "with open(os.environ['TEST_PID_FILE'],'w') as f:json.dump([os.getpid(),p.pid],f)\n"
        "print('parent-ready',flush=True)\np.wait()\n", encoding='utf-8')
    script = sandbox / 'slow.sh'
    script.write_text('#!/bin/sh\n"$HOST_PYTHON" -u "$TEST_CHILD_SCRIPT"\n', encoding='utf-8', newline='\n')
    env = dict(os.environ, FAKE_SANDBOX_ROOT=str(sandbox),
               HOST_PYTHON=deployment._posix(Path(sys.executable)),
               TEST_CHILD_SCRIPT=deployment._posix(child), TEST_PID_FILE=str(pid_file))
    started = time.monotonic()
    with pytest.raises(AssertionError, match='diagnostic evidence'):
        deployment._run_bash(script, [], env, timeout=3)
    assert time.monotonic() - started < 15
    evidence = tmp_path / 'evidence' / 'case'
    metadata = json.loads((evidence / 'metadata.json').read_text(encoding='utf-8'))
    assert metadata['process_pid'] > 0 and metadata['timeout_seconds'] == 3
    assert metadata['termination']['returncode'] == 0
    assert not metadata['termination']['still_running']
    assert b'parent-ready' in (evidence / 'stdout.txt').read_bytes()
    assert b'grandchild-ready' in (evidence / 'stdout.txt').read_bytes()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    for pid in json.loads(pid_file.read_text(encoding='utf-8')):
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                code = ctypes.c_uint32()
                assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                assert code.value != 259, f'task-owned child still running: {pid}'
            finally:
                kernel.CloseHandle(handle)
    shutil.rmtree(sandbox)
    assert (evidence / 'metadata.json').is_file()


@pytest.mark.skipif(os.name != 'nt', reason='Windows taskkill error branch')
def test_deploy254_termination_failure_still_saves_timeout_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('DEPLOY180_DIAGNOSTICS_ROOT', str(tmp_path / 'evidence'))
    sandbox = tmp_path / 'case'
    sandbox.mkdir()
    class FakeProcess:
        pid = 123
        returncode = None
        def __init__(self, command, **kwargs):
            kwargs['stdout'].write(b'partial\xff')
            kwargs['stdout'].flush()
        def wait(self, timeout):
            if self.returncode is None:
                raise subprocess.TimeoutExpired(['fake'], timeout)
            return self.returncode
        def poll(self):
            return self.returncode
        def kill(self):
            self.returncode = -9
    monkeypatch.setattr(deployment.subprocess, 'Popen', FakeProcess)
    def unavailable(*args, **kwargs):
        raise OSError('taskkill unavailable')
    monkeypatch.setattr(deployment.subprocess, 'run', unavailable)
    with pytest.raises(AssertionError, match='diagnostic evidence'):
        deployment._run_bash(tmp_path / 'fake.sh', [], dict(os.environ, FAKE_SANDBOX_ROOT=str(sandbox)), timeout=0.1)
    evidence = tmp_path / 'evidence' / 'case'
    assert (evidence / 'stdout.txt').read_bytes() == b'partial\xff'
    metadata = json.loads((evidence / 'metadata.json').read_text(encoding='utf-8'))
    assert metadata['process_pid'] == 123
    assert metadata['termination']['error'] == 'taskkill unavailable'
    assert not metadata['termination']['still_running']

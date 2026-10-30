"""Standalone real QtWebEngine/CDP regression for click253; no product or rag155 state."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

REPO = Path(__file__).resolve().parents[3]
NODE = Path('D:/MentorFlow/.local/node/node.exe')


def test_click253_real_cdp(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
               QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',
               QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',
               MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}')
    script = Path(__file__).with_name('click253_host.py')
    host_log = (tmp_path/'click253-host.log').open('w', encoding='utf-8')
    host = subprocess.Popen([sys.executable, '-B', str(script)], env=env,
                            stdout=host_log, stderr=subprocess.STDOUT)
    try:
        endpoint = env['MF_CDP_ENDPOINT']
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            try:
                with opener.open(endpoint+'/json/list', timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, TimeoutError):
                pass
            time.sleep(.1)
        else:
            raise AssertionError('standalone Qt CDP host did not become ready')
        result = subprocess.run([str(NODE), str(Path(__file__).with_name('click253_test.mjs'))],
                                env=env, capture_output=True, text=True, encoding='utf-8',
                                errors='strict', timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        host.terminate()
        host.wait(timeout=10)
        host_log.close()

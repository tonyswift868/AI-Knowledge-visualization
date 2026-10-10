"""Exercise Chromium's actual native composition changes in a fresh process."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native surface regression')
@pytest.mark.parametrize('theme', ['sakura', 'aurora'])
def test_flicker102b_real_surface_never_hides_on_start_register_back_forgot(tmp_path, theme):
    entry = Path(__file__).with_name('native_cycle102b.py')
    env = dict(os.environ, QSG_RHI_BACKEND='opengl', PYTHONDONTWRITEBYTECODE='1',
               TEMP=str(tmp_path), TMP=str(tmp_path))
    # This is a real normal Windows window; offscreen does not reproduce BUG-72.
    env.pop('QT_QPA_PLATFORM', None)
    completed = subprocess.run([sys.executable, '-B', str(entry), '--output', str(tmp_path),
                                '--theme', theme], env=env, capture_output=True, timeout=65)
    assert completed.returncode == 0, completed.stderr.decode(errors='replace')[-3000:]
    result = json.loads((tmp_path/'result.json').read_text(encoding='utf-8'))
    assert result['failure'] is None
    events = result['events_before_exit']
    assert [e['event'] for e in events if e['event'] in ('Show', 'Hide')] == ['Show']
    assert all(e['surface'] == 'OpenGLSurface' for e in events)
    assert [a['action'] for a in result['actions']] == [
        'register', 'back_from_register', 'forgot_password', 'back_from_forgot_password', 'returned_login']
    assert result['timings']['register'] < 1
    assert result['timings']['forgot_password'] < 1

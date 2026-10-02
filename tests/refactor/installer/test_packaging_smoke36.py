import json
import os
from pathlib import Path
import subprocess
import sys


def test_packaging_smoke36_initializes_offline_backend(tmp_path):
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, '-B', '-m', 'refactor.desktop.packaged_main',
        '--packaging-smoke-root', str(tmp_path)], cwd=root,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert data['backend_ready'] and data['source_lists_read'] and data['model_prices_read']
    assert data['store_type'] == 'CourseStore'

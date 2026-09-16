"""Workbench asset readiness only; never authentication or account authority.

Absent configuration preserves packaged/ordinary launch. A pending source build
may show the real native login, but must finish before using frontend assets.
"""
import json
import os
from pathlib import Path
import time


def preparation_state():
    configured = os.environ.get('MENTORFLOW_WORKBENCH_PREPARATION')
    if not configured:
        return 'ready'
    try:
        path = Path(configured)
        if not path.is_absolute():
            return 'error'
        from backend.app.plain_file_copy import assert_no_reparse_ancestors
        assert_no_reparse_ancestors(path, Path(path.anchor))
        if path.stat().st_size > 1024 or path.stat().st_nlink != 1:
            return 'error'
        state = json.loads(path.read_text(encoding='utf-8'))['state']
        return state if state in ('pending', 'ready', 'error') else 'error'
    except (OSError, ValueError, KeyError, TypeError):
        return 'error'


def preparation_message(state):
    return ('正在准备此版本页面，请稍后重试。' if state == 'pending' else
            '此版本页面准备失败，请查看工作台诊断日志并重新启动。')


def wait_for_preparation(cancelled, *, timeout=3600):
    """Worker thread only. Account generation cancellation wins over readiness."""
    deadline = time.monotonic() + timeout
    while not cancelled():
        state = preparation_state()
        if state == 'ready':
            return True
        if state == 'error' or time.monotonic() >= deadline:
            raise RuntimeError('WORKBENCH_ASSET_PREPARATION_FAILED')
        time.sleep(.1)
    return False

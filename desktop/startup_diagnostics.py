"""Small local startup journal: stages and stack locations, never values/messages."""
import json
import logging
import os
from pathlib import Path
import sys
import threading
import traceback
from datetime import datetime, timezone

_lock = threading.Lock()


def record(stage, error=None):
    value = dict(time=datetime.now(timezone.utc).isoformat(), pid=os.getpid(), stage=stage)
    if error is not None:
        value['exception'] = type(error).__name__
        value['frames'] = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                           for frame in traceback.extract_tb(error.__traceback__)]
    line = json.dumps(value, ensure_ascii=True) + '\n'
    try:
        configured = os.environ.get('MENTORFLOW_STARTUP_LOG')
        root = os.environ.get('MENTORFLOW_USER_DATA_ROOT')
        if not configured and not root:
            return
        path = Path(configured) if configured else Path(root) / 'diagnostics' / 'startup.jsonl'
        from backend.app.plain_file_copy import assert_no_reparse_ancestors
        assert_no_reparse_ancestors(path, Path(path.anchor))
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and (path.stat().st_nlink != 1 or path.stat().st_size > 1024 * 1024):
                return
            with path.open('a', encoding='utf-8') as stream:
                stream.write(line)
    except (OSError, ValueError):
        if sys.stderr is not None:
            sys.stderr.write(line)


class BackendFailureHandler(logging.Handler):
    def emit(self, entry):
        if entry.levelno >= logging.ERROR:
            record('backend_lifespan', entry.exc_info[1] if entry.exc_info else None)

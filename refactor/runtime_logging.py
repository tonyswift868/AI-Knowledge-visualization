"""Local desktop diagnostics. Never serialize arguments, exception text or locals.

Ordinary logging identifies the call site and exception frames; explicit events
add only enumerated state/code, model purpose and elapsed time. This deliberately
excludes exception messages (which can contain credentials or student content).
The Qt adapter may additionally supply bounded, redacted runtime messages.
"""
from contextlib import contextmanager
from functools import wraps
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys
import threading
import time
import traceback

LOGGER = logging.getLogger('refactor.runtime')
_handler = None
_crash_file = None
_lock = threading.RLock()
_EVENTS = {'startup', 'login', 'refresh', 'sync', 'update', 'model', 'bridge',
           'exit', 'window', 'qt', 'renderer', 'process'}


def log_directory(data_root):
    return Path(data_root) / 'logs'


def enable_crash_dump(data_root):
    """Call before importing Qt, and keep the descriptor alive until process exit."""
    import faulthandler
    import os
    global _crash_file
    target = log_directory(data_root) / f'crash-{os.getpid()}.log'
    target.parent.mkdir(parents=True, exist_ok=True)
    if _crash_file is None or _crash_file.closed or Path(_crash_file.name) != target:
        replacement = target.open('ab', buffering=0)
        faulthandler.enable(file=replacement, all_threads=True)
        if _crash_file is not None and not _crash_file.closed:
            _crash_file.close()
        _crash_file = replacement
    else:
        faulthandler.enable(file=_crash_file, all_threads=True)
    return _crash_file


def _label(value):
    # Codes/state/purpose are machine labels, never free text.
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z_]{1,64}', value) else 'UNKNOWN'


class DiagnosticFormatter(logging.Formatter):
    def format(self, record):
        payload = {'at': self.formatTime(record, '%Y-%m-%dT%H:%M:%S'),
                   'level': record.levelname, 'module': record.name,
                   'file': Path(record.pathname).name, 'line': record.lineno,
                   'function': record.funcName}
        event = getattr(record, 'mf_event', None)
        if event in _EVENTS:
            payload['event'] = event
            for field in ('state', 'code', 'purpose', 'reason', 'window'):
                if field in record.mf_fields:
                    payload[field] = _label(record.mf_fields[field])
            elapsed = record.mf_fields.get('elapsed_seconds')
            if isinstance(elapsed, (int, float)) and 0 <= elapsed < float('inf'):
                payload['elapsed_seconds'] = round(elapsed, 3)
            for field in ('pid', 'exit_code', 'memory_bytes', 'threads', 'python_threads', 'previous_pid', 'window_id',
                          'count', 'completed', 'total', 'failed', 'batch', 'http_status'):
                value = record.mf_fields.get(field)
                if type(value) is int:
                    payload[field] = value
            # Only the Qt adapter supplies this text, with URLs and secrets removed.
            if event == 'qt' and isinstance(record.mf_fields.get('message'), str):
                payload['message'] = record.mf_fields['message'][:1000]
        if record.exc_info:
            kind, exc, tb = record.exc_info
            payload['exception_type'] = kind.__name__
            payload['code'] = _label(getattr(exc, 'code', None))
            # format_exception() would include the exception text and source
            # line. Frame metadata gives a stack without leaking either.
            payload['stack'] = [dict(file=Path(f.filename).name, line=f.lineno, function=f.name)
                                for f in traceback.extract_tb(tb)]
        return json.dumps(payload, ensure_ascii=False)


def event(name, *, exc_info=None, **fields):
    if name not in _EVENTS:
        raise ValueError('unknown diagnostic event')
    severity = logging.ERROR if exc_info else logging.INFO
    if name == 'qt':
        severity = {'WARNING': logging.WARNING, 'CRITICAL': logging.ERROR,
                    'FATAL': logging.CRITICAL}.get(fields.get('state'), severity)
    LOGGER.log(severity, 'runtime event',
               extra={'mf_event': name, 'mf_fields': fields}, exc_info=exc_info)


def configure(data_root):
    """Five files total: active 5 MiB file plus four rotated files."""
    global _handler
    directory = log_directory(data_root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'mentorflow.log'
    with _lock:
        logger = logging.getLogger('refactor')
        if _handler is not None and Path(_handler.baseFilename) == target.resolve():
            return target
        if _handler is not None:
            logger.removeHandler(_handler)
            _handler.close()
        _handler = RotatingFileHandler(target, maxBytes=5 * 1024 * 1024, backupCount=4, encoding='utf-8')
        _handler.setFormatter(DiagnosticFormatter())
        logger.addHandler(_handler)
        logger.setLevel(logging.INFO)
    event('startup', state='STARTED')
    return target


def install_exception_hooks():
    def uncaught(kind, exc, tb):
        LOGGER.error('uncaught exception', exc_info=(kind, exc, tb))
    sys.excepthook = uncaught
    threading.excepthook = lambda args: uncaught(args.exc_type, args.exc_value, args.exc_traceback)


@contextmanager
def model_call(purpose):
    started, code = time.monotonic(), 'OK'
    try:
        yield
    except BaseException as exc:
        code = getattr(exc, 'code', 'INTERNAL')
        LOGGER.error('model failure', exc_info=True)
        raise
    finally:
        event('model', purpose=purpose, code=code, elapsed_seconds=time.monotonic() - started)


def trace_model_call(function):
    @wraps(function)
    def traced(self, purpose, *args, **kwargs):
        with model_call(purpose):
            return function(self, purpose, *args, **kwargs)
    return traced

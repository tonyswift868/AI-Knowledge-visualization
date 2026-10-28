"""Small filesystem operations shared by desktop installation and resources."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import time
from datetime import datetime, timezone


RENAME_ATTEMPTS = 8
_RENAME_DELAYS = (.01, .02, .04, .08, .16, .2, .2)
_RETRYABLE_WINDOWS_ERRORS = {5, 32, 33}


class RenameStateChangedError(RuntimeError):
    """A path changed during a denied rename; never retry against new state."""


def _signature(path: Path):
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size, stat.st_mtime_ns)


def _log_rename(state: str, component: str, attempt: int, started: float):
    try:
        from refactor.runtime_logging import event
        event('update', state=state, phase=component, count=attempt,
              elapsed_seconds=time.monotonic() - started)
    except Exception:
        # Diagnostics must never turn a filesystem operation into a failure.
        pass


def _query_rename_users(source, target, *, timeout_seconds):
    from refactor.desktop.delivery.restart_manager import query_directory_users
    return query_directory_users(source, target, timeout_seconds=timeout_seconds)


def rename_with_retry(source, target, *, component: str, replace: bool = False,
                      on_event=None, diagnose: bool = False, on_attempt=None,
                      budget_seconds: float | None = None):
    """Rename a path, retrying only transient Windows sharing/ACL denials.

    The operation is never retried if either endpoint's identity or metadata
    changes after the first denial. `replace=True` has os.replace semantics;
    otherwise the platform's os.rename behavior applies.
    Existing callers retain the 710 ms schedule. Callers that pass
    `budget_seconds` use the same backoff, then retry at 200 ms intervals up to
    that wall-clock budget. The updater opts into its user-selected 3 second
    budget; other shared filesystem operations keep their original behavior.
    """
    source, target = Path(source), Path(target)
    before_source, before_target = _signature(source), _signature(target)
    if before_source is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(source))
    move = os.replace if replace else os.rename
    started = time.monotonic()
    deadline = started + max(0.0, float(budget_seconds)) if budget_seconds is not None else None
    attempt = 0
    delay_index = 0
    diagnostic = None
    last_error = None

    def report(state, attempt):
        _log_rename(state, component, attempt, started)
        if on_event is not None:
            try:
                on_event(state, component, attempt, time.monotonic() - started)
            except Exception:
                pass  # A diagnostic sink cannot alter the rename outcome.

    while True:
        if attempt and deadline is not None and time.monotonic() >= deadline:
            report('DIRECTORY_RENAME_FAILED', attempt)
            raise last_error
        attempt += 1
        if attempt > 1 and (_signature(source) != before_source or
                            _signature(target) != before_target):
            report('DIRECTORY_RENAME_STATE_CHANGED', attempt - 1)
            raise RenameStateChangedError('rename endpoints changed during retry')
        try:
            move(source, target)
            if attempt > 1:
                report('DIRECTORY_RENAME_SUCCEEDED', attempt)
            return
        except OSError as error:
            last_error = error
            retryable = (os.name == 'nt' and getattr(error, 'winerror', None) in
                         _RETRYABLE_WINDOWS_ERRORS)
            if not retryable:
                if attempt > 1 or retryable:
                    report('DIRECTORY_RENAME_FAILED', attempt)
                raise

            error_time = datetime.now(timezone.utc).isoformat()
            error_elapsed = time.monotonic() - started
            if diagnose and diagnostic is None:
                remaining = 1.0 if deadline is None else max(0.0, min(1.0, deadline - time.monotonic()))
                try:
                    diagnostic = _query_rename_users(
                        source, target, timeout_seconds=remaining)
                except Exception as diagnostic_error:
                    diagnostic = {'status': 'unknown', 'processes': [],
                                  'reason': type(diagnostic_error).__name__}
                if isinstance(diagnostic, list):
                    diagnostic = {'status': 'ok', 'processes': diagnostic}
            if on_attempt is not None:
                processes = (diagnostic or {}).get('processes', [])
                details = {
                    'attempt': attempt,
                    'timestamp': error_time,
                    'elapsed_seconds': error_elapsed,
                    'error_code': getattr(error, 'winerror', None),
                    'blockers': processes,
                    'diagnostic_status': ((diagnostic or {}).get('status')
                                          if diagnose else 'not_requested'),
                    'diagnostic_reason': (diagnostic or {}).get('reason'),
                    'diagnostic_attempt': 1,
                }
                try:
                    on_attempt(details)
                except Exception:
                    pass  # Evidence collection must not change update behavior.

            if deadline is None and attempt >= RENAME_ATTEMPTS:
                report('DIRECTORY_RENAME_FAILED', attempt)
                raise
            if deadline is not None and time.monotonic() >= deadline:
                report('DIRECTORY_RENAME_FAILED', attempt)
                raise
            report('DIRECTORY_RENAME_RETRY', attempt)
            if deadline is None:
                delay = _RENAME_DELAYS[attempt - 1]
            else:
                delay = (_RENAME_DELAYS[delay_index] if delay_index < len(_RENAME_DELAYS)
                         else .2)
                delay_index += 1
                delay = min(delay, max(0.0, deadline - time.monotonic()))
            time.sleep(delay)

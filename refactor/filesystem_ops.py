"""Small filesystem operations shared by desktop installation and resources."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import time


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


def rename_with_retry(source, target, *, component: str, replace: bool = False, on_event=None):
    """Rename a path, retrying only transient Windows sharing/ACL denials.

    The operation is never retried if either endpoint's identity or metadata
    changes after the first denial. `replace=True` has os.replace semantics;
    otherwise the platform's os.rename behavior applies.
    The bounded schedule totals 710 ms, covering the 6.7 ms observed transient
    directory handle while keeping persistent denial finite.
    """
    source, target = Path(source), Path(target)
    before_source, before_target = _signature(source), _signature(target)
    if before_source is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(source))
    move = os.replace if replace else os.rename
    started = time.monotonic()

    def report(state, attempt):
        _log_rename(state, component, attempt, started)
        if on_event is not None:
            try:
                on_event(state, component, attempt, time.monotonic() - started)
            except Exception:
                pass  # A diagnostic sink cannot alter the rename outcome.

    for attempt in range(1, RENAME_ATTEMPTS + 1):
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
            retryable = (os.name == 'nt' and getattr(error, 'winerror', None) in
                         _RETRYABLE_WINDOWS_ERRORS)
            if not retryable or attempt == RENAME_ATTEMPTS:
                if attempt > 1 or retryable:
                    report('DIRECTORY_RENAME_FAILED', attempt)
                raise

            report('DIRECTORY_RENAME_RETRY', attempt)
            time.sleep(_RENAME_DELAYS[attempt - 1])

"""Background integrity checks; signed install/apply verification stays unconditional.

The stat record is an optimization, never release authority. A new signed release
or missing record always hashes every file. Records live outside the signed App.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import time

from . import per_user_install as install
from .package_verification import sha256_file
from .update_protocol import SecurityError, RecoverableError, canonical, digest, strict_json


def record_path(root):
    return Path(root) / 'Data/runtime/integrity-stat.json'


def _stat(item):
    value = item.stat()
    return [value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def _inventory(app, release):
    # Do not traverse plain() ancestors 14,000 times. Check each entry once.
    actual = {}
    for folder, directories, files in os.walk(install.plain(app), followlinks=False):
        for name in directories + files:
            item = Path(folder) / name
            value = item.lstat()
            if getattr(value, 'st_file_attributes', 0) & 0x400 or item.is_symlink():
                raise SecurityError('reparse path refused')
        for name in files:
            item = Path(folder) / name
            actual[item.relative_to(app).as_posix()] = _stat(item)
    if set(actual) != set(release.rows):
        raise SecurityError('application file set differs from signed release')
    return actual


def verified_stat_snapshot(root, release):
    return _inventory(install.app_path(root), release)


def remember_verified(root, release, *, stats=None):
    """Called only after the transaction has fully hashed its final App tree."""
    stats = verified_stat_snapshot(root, release) if stats is None else stats
    path = record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    install.atomic(path, canonical(dict(schema=1, release=digest(release.envelope), files=stats)))


@contextmanager
def background_io():
    """Windows background thread mode lowers CPU, memory and I/O priority."""
    changed = False
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentThread.restype = ctypes.c_void_p
        kernel.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        changed = bool(kernel.SetThreadPriority(kernel.GetCurrentThread(), 0x10000))
    try:
        yield
    finally:
        if changed:
            kernel.SetThreadPriority(kernel.GetCurrentThread(), 0x20000)


@contextmanager
def _wait_for_writer(root):
    from .update_download import InstallUpdateLock
    deadline = time.monotonic() + 180
    while True:
        lock = InstallUpdateLock(root)
        try:
            lock.__enter__()
            break
        except RecoverableError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.1)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def verify_background(root):
    started = time.perf_counter()
    # An installation coordinator can still hold this lock while accepting our
    # visible-window health proof. Wait in the background, never classify a swap
    # in progress as damage, and never recover/mutate that swap from the App.
    with background_io(), _wait_for_writer(root):
        release = install.installed(root)
        app = install.app_path(root)
        if app == Path(root):
            # Pre-canonical layouts retain their existing exact-namespace rules.
            install.verify_payload(root, release)
            install.verify_gui(root, executable=app / 'MentorFlow.exe')
            return dict(rehashed=len(release.rows), seconds=time.perf_counter() - started)
        fingerprint = digest(release.envelope)
        try:
            old = strict_json(install.read(record_path(root)))
            cached = (old['files'] if isinstance(old, dict) and old.get('schema') == 1
                      and old.get('release') == fingerprint and isinstance(old.get('files'), dict) else {})
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            cached = {}
        stats = _inventory(app, release)
        count = 0
        for name, row in release.rows.items():
            if stats[name][0] != row['size']:
                raise SecurityError('signed application content mismatch: ' + name)
            if cached.get(name) != stats[name]:
                count += 1
                if sha256_file(app / name) != row['sha256'] or _stat(app / name) != stats[name]:
                    raise SecurityError('signed application content mismatch: ' + name)
        install.gui_configuration(root, release)
        # Repair runtime is small and independently bound. Never skip its signed
        # check, but do it here rather than on the visible startup path.
        install.verify_recovery_runtime(root, release)
        path = record_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        install.atomic(path, canonical(dict(schema=1, release=fingerprint, files=stats)))
        return dict(rehashed=count, seconds=time.perf_counter() - started)


def schedule(root, app, shell):
    """Own UI repair hint; verification failure does not end the running App."""
    import threading
    from PySide6.QtCore import QObject, Signal, Qt
    class Completion(QObject):
        finished = Signal(object)
    receiver = Completion(app)
    app._mentorflow_integrity = receiver
    def received(result):
        from .startup_window import log_phase
        log_phase(root, 'background_verification_complete', **result)
        if 'error' not in result:
            return
        from PySide6.QtWidgets import QMessageBox
        parent = shell.home if shell.home is not None and shell.home.isVisible() else shell.gate
        dialog = QMessageBox(QMessageBox.Icon.Warning, 'MentorFlow',
                             '安装文件有损坏，请修复', parent=parent)
        repair = dialog.addButton('修复', QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton('稍后', QMessageBox.ButtonRole.RejectRole)
        def clicked(button):
            if button is repair:
                # Existing repair coordinator retains all signature/process gates.
                updates = app._mentorflow_updates
                updates.state = {**updates.state, 'shell': 'repair'}
                updates.run('install', parent)
        dialog.buttonClicked.connect(clicked)
        receiver.dialog = dialog
        dialog.show(); dialog.raise_()
    receiver.finished.connect(received, Qt.ConnectionType.QueuedConnection)
    def work():
        try:
            result = verify_background(root)
        except RecoverableError as error:
            result = dict(deferred=str(error))
        except Exception as error:
            result = dict(error=str(error))
        try:
            receiver.finished.emit(result)
        except RuntimeError:
            pass  # App has exited; next launch checks again.
    worker = threading.Thread(target=work, daemon=True, name='app-integrity')
    worker.start()
    return worker

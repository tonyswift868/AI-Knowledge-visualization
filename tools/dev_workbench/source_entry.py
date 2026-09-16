"""Workbench-only storage compatibility wrapper around each version's desktop.main."""
import json
import os
from pathlib import Path
import sys

source, profile, identity = map(Path, sys.argv[1:4])
selected = json.loads(sys.argv[4])
sys.path.insert(0, str(source))
# main2 predates the explicit INI adapter. Isolate its two-string QSettings
# constructor in this process; never redirect native user settings or migrate them.
from PySide6 import QtCore
OriginalSettings = QtCore.QSettings
class ProfileSettings(OriginalSettings):
    def __init__(self, *args, **kwargs):
        if len(args) == 2 and all(isinstance(v, str) for v in args):
            import hashlib
            name = hashlib.sha256(('\0'.join(args)).encode()).hexdigest()
            super().__init__(str(profile / (name+'.ini')), OriginalSettings.Format.IniFormat)
        else:
            super().__init__(*args, **kwargs)
QtCore.QSettings = ProfileSettings
if (source / 'desktop/backend_imports.py').is_file():
    from desktop.backend_imports import prepare_backend_imports
    prepare_backend_imports()
else:
    # main2's LocalService uses this older backend import layout.
    sys.path.append(str(source / 'backend'))
# Both import spellings exist in old/new source. Keep Windows Locker, only
# namespace these Workbench profiles so formal credentials are never read.
import importlib
import hashlib
for name in ('backend.app.credential_store', 'app.credential_store'):
    store = importlib.import_module(name)
    store.SERVICE_NAME += '.Workbench.' + hashlib.sha256(
        os.path.normcase(str(profile.absolute())).encode()).hexdigest()[:24]
from desktop.main import MentorFlowApplication
def main():
    app = MentorFlowApplication()
    from PySide6.QtWebEngineCore import QWebEngineProfile
    web = QWebEngineProfile.defaultProfile()
    for name in ('web-cache', 'web-storage'):
        (profile / name).mkdir(exist_ok=True)
    web.setCachePath(str(profile / 'web-cache'))
    web.setPersistentStoragePath(str(profile / 'web-storage'))
    def observed():
        from PySide6.QtWidgets import QMessageBox
        windows = [w for w in app.app.topLevelWidgets()
                   if w.isVisible() and w is not app.splash and not isinstance(w, QMessageBox)]
        if windows:
            identity.write_text(json.dumps({**selected, 'pid': os.getpid(), 'parent_pid': os.getppid(),
                'source': str(source), 'desktop_module': sys.modules['desktop.main'].__file__,
                'window_titles': [w.windowTitle() for w in windows]}), encoding='utf-8')
            timer.stop()
    timer = QtCore.QTimer(); timer.timeout.connect(observed); timer.start(200)
    return app.run()

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())

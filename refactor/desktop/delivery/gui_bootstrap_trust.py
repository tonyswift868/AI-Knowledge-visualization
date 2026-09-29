"""Runtime contracts extracted from scripts/gui_bootstrap_trust.py."""
CLOSURE = 'SELF-CHECK/gui-bootstrap-closure.json'

REQUIRED = {
    'MentorFlow.exe', '_internal/base_library.zip',
    '_internal/PySide6/QtCore.pyd', '_internal/PySide6/QtGui.pyd',
    '_internal/PySide6/QtWidgets.pyd', '_internal/PySide6/Qt6Core.dll',
    '_internal/PySide6/Qt6Gui.dll', '_internal/PySide6/Qt6Widgets.dll',
    '_internal/PySide6/plugins/platforms/qwindows.dll',
}

def closure_paths(document):
    if not isinstance(document, dict) or set(document) != {'schema', 'files'} or document['schema'] != 'mentorflow.gui-bootstrap-closure.v1':
        raise ValueError('invalid GUI bootstrap closure')
    files = document['files']
    if not isinstance(files, list) or not 1 <= len(files) <= 512:
        raise ValueError('invalid bootstrap closure size')
    for key in files:
        if (not isinstance(key, str) or not key or '\\' in key or ':' in key or
                key.startswith('/') or any(part in {'', '.', '..'} for part in key.split('/'))):
            raise ValueError('unsafe bootstrap path')
    if len(set(key.casefold() for key in files)) != len(files) or not REQUIRED.issubset(files):
        raise ValueError('incomplete or duplicate GUI bootstrap closure')
    return files

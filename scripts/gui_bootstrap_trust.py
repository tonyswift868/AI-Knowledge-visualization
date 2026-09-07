"""Bounded verification of the installed, minimal GUI bootstrap closure.

Internal UAT daily startup relies on the existing protected installation and
its full install-time verification. The closure check runs after first paint;
the full verifier remains available for install, update and manual repair.
"""
from pathlib import Path, PurePosixPath
import json
import re

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


def protected_gui_location(root, *, executable):
    """Constant-size path/ACL check; never enumerate or hash the package."""
    from scripts import windows_install_trust as trust
    root = trust._root(root)
    package = root / 'App'
    if Path(executable).absolute() != package / 'MentorFlow.exe':
        raise trust.RepairRequired('GUI entry must run from this protected installation')
    for path in (root, package, package / 'MentorFlow.exe'):
        trust.assert_safe_parents(path)
        trust.assert_protected(path)
    return package


def verify_gui_bootstrap(root, *, manifest_sha256, source_sha, tree_sha, executable):
    from scripts import windows_install_trust as trust
    from scripts import verify_windows_package as v
    for value, size in ((manifest_sha256, 64), (source_sha, 40), (tree_sha, 40)):
        if not re.fullmatch('[0-9a-f]{' + str(size) + '}', value or ''):
            raise trust.RepairRequired('missing GUI release identity')
    package = protected_gui_location(root, executable=executable)
    root = package.parent

    def protected_file(key):
        path = package.joinpath(*PurePosixPath(key).parts)
        trust.assert_safe_parents(path)
        v._validate_package_entry(path, package, directory=False)
        trust.assert_protected(path)
        return path

    manifest_path = protected_file(v.MANIFEST_RELATIVE.as_posix())
    if v.sha256_file(manifest_path) != manifest_sha256:
        raise trust.RepairRequired('GUI manifest pin mismatch')
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get('schema') != v.MANIFEST_SCHEMA or manifest.get('product') != 'MentorFlow':
        raise trust.RepairRequired('invalid GUI manifest')
    expected = {entry['path']: entry['sha256'] for entry in manifest['files']}
    if len(expected) != len(manifest['files']):
        raise trust.RepairRequired('duplicate GUI manifest entry')
    receipt_path = root / trust.RECEIPT
    trust.assert_safe_parents(receipt_path)
    trust.assert_protected(receipt_path)
    v._validate_package_entry(receipt_path, root, directory=False)
    if receipt_path.stat().st_size > 16 * 1024 * 1024:
        raise trust.RepairRequired('invalid GUI installation receipt')
    receipt = json.loads(receipt_path.read_bytes())
    binding = receipt['binding']
    from desktop.windows_identity import from_legacy
    native_id=[root.stat().st_dev, root.stat().st_ino]
    expected_identity=from_legacy(native_id) if binding.get('schema') == trust.SCHEMA else native_id
    if (receipt.get('full_verified') is not True or binding.get('schema') not in (trust.SCHEMA,trust.LEGACY_SCHEMA) or
            binding.get('root') != str(root) or binding.get('manifest_sha256') != manifest_sha256 or
            binding.get('source_sha') != source_sha or binding.get('tree_sha') != tree_sha or
            binding.get('root_identity') != expected_identity):
        raise trust.RepairRequired('GUI installation binding mismatch')
    closure_file = protected_file(CLOSURE)
    if v.sha256_file(closure_file) != expected.get(CLOSURE):
        raise trust.RepairRequired('bootstrap closure definition changed')
    if closure_file.stat().st_size > 128 * 1024:
        raise trust.RepairRequired('bootstrap closure too large')
    keys = closure_paths(json.loads(closure_file.read_bytes()))
    # Qt scans platform plugin metadata before choosing qwindows. Reject an
    # unknown candidate before any Qt library or plugin is imported.
    platforms = package / '_internal/PySide6/plugins/platforms'
    trust.assert_protected(platforms)
    for item in platforms.iterdir():
        key = item.relative_to(package).as_posix()
        if not item.is_file() or key not in keys:
            raise trust.RepairRequired('unknown bootstrap platform plugin')
    for key in ('qt.conf', '_internal/qt.conf', '_internal/PySide6/qt.conf'):
        if (package / key).exists() and key not in keys:
            raise trust.RepairRequired('unverified Qt loader configuration')
    for key in keys:
        path = protected_file(key)
        digest = v.sha256_file(path)
        if digest != expected.get(key) or digest != receipt['critical_hashes'].get(key):
            raise trust.RepairRequired('bootstrap executable/runtime changed')
    return package

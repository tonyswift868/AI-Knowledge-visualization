"""Opt-in harness storage adapter; no auth, sync, migration or launch authority."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import stat
import hashlib

PROFILE_CONTRACT = 'MENTORFLOW_TEST_DEVICE_V1'
DEVICE_BASE = Path('E:/Codex/TestDevices/MentorFlow')
MARKER = '.workbench-owner.json'
_run_lock = None


def plain(path):
    path = Path(path)
    for parent in reversed((path, *path.parents)):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('TEST_DEVICE_UNSAFE_PATH')
    return path


def read_profile(root, *, base=None):
    root, base = Path(root), Path(base or DEVICE_BASE)
    if root not in (base / 'A', base / 'B', base / 'UserTest') or not root.is_absolute():
        raise ValueError('TEST_DEVICE_UNOWNED_ROOT')
    plain(root); marker = plain(root / MARKER)
    if marker.stat().st_size > 2048: raise ValueError('TEST_DEVICE_INVALID_OWNER')
    value = json.loads(marker.read_text(encoding='utf-8'))
    if (set(value) != {'contract', 'device', 'generation', 'root', 'owner'} or
        value['contract'] != PROFILE_CONTRACT or value['device'] != root.name or
        value['root'] != str(root) or not re.fullmatch('[0-9a-f]{32}', value['generation']) or
        not re.fullmatch('[0-9a-f]{32}', value['owner'])):
        raise ValueError('TEST_DEVICE_INVALID_OWNER')
    return value


def environment(root, profile):
    """Code-owned locations only; manifests cannot provide environment overrides."""
    root = Path(root)
    vault = root / 'vault'
    return {
        'MENTORFLOW_TEST_DEVICE_ROOT': str(root),
        'MENTORFLOW_TEST_DEVICE_GENERATION': profile['generation'],
        'MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY': '1',
        'MENTORFLOW_FIRST_START_ACTION': 'defaults',
        'MENTORFLOW_ACCOUNT_VAULT_ROOT': str(vault),
        'MENTORFLOW_USER_DATA_ROOT': str(vault / 'prelaunch'),
        'MENTORFLOW_DATA_DIR': str(vault / 'prelaunch/data'),
        'MENTORFLOW_DB': str(vault / 'prelaunch/data/app.db'),
        'MENTORFLOW_ORM_DB': str(vault / 'prelaunch/data/mentorflow_v2.db'),
        'MENTORFLOW_TEMP_ROOT': str(root / 'temp'),
        'MENTORFLOW_DOCUMENT_CACHE_ROOT': str(root / 'cache/documents'),
        'MENTORFLOW_VOICE_CACHE_ROOT': str(root / 'cache/voice'),
        'LOCALAPPDATA': str(root / 'profile/Local'), 'APPDATA': str(root / 'profile/Roaming'),
        'TEMP': str(root / 'temp'), 'TMP': str(root / 'temp'),
    }


def validate_storage(root):
    pending, count = [Path(root)], 0
    while pending:
        folder = pending.pop(); plain(folder)
        for child in folder.iterdir():
            count += 1
            if count > 20000: raise ValueError('TEST_DEVICE_STORAGE_LIMIT')
            plain(child)
            if child.is_dir(): pending.append(child)
            elif child.stat().st_nlink != 1: raise ValueError('TEST_DEVICE_UNSAFE_PATH')


def credential_service(service):
    root = os.getenv('MENTORFLOW_TEST_DEVICE_ROOT')
    if not root: return service
    profile = read_profile(root)
    if os.getenv('MENTORFLOW_TEST_DEVICE_GENERATION') != profile['generation']:
        raise ValueError('TEST_DEVICE_GENERATION_CHANGED')
    return service + '.Workbench.' + profile['device'] + '.' + profile['generation']


def configure():
    """Call before any QSettings or application/backend initialization."""
    global _run_lock
    root = os.getenv('MENTORFLOW_TEST_DEVICE_ROOT')
    if not root: return
    profile = read_profile(root)
    if os.getenv('MENTORFLOW_TEST_DEVICE_GENERATION') != profile['generation']:
        raise ValueError('TEST_DEVICE_GENERATION_CHANGED')
    if _run_lock is not None: return
    import msvcrt
    lock = plain(Path(root) / '.running').open('r+b')
    try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close(); raise ValueError('TEST_DEVICE_BUSY') from None
    try:
        if read_profile(root) != profile: raise ValueError('TEST_DEVICE_GENERATION_CHANGED')
        validate_storage(root)
    except Exception:
        lock.close(); raise
    _run_lock = lock  # Held for this process lifetime; reset and duplicate launch fail closed.
    os.environ.update(environment(root, profile))
    from PySide6.QtCore import QSettings
    settings = Path(root) / 'preferences'
    plain(settings)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings))
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, str(settings / 'system'))


def bind_web_profile(profile):
    root = os.getenv('MENTORFLOW_TEST_DEVICE_ROOT')
    if root and not profile.isOffTheRecord():
        read_profile(root)
        name = hashlib.sha256(profile.storageName().encode('utf-8')).hexdigest()
        cache = plain(Path(root) / 'cache') / 'webengine' / name
        profile.setCachePath(str(cache / 'cache'))
        profile.setPersistentStoragePath(str(cache / 'storage'))
    return profile


def create_web_profile(profile_type, *args):
    return bind_web_profile(profile_type(*args))


def create_settings(settings_type, *args):
    root = os.getenv('MENTORFLOW_TEST_DEVICE_ROOT')
    if not root: return settings_type(*args)
    read_profile(root)
    if len(args) != 2 or not all(isinstance(arg, str) for arg in args):
        raise ValueError('TEST_DEVICE_SETTINGS_CONTRACT')
    from PySide6.QtCore import QSettings
    # The organization/application overload ignores QSettings.defaultFormat on
    # Windows. An explicit INI filename is required; changing APPDATA is insufficient.
    name = hashlib.sha256(('\0'.join(args)).encode('utf-8')).hexdigest()
    directory = plain(Path(root) / 'preferences')
    result = settings_type(str(directory / (name + '.ini')), QSettings.Format.IniFormat)
    result.setFallbacksEnabled(False)
    return result

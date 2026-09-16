"""One account-root resolver and migration durability gate (no filesystem writes)."""
from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path('E:/MentorFlowData')
TEST_ONLY_ENV = 'MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY'


def device_storage_root(source=None, *, root=None, legacy_profile=None):
    """Default new installs to E:, preserving an existing accepted device root."""
    env = os.environ if source is None else source
    selected = root if root is not None else env.get('MENTORFLOW_ACCOUNT_VAULT_ROOT')
    if selected is None and legacy_profile is not None:
        import re
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', legacy_profile):
            raise ValueError('Invalid device storage profile')
        legacy = Path(env['LOCALAPPDATA']) / 'MentorFlow' / 'Accounts' / legacy_profile
        reject_links(legacy)
        if legacy.exists():
            selected = legacy
    selected = Path(selected if selected is not None else DEFAULT_ROOT)
    reject_links(selected)
    if (selected.drive.upper() == 'D:' or str(selected)[:7].upper() == '\\\\?\\D:\\') and env.get(TEST_ONLY_ENV) == '1':
        from scripts.test_storage_policy import require_test_path
        require_test_path(selected, environ=env)
    if not durable_path(selected) and env.get(TEST_ONLY_ENV) != '1':
        raise ValueError('TARGET_STORAGE_STABLE=NO: device storage is disposable')
    return selected


def prelaunch_storage_environment(root):
    """Pre-auth paths are separate from every authenticated account namespace."""
    root = Path(root)
    return {'MENTORFLOW_ACCOUNT_VAULT_ROOT':str(root), 'MENTORFLOW_DATA_DIR':str(root),
            'MENTORFLOW_USER_DATA_ROOT':str(root/'prelaunch'),
            'MENTORFLOW_DB':str(root/'prelaunch/data/app.db'),
            'MENTORFLOW_ORM_DB':str(root/'prelaunch/data/mentorflow_v2.db')}


def durable_path(path: Path) -> bool:
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path.drive.startswith('\\\\'):
        return False
    from scripts.test_storage_policy import ASSIGNED_ROOT
    if path == ASSIGNED_ROOT or path.is_relative_to(ASSIGNED_ROOT):
        return False
    forbidden = {'temp', 'tmp', 'staging', 'installer-staging', 'build', 'dist',
                 'artifacts', 'worktrees', '.git', 'node_modules', '__pycache__',
                 'program files', 'program files (x86)'}
    if any(part.casefold() in forbidden for part in path.parts):
        return False
    excluded = [Path(__file__).resolve().parents[1]]
    if getattr(sys, 'frozen', False):
        excluded.append(Path(sys.executable).absolute().parent)
    for name in ('TEMP', 'TMP', 'TMPDIR'):
        if os.environ.get(name):
            excluded.append(Path(os.environ[name]).absolute())
    if any(path == root or root in path.parents for root in excluded):
        return False
    return True


def reject_links(path: Path):
    if os.name == 'nt' and str(path)[:7].upper() == '\\\\?\\D:\\':
        from scripts.test_storage_policy import require_test_path
        require_test_path(path)
        return
    if not path.is_absolute() or '..' in path.parts or path.drive.startswith('\\\\'):
        raise ValueError('Absolute storage path without traversal required')
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('账号存储路径不能经过符号链接或重解析点。')


@dataclass(frozen=True)
class AccountStorageTarget:
    root: Path
    account_id: str | None
    target_storage_stable: bool
    test_only: bool


def resolve_account_storage(profile, *, root=DEFAULT_ROOT, test_only=False):
    from .account_identity import AccountProfile, ProfileMode
    if not isinstance(profile, AccountProfile) or type(test_only) is not bool:
        raise ValueError('Validated account profile and explicit boolean test mode required')
    root = Path(root)
    reject_links(root)
    if root.drive.upper() == 'D:' and test_only:
        from scripts.test_storage_policy import test_io_path
        root = test_io_path(root)
    if profile.mode is ProfileMode.PERSONAL_LOCAL:
        target = root / 'personal-local'
    else:
        target = root / 'accounts' / profile.issuer_hash / profile.subject_hash
    reject_links(target)
    stable = durable_path(target) and not test_only
    if profile.mode is ProfileMode.ENTERPRISE_ACCOUNT and not stable and not test_only:
        raise ValueError('TARGET_STORAGE_STABLE=NO: configure a durable account root')
    return AccountStorageTarget(target, profile.account_id, stable, test_only)


def require_migration_target(target, *, profile=None, root=DEFAULT_ROOT, test_only=False):
    """Real callers cannot turn a TEST_ONLY environment into migration readiness."""
    target = Path(target)
    reject_links(target)
    if type(test_only) is not bool:
        raise ValueError('Explicit boolean test mode required')
    if test_only:
        if os.environ.get(TEST_ONLY_ENV) != '1':
            raise ValueError('Disposable fixture override must be explicit')
        if target.drive.upper() == 'D:':
            from scripts.test_storage_policy import require_test_path
            require_test_path(target)
        return AccountStorageTarget(target, getattr(profile, 'account_id', None), False, True)
    if os.environ.get(TEST_ONLY_ENV) == '1':
        raise ValueError('TARGET_STORAGE_STABLE=NO: TEST_ONLY processes cannot run real migration')
    if not durable_path(target):
        raise ValueError('TARGET_STORAGE_STABLE=NO: migration blocked')
    if profile is not None:
        expected = resolve_account_storage(profile, root=root)
        if target != expected.root:
            raise ValueError('Migration target belongs to another account or device root')
        return expected
    # Legacy local archives have no enterprise identity binding. Never allow
    # this entry point to publish into an enterprise account namespace.
    if 'accounts' in [p.casefold() for p in target.parts]:
        raise ValueError('Authenticated account profile required for account migration')
    return AccountStorageTarget(target, None, True, False)

"""Cleanup limited to the completed account's managed D-drive directory."""
import os
import re
from pathlib import Path
import stat

from .account_closure_client import failure
from .account_closure_store import ClosureStore
from .login_session import account_session_lock
from .shell_paths import DATA_ROOT

MANAGED_ACCOUNTS = DATA_ROOT / 'learning' / 'accounts'


def _checked(path, root):
    """Reject links/junctions at every level before traversal or removal."""
    path, root = Path(path).absolute(), Path(root).absolute()
    if path != root and root not in path.parents:
        raise failure('CLOSURE_LOCAL_CLEANUP_FAILED')
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise failure('CLOSURE_LOCAL_CLEANUP_FAILED')
    if path.resolve() != path or root.resolve() != root:
        raise failure('CLOSURE_LOCAL_CLEANUP_FAILED')


def remove_managed_account(account_id, *, managed_accounts=MANAGED_ACCOUNTS):
    # account_id is also validated against the profile by the caller. A direct
    # caller still cannot supply a path or choose the managed parent directory.
    if not isinstance(account_id, str) or not re.fullmatch(r'mfa1_[0-9a-f]{64}', account_id):
        raise failure('CLOSURE_LOCAL_CLEANUP_FAILED')
    root = Path(managed_accounts).absolute()
    target = root / account_id
    _checked(target, root)
    if not target.exists():
        return
    if not target.is_dir():
        raise failure('CLOSURE_LOCAL_CLEANUP_FAILED')

    def inventory(path):
        _checked(path, root)
        entries = []
        with os.scandir(path) as children:
            for child in children:
                item = Path(child.path)
                _checked(item, root)
                if child.is_dir(follow_symlinks=False):
                    entries.extend(inventory(item))
                    entries.append((item, True))
                elif child.is_file(follow_symlinks=False):
                    entries.append((item, False))
                else:
                    raise failure('CLOSURE_LOCAL_CLEANUP_FAILED')
        return entries

    entries = inventory(target) + [(target, True)]
    for item, directory in entries:
        _checked(item, root)
        if directory:
            item.rmdir()
        else:
            item.unlink()


class ClosureCleanup:
    def __init__(self, session, *, managed_accounts=MANAGED_ACCOUNTS, model_client_factory=None):
        self.session = session
        self.managed_accounts = Path(managed_accounts)
        self._model_client_factory = model_client_factory

    def _clear_models_and_data(self, profile):
        _checked(self.managed_accounts / profile.account_id, self.managed_accounts)
        factory = self._model_client_factory
        if factory is None:
            # Resolve only after COMPLETED. Login and the no-save choice must
            # not depend on model configuration or on this optional UI module.
            from refactor.learning.connections.client import ModelClient
            factory = ModelClient
        client = factory(account_id=profile.account_id, data_root=self.managed_accounts.parent.parent)
        if client.clear_account_credentials() is False:
            raise failure('CLOSURE_LOCAL_CLEANUP_FAILED')
        # Preserve metadata needed for another credential cleanup attempt if
        # the module/credential store failed; the recovery record also survives.
        remove_managed_account(profile.account_id, managed_accounts=self.managed_accounts)

    def run(self, record):
        profile = ClosureStore.profile(record)
        if record['state'] != 'COMPLETED':
            raise failure()
        with account_session_lock(profile):
            failures = []
            actions = [lambda: self.session.credentials.logout(profile),
                       lambda: self.session.passwords.delete(profile),
                       lambda: self.session.registry.remove(profile.account_id),
                       lambda: self.session.set_reauthentication_required(profile, False),
                       lambda: self.session.set_revocation_pending(profile, ''),
                       lambda: self._clear_models_and_data(profile)]
            for action in actions:
                try:
                    action()
                except Exception:
                    failures.append(True)
            if failures:
                raise failure('CLOSURE_LOCAL_CLEANUP_FAILED')

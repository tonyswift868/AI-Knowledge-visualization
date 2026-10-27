"""Child process used to exercise the real per-account Windows mutex."""
from pathlib import Path
import sys

from refactor.desktop.account_identity import AccountIdentity
from refactor.desktop.login_session import account_session_lock


issuer, subject, ready_path = sys.argv[1:]
profile = AccountIdentity(issuer, subject, '208 mutex fixture').profile()
with account_session_lock(profile):
    Path(ready_path).write_text('locked', encoding='ascii')
    sys.stdin.readline()

"""Regression coverage for the account-scoped named mutex on Windows."""
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from refactor.desktop.account_identity import AccountIdentity
from refactor.desktop.account_protocol import AccountLoginResult
from refactor.desktop.login_session import LoginSession
from test_offline_login181 import Passwords, Vault, make_session


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows named account mutex regression')

ISSUER = 'https://identity.example.test/realms/mf'
HOLDER = Path(__file__).with_name('session_mutex_holder208.py')


def _start_holder(tmp_path, subject):
    ready = tmp_path / f'{uuid4().hex}.ready'
    env = dict(os.environ)
    repo_root = str(Path(__file__).resolve().parents[3])
    env['PYTHONPATH'] = os.pathsep.join(filter(None, (repo_root, env.get('PYTHONPATH', ''))))
    process = subprocess.Popen(
        [sys.executable, '-B', str(HOLDER), ISSUER, subject, str(ready)],
        cwd=repo_root, env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not ready.exists():
        if process.poll() is not None:
            error = process.stderr.read().decode('utf-8', errors='replace')
            raise AssertionError(f'lock holder exited before taking its mutex: {error}')
        time.sleep(.01)
    if not ready.exists():
        process.kill()
        process.wait(timeout=5)
        raise AssertionError('lock holder did not acquire the mutex within 10 seconds')
    return process


def _release(process):
    if process.poll() is None:
        process.stdin.write(b'release\n')
        process.stdin.flush()
        process.wait(timeout=5)
    assert process.returncode == 0


def test_session_lock208_second_process_same_identity_reproduces_busy_accept(tmp_path):
    session, profile, identity = make_session(tmp_path)
    fresh = AccountLoginResult(identity, 'fresh-access', 'fresh-refresh', {'role': 'LEARNER'})
    holder = _start_holder(tmp_path, identity.subject)
    try:
        with pytest.raises(RuntimeError, match='Account session operation is already in progress'):
            session.accept(fresh, remember=True, automatic=False, remember_password=False)
    finally:
        _release(holder)
    session.accept(fresh, remember=True, automatic=False, remember_password=False)
    assert session.credentials.load_oauth_tokens(profile) == ('fresh-access', 'fresh-refresh')
    # The failed acquisition and successful accept must not leave a named mutex
    # held in this process: another process can take the same identity again.
    _release(_start_holder(tmp_path, identity.subject))
    assert session.load_offline_snapshot(profile) is not None


def test_session_lock208_distinct_test_identities_can_hold_mutexes_together(tmp_path):
    first = AccountIdentity(ISSUER, f'worker-{uuid4().hex}', 'first').subject
    second = AccountIdentity(ISSUER, f'worker-{uuid4().hex}', 'second').subject
    assert first != second
    holders = []
    try:
        holders.append(_start_holder(tmp_path, first))
        holders.append(_start_holder(tmp_path, second))
    finally:
        for process in holders:
            _release(process)

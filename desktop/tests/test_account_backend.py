"""Real spawned SQLite API isolation; synthetic disposable account roots only."""
from pathlib import Path
import os
import sqlite3
import time

import httpx

from desktop.account_identity import AccountIdentity
from desktop.account_vault import AccountVaultManager, VaultLayout
from desktop.runtime import LocalService


def test_account_api_process_a_b_a_closes_and_never_reuses_database(tmp_path, monkeypatch):
    monkeypatch.setenv('MENTORFLOW_ACCOUNT_VAULT_ROOT', str(tmp_path))
    monkeypatch.setenv('MENTORFLOW_DATA_DIR', str(tmp_path / 'prelaunch'))
    manager = AccountVaultManager(VaultLayout(tmp_path))
    pids = []
    for subject in ('a', 'b', 'a'):
        profile = AccountIdentity('https://identity.example.test', subject, 'Synthetic ' + subject).profile()
        root = manager.prepare_profile(profile)
        database = root / 'data' / 'mentorflow_v2.db'
        database.parent.mkdir(exist_ok=True)
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS isolation_marker (account TEXT)')
            if not connection.execute('SELECT * FROM isolation_marker').fetchall():
                connection.execute('INSERT INTO isolation_marker VALUES (?)', (subject,))
        service = LocalService(resolved_profile=profile)
        try:
            service.start()
            pids.append(service.thread.pid)
            assert service.thread.pid != os.getpid()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    response = httpx.get(service.origin + '/api/health', timeout=.5)
                    if LocalService._is_expected_health(response):
                        break
                except httpx.HTTPError:
                    pass
                assert service.thread.is_alive(), 'Account API exited during startup'
                time.sleep(.1)
            else:
                raise AssertionError('Account API never became healthy')
            # Exercise the running backend's actual database reader, not just
            # the GUI metadata fixture. Returning A must see A's existing row.
            with sqlite3.connect(root / 'data' / 'app.db') as connection:
                connection.execute(
                    'INSERT OR IGNORE INTO workspaces (id,title,created_at,updated_at) VALUES (?,?,?,?)',
                    ('fixture-' + subject, 'Synthetic ' + subject, '2026-09-05T00:00:00Z', '2026-09-05T00:00:00Z'),
                )
            response = httpx.get(service.origin + '/api/workspaces', timeout=2)
            response.raise_for_status()
            assert [row['id'] for row in response.json()] == ['fixture-' + subject]
        finally:
            service.stop()
        assert service.handles_closed
        assert service.thread.exitcode == 0
        # A's original database can be exclusively opened after the API dies.
        with sqlite3.connect(database, timeout=.1) as connection:
            connection.execute('BEGIN EXCLUSIVE')
            assert connection.execute('SELECT account FROM isolation_marker').fetchall() == [(subject,)]
            assert connection.execute("SELECT count(*) FROM sqlite_master WHERE name='orm_plans'").fetchone()[0] == 1
    assert len(set(pids)) == 3

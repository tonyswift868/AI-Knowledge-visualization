"""[BUG-281] MentorFlow runs normally or as administrator; our processes only have to match each other."""
import os
import sys
from contextlib import nullcontext

import pytest

from refactor.desktop.delivery import windows_process_peer as wpp

NORMAL, ADMIN = (8192, False), (12288, True)


def _fake_tokens(monkeypatch, tmp_path, own, peer):
    exe = tmp_path / 'App' / 'MentorFlow.exe'

    def init(self, pid):
        self.pid, self.handle = pid, None

    def inspect(self):
        rid, elevated = own if self.pid == os.getpid() else peer
        return dict(pid=self.pid, executable=str(exe), created_filetime_hex='0' * 16,
                    integrity_rid=rid, elevated=elevated, alive=True)

    monkeypatch.setattr(wpp.ProcessPeer, '__init__', init)
    monkeypatch.setattr(wpp.ProcessPeer, 'inspect', inspect)
    return exe


@pytest.mark.parametrize('level', [NORMAL, ADMIN])
def test_peer_at_our_own_level_is_accepted(monkeypatch, tmp_path, level):
    exe = _fake_tokens(monkeypatch, tmp_path, level, level)
    assert wpp.ProcessPeer(1234).require(executable=exe)['integrity_rid'] == level[0]


@pytest.mark.parametrize('own, peer', [(ADMIN, NORMAL), (NORMAL, ADMIN)])
def test_peer_at_a_different_level_is_refused(monkeypatch, tmp_path, own, peer):
    exe = _fake_tokens(monkeypatch, tmp_path, own, peer)
    with pytest.raises(ValueError):
        wpp.ProcessPeer(1234).require(executable=exe)


def test_runtime_entry_runs_under_an_admin_token(monkeypatch, tmp_path):
    from refactor.desktop.delivery import per_user_install as install, per_user_runtime_session
    _fake_tokens(monkeypatch, tmp_path, ADMIN, ADMIN)
    monkeypatch.setattr(per_user_runtime_session, 'runtime_session', lambda _: nullcontext())
    monkeypatch.setattr(sys, 'argv', ['UserRuntime.exe', 'preflight', '--root', str(tmp_path / 'MentorFlow')])
    monkeypatch.setattr(install, '_execute', lambda *_: {'status': 'PASS'})
    assert install.main() == {'status': 'PASS'}

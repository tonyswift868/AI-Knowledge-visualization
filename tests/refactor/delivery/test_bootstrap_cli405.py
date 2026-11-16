"""CLI failures must not seed an empty cold target with diagnostic files."""
from contextlib import nullcontext
import sys

import pytest

from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery.update_protocol import DownloadCancelled, RecoverableError


@pytest.mark.parametrize('error', [RecoverableError('network unavailable'), DownloadCancelled('cancelled')])
def test_bootstrap_cli_failure_keeps_empty_target_retryable(tmp_path, monkeypatch, error):
    from refactor.desktop.delivery import windows_process_peer, per_user_runtime_session

    class OrdinaryPeer:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def inspect(self): return {'elevated': False, 'integrity_rid': 8192}

    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', lambda _: OrdinaryPeer())
    monkeypatch.setattr(per_user_runtime_session, 'runtime_session', lambda _: nullcontext())
    root = tmp_path / 'MentorFlow'
    root.mkdir()
    result, status = tmp_path / 'result.json', tmp_path / 'status.ini'
    monkeypatch.setattr(sys, 'argv', ['UserRuntime.exe', 'bootstrap-install', '--root', str(root),
                                     '--result', str(result), '--status-file', str(status)])
    def fail(*_): raise error
    monkeypatch.setattr(install, '_execute', fail)
    with pytest.raises(type(error)):
        install.main()
    assert not list(root.iterdir())
    assert not install.is_install(root)
    assert install.validate_target(root)['root'] == str(root)
    assert install.strict_json(result.read_bytes())['status'] == 'FAILED'
    assert 'version=' not in status.read_text(encoding='utf-16')

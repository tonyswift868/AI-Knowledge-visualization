"""I06 acceptance: retry a verifiably owned interrupted first installation.

This is a blocking acceptance check, not xfail. It uses inert signed fixture
files and injected faults; neither an App nor an installer is ever executed.
"""
import pytest
from scripts.test_per_user_install import case, ready
from scripts import per_user_install as install


def test_verified_failed_first_install_can_be_repaired_in_place(case, monkeypatch):
    base, root, feed, publish = case
    monkeypatch.setattr(install, 'keys', lambda: feed.keys)
    sibling = base / 'user-neighbor.txt'
    sibling.write_bytes(b'preserve-neighbor')
    source, envelope = publish('1.3.0', 1)
    def fail_after_activation(phase):
        if phase == 'NEW_ACTIVE': raise RuntimeError('injected first-install interruption')
    with pytest.raises(RuntimeError, match='injected first-install interruption'):
        install.transact(root, source, envelope, health=ready, fault=fail_after_activation)
    assert (root / install.HIGHEST).read_bytes() == envelope
    assert not install.is_install(root)
    assert sibling.read_bytes() == b'preserve-neighbor'
    # Same signed source, same selected parent, no manual deletion or new folder.
    result = install.install_per_user(base, source, envelope, health=ready)
    assert result['status'] == 'HEALTHY'
    assert install.installed(root).version == '1.3.0'
    assert sibling.read_bytes() == b'preserve-neighbor'

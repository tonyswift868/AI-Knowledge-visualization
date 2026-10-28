"""BUG-186: an ENOSPC-interrupted signed staging identity must be retryable."""
import errno
import hashlib
from pathlib import Path

from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_update_core as core
from tests.refactor.delivery.test_delivery_per_user_install import case
from tests.refactor.delivery.test_delivery_one_click_update_core import setup


def test_enospc_while_writing_release_identity_can_retry_without_cleanup(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    original_open = Path.open
    injected = []

    class PartialFile:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, payload):
            self.stream.write(payload[:64])
            self.stream.flush()
            injected.append(len(payload))
            raise OSError(errno.ENOSPC, 'simulated disk full')

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def fail_once(path, mode='r', *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        if path.name == 'release.json.pending' and mode == 'xb' and not injected:
            return PartialFile(stream)
        return stream

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', fail_once)
        first = core.download_latest(root, service=service)

    assert injected
    assert first['state'] == 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
    stage = service.cache.runtime_root / hashlib.sha256(service.release.envelope).hexdigest()[:12] / 'App'
    release_identity = stage.parent / 'release.json'
    assert not release_identity.exists()
    assert not (stage.parent / 'release.json.pending').exists()

    # Model a half identity left by the prior direct-write implementation.
    # Freeing disk space and a normal retry must repair it without startup cleanup.
    release_identity.write_bytes(service.release.envelope[:64])
    second = core.download_latest(root, service=service)
    assert second['state'] == 'UPDATE_DOWNLOADED'
    assert release_identity.read_bytes() == service.release.envelope
    from refactor.desktop.delivery import per_user_install as install
    assert install.installed(root).version == '1.4.0'
    assert service.staged.exists()

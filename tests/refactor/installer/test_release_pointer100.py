"""Both mf-publish modes share the signed promotion that writes latest.json."""
import json
from pathlib import Path

import pytest

from scripts.release import pipeline
from refactor.desktop.delivery.update_protocol import release_pointer, pointer_matches
from test_server_installer90 import server90
from test_release_pipeline91 import release91, fake_compile
from test_publish_optional_installer99 import stage_version


@pytest.mark.parametrize('build_installer', [False, True])
def test_release100_publish_modes_write_matching_atomic_pointer(release91, build_installer):
    f=release91
    raw=stage_version(f,'2.0.3',2)
    pipeline.promote(f.server,'2.0.3',f.app.parent/'promote',public_key=f.key)
    pointer_path=f.app/'stable/latest.json'
    assert pointer_path.stat().st_size < 256
    assert pointer_matches(release_pointer(pointer_path.read_bytes()),pipeline.verified_release(f.stable,f.key))
    if build_installer:
        pipeline.installer(f.server,'2.0.3',f.app.parent/'compile',Path('ISCC.exe'),
                           public_key=f.key, latest=True,compile_fn=fake_compile)
    f.server.call('cleanup','2.0.3')
    assert f.stable.read_bytes()==raw and not (f.app/'.staging-2.0.3').exists()
    assert not list((f.app/'stable').glob('.*'))
    assert (f.app/'releases/2.0.3/MentorFlow-Setup-2.0.3.exe').exists()==build_installer


def test_release100_pointer_write_failure_keeps_complete_old_pointer_and_retry(release91, monkeypatch):
    from scripts.release import server_ops
    f=release91;stage_version(f,'2.0.3',2)
    old=f.app/'stable/latest.json';old.write_bytes(b'{"old":"complete"}\n')
    original=server_ops.os.replace
    def fail(src,dst):
        if Path(dst)==old:raise OSError('pointer rename interrupted')
        return original(src,dst)
    with monkeypatch.context() as m:
        m.setattr(server_ops.os,'replace',fail)
        with pytest.raises(OSError,match='interrupted'):
            pipeline.promote(f.server,'2.0.3',f.app.parent/'promote',public_key=f.key)
    assert old.read_bytes()==b'{"old":"complete"}\n'
    assert (f.app/'.staging-2.0.3').exists()
    pipeline.promote(f.server,'2.0.3',f.app.parent/'retry',public_key=f.key)
    assert pointer_matches(release_pointer(old.read_bytes()),pipeline.verified_release(f.stable,f.key))

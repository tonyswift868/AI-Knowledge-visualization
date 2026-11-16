"""Local bootstrap publication uses immutable cores and a fixed atomic stub path."""
from pathlib import Path

from scripts.release import pipeline
from scripts.release.transport import LocalServer


def fake_build_core(args):
    args.output.mkdir(parents=True, exist_ok=True)
    artifact = args.output / 'bootstrap-core.zip'
    artifact.write_bytes(b'PK core fixture')
    return dict(artifact='bootstrap-core.zip', path=str(artifact), size=artifact.stat().st_size,
                sha256=pipeline.builder.digest(artifact))


def fake_build_stub(args):
    args.output.mkdir(parents=True, exist_ok=True)
    artifact = args.output / 'MentorFlow-Setup.exe'
    artifact.write_bytes(b'MZ stub:' + args.core_sha256.encode())
    return dict(artifact='MentorFlow-Setup.exe', path=str(artifact), size=artifact.stat().st_size,
                sha256=pipeline.builder.digest(artifact))


def test_bootstrap405_first_build_then_skips_both_and_keeps_immutable_core(tmp_path, monkeypatch):
    server = LocalServer(tmp_path / 'server')
    counts = dict(core=0, stub=0)
    def core(args):
        counts['core'] += 1
        return fake_build_core(args)
    def stub(args):
        counts['stub'] += 1
        return fake_build_stub(args)
    core_fp, _ = pipeline.bootstrap_fingerprints()
    first = pipeline.ensure_bootstrap(server, tmp_path / 'first', Path('ISCC.exe'),
        core_compile_fn=core, stub_compile_fn=stub)
    fixed = server.app / 'releases/bootstrap/MentorFlow-Setup.exe'
    core_path = server.app / 'releases/bootstrap' / core_fp / 'bootstrap-core.zip'
    original_core = core_path.read_bytes()
    assert first['status'] == 'BOOTSTRAP_UPDATED'
    assert fixed.read_bytes().startswith(b'MZ stub:')
    assert core_path.exists() and first['core_url'].endswith(core_fp + '/bootstrap-core.zip')

    second = pipeline.ensure_bootstrap(server, tmp_path / 'second', Path('ISCC.exe'),
        core_compile_fn=core, stub_compile_fn=stub)
    assert second['status'] == 'BOOTSTRAP_UNCHANGED'
    assert counts == dict(core=1, stub=1)
    assert core_path.read_bytes() == original_core


def test_bootstrap405_public_files_and_directories_get_explicit_permissions(tmp_path, monkeypatch):
    import os
    server = LocalServer(tmp_path / 'server')
    chmod_calls = []
    original_chmod = Path.chmod
    def chmod(path, mode):
        chmod_calls.append((Path(path).name, mode))
        return original_chmod(path, mode)
    monkeypatch.setattr(Path, 'chmod', chmod)
    previous_mask = os.umask(0o077)
    try:
        pipeline.ensure_bootstrap(server, tmp_path / 'permission-test', Path('ISCC.exe'),
            core_compile_fn=fake_build_core, stub_compile_fn=fake_build_stub)
    finally:
        os.umask(previous_mask)
    assert ('bootstrap', 0o755) in chmod_calls
    assert ('bootstrap-core.zip', 0o644) in chmod_calls
    assert any(name.startswith('.MentorFlow-Setup-') and mode == 0o644 for name, mode in chmod_calls)
    assert ('bootstrap-core.json', 0o644) in chmod_calls
    assert ('bootstrap-state.json', 0o644) in chmod_calls


def test_bootstrap405_stub_failure_does_not_replace_old_stub_or_state(tmp_path, monkeypatch):
    server = LocalServer(tmp_path / 'server')
    previous = pipeline.ensure_bootstrap(server, tmp_path / 'initial', Path('ISCC.exe'),
        core_compile_fn=fake_build_core, stub_compile_fn=fake_build_stub)
    target = server.app / 'releases/bootstrap/MentorFlow-Setup.exe'
    old_bytes = target.read_bytes()
    old_state = (server.app / 'releases/bootstrap/bootstrap-state.json').read_bytes()
    new_core = 'f' * 64
    old_fingerprints = pipeline.bootstrap_fingerprints
    monkeypatch.setattr(pipeline, 'bootstrap_fingerprints', lambda public_key=pipeline.PUBLIC: (new_core, 'e' * 64))
    def fail(args):
        raise RuntimeError('compiler failure')
    try:
        import pytest
        with pytest.raises(RuntimeError, match='compiler failure'):
            pipeline.ensure_bootstrap(server, tmp_path / 'failed', Path('ISCC.exe'),
                core_compile_fn=fake_build_core, stub_compile_fn=fail)
    finally:
        monkeypatch.setattr(pipeline, 'bootstrap_fingerprints', old_fingerprints)
    assert target.read_bytes() == old_bytes
    assert (server.app / 'releases/bootstrap/bootstrap-state.json').read_bytes() == old_state
    assert (server.app / 'releases/bootstrap' / new_core / 'bootstrap-core.zip').is_file()


def test_bootstrap_first_publish_without_compiler_provisions_inno_only_for_stub(tmp_path, monkeypatch):
    # mf-publish passes no --compiler; a first publish builds the core and must not fail on Path(None) (BUG-269).
    server = LocalServer(tmp_path / 'server')
    seen = {}
    monkeypatch.setattr(pipeline, 'provision_inno', lambda work: Path('provisioned-ISCC.exe'))
    def stub(args):
        seen['stub'] = args.compiler
        return fake_build_stub(args)
    result = pipeline.ensure_bootstrap(server, tmp_path / 'first', None,
        core_compile_fn=fake_build_core, stub_compile_fn=stub)
    assert result['status'] == 'BOOTSTRAP_UPDATED'
    assert seen['stub'] == Path('provisioned-ISCC.exe')

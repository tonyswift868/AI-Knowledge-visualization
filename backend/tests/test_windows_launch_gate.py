"""Windows protected-cache and content gates; only synthetic disposable packages."""
import os
from pathlib import Path

import pytest

from scripts import verify_windows_package as v
from backend.tests.test_windows_package_privacy import _package

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI and directory notification integration')


@pytest.fixture
def gate(tmp_path):
    root = _package(tmp_path)
    (root / "QtCore.dll").write_bytes(b"synthetic DLL")
    (root / "resource.png").write_bytes(b"synthetic image")
    manifest = v.build_manifest(root)
    state = tmp_path / "runtime" / "verification.dpapi"
    anchor = v.sha256_file(manifest)
    return root, state, anchor


def run(gate):
    root, state, anchor = gate
    return v.verify_launch_gate(root, state_path=state, expected_manifest_sha256=anchor)


def test_full_then_repeat_protected_state(gate, monkeypatch):
    assert run(gate)["mode"] == "FULL"
    assert not gate[1].read_bytes().startswith(b"{")
    original = v.sha256_file
    hashed = []
    def measured(path):
        hashed.append(path.name)
        return original(path)
    monkeypatch.setattr(v, "sha256_file", measured)
    assert run(gate)["mode"] == "REPEAT"
    assert "MentorFlow.exe" in hashed and "QtCore.dll" in hashed
    assert "resource.png" not in hashed


@pytest.mark.parametrize("name", ["MentorFlow.exe", "QtCore.dll", "resource.png"])
def test_tamper_rejected_even_with_restored_mtime(gate, name):
    run(gate)
    path = gate[0] / name
    original = path.stat()
    path.write_bytes(b"X" * original.st_size)
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(v.PackageError, match="tampered"):
        run(gate)


def test_manifest_and_unknown_file_rejected(gate):
    run(gate)
    extra = gate[0] / "unknown.cfg"
    extra.write_text("unknown=true")
    with pytest.raises(v.PackageError, match="unexpected"):
        run(gate)
    extra.unlink()
    manifest = gate[0] / v.MANIFEST_RELATIVE
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(v.PackageError, match="manifest trust anchor"):
        run(gate)


@pytest.mark.parametrize("damage", [b'{"mode":"PASS"}', b"corrupt DPAPI"])
def test_edited_cache_forces_full_verification(gate, damage):
    run(gate)
    gate[1].write_bytes(damage)
    assert run(gate)["mode"] == "FULL"
    gate[1].write_bytes(damage)
    (gate[0] / "resource.png").write_bytes(b"X" * len(b"synthetic image"))
    with pytest.raises(v.PackageError, match="tampered"):
        run(gate)


def test_change_during_hash_rejected_without_second_walk(gate, monkeypatch):
    run(gate)
    original = v.sha256_file
    def changed(path):
        digest = original(path)
        if path.name == "QtCore.dll":
            (gate[0] / "late.bin").write_bytes(b"late")
        return digest
    monkeypatch.setattr(v, "sha256_file", changed)
    with pytest.raises(v.PackageError, match="changed during"):
        run(gate)


def test_category_deny_by_default():
    for name in ["app.exe", "Qt6Core.dll", "native.pyd", "bootstrap.py", "ui.qml", "data.pak", "frontend.js", "image.svg", "settings.json", "unknown"]:
        assert v.package_file_category(name) == "CRITICAL_EXECUTABLE"
    assert v.package_file_category("assets/logo.png") == "IMMUTABLE_RESOURCE"


def test_cache_expiry_forces_full(gate, monkeypatch):
    run(gate)
    now = v.time.time()
    monkeypatch.setattr(v.time, 'time', lambda: now + 86401)
    assert run(gate)['mode'] == 'FULL'


def test_resource_metadata_unavailable_never_reuses_cache(gate, monkeypatch):
    monkeypatch.setattr(v, '_resource_change_time', lambda path: 0)
    assert run(gate)['mode'] == 'FULL'
    assert run(gate)['mode'] == 'FULL'


def test_single_enumeration_and_bounded_parallel_hashes(gate, monkeypatch):
    original = os.scandir
    scans = []
    def measured(path):
        scans.append(str(path))
        return original(path)
    monkeypatch.setattr(os, 'scandir', measured)
    root, state, anchor = gate
    assert v.verify_launch_gate(root, state_path=state, expected_manifest_sha256=anchor, hash_workers=4)['mode'] == 'FULL'
    assert len(scans) == len(set(scans))
    assert v.verify_launch_gate(root, state_path=state, expected_manifest_sha256=anchor, hash_workers=4)['mode'] == 'REPEAT'
    (root / 'QtCore.dll').write_bytes(b'X' * len(b'synthetic DLL'))
    with pytest.raises(v.PackageError, match='tampered'):
        v.verify_launch_gate(root, state_path=state, expected_manifest_sha256=anchor, hash_workers=4)


def test_root_replay_and_missing_file_do_not_reuse_state(gate, tmp_path):
    import shutil
    run(gate)
    root, state, anchor = gate
    other = tmp_path / 'other-package'
    shutil.copytree(root, other)
    assert run((other, state, anchor))['mode'] == 'FULL'
    (other / 'resource.png').unlink()
    with pytest.raises(v.PackageError, match='missing payload'):
        run((other, state, anchor))


def test_hardlink_is_rejected_on_fast_path(gate, tmp_path):
    run(gate)
    root, _state, _anchor = gate
    source = root / 'QtCore.dll'
    outside = tmp_path / 'external.dll'
    outside.write_bytes(source.read_bytes())
    source.unlink()
    os.link(outside, source)
    with pytest.raises(v.PackageError, match='hardlink'):
        run(gate)


def test_cache_cannot_be_placed_inside_package_through_dotdot(gate, tmp_path):
    root, _state, anchor = gate
    alias = tmp_path / 'unused' / '..' / root.name / 'state.dpapi'
    with pytest.raises(v.PackageError, match='outside immutable'):
        v.verify_launch_gate(root, state_path=alias, expected_manifest_sha256=anchor)
    assert not (root / 'state.dpapi').exists()


def test_root_replacement_during_hash_fails_closed(gate, tmp_path, monkeypatch):
    import shutil
    run(gate)
    root = gate[0]
    original = v.sha256_file
    def replace_root(path):
        digest = original(path)
        if path.name == 'QtCore.dll':
            parked = tmp_path / 'parked-package'
            root.rename(parked)
            shutil.copytree(parked, root)
        return digest
    monkeypatch.setattr(v, 'sha256_file', replace_root)
    with pytest.raises(v.PackageError, match='changed during'):
        run(gate)

"""Offline server reconstruction and publishing; never execute an installer."""
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from refactor.desktop.delivery.update_protocol import canonical, digest, SecurityError
from scripts import build_learning_installer as builder
from scripts.server_installer import assemble_payload as assembly
from scripts.server_installer import run_build as server


@pytest.fixture
def server90(tmp_path):
    """Ephemeral TEST signing key; no network or official private key."""
    private = Ed25519PrivateKey.generate()
    key = tmp_path / "public.json"
    key.write_text(json.dumps(dict(key_id="fixture90", public_key=base64.b64encode(
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())))
    app = tmp_path / "app"
    objects = app / "objects"
    objects.mkdir(parents=True)
    files = {"MentorFlow.exe": b"MZ test exe", "UserRuntime/UserRuntime.exe": b"MZ updater",
             "_internal/中文/lesson.txt": b"fixture lesson", "_internal/.hidden": b"hidden file"}
    rows = [dict(path=name, size=len(data), sha256=digest(data)) for name, data in files.items()]
    manifest = dict(schema="mentorflow.windows-package-manifest.v1", product="MentorFlow",
                    version="2.0.3", executable="MentorFlow.exe", files=rows)
    files["SELF-CHECK/package-manifest.json"] = canonical(manifest)
    assets = []
    for name, data in files.items():
        (objects / digest(data)).write_bytes(data)
        assets.append(dict(path=name, size=len(data), sha256=digest(data), url="https://example.org/objects/" + digest(data)))
    now = datetime.now(timezone.utc)
    signed = dict(schema_version=1, app_id="MentorFlow", channel="stable", version="2.0.3",
        published_at=now.isoformat(), issued_at=now.isoformat(), expires_at=(now + timedelta(days=7)).isoformat(),
        key_id="fixture90", metadata_version=1, rollout_percentage=100, rollout_seed="fixture90", withdrawn=False,
        min_supported_version="2.0.3", min_updater_version="1.0.0", mandatory=True, mandatory_reason="data_compatibility",
        release_notes="test", full_release=dict(format="file-set-v1", size=sum(len(d) for d in files.values()), assets=assets),
        incremental_assets=[], file_manifest=manifest, hash_algorithm="sha256", signature_algorithm="ed25519",
        source_sha="a" * 40, tree_sha="b" * 40, base_versions=["2.0.2"])
    stable = app / "stable/stable.json"
    stable.parent.mkdir()
    stable.write_bytes(canonical(dict(signed=signed, key_id="fixture90", signature=base64.b64encode(
        private.sign(canonical(signed))).decode())))
    return SimpleNamespace(app=app, objects=objects, stable=stable, key=key, files=files,
                           state=tmp_path / "state", output=tmp_path / "payload")


@pytest.mark.parametrize("copy_only", [False, True])
def test_server90_assemble_complete_and_final_tree_verified(server90, monkeypatch, copy_only):
    if copy_only:
        def no_link(*args):
            raise OSError("different filesystems")
        monkeypatch.setattr(assembly.os, "link", no_link)
    calls = []
    original = assembly.verify_tree
    def verify(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(assembly, "verify_tree", verify)
    f = server90
    stats = assembly.assemble_payload(f.stable, f.objects, f.output, public_key=f.key)
    assert calls == [True] and stats["files"] == len(f.files)
    assert stats["copied"] == (len(f.files) if copy_only else 0)
    assert {p.relative_to(f.output).as_posix(): p.read_bytes() for p in f.output.rglob("*") if p.is_file()} == f.files


@pytest.mark.parametrize("fault", ["missing", "hash", "size", "signature", "final_tree"])
def test_server90_failure_has_no_partial_output(server90, monkeypatch, fault):
    f = server90
    source = f.objects / digest(f.files["_internal/中文/lesson.txt"])
    if fault == "missing": source.unlink()
    if fault == "hash": source.write_bytes(b"X" * source.stat().st_size)
    if fault == "size": source.write_bytes(b"X")
    if fault == "signature":
        value = json.loads(f.stable.read_bytes())
        value["signed"]["release_notes"] = "tampered"
        f.stable.write_bytes(canonical(value))
    if fault == "final_tree":
        def reject(*args): raise SecurityError("injected final tree mismatch")
        monkeypatch.setattr(assembly, "verify_tree", reject)
    with pytest.raises((FileNotFoundError, SecurityError)):
        assembly.assemble_payload(f.stable, f.objects, f.output, public_key=f.key)
    assert not f.output.exists() and not list(f.output.parent.glob(".payload-*"))


def test_server90_existing_output_untouched(server90):
    f = server90
    f.output.mkdir()
    (f.output / "keep").write_bytes(b"prior")
    with pytest.raises(FileExistsError):
        assembly.assemble_payload(f.stable, f.objects, f.output, public_key=f.key)
    assert (f.output / "keep").read_bytes() == b"prior"


def fake_compile(args):
    args.output.mkdir()
    (args.output / "MentorFlow-Setup.exe").write_bytes(b"MZ newly compiled fixture")


def test_server90_build_publish_backup_cleanup_and_repeat(server90):
    f = server90
    directory = f.app / "installer"
    directory.mkdir()
    (directory / "MentorFlow-Setup.exe").write_bytes(b"MZ previous fixture")
    result = server.build_on_server(f.app, f.state, f.key, Path("ISCC.exe"), compile_fn=fake_compile)
    assert result["status"] == "PUBLISHED"
    assert Path(result["backup"]).read_bytes() == b"MZ previous fixture"
    assert server.current_installer(directory, "2.0.3")
    assert not list((f.state / "installer-build").rglob("attempt-*"))
    def forbidden(*args): raise AssertionError("repeat must not compile")
    assert server.build_on_server(f.app, f.state, f.key, Path("ISCC.exe"), compile_fn=forbidden)["status"] == "UP_TO_DATE"
    # A stale or mismatching exe must rebuild even if VERSION says this version.
    (directory / "MentorFlow-Setup.exe").write_bytes(b"corrupted")
    assert not server.current_installer(directory, "2.0.3")


@pytest.mark.parametrize("fault", ["compiler", "feed_changed", "metadata_replace"])
def test_server90_build_failure_keeps_live_installer_and_version(server90, monkeypatch, fault):
    f = server90
    directory = f.app / "installer"
    directory.mkdir()
    exe, version = directory / "MentorFlow-Setup.exe", directory / "VERSION.txt"
    exe.write_bytes(b"old installer")
    version.write_bytes(b"old metadata")
    def compile_fn(args):
        if fault == "compiler": raise RuntimeError("compiler failed")
        fake_compile(args)
        if fault == "feed_changed": f.stable.write_bytes(f.stable.read_bytes() + b"\n")
    if fault == "metadata_replace":
        original = os.replace
        def replace(source, target):
            if Path(source).name == ".VERSION.txt.new": raise OSError("metadata denied")
            return original(source, target)
        monkeypatch.setattr(server.os, "replace", replace)
    with pytest.raises((RuntimeError, ValueError, OSError)):
        server.build_on_server(f.app, f.state, f.key, Path("ISCC.exe"), compile_fn=compile_fn)
    assert exe.read_bytes() == b"old installer" and version.read_bytes() == b"old metadata"
    assert not list(directory.glob(".*.new"))
    assert list(f.state.rglob("build-status.json"))
    assert not list(f.state.rglob("payload"))


def test_server90_first_publish_metadata_failure_leaves_no_installer(server90, monkeypatch):
    f = server90
    original = os.replace
    def replace(source, target):
        if Path(source).name == ".VERSION.txt.new": raise OSError("metadata denied")
        return original(source, target)
    monkeypatch.setattr(server.os, "replace", replace)
    with pytest.raises(OSError, match="metadata denied"):
        server.build_on_server(f.app, f.state, f.key, Path("ISCC.exe"), compile_fn=fake_compile)
    assert not list((f.app / "installer").iterdir())


def test_server90_failed_signature_never_calls_compiler_or_touches_installer(server90):
    f = server90
    live = f.app / "installer/MentorFlow-Setup.exe"
    live.parent.mkdir()
    live.write_bytes(b"previous installer")
    value = json.loads(f.stable.read_bytes())
    value["signed"]["version"] = "2.0.4"
    f.stable.write_bytes(canonical(value))
    def forbidden(*args): raise AssertionError("must not compile unverified release")
    with pytest.raises(SecurityError):
        server.build_on_server(f.app, f.state, f.key, Path("ISCC.exe"), compile_fn=forbidden)
    assert live.read_bytes() == b"previous installer" and not f.state.exists()


def test_server90_windows_command_unchanged_and_wine_paths_mapped(monkeypatch, tmp_path):
    options = dict(PayloadDir=tmp_path / "中文 payload", ProductVersion="2.0.3", RequiredBytes=123)
    script = tmp_path / "MentorFlow.iss"
    command = builder.installer_command(Path("ISCC.exe"), options, script)
    assert command == [Path("ISCC.exe"), "--no-ide-signtools", f"/DPayloadDir={options['PayloadDir']}",
                       "/DProductVersion=2.0.3", "/DRequiredBytes=123", script]
    monkeypatch.setattr(builder, "wine_path", lambda p: "Z:\\mapped\\" + Path(p).name)
    command = builder.installer_command(Path("ISCC.exe"), options, script, wine=Path("wine"))
    assert command[0] == Path("wine") and command[1] == "Z:\\mapped\\ISCC.exe"
    assert "/DPayloadDir=Z:\\mapped\\中文 payload" in command
    assert "/DProductVersion=2.0.3" in command and "/DRequiredBytes=123" in command
    assert command[-1] == "Z:\\mapped\\MentorFlow.iss"


def test_server90_posix_root_maps_to_wine_z(monkeypatch):
    class Posix:
        def __init__(self, value): self.value = PurePosixPath(value)
        def resolve(self): return self
        def as_posix(self): return self.value.as_posix()
    monkeypatch.setattr(builder, "Path", Posix)
    assert builder.wine_path("/srv/中文 payload/MentorFlow.exe") == "Z:\\srv\\中文 payload\\MentorFlow.exe"


def test_server90_linux_keeps_system_path_for_wine(monkeypatch, tmp_path):
    # Test only this platform seam; no Wine, subprocess or compiler is executed.
    calls = []
    monkeypatch.setattr(builder, "os", SimpleNamespace(name="posix", environ={"PATH": "/usr/local/bin:/usr/bin:/bin"}))
    monkeypatch.setattr(builder.subprocess, "run", lambda command, **kw: calls.append(kw))
    builder.run(["wine", "ISCC.exe"], tmp_path, tmp_path / "log")
    assert calls[0]["env"]["PATH"] == "/usr/local/bin:/usr/bin:/bin"

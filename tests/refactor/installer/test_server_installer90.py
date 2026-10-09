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
    return SimpleNamespace(private=private, app=app, objects=objects, stable=stable, key=key, files=files,
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

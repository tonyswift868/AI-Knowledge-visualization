from __future__ import annotations

import base64
import builtins
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import teacher_voice_pack as voice_pack


PACK_ID = "mentorflow-original-teacher"
KEY_ID = "mentorflow-test-key"
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(
    bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
)
PUBLIC_KEY = PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)


def _payloads() -> dict[str, bytes]:
    return {
        "models/teacher-gpt.ckpt": b"trained-gpt-model-fixture",
        "models/teacher-sovits.pth": b"trained-sovits-model-fixture",
        "references/lively.wav": b"RIFF" + b"lively" * 8,
        "references/relaxed.wav": b"RIFF" + b"relaxed" * 8,
        "rights/release.json": b'{"rights":"fixture"}',
    }


def _manifest(
    version: str,
    payloads: dict[str, bytes],
    *,
    path_override: str | None = None,
    missing_right: str | None = None,
) -> dict:
    roles = {
        "models/teacher-gpt.ckpt": "gpt_weight",
        "models/teacher-sovits.pth": "sovits_weight",
        "references/lively.wav": "reference_audio",
        "references/relaxed.wav": "reference_audio",
        "rights/release.json": "rights_evidence",
    }
    files = []
    for path, data in payloads.items():
        declared_path = path_override if path == "models/teacher-gpt.ckpt" and path_override else path
        files.append(
            {
                "path": declared_path,
                "role": roles[path],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    covers = sorted(voice_pack.REQUIRED_RIGHTS - ({missing_right} if missing_right else set()))
    return {
        "schema": voice_pack.PACK_SCHEMA,
        "pack_id": PACK_ID,
        "version": version,
        "identity": {
            "kind": "original",
            "display_name": "MentorFlow 原创教师声线",
            "impersonates": False,
            "source_identity": None,
        },
        "rights": {
            "status": "verified",
            "covers": covers,
            "evidence": ["rights/release.json"],
        },
        "runtime": {"adapter": "mentorflow.gpt-sovits.v1"},
        "styles": {
            "lively": {
                "reference_audio": "references/lively.wav",
                "language": "zh",
                "transcript": "今天我们轻快地继续。",
            },
            "relaxed": {
                "reference_audio": "references/relaxed.wav",
                "language": "zh",
                "transcript": "先慢慢理清这一步。",
            },
        },
        "files": files,
    }


def _build_archive(
    catalog_dir: Path,
    version: str,
    *,
    tamper_payload: bool = False,
    path_override: str | None = None,
    missing_right: str | None = None,
    symlink_payload: bool = False,
    invalid_signature: bool = False,
) -> dict:
    payloads = _payloads()
    manifest = _manifest(
        version,
        payloads,
        path_override=path_override,
        missing_right=missing_right,
    )
    raw_manifest = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature_bytes = PRIVATE_KEY.sign(raw_manifest)
    if invalid_signature:
        signature_bytes = bytes([signature_bytes[0] ^ 1]) + signature_bytes[1:]
    raw_signature = json.dumps(
        {
            "algorithm": "ed25519",
            "key_id": KEY_ID,
            "signature": base64.b64encode(signature_bytes).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    bundled = catalog_dir / "bundled"
    bundled.mkdir(parents=True, exist_ok=True)
    archive_path = bundled / f"{PACK_ID}-{version}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("voice-pack.json", raw_manifest)
        archive.writestr("voice-pack.sig", raw_signature)
        for path, data in payloads.items():
            member = path_override if path == "models/teacher-gpt.ckpt" and path_override else path
            actual = data + b"tampered" if tamper_payload and path == "models/teacher-gpt.ckpt" else data
            if symlink_payload and path == "models/teacher-gpt.ckpt":
                info = zipfile.ZipInfo(member)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, actual)
            else:
                archive.writestr(member, actual)
    return {
        "pack_id": PACK_ID,
        "version": version,
        "display_name": "MentorFlow 原创教师声线",
        "default": version == "1.0.0",
        "source": {
            "kind": "bundled",
            "archive_path": f"bundled/{archive_path.name}",
        },
        "archive": {
            "bytes": archive_path.stat().st_size,
            "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        },
        "signing_key_id": KEY_ID,
    }


def _configure(
    monkeypatch,
    tmp_path: Path,
    entries: list[dict],
    *,
    https_allowed_hosts: list[str] | None = None,
) -> tuple[Path, Path]:
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = catalog_dir / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema": voice_pack.CATALOG_SCHEMA,
                "https_allowed_hosts": https_allowed_hosts or [],
                "trusted_keys": [
                    {
                        "key_id": KEY_ID,
                        "public_key": base64.b64encode(PUBLIC_KEY).decode("ascii"),
                    }
                ],
                "packs": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    root = tmp_path / "installed-packs"
    monkeypatch.setenv("MENTORFLOW_VOICE_PACK_CATALOG", str(catalog_path))
    monkeypatch.setenv("MENTORFLOW_VOICE_PACK_ROOT", str(root))
    return catalog_path, root


def _one_pack(monkeypatch, tmp_path: Path, **archive_options) -> tuple[dict, Path]:
    catalog_dir = tmp_path / "catalog"
    entry = _build_archive(catalog_dir, "1.0.0", **archive_options)
    _catalog, root = _configure(monkeypatch, tmp_path, [entry])
    return entry, root


def test_install_activate_status_settings_and_job_contract(client, monkeypatch, tmp_path):
    _entry, root = _one_pack(monkeypatch, tmp_path)

    pristine_status = client.get("/api/teacher-voice/status")
    assert pristine_status.status_code == 200
    assert pristine_status.json()["active_pack"] is None
    assert pristine_status.json()["fallback_policy"] == "off"
    assert not root.exists(), "read-only status must not create the pack root"

    installed = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert installed.status_code == 200
    install_body = installed.json()
    assert install_body["job"]["status"] == "installed"
    assert [event["status"] for event in install_body["job"]["history"]] == [
        "not_installed",
        "staging",
        "verified",
        "installed",
    ]
    assert install_body["pack"]["state"] == "installed"
    job = client.get(f"/api/teacher-voice/jobs/{install_body['job']['job_id']}")
    assert job.status_code == 200 and job.json()["status"] == "installed"

    activated = client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"})
    assert activated.status_code == 200
    assert activated.json()["job"]["status"] == "active"
    status = client.get("/api/teacher-voice/status").json()
    assert status["active_pack"]["pack_id"] == PACK_ID
    assert status["active_pack"]["version"] == "1.0.0"
    assert status["runtime"]["state"] == "unavailable"
    assert status["runtime"]["starts_model"] is False
    assert status["runtime"]["adapter"] == "mentorflow.gpt-sovits.v1"
    assert status["runtime"]["prepared_pack"] is None
    assert len(status["runtime"]["manifest_sha256"]) == 64

    assert client.get("/api/teacher-voice/settings").json() == {"fallback_policy": "off"}
    changed = client.patch("/api/teacher-voice/settings", json={"fallback_policy": "system_voice"})
    assert changed.status_code == 200
    assert changed.json() == {"fallback_policy": "system_voice"}


def test_catalog_exposes_private_prerelease_scope_without_changing_pack_trust(
    client, monkeypatch, tmp_path
):
    catalog_dir = tmp_path / "catalog"
    entry = _build_archive(catalog_dir, "1.0.0")
    entry["release_channel"] = "prerelease"
    entry["usage_scope"] = "local_private"
    _configure(monkeypatch, tmp_path, [entry])

    response = client.get("/api/teacher-voice/packs")

    assert response.status_code == 200
    assert response.json()["packs"][0]["release_channel"] == "prerelease"
    assert response.json()["packs"][0]["usage_scope"] == "local_private"


def test_catalog_rejects_unknown_release_or_usage_scope(client, monkeypatch, tmp_path):
    catalog_dir = tmp_path / "catalog"
    entry = _build_archive(catalog_dir, "1.0.0")
    entry["release_channel"] = "experimental"
    entry["usage_scope"] = "share_anywhere"
    _configure(monkeypatch, tmp_path, [entry])

    response = client.get("/api/teacher-voice/packs")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "VOICE_PACK_CATALOG_INVALID"


def test_archive_hash_mismatch_fails_before_extraction(client, monkeypatch, tmp_path):
    entry, root = _one_pack(monkeypatch, tmp_path)
    archive = tmp_path / "catalog" / entry["source"]["archive_path"]
    archive.write_bytes(archive.read_bytes() + b"changed-after-catalog")

    response = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {
        "VOICE_PACK_ARCHIVE_SIZE_MISMATCH",
        "VOICE_PACK_ARCHIVE_HASH_MISMATCH",
    }
    assert not (root / PACK_ID / "1.0.0").exists()


def test_invalid_signature_fails_closed(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path, invalid_signature=True)
    response = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VOICE_PACK_SIGNATURE_INVALID"
    failed_job = client.get(
        f"/api/teacher-voice/jobs/{response.json()['error']['details']['job_id']}"
    )
    assert failed_job.status_code == 200
    assert failed_job.json()["status"] == "failed"
    assert failed_job.json()["error"]["code"] == "VOICE_PACK_SIGNATURE_INVALID"


def test_request_cannot_supply_url_or_local_source(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)
    response = client.post(
        f"/api/teacher-voice/packs/{PACK_ID}/install",
        json={
            "version": "1.0.0",
            "url": "https://attacker.invalid/voice.zip",
            "source_path": "C:/untrusted/voice.zip",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VOICE_PACK_REQUEST_INVALID"


def test_missing_crypto_dependency_is_a_hard_failure(monkeypatch):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("blocked cryptography fixture")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack.verify_ed25519(PUBLIC_KEY, b"manifest", b"\x22" * 64)
    assert raised.value.code == "VOICE_PACK_SIGNATURE_VERIFIER_UNAVAILABLE"
    assert raised.value.status_code == 503


def test_real_ed25519_rfc8032_fixture():
    public_key = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert voice_pack.verify_ed25519(public_key, b"", signature) is True


def test_payload_hash_mismatch_is_rejected(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path, tamper_payload=True)
    response = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {
        "VOICE_PACK_FILE_SIZE_MISMATCH",
        "VOICE_PACK_FILE_HASH_MISMATCH",
    }


def test_archive_path_escape_is_rejected(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path, path_override="../escape.bin")
    response = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VOICE_PACK_PATH_INVALID"
    assert not (tmp_path / "escape.bin").exists()


def test_archive_symlink_is_rejected(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path, symlink_payload=True)
    response = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VOICE_PACK_SYMLINK_FORBIDDEN"


def test_incomplete_distribution_rights_are_rejected(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path, missing_right="weight_distribution")
    response = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VOICE_PACK_RIGHTS_INCOMPLETE"


def test_atomic_promotion_failure_preserves_active_old_version(client, monkeypatch, tmp_path):
    catalog_dir = tmp_path / "catalog"
    v1 = _build_archive(catalog_dir, "1.0.0")
    v2 = _build_archive(catalog_dir, "1.1.0")
    _catalog, root = _configure(monkeypatch, tmp_path, [v1, v2])
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"}).status_code == 200

    def interrupted_promotion(_staged, _final, _root):
        raise OSError("simulated atomic rename failure")

    monkeypatch.setattr(voice_pack, "_atomic_promote", interrupted_promotion)
    failed = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.1.0"})
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "VOICE_PACK_INSTALL_FAILED"
    assert not (root / PACK_ID / "1.1.0").exists()
    status = client.get("/api/teacher-voice/status").json()
    assert status["active_pack"]["version"] == "1.0.0"


def test_active_version_cannot_be_deleted(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"}).status_code == 200
    response = client.delete(f"/api/teacher-voice/packs/{PACK_ID}/versions/1.0.0")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VOICE_PACK_ACTIVE_DELETE_FORBIDDEN"


def test_installed_version_is_immutable(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)
    first = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert first.status_code == 200
    second = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "VOICE_PACK_VERSION_EXISTS"


def test_status_uses_passive_adapter_only(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)

    class PassiveAdapter:
        status_calls = 0
        prepare_calls = 0
        synthesize_calls = 0

        def status(self, _pack):
            self.status_calls += 1
            return {"state": "stopped", "starts_model": False}

        async def prepare(self, *args, **kwargs):
            self.prepare_calls += 1
            raise AssertionError("status must not prepare or start a model")

        async def synthesize(self, *args, **kwargs):
            self.synthesize_calls += 1
            raise AssertionError("status must not synthesize or start a model")

    adapter = PassiveAdapter()
    monkeypatch.setitem(voice_pack._RUNTIME_ADAPTERS, "mentorflow.gpt-sovits.v1", adapter)
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"}).status_code == 200
    response = client.get("/api/teacher-voice/status")
    assert response.status_code == 200
    assert response.json()["runtime"]["state"] == "stopped"
    assert adapter.status_calls == 1
    assert adapter.prepare_calls == 0
    assert adapter.synthesize_calls == 0


def test_casual_chat_defaults_to_active_pack_adapter(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"}).status_code == 200

    class FixtureAdapter:
        prepare_calls = 0

        def status(self, _pack):
            return {"state": "ready", "starts_model": False}

        async def prepare(self, pack, *, installed_root, manifest_sha256):
            self.prepare_calls += 1
            assert pack["pack_id"] == PACK_ID
            assert len(manifest_sha256) == 64
            return {"prepared": True}

        async def synthesize(self, pack, *, text, style, reply_kind, installed_root):
            assert pack["pack_id"] == PACK_ID
            assert text == "请解释这个边界条件"
            assert style == "relaxed"
            assert installed_root == tmp_path / "installed-packs" / PACK_ID / "1.0.0"
            return voice_pack.SynthesizedTeacherVoice(
                audio=b"RIFF" + (b"\x00" * 64),
                media_type="audio/wav",
                engine="fixture-engine",
                profile=style,
                cache="miss",
            )

    monkeypatch.setitem(voice_pack._RUNTIME_ADAPTERS, "mentorflow.gpt-sovits.v1", FixtureAdapter())
    monkeypatch.delenv("MENTORFLOW_TEACHER_VOICE_PROVIDER", raising=False)
    response = client.post(
        "/api/casual-chat/voice/synthesize",
        json={"text": "请解释这个边界条件", "reply_kind": "teaching.explain"},
    )
    assert response.status_code == 200
    assert response.headers["x-mentorflow-voice-engine"] == "fixture-engine"
    assert response.headers["x-mentorflow-voice-release"] == f"{PACK_ID}@1.0.0"
    assert response.headers["x-mentorflow-voice-cache"] == "miss"


def test_gpt_sovits_prepare_loads_once_and_reloads_when_active_pack_changes(
    client, monkeypatch, tmp_path
):
    catalog_dir = tmp_path / "catalog"
    v1 = _build_archive(catalog_dir, "1.0.0")
    v2 = _build_archive(catalog_dir, "1.1.0")
    _configure(monkeypatch, tmp_path, [v1, v2])
    monkeypatch.setenv(
        "MENTORFLOW_VOICE_PACK_GPT_SOVITS_URL",
        "http://127.0.0.1:9880",
    )

    class FakeResponse:
        headers = {"content-type": "audio/wav"}
        content = b"RIFF" + (b"\x00" * 64)

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": "success"}

    class FakeClient:
        get_calls: list[tuple[str, dict]] = []
        post_calls: list[tuple[str, dict]] = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params):
            self.get_calls.append((url, params))
            return FakeResponse()

        async def post(self, url, json):
            self.post_calls.append((url, json))
            return FakeResponse()

    adapter = voice_pack.GptSovitsLoopbackAdapter()
    monkeypatch.setitem(
        voice_pack._RUNTIME_ADAPTERS,
        "mentorflow.gpt-sovits.v1",
        adapter,
    )
    monkeypatch.setitem(
        voice_pack._RUNTIME_ADAPTER_LOCKS,
        "mentorflow.gpt-sovits.v1",
        __import__("asyncio").Lock(),
    )
    monkeypatch.setattr(voice_pack.httpx, "AsyncClient", FakeClient)

    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"}).status_code == 200
    for text in ("第一次", "第二次"):
        response = client.post("/api/casual-chat/voice/synthesize", json={"text": text})
        assert response.status_code == 200

    assert [call[0].rsplit("/", 1)[-1] for call in FakeClient.get_calls] == [
        "set_gpt_weights",
        "set_sovits_weights",
    ]
    assert len(FakeClient.post_calls) == 2
    for _url, params in FakeClient.get_calls:
        weight = Path(params["weights_path"])
        assert weight.is_absolute()
        assert weight.is_relative_to(tmp_path / "installed-packs" / PACK_ID / "1.0.0")

    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.1.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.1.0"}).status_code == 200
    switched = client.post("/api/casual-chat/voice/synthesize", json={"text": "切换版本"})
    assert switched.status_code == 200
    assert len(FakeClient.get_calls) == 4
    assert len(FakeClient.post_calls) == 3
    assert Path(FakeClient.get_calls[-1][1]["weights_path"]).is_relative_to(
        tmp_path / "installed-packs" / PACK_ID / "1.1.0"
    )


def test_gpt_sovits_prepare_failure_never_calls_tts(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "MENTORFLOW_VOICE_PACK_GPT_SOVITS_URL",
        "http://127.0.0.1:9880",
    )

    class FakeResponse:
        def __init__(self, *, fail: bool = False):
            self.fail = fail

        def raise_for_status(self):
            if self.fail:
                request = voice_pack.httpx.Request("GET", "http://127.0.0.1:9880/set_sovits_weights")
                response = voice_pack.httpx.Response(400, request=request)
                raise voice_pack.httpx.HTTPStatusError("load failed", request=request, response=response)

        def json(self):
            return {"message": "success"}

    class FakeClient:
        get_count = 0
        post_count = 0

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params):
            type(self).get_count += 1
            return FakeResponse(fail=url.endswith("/set_sovits_weights"))

        async def post(self, url, json):
            type(self).post_count += 1
            raise AssertionError("TTS must not run after model prepare failed")

    adapter = voice_pack.GptSovitsLoopbackAdapter()
    monkeypatch.setitem(voice_pack._RUNTIME_ADAPTERS, "mentorflow.gpt-sovits.v1", adapter)
    monkeypatch.setitem(
        voice_pack._RUNTIME_ADAPTER_LOCKS,
        "mentorflow.gpt-sovits.v1",
        __import__("asyncio").Lock(),
    )
    monkeypatch.setattr(voice_pack.httpx, "AsyncClient", FakeClient)
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"}).status_code == 200

    response = client.post("/api/casual-chat/voice/synthesize", json={"text": "不得合成"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VOICE_PACK_RUNTIME_PREPARE_FAILED"
    assert FakeClient.get_count == 2
    assert FakeClient.post_count == 0
    assert adapter._prepared_identity is None


def test_active_state_semantic_damage_fails_closed(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"}).status_code == 200
    active_path = tmp_path / "installed-packs" / ".state" / "active.json"
    active_path.write_text('{"schema":"mentorflow.active-voice-pack.v1"}', encoding="utf-8")

    response = client.get("/api/teacher-voice/status")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "VOICE_PACK_ACTIVE_STATE_INVALID"


def test_active_manifest_hash_mismatch_blocks_status_and_synthesis(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"}).status_code == 200
    active_path = tmp_path / "installed-packs" / ".state" / "active.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active["manifest_sha256"] = "0" * 64
    active_path.write_text(json.dumps(active), encoding="utf-8")

    status = client.get("/api/teacher-voice/status")
    assert status.status_code == 409
    assert status.json()["error"]["code"] == "VOICE_PACK_ACTIVE_MANIFEST_MISMATCH"
    synth = client.post("/api/casual-chat/voice/synthesize", json={"text": "禁止错误声线"})
    assert synth.status_code == 409
    assert synth.json()["error"]["code"] == "VOICE_PACK_ACTIVE_MANIFEST_MISMATCH"


def test_activation_job_write_failure_restores_previous_active_pack(client, monkeypatch, tmp_path):
    catalog_dir = tmp_path / "catalog"
    v1 = _build_archive(catalog_dir, "1.0.0")
    v2 = _build_archive(catalog_dir, "1.1.0")
    _configure(monkeypatch, tmp_path, [v1, v2])
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.1.0"}).status_code == 200
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.0.0"}).status_code == 200
    original_update = voice_pack._update_job

    def fail_active_update(root, job, status, *, error=None):
        if status == "active" and job["version"] == "1.1.0":
            raise OSError("simulated active-job persistence failure")
        return original_update(root, job, status, error=error)

    monkeypatch.setattr(voice_pack, "_update_job", fail_active_update)
    response = client.post(f"/api/teacher-voice/packs/{PACK_ID}/activate", json={"version": "1.1.0"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "VOICE_PACK_ACTIVATE_FAILED"
    status = client.get("/api/teacher-voice/status").json()
    assert status["active_pack"]["version"] == "1.0.0"
    failed_job = client.get(
        f"/api/teacher-voice/jobs/{response.json()['error']['details']['job_id']}"
    ).json()
    assert failed_job["status"] == "failed"


def test_delete_cleanup_failure_is_recorded_after_logical_commit(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    original_rmtree = voice_pack.shutil.rmtree

    def fail_quarantine_delete(path, *args, **kwargs):
        if Path(path).parent.name == ".trash":
            raise OSError("simulated delete failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(voice_pack.shutil, "rmtree", fail_quarantine_delete)
    response = client.delete(f"/api/teacher-voice/packs/{PACK_ID}/versions/1.0.0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_installed"
    assert payload["cleanup"]["status"] == "failed"
    assert not (tmp_path / "installed-packs" / PACK_ID / "1.0.0").exists()
    job = client.get(f"/api/teacher-voice/jobs/{payload['job']['job_id']}").json()
    assert job["status"] == "not_installed"
    assert job["cleanup"]["status"] == "failed"
    assert job["history"][-1]["event"] == "cleanup_failed"
    assert job["history"][0]["status"] == "installed"


def test_delete_cleanup_record_failure_keeps_durable_pending_quarantine(
    client, monkeypatch, tmp_path
):
    _one_pack(monkeypatch, tmp_path)
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    original_rmtree = voice_pack.shutil.rmtree
    original_write = voice_pack._write_json_atomic

    def fail_quarantine_delete(path, *args, **kwargs):
        if Path(path).parent.name == ".trash":
            raise OSError("simulated delete failure")
        return original_rmtree(path, *args, **kwargs)

    def fail_cleanup_failure_record(path, payload, *, boundary=None):
        cleanup = payload.get("cleanup") if isinstance(payload, dict) else None
        if isinstance(cleanup, dict) and cleanup.get("status") == "failed":
            raise OSError("simulated cleanup-state persistence failure")
        return original_write(path, payload, boundary=boundary)

    monkeypatch.setattr(voice_pack.shutil, "rmtree", fail_quarantine_delete)
    monkeypatch.setattr(voice_pack, "_write_json_atomic", fail_cleanup_failure_record)
    response = client.delete(f"/api/teacher-voice/packs/{PACK_ID}/versions/1.0.0")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "VOICE_PACK_DELETE_CLEANUP_STATE_FAILED"
    assert error["details"]["quarantine"].startswith(".trash/")
    assert "installed-packs" not in error["details"]["quarantine"]

    durable_job = client.get(
        f"/api/teacher-voice/jobs/{error['details']['job_id']}"
    ).json()
    assert durable_job["status"] == "not_installed"
    assert durable_job["cleanup"]["status"] == "pending"
    quarantine = tmp_path / "installed-packs" / Path(
        durable_job["cleanup"]["quarantine"]
    )
    assert quarantine.is_dir()


def test_delete_state_commit_failure_never_reports_success(client, monkeypatch, tmp_path):
    _one_pack(monkeypatch, tmp_path)
    assert client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"}).status_code == 200
    original_update = voice_pack._update_job

    def fail_deleted_state(root, job, status, *, error=None):
        if status == "not_installed":
            raise OSError("simulated delete-state persistence failure")
        return original_update(root, job, status, error=error)

    monkeypatch.setattr(voice_pack, "_update_job", fail_deleted_state)
    response = client.delete(f"/api/teacher-voice/packs/{PACK_ID}/versions/1.0.0")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "VOICE_PACK_DELETE_FAILED"
    assert (tmp_path / "installed-packs" / PACK_ID / "1.0.0").is_dir()
    job = client.get(
        f"/api/teacher-voice/jobs/{response.json()['error']['details']['job_id']}"
    ).json()
    assert job["status"] == "failed"


@pytest.mark.parametrize(
    "unsafe_component",
    [".staging", PACK_ID],
)
def test_install_rejects_prepositioned_reparse_parent(
    client, monkeypatch, tmp_path, unsafe_component
):
    _one_pack(monkeypatch, tmp_path)
    root = tmp_path / "installed-packs"
    unsafe = root / unsafe_component
    unsafe.mkdir(parents=True, exist_ok=True)
    original_check = voice_pack._is_reparse_point

    def fake_reparse(path):
        return Path(path) == unsafe or original_check(Path(path))

    monkeypatch.setattr(voice_pack, "_is_reparse_point", fake_reparse)
    response = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VOICE_PACK_REPARSE_FORBIDDEN"
    assert not (root / PACK_ID / "1.0.0").exists()


def test_arbitrary_windows_reparse_attribute_is_rejected(monkeypatch, tmp_path):
    tagged = tmp_path / "cloud-placeholder"
    tagged.mkdir()
    original_lstat = voice_pack.os.lstat

    class TaggedMetadata:
        def __init__(self, original):
            self._original = original
            self.st_file_attributes = voice_pack.FILE_ATTRIBUTE_REPARSE_POINT

        def __getattr__(self, name):
            return getattr(self._original, name)

    def fake_lstat(path):
        metadata = original_lstat(path)
        if Path(path) == tagged:
            return TaggedMetadata(metadata)
        return metadata

    monkeypatch.setattr(voice_pack.os, "lstat", fake_lstat)
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack._assert_no_reparse(tagged, tmp_path)
    assert raised.value.code == "VOICE_PACK_REPARSE_FORBIDDEN"


def test_reparse_metadata_check_failure_is_fail_closed(monkeypatch, tmp_path):
    target = tmp_path / "unreadable"
    target.mkdir()
    original_lstat = voice_pack.os.lstat

    def fail_target_lstat(path):
        if Path(path) == target:
            raise PermissionError("simulated metadata denial")
        return original_lstat(path)

    monkeypatch.setattr(voice_pack.os, "lstat", fail_target_lstat)
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        voice_pack._assert_no_reparse(target, tmp_path)
    assert raised.value.code == "VOICE_PACK_REPARSE_CHECK_FAILED"


@pytest.mark.parametrize(
    "redirected_url",
    [
        "http://trusted.example/voice.zip",
        "https://untrusted.example/voice.zip",
        "file:///C:/voice.zip",
    ],
)
def test_https_redirect_is_revalidated_against_scheme_and_allowlist(
    client, monkeypatch, tmp_path, redirected_url
):
    catalog_dir = tmp_path / "catalog"
    entry = _build_archive(catalog_dir, "1.0.0")
    archive_path = catalog_dir / entry["source"]["archive_path"]
    archive_bytes = archive_path.read_bytes()
    entry["source"] = {
        "kind": "https",
        "url": "https://trusted.example/voice.zip",
    }
    _configure(
        monkeypatch,
        tmp_path,
        [entry],
        https_allowed_hosts=["trusted.example"],
    )

    visited = []

    class FakeRedirectResponse:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeOpener:
        def __init__(self, handler):
            self.handler = handler

        def open(self, request, timeout):
            assert timeout == 60
            visited.append(request.full_url)
            redirected = self.handler.redirect_request(
                request,
                FakeRedirectResponse(),
                302,
                "Found",
                {},
                redirected_url,
            )
            # This represents the network access urllib would perform only
            # after redirect_request has approved the target.
            visited.append(redirected.full_url)
            return io.BytesIO(archive_bytes)

    monkeypatch.setattr(
        voice_pack.urllib.request,
        "build_opener",
        lambda handler: FakeOpener(handler),
    )
    response = client.post(f"/api/teacher-voice/packs/{PACK_ID}/install", json={"version": "1.0.0"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VOICE_PACK_SOURCE_FORBIDDEN"
    assert visited == ["https://trusted.example/voice.zip"]
    assert not (tmp_path / "installed-packs" / PACK_ID / "1.0.0").exists()


def test_https_redirect_handler_allows_relative_trusted_redirect(
    client, monkeypatch, tmp_path
):
    catalog_dir = tmp_path / "catalog"
    entry = _build_archive(catalog_dir, "1.0.0")
    archive_bytes = (catalog_dir / entry["source"]["archive_path"]).read_bytes()
    entry["source"] = {
        "kind": "https",
        "url": "https://trusted.example/voice.zip",
    }
    _configure(
        monkeypatch,
        tmp_path,
        [entry],
        https_allowed_hosts=["trusted.example"],
    )
    visited = []

    class FakeOpener:
        def __init__(self, handler):
            self.handler = handler

        def open(self, request, timeout):
            visited.append(request.full_url)
            redirected = self.handler.redirect_request(
                request,
                io.BytesIO(),
                302,
                "Found",
                {},
                "/releases/voice.zip",
            )
            visited.append(redirected.full_url)
            return io.BytesIO(archive_bytes)

    monkeypatch.setattr(
        voice_pack.urllib.request,
        "build_opener",
        lambda handler: FakeOpener(handler),
    )
    response = client.post(
        f"/api/teacher-voice/packs/{PACK_ID}/install",
        json={"version": "1.0.0"},
    )
    assert response.status_code == 200
    assert visited == [
        "https://trusted.example/voice.zip",
        "https://trusted.example/releases/voice.zip",
    ]


def test_https_redirect_handler_enforces_hop_limit():
    handler = voice_pack._PrevalidatedHTTPSRedirectHandler({"trusted.example"})
    request = voice_pack.urllib.request.Request(
        "https://trusted.example/start.zip"
    )
    for index in range(voice_pack.MAX_HTTPS_REDIRECTS):
        request = handler.redirect_request(
            request,
            io.BytesIO(),
            302,
            "Found",
            {},
            f"/hop-{index}.zip",
        )
    with pytest.raises(voice_pack.TeacherVoicePackError) as raised:
        handler.redirect_request(
            request,
            io.BytesIO(),
            302,
            "Found",
            {},
            "/blocked-hop.zip",
        )
    assert raised.value.code == "VOICE_PACK_REDIRECT_LIMIT"

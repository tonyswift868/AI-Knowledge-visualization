from __future__ import annotations

import io
import hashlib
import json
import wave
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import release_trust_policy
from scripts import run_qwen_base_clone_real_e2e as harness


def _wav(sample: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(int(sample).to_bytes(2, "little", signed=True) * 240)
    return output.getvalue()


class FakeInspector:
    def __init__(self, port: int):
        self.port = port
        self.listeners: dict[int, tuple[int, ...]] = {
            8000: (47768,),
            8892: (),
            9880: (),
            port: (),
        }
        self.checked_pids: list[int] = []

    def snapshot(self, ports: tuple[int, ...]):
        return {port: self.listeners.get(port, ()) for port in ports}

    def assert_process_loopback_only(self, pid: int) -> None:
        self.checked_pids.append(pid)


class FakeBackend:
    def __init__(self, inspector: FakeInspector, *, fail_on_synthesis: int = 0):
        self.inspector = inspector
        self.fail_on_synthesis = fail_on_synthesis
        self.events: list[str] = []
        self.generation = 0
        self.pid: int | None = None
        self.synthesis_in_generation = 0
        self.total_synthesis = 0
        self.config: harness.E2EConfig | None = None
        self.release_scope: dict | None = None

    @staticmethod
    def _pin(path: Path, content: bytes) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "path": str(path.absolute()),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def configure(self, config: harness.E2EConfig):
        self.events.append("configure")
        self.config = config
        self.release_scope = {
            "engine": {
                "package_id": config.package_id,
                "version": config.version,
                "manifest": self._pin(config.engine_pack_root / "engine-manifest.json", b"fixture-engine-manifest\n"),
                "catalog": self._pin(config.engine_catalog, b"fixture-engine-catalog\n"),
                "archive": self._pin(config.engine_archive, b"fixture-engine-archive\n"),
            },
            "voice": {
                "pack_id": config.voice_pack_id,
                "version": config.voice_pack_version,
                "manifest": self._pin(config.voice_pack_root / "active" / "voice-pack.json", b"fixture-voice-manifest\n"),
                "catalog": self._pin(config.voice_catalog, b"fixture-voice-catalog\n"),
                "archive": self._pin(config.voice_catalog.parent / "fixture-voice.zip", b"fixture-voice-archive\n"),
            },
        }
        return {
            "build": {"status": "fixture"},
            "verify": {"status": "fixture"},
            "install": {"status": "fixture"},
            "register": {"starts_model": False},
        }

    def prepare(self):
        self.events.append("prepare")
        if self.pid is None:
            self.generation += 1
            self.pid = 51000 + self.generation
            self.synthesis_in_generation = 0
            self.inspector.listeners[self.inspector.port] = (self.pid,)
        return {"prepared": True, "generation": f"g{self.generation}"}

    def synthesize(self, *, text: str, style: str, reply_kind: str):
        self.events.append("synthesize")
        self.total_synthesis += 1
        if self.fail_on_synthesis == self.total_synthesis:
            raise harness.HarnessError("FIXTURE_SYNTHESIS_FAILED", "injected synthesis failure")
        self.synthesis_in_generation += 1
        assert self.config is not None and self.release_scope is not None
        audio = _wav(self.generation)
        audio_sha256 = hashlib.sha256(audio).hexdigest()
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        reference_path = self.config.voice_pack_root / "active" / f"{style}.wav"
        reference_content = b"fixture-reference"
        reference_pin = self._pin(reference_path, reference_content)
        transcript = "fixture reference transcript"
        transcript_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        reference = {
            "style": style,
            "relative_path": f"references/{style}.wav",
            "audio_path": str(reference_path.absolute()),
            "audio_bytes": reference_pin["bytes"],
            "audio_sha256": reference_pin["sha256"],
            "transcript": transcript,
            "transcript_sha256": transcript_sha256,
            "language": "zh",
        }
        binding_sha256 = "b" * 64
        request_sha256 = "c" * 64
        cache = "miss" if self.synthesis_in_generation == 1 else "hit"
        request = {
            "schema": "mentorflow.qwen-base-clone-synthesis.v1",
            "binding_sha256": binding_sha256,
            "engine_kind": "qwen3-tts-0.6b-base",
            "engine_catalog_id": self.config.package_id,
            "engine_version": self.config.version,
            "engine_manifest_sha256": self.release_scope["engine"]["manifest"]["sha256"],
            "pack_id": self.config.voice_pack_id,
            "pack_version": self.config.voice_pack_version,
            "pack_manifest_sha256": self.release_scope["voice"]["manifest"]["sha256"],
            "text": text,
            "text_sha256": text_sha256,
            "style": style,
            "reference": reference,
            "reference_audio_sha256": reference["audio_sha256"],
            "reference_transcript_sha256": transcript_sha256,
            "reply_kind": reply_kind,
            "request_sha256": request_sha256,
        }
        runtime_result = {
            "schema": "mentorflow.qwen-base-clone-synthesis.v1",
            "media_type": "audio/wav",
            **{
                field: request[field]
                for field in (
                    "engine_kind", "engine_catalog_id", "engine_version", "engine_manifest_sha256",
                    "pack_id", "pack_version", "pack_manifest_sha256", "binding_sha256", "style",
                    "reference_audio_sha256", "reference_transcript_sha256", "reply_kind", "request_sha256",
                )
            },
            "request_text_sha256": text_sha256,
            "audio_sha256": audio_sha256,
            "audio_bytes": len(audio),
            "profile": style,
            "cache": cache,
        }
        return harness.AudioRun(
            audio=audio,
            media_type="audio/wav",
            profile=style,
            cache=cache,
            scope_receipt={
                "schema": harness.AUDIO_SCOPE_SCHEMA,
                "release": self.release_scope,
                "adapter": {
                    "schema": "mentorflow.qwen-base-clone-audio-scope.v1",
                    "binding": {
                        "schema": "mentorflow.qwen-base-clone-pack-binding.v1",
                        "pack": {
                            "pack_id": self.config.voice_pack_id,
                            "version": self.config.voice_pack_version,
                            "manifest_sha256": self.release_scope["voice"]["manifest"]["sha256"],
                        },
                        "engine": {
                            "kind": "qwen3-tts-0.6b-base",
                            "catalog_id": self.config.package_id,
                            "version": self.config.version,
                            "manifest_sha256": self.release_scope["engine"]["manifest"]["sha256"],
                        },
                        "references": [reference],
                        "references_sha256": "a" * 64,
                        "binding_sha256": binding_sha256,
                    },
                    "request": request,
                    "runtime_result": runtime_result,
                    "audio": {
                        "bytes": len(audio),
                        "sha256": audio_sha256,
                        "media_type": "audio/wav",
                        "profile": style,
                        "cache": cache,
                    },
                },
            },
        )

    def runtime_status(self):
        return {
            "owned": self.pid is not None,
            "owned_pid": self.pid,
            "generation": f"g{self.generation}" if self.pid is not None else None,
            "state": "ready" if self.pid is not None else "stopped",
            "origin": f"http://127.0.0.1:{self.inspector.port}",
        }

    def restart_owned(self):
        self.events.append("restart_owned")
        old_pid = self.pid
        old_generation = f"g{self.generation}"
        self.pid = None
        self.inspector.listeners[self.inspector.port] = ()
        return {
            "stopped": True,
            "reason": "e2e_restart",
            "owned_pid": old_pid,
            "generation": old_generation,
            "external_processes_terminated": False,
        }

    def cleanup_owned(self):
        self.events.append("cleanup_owned")
        self.pid = None
        self.inspector.listeners[self.inspector.port] = ()
        return {"owned_only": True, "stopped": [], "failures": []}


def _config(tmp_path: Path, *, port: int = 55123, test_trust: bool = True) -> harness.E2EConfig:
    package_id = "test-qwen-runtime" if test_trust else "mentorflow-qwen-runtime"
    version = "0.1.0-test" if test_trust else "0.1.0"
    voice_pack_id = "test-teacher-reference" if test_trust else "mentorflow-teacher-reference"
    voice_version = "0.1.0-test" if test_trust else "0.1.0"
    return harness.E2EConfig(
        engine_build_spec=tmp_path / "build-spec.json",
        engine_output_root=tmp_path / "engine-output",
        engine_pack_root=tmp_path / "engine-output" / "pack",
        engine_archive=tmp_path / "engine-output" / "archive.zip",
        engine_catalog=tmp_path / "engine-output" / "catalog.json",
        package_id=package_id,
        version=version,
        public_key=tmp_path / "leaf-public.pem",
        trust_receipt=tmp_path / "leaf-receipt.json",
        trust_policy=tmp_path / "policy.json",
        trust_policy_signature=tmp_path / "policy.sig",
        voice_pack_root=tmp_path / "voice-packs",
        voice_catalog=tmp_path / "voice-catalog.json",
        voice_pack_id=voice_pack_id,
        voice_pack_version=voice_version,
        cache_root=tmp_path / "cache",
        evidence_root=tmp_path / "evidence",
        port=port,
        text="这是一个不启动真实模型的 tiny fixture。",
        style="relaxed",
        reply_kind="fixture",
        test_trust_chain=test_trust,
        temporary_test_reference=test_trust,
    )


def test_fake_backend_cold_restart_cache_and_owned_cleanup(tmp_path: Path):
    config = _config(tmp_path)
    inspector = FakeInspector(config.port)
    backend = FakeBackend(inspector)

    report = harness.run_e2e(
        config,
        backend=backend,
        inspector=inspector,
        network_guard=nullcontext(),
    )

    assert report["status"] == "passed"
    assert report["audio"]["cold"]["cache"] == "miss"
    assert report["audio"]["restarted"]["cache"] == "miss"
    assert report["audio"]["repeat"]["cache"] == "hit"
    assert report["audio"]["restarted"]["sha256"] == report["audio"]["repeat"]["sha256"]
    assert report["audio"]["cold"]["runtime"]["owned_pid"] != report["audio"]["restarted"]["runtime"]["owned_pid"]
    assert backend.events == [
        "configure",
        "prepare",
        "synthesize",
        "restart_owned",
        "prepare",
        "synthesize",
        "synthesize",
        "cleanup_owned",
    ]
    assert inspector.listeners[8000] == (47768,)
    assert inspector.listeners[config.port] == ()
    assert inspector.checked_pids == [51001, 51002, 51002]
    assert (config.evidence_root / "cold.wav").is_file()
    assert (config.evidence_root / "restarted.wav").is_file()
    assert (config.evidence_root / "repeat.wav").is_file()
    assert (config.evidence_root / "cold.scope.json").is_file()
    assert (config.evidence_root / "restarted.scope.json").is_file()
    assert (config.evidence_root / "repeat.scope.json").is_file()
    persisted = json.loads((config.evidence_root / "e2e-report.json").read_text(encoding="utf-8"))
    assert persisted["cleanup_gate"]["status"] == "passed"


def test_failure_still_cleans_only_owned_runtime_and_preserves_ports(tmp_path: Path):
    config = _config(tmp_path)
    inspector = FakeInspector(config.port)
    backend = FakeBackend(inspector, fail_on_synthesis=1)

    with pytest.raises(harness.HarnessError) as caught:
        harness.run_e2e(
            config,
            backend=backend,
            inspector=inspector,
            network_guard=nullcontext(),
        )

    assert caught.value.code == "FIXTURE_SYNTHESIS_FAILED"
    assert backend.events[-1] == "cleanup_owned"
    assert inspector.listeners[config.port] == ()
    assert inspector.listeners[8000] == (47768,)
    report = json.loads((config.evidence_root / "e2e-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["cleanup"]["owned_only"] is True
    assert report["cleanup"]["failures"] == []


def test_occupied_or_protected_port_fails_without_stopping_anything(tmp_path: Path):
    config = _config(tmp_path)
    inspector = FakeInspector(config.port)
    inspector.listeners[config.port] = (60001,)
    backend = FakeBackend(inspector)
    with pytest.raises(harness.HarnessError) as caught:
        harness.run_e2e(
            config,
            backend=backend,
            inspector=inspector,
            network_guard=nullcontext(),
        )
    assert caught.value.code == "E2E_PORT_IN_USE"
    assert backend.events == []
    assert inspector.listeners[config.port] == (60001,)

    with pytest.raises(harness.HarnessError) as protected:
        _config(tmp_path, port=8892).validated()
    assert protected.value.code == "E2E_PORT_INVALID"


def test_test_reference_requires_explicit_test_trust(tmp_path: Path):
    config = _config(tmp_path, test_trust=False)
    invalid = harness.E2EConfig(**{**config.__dict__, "temporary_test_reference": True})
    with pytest.raises(harness.HarnessError) as caught:
        invalid.validated()
    assert caught.value.code == "E2E_TEST_BOUNDARY_INVALID"


def test_loopback_guard_rejects_non_loopback_without_connecting():
    with pytest.raises(harness.HarnessError) as caught:
        harness.LoopbackOnlyNetworkGuard._check(None, ("8.8.8.8", 443))
    assert caught.value.code == "E2E_OFFLINE_GUARD_BLOCKED"
    harness.LoopbackOnlyNetworkGuard._check(None, ("127.0.0.1", 55123))
    harness.LoopbackOnlyNetworkGuard._check(None, ("::1", 55123))


def test_production_trust_does_not_enable_internal_test_roots(tmp_path: Path, monkeypatch):
    config = _config(tmp_path, test_trust=False)
    seen = {}

    def reject(**kwargs):
        seen.update(kwargs)
        raise release_trust_policy.ReleaseTrustError(
            "TRUST_REGISTRY_PRODUCTION_ROOT_MISSING",
            "production root is not configured",
        )

    monkeypatch.setattr(release_trust_policy, "verify_release_trust_chain", reject)
    with pytest.raises(harness.HarnessError) as caught:
        harness._verify_manifest_trust(
            config,
            manifest_name="engine-manifest.json",
            manifest_value={"schema": "mentorflow.qwen-engine-manifest.v1"},
        )
    assert caught.value.code == "TRUST_REGISTRY_PRODUCTION_ROOT_MISSING"
    assert seen["_allow_test_roots"] is False


def test_real_backend_cleanup_removes_only_its_exact_owned_runtime(monkeypatch):
    from app import qwen_base_clone_runtime as runtime_bridge
    from app import teacher_voice_pack as voice_pack

    calls = []
    owned = SimpleNamespace(
        supervisor=SimpleNamespace(
            shutdown=lambda: {"stopped": True, "external_processes_terminated": False}
        )
    )
    foreign = SimpleNamespace(supervisor=SimpleNamespace())
    monkeypatch.setattr(runtime_bridge, "_PRODUCTION_RUNTIMES", [foreign, owned])
    monkeypatch.setattr(
        voice_pack,
        "unregister_qwen_base_clone_runtime",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    backend = harness.RealQwenE2EBackend()
    backend.runtime = owned
    backend.capability = object()

    result = backend.cleanup_owned()

    assert result["failures"] == []
    assert result["owned_only"] is True
    assert runtime_bridge._PRODUCTION_RUNTIMES == [foreign]
    assert calls == [{"capability": backend.capability, "runtime": owned}]
    assert backend.runtime is None


def test_real_backend_cleanup_failure_retains_owner_for_safe_retry(monkeypatch):
    from app import qwen_base_clone_runtime as runtime_bridge
    from app import teacher_voice_pack as voice_pack

    def fail_shutdown():
        raise RuntimeError("termination unconfirmed")

    owned = SimpleNamespace(supervisor=SimpleNamespace(shutdown=fail_shutdown))
    monkeypatch.setattr(runtime_bridge, "_PRODUCTION_RUNTIMES", [owned])
    monkeypatch.setattr(
        voice_pack,
        "unregister_qwen_base_clone_runtime",
        lambda **_kwargs: pytest.fail("unregister must not run after unconfirmed stop"),
    )
    backend = harness.RealQwenE2EBackend()
    backend.runtime = owned
    backend.capability = object()

    result = backend.cleanup_owned()

    assert result["failures"][0]["phase"] == "supervisor"
    assert runtime_bridge._PRODUCTION_RUNTIMES == [owned]
    assert backend.runtime is owned


def test_real_qwen_binding_dataclass_is_losslessly_json_serialized_without_service(tmp_path: Path):
    from app import teacher_voice_pack as voice_pack

    reference = voice_pack.QwenBaseCloneReference(
        style="relaxed",
        relative_path="references/relaxed.wav",
        audio_path=tmp_path / "references" / "relaxed.wav",
        audio_bytes=123,
        audio_sha256="1" * 64,
        transcript="真实 binding 的无服务契约测试。",
        transcript_sha256="2" * 64,
        language="zh",
    )
    binding = voice_pack.QwenBaseClonePackBinding(
        pack_id="test-voice-pack",
        version="1.0.0-test",
        manifest_sha256="3" * 64,
        engine=voice_pack.QwenBaseCloneEngineDependency(
            kind="qwen3-tts-0.6b-base",
            catalog_id="test-engine-pack",
            version="1.0.0-test",
            manifest_sha256="4" * 64,
        ),
        references=(reference,),
    )

    generic = harness._strict_json_mapping(
        {"schema": "fixture.real-binding.v1", "binding": binding},
        field="real_binding",
    )
    explicit = voice_pack.qwen_base_clone_binding_contract(binding)

    assert generic["binding"]["references"][0]["audio_path"] == str(reference.audio_path)
    assert generic["binding"]["references"][0]["transcript"] == reference.transcript
    assert explicit["binding_sha256"] == binding.binding_sha256
    assert explicit["references"][0]["audio_sha256"] == reference.audio_sha256
    json.dumps(generic, ensure_ascii=False, sort_keys=True)
    json.dumps(explicit, ensure_ascii=False, sort_keys=True)

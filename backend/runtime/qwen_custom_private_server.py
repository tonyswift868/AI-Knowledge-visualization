#!/usr/bin/env python3
"""Persistent lazy loopback child for the signed private custom voice model."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import socket
import sys
import threading
import urllib.request
import wave
from pathlib import Path


PREPARE_SCHEMA = "mentorflow.qwen-custom-private-prepare.v1"
SYNTHESIS_SCHEMA = "mentorflow.qwen-custom-private-synthesis.v1"
RUNTIME_ID = "mentorflow.qwen3-tts-custom-private-runtime.v1"
SERVICE_ID = "mentorflow.qwen-custom-private-service.v1"
BINDING_FIELDS = {"pack_id", "pack_version", "pack_manifest_sha256", "model_root", "model_sha256", "speaker", "speaker_slot", "scope"}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_base_runner():
    path = Path(__file__).with_name("qwen_base_clone_server.py").resolve(strict=True)
    spec = importlib.util.spec_from_file_location("mentorflow_private_base_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("signed base runner cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load_base_runner()


def install_overlay(runtime_root: Path) -> dict:
    BASE._enforce_numba_workqueue()
    site_packages = runtime_root / "runtime" / "python" / "Lib" / "site-packages"
    expected = BASE.build_qwen_12hz_overlay_contract(site_packages)
    contract_path = runtime_root / "metadata" / "source-overlays" / "qwen-12hz-only.json"
    raw = contract_path.read_bytes()
    if json.loads(raw.decode("utf-8")) != expected:
        raise RuntimeError("portable runtime overlay contract mismatch")
    sys.meta_path.insert(0, BASE._Qwen12HzOverlayFinder(site_packages, expected))
    return {"contract_sha256": hashlib.sha256(raw).hexdigest(), "sox_loaded": "sox" in sys.modules}


def block_outbound_network() -> None:
    def blocked(*_args, **_kwargs):
        raise OSError("outbound network disabled by private Qwen runtime")
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    urllib.request.urlopen = blocked
    urllib.request.OpenerDirector.open = blocked


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class CustomModelBackend:
    def __init__(self, runtime_root: Path, model_root: Path, model_sha256: str, device: str, minimum_driver: str) -> None:
        self.runtime_root = runtime_root
        self.model_root = model_root
        self.model_sha256 = model_sha256
        self.device = device
        self.minimum_driver = minimum_driver
        self.model = None
        self.model_load_count = 0
        self.lock = threading.RLock()

    def load(self):
        with self.lock:
            if self.model is not None:
                return self.model
            if sha(self.model_root / "model.safetensors") != self.model_sha256:
                raise RuntimeError("installed trained model digest mismatch")
            config = json.loads((self.model_root / "config.json").read_text(encoding="utf-8"))
            talker = config.get("talker_config", {})
            if config.get("tts_model_type") != "custom_voice" or talker.get("spk_id", {}).get("tsubaki_teacher") != 3000:
                raise RuntimeError("installed trained model config mismatch")
            if BASE._version_tuple(BASE._query_nvidia_driver()) < BASE._version_tuple(self.minimum_driver):
                raise RuntimeError("NVIDIA driver is below private runtime minimum")
            overlay = install_overlay(self.runtime_root)
            block_outbound_network()
            import torch
            from qwen_tts import Qwen3TTSModel
            from safetensors import safe_open

            free_bytes, total_bytes = torch.cuda.mem_get_info(0)
            free_mib, total_mib = free_bytes // 1024**2, total_bytes // 1024**2
            cap_mib = min(8704, int(free_mib) - 1024)
            if free_mib < 8192 or cap_mib < 7168:
                raise RuntimeError("private Qwen GPU entry gate blocked")
            torch.cuda.set_per_process_memory_fraction(cap_mib / total_mib, device=0)
            model = Qwen3TTSModel.from_pretrained(str(self.model_root), device_map={"": self.device}, dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True, trust_remote_code=False)
            if model.model.tts_model_type != "custom_voice" or "tsubaki_teacher" not in model.get_supported_speakers():
                raise RuntimeError("loaded trained model identity mismatch")
            with safe_open(str(self.model_root / "model.safetensors"), framework="pt", device="cpu") as source:
                expected = source.get_tensor("talker.model.codec_embedding.weight")[3000]
            actual = model.model.state_dict()["talker.model.codec_embedding.weight"][3000].detach().cpu()
            if not torch.equal(expected, actual):
                raise RuntimeError("loaded trained speaker slot mismatch")
            self.model = model
            self.model_load_count += 1
            self.load_receipt = {"loaded_model_sha256": self.model_sha256, "speaker_slot_exact": True, "allocator_cap_mib": cap_mib, "overlay": overlay}
            return model

    def synthesize(self, text: str) -> tuple[bytes, dict]:
        import numpy as np
        import soundfile as sf
        model = self.load()
        wavs, sample_rate = model.generate_custom_voice(text=text, language="Chinese", speaker="tsubaki_teacher", do_sample=True, top_k=50, top_p=0.9, temperature=0.8, repetition_penalty=1.05, max_new_tokens=1024)
        audio = np.asarray(wavs[0], dtype=np.float32)
        if audio.ndim != 1 or audio.size == 0 or not np.isfinite(audio).all():
            raise RuntimeError("private runtime produced invalid audio")
        stream = io.BytesIO()
        sf.write(stream, audio, int(sample_rate), subtype="PCM_16", format="WAV")
        raw = stream.getvalue()
        with wave.open(io.BytesIO(raw), "rb") as reader:
            if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() != 24000:
                raise RuntimeError("private runtime WAV contract mismatch")
        return raw, {**self.load_receipt, "model_load_count": self.model_load_count, "sample_rate": int(sample_rate)}


class CustomRuntimeState:
    def __init__(self, backend: CustomModelBackend, expected_model_root: Path, expected_model_sha256: str) -> None:
        self.backend = backend
        self.expected_model_root = expected_model_root
        self.expected_model_sha256 = expected_model_sha256
        self.binding = None
        self.lock = threading.RLock()

    def validate_binding(self, value):
        if not isinstance(value, dict) or set(value) != BINDING_FIELDS:
            raise BASE.ProtocolError("BINDING_INVALID", "private binding fields invalid")
        if value["scope"] != "private_local_only_nonexportable" or value["speaker"] != "tsubaki_teacher" or value["speaker_slot"] != 3000:
            raise BASE.ProtocolError("BINDING_INVALID", "private binding identity invalid")
        if Path(value["model_root"]).resolve(strict=True) != self.expected_model_root or value["model_sha256"] != self.expected_model_sha256:
            raise BASE.ProtocolError("BINDING_INVALID", "private installed model binding mismatch")
        return dict(value)

    def prepare(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"schema", "binding"} or payload["schema"] != PREPARE_SCHEMA:
            raise BASE.ProtocolError("SCHEMA_INVALID", "private prepare schema invalid")
        binding = self.validate_binding(payload["binding"])
        with self.lock:
            self.binding = binding
        return {"schema": PREPARE_SCHEMA, "ready": True, "model_loaded": self.backend.model is not None, "binding_sha256": hashlib.sha256(canonical(binding)).hexdigest(), "model_sha256": binding["model_sha256"]}

    def synthesize(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"schema", "binding", "text", "style", "reply_kind"} or payload["schema"] != SYNTHESIS_SCHEMA:
            raise BASE.ProtocolError("SCHEMA_INVALID", "private synthesis schema invalid")
        binding = self.validate_binding(payload["binding"])
        text = str(payload["text"] or "").strip()
        if not text or len(text) > 800 or payload["style"] not in {"lively", "relaxed"} or len(str(payload["reply_kind"])) > 128:
            raise BASE.ProtocolError("REQUEST_INVALID", "private synthesis request invalid")
        with self.lock:
            if self.binding != binding:
                raise BASE.ProtocolError("RUNTIME_NOT_PREPARED", "private binding is not prepared", status=409)
            audio, load = self.backend.synthesize(text)
        receipt = {"pack_id": binding["pack_id"], "pack_version": binding["pack_version"], "pack_manifest_sha256": binding["pack_manifest_sha256"], "loaded_model_sha256": load["loaded_model_sha256"], "speaker_slot_exact": load["speaker_slot_exact"], "model_load_count": load["model_load_count"], "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "style": payload["style"], "audio_sha256": hashlib.sha256(audio).hexdigest(), "audio_bytes": len(audio), "sample_rate": load["sample_rate"], "network_used": False, "service_scope": "owned_loopback_internal", "port_8000_touched": False}
        return {"schema": SYNTHESIS_SCHEMA, "receipt": receipt, "audio_base64": base64.b64encode(audio).decode("ascii")}


def main() -> int:
    instance_key = str(os.environ.pop(BASE.INSTANCE_SECRET_ENV, "") or "")
    runtime_root = Path(os.environ["MENTORFLOW_VOICE_RUNTIME_ROOT"]).resolve(strict=True)
    if runtime_root != Path(__file__).resolve().parents[2] or os.environ.get("MENTORFLOW_VOICE_HOST") != "127.0.0.1":
        raise RuntimeError("private runtime root/host mismatch")
    port = int(os.environ["MENTORFLOW_VOICE_PORT"])
    cache_root = Path(os.environ["MENTORFLOW_VOICE_CACHE_ROOT"]).resolve(strict=False)
    model_root = Path(os.environ["MENTORFLOW_PRIVATE_MODEL_ROOT"]).resolve(strict=True)
    model_sha256 = os.environ["MENTORFLOW_PRIVATE_MODEL_SHA256"]
    backend = CustomModelBackend(runtime_root, model_root, model_sha256, os.environ["MENTORFLOW_VOICE_DEVICE"], os.environ["MENTORFLOW_VOICE_MIN_NVIDIA_DRIVER"])
    server = BASE._ThreadedLoopbackServer(("127.0.0.1", port), BASE.RuntimeRequestHandler, max_workers=4, request_queue_size=8, header_timeout_seconds=5.0, body_timeout_seconds=10.0)
    server.runtime_state = CustomRuntimeState(backend, model_root, model_sha256)
    server.authenticator = BASE.RequestAuthenticator(instance_key)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

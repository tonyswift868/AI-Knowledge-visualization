"""Read-only MF voice manifests and centralized, environment-overridable paths.

MF_TTS_ENGINE_ROOT / ENGINE_PYTHON / CATALOG / PACK_ROOTS (os.pathsep separated),
MF_TTS_VOICE_ID / VOICE_VERSION / STYLE / DATA_ROOT / DEVICE / CPU_THREADS.
MF_TTS_VOICE_MANIFEST selects an explicit, read-only voice-pack.json.
MF_TTS_BERT_ROOT / HUBERT_ROOT / G2PW_ROOT / SV_WEIGHTS select existing,
read-only common assets when they were moved out of the engine directory.
MF_TTS_BASE_ROOT overrides the shared base model directory.
The catalog chooses the default version; unpromoted variants require explicit
selection. No archives are unpacked, models downloaded, or source files changed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys



def _source_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "MentorFlow-Source"


# These optional overrides are intentionally empty.  Source checkouts discover
# components under their per-user data root or use explicit MF_TTS_* settings;
# they must never fall back to a developer machine's drive letter.
ENGINE_ROOT = None
BASE_MODEL_ROOT = None
CATALOG = None
PACK_ROOTS = ()
DEFAULT_DATA_ROOT = None
DEFAULT_VOICE = "phrolova-b-gpt-sovits"
# User-selected teacher voice (2026-09-27 audition; also chosen on 2026-08-17).
DEFAULT_VOICE_VERSION = "1.0.4"
VOICE_BACKUP = None

# Metadata only: model_manager.download requires prior user consent.
# The runtime never downloads. URLs are pinned; hashes were checked against the
# official LFS/Git objects and the downloaded bytes on 2026-09-27.
BASE_MODEL_DOWNLOADS = ({'path': 'chinese-hubert-base/config.json',
  'url': 'https://huggingface.co/lj1995/GPT-SoVITS/resolve/336b2ec4e8d4ac74740798dd40af44e74659ecaf/chinese-hubert-base/config.json',
  'size': 1449,
  'sha256': 'c3e5060a1277e0f078cc6be9da4528a605dba6ece93018981fe2c820e5c7b103'},
 {'path': 'chinese-hubert-base/preprocessor_config.json',
  'url': 'https://huggingface.co/lj1995/GPT-SoVITS/resolve/336b2ec4e8d4ac74740798dd40af44e74659ecaf/chinese-hubert-base/preprocessor_config.json',
  'size': 212,
  'sha256': 'dcd684124d06722947939d41ea6ae58dbf10968c60a11a29f23ddc602c64a29b'},
 {'path': 'chinese-hubert-base/pytorch_model.bin',
  'url': 'https://huggingface.co/lj1995/GPT-SoVITS/resolve/336b2ec4e8d4ac74740798dd40af44e74659ecaf/chinese-hubert-base/pytorch_model.bin',
  'size': 188811417,
  'sha256': '24164f129c66499d1346e2aa55f183250c223161ec2770c0da3d3b08cf432d3c'},
 {'path': 'chinese-roberta-wwm-ext-large/config.json',
  'url': 'https://huggingface.co/lj1995/GPT-SoVITS/resolve/336b2ec4e8d4ac74740798dd40af44e74659ecaf/chinese-roberta-wwm-ext-large/config.json',
  'size': 963,
  'sha256': '3d57de2fd7e80d0e5c8ff194f0bbb6baa10df7e43fc262a0cc71298a78b0a3e5'},
 {'path': 'chinese-roberta-wwm-ext-large/pytorch_model.bin',
  'url': 'https://huggingface.co/lj1995/GPT-SoVITS/resolve/336b2ec4e8d4ac74740798dd40af44e74659ecaf/chinese-roberta-wwm-ext-large/pytorch_model.bin',
  'size': 651225145,
  'sha256': 'e53a693acc59ace251d143d068096ae0d7b79e4b1b503fa84c9dcf576448c1d8'},
 {'path': 'chinese-roberta-wwm-ext-large/tokenizer.json',
  'url': 'https://huggingface.co/lj1995/GPT-SoVITS/resolve/336b2ec4e8d4ac74740798dd40af44e74659ecaf/chinese-roberta-wwm-ext-large/tokenizer.json',
  'size': 268962,
  'sha256': '173796956820ea27bd14f76bf28162607ff4254807e2948253eb5b46f5bb643b'},
 {'path': 'sv/pretrained_eres2netv2w24s4ep4.ckpt',
  'url': 'https://huggingface.co/lj1995/GPT-SoVITS/resolve/336b2ec4e8d4ac74740798dd40af44e74659ecaf/sv/pretrained_eres2netv2w24s4ep4.ckpt',
  'size': 107528697,
  'sha256': '4f5a0bf73c61eb41b174e1bb54e7ee3c83233892be8e0af1f187024e8e581a35'},
 {'path': 'G2PWModel.zip',
  'url': 'https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/0c47645e02a7bc3688d7b263b0042c81e3cd82cd/G2PWModel.zip',
  'size': 588856634,
  'sha256': '46292be0374a49308069233cd5c147ae4c41806558e4781a2467a31a4d8099da',
  'archive': 'zip',
  'extract_directory': 'G2PWModel'})


class TtsError(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class TtsCancelled(TtsError):
    def __init__(self):
        super().__init__("CANCELLED", "朗读已停止。")


def _read_json(path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise TtsError("VOICE_MANIFEST", "声音描述文件过大。")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _asset(root, name):
    relative = PurePosixPath(str(name).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or ":" in str(relative):
        raise TtsError("VOICE_MANIFEST", "声音资源路径无效。")
    result = (root / str(relative)).resolve()
    if not result.is_relative_to(root.resolve()) or not result.is_file():
        raise TtsError("VOICE_ASSET_MISSING", "声音包的权重或参考音频缺失。")
    return result


@dataclass(frozen=True)
class VoicePack:
    pack_id: str
    version: str
    manifest: Path
    gpt_weights: Path
    sovits_weights: Path
    reference_audio: Path
    reference_text: str
    language: str
    style: str
    speed: float = 1.0

    @classmethod
    def load(cls, manifest, style=None):
        manifest = Path(manifest)
        data = _read_json(manifest)
        if (data.get("schema") != "mentorflow.voice-pack.v1" or
                not re.fullmatch(r"phrolova-[a-z0-9-]+-gpt-sovits", data.get("pack_id", "")) or
                data.get("runtime", {}).get("adapter") != "mentorflow.gpt-sovits.v1"):
            raise TtsError("VOICE_MANIFEST", "需要 Phrolova GPT-SoVITS 声音描述。")
        styles = data.get("styles", {})
        selected = style or next((key for key in ("daily", "relaxed", "lively") if key in styles), None)
        if selected not in styles:
            raise TtsError("VOICE_STYLE", "声音包没有所选朗读风格。")
        profile = styles[selected]
        entries = data.get("files", [])
        paths = {entry["role"]: entry["path"] for entry in entries}
        if not all(role in paths for role in ("gpt_weight", "sovits_weight")):
            raise TtsError("VOICE_MANIFEST", "声音描述缺少 GPT 或 SoVITS 权重。")
        text = str(profile.get("transcript", "")).strip()
        if not text or profile.get("language") != "zh":
            raise TtsError("VOICE_MANIFEST", "需要中文参考文本和参考音频。")
        reference = str(profile.get("reference_audio", ""))
        if reference not in {item["path"] for item in entries if "reference_audio" in item["role"]}:
            raise TtsError("VOICE_MANIFEST", "参考音频未在声音描述中登记。")
        speed = float(data.get("runtime", {}).get("default_speed_factor", 1.0))
        if not .5 <= speed <= 2:
            raise TtsError("VOICE_MANIFEST", "声音包语速无效。")
        return cls(data["pack_id"], data["version"], manifest.resolve(),
                   _asset(manifest.parent, paths["gpt_weight"]), _asset(manifest.parent, paths["sovits_weight"]),
                   _asset(manifest.parent, reference), text, "zh", selected, speed)


@dataclass(frozen=True)
class TtsConfig:
    engine_root: Path | None = ENGINE_ROOT
    engine_python: Path | None = None
    catalog: Path | None = CATALOG
    pack_roots: tuple[Path, ...] = PACK_ROOTS
    voice_id: str = DEFAULT_VOICE
    voice_version: str = DEFAULT_VOICE_VERSION
    style: str = ""
    data_root: Path | None = None
    device: str = "cuda"
    cpu_threads: int = 4
    startup_timeout: float = 180.0
    synthesis_timeout: float = 90.0
    shutdown_timeout: float = 3.0
    base_model_root: Path | None = None
    bert_root: Path | None = None
    hubert_root: Path | None = None
    g2pw_root: Path | None = None
    sv_weights: Path | None = None
    voice_manifest: Path | None = None

    @classmethod
    def from_env(cls, data_root=None):
        from ..storage import storage_root
        data = Path(data_root if data_root is not None else os.environ.get("MF_TTS_DATA_ROOT", str(_source_data_root())))
        components = storage_root(data, "tts") / "tts/components-v1"
        installed = (components / "installed.json").is_file()
        packaged = bool(getattr(sys, "frozen", False))
        defaults = components if installed or packaged else None
        root_value = os.environ.get("MF_TTS_ENGINE_ROOT")
        root = Path(root_value) if root_value else (defaults / "engine" if defaults else None)
        manifest = os.environ.get("MF_TTS_VOICE_MANIFEST")
        if defaults and not manifest:
            manifest = str(defaults / "voice/voice-pack.json")
        if (not defaults and not manifest and "MF_TTS_PACK_ROOTS" not in os.environ
                and os.environ.get("MF_TTS_VOICE_ID", DEFAULT_VOICE) == DEFAULT_VOICE
                and os.environ.get("MF_TTS_VOICE_VERSION", DEFAULT_VOICE_VERSION) == DEFAULT_VOICE_VERSION
                and VOICE_BACKUP is not None and VOICE_BACKUP.is_file()):
            manifest = str(VOICE_BACKUP)
        engine_python_value = os.environ.get("MF_TTS_ENGINE_PYTHON")
        engine_python = (Path(engine_python_value) if engine_python_value else
                         (root / "runtime/python.exe" if root else None))
        catalog_value = os.environ.get("MF_TTS_CATALOG")
        catalog = (Path(catalog_value) if catalog_value else
                   (defaults / "voice/catalog.json" if defaults else None))
        pack_value = os.environ.get("MF_TTS_PACK_ROOTS")
        pack_roots = tuple(Path(p) for p in (pack_value.split(os.pathsep) if pack_value else
                                              ([str(defaults / "voice")] if defaults else ())) if p)
        base_value = os.environ.get("MF_TTS_BASE_ROOT")
        base_model_root = Path(base_value) if base_value else (defaults / "base" if defaults else None)
        return cls(engine_root=root,
            engine_python=engine_python,
            catalog=catalog,
            pack_roots=pack_roots,
            voice_id=os.environ.get("MF_TTS_VOICE_ID", DEFAULT_VOICE),
            voice_version=os.environ.get("MF_TTS_VOICE_VERSION", DEFAULT_VOICE_VERSION), style=os.environ.get("MF_TTS_STYLE", ""),
            data_root=data,
            device=os.environ.get("MF_TTS_DEVICE", "cuda"), cpu_threads=int(os.environ.get("MF_TTS_CPU_THREADS", "4")),
            base_model_root=base_model_root,
            voice_manifest=Path(manifest) if manifest else None,
            **{key: Path(os.environ[env]) if env in os.environ else None for key, env in (
                ("bert_root", "MF_TTS_BERT_ROOT"), ("hubert_root", "MF_TTS_HUBERT_ROOT"),
                ("g2pw_root", "MF_TTS_G2PW_ROOT"), ("sv_weights", "MF_TTS_SV_WEIGHTS"))})

    def base_assets(self):
        root = self.base_model_root
        if root is None:
            from .model_manager import ModelManager
            local = ModelManager(self.data_root)
            root = local.path if local.status()["installed"] or getattr(sys, "frozen", False) else BASE_MODEL_ROOT
            if root is None:
                root = local.path
        return {"bert_root": self.bert_root or root / "chinese-roberta-wwm-ext-large",
                "hubert_root": self.hubert_root or root / "chinese-hubert-base",
                "g2pw_root": self.g2pw_root or root / "G2PWModel",
                "sv_weights": self.sv_weights or root / "sv/pretrained_eres2netv2w24s4ep4.ckpt"}

    def voices(self):
        found = {}
        for root in self.pack_roots:
            for manifest in sorted(root.glob("phrolova-*-gpt-sovits/*/voice-pack.json")):
                voice = VoicePack.load(manifest, self.style or None)
                found.setdefault((voice.pack_id, voice.version), voice)
        return tuple(found.values())

    def voice(self):
        version = self.voice_version
        if not version:
            catalog = _read_json(self.catalog)
            if catalog.get("schema") != "mentorflow.voice-pack-catalog.v1":
                raise TtsError("VOICE_CATALOG", "声音目录格式无效。")
            selected = [p for p in catalog.get("packs", []) if p.get("pack_id") == self.voice_id and p.get("default")]
            if len(selected) != 1:
                raise TtsError("VOICE_SELECTION", "此候选声音需要明确选择版本。")
            version = selected[0]["version"]
        manifests = ([self.voice_manifest] if self.voice_manifest else
                     [root / self.voice_id / version / "voice-pack.json" for root in self.pack_roots])
        for manifest in manifests:
            if manifest.is_file():
                voice = VoicePack.load(manifest, self.style or None)
                if (voice.pack_id, voice.version) != (self.voice_id, version):
                    raise TtsError("VOICE_MANIFEST", "声音描述与所选版本不匹配。")
                return voice
        raise TtsError("VOICE_NOT_INSTALLED", "所选声音包未安装；不会自动下载。")

    def validate(self):
        if not re.fullmatch(r"phrolova-[a-z0-9-]+-gpt-sovits", self.voice_id) or not re.fullmatch(r"[A-Za-z0-9._-]*", self.voice_version):
            raise TtsError("VOICE_SELECTION", "声音标识无效。")
        if self.device not in {"cuda", "cpu"} or not 1 <= self.cpu_threads <= 32:
            raise TtsError("TTS_CONFIG", "推理设备或线程数无效。")
        if (self.engine_python is None or self.engine_root is None or
                not self.engine_python.is_file() or
                not (self.engine_root / "GPT_SoVITS/TTS_infer_pack/TTS.py").is_file()):
            raise TtsError("ENGINE_NOT_INSTALLED", "GPT-SoVITS 引擎或其自带 Python 不存在。")
        destination = self.data_root.resolve()
        protected = tuple(path for path in (
            self.engine_root,
            self.catalog.parent if self.catalog else None,
            *self.pack_roots,
            *((self.voice_manifest.parent,) if self.voice_manifest else ()),
        ) if path is not None)
        # Downloaded components live under models; runtime output is exclusively
        # data_root/tts. Keep that output disjoint from every executable/model.
        output = destination / "tts"
        if any(output.is_relative_to(path.resolve()) or path.resolve().is_relative_to(output)
               or destination.is_relative_to(path.resolve()) for path in protected):
            raise TtsError("TTS_DATA_ROOT", "日志、缓存不能写入引擎、声音包或其他项目目录。")
        for path in self.base_assets().values():
            resolved = path.resolve()
            # A chosen model directory may also be inside Data. Only the actual
            # runtime output subtree must remain disjoint from the assets.
            if (destination.is_relative_to(resolved) or output.is_relative_to(resolved)
                    or resolved.is_relative_to(output)):
                raise TtsError("TTS_DATA_ROOT", "底模目录不能与日志、缓存目录重叠。")
        missing = []
        for key, path in self.base_assets().items():
            if key == "sv_weights":
                exists = path.is_file()
            else:
                exists = path.is_dir() and any(p.is_file() for p in path.iterdir())
            if not exists:
                missing.append(key)
        if missing:
            raise TtsError("BASE_ASSET_MISSING", "本机通用底模缺失或目录为空：" + "、".join(missing) + "。请设置 MF_TTS_* 现存路径；不会下载。")
        return self.voice()

    def child_environment(self, run_dir):
        env = dict(os.environ)
        cache = Path(run_dir) / "cache"
        for key in ("TEMP", "TMP", "TMPDIR", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
                    "TORCH_HOME", "TORCH_EXTENSIONS_DIR", "TORCHINDUCTOR_CACHE_DIR", "TRITON_CACHE_DIR",
                    "NUMBA_CACHE_DIR", "XDG_CACHE_HOME", "MPLCONFIGDIR", "CUDA_CACHE_PATH"):
            env[key] = str(cache / key.lower())
            Path(env[key]).mkdir(parents=True, exist_ok=True)
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(key, None)
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1",
                   TOKENIZERS_PARALLELISM="false", USE_LIBUV="0", OMP_NUM_THREADS=str(self.cpu_threads),
                   OPENBLAS_NUM_THREADS=str(self.cpu_threads), MKL_NUM_THREADS=str(self.cpu_threads),
                   NLTK_DATA=str(self.engine_root / "runtime/nltk_data"),
                   bert_path=str(self.base_assets()["bert_root"]))
        env["PATH"] = str(self.engine_python.parent) + os.pathsep + env.get("PATH", "")
        return env

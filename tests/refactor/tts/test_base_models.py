from dataclasses import replace
import hashlib
import json

import pytest

from refactor.learning.voice.download import DownloadAsset
from refactor.learning.voice.tts import config as configuration
from refactor.learning.voice.tts import model_manager as module


def test_manifest_covers_eight_downloads_and_verified_g2pw_members():
    assert len(module.ASSETS) == 8
    assert sum(a.size for a in module.ASSETS) == 1536693479
    assert len(module.G2PW_MEMBERS) == 8
    assert all("hf-mirror.com" in a.urls[-1] for a in module.ASSETS)
    assert module.ASSETS[-1].archive == "zip"
    assert any(m.path == "G2PWModel/g2pW.onnx" and m.size == 635212732 for m in module.G2PW_MEMBERS)


def test_installed_data_root_priority_fallback_and_environment_override(config, monkeypatch):
    names = ("chinese-roberta-wwm-ext-large/model", "chinese-hubert-base/model", "G2PWModel/model",
             "sv/pretrained_eres2netv2w24s4ep4.ckpt")
    digest = hashlib.sha256(b"explicit fake model").hexdigest()
    monkeypatch.setattr(module, "ASSETS", tuple(DownloadAsset(str(i), ("https://example.invalid/model",),
        19, digest, name) for i, name in enumerate(names)))
    monkeypatch.setattr(configuration, "BASE_MODEL_ROOT", config.base_model_root)
    manager = module.ModelManager(config.data_root)
    selected = replace(config, base_model_root=None)
    assert selected.base_assets() == config.base_assets()
    assert not manager.path.exists()  # Merely selecting paths never downloads/creates directories.
    for name in names:
        target = manager.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"explicit fake model")
    (manager.path / "installed.json").write_text(json.dumps(manager._receipt(manager.path)))
    assert manager.status()["installed"]
    manager.verify()
    assert selected.base_assets()["bert_root"] == manager.path / "chinese-roberta-wwm-ext-large"
    assert selected.validate().pack_id == "phrolova-b-gpt-sovits"
    monkeypatch.setenv("MF_TTS_BASE_ROOT", str(config.base_model_root))
    monkeypatch.setenv("MF_TTS_HUBERT_ROOT", str(config.base_model_root / "chinese-hubert-base"))
    explicit = configuration.TtsConfig.from_env(config.data_root)
    assert explicit.base_assets() == config.base_assets()
    # An incomplete installation is never preferred over a complete development copy.
    (manager.path / names[0]).unlink()
    assert selected.base_assets() == config.base_assets()


def test_model_directories_cannot_overlap_runtime_output(config):
    with pytest.raises(configuration.TtsError) as exc:
        replace(config, bert_root=config.data_root / "tts").validate()
    assert exc.value.code == "TTS_DATA_ROOT"

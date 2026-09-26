import json
from pathlib import Path
import sys
import time

import pytest

sys.dont_write_bytecode = True


def pytest_configure(config):
    if not config.option.basetemp:
        station = Path(__file__).resolve().parents[4]
        (station / "temp").mkdir(exist_ok=True)
        config.option.basetemp = str(station / "temp" / ("tts-pytest-" + str(time.time_ns())))


@pytest.fixture
def config(tmp_path):
    from refactor.learning.voice.tts.config import TtsConfig
    engine = tmp_path / "engine"
    (engine / "GPT_SoVITS/TTS_infer_pack").mkdir(parents=True)
    (engine / "GPT_SoVITS/TTS_infer_pack/TTS.py").write_text("# explicit fake engine")
    (engine / "python.exe").write_bytes(b"explicit fake interpreter")
    model_root = tmp_path / "bases"
    for folder in ("chinese-roberta-wwm-ext-large", "chinese-hubert-base", "G2PWModel"):
        base = model_root / folder
        base.mkdir(parents=True)
        (base / "test-only-model").write_bytes(b"explicit fake asset")
    sv = model_root / "sv/pretrained_eres2netv2w24s4ep4.ckpt"
    sv.parent.mkdir()
    sv.write_bytes(b"explicit fake speaker embedding model")
    root = tmp_path / "packs"
    pack = root / "phrolova-b-gpt-sovits/1.0.4"
    pack.mkdir(parents=True)
    for name in ("gpt.ckpt", "sovits.pth", "reference.wav"):
        (pack / name).write_bytes(b"test only")
    manifest = {"schema": "mentorflow.voice-pack.v1", "pack_id": "phrolova-b-gpt-sovits", "version": "1.0.4",
        "runtime": {"adapter": "mentorflow.gpt-sovits.v1"},
        "files": [{"role": role, "path": name} for role, name in (("gpt_weight", "gpt.ckpt"), ("sovits_weight", "sovits.pth"), ("reference_audio", "reference.wav"))],
        "styles": {"daily": {"language": "zh", "reference_audio": "reference.wav", "transcript": "这是参考声音。"}}}
    (pack / "voice-pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    catalog = tmp_path / "catalog/catalog.json"
    catalog.parent.mkdir()
    catalog.write_text(json.dumps({"schema": "mentorflow.voice-pack-catalog.v1", "packs": [
        {"pack_id": "phrolova-b-gpt-sovits", "version": "1.0.4", "default": True}]}))
    return TtsConfig(engine_root=engine, engine_python=engine / "python.exe", pack_roots=(root,),
                     base_model_root=model_root,
                     catalog=catalog, data_root=tmp_path / "writable", startup_timeout=.25, shutdown_timeout=.01)


@pytest.fixture(scope="session")
def qt_app():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

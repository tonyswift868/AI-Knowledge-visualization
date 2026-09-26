"""Explicit TTS common-model installer. No engine or voice-pack downloading.

Call download on a worker only after presenting status()'s size/URLs and getting
user consent. ZIP transport bytes are discarded after verified extraction.
"""
from pathlib import Path

from ..download import ArchiveMember, AssetDownloader, DownloadAsset
from .config import BASE_MODEL_DOWNLOADS

G2PW_MEMBERS = (
    ArchiveMember('G2PWModel/bopomofo_to_pinyin_wo_tune_dict.json', 7739, '0fe90e26be7653023772b3ad2d08952961bbf295fb39ba685c7172d3d9769f52'),
    ArchiveMember('G2PWModel/char_bopomofo_dict.json', 1851143, '4832fb6a553a9c5ee0c18b5cb698ce0acc312f324ee77ee61c340266d228184d'),
    ArchiveMember('G2PWModel/config.py', 347, 'b6154e494355d14a8d2c8a38d07720f77b34479e26aab21a5f63495ae904da76'),
    ArchiveMember('G2PWModel/g2pW.onnx', 635212732, '2eb3c71fd95117b2e1abef8d2d0cd78aae894bbe7f0fac105ddc9c32ce63cbd0'),
    ArchiveMember('G2PWModel/MONOPHONIC_CHARS.txt', 120744, 'fe7ed53098648441a94f414ed5fd3395789d1dceb98dc1e584489c2d43036b69'),
    ArchiveMember('G2PWModel/POLYPHONIC_CHARS.txt', 102444, 'cc7e5a3eb377e3289af5e3a6be7505ac11a0d21422f80cfbf161059798f8671b'),
    ArchiveMember('G2PWModel/record.log', 81808, 'd56fd692e4fd0090b3e11741f10238ea69bf4b380c0d65894646941e1231aed4'),
    ArchiveMember('G2PWModel/version', 5, 'cf063c2ce8ac1403d4dca358f0568dfd32684e7a4412e24bc6e7cd1a7cd0cefb'),
)

ASSETS = tuple(DownloadAsset(
    name=item["path"].replace("/", "--"), urls=(item["url"],),
    size=item["size"], sha256=item["sha256"], install_path=item["path"],
    archive=item.get("archive"), members=G2PW_MEMBERS if item.get("archive") else (),
) for item in BASE_MODEL_DOWNLOADS)


class ModelManager(AssetDownloader):
    def __init__(self, data_root: Path):
        super().__init__(Path(data_root).absolute() / "models/tts/gpt-sovits-base", ASSETS)

    def status(self) -> dict:
        return {**super().status(), "model_name": "gpt-sovits-base"}

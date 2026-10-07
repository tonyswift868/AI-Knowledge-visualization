"""Explicit, pinned model installation. No network access outside download()."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Callable
import urllib.request  # Retained for existing callers/tests patching the transport.

from ..download import ArchiveMember, AssetDownloader, DownloadAsset as Asset, DownloadError
from .errors import AsrError, DownloadCancelled
from ..storage import storage_root, save_storage

MODEL_NAME = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
RELEASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
# These pinned releases were not found in the checked maintainer repositories
# on ModelScope. This public GitHub acceleration endpoint serves the identical
# GitHub assets; both files were downloaded and SHA256-checked on 2026-10-08.
MIRROR_URL = "https://ghfast.top/" + RELEASE_URL


@dataclass(frozen=True)
class DownloadAsset:
    name: str
    size: int
    sha256: str
    url: str
    mirrors: tuple[str, ...] = ()


# Release asset metadata verified against the official GitHub API and downloaded
# bytes on 2026-09-27. Never silently accept replacement bytes at the same URL.
SENSEVOICE_ARCHIVE = DownloadAsset(
    MODEL_NAME + ".tar.bz2", 163002883,
    "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
    RELEASE_URL + MODEL_NAME + ".tar.bz2",
    (MIRROR_URL + MODEL_NAME + ".tar.bz2",),
)
SILERO_VAD = DownloadAsset(
    "silero_vad.onnx", 643854,
    "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    RELEASE_URL + "silero_vad.onnx",
    (MIRROR_URL + "silero_vad.onnx",),
)
ASSETS = (SENSEVOICE_ARCHIVE, SILERO_VAD)
REQUIRED_FILES = {
    "model.int8.onnx": (239233841, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"),
    "tokens.txt": (315894, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"),
    "silero_vad.onnx": (SILERO_VAD.size, SILERO_VAD.sha256),
}
CHUNK_SIZE = 256 * 1024
_DOWNLOAD_LOCK = threading.Lock()


def _downloader(path: Path) -> AssetDownloader:
    assets = tuple(Asset(
        asset.name, (*asset.mirrors, asset.url), asset.size, asset.sha256, asset.name,
        archive="tar.bz2" if asset.name == SENSEVOICE_ARCHIVE.name else None,
        members=tuple(ArchiveMember(f"{MODEL_NAME}/{name}", *REQUIRED_FILES[name], install_path=name)
                      for name in ("model.int8.onnx", "tokens.txt"))
                if asset.name == SENSEVOICE_ARCHIVE.name else (),
    ) for asset in ASSETS)
    return AssetDownloader(path, assets, keep_partial_on_network_error=True,
                           timeout=30, fallback_on_network_error=True)


def _asr_error(exc: DownloadError) -> AsrError:
    if exc.code == "已取消":
        return DownloadCancelled()
    code = {"下载忙": "ASR_DOWNLOAD_BUSY", "校验失败": "ASR_MODEL_INVALID"}.get(
        exc.code, "ASR_DOWNLOAD_FAILED")
    return AsrError(code, exc.message)


def verify_model_path(path: Path, cancel: threading.Event | None = None) -> None:
    """Full local integrity check, called on the engine's worker thread."""
    try:
        _downloader(Path(path)).verify(cancel=cancel)
    except DownloadError as exc:
        if exc.code == "已取消":
            raise DownloadCancelled() from exc
        raise AsrError("ASR_MODEL_REQUIRED", "语音模型未安装完整或校验失败，请在确认后重新下载。") from exc


class ModelManager:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root).absolute()
        self.path = storage_root(self.data_root, "asr") / "asr" / MODEL_NAME
        self._staging = self.path.parent / ("." + MODEL_NAME + ".download")
        self.download_storage = None
        self._download_url: str | None = None

    def status(self) -> dict:
        return {"installed": _downloader(self.path).status()["installed"], "model_name": MODEL_NAME,
                "size_bytes": sum(asset.size for asset in ASSETS),
                "download_url": self._download_url or (SENSEVOICE_ARCHIVE.mirrors or (SENSEVOICE_ARCHIVE.url,))[0],
                "download_urls": [url for asset in ASSETS for url in (*asset.mirrors, asset.url)],
                "path": str(self.path)}

    def download(self, progress: Callable[[int, int], None], cancel: threading.Event) -> None:
        """Explicit worker call; preserves ASR's network-interruption resume cache."""
        if not _DOWNLOAD_LOCK.acquire(blocking=False):
            raise AsrError("ASR_DOWNLOAD_BUSY", "已有语音模型下载正在进行。")
        try:
            storage = self.download_storage
            target = Path(storage) / "asr" / MODEL_NAME if storage is not None else self.path
            downloader = _downloader(target)
            self._download_url = None

            def update(done, total):
                self._download_url = downloader.current_download_url
                progress(done, total)

            downloader.download(update, cancel)
            if storage is not None:
                save_storage(self.data_root, "asr", storage)
                self.path = target
                self._staging = target.parent / ("." + MODEL_NAME + ".download")
        except DownloadError as exc:
            raise _asr_error(exc) from exc
        finally:
            self.download_storage = None
            _DOWNLOAD_LOCK.release()

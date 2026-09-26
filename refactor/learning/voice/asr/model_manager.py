"""Explicit, pinned model installation. No network access outside download()."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import threading
from typing import Callable
import urllib.error
import urllib.request

from .errors import AsrError, DownloadCancelled

MODEL_NAME = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
RELEASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"


@dataclass(frozen=True)
class DownloadAsset:
    name: str
    size: int
    sha256: str
    url: str


# Release asset metadata verified against the official GitHub API and downloaded
# bytes on 2026-09-27. Never silently accept replacement bytes at the same URL.
SENSEVOICE_ARCHIVE = DownloadAsset(
    MODEL_NAME + ".tar.bz2", 163002883,
    "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
    RELEASE_URL + MODEL_NAME + ".tar.bz2",
)
SILERO_VAD = DownloadAsset(
    "silero_vad.onnx", 643854,
    "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    RELEASE_URL + "silero_vad.onnx",
)
ASSETS = (SENSEVOICE_ARCHIVE, SILERO_VAD)
REQUIRED_FILES = {
    "model.int8.onnx": (239233841, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"),
    "tokens.txt": (315894, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"),
    "silero_vad.onnx": (SILERO_VAD.size, SILERO_VAD.sha256),
}
CHUNK_SIZE = 256 * 1024
_DOWNLOAD_LOCK = threading.Lock()


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise DownloadCancelled()


def _digest(path: Path, cancel: threading.Event) -> str:
    result = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(CHUNK_SIZE):
            _check_cancel(cancel)
            result.update(chunk)
    _check_cancel(cancel)
    return result.hexdigest()


def verify_model_path(path: Path, cancel: threading.Event | None = None) -> None:
    """Full local integrity check, called on the engine's worker thread."""
    cancel = cancel if cancel is not None else threading.Event()
    for name, (size, digest) in REQUIRED_FILES.items():
        candidate = path / name
        try:
            valid = (not candidate.is_symlink() and candidate.is_file()
                     and candidate.stat().st_size == size and _digest(candidate, cancel) == digest)
        except OSError:
            valid = False
        if not valid:
            raise AsrError("ASR_MODEL_REQUIRED", "语音模型未安装完整或校验失败，请在确认后重新下载。")


class ModelManager:
    def __init__(self, data_root: Path):
        self.path = Path(data_root).absolute() / "models" / "asr" / MODEL_NAME
        self._staging = self.path.parent / ("." + MODEL_NAME + ".download")

    def _receipt(self) -> dict:
        return {name: {"size": size, "sha256": digest,
                       "mtime_ns": (self.path / name).stat().st_mtime_ns}
                for name, (size, digest) in REQUIRED_FILES.items()}

    def status(self) -> dict:
        # A small receipt avoids hashing a 239 MB file in the GUI thread.
        # A full hash is still checked in the worker before loading the model.
        installed = False
        try:
            receipt_path = self.path / "installed.json"
            if receipt_path.stat().st_size < 16384:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                installed = receipt == self._receipt() and all(
                    not (self.path / name).is_symlink()
                    and (self.path / name).is_file()
                    and (self.path / name).stat().st_size == size
                    for name, (size, _) in REQUIRED_FILES.items()
                )
        except (OSError, ValueError, TypeError):
            pass
        return {"installed": installed, "model_name": MODEL_NAME,
                "size_bytes": sum(asset.size for asset in ASSETS),
                "download_url": SENSEVOICE_ARCHIVE.url,
                "download_urls": [asset.url for asset in ASSETS], "path": str(self.path)}

    def _download_asset(self, asset: DownloadAsset, progress, cancel, base: int, total: int) -> Path:
        partial = self._staging / (asset.name + ".part")
        verified = self._staging / (asset.name + ".verified")
        if verified.is_file():
            if verified.stat().st_size == asset.size and _digest(verified, cancel) == asset.sha256:
                progress(base + asset.size, total)
                return verified
            verified.unlink()
        for attempt in range(3):
            _check_cancel(cancel)
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > asset.size:
                partial.unlink()
                offset = 0
            try:
                if offset < asset.size:
                    headers = {"User-Agent": "MentorFlow/VOICE-01", "Accept-Encoding": "identity"}
                    if offset:
                        headers["Range"] = f"bytes={offset}-"
                    request = urllib.request.Request(asset.url, headers=headers)
                    with urllib.request.urlopen(request, timeout=15) as response:
                        if response.status == 206:
                            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                            if (not match or int(match[1]) != offset or int(match[3]) != asset.size
                                    or not offset <= int(match[2]) < asset.size):
                                raise AsrError("ASR_DOWNLOAD_FAILED", "模型下载的续传范围无效，请重试。")
                        elif response.status == 200:
                            offset = 0  # Server ignored Range; restart, never append.
                        else:
                            raise AsrError("ASR_DOWNLOAD_FAILED", "模型下载服务器返回了无效响应。")
                        progress(base + offset, total)
                        with partial.open("ab" if offset else "wb") as writer:
                            while True:
                                _check_cancel(cancel)
                                chunk = response.read(min(CHUNK_SIZE, asset.size - offset + 1))
                                if not chunk:
                                    break
                                if offset + len(chunk) > asset.size:
                                    raise AsrError("ASR_MODEL_INVALID", "模型下载大小不符，未安装。")
                                writer.write(chunk)
                                offset += len(chunk)
                                progress(base + offset, total)
                            writer.flush()
                            os.fsync(writer.fileno())
                    if offset != asset.size:
                        raise OSError("incomplete download")
                _check_cancel(cancel)
                if _digest(partial, cancel) != asset.sha256:
                    partial.unlink(missing_ok=True)
                    raise AsrError("ASR_MODEL_INVALID", "模型 SHA256 校验失败，未安装。")
                os.replace(partial, verified)
                progress(base + asset.size, total)
                return verified
            except urllib.error.HTTPError as exc:
                if exc.code == 416:
                    partial.unlink(missing_ok=True)
                if attempt == 2:
                    raise AsrError("ASR_DOWNLOAD_FAILED", "模型下载失败，可重试；已保留可续传的数据。") from exc
            except (OSError, http.client.HTTPException) as exc:
                _check_cancel(cancel)
                if attempt == 2:
                    raise AsrError("ASR_DOWNLOAD_FAILED", "模型下载中断，可重试并断点续传。") from exc
        raise AsrError("ASR_DOWNLOAD_FAILED", "模型下载失败。")

    def download(self, progress: Callable[[int, int], None], cancel: threading.Event) -> None:
        """Blocking installer for the caller's download worker, after UI consent.

        Network interruptions retain resumable .part files for the next call.
        Explicit cancellation removes all staging bytes. Installed files are
        published only after every download and extracted member is verified.
        """
        if not _DOWNLOAD_LOCK.acquire(blocking=False):
            raise AsrError("ASR_DOWNLOAD_BUSY", "已有语音模型下载正在进行。")
        try:
            _check_cancel(cancel)
            total = sum(asset.size for asset in ASSETS)
            if self.status()["installed"]:
                try:
                    verify_model_path(self.path, cancel)
                except AsrError as exc:
                    if exc.code != "ASR_MODEL_REQUIRED":
                        raise
                    # A size/mtime-preserving corruption must be repairable
                    # through this same explicit download entry.
                else:
                    progress(total, total)
                    return
            self._staging.mkdir(parents=True, exist_ok=True)
            paths, base = [], 0
            for asset in ASSETS:
                paths.append(self._download_asset(asset, progress, cancel, base, total))
                base += asset.size
            with tempfile.TemporaryDirectory(prefix="unpack-", dir=self._staging) as temporary:
                ready = Path(temporary)
                expected = {f"{MODEL_NAME}/{name}": name for name in ("model.int8.onnx", "tokens.txt")}
                seen = set()
                with tarfile.open(paths[0], "r|bz2") as archive:
                    for member in archive:
                        _check_cancel(cancel)
                        name = expected.get(member.name)
                        if name is None:
                            continue
                        if name in seen or not member.isfile() or member.size != REQUIRED_FILES[name][0]:
                            raise AsrError("ASR_MODEL_INVALID", "模型压缩包内容不符，未安装。")
                        seen.add(name)
                        # Never extract paths, links, or any unrequested members.
                        with archive.extractfile(member) as reader, (ready / name).open("xb") as writer:
                            while chunk := reader.read(CHUNK_SIZE):
                                _check_cancel(cancel)
                                writer.write(chunk)
                            writer.flush()
                            os.fsync(writer.fileno())
                if seen != set(expected.values()):
                    raise AsrError("ASR_MODEL_INVALID", "模型压缩包缺少必要文件，未安装。")
                shutil.copyfile(paths[1], ready / "silero_vad.onnx")
                verify_model_path(ready, cancel)
                _check_cancel(cancel)
                # Cancellation is checked before this short commit section.
                # Receipt is the commit marker; status never trusts partial installs.
                self.path.mkdir(parents=True, exist_ok=True)
                (self.path / "installed.json").unlink(missing_ok=True)
                for name in REQUIRED_FILES:
                    os.replace(ready / name, self.path / name)
                receipt = ready / "installed.json"
                with receipt.open("w", encoding="utf-8") as writer:
                    json.dump(self._receipt(), writer)
                    writer.flush()
                    os.fsync(writer.fileno())
                os.replace(receipt, self.path / "installed.json")
            shutil.rmtree(self._staging)
            progress(total, total)
        except DownloadCancelled:
            if self._staging.exists():
                shutil.rmtree(self._staging)
            raise
        except (OSError, tarfile.TarError) as exc:
            raise AsrError("ASR_DOWNLOAD_FAILED", "模型安装未完成，请检查磁盘空间和目录权限后重试。") from exc
        finally:
            _DOWNLOAD_LOCK.release()

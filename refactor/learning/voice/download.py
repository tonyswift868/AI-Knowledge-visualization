"""Explicit, pinned asset installation, shared by ASR and TTS.

``status`` only examines a local receipt. Call ``download`` on a worker after
user consent; it never runs implicitly. Only verified, complete directories are
published. Retries (and interrupted processes) resume private staging files.
Ordinary failure/cancellation removes staging; the ASR compatibility adapter
may retain network-interrupted bytes for a later explicit retry.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import threading
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import zipfile

CHUNK_SIZE = 256 * 1024
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class DownloadError(RuntimeError):
    """Stable Chinese codes: 网络失败 / 校验失败 / 磁盘不足 / 已取消.

    下载忙 and 安装失败 describe local concurrency/permission failures.
    """
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _relative(value: str) -> str:
    value = str(value).replace("\\", "/")
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/"))
            or any(c in value for c in ':\x00') or any(p.endswith((" ", ".")) for p in path.parts)
            or any(re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p) for p in path.parts)):
        raise ValueError("资产必须使用安全的安装相对路径")
    return path.as_posix()


def _metadata(size: int, digest: str) -> None:
    if size < 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("资产需要准确的大小和 SHA256")


def asset_urls(urls: tuple[str, ...]) -> tuple[str, ...]:
    """Keep explicit order; append HF mirrors, never invent GitHub mirrors."""
    result = list(dict.fromkeys(urls))
    if not result:
        raise ValueError("资产缺少下载地址")
    for url in tuple(result):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
            raise ValueError("资产下载地址无效")
        if parts.hostname == "huggingface.co":
            mirror = urllib.parse.urlunsplit(parts._replace(netloc="hf-mirror.com"))
            if mirror not in result:
                result.append(mirror)
    return tuple(result)


@dataclass(frozen=True)
class ArchiveMember:
    path: str
    size: int
    sha256: str
    install_path: str = ""

    def __post_init__(self):
        object.__setattr__(self, "path", _relative(self.path))
        object.__setattr__(self, "install_path", _relative(self.install_path or self.path))
        _metadata(self.size, self.sha256)


@dataclass(frozen=True)
class DownloadAsset:
    """install_path places a raw file; archives install their listed members.

    The archive's install_path identifies its transport artifact only. Archive
    bytes stay in staging and are removed after installation.
    """
    name: str
    urls: tuple[str, ...]
    size: int
    sha256: str
    install_path: str
    archive: str | None = None
    members: tuple[ArchiveMember, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "name", _relative(self.name))
        if "/" in self.name:
            raise ValueError("资产名称不能包含目录")
        object.__setattr__(self, "urls", asset_urls(tuple(self.urls)))
        object.__setattr__(self, "install_path", _relative(self.install_path))
        object.__setattr__(self, "members", tuple(self.members))
        _metadata(self.size, self.sha256)
        if self.archive not in {None, "zip", "tar.bz2"} or bool(self.archive) != bool(self.members):
            raise ValueError("压缩包必须提供解压后校验清单")
        if len({m.path for m in self.members}) != len(self.members):
            raise ValueError("压缩包校验清单包含重复路径")


def check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise DownloadError("已取消", "资源下载已取消。")


def file_digest(path: Path, cancel: threading.Event) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(CHUNK_SIZE):
            check_cancel(cancel)
            digest.update(chunk)
    check_cancel(cancel)
    return digest.hexdigest()


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


class AssetDownloader:
    def __init__(self, path: Path, assets: tuple[DownloadAsset, ...], *,
                 keep_partial_on_network_error: bool = False, timeout: float = 15):
        self.path = Path(path).absolute()
        self.assets = tuple(assets)
        self.staging = self.path.with_name("." + self.path.name + ".download")
        self.backup = self.path.with_name("." + self.path.name + ".previous")
        self.keep_partial_on_network_error = keep_partial_on_network_error
        self.timeout = timeout
        self.last_download_urls: dict[str, str] = {}
        self.last_resolved_urls: dict[str, str] = {}
        self.required: dict[str, tuple[int, str]] = {}
        names: set[str] = set()
        for asset in self.assets:
            if asset.name.casefold() in names:
                raise ValueError("资产名称重复")
            names.add(asset.name.casefold())
            files = [(m.install_path, m.size, m.sha256) for m in asset.members] if asset.archive else [
                (asset.install_path, asset.size, asset.sha256)]
            for name, size, digest in files:
                folded = name.casefold()
                if (folded == "installed.json" or any(folded == n.casefold() or
                        folded.startswith(n.casefold() + "/") or n.casefold().startswith(folded + "/")
                        for n in self.required)):
                    raise ValueError("安装路径重复或冲突")
                self.required[name] = size, digest
        if not self.required:
            raise ValueError("资产清单为空")
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.path).casefold(), threading.Lock())

    @property
    def size_bytes(self) -> int:
        return sum(a.size for a in self.assets)

    def _safe_file(self, root: Path, name: str) -> bool:
        candidate = root / name
        return not any(_linked(p) for p in (candidate, *candidate.parents) if p == root or root in p.parents)

    def _receipt(self, root: Path) -> dict:
        return {name: {"size": size, "sha256": digest, "mtime_ns": (root / name).stat().st_mtime_ns}
                for name, (size, digest) in self.required.items()}

    def status(self) -> dict:
        """Cheap offline check; full hashing belongs to the download/load worker."""
        installed = False
        try:
            receipt_path = self.path / "installed.json"
            if self._safe_file(self.path, "installed.json") and receipt_path.stat().st_size < 1024 * 1024:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                installed = receipt == self._receipt(self.path) and all(
                    self._safe_file(self.path, name) and (self.path / name).is_file()
                    and (self.path / name).stat().st_size == size for name, (size, _) in self.required.items())
        except (OSError, ValueError, TypeError):
            pass
        return {"installed": installed, "path": str(self.path), "size_bytes": self.size_bytes,
                "installed_size_bytes": sum(size for size, _ in self.required.values()),
                "download_urls": [url for asset in self.assets for url in asset.urls]}

    def verify(self, root: Path | None = None, cancel: threading.Event | None = None) -> None:
        root = self.path if root is None else root
        cancel = threading.Event() if cancel is None else cancel
        for name, (size, digest) in self.required.items():
            check_cancel(cancel)
            path = root / name
            try:
                valid = (self._safe_file(root, name) and path.is_file() and path.stat().st_size == size
                         and file_digest(path, cancel) == digest)
            except OSError:
                valid = False
            if not valid:
                raise DownloadError("校验失败", f"资源 {name} 的大小或 SHA256 校验失败，未安装。")

    def _clear_staging(self):
        # Never follow a substituted link, and only remove our named sibling.
        if self.staging.exists() and not _linked(self.staging):
            shutil.rmtree(self.staging)

    def _check_space(self):
        needed = self.size_bytes + sum(size for size, _ in self.required.values()) + 1024 * 1024
        cached = sum(p.stat().st_size for p in self.staging.iterdir() if p.is_file() and not _linked(p))
        if shutil.disk_usage(self.path.parent).free < max(0, needed - cached):
            raise DownloadError("磁盘不足", "磁盘空间不足，无法下载并完整解压这些资源。")

    def _fetch(self, asset: DownloadAsset, progress, cancel, base: int) -> Path:
        partial = self.staging / (asset.name + ".part")
        verified = self.staging / (asset.name + ".verified")
        if _linked(partial) or _linked(verified):
            raise DownloadError("安装失败", "下载缓存路径不能是链接。")
        if verified.is_file():
            if verified.stat().st_size == asset.size and file_digest(verified, cancel) == asset.sha256:
                progress(base + asset.size, self.size_bytes)
                return verified
            verified.unlink()
        last_error = None
        for url in asset.urls:
            for _ in range(3):
                check_cancel(cancel)
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > asset.size:
                    partial.unlink()
                    offset = 0
                try:
                    if offset < asset.size or not partial.exists():
                        headers = {"User-Agent": "MentorFlow/VOICE-01", "Accept-Encoding": "identity"}
                        if offset:
                            headers["Range"] = f"bytes={offset}-"
                        request = urllib.request.Request(url, headers=headers)
                        with urllib.request.urlopen(request, timeout=self.timeout) as response:
                            if response.status == 206:
                                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                                if (not match or int(match[1]) != offset or int(match[3]) != asset.size
                                        or not offset <= int(match[2]) < asset.size):
                                    raise DownloadError("网络失败", "资源下载的续传范围无效，请重试。")
                            elif response.status == 200:
                                offset = 0  # A server may ignore Range. Never append its full response.
                            else:
                                raise DownloadError("网络失败", "资源下载服务器返回了无效响应。")
                            self.last_download_urls[asset.name] = url
                            self.last_resolved_urls[asset.name] = response.geturl()
                            progress(base + offset, self.size_bytes)
                            with partial.open("ab" if offset else "wb") as writer:
                                while True:
                                    check_cancel(cancel)
                                    chunk = response.read(min(CHUNK_SIZE, asset.size - offset + 1))
                                    if not chunk:
                                        break
                                    if offset + len(chunk) > asset.size:
                                        raise DownloadError("校验失败", "资源下载大小不符，未安装。")
                                    writer.write(chunk)
                                    offset += len(chunk)
                                    progress(base + offset, self.size_bytes)
                                writer.flush()
                                os.fsync(writer.fileno())
                        if offset != asset.size:
                            raise DownloadError("网络失败", "资源下载中断，可重试并断点续传。")
                    check_cancel(cancel)
                    if file_digest(partial, cancel) != asset.sha256:
                        raise DownloadError("校验失败", "资源 SHA256 校验失败，未安装。")
                    os.replace(partial, verified)
                    progress(base + asset.size, self.size_bytes)
                    return verified
                except DownloadError as exc:
                    if exc.code not in {"网络失败", "校验失败"}:
                        raise
                    last_error = exc
                    if exc.code == "校验失败":
                        partial.unlink(missing_ok=True)
                        break  # Do not download known bad bytes again from the same mirror.
                except (OSError, http.client.HTTPException) as exc:
                    if _disk_full(exc):
                        raise DownloadError("磁盘不足", "磁盘空间不足，资源未安装。") from exc
                    if isinstance(exc, urllib.error.HTTPError) and exc.code == 416:
                        partial.unlink(missing_ok=True)
                    last_error = DownloadError("网络失败", "资源下载失败，请检查网络后重试。")
                    last_error.__cause__ = exc
        check_cancel(cancel)
        raise last_error or DownloadError("网络失败", "资源下载失败。")

    def _copy(self, reader, target: Path, size: int, cancel):
        target.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with target.open("xb") as writer:
            while chunk := reader.read(min(CHUNK_SIZE, size - count + 1)):
                check_cancel(cancel)
                count += len(chunk)
                if count > size:
                    raise DownloadError("校验失败", "压缩包文件大小超出清单，未安装。")
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if count != size:
            raise DownloadError("校验失败", "压缩包文件大小不符，未安装。")

    def _unpack(self, asset: DownloadAsset, source: Path, ready: Path, cancel):
        if not asset.archive:
            with source.open("rb") as reader:
                self._copy(reader, ready / asset.install_path, asset.size, cancel)
            return
        expected = {m.path: m for m in asset.members}
        seen: set[str] = set()
        archive = zipfile.ZipFile(source) if asset.archive == "zip" else tarfile.open(source, "r|bz2")
        with archive:
            for info in archive.infolist() if asset.archive == "zip" else archive:
                check_cancel(cancel)
                name = info.filename if asset.archive == "zip" else info.name
                member = expected.get(name)
                if member is None:
                    continue  # Only extract named files; never extract arbitrary paths/links.
                if asset.archive == "zip":
                    mode = info.external_attr >> 16
                    regular = not info.is_dir() and stat.S_IFMT(mode) in {0, stat.S_IFREG}
                    size = info.file_size
                else:
                    regular, size = info.isfile(), info.size
                if name in seen or not regular or size != member.size:
                    raise DownloadError("校验失败", "压缩包内容与校验清单不符，未安装。")
                seen.add(name)
                with (archive.open(info) if asset.archive == "zip" else archive.extractfile(info)) as reader:
                    self._copy(reader, ready / member.install_path, member.size, cancel)
        if seen != set(expected):
            raise DownloadError("校验失败", "压缩包缺少必要文件，未安装。")

    def _publish(self, ready: Path):
        # Directory rename publishes the whole verified set at once. Windows
        # cannot replace nonempty directories: retain the old set for rollback.
        had_old = self.path.exists()
        if had_old:
            os.replace(self.path, self.backup)
        try:
            os.replace(ready, self.path)
        except BaseException:
            if had_old:
                os.replace(self.backup, self.path)
            raise
        if had_old:
            shutil.rmtree(self.backup)

    def download(self, progress: Callable[[int, int], None], cancel: threading.Event) -> None:
        if not self._lock.acquire(blocking=False):
            raise DownloadError("下载忙", "此资源已有下载正在进行。")
        try:
            if any(_linked(p) for p in (self.path, self.staging, self.backup)):
                raise DownloadError("安装失败", "模型安装及缓存目录不能是链接。")
            check_cancel(cancel)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.backup.exists():
                if not self.path.exists():
                    os.replace(self.backup, self.path)  # Recover an interrupted replacement.
                else:
                    shutil.rmtree(self.backup)
            if self.status()["installed"]:
                try:
                    self.verify(cancel=cancel)
                except DownloadError as exc:
                    if exc.code != "校验失败":
                        raise
                else:
                    self._clear_staging()
                    progress(self.size_bytes, self.size_bytes)
                    return
            self.staging.mkdir(exist_ok=True)
            ready = self.staging / "ready"
            if _linked(ready):
                raise DownloadError("安装失败", "解压缓存目录不能是链接。")
            if ready.exists():
                shutil.rmtree(ready)
            self._check_space()
            ready.mkdir()
            sources, base = [], 0
            for asset in self.assets:
                sources.append(self._fetch(asset, progress, cancel, base))
                base += asset.size
            for asset, source in zip(self.assets, sources):
                self._unpack(asset, source, ready, cancel)
            self.verify(ready, cancel)
            with (ready / "installed.json").open("w", encoding="utf-8") as writer:
                json.dump(self._receipt(ready), writer, ensure_ascii=False)
                writer.flush()
                os.fsync(writer.fileno())
            check_cancel(cancel)
            self._publish(ready)
            self._clear_staging()
            progress(self.size_bytes, self.size_bytes)
        except DownloadError as exc:
            if exc.code != "网络失败" or not self.keep_partial_on_network_error:
                self._clear_staging()
            raise
        except (zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
            self._clear_staging()
            raise DownloadError("校验失败", "资源压缩包损坏，未安装。") from exc
        except OSError as exc:
            self._clear_staging()
            code = "磁盘不足" if _disk_full(exc) else "安装失败"
            raise DownloadError(code, "磁盘空间不足，资源未安装。" if code == "磁盘不足" else
                                "资源安装失败，请检查目录权限后重试。") from exc
        except Exception:
            # A caller's progress callback can fail too; do not publish or
            # strand its partially prepared installation.
            self._clear_staging()
            raise
        finally:
            self._lock.release()


def _disk_full(exc) -> bool:
    return getattr(exc, "errno", None) == errno.ENOSPC or getattr(exc, "winerror", None) in {39, 112}

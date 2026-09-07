"""Unprivileged, content-addressed download/reconstruction for update_service.

No code in this module may write the installed App tree, elevate, or restart it.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import queue
import re
import shutil
import threading
import time
from urllib.parse import urlsplit
from typing import Protocol, Iterator, Callable

import httpx

from scripts import verify_windows_package as v
from .update_protocol import (DownloadCancelled, RecoverableError, SecurityError,
                              Release, MANIFEST_PATH)


def hash_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_entry(path: Path, root: Path, *, directory=False, missing=True):
    try:
        return v._validate_staging_entry(path, root, directory=directory,
                                        allow_missing=missing, label='desktop update cache')
    except v.PackageError as exc:
        raise SecurityError('unsafe update-cache entry') from exc


class InstallUpdateLock:
    """OS-held per-install lock; process exit releases it (no stale PID lease)."""
    def __init__(self, root):
        self.root, self.stream = Path(root), None

    def __enter__(self):
        import msvcrt
        safe_entry(self.root, self.root, directory=True, missing=False)
        path = self.root / 'operation.lock'
        safe_entry(path, self.root)
        self.stream = path.open('a+b')
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b'\0'); self.stream.flush()
        self.stream.seek(0)
        try:
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.stream.close(); self.stream = None
            raise RecoverableError('another update operation is active') from exc
        return self

    def __exit__(self, *_):
        self.stream.close()


class UpdateTransport(Protocol):
    """HTTP or future BITS adapter; callers own integrity and release policy."""
    bytes_received: int
    def validate_url(self, url: str) -> None: ...
    def fetch_metadata(self, url: str, maximum: int) -> bytes: ...
    def stream(self, url: str, *, offset: int = 0,
               cancel: Callable[[], bool] = ...) -> Iterator[tuple[str, object]]: ...


class HTTPTransport:
    """Explicit connect/read/total bounds without blocking Qt or waiting on DNS.

    A daemon network worker owns HTTPX. The caller's deadline is authoritative
    even if OS DNS has not returned. At most one worker per transport is allowed;
    subsequent requests fail recoverably while a timed-out worker is finishing.
    """
    def __init__(self, origin: str, *, fixture_http=False, connect=3.0, read=3.0, total=30.0):
        self.origin = urlsplit(origin)
        self.fixture_http = fixture_http
        self.connect, self.read, self.total = connect, read, total
        if not all(0 < n <= 300 for n in (connect, read, total)):
            raise ValueError('invalid HTTP timeout')
        self._active = threading.BoundedSemaphore(1)
        self.bytes_received = 0
        self.validate_url(origin)

    def validate_url(self, url):
        parsed = urlsplit(url)
        allowed_http = self.fixture_http and parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', '::1')
        if parsed.scheme != 'https' and not allowed_http:
            raise SecurityError('HTTPS required')
        if parsed.username or parsed.password or parsed.fragment or parsed.query or not parsed.hostname:
            raise SecurityError('invalid update URL')
        if (parsed.scheme, parsed.hostname, parsed.port) != (self.origin.scheme, self.origin.hostname, self.origin.port):
            raise SecurityError('update origin mismatch')

    def stream(self, url, *, offset=0, cancel=lambda: False):
        self.validate_url(url)
        if not self._active.acquire(blocking=False):
            raise RecoverableError('previous network request is still finishing')
        events = queue.Queue(maxsize=4)
        stop = threading.Event()
        deadline = time.monotonic() + self.total

        def put(value):
            while not stop.is_set():
                try:
                    events.put(value, timeout=.05); return
                except queue.Full:
                    pass

        def network():
            failure = None
            try:
                headers = {'Accept-Encoding': 'identity', 'User-Agent': 'MentorFlow-updater/1'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                with httpx.Client(timeout=httpx.Timeout(self.read, connect=self.connect),
                                  follow_redirects=False, trust_env=False) as client:
                    with client.stream('GET', url, headers=headers) as response:
                        put(('headers', (response.status_code, dict(response.headers))))
                        for block in response.iter_raw(chunk_size=65536):
                            self.bytes_received += len(block)
                            if stop.is_set():
                                break
                            put(('bytes', block))
            except Exception as exc:
                failure = type(exc).__name__
            finally:
                self._active.release()
                put(('error', failure) if failure else ('done', None))

        threading.Thread(target=network, daemon=True, name='mentorflow-update-http').start()
        try:
            while True:
                if cancel():
                    raise DownloadCancelled('download cancelled')
                if time.monotonic() >= deadline:
                    raise RecoverableError('update request total timeout')
                try:
                    kind, value = events.get(timeout=min(.05, max(.001, deadline-time.monotonic())))
                except queue.Empty:
                    continue
                if kind == 'done':
                    return
                if kind == 'error':
                    raise RecoverableError('update transport failed: ' + value)
                yield kind, value
        finally:
            stop.set()

    def fetch_metadata(self, url, maximum):
        result = bytearray()
        for kind, value in self.stream(url):
            if kind == 'headers':
                status, headers = value
                if status != 200:
                    raise RecoverableError('metadata HTTP status ' + str(status))
                if headers.get('content-encoding', 'identity') != 'identity':
                    raise SecurityError('encoded metadata refused')
            else:
                result.extend(value)
                if len(result) > maximum:
                    raise SecurityError('metadata exceeds size limit')
        return bytes(result)


class ObjectCache:
    def __init__(self, root: Path, transport: UpdateTransport):
        cache_root = Path(root).absolute()
        if os.name == 'nt':
            # Hash-addressed cache + a deep package path can exceed MAX_PATH.
            # Use explicit local extended paths; never alter global long-path
            # policy or shorten cryptographic object/install identities.
            if not re.fullmatch(r'[A-Za-z]:', cache_root.drive):
                raise SecurityError('cache requires a local volume')
            cache_root = Path('\\\\?\\' + str(cache_root))
        self.root, self.transport = cache_root, transport
        safe_entry(self.root, self.root, directory=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects = self.root / 'objects'
        safe_entry(self.objects, self.root, directory=True)
        self.objects.mkdir(exist_ok=True)

    def complete(self, row):
        path = self.objects / row['sha256']
        if not safe_entry(path, self.root):
            return None
        if path.stat().st_size == row['size'] and hash_file(path) == row['sha256']:
            return path
        raise SecurityError('completed cache object was modified')

    def download(self, asset, *, cancel=lambda: False, progress=lambda n: None):
        existing = self.complete(asset)
        if existing:
            return existing
        partial = self.objects / (asset['sha256'] + '.part')
        safe_entry(partial, self.root)
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > asset['size']:
            raise SecurityError('oversized partial object')
        if offset == asset['size'] and partial.exists():
            if hash_file(partial) != asset['sha256']:
                raise SecurityError('completed partial hash mismatch')
        else:
            stream = None
            try:
                for kind, value in self.transport.stream(asset['url'], offset=offset, cancel=cancel):
                    if kind == 'headers':
                        status, headers = value
                        if headers.get('content-encoding', 'identity') != 'identity':
                            raise SecurityError('encoded asset refused')
                        if status == 206:
                            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', headers.get('content-range', ''))
                            if not match or tuple(map(int, match.groups())) != (offset, asset['size']-1, asset['size']):
                                raise SecurityError('invalid range response')
                        elif status == 200:
                            offset = 0  # Range unsupported: restart only THIS object.
                        else:
                            raise RecoverableError('asset HTTP status ' + str(status))
                        safe_entry(partial, self.root)
                        stream = partial.open('ab' if offset else 'wb')
                    else:
                        if stream is None or offset + len(value) > asset['size']:
                            raise SecurityError('asset response size mismatch')
                        stream.write(value); offset += len(value); progress(len(value))
            finally:
                if stream is not None:
                    stream.flush(); os.fsync(stream.fileno()); stream.close()
        if not partial.exists() or partial.stat().st_size != asset['size']:
            raise RecoverableError('interrupted asset retained')
        if hash_file(partial) != asset['sha256']:
            raise SecurityError('asset SHA-256 mismatch')
        target = self.objects / asset['sha256']
        safe_entry(target, self.root)
        partial.replace(target)
        return target

    def disk_preflight(self, release: Release, plan: dict, *, free=None):
        missing = sum(release.rows[p]['size'] for p in plan['download_paths'] if not self.complete(release.rows[p]))
        # Additional reconstruction, two metadata copies and conservative reserve.
        required = missing + sum(r['size'] for r in release.rows.values()) + 2 * len(release.envelope) + 64*1024**2
        available = shutil.disk_usage(self.root).free if free is None else free
        if available < required:
            raise RecoverableError('insufficient disk space before download/reconstruction')
        return required

    def reconstruct(self, release: Release, plan: dict, old_app: Path | None, *, cancel=lambda: False,
                    progress=lambda n: None):
        self.disk_preflight(release, plan)
        target = self.root / ('version-' + hashlib.sha256(release.envelope).hexdigest())
        safe_entry(target, self.root, directory=True)
        target.mkdir(exist_ok=True)
        allowed = set(release.rows)
        for existing in target.rglob('*'):
            safe_entry(existing, self.root, directory=existing.is_dir(), missing=False)
            if existing.is_file() and existing.relative_to(target).as_posix() not in allowed:
                raise SecurityError('unknown staging file preserved')
        fetch = set(plan['download_paths'])
        for path, row in release.rows.items():
            if cancel():
                raise DownloadCancelled('reconstruction cancelled')
            destination = target / path
            safe_entry(destination, self.root)
            if destination.exists():
                if destination.stat().st_size != row['size'] or hash_file(destination) != row['sha256']:
                    raise SecurityError('reconstruction cache was modified')
                continue
            source = None
            if path not in fetch and old_app is not None:
                candidate = Path(old_app) / path
                if candidate.is_file():
                    safe_entry(candidate, Path(old_app), missing=False)
                    if candidate.stat().st_size == row['size'] and hash_file(candidate) == row['sha256']:
                        source = candidate
            if source is None:
                source = self.download(release.assets[path], cancel=cancel, progress=progress)
            # Never hardlink live App: its bytes and ACL must remain independent.
            safe_entry(destination.parent, self.root, directory=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            copying = self.root / 'copying'
            safe_entry(copying, self.root, directory=True)
            copying.mkdir(exist_ok=True)
            temporary = copying / (row['sha256'] + '.part')
            safe_entry(temporary, self.root)
            # Only this single owned incomplete-copy name is restarted. Completed
            # cache objects and live App are never removed after interruption.
            with source.open('rb') as incoming, temporary.open('wb') as outgoing:
                while block := incoming.read(65536):
                    if cancel():
                        raise DownloadCancelled('reconstruction cancelled')
                    outgoing.write(block)
                outgoing.flush(); os.fsync(outgoing.fileno())
            if hash_file(temporary) != row['sha256']:
                raise SecurityError('reconstructed file hash mismatch')
            safe_entry(destination, self.root)
            if destination.exists(): raise SecurityError('staging destination changed during copy')
            temporary.rename(destination)
        self.verify_tree(target, release)
        return target

    def verify_tree(self, target, release):
        files = set()
        for path in Path(target).rglob('*'):
            safe_entry(path, self.root, directory=path.is_dir(), missing=False)
            if path.is_file():
                files.add(path.relative_to(target).as_posix())
        if files != set(release.rows):
            raise SecurityError('reconstructed file set mismatch')
        for path, row in release.rows.items():
            item = Path(target) / path
            if item.stat().st_size != row['size'] or hash_file(item) != row['sha256']:
                raise SecurityError('reconstructed tree hash mismatch')

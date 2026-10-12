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
from contextvars import ContextVar
from urllib.parse import urlsplit
from typing import Protocol, Iterator, Callable

import httpx

from refactor.desktop.delivery import package_verification as v
from refactor.desktop.delivery.update_protocol import DownloadCancelled
from refactor.desktop.delivery.update_protocol import RecoverableError
from refactor.desktop.delivery.update_protocol import SecurityError
from refactor.desktop.delivery.update_protocol import Release
from refactor.desktop.delivery.update_protocol import MANIFEST_PATH


_trace = ContextVar('update_io_trace', default=None)
_directory_guard = ContextVar('update_directory_guard', default=None)

PRIMARY_UPDATE_ROOT = 'https://aiteacher.clannad.win/updates/app/'
FALLBACK_UPDATE_ROOT = 'https://89.45.45.28/mentorflow/updates/app/'


def update_sources(url):
    for root in (PRIMARY_UPDATE_ROOT, FALLBACK_UPDATE_ROOT):
        if url.startswith(root):
            suffix = url[len(root):]
            return [PRIMARY_UPDATE_ROOT + suffix, FALLBACK_UPDATE_ROOT + suffix]
    return [url]


class UpdateTelemetry:
    """Per-operation diagnostic counters; never an integrity authority."""
    def __init__(self):
        self.events = []
        self.totals = dict(file_count=0, bytes_read=0, bytes_written=0,
                          bytes_reused=0, bytes_hashed=0, hash_file_count=0,
                          hash_wall_ms=0., hash_cpu_ms=0., path_wall_ms=0.)
        self.totals.update(path_validation_calls=0, path_unique_dirs=0,
            path_system_calls=0,hardlinked_file_count=0, hardlinked_bytes=0, copy_file_count=0, copy_bytes=0)
        self.last_time = time.monotonic()
        self.last_totals = dict(self.totals)

    def mark(self, name, error=None):
        now = time.monotonic()
        self.events.append(dict(name=name, start_monotonic=self.last_time,
            end_monotonic=now, elapsed_ms=(now-self.last_time)*1000,
            **{k:self.totals[k]-self.last_totals[k] for k in self.totals},
            errors=[] if error is None else [type(error).__name__]))
        self.last_time, self.last_totals = now, dict(self.totals)

    def report(self):
        return dict(events=list(self.events), totals=dict(self.totals))


def hash_file(path):
    trace = _trace.get()
    start, cpu = time.monotonic(), time.thread_time()
    with Path(path).open('rb') as stream:
        result = hashlib.file_digest(stream, 'sha256').hexdigest()
        if trace:
            size = stream.tell()
            trace.totals['bytes_read'] += size
            trace.totals['bytes_hashed'] += size
            trace.totals['hash_file_count'] += 1
            trace.totals['hash_wall_ms'] += (time.monotonic()-start)*1000
            trace.totals['hash_cpu_ms'] += (time.thread_time()-cpu)*1000
        return result


def safe_entry(path: Path, root: Path, *, directory=False, missing=True):
    start = time.monotonic()
    try:
        guard = _directory_guard.get()
        if guard is not None:
            return guard.check(path, root, directory=directory, missing=missing)
        return v._validate_staging_entry(path, root, directory=directory,
                                        allow_missing=missing, label='desktop update cache')
    except v.PackageError as exc:
        raise SecurityError('unsafe update-cache entry') from exc
    finally:
        trace = _trace.get()
        if trace:
            trace.totals['path_wall_ms'] += (time.monotonic()-start)*1000
            trace.totals['path_validation_calls'] += 1


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
    """Idle-bounded transfers; metadata alone also has a total deadline.

    A daemon network worker owns HTTPX. The caller's deadline is authoritative
    even if OS DNS has not returned. One worker per origin is allowed; the two
    fixed mirrors have independent slots so stalled domain DNS cannot block IP.
    Subsequent requests to that origin fail while its old worker is finishing.
    """
    def __init__(self, origin: str, *, fixture_http=False, signed_static_http=False,
                 connect=15.0, read=30.0, total=None, metadata_total=300.0):
        self.origin = urlsplit(origin)
        self.fixture_http = fixture_http
        self.signed_static_http = signed_static_http
        self.connect, self.read, self.total = connect, read, total
        self.metadata_total = metadata_total
        if not all(0 < n <= 300 for n in (connect, read, metadata_total)) or (total is not None and not 0 < total <= 300):
            raise ValueError('invalid HTTP timeout')
        self._active = threading.BoundedSemaphore(1)
        key = (self.origin.scheme, self.origin.hostname, self.origin.port)
        self._network_slots = {key:self._active}
        if len(update_sources(origin)) == 2:
            for root in update_sources(origin):
                endpoint = urlsplit(root)
                endpoint_key = (endpoint.scheme, endpoint.hostname, endpoint.port)
                if endpoint_key not in self._network_slots:
                    self._network_slots[endpoint_key] = threading.BoundedSemaphore(1)
        self._tls_contexts = {}
        self.bytes_received = 0
        self.validate_url(origin)

    @property
    def busy(self):
        """Read-only UI scheduling seam; cancellation can precede socket exit."""
        for slot in self._network_slots.values():
            if not slot.acquire(blocking=False):
                return True
            slot.release()
        return False

    def validate_url(self, url):
        parsed = urlsplit(url)
        allowed_http = self.fixture_http and parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', '::1')
        if parsed.scheme != 'https' and not allowed_http and not (self.signed_static_http and parsed.scheme == 'http'):
            raise SecurityError('HTTPS required')
        if parsed.username or parsed.password or parsed.fragment or parsed.query or not parsed.hostname:
            raise SecurityError('invalid update URL')
        if (parsed.scheme, parsed.hostname, parsed.port) != (self.origin.scheme, self.origin.hostname, self.origin.port):
            if (len(update_sources(self.origin.geturl())) != 2 or len(update_sources(url)) != 2):
                raise SecurityError('update origin mismatch')

    def source_urls(self, url):
        self.validate_url(url)
        return update_sources(url) if len(update_sources(self.origin.geturl())) == 2 else [url]

    def stream(self, url, *, offset=0, cancel=lambda: False, _total=None, _metadata=False):
        self.validate_url(url)
        endpoint = urlsplit(url)
        slot = self._network_slots[(endpoint.scheme, endpoint.hostname, endpoint.port)]
        if not slot.acquire(blocking=False):
            raise RecoverableError('previous network request is still finishing')
        events = queue.Queue(maxsize=4)
        stop = threading.Event()
        started = last_received = time.monotonic()
        total = self.total if _total is None else _total

        def put(value):
            while not stop.is_set():
                try:
                    events.put(value, timeout=.05); return
                except queue.Full:
                    pass

        def network():
            nonlocal last_received
            failure = None
            try:
                host = urlsplit(url).hostname
                if host not in self._tls_contexts:
                    # Resolve trust on the worker, once per host. Domain uses
                    # system roots; the legacy IP also trusts the bundled fixed CA.
                    import ssl
                    import certifi
                    if host == 'aiteacher.clannad.win':
                        tls = ssl.create_default_context()  # Public certificate, system roots.
                    else:
                        tls = ssl.create_default_context(cafile=certifi.where())
                        if url.startswith(FALLBACK_UPDATE_ROOT):
                            from refactor.desktop.shell_paths import ACCOUNT_API_TRUST
                            tls.load_verify_locations(cafile=str(ACCOUNT_API_TRUST))
                    self._tls_contexts[host] = tls
                headers = {'Accept-Encoding': 'gzip' if _metadata else 'identity', 'User-Agent': 'MentorFlow-updater/1'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                with httpx.Client(timeout=httpx.Timeout(self.read, connect=self.connect),
                                  follow_redirects=False, trust_env=False, verify=self._tls_contexts[host]) as client:
                    with client.stream('GET', url, headers=headers) as response:
                        put(('headers', (response.status_code, dict(response.headers))))
                        # Do not wait for a 64 KiB buffer to fill: even a small
                        # arriving socket block means the connection is alive.
                        last_received = time.monotonic()
                        received = 0
                        try:
                            blocks = response.iter_bytes() if _metadata else response.iter_raw()
                            for block in blocks:
                                last_received = time.monotonic()
                                self.bytes_received += response.num_bytes_downloaded - received
                                received = response.num_bytes_downloaded
                                if stop.is_set():
                                    break
                                put(('bytes', block))
                        finally:
                            self.bytes_received += response.num_bytes_downloaded - received
            except Exception as exc:
                failure = type(exc).__name__
            finally:
                slot.release()
                put(('error', failure) if failure else ('done', None))

        threading.Thread(target=network, daemon=True, name='mentorflow-update-http').start()
        try:
            while True:
                if cancel():
                    raise DownloadCancelled('download cancelled')
                now = time.monotonic()
                if total is not None and now - started >= total:
                    raise RecoverableError('update request total timeout')
                if now - last_received >= self.read:
                    raise RecoverableError('update request idle timeout')
                try:
                    kind, value = events.get(timeout=.05)
                except queue.Empty:
                    continue
                if kind == 'done':
                    return
                if kind == 'error':
                    raise RecoverableError('update transport failed: ' + value)
                yield kind, value
        finally:
            stop.set()

    def fetch_metadata(self, url, maximum, *, cancel=lambda: False, validate=None, deadline=None):
        import logging
        sources = self.source_urls(url)
        for index, source in enumerate(sources):
            if cancel():
                raise DownloadCancelled('update check cancelled')
            try:
                raw = self._fetch_metadata_once(source, maximum, cancel=cancel, deadline=deadline)
                if validate is not None:
                    validate(raw)
                self.last_metadata_source = source
                return raw
            except DownloadCancelled:
                raise
            except (RecoverableError, SecurityError):
                if index == len(sources) - 1:
                    raise
                logging.getLogger(__name__).warning('update metadata source failed; falling back to IP: %s', source)

    def _fetch_metadata_once(self, url, maximum, *, cancel, deadline):
        result = bytearray()
        total = min(self.total, self.metadata_total) if self.total is not None else self.metadata_total
        if deadline is not None:
            total = min(total, deadline)
        for kind, value in self.stream(url, cancel=cancel, _total=total, _metadata=True):
            if kind == 'headers':
                status, headers = value
                if status != 200:
                    raise RecoverableError('metadata HTTP status ' + str(status))
                if headers.get('content-encoding', 'identity').lower() not in ('identity', 'gzip'):
                    raise SecurityError('encoded metadata refused')
            else:
                result.extend(value)
                if len(result) > maximum:
                    raise SecurityError('metadata exceeds size limit')
        return bytes(result)


def retry_wait(seconds, cancel=lambda: False):
    """Cancellation stays responsive during the agreed network backoff."""
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if cancel():
            raise DownloadCancelled('download cancelled')
        time.sleep(min(.05, max(0, until - time.monotonic())))
    if cancel():
        raise DownloadCancelled('download cancelled')


class ObjectCache:
    def __init__(self, root: Path, transport: UpdateTransport, *, runtime_root=None, accepted_app=None):
        cache_root = Path(root).absolute()
        if os.name == 'nt':
            # Hash-addressed cache + a deep package path can exceed MAX_PATH.
            # Use explicit local extended paths; never alter global long-path
            # policy or shorten cryptographic object/install identities.
            if not re.fullmatch(r'[A-Za-z]:', cache_root.drive):
                raise SecurityError('cache requires a local volume')
            cache_root = Path('\\\\?\\' + str(cache_root))
        self.root, self.transport = cache_root, transport
        self.runtime_root = Path(runtime_root).absolute() if runtime_root else self.root
        self.accepted_app = accepted_app
        self._hardlinks = {}
        self._download_offsets = {}
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
        import logging
        sources = self.transport.source_urls(asset['url']) if isinstance(self.transport, HTTPTransport) else [asset['url']]
        remote_content_errors = {'asset SHA-256 mismatch', 'encoded asset refused',
                                 'invalid range response', 'asset response size mismatch'}
        for index, source in enumerate(sources):
            for attempt in range(6):  # original attempt + five automatic resumes on last source
                try:
                    return self._download_once({**asset, 'url':source}, cancel=cancel, progress=progress)
                except DownloadCancelled:
                    raise
                except SecurityError as exc:
                    if index == len(sources)-1 or str(exc) not in remote_content_errors:
                        raise
                    partial = self.objects / (asset['sha256'] + '.part')
                    safe_entry(partial, self.root)
                    partial.unlink(missing_ok=True)  # Restart bad remote bytes, never a completed object.
                    logging.getLogger(__name__).warning('update asset content failed; falling back to IP: %s', source)
                    break
                except RecoverableError:
                    if index < len(sources)-1:
                        logging.getLogger(__name__).warning('update asset source failed; falling back to IP: %s', source)
                        break
                    if attempt == 5:
                        raise
                    retry_wait((2, 4, 8, 16, 30)[attempt], cancel)

    def downloaded_bytes(self, assets):
        """Presentation only; completion still requires the existing SHA check."""
        total = 0
        for asset in assets:
            hashed = asset['sha256']
            if hashed not in self._download_offsets:
                complete = self.objects / hashed
                partial = self.objects / (hashed + '.part')
                self._download_offsets[hashed] = (complete.stat().st_size if complete.is_file() else
                                                 partial.stat().st_size if partial.is_file() else 0)
            amount = self._download_offsets[hashed]
            total += min(asset['size'], amount)
        return total

    def _download_once(self, asset, *, cancel=lambda: False, progress=lambda n: None):
        if cancel():
            raise DownloadCancelled('download cancelled')
        existing = self.complete(asset)
        if existing:
            self._download_offsets[asset['sha256']] = asset['size']
            return existing
        partial = self.objects / (asset['sha256'] + '.part')
        safe_entry(partial, self.root)
        offset = partial.stat().st_size if partial.exists() else 0
        self._download_offsets[asset['sha256']] = offset
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
                            self._download_offsets[asset['sha256']] = 0
                        else:
                            raise RecoverableError('asset HTTP status ' + str(status))
                        safe_entry(partial, self.root)
                        stream = partial.open('ab' if offset else 'wb')
                    else:
                        if stream is None or offset + len(value) > asset['size']:
                            raise SecurityError('asset response size mismatch')
                        stream.write(value); offset += len(value)
                        self._download_offsets[asset['sha256']] = offset
                        progress(len(value))
                        trace = _trace.get()
                        if trace:
                            trace.totals['bytes_written'] += len(value)
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

    def local_file_plan(self, release, old_app, current, *, cancel=lambda: False, telemetry=None):
        """Hash actual local bytes at download time, never on the login check path."""
        from refactor.desktop.delivery.update_protocol import make_plan
        old_app = Path(old_app)
        reusable = {}
        token = _trace.set(telemetry) if telemetry is not None else None
        try:
            for path, row in release.rows.items():
                if cancel():
                    raise DownloadCancelled('local file verification cancelled')
                candidate = old_app / path
                if not safe_entry(candidate, old_app):
                    continue
                try:
                    if candidate.stat().st_size == row['size'] and hash_file(candidate) == row['sha256']:
                        reusable[path] = row
                except OSError:
                    # A missing/unreadable/changed local file is obtained from
                    # its signed object. Path alias violations still fail closed.
                    continue
            return make_plan(release, reusable, current)
        finally:
            if token is not None:
                _trace.reset(token)

    def disk_preflight(self, release: Release, plan: dict, *, free=None):
        missing = sum(release.rows[p]['size'] for p in plan['download_paths'] if not self.complete(release.rows[p]))
        # Additional reconstruction, two metadata copies and conservative reserve.
        required = missing + sum(r['size'] for r in release.rows.values()) + 2 * len(release.envelope) + 64*1024**2
        available = shutil.disk_usage(self.root).free if free is None else free
        if available < required:
            raise RecoverableError('insufficient disk space before download/reconstruction')
        return required

    def reconstruct(self, release: Release, plan: dict, old_app: Path | None, *, cancel=lambda: False,
                    progress=lambda n: None, telemetry=None):
        trace = telemetry or UpdateTelemetry()
        token = _trace.set(trace)
        guard = None
        guard_token = None
        if os.name == 'nt':
            from refactor.desktop.delivery.update_tree_security import DirectoryPins
            guard = DirectoryPins()
            guard_token = _directory_guard.set(guard)
        try:
            return self._reconstruct(release, plan, old_app, cancel=cancel,
                                     progress=progress, trace=trace)
        except Exception as exc:
            trace.mark('RECONSTRUCTION_FAILED', exc)
            raise
        finally:
            if guard is not None:
                trace.totals['path_system_calls'] = guard.calls
                trace.totals['path_unique_dirs'] = len(guard.handles)
                guard.close()
                _directory_guard.reset(guard_token)
            self.telemetry = trace.report()
            _trace.reset(token)

    def _reconstruct(self, release, plan, old_app, *, cancel, progress, trace):
        from refactor.desktop.delivery.runtime_path_budget import runtime_root as check_runtime
        stage_root = self.runtime_root
        target = stage_root / hashlib.sha256(release.envelope).hexdigest()[:12] / 'App'
        check_runtime(target, release.rows)
        self.disk_preflight(release, plan)
        # Fetch the small changed set first. Cancellation/corrupt-asset feedback
        # must not wait for materialization of the entire unchanged payload.
        fetch = set(plan['download_paths'])
        trace.mark('T03_ASSET_DOWNLOAD_BEGIN')
        downloaded = {}
        for path in plan['download_paths']:
            if cancel():
                raise DownloadCancelled('download cancelled')
            downloaded[path] = self.download(release.assets[path], cancel=cancel, progress=progress)
        trace.mark('T04_ASSET_DOWNLOAD_COMPLETE')
        trace.mark('T05_RECONSTRUCTION_BEGIN')
        safe_entry(target.parent,stage_root,directory=True)
        target.parent.mkdir(parents=True,exist_ok=True)
        identity=target.parent/'release.json'
        if safe_entry(identity,stage_root):
            if identity.read_bytes()!=release.envelope:
                raise SecurityError('short staging identity collision')
        else:
            with identity.open('xb') as stream:
                stream.write(release.envelope); stream.flush(); os.fsync(stream.fileno())
        safe_entry(target, stage_root, directory=True)
        target.mkdir(exist_ok=True)
        allowed = set(release.rows)
        for existing in target.rglob('*'):
            safe_entry(existing, stage_root, directory=existing.is_dir(), missing=False)
            if existing.is_file() and existing.relative_to(target).as_posix() not in allowed:
                raise SecurityError('unknown staging file preserved')
        trace.mark('T06_STAGE_DIRECTORY_READY')
        trace.mark('T07_UNCHANGED_MATERIALIZATION_BEGIN')
        hardlink_volume = False
        if self.accepted_app is not None and not self.accepted_app.closed:
            from refactor.desktop.delivery.update_tree_security import hardlink_eligible
            # A protected canonical receipt includes an exact no-alias file
            # inventory. Adding a user-staging hardlink changes the live file's
            # link/ChangeTime metadata and invalidates that authority. Keep the
            # hardlink optimization for explicit non-protected fixture leases;
            # protected installations reuse bytes through independent copies.
            hardlink_volume = (not self.accepted_app.lease.get('protected_contract') and
                               hardlink_eligible(old_app,stage_root))
        ordered = [p for p in release.rows if p not in fetch] + [p for p in release.rows if p in fetch]
        changed_started = False
        for path in ordered:
            row = release.rows[path]
            if path in fetch and not changed_started:
                trace.mark('T08_UNCHANGED_MATERIALIZATION_END')
                trace.mark('T09_CHANGED_MATERIALIZATION_BEGIN')
                changed_started = True
            if cancel():
                raise DownloadCancelled('reconstruction cancelled')
            destination = target / path
            safe_entry(destination, stage_root)
            if destination.exists():
                if destination.stat().st_size != row['size'] or hash_file(destination) != row['sha256']:
                    raise SecurityError('reconstruction cache was modified')
                continue
            source = downloaded.get(path)
            if path not in fetch and old_app is not None:
                candidate = Path(old_app) / path
                if candidate.is_file():
                    safe_entry(candidate, Path(old_app), missing=False)
                    if candidate.stat().st_size == row['size']:
                        # Untrusted source identity is verified while copying,
                        # not accepted on size/mtime. Final target bytes are
                        # independently read and hashed by verify_tree below.
                        source = candidate
            if source is None:
                source = self.download(release.assets[path], cancel=cancel, progress=progress)
            safe_entry(destination.parent, stage_root, directory=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            accepted = self.accepted_app
            if path not in fetch and accepted is not None and accepted.matches(path,row,old_app):
                from refactor.desktop.delivery.update_tree_security import normal
                if hardlink_volume:
                    try:
                        os.link(source,destination)
                    except OSError as exc:
                        # Only documented capability/permission failures can
                        # use an independent copy. Aliases/collisions/unknown
                        # errors never silently become a weaker path.
                        if getattr(exc,'winerror',None) not in (1,5,17,50,1314):
                            raise SecurityError('unexpected hardlink failure') from exc
                    else:
                        self._hardlinks[normal(destination)] = normal(source)
                        guard = _directory_guard.get()
                        guard.allowed_links.update((normal(source),normal(destination)))
                        accepted.pins.allowed_links.add(normal(source))
                        if not os.path.samefile(source,destination):
                            raise SecurityError('hardlink identity mismatch')
                        trace.totals['hardlinked_file_count'] += 1
                        trace.totals['hardlinked_bytes'] += row['size']
                        trace.totals['bytes_reused'] += row['size']
                        trace.totals['file_count'] += 1
                        continue
            # Changed/new/control files always have independent file objects.
            copying = stage_root / 'copying'
            safe_entry(copying, stage_root, directory=True)
            copying.mkdir(exist_ok=True)
            temporary = copying / (row['sha256'] + '.part')
            safe_entry(temporary, stage_root)
            # Only this single owned incomplete-copy name is restarted. Completed
            # cache objects and live App are never removed after interruption.
            def copy_verified(source):
                hasher = hashlib.sha256()
                count = 0
                with source.open('rb') as incoming, temporary.open('wb') as outgoing:
                    while block := incoming.read(65536):
                        if cancel():
                            raise DownloadCancelled('reconstruction cancelled')
                        count += len(block)
                        if count > row['size']:
                            raise SecurityError('reconstruction source grew')
                        start, cpu = time.monotonic(), time.thread_time()
                        hasher.update(block)
                        trace.totals['hash_wall_ms'] += (time.monotonic()-start)*1000
                        trace.totals['hash_cpu_ms'] += (time.thread_time()-cpu)*1000
                        trace.totals['bytes_hashed'] += len(block)
                        trace.totals['bytes_read'] += len(block)
                        outgoing.write(block)
                        trace.totals['bytes_written'] += len(block)
                    outgoing.flush(); os.fsync(outgoing.fileno())
                trace.totals['hash_file_count'] += 1
                return count == row['size'] and hasher.hexdigest() == row['sha256']
            if not copy_verified(source):
                if path in fetch or old_app is None or source != Path(old_app)/path:
                    raise SecurityError('reconstructed file hash mismatch')
                # A locally modified unchanged source is not trusted. Preserve
                # it and recover through the signed asset, as before.
                source = self.download(release.assets[path], cancel=cancel, progress=progress)
                safe_entry(temporary, stage_root)
                if not copy_verified(source):
                    raise SecurityError('reconstructed file hash mismatch')
            safe_entry(destination, stage_root)
            if destination.exists(): raise SecurityError('staging destination changed during copy')
            temporary.rename(destination)
            trace.totals['file_count'] += 1
            trace.totals['copy_file_count'] += 1
            trace.totals['copy_bytes'] += row['size']
            if path not in fetch:
                trace.totals['bytes_reused'] += row['size']
        if not changed_started:
            trace.mark('T08_UNCHANGED_MATERIALIZATION_END')
            trace.mark('T09_CHANGED_MATERIALIZATION_BEGIN')
        trace.mark('T10_CHANGED_MATERIALIZATION_END')
        trace.mark('T11_VERIFY_BEGIN')
        from .prepared_update import seal
        seal(target, release, lambda: self.verify_tree(target, release))
        trace.mark('T12_VERIFY_END')
        trace.mark('T13_TARGET_COMPLETE')
        return target

    def verify_tree(self, target, release):
        guard = _directory_guard.get()
        if guard is None and os.name=='nt':
            from refactor.desktop.delivery.update_tree_security import DirectoryPins
            with DirectoryPins() as pins:
                token = _directory_guard.set(pins)
                try: return self.verify_tree(target,release)
                finally: _directory_guard.reset(token)
        if guard is not None:
            from refactor.desktop.delivery.update_tree_security import normal
            for destination, source in self._hardlinks.items():
                if not destination.is_relative_to(normal(target)): continue
                if not self.accepted_app or self.accepted_app.closed:
                    raise SecurityError('immutable source lease ended; reacquire before reuse')
                guard.allowed_links.update((source,destination))
                safe_entry(source,self.accepted_app.root,missing=False)
                safe_entry(destination,self.runtime_root,missing=False)
                if destination.stat().st_nlink != 2 or not os.path.samefile(source,destination):
                    raise SecurityError('hardlink source binding changed')
        files = set()
        for path in Path(target).rglob('*'):
            safe_entry(path, self.runtime_root, directory=path.is_dir(), missing=False)
            if path.is_file():
                files.add(path.relative_to(target).as_posix())
        if files != set(release.rows):
            raise SecurityError('reconstructed file set mismatch')
        for path, row in release.rows.items():
            item = Path(target) / path
            if item.stat().st_size != row['size'] or hash_file(item) != row['sha256']:
                raise SecurityError('reconstructed tree hash mismatch')

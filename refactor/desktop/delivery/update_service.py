"""Signed update service; no legacy GUI or unsigned release flow."""
from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import time

def install_cache_identity(root):
    import os
    from refactor.desktop.delivery.update_protocol import digest
    return digest(os.path.normcase(str(Path(root).absolute())).encode('utf-8'))

def read_release_witness(cache_root, public_keys, *, channel):
    """Read the existing signed cache witness without constructing a transport."""
    from refactor.desktop.delivery.update_download import safe_entry
    from refactor.desktop.delivery.update_protocol import MAX_METADATA
    from refactor.desktop.delivery.update_protocol import SecurityError
    from refactor.desktop.delivery.update_protocol import verified_witness
    if channel not in ('stable', 'beta'):
        raise SecurityError('unsupported policy channel')
    cache_root = Path(cache_root)
    path = cache_root / ('highest-metadata-' + channel + '.json')
    if not safe_entry(path, cache_root): return None
    if path.stat().st_size > MAX_METADATA:
        raise SecurityError('high-water witness too large')
    return verified_witness(path.read_bytes(), public_keys, channel=channel)

class MatureUpdateService:
    STATES = frozenset(('IDLE', 'CHECKING', 'UP_TO_DATE', 'UPDATE_AVAILABLE',
        'PREPARING', 'DOWNLOADING', 'DOWNLOAD_PAUSED_OR_INTERRUPTED', 'READY_TO_APPLY', 'APPLYING',
        'RESTART_REQUIRED', 'FAILED_RECOVERABLE', 'FAILED_SECURITY'))

    def __init__(self, *, install_root, cache_root, metadata_url, public_keys,
                 current, channel='stable', transport=None, apply_bridge=None,
                 accepted_app=None, runtime_staging_root=None, accepted_source_factory=None):
        import threading
        from refactor.desktop.delivery.update_download import ObjectCache
        from refactor.desktop.delivery.update_download import HTTPTransport
        from refactor.desktop.delivery.update_protocol import SecurityError
        self.install_root = Path(install_root).absolute()
        cache_root = Path(cache_root).absolute()
        # Reject aliasing before overlap checks; lexical spelling must not make
        # a user-data or live-App cache look like an unrelated directory.
        from refactor.desktop.delivery.update_download import safe_entry
        safe_entry(self.install_root,self.install_root,directory=True)
        safe_entry(cache_root,cache_root,directory=True)
        if (cache_root == self.install_root or cache_root.is_relative_to(self.install_root)
                or self.install_root.is_relative_to(cache_root)
                or cache_root == Path('E:/MentorFlowData') or cache_root.is_relative_to(Path('E:/MentorFlowData'))):
            raise SecurityError('update cache overlaps program/user data')
        identity = install_cache_identity(self.install_root)
        self.install_identity = identity
        runtime_staging_root=Path(runtime_staging_root).absolute() if runtime_staging_root else cache_root/'.u'/identity[:12]
        safe_entry(runtime_staging_root,runtime_staging_root,directory=True)
        if (runtime_staging_root == self.install_root or runtime_staging_root.is_relative_to(self.install_root)
                or self.install_root.is_relative_to(runtime_staging_root)
                or runtime_staging_root == Path('E:/MentorFlowData')
                or runtime_staging_root.is_relative_to(Path('E:/MentorFlowData'))):
            raise SecurityError('update preparation must remain outside installed program/user data')
        self.cache = ObjectCache(cache_root / identity, transport or HTTPTransport(metadata_url),
            accepted_app=accepted_app, runtime_root=runtime_staging_root)
        self.metadata_url, self.keys = metadata_url, dict(public_keys)
        if channel not in ('stable', 'beta'):
            raise SecurityError('unsupported update channel')
        self.current, self.channel = current, channel
        self.apply_bridge = apply_bridge
        self._accepted_source_factory=accepted_source_factory
        self._owned_source=False; self._busy=False; self._closed=False
        self.cancel_event = threading.Event()
        self.release, self.plan, self.staged = None, None, None
        self.snapshot = dict(state='IDLE', current=current, channel=channel,
                             last_checked=None, actual_downloaded_bytes=0, message='尚未检查更新')
        self._next_check = 0
        self._failures = 0
        self._load_status()

    def _load_status(self):
        from refactor.desktop.delivery.update_download import safe_entry
        from refactor.desktop.delivery.update_protocol import strict_json
        path = self.cache.root / 'state.json'
        if safe_entry(path, self.cache.root):
            try:
                doc = strict_json(path.read_bytes())
                # Cache can throttle checks but cannot authorize any release/apply.
                import time
                checked = float(doc.get('last_checked') or 0)
                if 0 < checked <= time.time():
                    self.snapshot['last_checked'] = checked
                    self._next_check = min(float(doc.get('next_check') or 0), time.time()+86400)
            except (ValueError, TypeError):
                pass

    def _state(self, name, message, **values):
        assert name in self.STATES
        self.snapshot = {**self.snapshot, **values, 'state': name, 'message': message}

    def _persist_check(self):
        from refactor.desktop.delivery.update_protocol import canonical
        from refactor.desktop.delivery.update_download import safe_entry
        path = self.cache.root / 'state.json'
        temporary = self.cache.root / 'state.pending'
        for candidate in (path, temporary):
            safe_entry(candidate, self.cache.root)
        with temporary.open('wb') as stream:
            stream.write(canonical(dict(last_checked=self.snapshot['last_checked'], next_check=self._next_check)))
            stream.flush()
            import os
            os.fsync(stream.fileno())
        temporary.replace(path)

    def _local_rows(self):
        from refactor.desktop.delivery.update_protocol import MANIFEST_PATH
        from refactor.desktop.delivery.update_protocol import digest
        from refactor.desktop.delivery.update_protocol import strict_json
        from refactor.desktop.delivery.update_download import safe_entry
        app = self.install_root / 'App'
        path = app / MANIFEST_PATH
        if not path.is_file():
            return None
        safe_entry(path, app, missing=False)
        if path.stat().st_size > 16*1024**2:
            from refactor.desktop.delivery.update_protocol import SecurityError
            raise SecurityError('installed manifest too large')
        raw = path.read_bytes()
        try:
            doc = strict_json(raw)
            if doc.get('version') != self.current:
                return None
            rows = {r['path']: r for r in doc['files']}
            rows[MANIFEST_PATH] = dict(path=MANIFEST_PATH, size=len(raw), sha256=digest(raw))
            return rows
        except (KeyError, TypeError, ValueError):
            return None

    def _witness(self):
        from refactor.desktop.delivery.update_protocol import canonical
        from refactor.desktop.delivery.update_protocol import digest
        release = read_release_witness(self.cache.root, self.keys, channel=self.channel)
        return (release.signed['metadata_version'], digest(canonical(release.signed))) if release else (0, '')

    def _remember_release(self, release):
        from refactor.desktop.delivery.update_download import safe_entry
        from refactor.desktop.delivery.update_protocol import SecurityError
        from refactor.desktop.delivery.update_protocol import digest
        from refactor.desktop.delivery.update_protocol import canonical
        from refactor.desktop.delivery.update_protocol import require_monotonic_floor
        import os
        previous = read_release_witness(self.cache.root, self.keys, channel=self.channel)
        if previous is not None: require_monotonic_floor(release, previous)
        epoch, signed_hash = ((previous.signed['metadata_version'], digest(canonical(previous.signed)))
                              if previous is not None else (0, ''))
        incoming = release.signed['metadata_version']
        if incoming < epoch or incoming == epoch and signed_hash != digest(canonical(release.signed)):
            raise SecurityError('metadata replay or same-version equivocation')
        target = self.cache.root / ('highest-metadata-' + self.channel + '.json')
        temporary = target.with_suffix('.pending')
        safe_entry(target, self.cache.root); safe_entry(temporary, self.cache.root)
        with temporary.open('wb') as stream:
            stream.write(release.envelope); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(target)

    @contextmanager
    def _check_stage(self, name):
        started = time.monotonic()
        try:
            yield
        finally:
            elapsed = round((time.monotonic() - started) * 1000, 3)
            self._check_timings[name] = self._check_timings.get(name, 0) + elapsed
            from refactor.desktop.delivery.per_user_install import maintenance_log
            maintenance_log(self.install_root, 'update_check_stage', stage=name, elapsed_ms=elapsed)

    def check(self, *, manual=True, cancel=None):
        import logging
        import time
        from refactor.desktop.delivery.update_download import InstallUpdateLock
        from refactor.desktop.delivery.update_download import UpdateTelemetry
        from refactor.desktop.delivery.update_protocol import MAX_METADATA
        from refactor.desktop.delivery.update_protocol import SecurityError
        from refactor.desktop.delivery.update_protocol import verify_release
        from refactor.desktop.delivery.update_protocol import make_plan
        from refactor.desktop.delivery.update_protocol import rollout_eligible
        from refactor.desktop.delivery.update_protocol import version_key
        if not manual and time.time() < self._next_check:
            return dict(self.snapshot)
        if self._closed: return dict(self.snapshot)
        self.cancel_event.clear()
        try:
            self.telemetry = UpdateTelemetry()
            self.telemetry.mark('T00_UPDATE_BEGIN')
            self._check_timings = {}
            with self._check_stage('check_total'):
                self._state('CHECKING', '正在检查更新…', pointer_status='not_checked', pointer_mismatch=False,
                            witness_write_deferred=False, witness_reconciled=False)
                self.telemetry.mark('T01_METADATA_FETCH_BEGIN')
                with self._check_stage('signed_cache'):
                    previous = read_release_witness(self.cache.root, self.keys, channel=self.channel)
                highest = previous.signed['metadata_version'] if previous else 0
                validated = {}
                def validate_metadata(value):
                    # Transport validates before accepting a mirror. Retain that
                    # same verified result for this check, rather than parsing
                    # and verifying the full manifest repeatedly afterwards.
                    if value not in validated:
                        with self._check_stage('signature_and_manifest'):
                            validated[value] = verify_release(value, self.keys, current=self.current,
                                channel=self.channel, highest_seen=highest)
                    return validated[value]
                raw = self._fetch_metadata(cancel, validate=validate_metadata, cached=previous)
                self.telemetry.totals['bytes_read'] += len(raw)
                release = validate_metadata(raw)
                from refactor.desktop.delivery.runtime_path_budget import budget
                budget(self.install_root,release.rows)
                self.telemetry.mark('T02_METADATA_VERIFIED')
                # Read-only discovery must not queue behind an ongoing large
                # download. A new witness still commits under the existing OS
                # lock and rechecks the latest high-water/mandatory floor there.
                unchanged = previous is not None and previous.envelope == raw
                if not unchanged:
                    from refactor.desktop.delivery.update_protocol import RecoverableError
                    try:
                        with self._check_stage('witness_lock_and_commit'):
                            with InstallUpdateLock(self.cache.root):
                                self._remember_release(release)
                    except RecoverableError:
                        # A busy downloader cannot turn a newly verified
                        # mandatory policy back into "no update". Reconcile
                        # with any witness that won the race; keep discovery
                        # available. Download/apply still commit under the lock
                        # and reverify before materializing or replacing files.
                        latest = read_release_witness(self.cache.root, self.keys, channel=self.channel)
                        if latest is not None:
                            release = verify_release(raw, self.keys, current=self.current,
                                channel=self.channel, previous_envelope=latest.envelope)
                        self.snapshot = {**self.snapshot, 'witness_write_deferred': True}
                        logging.getLogger(__name__).info('verified update policy retained while witness lock busy')
                # The network request above intentionally runs without the
                # install lock. Another updater can therefore commit a newer
                # signed policy while this check is fetching the small pointer
                # and take the unchanged-cache fast path. Reconcile against the
                # atomically replaced witness immediately before classification
                # so an old cached envelope cannot hide a newer mandatory floor.
                with self._check_stage('witness_reconcile'):
                    latest_witness = read_release_witness(
                        self.cache.root, self.keys, channel=self.channel)
                    if (latest_witness is not None
                            and (previous is None or latest_witness.envelope != previous.envelope)):
                        latest_release = verify_release(
                            latest_witness.envelope, self.keys, current=self.current,
                            channel=self.channel, highest_seen=highest,
                            previous_envelope=previous.envelope if previous is not None else None)
                        network_epoch = release.signed['metadata_version']
                        witness_epoch = latest_release.signed['metadata_version']
                        if network_epoch < witness_epoch:
                            # The response was already authenticated against
                            # the older snapshot. It is now stale, so use the
                            # fresh witness, revalidated above for expiry,
                            # current version, channel, signature and floor.
                            release = latest_release
                        elif network_epoch == witness_epoch:
                            if release.envelope != latest_witness.envelope:
                                raise SecurityError('metadata replay or same-version equivocation')
                            release = latest_release
                        else:
                            # Keep a higher network response only when it
                            # extends the newly observed witness monotonically.
                            release = verify_release(
                                raw, self.keys, current=self.current, channel=self.channel,
                                highest_seen=highest,
                                previous_envelope=latest_witness.envelope)
                        self.snapshot = {**self.snapshot,
                                         'pointer_status': 'witness_reconciled',
                                         'witness_reconciled': True}
                with self._check_stage('classification'):
                    plan = make_plan(release, self._local_rows(), self.current)
                eligible = rollout_eligible(release, self.install_identity)
                plan['rollout_eligible'] = eligible
                plan['withdrawn'] = release.signed['withdrawn']
                plan['update'] = plan['update'] and eligible
                from refactor.desktop.delivery.update_protocol import mandatory_floor, startup_mandatory_reason
                plan['mandatory_floor'] = mandatory_floor(release)
                plan['mandatory'] = version_key(self.current) < version_key(plan['mandatory_floor'])
                plan['mandatory_reason'] = startup_mandatory_reason(release, self.current)
                self.release, self.plan, self.staged = release, plan, None
                self._failures = 0
                self._next_check = time.time() + 86400
                self._state('UPDATE_AVAILABLE' if plan['update'] else 'UP_TO_DATE',
                            '发现新版本' if plan['update'] else ('当前已是最新版本' if eligible else '此版本暂未向本设备开放，继续使用当前版本。'),
                            **plan, last_checked=time.time())
                from refactor.desktop.delivery.update_protocol import RecoverableError
                try:
                    with self._check_stage('status_lock_and_write'):
                        with InstallUpdateLock(self.cache.root):
                            self._persist_check()
                except RecoverableError:
                    # This receipt only throttles checks; the signed witness is
                    # already current. Busy housekeeping cannot hide its result.
                    logging.getLogger(__name__).info('update check status write deferred: lock busy')
        except Exception as exc:
            logging.getLogger(__name__).exception('desktop signed update check failed')
            from refactor.desktop.delivery.update_protocol import RecoverableError, DownloadCancelled
            self.release, self.plan, self.staged = None, None, None
            self._failures += 1
            self._next_check = time.time() + min(86400, 60 * 2**min(self._failures, 10))
            self._state('FAILED_SECURITY' if isinstance(exc, SecurityError) else 'FAILED_RECOVERABLE',
                        '更新校验未通过，已阻止更新。' if isinstance(exc, SecurityError) else '暂时无法检查更新，当前版本可继续使用。',
                        last_checked=time.time(),
                        code='UPDATE_CHECK_NETWORK_UNAVAILABLE' if isinstance(exc, RecoverableError) and not isinstance(exc, DownloadCancelled) else '')
            from refactor.desktop.delivery.runtime_path_budget import MESSAGE
            if str(exc)==MESSAGE: self.snapshot={**self.snapshot,'message':MESSAGE}
        self.snapshot = {**self.snapshot, 'check_timings_ms': dict(getattr(self, '_check_timings', {}))}
        return dict(self.snapshot)

    def _fetch_metadata(self, cancel=None, *, validate=None, cached=None):
        import logging
        from refactor.desktop.delivery.update_download import HTTPTransport, retry_wait
        from refactor.desktop.delivery.update_protocol import MAX_METADATA, RecoverableError, DownloadCancelled, SecurityError
        from refactor.desktop.delivery.update_protocol import release_pointer, pointer_matches
        cancelled = cancel or (lambda: self._closed or self.cancel_event.is_set())
        transport = self.cache.transport
        pointer = None
        if (validate is not None and isinstance(transport, HTTPTransport)
                and self.metadata_url.endswith('/stable/stable.json')):
            try:
                url = self.metadata_url.rsplit('/', 1)[0] + '/latest.json'
                with self._check_stage('pointer'):
                    pointer = release_pointer(transport.fetch_metadata(url, 2048, cancel=cancelled,
                                                                     validate=release_pointer, deadline=5.0))
                if cached is not None and pointer_matches(pointer, cached):
                    validate(cached.envelope)  # Recheck expiration, signature and high-water policy NOW.
                    self.snapshot = {**self.snapshot, 'pointer_status':'unchanged_verified_cache'}
                    return cached.envelope
                self.snapshot = {**self.snapshot, 'pointer_status':'changed'}
            except DownloadCancelled:
                raise
            except (RecoverableError, SecurityError, OSError):
                self.snapshot = {**self.snapshot, 'pointer_status':'unavailable_full_manifest'}
                logging.getLogger(__name__).info('update pointer unavailable; checking signed manifest directly')
        for attempt in range(4):  # initial request + three automatic retries
            if cancelled(): raise DownloadCancelled('update check cancelled')
            try:
                with self._check_stage('full_manifest'):
                    raw = (transport.fetch_metadata(self.metadata_url, MAX_METADATA, cancel=cancelled, validate=validate)
                           if isinstance(transport, HTTPTransport) else transport.fetch_metadata(self.metadata_url, MAX_METADATA))
                if pointer is not None and validate is not None:
                    matched = pointer_matches(pointer, validate(raw))
                    self.snapshot = {**self.snapshot, 'pointer_mismatch':not matched,
                                     'pointer_status':'matched_manifest' if matched else 'mismatch_signed_used'}
                    if not matched:
                        logging.getLogger(__name__).warning('update pointer mismatch; using verified signed manifest')
                return raw
            except DownloadCancelled:
                raise
            except RecoverableError:
                if attempt == 3: raise
                retry_wait((2, 4, 8)[attempt], cancelled)

    def download(self, *, progress=None, cancel=None):
        import logging
        from refactor.desktop.delivery.update_download import InstallUpdateLock
        from refactor.desktop.delivery.update_protocol import SecurityError
        from refactor.desktop.delivery.update_protocol import DownloadCancelled
        from refactor.desktop.delivery.update_protocol import verify_release
        if self.release is None or not self.plan or not self.plan['update']:
            return dict(self.snapshot)
        if self._closed: return dict(self.snapshot)
        self._busy=True
        self.cancel_event.clear()
        before = self.cache.transport.bytes_received
        try:
            def cancelled():
                return self.cancel_event.is_set() or (cancel is not None and cancel())
            if cancelled(): raise DownloadCancelled('download cancelled')
            with InstallUpdateLock(self.cache.root):
                if self._closed: raise DownloadCancelled('update service closed')
                self._state('PREPARING', '正在准备更新…')
                if progress is not None: progress(dict(self.snapshot))
                # Lazy worker-only acceptance: never scan the package while
                # constructing the ordinary startup/settings UI.
                if self.cache.accepted_app is None and self._accepted_source_factory is not None:
                    self.cache.accepted_app=self._accepted_source_factory()
                    self._owned_source=True
                release = verify_release(self.release.envelope, self.keys, current=self.current, channel=self.channel,
                                         highest_seen=self._witness()[0])
                self._remember_release(release)
                # Any installed version can reuse equal content. Check-time
                # manifest rows are an estimate only; hash actual App files now.
                self.plan.update(self.cache.local_file_plan(release, self.install_root/'App', self.current,
                    cancel=cancelled, telemetry=getattr(self, 'telemetry', None)))
                self._state('DOWNLOADING', '正在下载变化文件…', actual_downloaded_bytes=0, **self.plan)
                assets = [self.release.assets[p] for p in self.plan['download_paths']]
                self.snapshot = {**self.snapshot, 'total_download_files':len(assets)}
                import time
                last_report = 0
                def report_progress(_):
                    nonlocal last_report
                    self.snapshot = {**self.snapshot, 'actual_downloaded_bytes':self.cache.transport.bytes_received-before,
                                     'downloaded_bytes': self.cache.downloaded_bytes(assets),
                                     'downloaded_files':self.cache.downloaded_files(assets)}
                    done, total = self.snapshot['downloaded_bytes'], self.plan['expected_download_bytes']
                    if self.snapshot['downloaded_files'] == len(assets):
                        self.snapshot = {**self.snapshot, 'state':'PREPARING', 'message':'正在准备新版本…'}
                    else:
                        self.snapshot = {**self.snapshot, 'state':'DOWNLOADING', 'message':
                            f"正在下载 {self.snapshot['downloaded_files']}/{len(assets)} 个文件（{done/1048576:.2f} MB / {total/1048576:.2f} MB）"}
                    if progress is not None and (_ == 0 or time.monotonic() - last_report >= .2):
                        last_report = time.monotonic()
                        progress(dict(self.snapshot))
                report_progress(0)
                self.staged = self.cache.reconstruct(release, self.plan, self.install_root/'App',
                    cancel=cancelled, progress=report_progress,
                    telemetry=getattr(self, 'telemetry', None))
                actual = self.cache.transport.bytes_received-before
                self._state('READY_TO_APPLY', '已下载好，点一下就能更新。',
                            downloaded_files=len(assets), total_download_files=len(assets),
                            downloaded_bytes=self.plan['expected_download_bytes'],
                            actual_downloaded_bytes=actual,
                            saved_bytes=max(0, self.plan['full_bytes']-actual),
                            saving_percent=100 * max(0, 1-actual/max(1,self.plan['full_bytes'])))
                if getattr(self, 'telemetry', None):
                    self.telemetry.mark('T14_READY_TO_APPLY')
                if progress is not None: progress(dict(self.snapshot))
        except Exception as exc:
            logging.getLogger(__name__).exception('desktop incremental download failed')
            self.staged = None
            state = 'FAILED_SECURITY' if isinstance(exc, SecurityError) else 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
            downloaded = self.cache.downloaded_bytes([self.release.assets[p] for p in self.plan['download_paths']])
            total = self.plan['expected_download_bytes']
            from refactor.desktop.delivery.update_protocol import DownloadCancelled
            self._state(state, '更新校验未通过，已阻止应用。' if isinstance(exc, SecurityError) else
                        ('下载已暂停，可点继续。' if isinstance(exc, DownloadCancelled) else f'网络不稳，已下载 {downloaded}/{total} 字节，稍后会自动继续。'),
                        downloaded_bytes=downloaded, cancelled=isinstance(exc, DownloadCancelled),
                        actual_downloaded_bytes=self.cache.transport.bytes_received-before)
            from refactor.desktop.delivery.runtime_path_budget import MESSAGE
            if str(exc)==MESSAGE: self.snapshot={**self.snapshot,'message':MESSAGE}
        finally:
            self._busy=False
            if self._closed: self._close_source()
        return dict(self.snapshot)

    def cancel_download(self):
        self.cancel_event.set()

    def _close_source(self):
        if self._owned_source and self.cache.accepted_app is not None:
            self.cache.accepted_app.close()
            self._owned_source=False

    def close(self):
        self._closed=True; self.cancel_download()
        if not self._busy: self._close_source()

    def apply_and_restart(self):
        from refactor.desktop.delivery.update_protocol import SecurityError
        import logging
        try:
            return self._apply_verified()
        except Exception as exc:
            logging.getLogger(__name__).exception('protected update handoff failed')
            self._state('FAILED_SECURITY' if isinstance(exc,SecurityError) else 'FAILED_RECOVERABLE',
                        '更新安全校验未通过，未继续应用。' if isinstance(exc,SecurityError) else '更新未完成，请继续使用或恢复已验证的旧版本。')
            raise

    def _apply_verified(self):
        from refactor.desktop.delivery.update_protocol import RecoverableError
        from refactor.desktop.delivery.update_protocol import verify_release
        from refactor.desktop.delivery.update_download import InstallUpdateLock
        if not self.staged or self.apply_bridge is None:
            raise RecoverableError('protected update/restart bridge is not qualified/configured')
        with InstallUpdateLock(self.cache.root):
            # Observe the latest signed withdrawal/rollout epoch before consented
            # application. Offline is recoverable, never permission to bypass it.
            from refactor.desktop.delivery.update_protocol import MAX_METADATA
            from refactor.desktop.delivery.update_protocol import rollout_eligible
            from refactor.desktop.delivery.update_protocol import SecurityError
            from refactor.desktop.delivery.update_protocol import digest
            from refactor.desktop.delivery.update_protocol import canonical
            fresh = verify_release(self._fetch_metadata(),
                self.keys, current=self.current, channel=self.channel, highest_seen=self._witness()[0])
            self._remember_release(fresh)
            if not rollout_eligible(fresh, self.install_identity) or digest(canonical(fresh.signed)) != digest(canonical(self.release.signed)):
                raise SecurityError('release changed or withdrawn; recheck required')
            release = verify_release(self.release.envelope, self.keys, current=self.current, channel=self.channel,
                                     highest_seen=self._witness()[0])
            self._remember_release(release)
            self.cache.verify_tree(self.staged, release)
            self._state('APPLYING', '正在准备更新…')
            # Bridge must independently verify metadata after elevation, retain
            # previous known-good and restart via ordinary-user shell, not Setup.
            result = self.apply_bridge(self.install_root, self.staged, release.envelope)
            self._state('RESTART_REQUIRED', '等待更新重启与健康确认')
            return result

def configured_mature_service(*, version_manifest=None, install_root=None):
    """Build-provisioned feed only. Never infer a production trust key/endpoint."""
    import base64
    import sys
    from refactor.desktop.delivery.version import VERSION_MANIFEST
    manifest = version_manifest or VERSION_MANIFEST
    config = manifest.get('update', {})
    if config.get('protocol') != 'mentorflow.signed-file-release.v1':
        return None
    keys = config.get('public_keys')
    if not keys or not config.get('metadata_url'):
        return None
    root = Path(install_root) if install_root else Path(sys.executable).absolute().parent.parent
    from refactor.desktop.delivery.update_tree_security import AcceptedApp
    from refactor.desktop.delivery.update_download import HTTPTransport
    from refactor.desktop.delivery.install_product import configured
    from refactor.desktop.delivery.per_user_install import is_install
    per_user = is_install(root)
    if per_user:
        from refactor.desktop.delivery.per_user_install import keys as compiled_keys
        public_keys = compiled_keys()
    else:
        public_keys = {k:base64.b64decode(v,validate=True) for k,v in keys.items()}
    local_profile = Path(__import__('os').environ['LOCALAPPDATA'])
    default_cache = (local_profile/configured().app_id/'Updates' if configured().uat
                     else local_profile/'MentorFlowUpdates')
    runtime_staging = None
    if configured().uat and not config.get('staging_root'):
        # Metadata stays in the product namespace. The real package's long
        # dependency paths need a shorter reconstruction root; retain both
        # product and install identity without weakening the 240-unit gate.
        import os
        from refactor.desktop.delivery.update_protocol import digest
        identity = digest((configured().app_id + '\0' + os.path.normcase(str(root.absolute()))).encode())[:12]
        runtime_staging = local_profile / '.mfu' / identity
    return MatureUpdateService(install_root=root,
        cache_root=config.get('staging_root') or default_cache,
        runtime_staging_root=runtime_staging,
        metadata_url=config['metadata_url'], public_keys=public_keys,
        current=manifest['version'], channel=manifest.get('channel','stable'),
        transport=HTTPTransport(config['metadata_url'],fixture_http=configured().uat),
        accepted_source_factory=None if per_user else lambda:AcceptedApp.protected(root))

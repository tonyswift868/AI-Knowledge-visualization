"""Opt-in MentorFlow update checks and verified installer launching.

The package verifier remains the single implementation of GitHub release
validation and download staging.  This module is deliberately a small desktop
boundary around it: importing this module never performs network I/O, and a
network request only happens when :func:`check_for_update` or
:func:`download_update` is called after an explicit user action.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from scripts.verify_windows_package import PackageError, load_version, sha256_file, stage_update


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT_ROOT / "mentorflow-version.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class UpdateError(RuntimeError):
    """An actionable update-check, staging, or installer error."""


def _version_file(path: Path | str | None) -> Path:
    candidate = Path(path) if path is not None else VERSION_FILE
    return candidate.expanduser().resolve()


def _config(path: Path | str | None = None) -> tuple[Path, dict[str, Any]]:
    version_path = _version_file(path)
    try:
        manifest = load_version(version_path)
    except PackageError as exc:
        raise UpdateError(str(exc)) from exc
    update = manifest.get("update")
    if not isinstance(update, dict) or not bool(update.get("enabled")):
        raise UpdateError("官方更新未启用；请先配置可信的 GitHub release 来源")
    owner = str(update.get("owner") or "").strip()
    repo = str(update.get("repo") or "").strip()
    if not owner or not repo:
        raise UpdateError("官方更新缺少 GitHub owner/repo 配置")
    return version_path, update


def check_for_update(
    version_file: Path | str | None = None,
    release_json: Path | str | None = None,
) -> dict[str, Any]:
    """Check the configured official release without downloading anything.

    ``release_json`` is intentionally exposed for offline fixture tests and
    review.  Normal desktop calls omit it and therefore call GitHub only from
    the explicit Check button's worker task.
    """

    version_path, update = _config(version_file)
    try:
        return stage_update(
            version_path,
            owner=str(update["owner"]),
            repo=str(update["repo"]),
            release_json=Path(release_json).expanduser().resolve() if release_json else None,
            staging_root=None,
            download=False,
        )
    except PackageError as exc:
        raise UpdateError(str(exc)) from exc


def download_update(
    version_file: Path | str | None = None,
    release_json: Path | str | None = None,
    staging_root: Path | str | None = None,
) -> dict[str, Any]:
    """Download and hash-verify the configured release into E: staging.

    The verifier never replaces files in the running application directory;
    this function returns the verified staged path for an explicit install
    confirmation in the UI.
    """

    version_path, update = _config(version_file)
    try:
        return stage_update(
            version_path,
            owner=str(update["owner"]),
            repo=str(update["repo"]),
            release_json=Path(release_json).expanduser().resolve() if release_json else None,
            staging_root=Path(staging_root).expanduser().resolve() if staging_root else None,
            download=True,
        )
    except PackageError as exc:
        raise UpdateError(str(exc)) from exc


def _validate_staged_metadata(path: Path, *, expected_sha256: str | None) -> str:
    if not path.is_file():
        raise UpdateError(f"已校验的安装器不存在：{path}")
    if path.suffix.casefold() != ".exe":
        raise UpdateError(f"当前资源不是 Windows 安装器；已暂存到：{path}")
    try:
        digest = sha256_file(path).lower()
    except PackageError as exc:
        raise UpdateError(str(exc)) from exc
    if expected_sha256:
        expected = str(expected_sha256).strip().lower()
        if not _SHA256_RE.fullmatch(expected) or digest != expected:
            raise UpdateError("安装器 SHA-256 校验失败，拒绝启动")
    metadata_path = path.parent / "update-staging.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"缺少有效的更新暂存清单：{metadata_path}") from exc
    if not isinstance(metadata, dict):
        raise UpdateError("更新暂存清单格式无效，拒绝启动")
    metadata_path_value = str(metadata.get("staged_path") or "")
    try:
        if Path(metadata_path_value).expanduser().resolve() != path.resolve():
            raise UpdateError("更新暂存清单与安装器路径不一致，拒绝启动")
    except (OSError, RuntimeError) as exc:
        raise UpdateError("无法验证更新暂存清单路径，拒绝启动") from exc
    metadata_digest = str(metadata.get("sha256") or "").strip().lower()
    if not _SHA256_RE.fullmatch(metadata_digest) or metadata_digest != digest:
        raise UpdateError("更新暂存清单中的 SHA-256 与安装器不一致，拒绝启动")
    return digest


def launch_verified_installer(
    staged_path: Path | str,
    *,
    expected_sha256: str | None = None,
    launcher: Callable[[list[str], Path], Any] | None = None,
) -> dict[str, Any]:
    """Launch one verified ``.exe`` using an argument list, never a shell.

    The caller is responsible for obtaining explicit confirmation before
    calling this function.  ``launcher`` exists solely for deterministic tests
    and receives ``([absolute_path], parent_directory)``.
    """

    path = Path(staged_path).expanduser().absolute()
    _validate_staged_metadata(path, expected_sha256=expected_sha256)
    command = [str(path)]
    try:
        if launcher is not None:
            launcher(command, path.parent)
        else:
            subprocess.Popen(
                command,
                cwd=str(path.parent),
                shell=False,
                close_fds=True,
            )
    except (OSError, ValueError) as exc:
        raise UpdateError(f"无法启动已校验的安装器：{exc}") from exc
    return {"launched": True, "staged_path": str(path), "sha256": sha256_file(path)}


# Concise aliases useful to callers that describe the action as "stage".
check_update = check_for_update
stage_download = download_update


__all__ = [
    "UpdateError",
    "check_for_update",
    "check_update",
    "download_update",
    "stage_download",
    "launch_verified_installer",
]


# Mature update path extends this existing facade/cache; the legacy explicit
# source checker above is NOT the normal in-app installation action anymore.
class MatureUpdateService:
    STATES = frozenset(('IDLE', 'CHECKING', 'UP_TO_DATE', 'UPDATE_AVAILABLE',
        'PREPARING', 'DOWNLOADING', 'DOWNLOAD_PAUSED_OR_INTERRUPTED', 'READY_TO_APPLY', 'APPLYING',
        'RESTART_REQUIRED', 'FAILED_RECOVERABLE', 'FAILED_SECURITY'))

    def __init__(self, *, install_root, cache_root, metadata_url, public_keys,
                 current, channel='stable', transport=None, apply_bridge=None,
                 accepted_app=None, runtime_staging_root=None, accepted_source_factory=None):
        import os
        import threading
        from .update_download import ObjectCache, HTTPTransport
        from .update_protocol import digest, SecurityError
        self.install_root = Path(install_root).absolute()
        cache_root = Path(cache_root).absolute()
        # Reject aliasing before overlap checks; lexical spelling must not make
        # a user-data or live-App cache look like an unrelated directory.
        from .update_download import safe_entry
        safe_entry(self.install_root,self.install_root,directory=True)
        safe_entry(cache_root,cache_root,directory=True)
        if (cache_root == self.install_root or cache_root.is_relative_to(self.install_root)
                or self.install_root.is_relative_to(cache_root)
                or cache_root == Path('E:/MentorFlowData') or cache_root.is_relative_to(Path('E:/MentorFlowData'))):
            raise SecurityError('update cache overlaps program/user data')
        identity = digest(os.path.normcase(str(self.install_root)).encode('utf-8'))
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
        from .update_download import safe_entry
        from .update_protocol import strict_json
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
        from .update_protocol import canonical
        from .update_download import safe_entry
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
        from .update_protocol import MANIFEST_PATH, digest, strict_json
        from .update_download import safe_entry
        app = self.install_root / 'App'
        path = app / MANIFEST_PATH
        if not path.is_file():
            return None
        safe_entry(path, app, missing=False)
        if path.stat().st_size > 16*1024**2:
            from .update_protocol import SecurityError
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
        from .update_download import safe_entry
        from .update_protocol import witness_version
        path = self.cache.root / ('highest-metadata-' + self.channel + '.json')
        if safe_entry(path, self.cache.root):
            if path.stat().st_size > 16*1024**2:
                from .update_protocol import SecurityError
                raise SecurityError('high-water witness too large')
            return witness_version(path.read_bytes(), self.keys, channel=self.channel)
        return 0, ''

    def _remember_release(self, release):
        from .update_download import safe_entry
        from .update_protocol import SecurityError, digest, canonical
        import os
        epoch, signed_hash = self._witness()
        incoming = release.signed['metadata_version']
        if incoming < epoch or incoming == epoch and signed_hash != digest(canonical(release.signed)):
            raise SecurityError('metadata replay or same-version equivocation')
        target = self.cache.root / ('highest-metadata-' + self.channel + '.json')
        temporary = target.with_suffix('.pending')
        safe_entry(target, self.cache.root); safe_entry(temporary, self.cache.root)
        with temporary.open('wb') as stream:
            stream.write(release.envelope); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(target)

    def check(self, *, manual=True):
        import logging
        import time
        from .update_download import InstallUpdateLock, UpdateTelemetry
        from .update_protocol import MAX_METADATA, SecurityError, verify_release, make_plan, rollout_eligible
        if not manual and time.time() < self._next_check:
            return dict(self.snapshot)
        try:
            self.telemetry = UpdateTelemetry()
            self.telemetry.mark('T00_UPDATE_BEGIN')
            with InstallUpdateLock(self.cache.root):
                self._state('CHECKING', '正在检查更新…')
                self.telemetry.mark('T01_METADATA_FETCH_BEGIN')
                raw = self.cache.transport.fetch_metadata(self.metadata_url, MAX_METADATA)
                self.telemetry.totals['bytes_read'] += len(raw)
                release = verify_release(raw, self.keys, current=self.current, channel=self.channel,
                                         highest_seen=self._witness()[0])
                from scripts.runtime_path_budget import budget
                budget(self.install_root,release.rows)
                self.telemetry.mark('T02_METADATA_VERIFIED')
                self._remember_release(release)
                plan = make_plan(release, self._local_rows(), self.current)
                eligible = rollout_eligible(release, self.install_identity)
                plan['rollout_eligible'] = eligible
                plan['withdrawn'] = release.signed['withdrawn']
                plan['update'] = plan['update'] and eligible
                self.release, self.plan, self.staged = release, plan, None
                self._failures = 0
                self._next_check = time.time() + 86400
                self._state('UPDATE_AVAILABLE' if plan['update'] else 'UP_TO_DATE',
                            '发现新版本' if plan['update'] else ('当前已是最新版本' if eligible else '此版本暂未向本设备开放，继续使用当前版本。'),
                            **plan, last_checked=time.time())
                self._persist_check()
        except Exception as exc:
            logging.getLogger(__name__).exception('desktop signed update check failed')
            self.release, self.plan, self.staged = None, None, None
            self._failures += 1
            self._next_check = time.time() + min(86400, 60 * 2**min(self._failures, 10))
            self._state('FAILED_SECURITY' if isinstance(exc, SecurityError) else 'FAILED_RECOVERABLE',
                        '更新校验未通过，已阻止更新。' if isinstance(exc, SecurityError) else '暂时无法检查更新，当前版本可继续使用。',
                        last_checked=time.time())
            from scripts.runtime_path_budget import MESSAGE
            if str(exc)==MESSAGE: self.snapshot={**self.snapshot,'message':MESSAGE}
        return dict(self.snapshot)

    def download(self):
        import logging
        from .update_download import InstallUpdateLock
        from .update_protocol import SecurityError, DownloadCancelled, verify_release
        if self.release is None or not self.plan or not self.plan['update']:
            return dict(self.snapshot)
        if self._closed: return dict(self.snapshot)
        self._busy=True
        self.cancel_event.clear()
        before = self.cache.transport.bytes_received
        try:
            with InstallUpdateLock(self.cache.root):
                if self._closed: raise DownloadCancelled('update service closed')
                self._state('PREPARING', '正在准备更新…')
                # Lazy worker-only acceptance: never scan the package while
                # constructing the ordinary startup/settings UI.
                if self.cache.accepted_app is None and self._accepted_source_factory is not None:
                    self.cache.accepted_app=self._accepted_source_factory()
                    self._owned_source=True
                release = verify_release(self.release.envelope, self.keys, current=self.current, channel=self.channel,
                                         highest_seen=self._witness()[0])
                self._remember_release(release)
                self._state('DOWNLOADING', '正在下载变化文件…', actual_downloaded_bytes=0)
                def progress(_):
                    self.snapshot = {**self.snapshot, 'actual_downloaded_bytes':self.cache.transport.bytes_received-before}
                self.staged = self.cache.reconstruct(release, self.plan, self.install_root/'App',
                    cancel=self.cancel_event.is_set, progress=progress,
                    telemetry=getattr(self, 'telemetry', None))
                actual = self.cache.transport.bytes_received-before
                self._state('READY_TO_APPLY', '下载和校验完成，可稍后更新。',
                            actual_downloaded_bytes=actual,
                            saved_bytes=max(0, self.plan['full_bytes']-actual),
                            saving_percent=100 * max(0, 1-actual/max(1,self.plan['full_bytes'])))
                if getattr(self, 'telemetry', None):
                    self.telemetry.mark('T14_READY_TO_APPLY')
        except Exception as exc:
            logging.getLogger(__name__).exception('desktop incremental download failed')
            self.staged = None
            state = 'FAILED_SECURITY' if isinstance(exc, SecurityError) else 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
            self._state(state, '更新校验未通过，已阻止应用。' if isinstance(exc, SecurityError) else '下载已暂停，当前版本不受影响，可稍后继续。',
                        actual_downloaded_bytes=self.cache.transport.bytes_received-before)
            from scripts.runtime_path_budget import MESSAGE
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
        from .update_protocol import SecurityError
        import logging
        try:
            return self._apply_verified()
        except Exception as exc:
            logging.getLogger(__name__).exception('protected update handoff failed')
            self._state('FAILED_SECURITY' if isinstance(exc,SecurityError) else 'FAILED_RECOVERABLE',
                        '更新安全校验未通过，未继续应用。' if isinstance(exc,SecurityError) else '更新未完成，请继续使用或恢复已验证的旧版本。')
            raise

    def _apply_verified(self):
        from .update_protocol import RecoverableError, verify_release
        from .update_download import InstallUpdateLock
        if not self.staged or self.apply_bridge is None:
            raise RecoverableError('protected update/restart bridge is not qualified/configured')
        with InstallUpdateLock(self.cache.root):
            # Observe the latest signed withdrawal/rollout epoch before consented
            # application. Offline is recoverable, never permission to bypass it.
            from .update_protocol import MAX_METADATA, rollout_eligible, SecurityError, digest, canonical
            fresh = verify_release(self.cache.transport.fetch_metadata(self.metadata_url, MAX_METADATA),
                self.keys, current=self.current, channel=self.channel, highest_seen=self._witness()[0])
            self._remember_release(fresh)
            if not rollout_eligible(fresh, self.install_identity) or digest(canonical(fresh.signed)) != digest(canonical(self.release.signed)):
                raise SecurityError('release changed or withdrawn; recheck required')
            release = verify_release(self.release.envelope, self.keys, current=self.current, channel=self.channel,
                                     highest_seen=self._witness()[0])
            self._remember_release(release)
            self.cache.verify_tree(self.staged, release)
            self._state('APPLYING', '正在准备受保护更新…')
            # Bridge must independently verify metadata after elevation, retain
            # previous known-good and restart via ordinary-user shell, not Setup.
            result = self.apply_bridge(self.install_root, self.staged, release.envelope)
            self._state('RESTART_REQUIRED', '等待更新重启与健康确认')
            return result


def configured_mature_service(*, version_manifest=None, install_root=None):
    """Build-provisioned feed only. Never infer a production trust key/endpoint."""
    import base64
    import sys
    from .version import VERSION_MANIFEST
    manifest = version_manifest or VERSION_MANIFEST
    config = manifest.get('update', {})
    if config.get('protocol') != 'mentorflow.signed-file-release.v1':
        return None
    keys = config.get('public_keys')
    if not keys or not config.get('metadata_url'):
        return None
    root = Path(install_root) if install_root else Path(sys.executable).absolute().parent.parent
    from .update_tree_security import AcceptedApp
    from .update_download import HTTPTransport
    from scripts.install_product import configured
    return MatureUpdateService(install_root=root,
        cache_root=config.get('staging_root', r'E:\MentorFlowUpdates'),
        metadata_url=config['metadata_url'], public_keys={k:base64.b64decode(v,validate=True) for k,v in keys.items()},
        current=manifest['version'], channel=manifest.get('channel','stable'),
        transport=HTTPTransport(config['metadata_url'],fixture_http=configured().uat),
        accepted_source_factory=lambda:AcceptedApp.protected(root))

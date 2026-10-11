"""Public consumer lifecycle facade. Receipts describe state, never authority.

UI calls run on its existing worker. No UI, credentials or elevation are created
here. A downloaded update may be deferred and revalidated on a normal launch.
"""
from pathlib import Path
import os
import threading

from refactor.desktop.delivery.update_protocol import canonical
from refactor.desktop.delivery.update_protocol import digest
from refactor.desktop.delivery.update_protocol import strict_json
from refactor.desktop.delivery.update_protocol import RecoverableError
from refactor.desktop.delivery.update_protocol import SecurityError
from refactor.desktop.delivery import per_user_install as install

PENDING = 'pending-update.json'
SCHEMA = 'mentorflow.per-user-pending.v1'
PENDING_MESSAGE = '更新已下载好，关闭软件后下次打开时自动安装。'
STATES = frozenset(('UPDATE_DOWNLOADED', 'PENDING_INSTALL', 'APPLYING', 'CURRENT_HEALTHY', 'PREVIOUS_HEALTHY'))
_ending = threading.Event()

class NoFeedConfigured(RecoverableError):
    """An unbound product is usable, but cannot discover updates yet."""


def _no_feed_result():
    return dict(state='FAILED_RECOVERABLE', code='NO_FEED', no_feed=True,
                message='更新源尚未配置，您可以继续使用当前版本。')

STARTUP_STATES = frozenset(('HEALTHY', 'OPTIONAL_UPDATE', 'MANDATORY_UPDATE',
                            'REPAIR_REQUIRED', 'FATAL_UNRECOVERABLE'))


def _startup_result(state, **details):
    details.setdefault('current', '')
    details.setdefault('latest', '')
    details.setdefault('min_supported_version', details.get('mandatory_floor', ''))
    details['mandatory_reason'] = ((details.get('mandatory_reason') or 'support_floor')
                                   if state == 'MANDATORY_UPDATE' else '')
    messages = {
        'HEALTHY': '可以正常登录。',
        'OPTIONAL_UPDATE': '有新版本可用，可以现在更新，或稍后在 设置 → 关于 更新。',
        'MANDATORY_UPDATE': '当前版本需要更新后才能继续',
        'REPAIR_REQUIRED': '安装需要修复，您可以修复安装或更新到最新版本。',
        'FATAL_UNRECOVERABLE': '修复运行时也无法安全加载。请使用可信的 MentorFlow 安装包修复，学习资料不会被删除。',
    }
    return dict(state=state, message=messages[state], login_allowed=state in ('HEALTHY', 'OPTIONAL_UPDATE'),
                shell='login' if state in ('HEALTHY','OPTIONAL_UPDATE','MANDATORY_UPDATE') else 'repair', **details)


def _installed_identity(root):
    # A migrated Inno install retains its flat executable. Losing STATE must
    # not silently reinterpret that canonical installation as the old layout.
    if (root / 'App').exists() and not (root / install.STATE).is_file():
        raise SecurityError('canonical installation state missing')
    return install.installed(root)


def _canonical_identity_candidate(root):
    """Read-only recovery evidence, never directory/receipt authority.

    Fixed signed sidecars must bind the surviving App manifest (and its version
    configuration when present). Updater's older pinned release can identify
    App only if it passes that same binding. Other payload damage is repaired
    through the existing full reconstruction, never accepted as healthy here.
    """
    from refactor.desktop.delivery.update_protocol import MANIFEST_PATH
    app = install.plain(root / 'App')
    if not app.is_dir(): raise SecurityError('canonical App identity missing')
    names = (install.CURRENT, install.HIGHEST, 'previous/release.json', install.UPDATER_RELEASE)
    for name in (*names, install.STATE):
        path = install.plain(root / name)
        if path.exists() and not path.is_file():
            raise SecurityError('identity record is not a regular file')
    public_keys = install.keys()
    # This remains the transaction's anti-replay witness. Never discard a bad
    # witness or overwrite a higher epoch while restoring current identity.
    if (root / install.HIGHEST).exists():
        install.historical(install.read(root / install.HIGHEST), public_keys)
    for name in names:
        try:
            release = install.historical(install.read(root / name), public_keys)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            continue
        try:
            if release.signed['withdrawn']:
                raise SecurityError('withdrawn identity candidate')
            subset = [MANIFEST_PATH]
            if 'mentorflow-version.json' in release.rows:
                subset.append('mentorflow-version.json')
            install.verify_tree(app, release, subset=subset)
            if len(subset) > 1:
                manifest = strict_json(install.read(app / 'mentorflow-version.json'))
                if not isinstance(manifest, dict) or manifest.get('version') != release.version:
                    raise SecurityError('recovery configuration version mismatch')
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            # A surviving authentic CURRENT cannot be replaced by another
            # generation just because a different sidecar happens to match.
            if name == install.CURRENT: raise
            continue
        install.verify_recovery_runtime(root, release)
        return release
    raise SecurityError('no signed candidate binds the canonical App identity')


def _startup_policy_evidence(root, current, service=None):
    """Read existing signed witnesses; cache status/receipts are not authority."""
    from refactor.desktop.delivery.update_service import install_cache_identity
    from refactor.desktop.delivery.update_service import read_release_witness
    from refactor.desktop.delivery.update_protocol import verified_witness
    evidence = [current]
    highest = install.plain(root / install.HIGHEST)
    if highest.exists():
        raw = install.read(highest)
        evidence.append(current if raw == current.envelope else
                        verified_witness(raw, install.keys(), channel=current.signed['channel']))
    cache = getattr(getattr(service, 'cache', None), 'root', None)
    if cache is None:
        cache = (Path(os.environ['LOCALAPPDATA']) / install.configured().app_id / 'Updates'
                 / install_cache_identity(root))
    saved = read_release_witness(cache, install.keys(), channel=current.signed['channel'])
    if saved is not None: evidence.append(saved)
    return evidence


def get_startup_update_state(root, *, service=None, check_feed=True, full=True, cancel=None):
    """Single authority for startup; no account API, token or UI dependency.

    Network errors cannot invent a mandatory gate or erase one already proved
    by signed metadata. Historical witnesses retain policy, never apply rights.
    """
    try:
        root = install.target(root)
        try:
            release = _installed_identity(root)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            release = _canonical_identity_candidate(root)
            return _startup_result('REPAIR_REQUIRED', current=release.version,
                                   reason='canonical_identity_recovery_required')
        if full: install.verify_recovery_runtime(root, release)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return _startup_result('FATAL_UNRECOVERABLE', reason='recovery_runtime_unverified')
    try:
        if full:
            install.verify_gui(root, executable=install.app_path(root)/'MentorFlow.exe')
            install.verify_payload(root, release)
        else:
            install.check_launch_files(root, release)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return _startup_result('REPAIR_REQUIRED', current=release.version, reason='payload_verification_failed')
    from refactor.desktop.delivery.update_protocol import mandatory_floor
    from refactor.desktop.delivery.update_protocol import startup_version_policy
    from refactor.desktop.delivery.update_protocol import startup_mandatory_reason
    from refactor.desktop.delivery.update_protocol import version_key
    from refactor.desktop.delivery.update_protocol import verify_release
    from refactor.desktop.delivery.update_protocol import require_monotonic_floor
    try:
        evidence = _startup_policy_evidence(root, release, service)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return _startup_result('REPAIR_REQUIRED', current=release.version, reason='signed_policy_witness_unverified')
    def retained_policy(feed):
        floor = max((mandatory_floor(item) for item in evidence), key=version_key)
        if version_key(release.version) < version_key(floor):
            compatible = [item for item in evidence
                          if version_key(mandatory_floor(item)) >= version_key(floor)]
            latest = max(compatible, key=lambda item: version_key(item.version))
            return _startup_result('MANDATORY_UPDATE', current=release.version,
                                   latest=latest.version, target=latest.version, mandatory_floor=floor,
                                   mandatory_reason=startup_mandatory_reason(latest, release.version,
                                                                           witnesses=evidence),
                                   release_notes=latest.signed['release_notes'], feed=feed)
        return _startup_result('HEALTHY', current=release.version, mandatory_floor=floor, feed=feed)
    if not check_feed: return retained_policy('NOT_CHECKED')
    owned = service is None
    try:
        service = service if service is not None else one_click_service(root)
        result = service.check(manual=True, cancel=cancel) if cancel is not None else service.check(manual=True)
        # check() may have persisted a valid witness before an unrelated status
        # write failed. Re-read it even when no downloadable release is returned.
        evidence.extend(_startup_policy_evidence(root, release, service))
        if result.get('state') not in ('UPDATE_AVAILABLE','UP_TO_DATE') or service.release is None:
            return retained_policy('UNAVAILABLE_RECOVERABLE')
        prior = max(evidence, key=lambda item: item.signed['metadata_version'])
        latest = verify_release(service.release.envelope, install.keys(), current=release.version,
                                channel=release.signed['channel'], previous_envelope=prior.envelope)
        for item in evidence: require_monotonic_floor(latest, item)
        state = startup_version_policy(latest, release.version)
        if state == 'OPTIONAL_UPDATE' and not result.get('update'):
            state = 'HEALTHY'
        return _startup_result(state, current=release.version, latest=latest.version, target=latest.version,
                               mandatory_floor=mandatory_floor(latest),
                               mandatory_reason=startup_mandatory_reason(latest, release.version,
                                                                       witnesses=evidence),
                               release_notes=latest.signed['release_notes'], feed='VERIFIED')
    except NoFeedConfigured:
        return retained_policy('NO_FEED')
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return retained_policy('UNAVAILABLE_RECOVERABLE')
    finally:
        if owned and service is not None: service.close()


def bootstrap_legacy_identity(root, *, envelope=None):
    """Restore signed identity for canonical or older flat-Inno installations.

    The corrected installer supplies current-release.json outside payload.
    Older flat installs can obtain the exact build-provisioned BASE descriptor.
    Offline failure leaves the installation untouched and remains recoverable.
    """
    root = install.target(root)
    try:
        return _installed_identity(root)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        if not (root / 'App').exists() and install.is_install(root): raise
    if (root / 'App').exists():
        _canonical_identity_candidate(root)  # reject before even creating a lock
        from refactor.desktop.delivery.update_download import InstallUpdateLock
        with InstallUpdateLock(root):
            release = _canonical_identity_candidate(root)
            install.atomic(root / install.CURRENT, release.envelope)
            install.atomic(root / install.STATE, canonical(dict(
                schema=install.SCHEMA, mode='per_user', app_id=install.configured().app_id)))
        return release
    if install.app_path(root) != root: raise SecurityError('unrecognized legacy layout')
    if envelope is None:
        from mentorflow_delivery_authority import RECOVERY_RELEASE_URL, ONE_CLICK_TEST_FIXTURE
        url = static_feed_url({'update':{'UPDATE_FEED_URL':RECOVERY_RELEASE_URL}}, test_fixture=ONE_CLICK_TEST_FIXTURE)
        from refactor.desktop.delivery.update_download import HTTPTransport
        from refactor.desktop.delivery.update_protocol import MAX_METADATA
        envelope = HTTPTransport(url, signed_static_http=install.configured().uat).fetch_metadata(url, MAX_METADATA)
    release = install.historical(envelope, install.keys())
    # The signed recovery runtime is independent of any damaged App file.
    install.verify_recovery_runtime(root, release)
    install.atomic(root / install.CURRENT, envelope)
    return release


def repair_installation(root, progress=None, cancel=None, *, service=None, pid=None, hwnd_hex=None):
    """Reuse signed downloader and transaction; same-version repair is explicit.

    Full reconstruction never reuses corrupt installed bytes. Metadata is
    independently refreshed immediately before the existing swap/health gate.
    """
    owned = service is None
    try:
        root = install.target(root)
        bootstrap_legacy_identity(root)
        import sys
        if pid is None and Path(sys.executable).absolute() == install.app_path(root)/'MentorFlow.exe':
            pid = os.getpid()
        if unsafe_teardown(): return dict(state='PENDING_INSTALL', destructive_apply=False, relaunched=False)
        service = service if service is not None else one_click_service(root, repair=True)
        snapshot = service.check(manual=True)
        if service.release is None: return snapshot
        from refactor.desktop.delivery.update_protocol import make_plan
        from refactor.desktop.delivery.update_protocol import rollout_eligible
        if not rollout_eligible(service.release, service.install_identity):
            raise RecoverableError('signed repair release is not available')
        install.admit(root, service.release.envelope, install.keys(), repair=True)
        service.plan = {**make_plan(service.release, None, service.current), 'update':True}
        snapshot = service.download(progress=progress, cancel=cancel)
        if snapshot.get('state') != 'READY_TO_APPLY': return snapshot
        if cancel is not None and cancel(): return dict(state='PENDING_INSTALL', destructive_apply=False)
        # In-App callers hand off before closing themselves, just like update.
        if pid == os.getpid():
            return install.handoff_repair(root, hwnd_hex=hwnd_hex)
        if bool(pid) != bool(hwnd_hex): raise ValueError('complete App identity required')
        def bridge(root, stage, envelope):
            if unsafe_teardown(): raise RecoverableError('repair deferred during session teardown')
            critical = False
            def copied(_):
                if not critical and cancel is not None and cancel():
                    raise RecoverableError('修复已取消；安装文件未替换。')
            def before_swap():
                nonlocal critical
                copied(0)
                critical = True
                if progress is not None:
                    progress(dict(state='APPLYING', critical=True, message='正在处理，请稍候…'))
                # The window's shared cancellation event may change while the
                # queued UI signal is pending. Check it again before mutation.
                if cancel is not None and cancel():
                    raise RecoverableError('修复已取消；安装文件未替换。')
                if pid: install.request_app_shutdown(root, pid, hwnd_hex)
                else: install.require_app_stopped(root)
            try:
                return install.transact(root, stage, envelope, repair=True,
                                        progress=copied, before_swap=before_swap)
            finally:
                if progress is not None and critical:
                    progress(dict(critical=False))
        service.apply_bridge = bridge
        result = service.apply_and_restart()
        return {**result, 'state':'CURRENT_HEALTHY', 'repair':True}
    except NoFeedConfigured:
        return _no_feed_result()
    except SecurityError:
        return dict(state='FAILED_SECURITY', message='修复文件未通过安全校验，未继续应用。')
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return dict(state='FAILED_RECOVERABLE', message='修复暂未完成，请检查网络后重试。原有学习资料未更改。')
    finally:
        if owned and service is not None: service.close()


def unsafe_teardown(reason='normal'):
    if reason not in ('normal', 'next_launch', 'shutdown', 'logoff', 'unsafe'):
        raise ValueError('unknown exit reason')
    import ctypes
    from refactor.desktop.delivery.per_user_runtime_session import session_ending
    return reason in ('shutdown', 'logoff', 'unsafe') or _ending.is_set() or session_ending() or bool(ctypes.windll.user32.GetSystemMetrics(0x2000))


def attach_shutdown_guard(application):
    """Keep the Windows shutdown/logoff notification independent of update UI."""
    from PySide6.QtCore import QAbstractNativeEventFilter
    import ctypes
    from ctypes import wintypes
    class Guard(QAbstractNativeEventFilter):
        def nativeEventFilter(self, event_type, message):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message in (0x11, 0x16):  # WM_QUERYENDSESSION / WM_ENDSESSION
                _ending.set()
            return False, 0
    guard = Guard()
    application.installNativeEventFilter(guard)
    application._per_user_shutdown_guard = guard


def configured_service(root):
    root = install.target(root)
    release = install.installed(root)
    install.verify_tree(root/'App', release, subset=['mentorflow-version.json'])
    from refactor.desktop.delivery.update_service import configured_mature_service
    manifest = strict_json(install.read(root/'App/mentorflow-version.json'))
    service = configured_mature_service(version_manifest=manifest, install_root=root)
    if service is None: raise RecoverableError('signed update feed is not configured')
    return service


def static_feed_url(manifest, *, test_fixture=False):
    """The one-click endpoint is build-provisioned, never a browser/local server."""
    import ipaddress
    from urllib.parse import urlsplit
    config = manifest.get('update', {})
    if not isinstance(config, dict): raise SecurityError('invalid update configuration')
    if 'enabled' in config and type(config['enabled']) is not bool:
        raise SecurityError('invalid update enabled flag')
    if config.get('enabled') is False:
        raise NoFeedConfigured('UPDATE_FEED_URL is disabled')
    url = config.get('UPDATE_FEED_URL') or config.get('metadata_url')
    if config.get('UPDATE_FEED_URL') and config.get('metadata_url') and config['UPDATE_FEED_URL'] != config['metadata_url']:
        raise SecurityError('conflicting update feed configuration')
    if not isinstance(url, str) or not url:
        raise NoFeedConfigured('UPDATE_FEED_URL is not configured')
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise SecurityError('fixed HTTP(S) update feed required')
    host = parsed.hostname.lower().rstrip('.')
    try: address = ipaddress.ip_address(host)
    except ValueError: address = None
    local = host == 'localhost' or host.endswith('.localhost') or (address and (address.is_loopback or address.is_unspecified))
    if local and not (test_fixture and install.configured().uat):
        raise SecurityError('one-click update cannot depend on a localhost feed')
    if parsed.scheme == 'http' and not install.configured().uat:
        raise SecurityError('production update feed requires HTTPS')
    if manifest.get('channel', 'stable') != 'stable':
        raise SecurityError('one-click update requires stable channel')
    # A signed directory binding names the public mount, not a JSON response.
    # Keep legacy exact-file bindings unchanged; never probe or guess endpoints.
    return url + 'stable/stable.json' if parsed.path.endswith('/') else url


def one_click_service(root, *, repair=False):
    root = install.target(root)
    release = install.installed(root)
    app = install.app_path(root)
    try:
        install.verify_tree(app, release, subset=['mentorflow-version.json'])
        manifest = strict_json(install.read(app / 'mentorflow-version.json'))
    except (OSError, RuntimeError, ValueError):
        if not repair: raise
        from mentorflow_delivery_authority import RECOVERY_FEED_URL
        manifest = dict(version=release.version, channel='stable', update=dict(
            protocol='mentorflow.signed-file-release.v1', UPDATE_FEED_URL=RECOVERY_FEED_URL))
    if manifest.get('version') != release.version:
        raise SecurityError('installed update configuration version mismatch')
    try:
        from mentorflow_delivery_authority import ONE_CLICK_TEST_FIXTURE
    except ImportError:
        ONE_CLICK_TEST_FIXTURE = False
    fixture = ONE_CLICK_TEST_FIXTURE is True and install.configured().uat
    url = static_feed_url(manifest, test_fixture=fixture)
    if manifest['update'].get('protocol') != 'mentorflow.signed-file-release.v1':
        raise SecurityError('signed file release protocol required')
    from refactor.desktop.delivery.update_download import HTTPTransport
    from refactor.desktop.delivery.update_service import MatureUpdateService
    # Never compile a developer's staging path into the distribution. The cache
    # is ordinary-user-local and separated per installation by MatureUpdateService.
    local = Path(os.environ['LOCALAPPDATA'])
    identity = digest((install.configured().app_id + '\0' + os.path.normcase(str(root))).encode())[:12]
    return MatureUpdateService(install_root=root,
        cache_root=local / install.configured().app_id / 'Updates',
        runtime_staging_root=local / '.mfu' / identity,
        metadata_url=url, public_keys=install.keys(), current=release.version,
        channel='stable', transport=HTTPTransport(url, signed_static_http=install.configured().uat))


def check_latest(root, *, service=None, cancel=None):
    owned = service is None
    try:
        service = service if service is not None else one_click_service(root)
        return service.check(manual=True, cancel=cancel) if cancel is not None else service.check(manual=True)
    except NoFeedConfigured: return _no_feed_result()
    except SecurityError as error:
        install.maintenance_log(root, 'check_latest_failed', detail=str(error))
        return dict(state='FAILED_SECURITY', message='更新清单未通过安全校验，未继续应用。')
    except (OSError, RuntimeError, ValueError) as error:
        install.maintenance_log(root, 'check_latest_failed', detail=str(error))
        return dict(state='FAILED_RECOVERABLE', message='暂时无法检查更新，请稍后重试。', no_feed=service is None)
    finally:
        if owned and service is not None: service.close()


def download_latest(root, progress=None, cancel=None, *, service=None):
    owned = service is None
    try:
        service = service if service is not None else one_click_service(root)
        return prepare_update(root, service=service, progress=progress, cancel=cancel)
    except NoFeedConfigured: return _no_feed_result()
    except SecurityError as error:
        install.maintenance_log(root, 'download_latest_failed', detail=str(error))
        return dict(state='FAILED_SECURITY', message='更新文件未通过安全校验，未继续应用。')
    except (OSError, RuntimeError, ValueError) as error:
        install.maintenance_log(root, 'download_latest_failed', detail=str(error))
        return dict(state='FAILED_RECOVERABLE', message='更新下载暂时无法完成，请稍后重试。', no_feed=service is None)
    finally:
        if owned and service is not None: service.close()


def apply_and_relaunch(root, *, pid=None, hwnd_hex=None, reason='normal', service=None,
                       wait_for_exit=False):
    """In-App calls hand off before closing their own process; native callers wait.

    The caller captures HWND on the GUI thread. An in-App result of APPLYING is
    dispatch only, never a fabricated health PASS; the coordinator owns completion.
    """
    pid = os.getpid() if pid is None and reason == 'normal' else pid
    if pid == os.getpid():
        return install.handoff_pending(root, hwnd_hex=hwnd_hex, reason=reason)
    return apply_update(root, pid=pid, hwnd_hex=hwnd_hex, reason=reason, service=service,
                        service_factory=one_click_service, wait_for_exit=wait_for_exit)


def update_to_latest(root, progress=None, cancel=None, *, pid=None, hwnd_hex=None,
                     reason='normal', service=None):
    """One consent: signed stable discovery -> download -> apply -> relaunch.

    Blocking work belongs on the UI's worker or the stable native coordinator,
    never on the Qt event loop. There is no browser, shell script or Setup fallback.
    """
    owned = service is None
    try:
        service = service if service is not None else one_click_service(root)
        prepared = download_latest(root, progress, cancel, service=service)
        if prepared.get('state') != 'UPDATE_DOWNLOADED': return prepared
        if cancel is not None and cancel():
            pending = read_pending(root)
            install.atomic(Path(root) / PENDING, canonical({**pending, 'state': 'PENDING_INSTALL'}))
            return {**prepared, 'state': 'PENDING_INSTALL', 'cancelled': True, 'destructive_apply': False}
        if progress is not None: progress({**prepared, 'state': 'APPLYING'})
        result = apply_and_relaunch(root, pid=pid, hwnd_hex=hwnd_hex, reason=reason, service=service)
        return {**prepared, **result}
    except NoFeedConfigured:
        return _no_feed_result()
    except SecurityError as error:
        install.maintenance_log(root, 'update_to_latest_failed', detail=str(error))
        return dict(state='FAILED_SECURITY', error=type(error).__name__, message='更新文件未通过安全校验，未继续应用。')
    except (OSError, RuntimeError, ValueError) as error:
        install.maintenance_log(root, 'update_to_latest_deferred', detail=str(error))
        try:
            pending = read_pending(root)
            if pending['state'] in ('UPDATE_DOWNLOADED', 'PENDING_INSTALL', 'APPLYING'):
                install.atomic(Path(root) / PENDING, canonical({**pending, 'state': 'PENDING_INSTALL'}))
                return dict(state='PENDING_INSTALL', destructive_apply=False, message=PENDING_MESSAGE)
        except (OSError, RuntimeError, ValueError):
            pass
        return dict(state='FAILED_RECOVERABLE', error=type(error).__name__, message='更新暂时无法完成，请稍后重试。')
    finally:
        if owned and service is not None: service.close()


def remember_download(root, stage, envelope, *, state='UPDATE_DOWNLOADED'):
    if state not in ('UPDATE_DOWNLOADED', 'PENDING_INSTALL'): raise ValueError('invalid pending state')
    root = install.target(root); stage = install.plain(stage)
    _, release = install.admit(root, envelope, install.keys())
    install.verify_tree(stage, release)
    doc = dict(schema=SCHEMA, state=state, stage=str(stage), target=release.version,
               release_sha256=digest(envelope))
    install.atomic(root/PENDING, canonical(doc))
    return dict(state=state, target=release.version)


def read_pending(root):
    doc = strict_json(install.read(Path(root)/PENDING))
    if set(doc) != {'schema', 'state', 'stage', 'target', 'release_sha256'} or doc['schema'] != SCHEMA or doc['state'] not in STATES:
        raise SecurityError('unknown pending update state')
    return doc


def discard_stale_pending(root):
    """An installed/equal version cannot be pending; do this before any network."""
    from refactor.desktop.delivery.update_protocol import version_key
    root = install.target(root)
    if not (root / PENDING).exists(): return False
    pending = read_pending(root)
    current = install.installed(root)
    if version_key(pending['target']) > version_key(current.version): return False
    (root / PENDING).unlink()
    install.maintenance_log(root, 'pending_obsolete', target=pending['target'], current=current.version)
    # The following background cleanup owns cache deletion. Invalidating this
    # hint must not perform a gigabyte walk on the foreground launch path.
    return True


def _pending_stage(root, pending, service):
    """Rebind verification to the fixed legacy staging root when resuming it.

    The UI and old launcher used different staging roots.  Do not relocate or
    trust the pending bytes: the existing guarded tree verifier still hashes
    every file against freshly verified metadata before any swap.
    """
    root = install.target(root)
    stage = install.plain(pending['stage'])
    if stage.is_relative_to(install.plain(service.cache.runtime_root)):
        return stage
    legacy_root = install.plain(root.parent / '.MentorFlow-runtime' / '.mfu')
    if not stage.is_relative_to(legacy_root) or stage.name != 'App':
        raise SecurityError('pending staging path is outside the approved cache')
    service.cache.runtime_root = legacy_root
    return stage


def status(root):
    root = install.target(root)
    current = install.installed(root)
    result = dict(state='CURRENT_HEALTHY', current=current.version)
    journal = root/install.JOURNAL
    if journal.exists():
        phase = strict_json(install.read(journal)).get('phase')
        if phase == 'ROLLED_BACK': result['state'] = 'PREVIOUS_HEALTHY'
        elif phase != 'HEALTHY': result['state'] = 'APPLYING'
    if (root/PENDING).exists():
        pending = read_pending(root)
        if pending['state'] not in ('CURRENT_HEALTHY', 'PREVIOUS_HEALTHY'):
            result.update(state=pending['state'], target=pending['target'])
    return result


def prepare_update(root, *, service=None, progress=None, cancel=None):
    """Existing signed downloader, Range/resume/cache/full-fallback unchanged.

    Passing an already configured service lets UI own progress, cancel and retry.
    The frozen CLI always creates its service from the installed signed config.
    """
    owned = service is None; service = service or configured_service(root)
    try:
        snapshot = service.check(manual=True)
        if snapshot['state'] != 'UPDATE_AVAILABLE': return snapshot
        snapshot = service.download(progress=progress, cancel=cancel)
        if snapshot['state'] != 'READY_TO_APPLY': return snapshot
        pending = remember_download(root, service.staged, service.release.envelope)
        return {**snapshot, **pending, 'ui_state': 'READY_TO_APPLY'}
    finally:
        if owned: service.close()


def apply_update(root, *, pid=None, hwnd_hex=None, reason='normal', service=None,
                 service_factory=None, wait_for_exit=False):
    """Fresh discovery + cache validation, then the existing real transaction.

    next_launch is called by the stable launcher BEFORE starting App. Normal
    apply supplies the exact running App PID/HWND; no guessed process is closed.
    """
    root = install.target(root); pending = read_pending(root)
    if pending['state'] in ('CURRENT_HEALTHY', 'PREVIOUS_HEALTHY'):
        raise RecoverableError('no pending update')
    if unsafe_teardown(reason):
        install.atomic(root/PENDING, canonical({**pending, 'state': 'PENDING_INSTALL'}))
        return dict(state='PENDING_INSTALL', destructive_apply=False, relaunched=False)
    if bool(pid) != bool(hwnd_hex): raise ValueError('complete App identity required')
    if pid is None and reason != 'next_launch': raise ValueError('normal apply requires running App identity')
    if pid is None: install.require_app_stopped(root)
    owned = service is None; service = service or (service_factory or configured_service)(root)
    applied = False
    try:
        snapshot = service.check(manual=True)  # never trust a stale cached envelope
        if snapshot['state'] != 'UPDATE_AVAILABLE':
            raise RecoverableError('pending target is no longer available; check update again')
        if digest(service.release.envelope) != pending['release_sha256']:
            raise SecurityError('pending release changed; download the current signed release')
        stage = _pending_stage(root, pending, service)
        pending = {**pending, 'stage': str(stage)}
        if digest(install.read(stage.parent/'release.json')) != pending['release_sha256']:
            raise SecurityError('cached release changed')
        service.cache.verify_tree(stage, service.release)
        service.staged = stage
        def bridge(root, stage, envelope):
            nonlocal applied
            if unsafe_teardown(reason): raise RecoverableError('teardown: update remains pending')
            install.atomic(root/PENDING, canonical({**pending, 'state': 'APPLYING'}))
            applied = True
            if pid:
                close = (lambda: install.wait_for_app_exit(root, pid, hwnd_hex)
                         if wait_for_exit else
                         install.request_app_shutdown(root, pid, hwnd_hex))
            else:
                close = lambda: install.require_app_stopped(root)
            return install.transact(root, stage, envelope, before_swap=close)
        service.apply_bridge = bridge
        result = service.apply_and_restart()  # independently refreshes metadata again
        # The signed transaction already records HEALTHY. A completed download
        # is no longer pending, even if background housekeeping meets a lock.
        (root/PENDING).unlink(missing_ok=True)
        return {**result, 'state': 'CURRENT_HEALTHY'}
    except install.AppShutdownTimeout:
        install.atomic(root/PENDING, canonical({**pending, 'state': 'PENDING_INSTALL'}))
        return dict(state='PENDING_INSTALL', destructive_apply=False, relaunched=False,
                    message='更新已下载好，关闭软件后下次打开时自动安装。')
    except Exception:
        journal = strict_json(install.read(root/install.JOURNAL)) if (root/install.JOURNAL).exists() else {}
        # A prior transaction's ROLLED_BACK journal must not consume a newly
        # downloaded pending update after an offline/fresh-metadata failure.
        recovery = root/'last-recovery.json'
        proof = strict_json(install.read(recovery)) if recovery.exists() else {}
        recovered = (applied and journal.get('phase') == 'ROLLED_BACK'
                     and proof.get('attempt') == journal.get('attempt')
                     and proof.get('health', {}).get('ready') is True)
        state = 'PREVIOUS_HEALTHY' if recovered else 'PENDING_INSTALL'
        install.atomic(root/PENDING, canonical({**pending, 'state': state}))
        raise
    finally:
        if owned: service.close()


def resume_pending_on_normal_launch(root, *, service=None, **options):
    if discard_stale_pending(root):
        return dict(state='NO_PENDING_UPDATE', obsolete=True)
    options.setdefault('reason', 'next_launch')
    return apply_update(root, service=service, service_factory=one_click_service, **options)
launch_updated_app = install.launch_health
record_health = install.notify_health
rollback_previous = install.recover

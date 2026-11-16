"""Cold-install bridge: signed feed -> verified object tree -> install transaction."""
from __future__ import annotations

import os
from pathlib import Path


def _production_service(root):
    # Feed and release keys are provisioned into the runtime at build time.
    from mentorflow_delivery_authority import RECOVERY_FEED_URL
    from refactor.desktop.delivery.install_product import configured
    from refactor.desktop.delivery.per_user_install import installed, is_install, keys
    from refactor.desktop.delivery.update_download import HTTPTransport
    from refactor.desktop.delivery.update_service import MatureUpdateService
    from refactor.desktop.delivery.update_protocol import digest

    product = configured()
    local = Path(os.environ['LOCALAPPDATA'])
    identity = digest((product.app_id + '\0' + os.path.normcase(str(root))).encode())[:12]
    existing_version = installed(root).version if is_install(root) else '0.0.0'
    try:
        from mentorflow_delivery_authority import ONE_CLICK_TEST_FIXTURE
    except ImportError:
        ONE_CLICK_TEST_FIXTURE = False
    return MatureUpdateService(install_root=root,
        cache_root=local / product.app_id / 'Updates',
        runtime_staging_root=local / '.mfu' / identity,
        metadata_url=RECOVERY_FEED_URL, public_keys=keys(), current=existing_version,
        channel='stable', transport=HTTPTransport(RECOVERY_FEED_URL,
            signed_static_http=ONE_CLICK_TEST_FIXTURE is True and product.uat))


def bootstrap_install(root, status_file=None, cancel_file=None, result_file=None,
                      close_after_health=False, *, service_factory=None, health=None,
                      transaction=None, close_app=None):
    """Install current signed stable to root. Dependencies are injectable for tests only."""
    from refactor.desktop.delivery.per_user_install import atomic, canonical, transact
    from refactor.desktop.delivery.update_protocol import DownloadCancelled, RecoverableError

    root = Path(root).absolute()
    service = None
    cancelled = lambda: bool(cancel_file and Path(cancel_file).exists())
    previous_progress = [-1]
    repair = False

    def report(percent, stage, error='', version=''):
        if status_file:
            content = ('[install]\r\npercent=' + str(max(0, min(100, int(percent)))) +
                       '\r\nstage=' + str(stage).replace('\r', ' ').replace('\n', ' ') +
                       '\r\nerror=' + str(error).replace('\r', ' ').replace('\n', ' ') +
                       '\r\nversion=' + str(version) + '\r\n')
            atomic(Path(status_file), content.encode('utf-16'))

    def progress(value):
        if cancelled():
            raise DownloadCancelled('安装已取消')
        if value != previous_progress[0]:
            previous_progress[0] = value
            report(value, '正在安装 MentorFlow…')

    try:
        from refactor.desktop.delivery.per_user_install import validate_target
        root = Path(validate_target(root)['root'])
        service = (service_factory or _production_service)(root)
        report(2, '正在获取已签名版本…')
        checked = service.check(manual=True, cancel=cancelled)
        if cancelled() or checked.get('cancelled'):
            raise DownloadCancelled('安装已取消')
        if checked.get('state') not in ('UPDATE_AVAILABLE', 'UP_TO_DATE'):
            from refactor.desktop.delivery.update_protocol import SecurityError
            error = SecurityError if checked.get('state') == 'FAILED_SECURITY' else RecoverableError
            raise error(checked.get('message') or '无法获取已验证的签名版本')
        if service.release is None:
            raise RecoverableError('没有已验证的签名版本')
        if checked.get('state') == 'UP_TO_DATE':
            from refactor.desktop.delivery.per_user_install import (admit, installed, is_install,
                verify_recovery_runtime, verify_tree, launch_health, keys)
            if not is_install(root):
                raise RecoverableError('没有可用的现有安装，不能跳过安装事务')
            current = installed(root, service.keys)
            if service.release.version != current.version:
                raise RecoverableError('当前签名版本暂不可安装到此设备')
            try:
                verify_tree(root / 'App', current)
                verify_recovery_runtime(root, current)
                proof = (health or launch_health)(root, current)
                if not isinstance(proof, dict) or proof.get('ready') is not True:
                    raise RecoverableError('existing installation did not become ready')
                result = dict(status='HEALTHY', version=current.version, health=proof)
                if close_after_health:
                    closer = close_app
                    if closer is None:
                        from refactor.desktop.delivery.per_user_install import request_app_shutdown
                        closer = request_app_shutdown
                    closer(root, proof['pid'], proof['hwnd_hex'])
                    result['installer_finish'] = 'AWAITING_EXPLICIT_LAUNCH'
                report(100, '现有安装健康检查完成', version=current.version)
                if result_file:
                    atomic(Path(result_file), canonical(result))
                return result
            except DownloadCancelled:
                raise
            except Exception:
                # Same-version repair is authorized only by this exact signed release.
                from refactor.desktop.delivery.update_protocol import make_plan, rollout_eligible
                if not rollout_eligible(service.release, service.install_identity):
                    raise RecoverableError('签名修复版本暂不可用')
                admit(root, service.release.envelope, keys(), repair=True)
                service.plan = {**make_plan(service.release, None, service.current), 'update': True}
                repair = True
                report(15, '现有安装校验未通过，正在下载签名文件修复…')
        report(15, '已验证版本 ' + service.release.version)

        def download_progress(snapshot):
            if cancelled():
                raise DownloadCancelled('安装已取消')
            percent = 15 + int(60 * snapshot.get('downloaded_bytes', 0) /
                               max(1, (service.plan or {}).get('expected_download_bytes', 1)))
            report(percent, snapshot.get('message') or '正在下载并校验文件…')

        downloaded = service.download(progress=download_progress, cancel=cancelled)
        if cancelled() or downloaded.get('cancelled'):
            raise DownloadCancelled('安装已取消')
        if service.staged is None:
            raise RecoverableError(downloaded.get('message') or '版本文件下载或校验失败')
        report(78, '全部文件已校验，正在安装…')
        from refactor.desktop.delivery.per_user_install import is_install, require_app_stopped
        result = (transaction or transact)(root, service.staged, service.release.envelope,
            public_keys=service.keys, health=health, progress=progress,
            repair=repair, refresh_runtime=True,
            before_swap=lambda: require_app_stopped(root))
        version = result.get('version')
        if result.get('status') != 'HEALTHY' or not version:
            raise RecoverableError('安装健康检查未确认')
        if close_after_health:
            proof = result.get('health') or {}
            closer = close_app
            if closer is None:
                from refactor.desktop.delivery.per_user_install import request_app_shutdown
                closer = request_app_shutdown
            closer(root, proof['pid'], proof['hwnd_hex'])
            result['installer_finish'] = 'AWAITING_EXPLICIT_LAUNCH'
        report(100, '安装完成', version=version)
        result.update(status='HEALTHY', version=version)
        if result_file:
            atomic(Path(result_file), canonical(result))
        return result
    except Exception as error:
        if result_file:
            atomic(Path(result_file), canonical(dict(status='FAILED', error=type(error).__name__, detail=str(error))))
        report(0, '安装未完成', error=str(error))
        raise
    finally:
        if service is not None and hasattr(service, 'close'):
            service.close()

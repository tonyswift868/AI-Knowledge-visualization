"""One-consent offline contract tests: signed inert files; never run an App."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.test_per_user_install import case, ready
from scripts import per_user_install as install
from scripts import per_user_update_core as core
from desktop.update_service import MatureUpdateService
from desktop.update_download import HTTPTransport
from desktop.update_protocol import SecurityError, RecoverableError, canonical, strict_json


def setup(case, monkeypatch):
    base, root, feed, publish = case
    monkeypatch.setattr(install, 'keys', lambda: feed.keys)
    source, raw = publish('1.4.0', 1)
    install.transact(root, source, raw, health=ready)
    source, raw = publish('1.4.1', 2)
    feed.signed['base_versions'] = ['1.4.0']; feed.sign(feed.signed)
    service = MatureUpdateService(install_root=root, cache_root=base/'cache',
        metadata_url=feed.origin+'/stable.json', public_keys=feed.keys, current='1.4.0',
        transport=HTTPTransport(feed.origin, fixture_http=True))
    monkeypatch.setattr(install, 'launch_health', ready)
    return root, service


def test_one_call_download_apply_and_relaunch_with_single_consent(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    events = []; progress = []
    monkeypatch.setattr(install, 'request_app_shutdown', lambda *args: events.append(('close', args[1:])))
    monkeypatch.setattr(install, 'launch_health', lambda r, v: (events.append(('launch', v.version)) or ready(r,v)))
    result = core.update_to_latest(root, progress.append, pid=9876, hwnd_hex='0001', service=service)
    assert result['state'] == 'CURRENT_HEALTHY'
    assert result['update_path'] == 'FILE_LEVEL_INCREMENTAL'
    assert result['actual_downloaded_bytes'] < result['full_bytes']/10
    assert events == [('close', (9876, '0001')), ('launch', '1.4.1')]
    assert progress and any(p['state'] == 'DOWNLOADING' for p in progress)
    assert install.installed(root).version == '1.4.1'
    assert (root/'previous/App').is_dir()
    assert not any('url' in key.lower() for key in result)


def test_in_app_call_dispatches_before_own_shutdown(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    from desktop.windows_process_peer import ProcessPeer
    monkeypatch.setattr(ProcessPeer, 'require', lambda *a, **kw: {})
    monkeypatch.setattr(ProcessPeer, 'visible_responsive_window', lambda *a: None)
    commands = []
    monkeypatch.setattr(install.subprocess, 'Popen', lambda args, **kw: (commands.append((args,kw)) or SimpleNamespace(pid=54321)))
    monkeypatch.setattr(install, 'request_app_shutdown', lambda *a: pytest.fail('caller must survive dispatch'))
    result = core.update_to_latest(root, hwnd_hex='0001', service=service)
    assert result['state'] == 'APPLYING' and result['coordinator_started']
    assert install.installed(root).version == '1.4.0'
    assert len(commands) == 1
    command, options = commands[0]
    assert command[0] == str(root/'Updater/UserRuntime.exe') and command[1] == 'apply-latest'
    assert str(os.getpid()) in command and options['creationflags'] == install.subprocess.CREATE_NO_WINDOW
    assert not options.get('shell')


def test_in_app_tampered_coordinator_never_executes(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    from desktop.windows_process_peer import ProcessPeer
    monkeypatch.setattr(ProcessPeer, 'require', lambda *a, **kw: {})
    monkeypatch.setattr(ProcessPeer, 'visible_responsive_window', lambda *a: None)
    (root/'Updater/UserRuntime.exe').write_bytes(b'tampered executable')
    monkeypatch.setattr(install.subprocess, 'Popen', lambda *a, **kw: pytest.fail('must not execute tampered helper'))
    result = core.update_to_latest(root, hwnd_hex='0001', service=service)
    assert result['state'] == 'FAILED_SECURITY'
    assert install.installed(root).version == '1.4.0'


@pytest.mark.parametrize('reason', ['shutdown','logoff','unsafe'])
def test_one_click_teardown_defers_then_next_normal_launch_revalidates(case, monkeypatch, reason):
    root, service = setup(case, monkeypatch)
    monkeypatch.setattr(install, 'request_app_shutdown', lambda *a: pytest.fail('unsafe close'))
    result = core.update_to_latest(root, pid=9876, hwnd_hex='0001', reason=reason, service=service)
    assert result['state'] == 'PENDING_INSTALL' and not result['destructive_apply']
    assert install.installed(root).version == '1.4.0'
    result = core.resume_pending_on_normal_launch(root, service=service)
    assert result['state'] == 'CURRENT_HEALTHY'
    assert install.installed(root).version == '1.4.1'


def test_bad_target_automatically_restores_and_relaunches_previous(case, monkeypatch):
    root, service = setup(case, monkeypatch); calls = []
    monkeypatch.setattr(install, 'request_app_shutdown', lambda *a: None)
    def health(root, release):
        calls.append(release.version)
        if release.version == '1.4.1': raise RuntimeError('inert target health failure')
        return ready(root, release)
    monkeypatch.setattr(install, 'launch_health', health)
    result = core.update_to_latest(root, pid=9876, hwnd_hex='0001', service=service)
    assert result['state'] == 'FAILED_RECOVERABLE'
    assert calls == ['1.4.1', '1.4.0']
    assert install.installed(root).version == '1.4.0'
    assert core.read_pending(root)['state'] == 'PREVIOUS_HEALTHY'


@pytest.mark.parametrize('mode', ['offline','cancel_before','cancel_during'])
def test_failed_or_cancelled_download_keeps_running_app(case, monkeypatch, mode):
    root, service = setup(case, monkeypatch)
    before = (root/install.CURRENT).read_bytes(); cancel = [mode == 'cancel_before']
    monkeypatch.setattr(install, 'request_app_shutdown', lambda *a: pytest.fail('old App must keep running'))
    if mode == 'offline':
        monkeypatch.setattr(service.cache.transport, 'fetch_metadata', lambda *a: (_ for _ in ()).throw(RecoverableError('offline')))
    def progress(snapshot):
        if mode == 'cancel_during': cancel[0] = True
    result = core.update_to_latest(root, progress, lambda: cancel[0], pid=9876, hwnd_hex='0001', service=service)
    assert result['state'] in ('FAILED_RECOVERABLE','DOWNLOAD_PAUSED_OR_INTERRUPTED','PENDING_INSTALL')
    assert (root/install.CURRENT).read_bytes() == before


def test_offline_next_launch_still_opens_current_not_stale_previous(case, monkeypatch):
    import sys
    from desktop.windows_process_peer import ProcessPeer
    root, service = setup(case, monkeypatch)
    assert core.download_latest(root, service=service)['state'] == 'UPDATE_DOWNLOADED'
    journal = strict_json((root/install.JOURNAL).read_bytes())
    (root/install.JOURNAL).write_bytes(canonical({**journal, 'phase':'ROLLED_BACK'}))
    (root/'last-recovery.json').write_bytes(canonical(dict(attempt=journal['attempt'],health=dict(ready=True,pid=1))))
    monkeypatch.setattr(core, 'one_click_service', lambda _: service)
    monkeypatch.setattr(service.cache.transport, 'fetch_metadata', lambda *a: (_ for _ in ()).throw(RecoverableError('offline')))
    monkeypatch.setattr(ProcessPeer, 'inspect', lambda _: dict(elevated=False, integrity_rid=8192))
    launches = []
    monkeypatch.setattr(install, 'launch_health', lambda r,v: (launches.append(v.version) or ready(r,v)))
    monkeypatch.setattr(sys,'argv',['UserRuntime','launch','--root',str(root)])
    assert install.main()['status'] == 'HEALTHY'
    assert launches == ['1.4.0']
    assert core.read_pending(root)['state'] == 'PENDING_INSTALL'


@pytest.mark.parametrize('url', ['file:///E:/feed.json','E:/feed.json','http://localhost/stable.json',
    'https://127.0.0.1/stable.json','http://[::1]/stable.json','https://user:pass@updates.example.org/feed',
    'https://updates.example.org/feed?token=secret','https://updates.example.org/feed#part'])
def test_non_static_or_secret_feed_rejected(url):
    with pytest.raises(SecurityError): core.static_feed_url({'update':{'UPDATE_FEED_URL':url}})


def test_build_provisioned_feed_alias_and_production_tls():
    url = 'https://updates.example.org/mentorflow/stable.json'
    assert core.static_feed_url({'update':{'UPDATE_FEED_URL':url}}) == url
    assert core.static_feed_url({'update':{'metadata_url':url}}) == url
    with pytest.raises(SecurityError): core.static_feed_url({'update':{'UPDATE_FEED_URL':url,'metadata_url':url+'/other'}})
    with pytest.raises(SecurityError): core.static_feed_url({'update':{'UPDATE_FEED_URL':url.replace('https:','http:')}})
    with pytest.raises(SecurityError): core.static_feed_url({'channel':'beta','update':{'UPDATE_FEED_URL':url}})


def test_missing_feed_is_recoverable_without_launch(case, monkeypatch):
    _, root, _, _ = case
    monkeypatch.setattr(core,'one_click_service',lambda _: (_ for _ in ()).throw(RecoverableError('feed not configured')))
    assert core.update_to_latest(root)['state'] == 'FAILED_RECOVERABLE'
    assert core.check_latest(root)['state'] == 'FAILED_RECOVERABLE'
    assert core.download_latest(root)['state'] == 'FAILED_RECOVERABLE'


def test_product_cannot_enable_loopback_test_marker(monkeypatch):
    from scripts.install_product import Product
    monkeypatch.setattr(install, 'configured', lambda: Product())
    with pytest.raises(SecurityError):
        core.static_feed_url({'update':{'UPDATE_FEED_URL':'http://127.0.0.1/stable.json'}},test_fixture=True)


def test_signed_uat_http_keeps_origin_and_production_transport_tls():
    static = HTTPTransport('http://updates.example.org', signed_static_http=True)
    static.validate_url('http://updates.example.org/objects/a')
    with pytest.raises(SecurityError): static.validate_url('http://other.example.org/objects/a')
    with pytest.raises(SecurityError): HTTPTransport('http://updates.example.org')


def test_desktop_build_combines_current_coordinator_dependency_closure():
    root = Path(__file__).resolve().parents[1]
    builder = (root/'scripts/build_per_user_delivery.py').read_text(encoding='utf-8')
    spec = (root/'release/real-delivery-app.spec').read_text(encoding='utf-8')
    assert 'MF_DELIVERY_RUNTIME_ANALYSIS' in builder and 'pyi-user/UserRuntime/Analysis-00.toc' in builder
    assert 'MF_DELIVERY_RUNTIME_ANALYSIS' in spec and "[*fields['pure'],*runtime_pure]" in spec
    assert 'MF_DELIVERY_SEED_PYZ_TOC' in builder and 'MF_DELIVERY_SEED_PYZ_TOC' in spec


@pytest.mark.parametrize('mode', ['configured', 'no_config', 'no_url', 'invalid_config'])
def test_one_click_service_uses_signed_config_not_environment(case, monkeypatch, mode):
    import sys
    from scripts.install_product import Product
    _, root, _, _ = case
    release = SimpleNamespace(version='1.4.0')
    manifest = dict(version='1.4.0',channel='stable',update=dict(
        protocol='mentorflow.signed-file-release.v1', UPDATE_FEED_URL='https://updates.example.org/stable.json',
        staging_root='E:/wrong-developer-path'))
    if mode == 'no_config': manifest.pop('update')
    if mode == 'no_url': manifest['update'].pop('UPDATE_FEED_URL')
    if mode == 'invalid_config': manifest['update'] = []
    monkeypatch.setattr(install,'installed',lambda _:release)
    verified = []
    monkeypatch.setattr(install,'verify_tree',lambda *a,**kw:verified.append(kw))
    monkeypatch.setattr(install,'read',lambda _:canonical(manifest))
    monkeypatch.setattr(install,'keys',lambda:{'compiled':b'k'*32})
    monkeypatch.setattr(install,'configured',lambda:Product('MentorFlow.UAT.OneClick'))
    monkeypatch.setenv('LOCALAPPDATA',str(root.parent/'profile'))
    monkeypatch.setenv('UPDATE_FEED_URL','https://attacker.example.org/feed')
    monkeypatch.setitem(sys.modules,'mentorflow_delivery_authority',SimpleNamespace(ONE_CLICK_TEST_FIXTURE=False))
    if mode != 'configured':
        expected = 'FAILED_SECURITY' if mode == 'invalid_config' else 'FAILED_RECOVERABLE'
        assert core.update_to_latest(root)['state'] == expected
        assert core.check_latest(root)['state'] == expected
        assert core.download_latest(root)['state'] == expected
        return
    service = core.one_click_service(root)
    try:
        assert service.metadata_url == manifest['update']['UPDATE_FEED_URL']
        assert service.keys == {'compiled':b'k'*32}
        assert service.cache.runtime_root.is_relative_to(root.parent/'profile/.mfu')
        assert verified == [{'subset':['mentorflow-version.json']}]
    finally: service.close()

"""Signed inert lifecycle and offscreen shell regression; no account or user UI."""
import configparser
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from scripts.test_per_user_install import case, ready
from scripts.test_one_click_update_core import setup
from scripts import per_user_install as install
from scripts import per_user_update_core as core
from desktop.update_protocol import SecurityError, RecoverableError, canonical, startup_version_policy, verify_release


def test_direct_inno_and_canonical_roots_resolve_without_parent_guess(case):
    _,root,_,_=case
    assert install.resolve_launch_root(root/'MentorFlow.exe') == root
    assert install.resolve_launch_root(root/'App/MentorFlow.exe') == root
    (root.parent/'other').mkdir()
    with pytest.raises(SecurityError): install.resolve_launch_root(root/'App/MentorFlow.exe',root.parent/'other/MentorFlow')


def flat(case, monkeypatch):
    _,root,feed,publish=case
    monkeypatch.setattr(install,'keys',lambda:feed.keys)
    source,raw=publish('1.4.0',1)
    shutil.copytree(source,root)
    release=install.historical(raw,feed.keys)
    ini=configparser.ConfigParser(interpolation=None);ini.optionxform=str
    ini['Product']=dict(Id='MentorFlow.Inno.v1',Version='1.4.0',ExecutableSHA256=release.rows['MentorFlow.exe']['sha256'])
    ini['Files']={name.replace('/','\\'):'1' for name in release.rows}
    dirs=set(p.as_posix() for name in release.rows for p in Path(name).parents if p.as_posix()!='.')
    ini['Directories']={name.replace('/','\\'):'1' for name in dirs}
    with (root/'.mentorflow-install.ini').open('w',encoding='utf-16') as stream:ini.write(stream)
    (root/'unins000.exe').write_bytes(b'MZ Inno fixture');(root/'unins000.dat').write_bytes(b'Inno ownership')
    return root,raw,release


def test_inno_metadata_is_separate_not_payload_authority(case,monkeypatch):
    root,raw,release=flat(case,monkeypatch)
    assert not install.is_install(root)
    core.bootstrap_legacy_identity(root,envelope=raw)
    assert install.app_path(root)==root
    install.verify_payload(root)
    with pytest.raises(SecurityError): install.verify_tree(root,release)
    (root/'unins000.dat').write_bytes(b'different inert uninstall state')
    install.verify_payload(root)
    (root/'inject.dll').write_bytes(b'MZ unknown')
    with pytest.raises(SecurityError):install.verify_payload(root)


def test_flat_ownership_ini_never_authorizes_tampered_payload(case,monkeypatch):
    root,raw,release=flat(case,monkeypatch)
    core.bootstrap_legacy_identity(root,envelope=raw)
    (root/'MentorFlow.exe').write_bytes(b'tampered')
    with pytest.raises(SecurityError):install.verify_payload(root)


def test_malformed_inno_claim_is_repair_not_an_uncaught_parser_error(case,monkeypatch):
    root,raw,release=flat(case,monkeypatch)
    core.bootstrap_legacy_identity(root,envelope=raw)
    (root/'.mentorflow-install.ini').write_bytes(b'not a valid installer ownership document')
    monkeypatch.setattr(install,'verify_gui',lambda *a,**kw:None)
    with pytest.raises(SecurityError):install.verify_payload(root)
    state=core.get_startup_update_state(root,check_feed=False)
    assert state['state']=='REPAIR_REQUIRED' and not state['login_allowed']


def test_mandatory_policy_blocks_product_even_when_account_connection_is_ready():
    os.environ['QT_QPA_PLATFORM']='offscreen'
    from PySide6.QtWidgets import QApplication
    from desktop.login_shell import LoginGateWindow
    from desktop.startup_controller import StartupController
    app=QApplication.instance() or QApplication([])
    gate=LoginGateWindow();attached=[];closed=[]
    connection=SimpleNamespace(close=lambda:closed.append(True))
    controller=StartupController(gate,verify=lambda:None,connection_factory=lambda:connection,
                                 attach=lambda _:attached.append(True))
    controller.connection=connection
    controller._accept(0,'TUNNEL',True)
    controller._accept(0,'SECURITY_GATE',dict(verification='PASS',
        startup_update=core._startup_result('MANDATORY_UPDATE')))
    assert not attached and closed and not controller.attached
    assert gate.state_detail.text()=='当前版本需要更新后才能继续'
    controller.close();gate.close()


def test_legacy_migration_preserves_original_flat_and_uninstaller(case,monkeypatch):
    root,raw,release=flat(case,monkeypatch)
    core.bootstrap_legacy_identity(root,envelope=raw)
    originals={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    source,new=case[3]('1.4.1',2)
    result=install.transact(root,source,new,health=ready)
    assert result['version']=='1.4.1' and install.app_path(root)==root/'App'
    for name,value in originals.items():
        if name!=install.CURRENT:assert (root/name).read_bytes()==value
    install.verify_tree(root/'previous/App',release)


@pytest.mark.parametrize('latest,minimum,mandatory,expected',[
    ('1.4.0','1.4.0',False,'HEALTHY'),('1.4.1','1.4.0',False,'OPTIONAL_UPDATE'),
    ('1.4.1','1.4.1',False,'MANDATORY_UPDATE'),('1.4.1','1.4.0',True,'MANDATORY_UPDATE')])
def test_single_signed_version_classifier(case,monkeypatch,latest,minimum,mandatory,expected):
    root,service=setup(case,monkeypatch)
    feed=case[2];feed.signed.update(version=latest,min_supported_version=minimum,mandatory=mandatory,
                                   mandatory_reason='security' if mandatory else '')
    # Republish changes through the real signature verifier, never raw UI policy.
    if latest=='1.4.0':
        release=install.installed(root)
    else:
        feed.sign(feed.signed);release=verify_release(feed.raw,feed.keys,current='1.4.0')
    assert startup_version_policy(release,'1.4.0')==expected
    service.close()


def test_get_startup_state_offline_does_not_brick_healthy_app(case,monkeypatch):
    root,service=setup(case,monkeypatch)
    monkeypatch.setattr(install,'verify_gui',lambda *a,**kw:None)
    monkeypatch.setattr(service.cache.transport,'fetch_metadata',lambda *a: (_ for _ in ()).throw(RecoverableError('offline')))
    result=core.get_startup_update_state(root,service=service)
    assert result['state']=='HEALTHY' and result['login_allowed'] and result['feed']=='UNAVAILABLE_RECOVERABLE'
    service.close()


def test_corruption_classified_as_repair_with_independent_runtime(case,monkeypatch):
    root,service=setup(case,monkeypatch)
    monkeypatch.setattr(install,'verify_gui',lambda *a,**kw:None)
    (root/'App/MentorFlow.exe').write_bytes(b'corrupt')
    assert core.get_startup_update_state(root,check_feed=False)['state']=='REPAIR_REQUIRED'
    (root/'Updater/UserRuntime.exe').write_bytes(b'corrupt helper')
    assert core.get_startup_update_state(root,check_feed=False)['state']=='FATAL_UNRECOVERABLE'
    service.close()


def test_same_version_repair_without_login_uses_real_existing_engine(case,monkeypatch):
    root,service=setup(case,monkeypatch)
    feed=case[2]
    current=install.installed(root)
    feed.raw=current.envelope
    (root/'App/MentorFlow.exe').write_bytes(b'corrupt')
    result=core.repair_installation(root,service=service)
    assert result['state']=='CURRENT_HEALTHY' and result['version']=='1.4.0'
    install.verify_tree(root/'App',current)
    assert (root/'previous/App/MentorFlow.exe').read_bytes()==b'corrupt'
    service.close()


def test_mandatory_prelogin_update_same_high_level_api(case,monkeypatch):
    root,service=setup(case,monkeypatch)
    feed=case[2];feed.signed['min_supported_version']='1.4.1';feed.sign(feed.signed)
    monkeypatch.setattr(install,'verify_gui',lambda *a,**kw:None)
    state=core.get_startup_update_state(root,service=service)
    assert state['state']=='MANDATORY_UPDATE' and not state['login_allowed']
    monkeypatch.setattr(install,'request_app_shutdown',lambda *a:None)
    result=core.update_to_latest(root,pid=9876,hwnd_hex='0001',service=service)
    assert result['state']=='CURRENT_HEALTHY'
    service.close()


@pytest.mark.parametrize('state', ['OPTIONAL_UPDATE','MANDATORY_UPDATE','REPAIR_REQUIRED'])
def test_offscreen_login_shell_policy_and_actions(state):
    os.environ['QT_QPA_PLATFORM']='offscreen'
    from PySide6.QtWidgets import QApplication,QWidget
    from desktop.login_shell import LoginGateWindow
    app=QApplication.instance() or QApplication([])
    gate=LoginGateWindow();gate.show();app.processEvents()
    snapshot=core._startup_result(state)
    gate.set_startup_policy(snapshot);app.processEvents()
    assert gate.isVisible() and gate.update_button.isVisible()
    assert gate.repair_button.isVisible()==(state=='REPAIR_REQUIRED')
    if state=='MANDATORY_UPDATE':
        assert gate.state_detail.text()=='当前版本需要更新后才能继续'
        called=[];gate.login_requested.connect(lambda:called.append(1));gate._activate()
        assert not called
        content=QWidget();gate.set_auth_content(content);assert not content.isVisible();content.deleteLater()
    gate.close()


def test_user_error_does_not_expose_python_exception(monkeypatch):
    from scripts import windows_installed_launch
    called=[]
    monkeypatch.setattr(windows_installed_launch.ctypes.windll.user32,'MessageBoxW',lambda *args:called.append(args))
    windows_installed_launch.show_repair('ValueError: secret path')
    assert 'ValueError' not in called[0][1] and 'secret path' not in called[0][1]


def test_unbound_feed_is_explicit_and_never_launches(case,monkeypatch):
    from scripts import build_e108_user_candidate as builder
    root,service=setup(case,monkeypatch)
    monkeypatch.setattr(install,'verify_gui',lambda *a,**kw:None)
    def no_feed(*args,**kwargs):
        core.static_feed_url(builder.version_document('1.4.0',{}))
    monkeypatch.setattr(core,'one_click_service',no_feed)
    monkeypatch.setattr(install,'launch_health',lambda *a,**kw:pytest.fail('no feed must not start any process'))
    assert builder.UPDATE_FEED_URL == ''
    for operation in (core.check_latest,core.download_latest,core.update_to_latest,core.repair_installation):
        result=operation(root)
        assert result['state']=='FAILED_RECOVERABLE' and result['code']=='NO_FEED' and result['no_feed']
    result=core.get_startup_update_state(root)
    assert result['state']=='HEALTHY' and result['feed']=='NO_FEED' and result['login_allowed']
    service.close()


def test_source_fixture_cannot_create_native_repair_dialog(case,monkeypatch):
    import sys
    from scripts.per_user_launcher import repair_update_shell
    monkeypatch.delattr(sys,'frozen',raising=False)
    with pytest.raises(RecoverableError,match='packaged runtime'):
        repair_update_shell(case[1])


@pytest.mark.parametrize('config',[{'enabled':'false'},{'enabled':1},{'enabled':None}])
def test_invalid_feed_enabled_flag_rejected(config):
    with pytest.raises(SecurityError):core.static_feed_url({'update':config})


def test_next_launch_recovers_interrupted_swap_before_gui_classification(case,monkeypatch):
    from scripts import per_user_launcher as launcher
    root,service=setup(case,monkeypatch)
    old=install.installed(root)
    previous=root/'previous';previous.mkdir()
    (previous/'release.json').write_bytes(old.envelope)
    (root/'App').rename(previous/'App')
    (root/install.JOURNAL).write_bytes(canonical(dict(schema='mentorflow.per-user-transaction.v1',
        phase='OLD_MOVED',attempt='a'*32,base=old.version,base_manifest=core.digest(old.manifest_bytes))))
    def classify(*args,**kwargs):
        install.verify_tree(root/'App',old)
        return core._startup_result('HEALTHY')
    monkeypatch.setattr(core,'get_startup_update_state',classify)
    monkeypatch.setattr(install,'launch_health',ready)
    monkeypatch.setattr(launcher,'repair_update_shell',lambda *a:pytest.fail('recovery must precede GUI gate'))
    assert launcher.launch(root)['health']['ready']
    assert install.installed(root).version==old.version
    service.close()

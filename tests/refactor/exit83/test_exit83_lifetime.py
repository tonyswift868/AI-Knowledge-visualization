"""Lifetime policy and diagnostic contracts, with real Qt windows/child processes."""
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import faulthandler
import pytest
from PySide6.QtCore import QEvent, QTimer, QtMsgType, qWarning
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QWidget

from refactor import runtime_logging as logging_runtime
from refactor.desktop.account_shell_main import AccountShell
from refactor.desktop.process_diagnostics import ProcessDiagnostics, process_sample, watch_renderer
from refactor.desktop.login_shell import LoginGateWindow


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def diagnostics(app, tmp_path):
    logger = logging.getLogger('refactor')
    previous, level = logging_runtime._handler, logger.level
    if previous: logger.removeHandler(previous)
    logging_runtime._handler = None
    target = logging_runtime.configure(tmp_path)
    old_quit = app.quitOnLastWindowClosed()
    diag = ProcessDiagnostics(tmp_path)
    diag.attach(app)
    yield diag, target
    app.processEvents()
    diag.uninstall()
    app.setQuitOnLastWindowClosed(old_quit)
    handler = logging_runtime._handler
    logger.removeHandler(handler); handler.close()
    logging_runtime._handler = previous
    if previous: logger.addHandler(previous)
    logger.setLevel(level)


def records(target):
    return [json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()]


def test_exit83_default_crash_descriptor_qt_messages_and_resource_timer(diagnostics):
    diag, target = diagnostics
    assert faulthandler.is_enabled() and not diag._crash.closed
    faulthandler.dump_traceback(file=diag._crash, all_threads=True)
    assert b'test_exit83_lifetime.py' in Path(diag._crash.name).read_bytes()
    assert diag.timer.interval() == 300_000
    qWarning('fixture warning')
    diag.qt_message(QtMsgType.QtCriticalMsg, None, 'fixture critical')
    diag.qt_message(QtMsgType.QtFatalMsg, None, 'fixture fatal')
    diag.qt_message(QtMsgType.QtWarningMsg, None, 'token=SECRET sk-TEST https://host.invalid/private')
    events = records(target)
    assert {r['state'] for r in events if r.get('event')=='qt'} == {'WARNING','CRITICAL','FATAL'}
    assert 'SECRET' not in target.read_text(encoding='utf-8')
    sample = [r for r in events if r.get('event')=='process' and r.get('state')=='ALIVE'][0]
    assert sample['pid']==os.getpid() and sample['threads']>=sample['python_threads']>=1
    if os.name=='nt': assert sample['memory_bytes']>0


def test_exit83_auxiliary_close_hide_and_programmatic_main_close_do_not_quit(app, diagnostics):
    diag, target = diagnostics
    home, auxiliary = QWidget(), QWidget()
    home.setObjectName('fixtureHome');home.showMinimized()
    diag.set_primary(home, 'LEARNING_MAIN')
    auxiliary.show();app.processEvents();auxiliary.close();app.processEvents()
    assert not app.quitOnLastWindowClosed() and diag.reason is None
    assert home.isVisible() and home.isMinimized()
    assert not home.close()
    assert home.isVisible()
    home.hide();auxiliary.show();auxiliary.close();app.processEvents()
    assert diag.reason is None
    # The outer pytest process does not run QApplication.exec(); test the
    # signal wiring here, and actual last-window behavior in a child below.
    app.lastWindowClosed.emit()
    assert any(r.get('reason')=='LAST_WINDOW_CLOSED' and r['window']=='QWidget' for r in records(target))
    assert diag.eventFilter(app,QEvent(QEvent.Type.Quit))
    assert any(r.get('reason')=='UNREQUESTED_QT_QUIT_BLOCKED' for r in records(target))
    diag.primary=None;home.close();home.deleteLater();auxiliary.deleteLater()


def test_exit83_explicit_close_and_show_replacement_first(app, diagnostics, monkeypatch):
    diag, target = diagnostics
    old,new=QWidget(),QWidget();old.showMinimized();diag.set_primary(old,'LEARNING_MAIN')
    new.showMinimized();diag.set_primary(new,'LEARNING_MAIN');old.close();app.processEvents()
    assert new.isVisible() and new.isMinimized() and diag.reason is None
    # Native close events are spontaneous; synthetic QWidget.close is not.
    class UserClose(QCloseEvent):
        def spontaneous(self):return True
    queued=[]
    monkeypatch.setattr(QTimer,'singleShot',lambda *args:queued.append(args))
    assert not diag.eventFilter(new,UserClose())
    assert diag.reason=='USER_MAIN_CLOSE' and queued
    new.close();app.processEvents()
    assert any(r.get('reason')=='USER_MAIN_CLOSE' for r in records(target))
    old.deleteLater();new.deleteLater()


@pytest.mark.parametrize('control,reason',[(0,'CONSOLE_INTERRUPT'),(1,'CONSOLE_BREAK'),(2,'CONSOLE_CLOSE'),
                                        (5,'WINDOWS_LOGOFF'),(6,'WINDOWS_SHUTDOWN')])
def test_exit83_console_reason_is_written_synchronously(diagnostics, control, reason):
    diag,target=diagnostics
    assert diag.console_event(control) is False
    assert records(target)[-1]['reason']==reason


def test_exit83_signals_session_end_about_to_quit_atexit(diagnostics, monkeypatch):
    diag,target=diagnostics
    monkeypatch.setattr(QTimer,'singleShot',lambda *args:None)
    diag.signal_received(signal.SIGTERM,None)
    diag.signal_received(signal.SIGINT,None)
    diag.session_end(None)
    diag.about_to_quit()
    diag.python_exit();diag.python_exit()
    reasons=[r.get('reason') for r in records(target)]
    assert {'SIGNAL_TERM','SIGNAL_INTERRUPT','WINDOWS_SESSION_END','ABOUT_TO_QUIT','ATEXIT'}<=set(reasons)
    assert reasons.count('ATEXIT')==1 and not diag._marker.exists()


def test_exit83_renderer_exit_has_role_status_code(diagnostics):
    from PySide6.QtWebEngineCore import QWebEnginePage
    diag,target=diagnostics
    page=QWebEnginePage()
    watch_renderer(page,'TEACHER_PAGE')
    page.renderProcessTerminated.emit(QWebEnginePage.RenderProcessTerminationStatus.CrashedTerminationStatus,11)
    row=records(target)[-1]
    assert row['event']=='renderer' and row['window']=='TEACHER_PAGE'
    assert row['exit_code']==11 and row['state']=='CrashedTerminationStatus'
    page.deleteLater()


def test_exit83_hidden_gate_close_keeps_shell_active(app, tmp_path):
    # Construct the real account shell with its own local settings and dummy
    # credential owners: never read or revoke the current user's session.
    from PySide6.QtCore import QSettings
    from refactor.desktop.account_identity import AccountPreferenceStore
    from refactor.desktop.login_session import LoginSession
    preferences=AccountPreferenceStore(QSettings(str(tmp_path/'account.ini'),QSettings.IniFormat))
    session=LoginSession(preferences,credentials=Mock(),passwords=Mock())
    shell=AccountShell(app,NS(data_root=tmp_path),login_session=session)
    home=QWidget();home.showMinimized();shell.home=home
    shell.gate.hide();shell.gate.close();app.processEvents()
    assert not shell._closing and home.isVisible() and home.isMinimized()
    shell.home=None;shell.close();shell.app.aboutToQuit.disconnect(shell.close)
    home.close();home.deleteLater();shell.gate.deleteLater()


def test_exit83_scope_rebind_never_closes_or_unminimizes_primary(app, diagnostics):
    diag,target=diagnostics
    home=QWidget();home.showMinimized();diag.set_primary(home,'LEARNING_MAIN')
    home.set_server_role=Mock()
    shell=NS(home=home,_bind_learning_features=Mock(),_confirmed_server_role='LEARNER')
    assert AccountShell._replace_learning_home_for_scope(shell,object()) is home
    shell._bind_learning_features.assert_called_once_with(home,rebind=True)
    assert home.isMinimized() and home.isVisible() and diag.reason is None
    diag.primary=None;home.close();home.deleteLater()


def test_exit83_python_exit_and_next_launch_detect_forced_kill(tmp_path):
    """Actual separate interpreters, not just direct hook calls."""
    repo=Path(__file__).resolve().parents[3]
    script=tmp_path/'child.py'
    script.write_text('''import os,sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from refactor.runtime_logging import configure
from refactor.desktop.process_diagnostics import ProcessDiagnostics
root=Path(sys.argv[1]); configure(root)
app=QApplication([]); diag=ProcessDiagnostics(root);diag.attach(app)
if sys.argv[2]=='kill':os._exit(23)
''',encoding='utf-8')
    env={**os.environ,'PYTHONPATH':str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    args=[sys.executable,str(script),str(tmp_path)]
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    first=subprocess.run(args+['kill'],env=env,creationflags=flags,timeout=30)
    assert first.returncode==23 and list((tmp_path/'logs').glob('process-*.json'))
    second=subprocess.run(args+['normal'],env=env,creationflags=flags,timeout=30)
    assert second.returncode==0 and not list((tmp_path/'logs').glob('process-*.json'))
    events=records(tmp_path/'logs/mentorflow.log')
    assert any(r.get('reason')=='PREVIOUS_PROCESS_UNEXPLAINED' for r in events)
    assert any(r.get('reason')=='ATEXIT' for r in events)


def test_exit83_real_event_loop_auxiliary_last_window_and_menu_exit(tmp_path):
    repo=Path(__file__).resolve().parents[3]
    script=tmp_path/'loop.py'
    script.write_text('''import json,sys
from pathlib import Path
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import QTimer
from refactor.runtime_logging import configure
from refactor.desktop.process_diagnostics import ProcessDiagnostics
root=Path(sys.argv[1]);configure(root)
app=QApplication([]);diag=ProcessDiagnostics(root);diag.attach(app)
home=QWidget();diag.set_primary(home,'LEARNING_MAIN');home.showMinimized()
aux=QWidget();aux.setProperty('mf_window_role','SETTINGS_WINDOW');aux.show()
def close_aux():
    home.hide();aux.close()
def verify_alive():
    (root/'alive.json').write_text(json.dumps(dict(aux_closed=not aux.isVisible(),reason=diag.reason)))
    diag.request_exit('USER_MENU_EXIT')
QTimer.singleShot(30,close_aux);QTimer.singleShot(80,app.quit);QTimer.singleShot(200,verify_alive)
code=app.exec();sys.exit(code)
''',encoding='utf-8')
    result=subprocess.run([sys.executable,str(script),str(tmp_path)],
        env={**os.environ,'PYTHONPATH':str(repo),'PYTHONDONTWRITEBYTECODE':'1'},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,timeout=30)
    assert result.returncode==0
    assert json.loads((tmp_path/'alive.json').read_text())==dict(aux_closed=True,reason=None)
    events=records(tmp_path/'logs/mentorflow.log')
    assert any(r.get('reason')=='LAST_WINDOW_CLOSED' and r['window']=='SETTINGS_WINDOW' for r in events)
    assert any(r.get('reason')=='UNREQUESTED_QT_QUIT_BLOCKED' for r in events)
    assert any(r.get('reason')=='USER_MENU_EXIT' for r in events)
    assert any(r.get('reason')=='ABOUT_TO_QUIT' and r['code']=='USER_MENU_EXIT' for r in events)


@pytest.mark.skipif(os.name!='nt',reason='Windows console-free source launcher')
def test_exit83_source_launcher_has_no_console_and_captures_stderr(tmp_path):
    repo=Path(__file__).resolve().parents[3]
    script=tmp_path/'launcher_probe.py'
    script.write_text('''import ctypes,runpy,sys
from pathlib import Path
from refactor.desktop import shell_paths,account_shell_main
root=Path(sys.argv[1]);shell_paths.DATA_ROOT=root
def main():
    assert not ctypes.windll.kernel32.GetConsoleWindow()
    sys.stderr.write('source launcher stderr fixture\\n')
    return 0
account_shell_main.main=main
runpy.run_path(sys.argv[2],run_name='__main__')
''',encoding='utf-8')
    pythonw=Path(sys.executable).with_name('pythonw.exe')
    result=subprocess.run([str(pythonw),str(script),str(tmp_path),str(repo/'refactor/desktop/source_start.pyw')],
        env={**os.environ,'PYTHONPATH':str(repo),'PYTHONDONTWRITEBYTECODE':'1'},creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
    assert result.returncode==0
    assert 'source launcher stderr fixture' in (tmp_path/'logs/source-stderr.log').read_text()


@pytest.mark.skipif(os.name!='nt',reason='Windows console control callback')
def test_exit83_native_console_control_writes_reason_before_termination(tmp_path):
    repo=Path(__file__).resolve().parents[3]
    script=tmp_path/'console_probe.py'
    script.write_text('''import ctypes,sys,time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from refactor.runtime_logging import configure
from refactor.desktop.process_diagnostics import ProcessDiagnostics
root=Path(sys.argv[1]);configure(root)
app=QApplication([]);diag=ProcessDiagnostics(root);diag.attach(app)
assert ctypes.windll.kernel32.GetConsoleWindow()
assert ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1,0)
time.sleep(10)
''',encoding='utf-8')
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    result=subprocess.run([sys.executable,str(script),str(tmp_path)],
        env={**os.environ,'PYTHONPATH':str(repo),'PYTHONDONTWRITEBYTECODE':'1'},
        startupinfo=startup,creationflags=subprocess.CREATE_NEW_CONSOLE,timeout=20)
    assert result.returncode!=0
    assert any(r.get('reason')=='CONSOLE_BREAK' for r in records(tmp_path/'logs/mentorflow.log'))


def test_exit83_fatal_abort_leaves_stack_in_crash_file(tmp_path):
    repo=Path(__file__).resolve().parents[3]
    script=tmp_path/'fatal_probe.py'
    script.write_text('''import ctypes,os,sys
from pathlib import Path
from refactor.runtime_logging import configure,enable_crash_dump
root=Path(sys.argv[1]);configure(root);enable_crash_dump(root)
if os.name=='nt':
    ctypes.CDLL('ucrtbase')._set_abort_behavior(0,3)
os.abort()
''',encoding='utf-8')
    result=subprocess.run([sys.executable,str(script),str(tmp_path)],
        env={**os.environ,'PYTHONPATH':str(repo),'PYTHONDONTWRITEBYTECODE':'1'},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,timeout=20)
    assert result.returncode!=0
    assert any(b'fatal_probe.py' in p.read_bytes() for p in (tmp_path/'logs').glob('crash-*.log'))

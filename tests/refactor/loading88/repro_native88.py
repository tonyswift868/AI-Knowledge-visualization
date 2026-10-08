"""Offline actual source-shell timing/screenshots on a supplied data COPY.

Run only in the batch scratch directory, with a web build and snapshot there.
Auth, sync and models use in-memory transports: no Windows credential access.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace as NS

START = time.monotonic()
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
THEME = sys.argv[2]
MODE = sys.argv[3] if len(sys.argv) > 3 else 'fast'
assert ROOT.as_posix().startswith('D:/MentorFlow/.local/test-tmp/fix88-login')
os.environ.update(TEMP=str(ROOT), TMP=str(ROOT), PYTHONDONTWRITEBYTECODE='1', QSG_RHI_BACKEND='opengl')
os.environ.pop('PYTHONFAULTHANDLER', None)
DATA = ROOT / f'data-{THEME}-{MODE}'
shutil.copytree(ROOT/'snapshot', DATA, dirs_exist_ok=True)
ACCOUNT = 'mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932'
settings_path = DATA/'learning/accounts'/ACCOUNT/'app-settings.json'
settings = json.loads(settings_path.read_text(encoding='utf-8'))
settings.update(theme=THEME, reduce_motion=MODE=='reduced')
settings_path.write_text(json.dumps(settings,ensure_ascii=False),encoding='utf-8')
from refactor.desktop import shell_paths
shell_paths.DATA_ROOT = DATA
shell_paths.CACHE_ROOT = shell_paths.SHARED_CACHE_ROOT = DATA/'cache'
from PySide6.QtCore import QSettings, QTimer
from refactor.desktop.account_identity import AccountPreferenceStore, AccountIdentity
from refactor.desktop.login_session import LoginSession
from refactor.desktop.account_protocol import AccountLoginResult, AccountError
from refactor.desktop.login_shell import LoginGateWindow, LoginGateState
from refactor.desktop.account_shell_main import AccountShell, main
from refactor.learning.app import window
from refactor.learning.app.services import AppServices
from refactor.learning.course.store import CourseStore
from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.gateway import ModelGateway
from refactor.learning.connections.providers import ModelError
from refactor.learning.app.sync import WindowSync
import httpx

course = CourseStore(ACCOUNT, DATA)
scope = json.loads(course.sync_cursor())['scope']; course.close()
identity = AccountIdentity(scope['issuer'],scope['subject_id'],'加载测试副本',scope['organization_id'],scope['workspace_id'])
assert identity.account_id == ACCOUNT
result = AccountLoginResult(identity,'fixture-access','fixture-refresh',dict(role='SUPER_ADMIN',authenticated=True,
    account=dict(account_id=ACCOUNT,subject_id=identity.subject),
    context=dict(organization_id=identity.organization_id,workspace_id=identity.workspace_id)))
state = dict(theme=THEME, mode=MODE, events=[], screenshots=[], paid_calls=0, password_retry_retained=False)
holder = {}

def event(name, **values):
    state['events'].append(dict(name=name, ms=round((time.monotonic()-START)*1000,2), **values))
    (ROOT/f'result-{THEME}-{MODE}.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')

def shot(name, widget):
    target = ROOT/'screenshots'/f'{THEME}-{MODE}-{name}.png'
    target.parent.mkdir(parents=True,exist_ok=True)
    assert widget.grab().save(str(target))
    state['screenshots'].append(str(target))

class Vault:
    def store_oauth_tokens(self,*a,**kw): pass
    def load_oauth_tokens(self,*a): return 'fixture-access','fixture-refresh'
    def logout(self,*a): pass
class Passwords:
    def delete(self,*a): pass

def transport(req):
    if req.url.path.endswith('/native/session'):
        return httpx.Response(200,json={'state':'active'})
    if req.url.path.endswith('/changes'):
        since = req.url.params.get('since','0')
        return httpx.Response(200,json=dict(schema='mentorflow.structured-sync-changes.v1',contract_version=1,
            scope=scope,kind='learning_run',since=since,next_cursor=since,has_more=False,entities=[]))
    return httpx.Response(200,json={'entities':[]})
window.WEB_ROOT = ROOT/'web-dist'
window.WindowSync = lambda w,s: WindowSync(w,s,http_transport=httpx.MockTransport(transport))
AppServices.refresh_catalog = lambda s: None
AppServices._price_catalog = lambda s: PriceCatalog.empty()
def no_model(*a, **kw): raise ModelError('MODEL_CONNECTION_FAILED','离线加载测试不调用模型。')
ModelGateway.complete = no_model
ModelGateway.balance = lambda *a,**kw: None

gate_init = LoginGateWindow.__init__
def init_gate(self):
    gate_init(self)
    self.set_loading_appearance(THEME, MODE=='reduced')
    event('gate_constructed')
    self.first_paint_completed.connect(lambda: event('gate_first_paint'))
    self.loading.tier_changed.connect(lambda tier: event('gate_tier',tier=tier))
LoginGateWindow.__init__ = init_gate

shell_init = AccountShell.__init__
def authenticate(username, password):
    event('auth_worker_started')
    time.sleep(16.2 if MODE=='slow' else .65)
    holder['auth_attempts'] = holder.get('auth_attempts', 0) + 1
    if MODE=='fail' and holder['auth_attempts']==1:
        event('auth_fixture_failed')
        raise AccountError('NATIVE_NETWORK_UNAVAILABLE','离线夹具：认证暂不可用。')
    event('auth_worker_returned')
    return result

def init_shell(self, app, runtime, **kwargs):
    event('shell_init_started')
    session = LoginSession(AccountPreferenceStore(QSettings(str(DATA/'config/account.ini'),QSettings.IniFormat)),
        credentials=Vault(), passwords=Passwords())
    session.take_update_login = lambda: (False,None,None)
    session.logout = lambda *a,**kw: (time.sleep(.65) or False)
    # The saved account is real copied metadata; restoration is a fixture only.
    if MODE in {'restore','restore-fail'}:
        session.remembered = lambda: (identity.profile(), True)
        def restore(*args):
            event('restore_worker_started'); time.sleep(.65)
            holder['restore_attempts'] = holder.get('restore_attempts',0)+1
            if MODE=='restore-fail' and holder['restore_attempts']==1:
                event('restore_fixture_failed')
                raise AccountError('NATIVE_NETWORK_UNAVAILABLE','离线夹具：恢复暂不可用。')
            event('restore_worker_returned'); return result
        session.restore = restore
    else:
        session.remembered = lambda: (None,False)
    shell_init(self,app,runtime,login_session=session,native_login=authenticate,
        capability_fetcher=lambda: {},**kwargs)
    holder['shell'] = self
    event('shell_initialized')
    QTimer.singleShot(100, begin_login)
    QTimer.singleShot(45000,lambda: app._mentorflow_diagnostics.request_exit('TEST_LOADING88_TIMEOUT'))
AccountShell.__init__ = init_shell
AccountShell._best_effort_native_logout = lambda *a,**kw: None
AccountShell._restore_active_session = lambda *a: result

def begin_login():
    shell = holder['shell']
    if MODE in {'restore','restore-fail'}:
        event('auto_restore_observed')
        QTimer.singleShot(400,lambda:shot('restore-medium',shell.gate))
    else:
        shell.gate.username_field.setText('加载测试副本')
        shell.gate.password_field.setText('fixture-password')
        shell.gate.remember_password_box.setChecked(False)
        shell.gate.auto_box.setChecked(False)
        event('login_clicked')
        shell.gate.primary_button.click()
        QTimer.singleShot(420, lambda: shot('login-medium', shell.gate))
        if MODE=='slow':
            QTimer.singleShot(3200,lambda:shot('login-long',shell.gate))
            QTimer.singleShot(15300,lambda:shot('slow',shell.gate))
    poll = QTimer(shell.app); poll.setInterval(20); poll.timeout.connect(check_home); poll.start()
    holder['poll'] = poll

def check_home():
    shell = holder['shell']
    if shell.home is None:
        if MODE=='fail' and shell.gate.state is LoginGateState.AUTH_FAILED and not holder.get('retried'):
            holder['retried'] = True
            event('login_failure_visible',loading_active=shell.gate.loading.active)
            shot('failure',shell.gate)
            shell.gate.password_field.setText('fixture-password')
            event('failed_login_retry_clicked')
            shell.gate.primary_button.click()
        elif MODE=='restore-fail' and shell.gate.loading.slow and not holder.get('retried'):
            holder['retried'] = True
            event('restore_failure_slow_visible')
            shot('restore-slow',shell.gate)
            event('restore_retry_clicked')
            shell.gate.loading.retry_button.click()
        return
    home = shell.home
    if not holder.get('home_seen'):
        holder['home_seen'] = True
        event('home_shown',cover_visible=not home._native_first_frame.cover.isHidden())
        shot('web-startup',home)
    if not home._native_first_frame._done:
        return
    holder['poll'].stop()
    event('web_first_frame',cover_hidden=home._native_first_frame.cover.isHidden())
    shot('home-ready',home)
    QTimer.singleShot(100, switch_and_logout)

def switch_and_logout():
    shell = holder['shell']
    event('switch_clicked')
    shell._switch_account()
    event('switch_gate_ready',state=shell.gate.state.value)
    # Only the supplied fixture result is reused; no credentials read.
    shell._accept_login(result,username='加载测试副本',remember=False,automatic=False)
    QTimer.singleShot(400,do_logout)

def do_logout():
    shell = holder['shell']
    event('logout_clicked')
    shell._logout_account()
    poll = QTimer(shell.app); poll.setInterval(20)
    def done():
        if shell.gate.state is LoginGateState.LOGIN_READY:
            poll.stop(); event('logout_gate_ready')
            shell.app._mentorflow_diagnostics.request_exit('TEST_LOADING88_COMPLETED')
    poll.timeout.connect(done); poll.start(); holder['logout_poll'] = poll

# Long-startup screenshots use the same live native widget and an injected age;
# they do not claim the local initialization actually takes three seconds.
original_show = AccountShell.show
def show(self):
    self.gate.loading.started -= 3.2
    self.gate.loading.advance()
    shot('startup-long-fixture', self.gate)
    original_show(self)
AccountShell.show = show
event('main_enter')
code = main()
event('exit',code=code)
if not any(e['name']=='web_first_frame' for e in state['events']):
    raise SystemExit(2)
raise SystemExit(code)

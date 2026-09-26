"""Thin native bridge for the existing 03 FreeMarker lifecycle presentation.

Only generated, bundled local UI can access this channel. Email-code recovery
stays in this window; opaque reset state never crosses the JavaScript channel.
"""
import asyncio
import json
from pathlib import Path
import sys
import threading
import time
from PySide6.QtCore import QObject, Signal, Slot, QUrl, QTimer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
from .enterprise_oidc import DesktopOidcConfig
from .lifecycle_client import LifecycleClient, RESET_MESSAGES


def assets():
    return Path(sys._MEIPASS)/'account-lifecycle' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]/'artifacts/account-lifecycle-ui'


class LocalOnly(QWebEngineUrlRequestInterceptor):
    def __init__(self, root, parent): super().__init__(parent); self.root=root.resolve()
    def interceptRequest(self, info):
        url=info.requestUrl()
        allowed=url.scheme()=='qrc' and url.path()=='/qtwebchannel/qwebchannel.js'
        if url.isLocalFile():
            p=Path(url.toLocalFile()).resolve()
            allowed=p.is_relative_to(self.root)
        if not allowed: info.block(True)


class LocalPage(QWebEnginePage):
    def acceptNavigationRequest(self,url,kind,is_main):
        return url.isLocalFile() and Path(url.toLocalFile()).resolve()==(assets()/'lifecycle.html').resolve()
    def javaScriptConsoleMessage(self,*args): pass


class _ResetResults(QObject):
    """UI-thread delivery, deliberately NOT registered with QWebChannel."""
    ready=Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.ready.connect(self.deliver)

    @Slot(object)
    def deliver(self, result):
        self.parent()._finish(result)


class Bridge(QObject):
    result=Signal(str)
    back=Signal(bool)
    def __init__(self,parent):
        super().__init__(parent)
        self.cancelled=threading.Event(); self.busy=False
        self.transaction_id=None; self.reset_proof=None; self.email=''
        self.deadline=0; self.resend_deadline=0; self.stage='email'
        self.completed_account_id=None; self.generation=0
        self._worker_results=_ResetResults(self)
        self.timer=QTimer(self); self.timer.setInterval(500)
        self.timer.timeout.connect(self._expire); self.timer.start()

    def clear_reset(self):
        self.transaction_id=None; self.reset_proof=None; self.deadline=0

    def cancel(self):
        self.cancelled.set(); self.generation+=1
        self.clear_reset(); self.email=''; self.timer.stop()

    @Slot()
    def returnLogin(self): self.cancel(); self.back.emit(False)
    @Slot(str,str,str,int)
    def register(self,username,password,email,request_id):
        if self.busy or self.cancelled.is_set(): return
        self.busy=True
        def work():
            nonlocal password
            async def request():
                client=LifecycleClient(DesktopOidcConfig.from_env())
                try: return await client.action('register',dict(username=username,password=password,email=email))
                finally: await client.aclose()
            try:
                asyncio.run(request()); result={'kind':'register','requestId':request_id,'outcome':'verification_pending','email':email}
            except Exception as exc:
                result={'kind':'register','requestId':request_id,'outcome':'username_taken' if getattr(exc,'code','')=='ACCOUNT_ALREADY_IN_USE' else 'failed'}
            finally: password=None; self.busy=False
            if not self.cancelled.is_set(): self.result.emit(json.dumps(result))
        threading.Thread(target=work,daemon=True).start()
    def _publish(self, kind, request_id=0, code='', **extra):
        self.result.emit(json.dumps(dict(kind=kind, requestId=request_id, code=code,
            message=RESET_MESSAGES.get(code, ''), stage=self.stage,
            resendAfter=max(0, int(self.resend_deadline-time.monotonic()+0.999)), **extra)))

    def _expire(self):
        if self.busy or self.cancelled.is_set(): return
        if self.deadline and time.monotonic() >= self.deadline:
            code='RESET_CODE_INVALID_OR_EXPIRED' if self.stage=='code' else 'RESET_PROOF_INVALID_OR_EXPIRED'
            self.clear_reset(); self.stage='expired'
            self._publish('expired', code=code)

    @Slot()
    def restartReset(self):
        if self.busy or self.cancelled.is_set(): return
        self.clear_reset(); self.stage='email'
        self._publish('restart')

    @Slot(str,int)
    def requestCode(self, email, request_id):
        if self.busy or self.cancelled.is_set(): return
        if time.monotonic() < self.resend_deadline:
            self._publish('request', request_id, 'RESET_RATE_LIMITED'); return
        self.clear_reset(); self.stage='email'; self.email=email.strip()
        # Retain cooldown even when the send response is lost; never auto-resend.
        self.resend_deadline=time.monotonic()+60
        self._start('request', {'email':self.email}, request_id)

    @Slot(int)
    def resendCode(self, request_id):
        self.requestCode(self.email, request_id)

    @Slot(str,int)
    def verifyCode(self, code, request_id):
        self._expire()
        if self.busy or self.cancelled.is_set(): return
        if self.stage!='code' or not self.transaction_id:
            self._publish('verify', request_id, 'RESET_CODE_INVALID_OR_EXPIRED'); return
        self._start('verify', {'transaction_id':self.transaction_id, 'code':code}, request_id)

    @Slot(str,str,int)
    def completeReset(self, password, confirmation, request_id):
        self._expire()
        if self.busy or self.cancelled.is_set(): return
        if self.stage!='password' or not self.reset_proof:
            self._publish('complete', request_id, 'RESET_PROOF_INVALID_OR_EXPIRED'); return
        self._start('complete', {'reset_proof':self.reset_proof,
            'new_password':password, 'confirm_password':confirmation}, request_id)

    def _start(self, operation, payload, request_id):
        self.busy=True
        generation=self.generation; started=time.monotonic()
        def work():
            async def request():
                client=LifecycleClient(DesktopOidcConfig.from_env())
                try: return await client.reset_code(operation, payload)
                finally: await client.aclose()
            try: value=asyncio.run(request()); error=''
            except Exception as exc:
                value=None; error=getattr(exc, 'code', 'RESET_RESULT_UNCERTAIN')
                if error not in RESET_MESSAGES: error='RESET_RESULT_UNCERTAIN'
            finally: payload.clear()
            if not self.cancelled.is_set():
                try: self._worker_results.ready.emit((generation, operation, request_id, started, value, error))
                except RuntimeError: pass  # Window was disposed while the request completed.
        threading.Thread(target=work,daemon=True).start()

    def _finish(self, result):
        generation, operation, request_id, started, value, error=result
        if self.cancelled.is_set() or generation!=self.generation: return
        self.busy=False
        if error:
            if error=='RESET_EMAIL_INVALID': self.resend_deadline=0
            if error in ('RESET_CODE_INVALID_OR_EXPIRED', 'RESET_PROOF_INVALID_OR_EXPIRED', 'RESET_RESULT_UNCERTAIN'):
                self.clear_reset(); self.stage='expired'
            self._expire()
            self._publish(operation, request_id, error)
            return
        if operation=='request':
            self.transaction_id=value['transaction_id']; self.stage='code'
            self.deadline=started+value['expires_in']
            self.resend_deadline=max(self.resend_deadline, started+value['resend_after'])
        elif operation=='verify':
            self.clear_reset(); self.reset_proof=value['reset_proof']; self.stage='password'
            self.deadline=started+value['expires_in']
        else:
            self.clear_reset(); self.email=''; self.stage='completed'
            self.completed_account_id=value['account_id']
            self._publish('completed', request_id)
            self.back.emit(True)
            return
        self._expire()
        self._publish(operation, request_id)


class AccountLifecyclePane(QWidget):
    finished=Signal(bool)
    def __init__(self,intent,parent=None,*,shell_runtime=None):
        super().__init__(parent)
        if intent not in ('register','forgot_password'):raise ValueError('Invalid account action')
        root=shell_runtime.lifecycle_root if shell_runtime is not None else assets()
        assert (root/'lifecycle.html').is_file(),'Lifecycle UI has not been built'
        layout=QVBoxLayout(self); self.view=QWebEngineView(self); layout.addWidget(self.view)
        self.profile=QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        if shell_runtime is not None: shell_runtime.configure_web_profile(self.profile)
        self.interceptor=LocalOnly(root,self.profile);self.profile.setUrlRequestInterceptor(self.interceptor)
        self.page=LocalPage(self.profile,self.view);self.view.setPage(self.page)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,False)
        self.channel=QWebChannel(self.page);self.bridge=Bridge(self)
        self.channel.registerObject('lifecycle',self.bridge);self.page.setWebChannel(self.channel)
        self.bridge.back.connect(self.finished.emit)
        self.view.setUrl(QUrl.fromLocalFile(str(root/'lifecycle.html')))
        self.view.loadFinished.connect(lambda ok:self.page.runJavaScript('window.mfNativeIntent='+json.dumps(intent)+';window.mfStartNative?.();') if ok else None)
        back=QPushButton('返回登录');back.clicked.connect(self.bridge.returnLogin);layout.addWidget(back)
    def cancel(self):self.bridge.cancel();self.view.stop()
    def dispose(self):self.cancel();self.hide();self.deleteLater()
    def closeEvent(self,event):self.cancel();super().closeEvent(event)

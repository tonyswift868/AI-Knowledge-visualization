"""Thin native bridge for the existing 03 FreeMarker lifecycle presentation.

Only generated, bundled local UI can access this channel. Public email pages
remain in the user's browser and never receive a native bridge or login session.
"""
import asyncio
import json
from pathlib import Path
import sys
import threading
from PySide6.QtCore import QObject, Signal, Slot, QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
from .enterprise_oidc import DesktopOidcConfig
from .lifecycle_client import LifecycleClient
from .reset_completion import ResetCompletionClient


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


class Bridge(QObject):
    result=Signal(str)
    back=Signal(bool)
    def __init__(self,parent): super().__init__(parent); self.cancelled=threading.Event(); self.busy=False
    @Slot()
    def returnLogin(self): self.cancelled.set(); self.back.emit(False)
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
    @Slot(str,int)
    def forgot(self,name,request_id):
        if self.busy or self.cancelled.is_set(): return
        self.busy=True
        self.account_name=name
        def work():
            accepted=False
            async def request():
                nonlocal accepted
                config=DesktopOidcConfig.from_env(); client=LifecycleClient(config)
                try: value=await client.action('forgot',{'account':name})
                finally: await client.aclose()
                if self.cancelled.is_set(): return
                accepted=True
                self.result.emit(json.dumps({'kind':'forgot','requestId':request_id,'accepted':True}))
                observer=ResetCompletionClient(config)
                try:
                    await observer.wait_completed(value['observation']['transaction_id'],self.cancelled)
                    if not self.cancelled.is_set(): self.back.emit(True)
                finally: await observer.aclose()
            try: asyncio.run(request())
            except Exception as exc:
                if not self.cancelled.is_set(): self.result.emit(json.dumps({'kind':'forgot','requestId':request_id,'accepted':False,'stage':'observation' if accepted else 'request','expired':getattr(exc,'code','')=='RESET_OBSERVATION_EXPIRED'}))
            finally:self.busy=False
        threading.Thread(target=work,daemon=True).start()


class AccountLifecyclePane(QWidget):
    finished=Signal(bool)
    def __init__(self,intent,parent=None):
        super().__init__(parent)
        if intent not in ('register','forgot_password'):raise ValueError('Invalid account action')
        root=assets(); assert (root/'lifecycle.html').is_file(),'Lifecycle UI has not been built'
        layout=QVBoxLayout(self); self.view=QWebEngineView(self); layout.addWidget(self.view)
        self.profile=QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.interceptor=LocalOnly(root,self.profile);self.profile.setUrlRequestInterceptor(self.interceptor)
        self.page=LocalPage(self.profile,self.view);self.view.setPage(self.page)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,False)
        self.channel=QWebChannel(self.page);self.bridge=Bridge(self)
        self.channel.registerObject('lifecycle',self.bridge);self.page.setWebChannel(self.channel)
        self.bridge.back.connect(self.finished.emit)
        self.view.setUrl(QUrl.fromLocalFile(str(root/'lifecycle.html')))
        self.view.loadFinished.connect(lambda ok:self.page.runJavaScript('window.mfNativeIntent='+json.dumps(intent)+';window.mfStartNative?.();') if ok else None)
        back=QPushButton('返回登录');back.clicked.connect(self.bridge.returnLogin);layout.addWidget(back)
    def cancel(self):self.bridge.cancelled.set();self.view.stop()
    def dispose(self):self.cancel();self.hide();self.deleteLater()
    def closeEvent(self,event):self.cancel();super().closeEvent(event)

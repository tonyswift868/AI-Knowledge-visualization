import asyncio
import json
import threading
import time
import httpx
import pytest
from PySide6.QtWidgets import QApplication
from refactor.desktop import lifecycle_pane as pane
from refactor.desktop.lifecycle_client import LifecycleClient
from refactor.desktop.account_protocol import AccountConfig


@pytest.fixture
def bridge(monkeypatch):
    app=QApplication.instance() or QApplication([])
    config=AccountConfig('https://identity.invalid/realms/test','mentorflow-desktop','mentorflow-api','https://api.invalid')
    monkeypatch.setattr(pane.AccountConfig,'from_env',lambda:config)
    b=pane.Bridge(None); events=[]; back=[]
    b.result.connect(lambda raw:events.append(json.loads(raw)))
    b.back.connect(back.append)
    yield b,events,back,app
    b.cancel()


def pump(app, condition):
    end=time.monotonic()+5
    while not condition() and time.monotonic()<end:
        app.processEvents(); time.sleep(.005)
    app.processEvents(); assert condition()


def connect_mock(monkeypatch, handler):
    def factory(config):
        return LifecycleClient(config,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(pane,'LifecycleClient',factory)


def test_registration_bridge_resend_verify_no_credential_to_web(monkeypatch, bridge):
    b,events,back,app=bridge;calls=[]
    def handler(r):
        calls.append(json.loads(r.content))
        if r.url.path.endswith('/verify'): return httpx.Response(200,json={'state':'VERIFIED'})
        return httpx.Response(200 if r.url.path.endswith('/resend') else 201,json={
            'state':'CODE_SENT','credential':('r' if r.url.path.endswith('/resend') else 't')*43,'expires_in':600,'resend_after':60})
    connect_mock(monkeypatch,handler)
    b.register('new-account','private-password','mail@fixture.invalid',1)
    pump(app,lambda:len(events)==1)
    assert b.registration_credential=='t'*43 and events[0]['outcome']=='verification_pending'
    b.resendRegistration(2)
    assert events[-1]['code']=='REGISTRATION_COOLDOWN' and len(calls)==1
    b.registration_resend_deadline=0; b.resendRegistration(3)
    pump(app,lambda:len(calls)==2 and not b.busy)
    assert b.registration_credential=='r'*43
    b.verifyRegistration('123456',4);pump(app,lambda:back==[True])
    assert calls[-1]=={'credential':'r'*43,'code':'123456'}
    assert b.registration_credential is None and b.completed_username=='new-account'
    serialized=json.dumps(events)
    assert 't'*43 not in serialized and 'r'*43 not in serialized and 'private-password' not in serialized


def test_registration_expiry_and_restart_clear_private_state(bridge):
    b,events,back,app=bridge
    b.registration_credential='t'*43; b.registration_deadline=time.monotonic()-1
    b._expire()
    assert events[-1]['code']=='REGISTRATION_EXPIRED' and b.registration_credential is None
    b.restartRegistration(); assert events[-1]['kind']=='registration_restart' and not back


def test_registration_cancel_fences_late_worker(monkeypatch, bridge):
    b,events,back,app=bridge;entered=threading.Event();release=threading.Event();finished=threading.Event()
    def handler(r):
        entered.set(); release.wait(5)
        finished.set()
        return httpx.Response(201,json={'state':'CODE_SENT','credential':'t'*43,'expires_in':600,'resend_after':60})
    connect_mock(monkeypatch,handler)
    b.register('name','password','mail@fixture.invalid',1)
    pump(app,entered.is_set);b.cancel();release.set();pump(app,finished.is_set)
    # A queued result from the old generation is also rejected on the UI thread.
    b._finish_registration((b.generation-1,'start',1,0,{},''))
    assert not events and not back and b.registration_credential is None

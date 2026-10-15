"""Only exhausted logical operations may publish; maintenance must stay lazy."""
from datetime import date
from types import SimpleNamespace
import threading

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.services import AppServices
from refactor.learning.connections.errors import ModelError
from tests.refactor.app.test_plan_generate_batches import Gateway,flow,draft,chunks,ctx,MODULE


def test_retry_boundary134_split_recovery_does_not_show_early_error():
    gateway=Gateway(truncate=2);notices=[]
    gateway.report_error=lambda error,**kw:notices.append((error.code,len(gateway.requests)))
    result=flow(gateway)._generate_module(draft(),MODULE,chunks(4,2000),ctx())
    assert result and len(gateway.requests)==4
    assert notices==[]


def test_retry_boundary134_exhausted_half_reports_once_after_all_attempts():
    gateway=Gateway(truncate=4);notices=[]
    gateway.report_error=lambda error,**kw:notices.append((error.code,len(gateway.requests)))
    with pytest.raises(ModelError):flow(gateway)._generate_module(draft(),MODULE,chunks(4,2000),ctx())
    assert notices==[('MODEL_OUTPUT_TRUNCATED',4)]


def test_retry_boundary134_pure_plan_operation_never_initializes_model(tmp_path,monkeypatch):
    services=AppServices(account_id='lazy134',data_root=tmp_path,session_provider=lambda:None,
                         api_base_url='https://localhost',ca_bundle=None)
    def prohibited():raise AssertionError('pure plan operation loaded model prices')
    monkeypatch.setattr(services,'_price_catalog',prohibited)
    f=PlanFlow(services,lambda *a:None,lambda:date(2026,10,15));d=f.current()
    try:
        result=f.run(d['plan_id'],lambda *_:None,SimpleNamespace(cancel=threading.Event()))
        assert result['draft']['phase']=='form'
        assert services._gateway is None and services._catalog is None
    finally:services.close()

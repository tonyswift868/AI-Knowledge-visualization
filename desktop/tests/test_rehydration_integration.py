"""Current producer contract through authenticated epoch and real Qt presentation."""
from copy import deepcopy
import httpx
import pytest
from desktop.tests.test_login_smooth import login, ISSUER, API
from desktop.tests.test_native_login import pair
from desktop.enterprise_oidc import DesktopOidcConfig
from desktop.rehydration_transport import AuthenticatedRehydrationTransport, invalidate_active
from desktop.rehydration_adapter import present, project
from desktop.rehydration_page import RehydrationPage


def server_snapshot(empty='NONEMPTY'):
    entities = [] if empty != 'NONEMPTY' else [
        dict(kind='resource', entity_id='shared', revision=1, deleted=False,
             payload=dict(filename='not-a-model.txt', product_classification='MODEL')),
        *[dict(kind='plan', entity_id=f'p{i}', revision=1, deleted=False,
               payload=dict(title=f'计划{i}', resource_ids=['shared'])) for i in (1,2)],
        dict(kind='progress', entity_id='g', revision=1, deleted=False,
             payload=dict(plan_id='p1', mastery=.5, completed_tasks=2))]
    learning=dict(status=empty, supported_scope=['plans','progress','resource_metadata'])
    return dict(schema='mentorflow.structured-sync.v1', contract_version=3, complete=True,
        snapshot_revision='pg-snapshot:1:2:', snapshot_scope=dict(subject_id='a', organization_id='org-a', workspace_id='ws-a'),
        learning_state=learning, entities=entities, resources=[] if not entities else [
            dict(resource_id='shared', product_classification='MODEL', plan_ids=['p1','p2'])],
        progress_provenance=[], observation=dict(source='SERVER_CONFIRMED', snapshot_revision='pg-snapshot:1:2:',
            server_confirmed_at='2026-09-13T10:00:00Z', counts=dict(plans=2,progress=1,resources=1),
            supported_scope=learning['supported_scope'], empty_state=empty))


def session(snapshot):
    identity=login('a').identity
    transport=AuthenticatedRehydrationTransport(DesktopOidcConfig(ISSUER,'mentorflow-desktop','mentorflow-api',API),
        identity, dict(authenticated=True, account=dict(account_id=identity.account_id,subject_id='a'),
                       context=dict(organization_id='org-a',workspace_id='ws-a')), pair('a'),
        client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=snapshot))))
    transport.activate()
    return transport


@pytest.fixture
def page(desktop_qt_session):
    p=RehydrationPage()
    yield p
    invalidate_active(); p.close(); p.deleteLater(); desktop_qt_session.processEvents()


def test_current_transport_producer_shared_resource_and_progress(page):
    t=session(server_snapshot()); page.authenticated(t.context.account_id)
    assert present(page,page.start_sync(),t.bootstrap())
    value=page.model.snapshot
    assert len(value.plans)==2 and value.progress_count==1 and len(value.resources)==1
    assert value.resources[0].key=='shared' and value.resources[0].plan_keys==('p1','p2')
    assert value.resources[0].kind=='model' and value.resources[0].missing
    assert '50%' in value.plans[0].progress and '暂不支持' in page.summary.text()


@pytest.mark.parametrize('empty,caption,allowed',[
    ('TRUE_EMPTY_SUPPORTED_SCOPE','范围内暂无',True),
    ('PARTIAL_UNSUPPORTED','部分学习数据',True),('NOT_INITIALIZED','尚未完成初始化',False)])
def test_server_empty_scope_is_not_guessed(page,empty,caption,allowed):
    t=session(server_snapshot(empty)); page.authenticated(t.context.account_id)
    present(page,page.start_sync(),t.bootstrap())
    assert caption in page.title.text() and page.enter.isEnabled() is allowed
    assert not page.model.snapshot.empty_account


def test_late_result_cannot_publish_after_logout(page):
    t=session(server_snapshot()); page.authenticated(t.context.account_id)
    generation=page.start_sync(); envelope=t.bootstrap(); t.invalidate()
    present(page,generation,envelope)
    assert page.model.snapshot is None and page.model.state.value=='NOT_AUTHORIZED'


def test_wrong_workspace_and_missing_authority_fail_closed(page):
    raw=server_snapshot();raw['snapshot_scope']['workspace_id']='wrong'
    t=session(raw);page.authenticated(t.context.account_id)
    present(page,page.start_sync(),t.bootstrap())
    assert page.model.snapshot is None and page.model.state.value=='SYNC_FAILED'

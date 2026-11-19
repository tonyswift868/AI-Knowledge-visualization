"""RAG sessions use the same account CourseStore outbox as free chat."""
import hashlib

import httpx

from refactor.learning.app import rag
from refactor.learning.app.services import AppServices
from tests.refactor.rag155.test_rag155_backend import CA, FakeRagServer, FakeGateway, rag155_session


def test_rag155_real_course_materials_services_and_session_sync_dirty_outbox(tmp_path,monkeypatch):
    state=rag155_session()
    server=FakeRagServer()
    original=rag.RagTransport.__init__
    def transport(self,**kwargs):
        kwargs['http_transport']=httpx.MockTransport(server)
        original(self,**kwargs)
    monkeypatch.setattr(rag.RagTransport,'__init__',transport)
    services=AppServices(account_id=state.identity.account_id,data_root=tmp_path,
        session_provider=lambda:state,api_base_url='https://rag155.invalid',ca_bundle=CA)
    gateway=FakeGateway();gateway.close=lambda:None
    services._gateway=gateway
    try:
        store=services.store
        plan=store.create_plan(goal='实际计划名称')
        body='队列采用先进先出。'
        digest=hashlib.sha256(body.encode()).hexdigest()
        store.add_sources(plan,[dict(id='3'*32,kind='file',title='数据结构资料',adopted=True,sha256=digest,
            chunks=[dict(id='2'*32,text=body,page_start=5,section=['数据结构','队列'])])])
        before=store.pending_records(10000)
        service=services.rag
        listing=service.sources()
        assert listing['plans'][0]['title']=='实际计划名称'
        assert listing['plans'][0]['document_ids']==[digest]
        assert service.ingest([digest])['documents'][0]['status']=='ready'
        server.search_passages=[dict(document_id=digest,seq=0,text=body,page=5,section='数据结构 / 队列')]
        session=service.create([digest])['session']
        result=service.send(session['id'],'队列按什么顺序？')['session']
        assert [m['role'] for m in result['messages']]==['user','assistant']
        assert result['messages'][-1]['passages'][0]['page']==5
        pending = store.pending_records(10000)
        assert pending[:len(before)] == before
        assert {record.entity for record in pending[len(before):]} == {"rag_sessions", "rag_messages"}
    finally:
        services.close()
    assert service.local._closed and service.transport._closed.is_set() and store._closed

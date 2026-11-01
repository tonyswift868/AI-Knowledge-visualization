"""269 deletion fences use Events, never network or paid models."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import threading
from types import SimpleNamespace

import httpx
import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.rag import API, RagService
from tests.refactor.rag155.test_rag155_backend import FakeRagServer, service
from tests.refactor.rag263.test_rag263_background import CA, FakeCourseStore, FakeGateway, close_service, eventually, state_for


class DeleteServer269(FakeRagServer):
    def __init__(self):
        super().__init__()
        self.online = True
        self.lose_delete_response = False
        self.block_upload = False
        self.upload_entered = threading.Event()
        self.upload_release = threading.Event()
        self.delete_entered = threading.Event()
        self.delete_paths = []
        self.block_delete = False
        self.delete_release = threading.Event()

    def __call__(self, request):
        if not self.online:
            raise httpx.ConnectError('synthetic offline', request=request)
        if request.method == 'POST' and request.url.path.endswith('/chunks'):
            self.upload_entered.set()
            if self.block_upload:
                assert self.upload_release.wait(10), 'upload gate not released'
        if request.method == 'DELETE':
            self.requests.append(request)
            self.delete_paths.append(request.url.path)
            self.delete_entered.set()
            if self.block_delete:
                assert self.delete_release.wait(10), 'delete gate not released'
            doc_id = request.url.path.rsplit('/', 1)[1]
            if doc_id in self.aliases:
                canonical = self.aliases.pop(doc_id)
                result = {'deleted': True, 'content_deleted': False, 'canonical_id': canonical}
            elif doc_id in self.documents:
                remaining = sorted(key for key, val in self.aliases.items() if val == doc_id)
                if remaining:
                    new = remaining[0]
                    self.documents[new] = self.documents.pop(doc_id)
                    self.aliases.pop(new)
                    for alias in remaining[1:]:
                        self.aliases[alias] = new
                    result = {'deleted': True, 'content_deleted': False, 'canonical_id': new}
                else:
                    self.documents.pop(doc_id)
                    result = {'deleted': True, 'content_deleted': True, 'canonical_id': None}
            else:
                return httpx.Response(404, json={'error': {'code': 'RAG_DOCUMENT_NOT_FOUND'}})
            if self.lose_delete_response:
                self.online = False
                if self.lose_delete_response == 'gateway-504':
                    return httpx.Response(504, json={'error': 'synthetic lost upstream response'})
                raise httpx.ReadError('synthetic lost DELETE response', request=request)
            return httpx.Response(200, json=result)
        return super().__call__(request)


def add269(instance, marker='synthetic', *, chunks=1):
    doc_id = sha256(marker.encode()).hexdigest()
    instance.local.save_document(doc_id, marker, marker + '.txt',
        [{'text': f'{marker} paragraph {i}'} for i in range(chunks)])
    return doc_id


def pending269(instance):
    with instance.local._lock:
        return instance.local._db.execute('SELECT COUNT(*) FROM rag_document_deletions').fetchone()[0]


def test_rag269_delete_drains_inflight_upload_and_prevents_late_batches(tmp_path):
    fake = DeleteServer269()
    fake.block_upload = True
    instance, _, _ = service(tmp_path, fake)
    doc_id = add269(instance, chunks=130)
    try:
        job_id = instance.prepare([doc_id])['jobs'][0]['id']
        assert fake.upload_entered.wait(3)
        with ThreadPoolExecutor(max_workers=1) as pool:
            deleting = pool.submit(instance.delete_document, doc_id)
            eventually(lambda: instance._jobs[job_id]['cancel'].is_set())
            assert not fake.delete_entered.is_set()
            assert instance.local.document(doc_id) is not None
            with pytest.raises(BridgeError):
                instance.prepare([doc_id])
            fake.upload_release.set()
            assert deleting.result(10)['ok']
        assert len([r for r in fake.requests if r.method == 'POST' and r.url.path.endswith('/chunks')]) == 1
        assert not fake.documents and instance.local.document(doc_id) is None
        assert instance.preparation([doc_id])['jobs'] == []
    finally:
        fake.upload_release.set()
        close_service(instance)


def test_rag269_delete_waits_for_terminal_progress_callback_and_ignores_late_callback(tmp_path, monkeypatch):
    fake = DeleteServer269()
    instance, _, _ = service(tmp_path, fake)
    doc_id = add269(instance)
    callback_entered, callback_release = threading.Event(), threading.Event()
    original = instance._push_job
    events = []
    instance._progress_callback = events.append

    def blocked(job):
        callback_entered.set()
        assert callback_release.wait(10)
        original(job)

    monkeypatch.setattr(instance, '_push_job', blocked)
    try:
        job_id = instance.prepare([doc_id])['jobs'][0]['id']
        assert callback_entered.wait(3)
        job = instance._jobs[job_id]
        with ThreadPoolExecutor(max_workers=1) as pool:
            deleting = pool.submit(instance.delete_document, doc_id)
            eventually(lambda: doc_id in instance._deleting_documents)
            assert not fake.delete_entered.is_set(), 'terminal state alone is not a completion barrier'
            callback_release.set()
            assert deleting.result(10)['ok']
        count = len(events)
        original(job)
        instance._job_progress(job, {'id': doc_id, 'status': 'ready', 'uploaded_chunks': 1, 'total_chunks': 1})
        assert len(events) == count, 'a retired callback must not resurrect the deleted row'
    finally:
        callback_release.set()
        close_service(instance)


def test_rag269_delete_cancels_active_question_without_committing_late_turn(tmp_path):
    fake = DeleteServer269()
    entered, release = threading.Event(), threading.Event()
    cancels = []

    class Gateway:
        def complete(self, purpose, messages, **kwargs):
            # Gateway._complete validates this exact public cancellation type.
            assert isinstance(kwargs['cancel'], threading.Event)
            cancels.append(kwargs['cancel'])
            entered.set()
            assert release.wait(10)
            if kwargs.get('on_text'):
                kwargs['on_text']('late synthetic reply')
            return SimpleNamespace(text='late synthetic reply')

    instance, _, _ = service(tmp_path, fake, gateway=Gateway())
    doc_id = add269(instance)
    instance.ingest([doc_id])
    fake.search_passages = [{'document_id': doc_id, 'text': 'synthetic', 'seq': 0}]
    session = instance.create([doc_id])['session']
    text_events = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            asking = pool.submit(instance.send, session['id'], 'preserve this question draft', on_text=text_events.append)
            assert entered.wait(3)
            deleting = pool.submit(instance.delete_document, doc_id)
            eventually(lambda: cancels[0].is_set())
            assert not fake.delete_entered.is_set()
            release.set()
            with pytest.raises(BridgeError) as cancelled:
                asking.result(10)
            assert cancelled.value.code == 'CANCELLED'
            assert deleting.result(10)['ok']
        saved = instance.get(session['id'])['session']
        assert saved['messages'] == [] and saved['document_ids'] == []
        assert text_events == [], 'cancelled request must not send late text to the page'
    finally:
        release.set()
        close_service(instance)


def test_rag269_double_delete_shares_remote_operation(tmp_path):
    fake = DeleteServer269()
    instance, _, _ = service(tmp_path, fake)
    doc_id = add269(instance)
    instance.ingest([doc_id])
    fake.block_delete = True
    started = threading.Event()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(instance.delete_document, doc_id)
            assert fake.delete_entered.wait(3)
            def again():
                started.set()
                return instance.delete_document(doc_id)
            second = pool.submit(again)
            assert started.wait(3)
            fake.delete_release.set()
            assert first.result(10)['ok'] and second.result(10)['ok']
        assert len(fake.delete_paths) == 1
        assert instance.local.document(doc_id) is None
    finally:
        fake.delete_release.set()
        close_service(instance)


def test_rag269_offline_never_fake_deletes_local_source_or_history(tmp_path):
    fake = DeleteServer269()
    instance, _, _ = service(tmp_path, fake)
    doc_id = add269(instance)
    instance.ingest([doc_id])
    session = instance.create([doc_id])['session']
    instance.local.append_turn(session['id'], 'q', 'a [1]', [{'document_id': doc_id, 'number': 1, 'text': 'snapshot'}], False)
    before = instance.get(session['id'])['session']
    fake.online = False
    try:
        with pytest.raises(BridgeError):
            instance.delete_document(doc_id)
        assert instance.local.document(doc_id) is not None
        assert instance.get(session['id'])['session'] == before
        assert len(fake.documents) == 1
    finally:
        close_service(instance)


@pytest.mark.parametrize('failure', ['disconnect', 'gateway-504'])
def test_rag269_lost_delete_response_reconciles_after_reopen_and_blocks_reprepare(tmp_path, failure):
    fake = DeleteServer269()
    instance, _, _ = service(tmp_path, fake)
    doc_id = add269(instance)
    instance.ingest([doc_id])
    session = instance.create([doc_id])['session']
    fake.lose_delete_response = failure
    with pytest.raises(BridgeError):
        instance.delete_document(doc_id)
    assert instance.local.document(doc_id) is not None
    assert instance.get(session['id'])['session']['document_ids'] == [doc_id]
    assert pending269(instance) == 1
    with pytest.raises(BridgeError):
        instance.prepare([doc_id])
    close_service(instance)
    fake.online = True
    reopened, _, _ = service(tmp_path, fake)
    try:
        reopened.sources()
        assert reopened.local.document(doc_id) is None
        assert reopened.get(session['id'])['session']['document_ids'] == []
        assert pending269(reopened) == 0
        assert len(fake.delete_paths) == 1, 'reconciliation must not blindly repeat DELETE'
    finally:
        close_service(reopened)


def test_rag269_same_file_in_two_account_clients_does_not_cross_delete(tmp_path):
    # The fake deliberately keeps independent scope dictionaries, matching the
    # Java contract. This checks client identity/routing; Java RLS has its own tests.
    servers = {}
    def scoped(request):
        token = request.headers['authorization']
        return servers.setdefault(token, DeleteServer269())(request)
    def account(subject):
        state = state_for(subject, token='synthetic-' + subject)
        return RagService(store=FakeCourseStore(tmp_path / subject),
            account_id=state.identity.account_id, session_provider=lambda: state,
            api_base_url='https://account.example.test', ca_bundle=CA,
            gateway=FakeGateway(), http_transport=httpx.MockTransport(scoped))
    first = account('account-a269')
    second = account('account-b269')
    try:
        doc_id = add269(first, 'identical file in two accounts')
        assert add269(second, 'identical file in two accounts') == doc_id
        first.ingest([doc_id])
        second.ingest([doc_id])
        assert len(servers) == 2
        first.delete_document(doc_id)
        assert first.local.document(doc_id) is None
        assert second.sources()['documents'][0]['status'] == 'ready'
        assert sorted(len(server.documents) for server in servers.values()) == [0, 1]
        assert second.local.document(doc_id)['uploaded_chunks'] == 1
    finally:
        close_service(first)
        close_service(second)


def test_rag269_remote_success_local_rollback_keeps_intent_and_fence_until_reconciled(tmp_path):
    import sqlite3
    fake = DeleteServer269()
    instance, _, _ = service(tmp_path, fake)
    doc_id = add269(instance)
    instance.ingest([doc_id])
    session = instance.create([doc_id])['session']
    instance.local.append_turn(session['id'], 'question unchanged', 'answer unchanged [1]',
        [{'number': 1, 'document_id': doc_id, 'text': 'original snapshot'}], False)
    before = instance.get(session['id'])['session']
    with instance.local._lock:
        instance.local._db.execute("""CREATE TRIGGER synthetic_delete_failure269
            BEFORE DELETE ON rag_documents BEGIN SELECT RAISE(ABORT, 'synthetic local failure'); END""")
    try:
        with pytest.raises((sqlite3.DatabaseError, BridgeError)):
            instance.delete_document(doc_id)
        assert not fake.documents, 'the server has already committed'
        assert instance.get(session['id'])['session'] == before, 'local detach/citation edits must roll back as one unit'
        assert instance.local.document(doc_id) is not None and pending269(instance) == 1
        with pytest.raises(BridgeError):
            instance.prepare([doc_id])
        with instance.local._lock:
            instance.local._db.execute('DROP TRIGGER synthetic_delete_failure269')
        instance.sources()
        after = instance.get(session['id'])['session']
        assert after['document_ids'] == []
        assert [m['content'] for m in after['messages']] == ['question unchanged', 'answer unchanged [1]']
        assert after['messages'][1]['passages'][0]['deleted'] is True
        assert instance.local.document(doc_id) is None and pending269(instance) == 0
        assert len(fake.delete_paths) == 1
    finally:
        close_service(instance)


def test_rag269_alias_deletion_uses_own_remote_version_and_survivor_still_answers(tmp_path):
    fake = DeleteServer269()
    instance, _, gateway = service(tmp_path, fake)
    first, alias = sha256(b'file-a269').hexdigest(), sha256(b'file-b269').hexdigest()
    for doc_id in (first, alias):
        instance.local.save_document(doc_id, doc_id[:8], 'synthetic.txt', [{'text': 'identical body'}])
    instance.local.set_remote_upload_id(alias, 'version-b269')
    try:
        instance.ingest([first, alias])
        assert instance.local.document(alias)['canonical_id'] == first
        session = instance.create([first])['session']
        deleted = instance.delete_document(alias)
        assert deleted['content_deleted'] is False and deleted['canonical_id'] == first
        assert fake.delete_paths == [f'{API}/documents/version-b269']
        assert instance.local.document(alias) is None
        assert instance.local.document(first)['status'] == 'ready'
        fake.search_passages = [{'document_id': first, 'seq': 0, 'text': 'identical body'}]
        answered = instance.send(session['id'], 'question for surviving material')['session']
        assert answered['messages'][1]['passages'][0]['document_id'] == first
        assert fake.search_queries[-1]['document_ids'] == [first]
        assert len(gateway.calls) == 1
    finally:
        close_service(instance)


def test_rag269_import_of_same_file_cannot_replace_a_deleting_document(tmp_path):
    fake = DeleteServer269()
    instance, _, _ = service(tmp_path, fake)
    path = tmp_path / 'original269.md'
    path.write_text('# Example\n\nA synthetic source for the import/deletion race.', encoding='utf-8')
    try:
        instance._picked_paths.add(str(path.resolve()))
        doc_id = instance.import_files([str(path)])['documents'][0]['id']
        instance.ingest([doc_id])
        fake.block_delete = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            deleting = pool.submit(instance.delete_document, doc_id)
            assert fake.delete_entered.wait(3)
            instance._picked_paths.add(str(path.resolve()))
            with pytest.raises(BridgeError) as caught:
                instance.import_files([str(path)])
            assert caught.value.code == 'RAG_DELETE_PENDING'
            fake.delete_release.set()
            assert deleting.result(10)['ok']
        assert instance.local.document(doc_id) is None and not fake.documents
        assert path.is_file(), 'RAG deletion must not remove the selected original file'
    finally:
        fake.delete_release.set()
        close_service(instance)


def test_rag269_unknown_deletion_rejects_new_session_and_existing_session_add(tmp_path):
    fake = DeleteServer269()
    instance, _, _ = service(tmp_path, fake)
    doc_id = add269(instance)
    instance.ingest([doc_id])
    session = instance.create([doc_id])['session']
    instance.update_documents(session['id'], [])
    try:
        fake.lose_delete_response = True
        with pytest.raises(BridgeError):
            instance.delete_document(doc_id)
        with pytest.raises(BridgeError) as create:
            instance.create([doc_id])
        with pytest.raises(BridgeError) as update:
            instance.update_documents(session['id'], [doc_id])
        assert create.value.code == update.value.code == 'RAG_DELETE_PENDING'
        assert instance.get(session['id'])['session']['document_ids'] == []
        assert len(instance.local.list_sessions()) == 1
    finally:
        close_service(instance)

"""Read/listing behavior while account-owned RAG preparations run."""
import threading

from tests.refactor.rag263.test_rag263_background import (
    FakeCourseStore, GatedFakeTransport, add_doc, close_service, eventually, make_service,
)


def test_rag263_sources_returns_while_upload_is_blocked(tmp_path):
    fake = GatedFakeTransport()
    fake.block_upload = True
    service = make_service(tmp_path, fake)
    document_id = add_doc(service, 'blocked preparation')
    done = threading.Event()
    errors = []

    def read_sources():
        try:
            service.sources()
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    try:
        service.prepare([document_id])
        assert fake.entered.wait(2)
        reader = threading.Thread(target=read_sources)
        reader.start()
        assert done.wait(2), 'a pending upload held the listing behind a network lock'
        assert not errors
    finally:
        fake.release.set()
        if 'reader' in locals():
            reader.join(5)
        close_service(service)


def test_rag263_metadata_change_does_not_reuse_completed_job(tmp_path):
    fake = GatedFakeTransport()
    service = make_service(tmp_path, fake)
    document_id = add_doc(service, 'one body')
    try:
        original = service.prepare([document_id])['jobs'][0]
        eventually(lambda: service.preparation()['jobs'][0]['state'] == 'ready')
        row = service.local.document(document_id)
        service.local.save_document(document_id, 'renamed title', 'renamed-source.txt', row['chunks'])
        changed = service.prepare([document_id])['jobs'][0]
        assert changed['id'] != original['id']
        assert changed['sequence'] > original['sequence']
        eventually(lambda: service.preparation()['jobs'][-1]['state'] == 'ready')
    finally:
        close_service(service)


def test_rag263_remote_loss_does_not_reuse_a_ready_job(tmp_path):
    fake = GatedFakeTransport()
    service = make_service(tmp_path, fake)
    document_id = add_doc(service, 'rebuild remote missing document')
    try:
        original = service.prepare([document_id])['jobs'][0]
        eventually(lambda: service.preparation()['jobs'][0]['state'] == 'ready')
        fake.documents.clear()
        listed = service.sources()['documents'][0]
        assert listed['status'] == 'not_uploaded'
        replacement = service.prepare([document_id])['jobs'][0]
        assert replacement['id'] != original['id']
        eventually(lambda: service.preparation()['jobs'][-1]['state'] == 'ready')
        assert document_id in fake.documents
    finally:
        close_service(service)


def test_rag263_cancel_all_generations_of_the_same_document(tmp_path):
    fake = GatedFakeTransport()
    fake.block_upload = True
    service = make_service(tmp_path, fake)
    document_id = add_doc(service, 'two versions pending')
    try:
        old = service.prepare([document_id])['jobs'][0]
        assert fake.entered.wait(2)
        row = service.local.document(document_id)
        service.local.save_document(document_id, row['title'], row['source'],
                                    [{'text': 'edited while old upload is blocked'}])
        new = service.prepare([document_id])['jobs'][0]
        assert old['id'] != new['id']
        assert [job['state'] for job in service.preparation()['jobs']] == ['running', 'queued']
        service.cancel_preparation([old['id'], new['id']])
        fake.release.set()
        eventually(lambda: all(job['state'] == 'cancelled' for job in service.preparation()['jobs']))
        assert len(fake.upload_ids) == 1  # The queued replacement never uploads.
    finally:
        fake.release.set()
        close_service(service)


def test_rag263_listing_changed_ready_plan_source_never_enqueues_upload(tmp_path):
    class MutableStore(FakeCourseStore):
        body = 'original body'

        def list_plans(self, *, include_archived=False):
            return [{'id': 'plan263', 'title': 'local plan'}]

        def records(self, entity, plan_id=''):
            if entity == 'sources':
                return [{'id': 'source263', 'sha256': 'a' * 64, 'title': 'local source',
                         'adopted': True, 'filename': 'local.txt'}]
            if entity == 'source_chunks':
                return [{'id': 'chunk263', 'source_id': 'source263', 'position': 0, 'text': self.body}]
            return []

    fake = GatedFakeTransport()
    store = MutableStore(tmp_path / 'mutable263')
    service = make_service(tmp_path, fake, store=store)
    try:
        service.sources()
        service.ingest(['a' * 64])
        uploads = len(fake.upload_ids)
        store.body = 'changed after previous upload'
        result = service.sources()
        assert result['documents'][0]['status'] == 'not_uploaded'
        assert len(fake.upload_ids) == uploads
        assert service.preparation()['jobs'] == []
    finally:
        close_service(service)

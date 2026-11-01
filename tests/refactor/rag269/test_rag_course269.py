"""Deleting the RAG projection cannot edit learning data or its sync outbox."""
from datetime import date, timedelta
from hashlib import sha256

from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from tests.refactor.mastery70.support import seed_assessment
from tests.refactor.rag155.test_rag155_backend import rag155_session, service
from tests.refactor.rag263.test_rag263_background import close_service
from tests.refactor.rag269.test_rag_backend269 import DeleteAliasServer


def test_rag269_actual_course_questions_progress_outbox_and_original_file_stay_identical(tmp_path):
    state = rag155_session()
    store = CourseStore(state.identity.account_id, tmp_path)
    today = date(2026, 11, 1)
    original = b'synthetic original file for RAG deletion regression'
    original_hash = store.put_original(original)
    store.create_plan('plan269', goal='learning goal',
        window=PlanWindow(today, today + timedelta(days=4), 60))
    store.add_sources('plan269', [{'id': 'source269', 'kind': 'file',
        'filename': 'synthetic.txt', 'title': 'synthetic learning material',
        'sha256': original_hash, 'adopted': True,
        'chunks': [{'chunk_id': 'chunk269', 'text': 'Original material for classroom and questions.'}]}])
    store.save_outline('plan269', [{'id': 'stage269', 'title': 'stage'}],
        [{'id': 'module269', 'stage_id': 'stage269', 'title': 'module', 'prerequisites': []}])
    store.save_generated_module('plan269', 'module269',
        [{'id': 'point269', 'summary': 'point', 'minutes': 40, 'chunk_ids': ['chunk269']}],
        check_minutes=10, today=today)
    seed_assessment(store, 'plan269')
    store.start_plan('plan269')
    store.add_credit('plan269', 'credit269', 'point269', 10, today, 'tentative')
    before_dump = '\n'.join(store._db.iterdump())
    before_ledger = store.load_ledger('plan269').to_dict()
    before_mastery = store.load_mastery('plan269').to_dict()
    assert store.records('question_bank', 'plan269')
    fake = DeleteAliasServer()
    instance, _, _ = service(tmp_path, fake, store=store)
    try:
        view = instance.sources()
        doc_id = view['documents'][0]['id']
        instance.ingest([doc_id])
        result = instance.delete_document(doc_id)
        projected = next(doc for doc in result['documents'] if doc['id'] == doc_id)
        assert projected['origin'] == 'plan' and projected['status'] == 'not_uploaded'
        assert '\n'.join(store._db.iterdump()) == before_dump
        assert store.load_ledger('plan269').to_dict() == before_ledger
        assert store.load_mastery('plan269').to_dict() == before_mastery
        assert sha256(store.get_original(original_hash)).hexdigest() == original_hash
        # The plan's complete local material remains available to classroom fallback.
        assert store.records('source_chunks', 'plan269')[0]['text'] == 'Original material for classroom and questions.'
    finally:
        close_service(instance)
        store.close()

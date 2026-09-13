"""Producer semantics against a fully migrated, owned PostgreSQL fixture."""
import concurrent.futures
import unittest
from sqlalchemy import text
from test_vertical_slice import SyncTests


class ProducerTests(unittest.TestCase):
    setUpClass = classmethod(SyncTests.setUpClass.__func__)
    tearDownClass = classmethod(SyncTests.tearDownClass.__func__)
    setUp = SyncTests.setUp
    tearDown = SyncTests.tearDown
    server = SyncTests.server
    device = SyncTests.device
    seed = SyncTests.seed

    def test_empty_scope_never_claims_empty_account_or_zero_history(self):
        c, _, _ = self.device()
        state = c.rehydrate()
        self.assertEqual(state['learning_state']['status'], 'PARTIAL_UNSUPPORTED')
        self.assertFalse(state['learning_state']['known_empty'])
        self.assertFalse(state['learning_state']['supported_scope_initialized'])
        self.assertTrue(state['learning_state']['supported_scope_empty'])
        self.assertEqual(state['learning_state']['account_initialized'], 'UNKNOWN')
        self.assertIsNone(state['observation']['unsupported_counts']['messages'])
        self.seed(c)
        self.assertTrue(c.rehydrate()['learning_state']['supported_scope_initialized'])

    def test_shared_classified_resources_and_progress_provenance(self):
        c, t, _ = self.device()
        self.seed(c)
        c.write('plan','second-plan', request_id='second', expected_revision=0,
                payload={'title':'Second', 'resource_ids':['resource-1']})
        legacy=c.rehydrate()
        self.assertEqual(legacy['resources'][0]['product_classification'], 'UNKNOWN')
        self.assertEqual(legacy['resources'][0]['plan_ids'], ['plan-1','second-plan'])
        for n, classification in enumerate(('USER_FILE','MODEL','OTHER','UNKNOWN'), 1):
            c.write('resource','resource-1',request_id='class'+str(n),expected_revision=n,
                payload=dict(filename='unchanged.pdf',media_type='application/pdf',sha256='a'*64,
                             size_bytes=12,product_classification=classification))
            state=c.rehydrate()
            self.assertEqual(state['resources'][0]['product_classification'], classification)
        for item in state['progress_provenance']:
            self.assertEqual(item['source'], 'SERVER_CONFIRMED')
            self.assertEqual(item['snapshot_revision'], state['snapshot_revision'])
            self.assertFalse(item['trusted_cache'])
        self.assertEqual(state['observation']['missing_resources'], ['resource-1'])
        marker=state['snapshot_revision'].removeprefix('pg-snapshot:')
        with self.admin.connect() as db:
            self.assertEqual(db.execute(text('SELECT CAST(:s AS pg_snapshot)::text'), {'s':marker}).scalar_one(),marker)

    def test_invalid_classification_and_backward_replay(self):
        c, t, _ = self.device()
        self.seed(c)
        body=dict(filename='fixture.pdf',media_type='application/pdf',sha256='a'*64,size_bytes=12)
        replay=c.write('resource','resource-1',request_id='r1',expected_revision=0,payload=body)
        self.assertEqual(replay['revision'],1)
        response=t.put(c.PREFIX+'/entities/resource/resource-1',json=dict(request_id='bad',
            expected_revision=1,payload=dict(body,product_classification='GUESS_FROM_EXTENSION')))
        self.assertEqual(response.status_code,422)

    def test_single_observation_under_concurrent_atomic_multi_entity_writes(self):
        c, _, _ = self.device()
        self.seed(c)
        scope=c.rehydrate()['snapshot_scope']
        where='issuer=:issuer AND subject_id=:subject_id AND organization_id=:organization_id AND workspace_id=:workspace_id'
        def writes():
            for revision in range(2,20):
                with self.admin.begin() as db:
                    db.execute(text('UPDATE structured_sync_entities SET revision=:revision WHERE '+where),
                               dict(scope,revision=revision))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(writes)
            for _ in range(20):
                state=c.rehydrate()
                revisions={e['revision'] for group in ('plans','progress','resources') for e in state[group]}
                self.assertEqual(len(revisions),1)
                self.assertEqual(state['snapshot_revision'],state['observation']['snapshot_revision'])
            future.result()

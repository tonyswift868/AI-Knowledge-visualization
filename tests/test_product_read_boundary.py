"""Reads remain authorized and available despite an unrelated pending upload."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fastapi.responses import Response
from app.product_command_boundary import invoke
from app.product_sync import ProductSyncFailure


class Producer:
    def __init__(self, pending='PRODUCT_SYNC_CONFLICT', authorized=True):
        self.pending, self.authorized = pending, authorized
        self.flushes = 0

    def authorize(self):
        if not self.authorized:
            raise ProductSyncFailure('PRODUCT_SYNC_DENIED')

    def flush(self):
        self.flushes += 1
        if self.pending:
            raise ProductSyncFailure(self.pending)

    def status(self):
        return {'status': self.pending or 'SERVER_ACKNOWLEDGED', 'pending': int(bool(self.pending))}


class ReadBoundaryTests(unittest.TestCase):
    def request(self, method='GET'):
        return SimpleNamespace(method=method, url=SimpleNamespace(path='/api/active-teaching/workspaces/ws/materials', query=''), headers={})

    def test_bad_queue_does_not_block_read_and_is_not_reported_acknowledged(self):
        for code in ('PRODUCT_SYNC_CONFLICT', 'PRODUCT_SYNC_INVALID_PROJECTION', 'PRODUCT_SYNC_PENDING'):
            with self.subTest(code=code):
                p = Producer(code)
                async def handler(request):
                    return Response('existing local structured state')
                response = asyncio.run(invoke(self.request(), handler, p))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['X-MentorFlow-Product-Sync'], code)
                self.assertEqual(p.flushes, 0)

    def test_other_account_or_expired_authority_still_cannot_read(self):
        called = []
        async def handler(request):
            called.append(True)
            return Response('must not be read')
        with self.assertRaisesRegex(ProductSyncFailure, 'PRODUCT_SYNC_DENIED'):
            asyncio.run(invoke(self.request(), handler, Producer(authorized=False)))
        self.assertEqual(called, [])

    def test_write_still_requires_pending_queue_commit(self):
        called = []
        async def handler(request):
            called.append(True)
            return Response('must not execute')
        with self.assertRaisesRegex(ProductSyncFailure, 'PRODUCT_SYNC_CONFLICT'):
            asyncio.run(invoke(self.request('POST'), handler, Producer()))
        self.assertEqual(called, [])

    def test_local_database_binding_is_checked_before_handler(self):
        class WrongLocalScope(Producer):
            def status(self):
                raise ProductSyncFailure('PRODUCT_SYNC_SCOPE_MISMATCH')
        called = []
        async def handler(request):
            called.append(True)
            return Response('stale account cache')
        with self.assertRaisesRegex(ProductSyncFailure, 'PRODUCT_SYNC_SCOPE_MISMATCH'):
            asyncio.run(invoke(self.request(), handler, WrongLocalScope()))
        self.assertEqual(called, [])

    def test_clean_read_has_truthful_acknowledged_status(self):
        p = Producer(None)
        async def handler(request):
            return Response('restored state')
        response = asyncio.run(invoke(self.request(), handler, p))
        self.assertEqual(response.headers['X-MentorFlow-Product-Sync'], 'SERVER_ACKNOWLEDGED')
        self.assertEqual(p.flushes, 0)


if __name__ == '__main__':
    unittest.main()

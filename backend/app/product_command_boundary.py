"""Command receipts surround existing handlers; delivery still uses ProductSyncProducer."""
import asyncio
import hashlib
import re
from uuid import uuid4
from fastapi.responses import JSONResponse, Response
from .product_sync import ProductSyncFailure
from .product_runtime_state import export_plan_records, restore_plan_records, export_review, restore_review


def supported_path(path):
    if path.endswith('/export') or '/sources/archive/preview' in path:
        return False  # Device export / ingestion preview, not a cloud mutation.
    if path.startswith('/api/workspaces'):
        return bool(re.fullmatch(r'/api/workspaces(?:/[^/]+(?:/(?:sources(?:/.*)?|goal|persona|feasibility/(?:analyze|confirm)|plans(?:/.*)?|sessions))?)?',path))
    return path.startswith(('/api/active-teaching/', '/api/plan-product/', '/api/review/',
        '/api/product-resources', '/api/product-settings', '/api/plans/', '/api/tasks/',
        '/api/sources/', '/api/sessions/', '/api/learning-runs/', '/api/lessons/'))


async def invoke(request, call_next, producer):
    path = request.url.path
    await asyncio.to_thread(producer.authorize)
    if path == '/api/product-sync/status' and request.method == 'GET':
        return JSONResponse(content=await asyncio.to_thread(producer.status))
    if path == '/api/product-sync/conflicts' and request.method == 'GET':
        return JSONResponse(content=await asyncio.to_thread(producer.conflicts))
    if path.startswith('/api/product-sync/conflicts/') and request.method == 'POST':
        body = await request.json()
        if set(body) != {'resolution','expected_server_revision'} or body['resolution'] != 'keep_local':
            raise ProductSyncFailure('PRODUCT_SYNC_CONTRACT_REJECTED')
        await asyncio.to_thread(producer.retry_conflict,path.rsplit('/',1)[-1],body['expected_server_revision'])
        return JSONResponse(content=producer.status())
    # Reading already-restored state must not depend on delivery of an unrelated
    # invalid/conflicting intent. Authorization above remains mandatory; writes
    # and explicit refresh still require the existing commit/restore boundary.
    read_only = request.method in {'GET', 'HEAD', 'OPTIONS'}
    if read_only:
        # status() also verifies the local database's account/workspace binding.
        # Check it before a handler can observe rows from a stale mounted cache.
        await asyncio.to_thread(producer.status)
    else:
        await asyncio.to_thread(producer.flush)
    if path == '/api/product-sync/refresh' and request.method == 'POST':
        snapshot = await asyncio.to_thread(producer.send, {'operation':'snapshot'})
        await asyncio.to_thread(producer.restore, snapshot)
        return JSONResponse(content=producer.status())
    tracked = supported_path(path)
    command_id = None
    if tracked and request.method in {'POST','PUT','PATCH','DELETE'}:
        command_id = request.headers.get('X-MentorFlow-Command-ID') or uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', command_id):
            raise ProductSyncFailure('PRODUCT_SYNC_COMMAND_ID_REQUIRED')
        # Only a digest is journaled; request bytes may include local uploads.
        body = await request.body()
        content_type = request.headers.get('content-type','')
        if content_type.startswith('multipart/form-data;'):
            boundary = re.search(r'boundary="?([^";]+)', content_type)
            if boundary:
                body = body.replace(b'--'+boundary.group(1).encode(),b'--MENTORFLOW-COMMAND-BOUNDARY')
        fingerprint = hashlib.sha256(request.method.encode()+b'\0'+(path+'?'+request.url.query).encode()+b'\0'+body).hexdigest()
        old = await asyncio.to_thread(producer.begin_command, command_id, fingerprint)
        if old:
            return Response(old['response'].encode(), status_code=old['status_code'],
                media_type='application/json', headers={'X-MentorFlow-Command-ID': command_id,
                'X-MentorFlow-Product-Sync':'SERVER_ACKNOWLEDGED', 'Cache-Control':'no-store'})
    if path.startswith('/api/plan-product/'):
        state = await asyncio.to_thread(producer.runtime_states, 'product_plan_state')
        request.app.state.plan_product_service = restore_plan_records(state)
    if path.startswith('/api/review/'):
        state = await asyncio.to_thread(producer.runtime_states, 'product_review_state')
        request.app.state.review_product_service = restore_review(state)
        request.app.state.review_product_service.account_sync_enabled = True
    response = await call_next(request)
    states = []
    if path.startswith('/api/plan-product/') and not read_only:
        states = [('product_plan_state', key, value) for key,value in export_plan_records(request.app.state.plan_product_service).items()]
    if path.startswith('/api/review/') and not read_only:
        states = [('product_review_state', key, value) for key,value in export_review(request.app.state.review_product_service).items()]
    if command_id or states:
        content = b''.join([part async for part in response.body_iterator])
        await asyncio.to_thread(producer.complete_command, command_id, content.decode('utf-8'), response.status_code, states)
        response = Response(content, status_code=response.status_code, headers=dict(response.headers), background=response.background)
    if not read_only:
        await asyncio.to_thread(producer.flush)
    response.headers['X-MentorFlow-Product-Sync-Scope'] = 'SUPPORTED_PRODUCT_RECORDS'
    status = (await asyncio.to_thread(producer.status))['status'] if read_only and tracked else 'SERVER_ACKNOWLEDGED'
    response.headers['X-MentorFlow-Product-Sync'] = status if tracked else 'OUTSIDE_PRODUCT_SYNC_CONTRACT'
    if command_id:
        response.headers['X-MentorFlow-Command-ID'] = command_id
    return response

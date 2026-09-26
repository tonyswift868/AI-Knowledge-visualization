"""HTTP adapter consuming the existing identity middleware's verified scope."""
from typing import Literal
from fastapi import APIRouter, Request, Query, Response, HTTPException
from fastapi.responses import JSONResponse
from app.enterprise.identity.oidc import IdentityError
from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
from app.enterprise_tenancy.structured_sync import Write, Delete
from refactor.server.account_object_storage import MAX_OBJECT_BYTES
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(prefix='/api/enterprise/sync/v1')

def call(request, method, *args, **kwargs):
    actor = getattr(request.state, 'actor_context', None)
    tenant = getattr(request.state, 'tenant_context', None)
    if actor is None or tenant is None:
        raise IdentityError('ENTERPRISE_AUTH_REQUIRED', 'Authenticated workspace required', status=401)
    service = getattr(request.app.state, 'structured_sync_service', None)
    if service is None:
        raise IdentityError('SYNC_NOT_READY', 'Structured sync unavailable', status=503)
    try:
        result = getattr(service, method)(actor, tenant, *args, **kwargs)
    except EnterprisePersistenceRequestError as exc:
        return JSONResponse({'code': exc.code, 'sync_status': 'QUERY_FAILED'}, status_code=exc.status, headers={'Cache-Control': 'no-store'})
    except SQLAlchemyError:
        # Do not serialize SQL, connection strings, bound values or partial state.
        return JSONResponse({'code': 'SYNC_QUERY_FAILED', 'sync_status': 'QUERY_FAILED'},
                            status_code=503, headers={'Cache-Control': 'no-store'})
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})

@router.put('/entities/{kind}/{entity_id}')
def write(request: Request, kind: Literal['plan','progress','resource','task','calendar','review','learner_profile','learning_run','history','setting'], entity_id: str, body: Write):
    return call(request, 'write', kind, entity_id, body)

@router.post('/entities/{kind}/{entity_id}/delete')
def delete(request: Request, kind: Literal['plan','progress','resource','task','calendar','review','learner_profile','learning_run','history','setting'], entity_id: str, body: Delete):
    return call(request, 'write', kind, entity_id, body, deleting=True)

@router.get('/snapshot')
def snapshot(request: Request, required_scope: str | None = Query(default=None, max_length=512),
             scope_version: str | None = Query(default=None, max_length=64)):
    requested = None if required_scope is None else required_scope.split(',')
    return call(request, 'snapshot', requested_scope=requested, scope_version=scope_version)


@router.get('/changes')
def changes(request: Request,
            since: int = Query(default=0, ge=0, le=9_223_372_036_854_775_807),
            kind: Literal['plan','progress','resource','task','calendar','review','learner_profile','learning_run','history','setting'] = 'learning_run',
            limit: int = Query(default=100, ge=1, le=500)):
    return call(request, 'changes', since=since, kind=kind, limit=limit)


def _object_store(request: Request):
    store = getattr(request.app.state, 'account_object_storage', None)
    if store is None:
        raise IdentityError('SYNC_OBJECTS_NOT_READY', 'Learning resource storage unavailable', status=503)
    actor = getattr(request.state, 'actor_context', None)
    tenant = getattr(request.state, 'tenant_context', None)
    if actor is None or tenant is None:
        raise IdentityError('ENTERPRISE_AUTH_REQUIRED', 'Authenticated workspace required', status=401)
    return store, actor, tenant


@router.put('/objects/{sha256}')
async def upload_object(request: Request, sha256: str):
    store, actor, tenant = _object_store(request)
    declared_length = request.headers.get('Content-Length')
    if declared_length and (not declared_length.isdigit() or int(declared_length) > MAX_OBJECT_BYTES):
        raise HTTPException(status_code=413, detail='learning resource exceeds 8 MiB')
    # Do not rely on Content-Length: chunked requests must be bounded before
    # they are assembled in memory.  The object store performs the final
    # digest/size check and atomically replaces a damaged retry target.
    chunks = []
    total = 0
    async for chunk in request.stream():
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise HTTPException(status_code=422, detail='invalid object body')
        total += len(chunk)
        if total > MAX_OBJECT_BYTES:
            raise HTTPException(status_code=413, detail='learning resource exceeds 8 MiB')
        chunks.append(bytes(chunk))
    data = b''.join(chunks)
    supplied_digest = request.headers.get('X-MentorFlow-SHA256')
    if supplied_digest is not None and supplied_digest != sha256:
        return Response(status_code=422)
    expected_size = request.headers.get('X-MentorFlow-Size')
    if expected_size is not None and (not expected_size.isdigit() or int(expected_size) != len(data)):
        return Response(status_code=422)
    try:
        size = store.put(actor, tenant, sha256, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'sha256': sha256, 'size': size}


@router.get('/objects/{sha256}')
def download_object(request: Request, sha256: str):
    store, actor, tenant = _object_store(request)
    try:
        data = store.get(actor, tenant, sha256)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='resource original is not available') from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=data, media_type='application/octet-stream',
                    headers={'X-MentorFlow-SHA256': sha256, 'Cache-Control': 'no-store'})

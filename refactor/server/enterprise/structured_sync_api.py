"""HTTP adapter consuming the existing identity middleware's verified scope."""
from typing import Literal
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from refactor.server.enterprise.identity.oidc import IdentityError
from refactor.server.enterprise_tenancy.models import EnterprisePersistenceRequestError
from refactor.server.enterprise_tenancy.structured_sync import Write, Delete, BulkWrite
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

@router.post('/entities/bulk')
def write_many(request: Request, body: BulkWrite):
    return call(request, 'write_many', body)

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

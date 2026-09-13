"""HTTP adapter consuming the existing identity middleware's verified scope."""
from typing import Literal
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.enterprise.identity.oidc import IdentityError
from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
from app.enterprise_tenancy.structured_sync import Write, Delete

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
        return JSONResponse({'code': exc.code}, status_code=exc.status, headers={'Cache-Control': 'no-store'})
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})

@router.put('/entities/{kind}/{entity_id}')
def write(request: Request, kind: Literal['plan','progress','resource'], entity_id: str, body: Write):
    return call(request, 'write', kind, entity_id, body)

@router.post('/entities/{kind}/{entity_id}/delete')
def delete(request: Request, kind: Literal['plan','progress','resource'], entity_id: str, body: Delete):
    return call(request, 'write', kind, entity_id, body, deleting=True)

@router.get('/snapshot')
def snapshot(request: Request):
    return call(request, 'snapshot')

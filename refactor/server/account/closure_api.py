"""MF-D02 account-only prepare and operation-scoped recovery HTTP boundary."""
import asyncio
import json
import re
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from refactor.server.enterprise.account_workspaces import authenticate_account
from refactor.server.enterprise.identity.oidc import IdentityError
from .closure import ClosureError, OPERATION, AccountClosure
from .closure_provider import KeycloakClosureProvider
from .closure_store import PostgresClosureStore

PREFIX = "/api/enterprise/account/closure"
router = APIRouter()
_PATH = re.compile(re.escape(PREFIX) + r"/[^/]{1,128}(?:/commit)?")

def is_closure_path(path):
    return _PATH.fullmatch(path) is not None

def _transport(request):
    forbidden = ("Origin", "Cookie", "X-MentorFlow-Organization-ID", "X-MentorFlow-Workspace-ID")
    if request.url.scheme != "https" or any(name in request.headers for name in forbidden):
        raise ClosureError("CLOSURE_TRANSPORT_REQUIRED", 403)

def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value: raise ValueError()
        value[key] = item
    return value

async def _body(request, key, expected_type):
    raw = bytearray()
    try:
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise ValueError()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 4096: raise ValueError()
            raw.extend(chunk)
        value = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(value, dict) or set(value) != {key} or type(value[key]) is not expected_type:
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise ClosureError("CLOSURE_INPUT_INVALID", 400) from None
    finally:
        raw[:] = b"\0" * len(raw)

def _runtime(request):
    runtime = getattr(request.app.state, "enterprise_identity_runtime", None)
    if runtime is None or getattr(runtime, "closure_store", None) is None:
        raise ClosureError("CLOSURE_UNAVAILABLE")
    return runtime

def _service(runtime, *, mutation=True):
    lifecycle = getattr(runtime, "lifecycle", None)
    if mutation and (lifecycle is None or not lifecycle.active):
        raise ClosureError("CLOSURE_UNAVAILABLE")
    provider = KeycloakClosureProvider(lifecycle.admin) if lifecycle is not None and lifecycle.active else None
    return AccountClosure(runtime.closure_store, provider, runtime)

def _response(result):
    return JSONResponse(result.payload, status_code=result.status, headers={"Cache-Control": "no-store"})

def _error(exc, action, operation):
    payload = {"code": exc.code, "message": "注销请求未完成，不能视为注销成功。"}
    if action != "prepare":
        payload.update(state=None, operation_id=operation if isinstance(operation,str) and OPERATION.fullmatch(operation) else None,
                       account_id=None)
    return JSONResponse(payload,
                        status_code=exc.status, headers={"Cache-Control": "no-store"})

async def _handle(request, action, operation=None):
    value = {}
    try:
        _transport(request)
        runtime = _runtime(request)
        if action == "prepare":
            if "X-MentorFlow-Closure-Token" in request.headers:
                raise ClosureError("CLOSURE_AUTH_BOUNDARY_CONFLICT", 403)
            if not request.headers.get("Authorization", "").startswith("Bearer mfn1_a_"):
                raise ClosureError("CLOSURE_NATIVE_AUTH_REQUIRED", 401)
            await authenticate_account(request, runtime)
            principal = request.state.account_principal
            value = await _body(request, "current_password", str)
            if not 1 <= len(value["current_password"]) <= 1024:
                raise ClosureError("CLOSURE_INPUT_INVALID", 400)
            runtime.native_sessions.throttle(principal.subject, request.client.host if request.client else "unknown")
            return _response(await _service(runtime).prepare(principal, value["current_password"]))
        if "Authorization" in request.headers:
            raise ClosureError("CLOSURE_AUTH_BOUNDARY_CONFLICT", 403)
        token = request.headers.get("X-MentorFlow-Closure-Token")
        service = _service(runtime, mutation=False)
        if action == "status":
            return _response(await service.status(operation, token))
        value = await _body(request, "confirmed", bool)
        if value["confirmed"] is not True:
            raise ClosureError("CLOSURE_CONFIRMATION_REQUIRED", 400)
        return _response(await service.commit(operation, token))
    except (ClosureError, IdentityError) as exc:
        return _error(exc, action, operation)
    except Exception:
        return _error(ClosureError("CLOSURE_UNAVAILABLE"), action, operation)
    finally:
        value.clear()

@router.post(PREFIX + "/prepare")
async def prepare(request: Request):
    return await _handle(request, "prepare")

@router.post(PREFIX + "/{operation_id}/commit")
async def commit(operation_id: str, request: Request):
    return await _handle(request, "commit", operation_id)

@router.get(PREFIX + "/{operation_id}")
async def status(operation_id: str, request: Request):
    return await _handle(request, "status", operation_id)

def install_store(runtime, engine, issuer):
    runtime.closure_store = PostgresClosureStore(engine, issuer)

async def assert_principal_open(runtime, principal):
    store = getattr(runtime, "closure_store", None)
    if store is None:
        return
    try:
        await asyncio.to_thread(store.ensure_open, principal.issuer, principal.subject)
    except ClosureError as exc:
        raise IdentityError(exc.code, "账号注销处理中或身份已关闭。" if exc.status == 403 else "账号状态暂不可用。",
                            status=exc.status) from None

async def guard_native_result(runtime, result):
    identity = result.get("identity")
    if identity is not None:
        from types import SimpleNamespace
        principal = SimpleNamespace(issuer=identity["issuer"], subject=identity["subject"])
        try:
            await assert_principal_open(runtime, principal)
        except IdentityError:
            runtime.native_sessions.revoke_subject(principal.subject)
            raise

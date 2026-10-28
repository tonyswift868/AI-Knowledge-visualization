"""MVP-only authenticated account discovery, before tenant selection."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from refactor.server.enterprise.contracts_v1 import ActorKind
from refactor.server.enterprise.identity.oidc import IdentityError
from refactor.server.enterprise_tenancy.models import EnterprisePersistenceRequestError


DISCOVERY_PATH = "/api/enterprise/account/workspaces"
INITIALIZE_PATH = DISCOVERY_PATH + "/initialize"
MAX_INITIALIZE_BODY_BYTES = 4096
ACCOUNT_WORKSPACE_PATHS = frozenset({DISCOVERY_PATH, INITIALIZE_PATH})


class InitializeOwnWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoundedInitializeBodyRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded_handler(request: Request):
            if request.method != "POST" or request.url.path != INITIALIZE_PATH:
                return await original(request)
            receive = request.receive
            received = 0

            async def bounded_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    body = message.get("body", b"")
                    remaining = MAX_INITIALIZE_BODY_BYTES - received
                    if len(body) > remaining:
                        raise HTTPException(status_code=413, detail="Request body too large")
                    received += len(body)
                return message

            request._receive = bounded_receive
            return await original(request)

        return bounded_handler


router = APIRouter(route_class=BoundedInitializeBodyRoute)


async def authenticate_account(request: Request, runtime):
    """Reuse the unchanged token validator; tenant headers cannot authorize bootstrap."""
    authorization = request.headers.get("Authorization", "").strip()
    if request.cookies.get(runtime.config.session_cookie_name):
        raise IdentityError("AUTH_BOUNDARY_CONFLICT", "Account bootstrap requires bearer authentication")
    if not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
        raise IdentityError("ENTERPRISE_AUTH_REQUIRED", "A verified bearer account is required")
    principal = await runtime.authenticate_access_token(authorization[7:], request)
    if principal.actor_kind is not ActorKind.HUMAN:
        raise IdentityError("ACCOUNT_BOOTSTRAP_HUMAN_REQUIRED", "A human account is required", status=403)
    # Reject rather than accidentally treating a cached client scope as authority.
    if any(request.headers.get(name) for name in (
        "X-MentorFlow-Organization-ID", "X-MentorFlow-Workspace-ID")):
        raise IdentityError("ACCOUNT_BOOTSTRAP_CONTEXT_NOT_ALLOWED",
                            "Discover account workspaces without tenant headers", status=422)
    request.state.account_principal = principal


async def _call(request, method):
    principal = getattr(request.state, "account_principal", None)
    service = getattr(request.app.state, "account_workspace_bootstrap", None)
    if principal is None:
        raise IdentityError("ENTERPRISE_AUTH_REQUIRED", "A verified account is required")
    if service is None:
        raise IdentityError("ENTERPRISE_PERSISTENCE_NOT_READY", "Account workspace persistence is not ready", status=503)
    try:
        return await run_in_threadpool(getattr(service, method), principal)
    except EnterprisePersistenceRequestError as exc:
        raise IdentityError(exc.code, exc.message, status=exc.status) from exc


@router.get(DISCOVERY_PATH)
async def discover_account_workspaces(request: Request):
    return await _call(request, "discover")


@router.post(INITIALIZE_PATH)
async def initialize_account_workspaces(request: Request, body: InitializeOwnWorkspace):
    return await _call(request, "initialize")

"""HTTPS subprocess entry for the production Python reference app in 144 tests."""
from __future__ import annotations

import os
import uvicorn
from fastapi import Request
from refactor.server.app import app


@app.post("/contract144/browser-cookie")
async def contract144_browser_cookie(request: Request):
    """Evidence of Python's actual private cookie store; never a shared Java adapter."""
    import secrets
    from refactor.server.enterprise.identity.models import EnterpriseSession
    value=await request.json()
    runtime=request.app.state.enterprise_identity_runtime
    principal=runtime.native_sessions.authenticate(value["access_token"])
    session=EnterpriseSession(session_id=secrets.token_urlsafe(32),principal=principal,
        created_at=principal.issued_at,refreshed_at=principal.issued_at,
        session_expires_at=None,access_token_expires_at=principal.expires_at,
        selected_organization_id="org-144",selected_workspace_id="workspace-144")
    runtime.client.store.put(session,refresh_token=None)
    return {"cookie_name":runtime.config.session_cookie_name,"session_id":session.session_id}


@app.get("/api/enterprise/contract144/context")
async def contract144_context(request: Request):
    actor = getattr(request.state, "actor_context", None)
    tenant = getattr(request.state, "tenant_context", None)
    return {
        "actor": actor.model_dump(mode="json") if actor is not None else None,
        "tenant": tenant.model_dump(mode="json") if tenant is not None else None,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]),
        ssl_certfile=os.environ["CONTRACT144_SERVER_CERT"],
        ssl_keyfile=os.environ["CONTRACT144_SERVER_KEY"],
        log_level="error", access_log=False, lifespan="on", proxy_headers=False)

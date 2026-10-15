"""Subprocess entry for the real Python reference HTTP server used by 142 tests."""
from __future__ import annotations

import os
import json

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse

from contract_support142 import FakeDirectory142, FixtureClosure142
from refactor.server.enterprise.contracts_v1 import TenantMode
from refactor.server.enterprise.identity.config import EnterpriseIdentityConfig, KeycloakOidcConfig
from refactor.server.enterprise.identity.keycloak import KeycloakOidcClient
from refactor.server.enterprise.identity.runtime import EnterpriseIdentityRuntime
from refactor.server import app as entry


issuer=os.environ["CONTRACT142_ISSUER"]
configuration=EnterpriseIdentityConfig(
    mode=TenantMode.ENTERPRISE_SERVER,
    keycloak=KeycloakOidcConfig(
        issuer=issuer, client_id="mentorflow-desktop", audience="mentorflow-api",
        redirect_uri="http://127.0.0.1/callback", allow_loopback_http=True,
        max_token_lifetime_seconds=3600),
    browser_allowed_origins=("http://127.0.0.1",))
directory=FakeDirectory142(); directory.issuer=issuer
identity=EnterpriseIdentityRuntime(configuration,client=KeycloakOidcClient(configuration),directory=directory)
identity.closure_store=FixtureClosure142(os.environ["CONTRACT142_HEALTH_FILE"])
entry.app.state.enterprise_identity_runtime=identity
entry.account_core_health_registry.started=True
entry.account_core_health_registry._probes={
    "email":(lambda:(False,"EMAIL_AUTHORITY_NOT_CONFIGURED"),False),
    "keycloak_discovery":(lambda:(True,"KEYCLOAK_DISCOVERY_OK"),True),
    "postgres":(lambda:(True,"POSTGRES_REACHABLE"),True),
}

def _health_state():
    with open(os.environ["CONTRACT142_HEALTH_FILE"],encoding="utf-8") as stream:
        return json.load(stream)

def _apply_health_state():
    state=_health_state()
    entry.account_core_health_registry.started=bool(state.get("started",True))
    entry.account_core_health_registry._probes={
        "email":(lambda:(False,"EMAIL_AUTHORITY_NOT_CONFIGURED"),False),
        "keycloak_discovery":(lambda:((ready:=bool(_health_state().get("keycloak",True))),
            "KEYCLOAK_DISCOVERY_OK" if ready else "KEYCLOAK_DISCOVERY_UNREACHABLE"),True),
        "postgres":(lambda:((ready:=bool(_health_state().get("postgres",True))),
            "POSTGRES_REACHABLE" if ready else "POSTGRES_UNREACHABLE"),True),
    }

_apply_health_state()


@entry.app.get("/api/enterprise/contract142/context")
async def contract_context142(request: Request):
    actor=getattr(request.state,"actor_context",None)
    tenant=getattr(request.state,"tenant_context",None)
    if actor is None or tenant is None:
        return JSONResponse({"error":"missing context"},status_code=500)
    return {"actor":actor.model_dump(mode="json"),"tenant":tenant.model_dump(mode="json")}

@entry.app.put("/contract142/health-state")
async def update_contract_health142(request: Request):
    payload=await request.json()
    with open(os.environ["CONTRACT142_HEALTH_FILE"],"w",encoding="utf-8") as stream:
        json.dump(payload,stream)
    _apply_health_state()
    return {"updated":True}


if __name__=="__main__":
    uvicorn.run(entry.app,host="127.0.0.1",port=int(os.environ["CONTRACT142_PORT"]),
                log_level="error",access_log=False,lifespan="off",proxy_headers=False)

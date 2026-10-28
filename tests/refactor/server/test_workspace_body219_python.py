from fastapi import FastAPI
from fastapi.testclient import TestClient

from refactor.server.enterprise import account_workspaces


def test_initialize_route_bounds_body_and_preserves_validation_contract(monkeypatch):
    calls = []

    async def fake_call(_request, method):
        calls.append(method)
        return {"initialized": True}

    monkeypatch.setattr(account_workspaces, "_call", fake_call)
    app = FastAPI()
    app.include_router(account_workspaces.router)
    client = TestClient(app)

    accepted = client.post(account_workspaces.INITIALIZE_PATH, content=b"{}",
                           headers={"content-type": "application/json"})
    assert accepted.status_code == 200 and accepted.json() == {"initialized": True}
    assert calls == ["initialize"]

    oversized = client.post(account_workspaces.INITIALIZE_PATH,
        content=b"{}" + b" " * (1_048_589 - 2), headers={"content-type": "application/json"})
    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "Request body too large"}
    assert calls == ["initialize"]

    malformed = client.post(account_workspaces.INITIALIZE_PATH, content=b"{",
                            headers={"content-type": "application/json"})
    extra = client.post(account_workspaces.INITIALIZE_PATH, json={"unexpected": 1})
    assert malformed.status_code == 422
    assert extra.status_code == 422
    assert calls == ["initialize"]

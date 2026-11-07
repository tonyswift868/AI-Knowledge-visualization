from types import SimpleNamespace as NS
import hashlib

import certifi
import httpx
import pytest

from refactor.learning.sync_identity import PlanError, PlanScope
from refactor.learning.sync_transport import SyncTransport


@pytest.fixture
def transport_factory():
    transports = []

    def make(handler):
        issuer, subject = "https://identity.invalid/realm", "test-user"
        hashes = ":".join(hashlib.sha256(value.encode()).hexdigest()
                           for value in (issuer, subject))
        account_id = "mfa1_" + hashlib.sha256(
            ("mentorflow.account.v1:" + hashes).encode()).hexdigest()
        scope = PlanScope(account_id, issuer, subject, "org-fixture", "workspace-fixture")
        login = NS(
            identity=NS(account_id=account_id, issuer=issuer, subject=subject,
                         organization_id=scope.organization_id, workspace_id=scope.workspace_id),
            server_state={
                "authenticated": True,
                "account": {"account_id": account_id, "subject_id": subject},
                "context": {"organization_id": scope.organization_id,
                            "workspace_id": scope.workspace_id},
            },
            access_token="fixture-token",
        )
        instance = SyncTransport(
            account_id=account_id,
            session_provider=lambda: login,
            api_base_url="https://api.invalid",
            ca_bundle=certifi.where(),
            http_transport=httpx.MockTransport(handler),
        )
        transports.append(instance)
        return instance

    yield make
    for instance in transports:
        instance.close()


def test_tombstone_posts_atomic_delete_without_placeholder(transport_factory):
    seen = {}
    kind, entity_id = "learning_run", "refactor.v1:course_record:abc123"

    def handle(request):
        seen["method"] = request.method
        seen["url"] = request.url.path
        seen["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={
            "kind": kind,
            "entity_id": entity_id,
            "revision": 1,
            "deleted": True,
            "payload": {},
        })

    transport = transport_factory(handle)
    result = transport.tombstone(kind, entity_id, request_id="delete-plan-1", expected_revision=0)

    assert seen["method"] == "POST"
    assert seen["url"] == f"/api/enterprise/sync/v1/entities/{kind}/{entity_id}/tombstone"
    assert seen["body"] == '{"request_id":"delete-plan-1","expected_revision":0}'
    assert result["revision"] == 1 and result["deleted"] is True and result["payload"] == {}


@pytest.mark.parametrize("status", [404, 405])
def test_old_backend_tombstone_unsupported_is_explicit(transport_factory, status):
    transport = transport_factory(lambda request: httpx.Response(status))

    with pytest.raises(PlanError) as caught:
        transport.tombstone("learning_run", "wire-id", request_id="delete-plan-2",
                             expected_revision=0)

    assert caught.value.code == "SYNC_TOMBSTONE_UNSUPPORTED"


def test_ordinary_mutation_404_is_not_treated_as_tombstone_or_success(transport_factory):
    transport = transport_factory(lambda request: httpx.Response(404))

    with pytest.raises(PlanError) as caught:
        transport.write("learning_run", "wire-id", request_id="write-1",
                        expected_revision=0, payload={"value": 1})

    assert caught.value.code == "SYNC_UNAVAILABLE"


def test_tombstone_preserves_compare_and_swap_conflict(transport_factory):
    transport = transport_factory(lambda request: httpx.Response(409))

    with pytest.raises(PlanError) as caught:
        transport.tombstone("learning_run", "wire-id", request_id="delete-plan-3",
                             expected_revision=4)

    assert caught.value.code == "SYNC_CONFLICT"


def test_existing_delete_endpoint_is_unchanged(transport_factory):
    seen = {}

    def handle(request):
        seen["url"] = request.url.path
        return httpx.Response(200, json={
            "kind": "learning_run",
            "entity_id": "wire-id",
            "revision": 3,
            "deleted": True,
            "payload": {},
        })

    transport = transport_factory(handle)
    result = transport.delete("learning_run", "wire-id", request_id="delete-known-1",
                              expected_revision=2)

    assert seen["url"] == "/api/enterprise/sync/v1/entities/learning_run/wire-id/delete"
    assert result["revision"] == 3 and result["deleted"] is True

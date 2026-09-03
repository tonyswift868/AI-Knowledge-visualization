from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.enterprise.contracts_v1 import (
    ActorKind,
    ResourceClassification,
    ResourceScopeType,
    TenantMode,
)
from app.enterprise.identity.config import EnterpriseIdentityConfig, KeycloakOidcConfig
from app.enterprise.identity.context import IdentityContextError
from app.enterprise.identity.models import (
    AuthenticatedPrincipal,
    BusinessRole,
    MembershipGrant,
    MembershipStatus,
)
from app.enterprise.identity.oidc import IdentityError
from app.enterprise.identity.runtime import (
    EnterpriseIdentityRuntime,
    enterprise_route_is_tenant_ready,
    identity_error_response,
    install_enterprise_identity,
)
from app.enterprise.product_bridge import (
    ACCOUNT_CORE_METADATA_SCHEMA_VERSION,
    ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION,
)
from app.enterprise_tenancy.asset_manifests import (
    ASSET_MANIFEST_READ_CONTRACT,
    MISSING_ASSET_STATE_CONTRACT,
    AssetManifestRecord,
)
from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
from app.enterprise_tenancy.models import (
    EnterprisePersistenceRequestError,
    TenantResourceDraft,
    TenantResourceKind,
    TenantResourceRecord,
)
from app.enterprise_tenancy.workspaces import EnterpriseWorkspaceService


ISSUER = "https://identity.example.test/realms/mentorflow"
ORIGIN = "https://mentorflow.example"
ORGANIZATION_ID = "org-shared"
WORKSPACE_ID = "workspace-shared"


def _principal(subject: str, token: str) -> AuthenticatedPrincipal:
    now = datetime.now(timezone.utc)
    return AuthenticatedPrincipal(
        issuer=ISSUER,
        audiences=("mentorflow-api",),
        token_id=f"jti-{token}",
        subject=subject,
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=5),
        authentication_time=now - timedelta(minutes=1),
        authentication_strength="mfa",
        client_id="mentorflow-web",
        actor_kind=ActorKind.HUMAN,
    )


class _BearerClient:
    async def authenticate_access_token(self, token: str) -> AuthenticatedPrincipal:
        subjects = {"token-a": "user-a", "token-b": "user-b"}
        try:
            subject = subjects[token]
        except KeyError as exc:
            raise IdentityError("OIDC_TOKEN_INVALID", "Bearer token is invalid") from exc
        return _principal(subject, token)

    async def aclose(self) -> None:
        return None


class _Directory:
    def resolve_membership(
        self,
        *,
        issuer: str,
        subject: str,
        organization_id: str,
        workspace_id: str | None,
    ) -> MembershipGrant | None:
        if (
            issuer != ISSUER
            or subject not in {"user-a", "user-b"}
            or organization_id != ORGANIZATION_ID
            or workspace_id != WORKSPACE_ID
        ):
            return None
        return MembershipGrant(
            membership_id=f"membership-{subject}",
            issuer=issuer,
            subject=subject,
            organization_id=organization_id,
            workspace_id=workspace_id,
            roles=(BusinessRole.LEARNER,),
            status=MembershipStatus.ACTIVE,
        )

    def resolve_service_identity(self, **_kwargs):
        return None


def _runtime() -> EnterpriseIdentityRuntime:
    config = EnterpriseIdentityConfig(
        mode=TenantMode.ENTERPRISE_SERVER,
        keycloak=KeycloakOidcConfig(
            issuer=ISSUER,
            client_id="mentorflow-web",
            audience="mentorflow-api",
            redirect_uri=f"{ORIGIN}/api/enterprise/auth/callback",
        ),
        browser_allowed_origins=(ORIGIN,),
    )
    return EnterpriseIdentityRuntime(
        config,
        client=_BearerClient(),  # type: ignore[arg-type]
        directory=_Directory(),
    )


def _headers(identity: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer token-{identity}",
        "X-MentorFlow-Organization-ID": ORGANIZATION_ID,
        "X-MentorFlow-Workspace-ID": WORKSPACE_ID,
    }


class _Transactions:
    @contextmanager
    def transaction(self, context):
        yield type(
            "Transaction",
            (),
            {"actor": context.actor, "tenant": context.tenant},
        )()


class _Resources:
    def __init__(self) -> None:
        self.records: dict[
            tuple[str, str, TenantResourceKind, str], TenantResourceRecord
        ] = {}

    @staticmethod
    def _key(transaction, kind: TenantResourceKind, resource_id: str):
        return (
            str(transaction.tenant.organization_id),
            str(transaction.tenant.workspace_id),
            TenantResourceKind(kind),
            resource_id,
        )

    def seed(
        self,
        *,
        subject: str,
        kind: TenantResourceKind,
        resource_id: str,
        title: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        self.records[(ORGANIZATION_ID, WORKSPACE_ID, kind, resource_id)] = (
            TenantResourceRecord(
                resource_id=resource_id,
                kind=kind,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                scope_type=ResourceScopeType.PERSONAL,
                owner_subject_id=subject,
                classification=ResourceClassification.CONFIDENTIAL,
                title=title,
                status=status,
                payload=payload,
                search_text=title,
                job_id="",
                cache_key="",
                created_at=now,
                updated_at=now,
            )
        )

    def get(self, transaction, kind: TenantResourceKind, resource_id: str):
        return self.records.get(self._key(transaction, kind, resource_id))

    def list_records(
        self,
        transaction,
        kind: TenantResourceKind,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> tuple[TenantResourceRecord, ...]:
        prefix = (
            str(transaction.tenant.organization_id),
            str(transaction.tenant.workspace_id),
            TenantResourceKind(kind),
        )
        records = [
            record
            for key, record in self.records.items()
            if key[:3] == prefix and (status is None or record.status == status)
        ]
        return tuple(sorted(records, key=lambda item: item.resource_id)[:limit])

    def upsert(self, transaction, draft: TenantResourceDraft) -> TenantResourceRecord:
        key = self._key(transaction, draft.kind, draft.resource_id)
        current = self.records.get(key)
        now = datetime.now(timezone.utc)
        record = TenantResourceRecord(
            resource_id=draft.resource_id,
            kind=draft.kind,
            organization_id=str(transaction.tenant.organization_id),
            workspace_id=str(transaction.tenant.workspace_id),
            scope_type=draft.scope_type,
            owner_subject_id=draft.owner_subject_id,
            classification=draft.classification,
            title=draft.title,
            status=draft.status,
            payload=dict(draft.payload),
            search_text=draft.search_text,
            job_id=draft.job_id,
            cache_key=draft.cache_key,
            created_at=current.created_at if current is not None else now,
            updated_at=now,
        )
        self.records[key] = record
        return record


class _Assets:
    def __init__(self) -> None:
        self.records: dict[
            tuple[str, str, str, str, str], AssetManifestRecord
        ] = {}

    @staticmethod
    def _key(transaction, asset_id: str):
        return (
            transaction.actor.issuer,
            transaction.actor.subject_id,
            str(transaction.tenant.organization_id),
            str(transaction.tenant.workspace_id),
            asset_id,
        )

    def seed(self, *, subject: str, suffix: str) -> None:
        now = datetime.now(timezone.utc)
        record = AssetManifestRecord(
            asset_id=f"asset-{suffix}",
            asset_revision_id=f"assetrev-{suffix}",
            issuer=ISSUER,
            subject_id=subject,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            local_asset_id=f"local-asset-{suffix}",
            device_id=f"device-{suffix}",
            filename=f"lesson-{suffix}.pdf",
            mime_type="application/pdf",
            size_bytes=123,
            sha256=suffix * 64,
            source_kind="ACCOUNT_VAULT",
            import_mode="MANAGED",
            portability="PORTABLE",
            verified_source_url=None,
            availability="AVAILABLE",
            derived_artifact_manifest={"page_count": 3},
            created_at=now,
            updated_at=now,
        )
        self.records[(ISSUER, subject, ORGANIZATION_ID, WORKSPACE_ID, record.asset_id)] = (
            record
        )

    def list_records(
        self,
        transaction,
        *,
        include_deleted: bool = False,
        limit: int = 200,
    ) -> tuple[AssetManifestRecord, ...]:
        prefix = self._key(transaction, "")[:-1]
        records = [
            record
            for key, record in self.records.items()
            if key[:-1] == prefix
            and (include_deleted or record.availability != "DELETED")
        ]
        return tuple(sorted(records, key=lambda item: item.asset_id)[:limit])

    def set_availability(self, transaction, asset_id: str, availability: str):
        key = self._key(transaction, asset_id)
        record = self.records.get(key)
        if record is None:
            return None
        updated = replace(
            record,
            availability=availability,
            updated_at=datetime.now(timezone.utc),
        )
        self.records[key] = updated
        return updated


class _DocumentService(EnterpriseDocumentImportService):
    def __init__(self) -> None:
        self.transactions = _Transactions()
        self.resources = _Resources()
        self.assets = _Assets()


class _WorkspaceService(EnterpriseWorkspaceService):
    def __init__(self) -> None:
        pass

    def get(self, *, actor, tenant, workspace_id):
        if workspace_id != tenant.workspace_id:
            raise EnterprisePersistenceRequestError(
                "ENTERPRISE_WORKSPACE_NOT_FOUND",
                "Workspace was not found",
                status=404,
            )
        return {
            "id": workspace_id,
            "title": "Shared course",
            "status": "active",
            "sources": [],
            "goal": None,
            "plans": [],
        }


def _app() -> tuple[FastAPI, _DocumentService]:
    runtime = _runtime()
    documents = _DocumentService()
    app = FastAPI()
    app.state.enterprise_identity_runtime = runtime
    app.state.enterprise_workspace_service = _WorkspaceService()
    app.state.enterprise_document_import_service = documents
    install_enterprise_identity(app)

    @app.middleware("http")
    async def enterprise_boundary(request: Request, call_next):
        request.state.request_id = "account-core-request"
        try:
            path = request.url.path
            if path.startswith("/api") and not runtime.is_public_path(path):
                binding = await runtime.authenticate_request(request)
                request.state.enterprise_identity = binding
                request.state.actor_context = (
                    binding.context.actor if binding.context is not None else None
                )
                request.state.tenant_context = (
                    binding.context.tenant if binding.context is not None else None
                )
                if not enterprise_route_is_tenant_ready(path):
                    raise IdentityError(
                        "ENTERPRISE_ROUTE_NOT_TENANT_READY",
                        "This API route is not bound to Enterprise persistence",
                        status=503,
                    )
            return await call_next(request)
        except (IdentityError, IdentityContextError) as exc:
            return identity_error_response(request, exc)

    return app, documents


def _progress(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": "material-a-hash-map",
        "version": 1,
        "plan_id": "plan-a",
        "material_id": "material-a",
        "normalized_name": "hash map",
        "name": "Hash Map",
        "mastery": 0.6,
        "completed_task_count": 2,
        "completed_task_minutes": 35,
        "actual_elapsed_minutes": 38.5,
        "completed_phases": ["explain", "practice"],
        "status": "IN_PROGRESS",
    }
    payload.update(updates)
    return payload


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_authenticated_account_reads_only_its_server_metadata() -> None:
    app, documents = _app()
    documents.resources.seed(
        subject="user-a",
        kind=TenantResourceKind.PLAN,
        resource_id="plan-a",
        title="Plan A",
        status="ACTIVE",
        payload={"version": 7, "private_note": "A-only"},
    )
    documents.resources.seed(
        subject="user-b",
        kind=TenantResourceKind.PLAN,
        resource_id="plan-b",
        title="Plan B",
        status="ACTIVE",
        payload={"version": 2, "private_note": "B-only"},
    )
    documents.assets.seed(subject="user-a", suffix="a")
    documents.assets.seed(subject="user-b", suffix="b")

    with TestClient(app, base_url=ORIGIN) as client:
        stored = client.put(
            "/api/enterprise/account/metadata/progress",
            headers=_headers("a"),
            json=_progress(),
        )
        account_a = client.get(
            "/api/enterprise/account/metadata?organization_id=forged&subject_id=user-b",
            headers=_headers("a"),
        )
        account_b = client.get(
            "/api/enterprise/account/metadata",
            headers=_headers("b"),
        )

    assert stored.status_code == 200
    assert account_a.status_code == 200
    payload_a = account_a.json()
    assert payload_a["schema_version"] == ACCOUNT_CORE_METADATA_SCHEMA_VERSION
    assert payload_a["context_contract"] == "enterprise-context.v1"
    assert payload_a["account"] == {"issuer": ISSUER, "subject_id": "user-a"}
    assert payload_a["tenant"] == {
        "organization_id": ORGANIZATION_ID,
        "workspace_id": WORKSPACE_ID,
    }
    assert [item["plan_id"] for item in payload_a["plans"]] == ["plan-a"]
    assert [item["snapshot_id"] for item in payload_a["progress"]] == [
        "material-a-hash-map"
    ]
    assert [
        item["asset_id"] for item in payload_a["resource_manifest"]["assets"]
    ] == ["asset-a"]

    assert account_b.status_code == 200
    payload_b = account_b.json()
    assert payload_b["account"]["subject_id"] == "user-b"
    assert [item["plan_id"] for item in payload_b["plans"]] == ["plan-b"]
    assert payload_b["progress"] == []
    assert [
        item["asset_id"] for item in payload_b["resource_manifest"]["assets"]
    ] == ["asset-b"]
    b_strings = set(_strings(payload_b))
    assert {"user-a", "plan-a", "asset-a", "A-only"}.isdisjoint(b_strings)


def test_progress_metadata_rejects_identity_override_paths_and_raw_content() -> None:
    app, documents = _app()
    with TestClient(app, base_url=ORIGIN) as client:
        forged = client.put(
            "/api/enterprise/account/metadata/progress",
            headers=_headers("a"),
            json=_progress(organization_id="other-org", subject_id="user-b"),
        )
        windows_path = client.put(
            "/api/enterprise/account/metadata/progress",
            headers=_headers("a"),
            json=_progress(name=r"C:\Users\user\lesson.pdf"),
        )
        raw_content = client.put(
            "/api/enterprise/account/metadata/progress",
            headers=_headers("a"),
            json=_progress(
                raw_bytes="c2VjcmV0",
                content_base64="c2VjcmV0",
                provider_secret="must-not-cross-boundary",
            ),
        )
        multipart = client.put(
            "/api/enterprise/account/metadata/progress",
            headers=_headers("a"),
            files={"file": ("lesson.pdf", b"must-stay-local", "application/pdf")},
        )

    assert forged.status_code == 422
    assert windows_path.status_code == 422
    assert raw_content.status_code == 422
    assert multipart.status_code == 422
    assert all(
        key[2] is not TenantResourceKind.STUDENT_EVENT
        for key in documents.resources.records
    )


def test_manifest_projection_is_pathless_and_missing_is_account_scoped() -> None:
    app, documents = _app()
    documents.assets.seed(subject="user-a", suffix="a")
    documents.assets.seed(subject="user-b", suffix="b")

    with TestClient(app, base_url=ORIGIN) as client:
        manifest = client.get(
            "/api/enterprise/account/metadata/assets",
            headers=_headers("a"),
        )
        missing = client.post(
            "/api/enterprise/account/metadata/assets/asset-a/missing",
            headers=_headers("a"),
        )
        cross_account = client.post(
            "/api/enterprise/account/metadata/assets/asset-a/missing",
            headers=_headers("b"),
        )

    assert manifest.status_code == 200
    payload = manifest.json()
    assert payload["schema_version"] == ASSET_MANIFEST_READ_CONTRACT
    assert [item["asset_id"] for item in payload["assets"]] == ["asset-a"]
    normalized_keys = {key.casefold().replace("-", "_") for key in _keys(payload)}
    assert "local_path" not in normalized_keys
    assert "raw_bytes" not in normalized_keys
    assert "content_base64" not in normalized_keys
    assert not any(
        len(value) >= 3
        and value[0].isalpha()
        and value[1] == ":"
        and value[2] in {"\\", "/"}
        for value in _strings(payload)
    )
    assert missing.status_code == 200
    assert missing.json()["availability"] == "MISSING"
    assert missing.json()["missing_state_contract"] == MISSING_ASSET_STATE_CONTRACT
    assert cross_account.status_code == 404


def test_progress_snapshot_version_is_idempotent_and_fenced() -> None:
    app, _documents = _app()
    with TestClient(app, base_url=ORIGIN) as client:
        first = client.put(
            "/api/enterprise/account/metadata/progress",
            headers=_headers("a"),
            json=_progress(),
        )
        replay = client.put(
            "/api/enterprise/account/metadata/progress",
            headers=_headers("a"),
            json=_progress(),
        )
        conflict = client.put(
            "/api/enterprise/account/metadata/progress",
            headers=_headers("a"),
            json=_progress(mastery=0.7),
        )
        advanced = client.put(
            "/api/enterprise/account/metadata/progress",
            headers=_headers("a"),
            json=_progress(version=2, mastery=0.7),
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "ACCOUNT_PROGRESS_VERSION_CONFLICT"
    assert advanced.status_code == 200
    assert advanced.json()["version"] == 2
    assert advanced.json()["mastery"] == 0.7


def test_account_metadata_response_contains_no_raw_file_bytes() -> None:
    app, documents = _app()
    documents.assets.seed(subject="user-a", suffix="a")
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(
            "/api/enterprise/account/metadata",
            headers=_headers("a"),
        )

    assert response.status_code == 200
    payload = response.json()
    forbidden = {"raw", "raw_bytes", "file_bytes", "content_base64", "local_path"}
    normalized_keys = {key.casefold().replace("-", "_") for key in _keys(payload)}
    assert forbidden.isdisjoint(normalized_keys)
    assert b"must-stay-local" not in response.content
    json.dumps(payload, sort_keys=True)

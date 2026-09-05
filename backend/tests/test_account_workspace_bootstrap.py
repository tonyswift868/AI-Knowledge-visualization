"""Normal-user bootstrap via the actual MVP HTTP boundary (isolated SQLite).

SQLite proves transactions/product authorization, not PostgreSQL RLS. The last
test requires an explicitly provisioned isolated PostgreSQL application DSN.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import os
import uuid

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text

from app import account_core_mvp
from app.enterprise.identity.models import MembershipGrant, MembershipStatus, BusinessRole
from app.enterprise_tenancy.context import require_safe_application_role
from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
from app.enterprise_tenancy.workspaces import EnterpriseWorkspaceService
from app.enterprise_tenancy.workspace_bootstrap import AccountWorkspaceBootstrap, personal_scope
from test_enterprise_account_core_api import _runtime, _principal, ISSUER
from test_enterprise_workspace_persistence import _config, _SQLiteTransactions, _context, _seed_identity

DISCOVER = "/api/enterprise/account/workspaces"
INITIALIZE = DISCOVER + "/initialize"


class Directory:
    def __init__(self, engine):
        self.engine = engine

    def resolve_service_identity(self, **kwargs):
        return None

    def resolve_membership(self, *, issuer, subject, organization_id, workspace_id):
        if issuer != ISSUER:
            return None
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT om.role FROM organization_memberships om
                JOIN organizations o ON o.id = om.organization_id
                JOIN workspace_memberships wm ON wm.organization_id = om.organization_id
                  AND wm.subject_id = om.subject_id
                JOIN workspaces w ON w.id = wm.workspace_id AND w.organization_id = wm.organization_id
                WHERE om.subject_id = :subject AND om.organization_id = :org
                  AND wm.workspace_id = :ws AND om.status = 'active'
                  AND wm.status = 'active' AND o.status = 'active' AND w.status = 'active'
            """), dict(subject=subject, org=organization_id, ws=workspace_id)).first()
        if row is None:
            return None
        return MembershipGrant(membership_id=f"membership-{subject}", issuer=issuer,
                               subject=subject, organization_id=organization_id,
                               workspace_id=workspace_id, roles=(BusinessRole(row[0]),),
                               status=MembershipStatus.ACTIVE)


@pytest.fixture
def system(tmp_path, monkeypatch):
    path = tmp_path / "bootstrap.sqlite3"
    command.upgrade(_config(path), "head")
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"timeout": 15})
    transactions = _SQLiteTransactions(engine)
    service = AccountWorkspaceBootstrap(engine, issuer=ISSUER)
    runtime = _runtime()
    runtime.directory = Directory(engine)
    monkeypatch.setattr(account_core_mvp.app.state, "enterprise_identity_runtime", runtime, raising=False)
    monkeypatch.setattr(account_core_mvp.app.state, "account_workspace_bootstrap", service, raising=False)
    monkeypatch.setattr(account_core_mvp.app.state, "enterprise_workspace_service",
                        EnterpriseWorkspaceService(transaction_manager=transactions), raising=False)
    monkeypatch.setattr(account_core_mvp.app.state, "enterprise_document_import_service",
                        EnterpriseDocumentImportService(transaction_manager=transactions), raising=False)
    # No lifespan: all storage/runtime is explicitly isolated, no configured DB.
    client = TestClient(account_core_mvp.app)
    yield client, service, engine
    client.close()
    engine.dispose()


def headers(who="a", workspace=None):
    result = {"Authorization": f"Bearer token-{who}"}
    if workspace:
        result.update({"X-MentorFlow-Organization-ID": workspace["organization_id"],
                       "X-MentorFlow-Workspace-ID": workspace["id"]})
    return result


def counts(engine):
    with engine.connect() as connection:
        return {name: connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()
                for name in ("organizations", "organization_memberships", "workspaces",
                             "workspace_memberships", "workspace_role_bindings", "workspace_profiles")}


def test_empty_account_normal_http_initialization_and_device_rediscovery(system):
    client, service, engine = system
    before = counts(engine)
    assert all(value == 0 for value in before.values())
    assert client.get(DISCOVER, headers=headers()).json() == dict(
        workspaces=[], default_workspace_id=None, initialization_required=True)
    assert counts(engine) == before  # GET is strictly read-only.
    response = client.post(INITIALIZE, headers=headers(), json={})
    assert response.status_code == 200, response.text
    workspace = response.json()["workspaces"][0]
    assert workspace["title"] == "我的学习空间"
    assert workspace["initialization_status"] == "READY"
    assert all(value == 1 for value in counts(engine).values())
    metadata = client.get("/api/enterprise/account/metadata", headers=headers(workspace=workspace))
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["workspace"]["workspace_id"] == workspace["id"]
    assert client.post(INITIALIZE, headers=headers(), json={}).json() == response.json()
    fresh_client = TestClient(account_core_mvp.app)
    try:
        assert fresh_client.get(DISCOVER, headers=headers()).json() == response.json()
    finally:
        fresh_client.close()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT role FROM organization_memberships")).scalar_one() == "LEARNER"
        assert connection.execute(text("SELECT role FROM workspace_role_bindings")).scalar_one() == "LEARNER"


def test_missing_profile_repair_existing_learner_and_no_overwrite(system):
    client, service, engine = system
    # Model an existing ordinary learner space, not a personal ID created by the new API.
    _seed_identity(engine, organization="legacy-org", workspace="legacy-workspace", subject="user-a")
    workspace = dict(id="legacy-workspace", organization_id="legacy-org")
    with engine.begin() as connection:
        connection.execute(text("UPDATE organization_memberships SET role='LEARNER'"))
        connection.execute(text("UPDATE workspace_role_bindings SET role='LEARNER'"))
        connection.execute(text("DELETE FROM workspace_profiles"))
        connection.execute(text("INSERT INTO workspace_goals(goal_id, organization_id, workspace_id, payload_json, confirmed) "
                                "VALUES ('kept-goal', :org, :ws, '{\"learning_goal\":\"keep me\"}', false)"),
                           {"org": workspace["organization_id"], "ws": workspace["id"]})
    before = counts(engine)
    discovered = client.get(DISCOVER, headers=headers()).json()
    assert discovered["workspaces"][0]["initialization_status"] == "PROFILE_REQUIRED"
    assert counts(engine) == before
    missing = client.get("/api/enterprise/account/metadata", headers=headers(workspace=workspace))
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "ENTERPRISE_WORKSPACE_INITIALIZATION_REQUIRED"
    repaired = client.post(INITIALIZE, headers=headers(), json={})
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["workspaces"][0]["id"] == workspace["id"]
    assert client.get("/api/enterprise/account/metadata", headers=headers(workspace=workspace)).status_code == 200
    with engine.begin() as connection:
        connection.execute(text("UPDATE workspace_profiles SET description='untouched', character_id='custom'"))
        profile_before = dict(connection.execute(text("SELECT * FROM workspace_profiles")).mappings().one())
        goal_before = dict(connection.execute(text("SELECT * FROM workspace_goals")).mappings().one())
    client.post(INITIALIZE, headers=headers(), json={}).raise_for_status()
    with engine.connect() as connection:
        assert dict(connection.execute(text("SELECT * FROM workspace_profiles")).mappings().one()) == profile_before
        assert dict(connection.execute(text("SELECT * FROM workspace_goals")).mappings().one()) == goal_before
    assert counts(engine) == {**before, "workspace_profiles": 1}


def test_initialization_preserves_plan_progress_assets_and_relationships(system):
    client, service, engine = system
    from app.enterprise.contracts_v1 import ResourceClassification, ResourceScopeType
    from app.enterprise_tenancy.context import TenantTransactionContext
    from app.enterprise_tenancy.repository import PostgresTenantResourceRepository
    from app.enterprise_tenancy.models import TenantResourceDraft, TenantResourceKind
    from app.enterprise_tenancy.asset_manifests import AssetManifestRegistrationIn
    workspace = client.post(INITIALIZE, headers=headers(), json={}).json()["workspaces"][0]
    actor, tenant = _context(organization=workspace["organization_id"],
                             workspace=workspace["id"], subject="user-a")
    actor = actor.model_copy(update={"issuer": ISSUER, "roles": ("LEARNER",)})
    transactions = _SQLiteTransactions(engine)
    repository = PostgresTenantResourceRepository()
    with transactions.transaction(TenantTransactionContext(tenant=tenant, actor=actor)) as transaction:
        for kind in (TenantResourceKind.PLAN, TenantResourceKind.STUDENT_EVENT):
            repository.upsert(transaction, TenantResourceDraft(
                resource_id="kept-" + kind.value, kind=kind,
                scope_type=ResourceScopeType.PERSONAL, owner_subject_id=actor.subject_id,
                classification=ResourceClassification.CONFIDENTIAL,
                title="Preserved", status="ready", payload={"unchanged": [1, 2, 3]}))
        account_core_mvp.app.state.enterprise_document_import_service.assets.upsert(
            transaction, AssetManifestRegistrationIn(
                local_asset_id="kept-asset", filename="notes.txt", mime_type="text/plain",
                size_bytes=10, sha256="a" * 64, import_mode="MANAGED", portability="PORTABLE"))
    def snapshot():
        with engine.connect() as connection:
            return {table: [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]
                    for table in ("organizations", "workspaces", "organization_memberships",
                                  "workspace_memberships", "workspace_role_bindings", "workspace_profiles",
                                  "learning_plans", "student_events", "resource_scopes",
                                  "asset_manifests", "asset_revisions")}
    before = snapshot()
    assert before["learning_plans"] and before["student_events"] and before["asset_manifests"]
    client.post(INITIALIZE, headers=headers(), json={}).raise_for_status()
    assert snapshot() == before


def test_bilateral_membership_isolation_and_payload_authority_denial(system):
    client, service, engine = system
    a = client.post(INITIALIZE, headers=headers("a"), json={}).json()["workspaces"][0]
    b = client.post(INITIALIZE, headers=headers("b"), json={}).json()["workspaces"][0]
    assert a["id"] != b["id"]
    for who, own, other in (("a", a, b), ("b", b, a)):
        assert client.get(DISCOVER, headers=headers(who)).json()["workspaces"] == [own]
        assert client.get("/api/enterprise/account/metadata", headers=headers(who, other)).status_code == 403
        assert client.post("/api/enterprise/workspaces", headers=headers(who, own), json={"title": "new"}).status_code == 403
        assert client.post("/api/enterprise/workspaces", headers=headers(who, other), json={"title": "new"}).status_code == 403
        for payload in ({"organization_id": other["organization_id"]}, {"role": "OWNER"},
                        {"owner": "user-b"}, {"workspace_id": other["id"]}, {"tenant": other}):
            assert client.post(INITIALIZE, headers=headers(who), json=payload).status_code == 422
        assert client.get(DISCOVER, headers=headers(who, other)).status_code == 422
    assert client.get(DISCOVER).status_code == 401
    assert client.post(INITIALIZE, json={}).status_code == 401
    with engine.connect() as connection:
        assert set(connection.execute(text("SELECT role FROM organization_memberships")).scalars()) == {"LEARNER"}


def test_double_request_returns_one_default_workspace(system):
    client, service, engine = system
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(INITIALIZE, headers=headers(), json={}), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json() == results[1].json()
    assert all(value == 1 for value in counts(engine).values())


def test_failure_rolls_back_all_first_use_records(system, monkeypatch):
    client, service, engine = system
    from app.enterprise_tenancy import workspace_bootstrap
    def fail(*args):
        raise RuntimeError("injected profile failure")
    monkeypatch.setattr(workspace_bootstrap, "ensure_workspace_profile", fail)
    with pytest.raises(RuntimeError, match="injected profile failure"):
        client.post(INITIALIZE, headers=headers(), json={})
    assert all(value == 0 for value in counts(engine).values())


def test_revoked_membership_is_not_resurrected(system):
    client, service, engine = system
    client.post(INITIALIZE, headers=headers(), json={}).raise_for_status()
    with engine.begin() as connection:
        connection.execute(text("UPDATE organization_memberships SET status='revoked'"))
    before = counts(engine)
    assert client.get(DISCOVER, headers=headers()).json()["workspaces"] == []
    assert client.post(INITIALIZE, headers=headers(), json={}).status_code == 403
    assert counts(engine) == before
    with engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM organization_memberships")).scalar_one() == "revoked"


def test_issuer_binding_and_service_identity_fail_closed(system):
    _, service, _ = system
    from app.enterprise.contracts_v1 import ActorKind
    from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
    principal = _principal("user-a", "test")
    for bad in (principal.model_copy(update={"issuer": "https://other-issuer.test"}),
                principal.model_copy(update={"actor_kind": ActorKind.SERVICE})):
        with pytest.raises(EnterprisePersistenceRequestError):
            service.initialize(bad)
    assert personal_scope(ISSUER, "user-a") != personal_scope(ISSUER + "/other", "user-a")


def test_forward_backfill_is_idempotent_preserves_existing_and_history(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    config = _config(path)
    command.upgrade(config, "0034_enterprise_asset_manifests")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO organizations(id,slug,name,status) VALUES ('legacy','legacy','Original','active')"))
        for name in ("missing", "complete"):
            connection.execute(text("INSERT INTO workspaces(id,organization_id,slug,name,status) VALUES (:id,'legacy',:id,:id,'active')"), {"id": name})
        connection.execute(text("INSERT INTO workspace_profiles(workspace_id,organization_id,description) VALUES ('complete','legacy','keep')"))
    command.upgrade(config, "head")
    spec = importlib.util.spec_from_file_location("bootstrap_migration", Path(__file__).parents[1] / "alembic/versions/0035_account_workspace_bootstrap.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        baseline = [dict(r) for r in connection.execute(text("SELECT * FROM workspace_profiles ORDER BY workspace_id")).mappings()]
        migration.backfill(connection)
        assert [dict(r) for r in connection.execute(text("SELECT * FROM workspace_profiles ORDER BY workspace_id")).mappings()] == baseline
        assert baseline[0]["description"] == "keep"
        assert baseline[1]["character_id"] == "warm"
        assert connection.execute(text("SELECT name FROM organizations")).scalar_one() == "Original"
    command.downgrade(config, "0034_enterprise_asset_manifests")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM workspace_profiles")).scalar_one() == 2
    engine.dispose()


def test_supported_synthetic_creation_includes_profiles_without_overwriting(system):
    _, _, engine = system
    from app.enterprise_tenancy.synthetic import seed_synthetic_tenants
    with engine.begin() as connection:
        seed = seed_synthetic_tenants(connection)
        assert connection.execute(text("SELECT count(*) FROM workspace_profiles")).scalar_one() == seed.workspace_count
        connection.execute(text("UPDATE workspace_profiles SET description='keep original'"))
        before = [dict(row) for row in connection.execute(text(
            "SELECT * FROM workspace_profiles ORDER BY workspace_id")).mappings()]
        seed_synthetic_tenants(connection)
        assert [dict(row) for row in connection.execute(text(
            "SELECT * FROM workspace_profiles ORDER BY workspace_id")).mappings()] == before


def test_real_isolated_postgres_first_use_rls_if_provisioned():
    dsn = os.environ.get("E1_WORKSPACE_BOOTSTRAP_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("NOT_RUN: no explicitly provisioned isolated PostgreSQL bootstrap database")
    engine = create_engine(dsn)
    with engine.connect() as connection:
        require_safe_application_role(connection)
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0035_account_workspace_bootstrap"
    service = AccountWorkspaceBootstrap(engine, issuer=ISSUER)
    a = _principal("bootstrap-test-" + uuid.uuid4().hex, "test-a")
    b = _principal("bootstrap-test-" + uuid.uuid4().hex, "test-b")
    assert service.discover(a)["workspaces"] == []
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.initialize(a), range(2)))
    assert results[0] == results[1]
    b_result = service.initialize(b)
    assert service.discover(a) == results[0]
    assert service.discover(b) == b_result
    assert results[0]["default_workspace_id"] != b_result["default_workspace_id"]
    # Direct SQL deliberately omits the service's WHERE filter: native subject-only
    # RLS itself must hide B's workspace and memberships from A's discovery context.
    with engine.begin() as connection:
        service._context(connection, a)
        assert list(connection.execute(text("SELECT id FROM workspaces")).scalars()) == [results[0]["default_workspace_id"]]
        assert set(connection.execute(text("SELECT subject_id FROM workspace_memberships")).scalars()) == {a.subject}
    engine.dispose()

"""Account first-use operations over the existing Workspace/Membership tables.

The issuer is deployment authority, never a request field. PostgreSQL still
enforces RLS; only the discovery SELECT policies permit a subject-only context.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib

from sqlalchemy import text

from app.enterprise.contracts_v1 import ActorKind
from app.enterprise.identity.models import AuthenticatedPrincipal
from .models import EnterprisePersistenceRequestError
from .rehydration_manifest import initialize_learning_state, learning_state_initialized


DEFAULT_WORKSPACE_TITLE = "我的学习空间"


def ensure_workspace_profile(connection, organization_id: str, workspace_id: str) -> None:
    """Insert only 0033's official defaults; never overwrite an existing profile."""
    connection.execute(text("""
        INSERT INTO workspace_profiles(workspace_id, organization_id)
        VALUES (:workspace, :organization)
        ON CONFLICT (workspace_id) DO NOTHING
    """), {"workspace": workspace_id, "organization": organization_id})


def personal_scope(issuer: str, subject: str) -> tuple[str, str]:
    # Length-prefixed identity, unambiguous even for subjects containing separators.
    digest = hashlib.sha256(f"{len(issuer)}:{issuer}{subject}".encode()).hexdigest()
    return f"personal_org_{digest}", f"personal_ws_{digest}"


class AccountWorkspaceBootstrap:
    def __init__(self, engine, *, issuer: str) -> None:
        self.engine = engine
        self.issuer = issuer.rstrip("/")

    def _validate(self, principal: AuthenticatedPrincipal) -> None:
        if (principal.issuer != self.issuer or principal.actor_kind is not ActorKind.HUMAN
                or not principal.subject or len(principal.subject) > 128):
            raise EnterprisePersistenceRequestError(
                "ACCOUNT_BOOTSTRAP_IDENTITY_INVALID", "A verified human account is required", status=403)

    def _context(self, connection, principal, organization="", workspace="") -> None:
        if connection.dialect.name == "postgresql":
            connection.execute(text("""
                SELECT set_config('mentorflow.actor_issuer', :issuer, true),
                       set_config('mentorflow.actor_subject', :subject, true),
                       set_config('mentorflow.organization_id', :organization, true),
                       set_config('mentorflow.workspace_id', :workspace, true)
            """), dict(issuer=principal.issuer, subject=principal.subject,
                       organization=organization, workspace=workspace))

    @contextmanager
    def _transaction(self, principal, *, write=False):
        self._validate(principal)
        with self.engine.connect() as connection:
            # SQLite is used only by isolated tests, never by the MVP runtime.
            if write and connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            else:
                with connection.begin():
                    if not write and connection.dialect.name == "postgresql":
                        connection.execute(text("SET TRANSACTION READ ONLY"))
                    self._context(connection, principal)
                    if write:
                        # Serialize simultaneous first-use/repair by verified identity.
                        lock_key = int(hashlib.sha256(
                            (principal.issuer + "\0" + principal.subject).encode()
                        ).hexdigest()[:16], 16)
                        if lock_key >= 2**63:
                            lock_key -= 2**64
                        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
                    yield connection

    def _memberships(self, connection, principal):
        self._context(connection, principal)
        statement = """
            SELECT w.id, w.organization_id, w.name, w.status, w.created_at
            FROM workspace_memberships wm
            JOIN organization_memberships om
              ON om.organization_id = wm.organization_id AND om.subject_id = wm.subject_id
            JOIN organizations o ON o.id = om.organization_id
            JOIN workspaces w ON w.id = wm.workspace_id AND w.organization_id = wm.organization_id
            WHERE wm.subject_id = :subject AND wm.status = 'active'
              AND om.status = 'active' AND o.status = 'active' AND w.status = 'active'
              AND upper(om.role) IN ('OWNER', 'ORGANIZATION_OWNER', 'ADMIN',
                                    'CONTENT_MANAGER', 'INSTRUCTOR', 'LEARNER', 'AUDITOR')
            ORDER BY w.created_at, w.id
        """
        return connection.execute(text(statement), {"subject": principal.subject}).mappings().all()

    def _result(self, connection, principal, rows):
        workspaces = []
        learning_required = False
        for row in rows:
            self._context(connection, principal, row["organization_id"], row["id"])
            initialized = connection.execute(text("""
                SELECT workspace_id FROM workspace_profiles
                WHERE workspace_id = :workspace AND organization_id = :organization
            """), {"workspace": row["id"], "organization": row["organization_id"]}).scalar_one_or_none()
            if connection.dialect.name == 'postgresql':
                learning_required |= not learning_state_initialized(connection, dict(
                    issuer=principal.issuer, subject_id=principal.subject,
                    organization_id=row['organization_id'], workspace_id=row['id']))
            workspaces.append(dict(id=row["id"], organization_id=row["organization_id"],
                                   title=row["name"], status=row["status"],
                                   initialization_status="READY" if initialized else "PROFILE_REQUIRED"))
        return dict(workspaces=workspaces,
                    default_workspace_id=workspaces[0]["id"] if workspaces else None,
                    initialization_required=learning_required or not workspaces or any(
                        item["initialization_status"] != "READY" for item in workspaces))

    def discover(self, principal: AuthenticatedPrincipal):
        with self._transaction(principal) as connection:
            return self._result(connection, principal, self._memberships(connection, principal))

    def initialize(self, principal: AuthenticatedPrincipal):
        with self._transaction(principal, write=True) as connection:
            rows = self._memberships(connection, principal)
            if not rows:
                organization, workspace = personal_scope(principal.issuer, principal.subject)
                self._context(connection, principal, organization, workspace)
                params = dict(organization=organization, workspace=workspace,
                              subject=principal.subject, title=DEFAULT_WORKSPACE_TITLE)
                # Never resurrect revoked/suspended membership or join an existing org.
                if connection.execute(text("SELECT id FROM organizations WHERE id = :organization"), params).first():
                    raise EnterprisePersistenceRequestError(
                        "ACCOUNT_PERSONAL_WORKSPACE_UNAVAILABLE",
                        "Existing personal membership is unavailable; access cannot be restored by initialization",
                        status=403)
                connection.execute(text("""
                    INSERT INTO organizations(id, slug, name, status)
                    VALUES (:organization, :organization, :title, 'active')
                """), params)
                connection.execute(text("""
                    INSERT INTO organization_memberships(organization_id, subject_id, role, status)
                    VALUES (:organization, :subject, 'LEARNER', 'active')
                """), params)
                connection.execute(text("""
                    INSERT INTO workspaces(id, organization_id, slug, name, status)
                    VALUES (:workspace, :organization, :workspace, :title, 'active')
                """), params)
                connection.execute(text("""
                    INSERT INTO workspace_memberships(organization_id, workspace_id, subject_id, status)
                    VALUES (:organization, :workspace, :subject, 'active')
                """), params)
                connection.execute(text("""
                    INSERT INTO workspace_role_bindings(organization_id, workspace_id, subject_id, role, status)
                    VALUES (:organization, :workspace, :subject, 'LEARNER', 'active')
                """), params)
                rows = self._memberships(connection, principal)
                if not rows:
                    raise RuntimeError("Initialized workspace is not membership-visible")
            for row in rows:
                self._context(connection, principal, row["organization_id"], row["id"])
                ensure_workspace_profile(connection, row["organization_id"], row["id"])
                if connection.dialect.name == 'postgresql':
                    connection.execute(text('SET LOCAL synchronous_commit = on'))
                    initialize_learning_state(connection, dict(issuer=principal.issuer,
                        subject_id=principal.subject, organization_id=row['organization_id'], workspace_id=row['id']))
            return self._result(connection, principal, rows)

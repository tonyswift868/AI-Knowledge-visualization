"""Account workspace discovery, personal first use, and missing-profile backfill.

Revision ID: 0035_account_workspace_bootstrap
Revises: 0034_enterprise_asset_manifests
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_account_workspace_bootstrap"
down_revision = "0034_enterprise_asset_manifests"
branch_labels = None
depends_on = None

SUBJECT = "NULLIF(current_setting('mentorflow.actor_subject', true), '')"
ISSUER = "NULLIF(current_setting('mentorflow.actor_issuer', true), '')"
DISCOVERY = ("NULLIF(current_setting('mentorflow.organization_id', true), '') IS NULL "
             f"AND {SUBJECT} IS NOT NULL AND {ISSUER} IS NOT NULL")
IDENTITY_HASH = (f"encode(sha256(convert_to(length({ISSUER})::text || ':' || "
                 f"{ISSUER} || {SUBJECT}, 'UTF8')), 'hex')")
PERSONAL_ORG = f"('personal_org_' || {IDENTITY_HASH})"
PERSONAL_WS = f"('personal_ws_' || {IDENTITY_HASH})"


def policies():
    own_membership = f"subject_id = {SUBJECT} AND status = 'active'"
    organization_member = (
        "EXISTS (SELECT 1 FROM public.organization_memberships om "
        f"WHERE om.organization_id = organizations.id AND om.subject_id = {SUBJECT} "
        "AND om.status = 'active')")
    workspace_member = (
        "EXISTS (SELECT 1 FROM public.workspace_memberships wm "
        "JOIN public.organization_memberships om ON om.organization_id = wm.organization_id "
        "AND om.subject_id = wm.subject_id "
        "WHERE wm.organization_id = workspaces.organization_id AND wm.workspace_id = workspaces.id "
        f"AND wm.subject_id = {SUBJECT} AND wm.status = 'active' AND om.status = 'active')")
    personal = (f"organization_id = {PERSONAL_ORG} "
                "AND organization_id = NULLIF(current_setting('mentorflow.organization_id', true), '') "
                f"AND {SUBJECT} IS NOT NULL AND {ISSUER} IS NOT NULL")
    return (
        ("organization_memberships", "account_discover_org_membership", "SELECT",
         f"{DISCOVERY} AND {own_membership}"),
        ("workspace_memberships", "account_discover_workspace_membership", "SELECT",
         f"{DISCOVERY} AND {own_membership}"),
        ("organizations", "account_discover_organization", "SELECT",
         f"{DISCOVERY} AND status = 'active' AND {organization_member}"),
        ("workspaces", "account_discover_workspace", "SELECT",
         f"{DISCOVERY} AND status = 'active' AND {workspace_member}"),
        ("workspaces", "account_create_personal_workspace", "INSERT",
         f"{personal} AND id = {PERSONAL_WS} AND status = 'active' AND EXISTS ("
         "SELECT 1 FROM public.organization_memberships om "
         "WHERE om.organization_id = workspaces.organization_id "
         f"AND om.subject_id = {SUBJECT} AND om.status = 'active' AND om.role = 'LEARNER')"),
        ("workspace_memberships", "account_create_personal_membership", "INSERT",
         f"{personal} AND workspace_id = {PERSONAL_WS} AND {own_membership} AND EXISTS ("
         "SELECT 1 FROM public.organization_memberships om "
         "WHERE om.organization_id = workspace_memberships.organization_id "
         f"AND om.subject_id = {SUBJECT} AND om.status = 'active' AND om.role = 'LEARNER')"),
    )


def backfill(bind):
    # 0033 server defaults are authoritative; names/learning data are untouched.
    bind.execute(sa.text("""
        INSERT INTO workspace_profiles(workspace_id, organization_id)
        SELECT w.id, w.organization_id FROM workspaces w
        WHERE NOT EXISTS (SELECT 1 FROM workspace_profiles p WHERE p.workspace_id = w.id)
        ON CONFLICT (workspace_id) DO NOTHING
    """))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        if bind.execute(sa.text("SELECT current_user")).scalar_one() != "mentorflow_migrator":
            raise RuntimeError("Account bootstrap migration requires mentorflow_migrator")
        # Transaction-local maintenance policies, solely for the existing migration
        # owner. FORCE RLS remains enabled; none is granted to mentorflow_app.
        for table in ("workspaces", "workspace_profiles"):
            op.execute(sa.text(f"CREATE POLICY account_profile_backfill_read ON {table} "
                               "FOR SELECT TO mentorflow_migrator USING (true)"))
        op.execute(sa.text("CREATE POLICY account_profile_backfill_insert ON workspace_profiles "
                           "FOR INSERT TO mentorflow_migrator WITH CHECK (true)"))
        backfill(bind)
        op.execute(sa.text("DROP POLICY account_profile_backfill_insert ON workspace_profiles"))
        for table in ("workspaces", "workspace_profiles"):
            op.execute(sa.text(f"DROP POLICY account_profile_backfill_read ON {table}"))
        for table, name, operation, predicate in policies():
            clause = "USING" if operation == "SELECT" else "WITH CHECK"
            op.execute(sa.text(f"CREATE POLICY {name} ON {table} FOR {operation} "
                               f"TO mentorflow_app {clause} ({predicate})"))
    else:
        backfill(bind)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        for table, name, _operation, _predicate in reversed(policies()):
            op.execute(sa.text(f"DROP POLICY IF EXISTS {name} ON {table}"))
    # Profiles created by initialization/backfill are user state. Never delete them.

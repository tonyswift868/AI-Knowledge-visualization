"""Allow the sync app to enter the existing account-closure fence."""

from alembic import op


revision = "0051_sync_closure_fence"
down_revision = "0050_account_daily_activity"
branch_labels = None
depends_on = None

FUNCTION = "account_closure.assert_open(text,text)"


def _as_closure_role() -> None:
    # The function is owned by the restricted closure role; the migrator only
    # has the existing SET ROLE capability established by migration 0041.
    op.execute("SET LOCAL ROLE mentorflow_closure")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _as_closure_role()
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO mentorflow_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _as_closure_role()
    op.execute(f"REVOKE EXECUTE ON FUNCTION {FUNCTION} FROM mentorflow_app")
    op.execute("RESET ROLE")

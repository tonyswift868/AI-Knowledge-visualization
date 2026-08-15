"""Add local desktop users, sessions and workspace ownership.

The migration is append-only. Existing workspace and learner state stays in
place; the first registered local user may explicitly claim those workspace
identifiers through the authentication service.
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_local_user_authentication"
down_revision = "0017_canonical_plan_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "orm_local_users" not in tables:
        op.create_table(
            "orm_local_users",
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("username_normalized", sa.String(length=64), nullable=False),
            sa.Column("username_display", sa.String(length=64), nullable=False),
            sa.Column("display_name", sa.String(length=120), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("username_normalized", name="uq_orm_local_user_username"),
        )
        op.create_index("ix_orm_local_users_status", "orm_local_users", ["status"])
    if "orm_local_sessions" not in tables:
        op.create_table(
            "orm_local_sessions",
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("user_id", sa.String(length=96), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("client_kind", sa.String(length=32), nullable=False, server_default="desktop"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["orm_local_users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("token_hash", name="uq_orm_local_session_token_hash"),
        )
        op.create_index("ix_orm_local_sessions_user_id", "orm_local_sessions", ["user_id"])
        op.create_index("ix_orm_local_sessions_expires_at", "orm_local_sessions", ["expires_at"])
    if "orm_workspace_ownership" not in tables:
        op.create_table(
            "orm_workspace_ownership",
            sa.Column("workspace_id", sa.String(length=96), primary_key=True),
            sa.Column("user_id", sa.String(length=96), nullable=False),
            sa.Column("ownership_kind", sa.String(length=24), nullable=False, server_default="owner"),
            sa.Column("claim_source", sa.String(length=40), nullable=False),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["user_id"], ["orm_local_users.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_orm_workspace_ownership_user_id", "orm_workspace_ownership", ["user_id"])


def downgrade() -> None:
    # Account and ownership data must never be silently destroyed.
    pass

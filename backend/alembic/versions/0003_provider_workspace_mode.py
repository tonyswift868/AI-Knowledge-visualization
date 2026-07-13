"""add workspace execution mode and provider identity"""

from alembic import op
import sqlalchemy as sa

revision = "0003_provider_workspace_mode"
down_revision = "0002_conflict_record_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("orm_workspaces")}
    if "execution_mode" not in columns:
        op.add_column("orm_workspaces", sa.Column("execution_mode", sa.String(16), server_default="demo", nullable=False))
    if "provider_name" not in columns:
        op.add_column("orm_workspaces", sa.Column("provider_name", sa.String(64), server_default="fake", nullable=False))
    if "provider_model" not in columns:
        op.add_column("orm_workspaces", sa.Column("provider_model", sa.String(128), server_default="", nullable=False))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("orm_workspaces")}
    for name in ("provider_model", "provider_name", "execution_mode"):
        if name in columns:
            op.drop_column("orm_workspaces", name)

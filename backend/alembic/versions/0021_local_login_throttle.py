"""Add durable failed-login throttling for local accounts."""

from alembic import op
import sqlalchemy as sa


revision = "0021_local_login_throttle"
down_revision = "0020_local_phone_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("orm_local_users")}
    if "failed_login_count" not in columns:
        op.add_column(
            "orm_local_users",
            sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "locked_until" not in columns:
        op.add_column("orm_local_users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Account security history must not be silently removed by a downgrade.
    pass

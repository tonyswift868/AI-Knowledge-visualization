"""Add verified phone binding and SMS password-reset challenges."""

from alembic import op
import sqlalchemy as sa


revision = "0020_local_phone_recovery"
down_revision = "0018_local_user_authentication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("orm_local_users")}
    if "phone_e164" not in user_columns:
        op.add_column("orm_local_users", sa.Column("phone_e164", sa.String(length=20), nullable=True))
    if "phone_verified_at" not in user_columns:
        op.add_column("orm_local_users", sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True))
    tables = set(inspector.get_table_names())
    if "orm_local_phone_challenges" not in tables:
        op.create_table(
            "orm_local_phone_challenges",
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("user_id", sa.String(length=96), nullable=False),
            sa.Column("phone_e164", sa.String(length=20), nullable=False),
            sa.Column("purpose", sa.String(length=16), nullable=False),
            sa.Column("code_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["orm_local_users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("code_hash", name="uq_orm_local_phone_challenge_code_hash"),
        )
        op.create_index("ix_orm_local_phone_challenges_user_id", "orm_local_phone_challenges", ["user_id"])
        op.create_index("ix_orm_local_phone_challenges_expires_at", "orm_local_phone_challenges", ["expires_at"])
    # One verified phone can recover only one local account.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_orm_local_user_phone_e164 "
        "ON orm_local_users(phone_e164) WHERE phone_e164 IS NOT NULL"
    )


def downgrade() -> None:
    # Phone identity and challenge history must not be silently destroyed.
    pass

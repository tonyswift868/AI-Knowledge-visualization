"""add Learning Run request idempotency storage"""

from alembic import op
import sqlalchemy as sa


revision = "0007_learning_run_idempotency"
down_revision = "0006_learning_runs_and_dialogue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("orm_learning_runs")}
    if "last_idempotency_key" not in columns:
        op.add_column("orm_learning_runs", sa.Column("last_idempotency_key", sa.String(160), nullable=False, server_default=""))
    if "last_response" not in columns:
        op.add_column("orm_learning_runs", sa.Column("last_response", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    message_columns = {column["name"] for column in sa.inspect(bind).get_columns("orm_learning_run_messages")}
    if "idempotency_key" not in message_columns:
        op.add_column("orm_learning_run_messages", sa.Column("idempotency_key", sa.String(160), nullable=False, server_default=""))
    constraints = {item.get("name") for item in sa.inspect(bind).get_unique_constraints("orm_learning_run_messages")}
    if "uq_orm_learning_run_message_idempotency" not in constraints:
        bind.execute(sa.text("UPDATE orm_learning_run_messages SET idempotency_key = 'legacy:' || id WHERE idempotency_key = ''"))
        with op.batch_alter_table("orm_learning_run_messages") as batch:
            batch.create_unique_constraint("uq_orm_learning_run_message_idempotency", ["run_id", "idempotency_key"])


def downgrade() -> None:
    with op.batch_alter_table("orm_learning_run_messages") as batch:
        batch.drop_constraint("uq_orm_learning_run_message_idempotency", type_="unique")
    op.drop_column("orm_learning_run_messages", "idempotency_key")
    op.drop_column("orm_learning_runs", "last_response")
    op.drop_column("orm_learning_runs", "last_idempotency_key")

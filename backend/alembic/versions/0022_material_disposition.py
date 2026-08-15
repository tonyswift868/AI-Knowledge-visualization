"""Add reversible disposition metadata to legacy active materials.

The active-teaching runtime table is historically bootstrapped outside the
Alembic metadata registry.  This migration is therefore deliberately
conditional: existing databases receive additive columns, while fresh
databases receive them from the startup bridge before the first request.
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_material_disposition"
down_revision = "0021_local_login_throttle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "active_materials" not in inspector.get_table_names():
        return
    present = {item["name"] for item in inspector.get_columns("active_materials")}
    additions = (
        ("archived", sa.Column("archived", sa.Integer(), nullable=False, server_default="0")),
        ("archived_at", sa.Column("archived_at", sa.Text(), nullable=True)),
        ("archive_reason", sa.Column("archive_reason", sa.Text(), nullable=False, server_default="")),
        ("material_revision", sa.Column("material_revision", sa.Integer(), nullable=False, server_default="1")),
    )
    for name, column in additions:
        if name not in present:
            op.add_column("active_materials", column)


def downgrade() -> None:
    # Disposition is audit data; a downgrade must not erase archive history.
    pass

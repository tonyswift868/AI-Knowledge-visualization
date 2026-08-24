"""Add the durable anchor/support scope contract to map revisions."""

from alembic import op
import sqlalchemy as sa


revision = "0027_knowledge_map_scope_anchor"
down_revision = "0026_planning_resource_pool"
branch_labels = None
depends_on = None


def _ensure_columns(bind, table_name: str, columns: list[sa.Column]) -> None:
    inspector = sa.inspect(bind)
    existing = {str(item["name"]) for item in inspector.get_columns(table_name)}
    for column in columns:
        if column.name in existing:
            continue
        op.add_column(table_name, column)
        existing.add(column.name)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "orm_knowledge_map_revisions" not in set(inspector.get_table_names()):
        # The knowledge-map tables are owned by migration 0013.  Keep this
        # revision idempotent on a partially initialized database instead of
        # creating a parallel table with a different contract.
        return
    _ensure_columns(
        bind,
        "orm_knowledge_map_revisions",
        [
            sa.Column("anchor_material_id", sa.String(160), nullable=True),
            sa.Column("supporting_material_ids", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("scope_policy", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("role_provenance", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("source_fingerprints", sa.JSON(), nullable=False, server_default="{}"),
        ],
    )
    indexes = {str(item.get("name")) for item in sa.inspect(bind).get_indexes("orm_knowledge_map_revisions")}
    if "ix_orm_knowledge_map_revisions_anchor_material_id" not in indexes:
        op.create_index(
            "ix_orm_knowledge_map_revisions_anchor_material_id",
            "orm_knowledge_map_revisions",
            ["anchor_material_id"],
        )


def downgrade() -> None:
    # Scope columns are additive audit state.  Keep them on downgrade so a
    # rollback cannot silently discard the selected anchor/support contract.
    pass

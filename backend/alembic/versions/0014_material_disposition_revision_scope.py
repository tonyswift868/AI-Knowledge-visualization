"""Scope material dispositions to an immutable map revision.

Revision ID: 0014_material_disposition_revision_scope
Revises: 0013_knowledge_map_teacher_v1
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_material_disposition_revision_scope"
down_revision = "0013_knowledge_map_teacher_v1"
branch_labels = None
depends_on = None


OLD_CONSTRAINT = "uq_orm_material_disposition_material"
NEW_CONSTRAINT = "uq_orm_material_disposition_revision"


def upgrade() -> None:
    """Preserve every row while allowing a disposition per map revision."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "orm_material_dispositions" not in tables:
        return
    # Some recovery-era databases were stamped at 0012 after request-time
    # bootstrap created only the revision table.  0013 intentionally accepts
    # that incomplete legacy shape.  Avoid reflecting unresolved foreign keys
    # here; a later repair/import can rebuild those legacy-only tables without
    # risking the preserved revision row.
    if not {
        "orm_source_revisions",
        "orm_knowledge_map_revisions",
    } <= tables:
        return
    constraints = {
        item.get("name")
        for item in inspector.get_unique_constraints("orm_material_dispositions")
    }
    if NEW_CONSTRAINT in constraints:
        return
    with op.batch_alter_table("orm_material_dispositions", recreate="always") as batch:
        if OLD_CONSTRAINT in constraints:
            batch.drop_constraint(OLD_CONSTRAINT, type_="unique")
        batch.create_unique_constraint(
            NEW_CONSTRAINT,
            ["workspace_id", "map_revision_id", "material_key"],
        )


def downgrade() -> None:
    # The new key permits multiple audited revisions for one source. Restoring
    # the old key could discard history, so downgrade is deliberately non-
    # destructive, matching the other knowledge-map audit migrations.
    pass

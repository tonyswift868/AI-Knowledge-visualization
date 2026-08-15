"""Mark pre-v1 fallback maps as legacy and read-only.

Revision ID: 0015_mark_unverified_legacy_maps
Revises: 0014_material_disposition_revision_scope
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_mark_unverified_legacy_maps"
down_revision = "0014_material_disposition_revision_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "orm_knowledge_map_revisions" not in tables:
        return
    bind.execute(
        sa.text(
            "UPDATE orm_knowledge_map_revisions "
            "SET status='legacy_unverified', is_current=0 "
            "WHERE provider_used=0 "
            "OR generation_mode IN "
            "('deterministic_fallback','structural_fallback','legacy_unverified')"
        )
    )


def downgrade() -> None:
    # Restoring an unverified fallback as active would make it eligible for
    # planning again, so this audit-only classification is intentionally kept.
    pass

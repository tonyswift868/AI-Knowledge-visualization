"""Move generic-map support tables out of request-time schema creation.

The tables may already exist in long-lived MentorFlow databases.  This
migration is therefore append-only and creates only missing tables.
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_migrate_generic_knowledge_support_tables"
down_revision = "0015_mark_unverified_legacy_maps"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "active_extraction_runs" not in tables:
        op.create_table(
            "active_extraction_runs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", sa.Text(), nullable=False),
            sa.Column("mode", sa.Text(), nullable=False),
            sa.Column("provider_meta", sa.Text(), nullable=False),
            sa.Column("warnings", sa.Text(), nullable=False),
            sa.Column("input_chunk_count", sa.Integer(), nullable=False),
            sa.Column("output_point_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
    if "active_point_details" not in tables:
        op.create_table(
            "active_point_details",
            sa.Column("knowledge_point_id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", sa.Text(), nullable=False),
            sa.Column("mastery_estimate", sa.Text(), nullable=False),
            sa.Column("extraction_mode", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
    if "active_plan_calendars" not in tables:
        op.create_table(
            "active_plan_calendars",
            sa.Column("plan_id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", sa.Text(), nullable=False),
            sa.Column("calendar", sa.Text(), nullable=False),
            sa.Column("available_capacity_minutes", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
    if "active_external_resources" not in tables:
        op.create_table(
            "active_external_resources",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", sa.Text(), nullable=False),
            sa.Column("knowledge_point_id", sa.Text(), nullable=False),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("host", sa.Text(), nullable=False),
            sa.Column("provider_label", sa.Text(), nullable=False),
            sa.Column("authority", sa.Text(), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("search_query", sa.Text(), nullable=False),
            sa.Column("discovery_meta", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["knowledge_point_id"],
                ["active_knowledge_points.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "knowledge_point_id",
                "url",
                name="uq_active_external_resource_point_url",
            ),
        )
    if "active_resource_discovery_runs" not in tables:
        op.create_table(
            "active_resource_discovery_runs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", sa.Text(), nullable=False),
            sa.Column("point_fingerprint", sa.Text(), nullable=False),
            sa.Column("provider_meta", sa.Text(), nullable=False),
            sa.Column("searched_point_count", sa.Integer(), nullable=False),
            sa.Column("resource_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("warnings", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )


def downgrade() -> None:
    # These tables may contain user resources and historical plan evidence.
    # An automated downgrade must never erase them.
    pass

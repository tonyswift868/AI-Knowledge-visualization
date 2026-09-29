"""Add durable planning-resource candidate pools and decision history."""

from alembic import op
import sqlalchemy as sa


revision = "0026_planning_resource_pool"
down_revision = "0025_planning_assistant_cache"
branch_labels = None
depends_on = None


def _ensure_columns(bind, table_name: str, columns: list[sa.Column]) -> None:
    """Add only missing columns when a partially-created table is present.

    Some local databases were interrupted while applying this revision.  The
    migration must repair those tables one column at a time and remain safe
    to run repeatedly.
    """
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    for column in columns:
        if column.name in existing:
            continue
        op.add_column(table_name, column)
        existing.add(column.name)


def _ensure_index(bind, name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    inspector = sa.inspect(bind)
    indexes = inspector.get_indexes(table_name)
    if name in {item.get("name") for item in indexes}:
        return
    wanted = tuple(columns)
    if any(tuple(item.get("column_names") or ()) == wanted and (not unique or item.get("unique")) for item in indexes):
        return
    if unique:
        constraints = inspector.get_unique_constraints(table_name)
        if any(tuple(item.get("column_names") or ()) == wanted for item in constraints):
            return
    op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "orm_planning_resource_pools" not in tables:
        op.create_table(
            "orm_planning_resource_pools",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("workspace_id", sa.String(64), nullable=False),
            sa.Column("planning_session_id", sa.String(128), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("page_size", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("selected_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("anchor_candidate_id", sa.String(180), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("workspace_id", "planning_session_id", name="uq_orm_planning_resource_pool_session"),
        )
        op.create_index("ix_orm_planning_resource_pools_workspace_id", "orm_planning_resource_pools", ["workspace_id"])
        op.create_index("ix_orm_planning_resource_pool_workspace_revision", "orm_planning_resource_pools", ["workspace_id", "revision"])
    else:
        _ensure_columns(bind, "orm_planning_resource_pools", [
            sa.Column("workspace_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("planning_session_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("page_size", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("selected_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("anchor_candidate_id", sa.String(180), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        ])
    _ensure_index(bind, "ix_orm_planning_resource_pools_workspace_id", "orm_planning_resource_pools", ["workspace_id"])
    _ensure_index(bind, "ix_orm_planning_resource_pool_workspace_revision", "orm_planning_resource_pools", ["workspace_id", "revision"])
    _ensure_index(bind, "uq_orm_planning_resource_pool_session", "orm_planning_resource_pools", ["workspace_id", "planning_session_id"], unique=True)
    if "orm_planning_resource_candidates" not in tables:
        op.create_table(
            "orm_planning_resource_candidates",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("pool_id", sa.String(64), nullable=False),
            sa.Column("candidate_id", sa.String(180), nullable=False),
            sa.Column("stable_id", sa.String(180), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payload_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("selection_state", sa.String(24), nullable=False, server_default="unselected"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("retained", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("direct_user_reference", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("pool_id", "stable_id", name="uq_orm_planning_resource_candidate_identity"),
        )
        op.create_index("ix_orm_planning_resource_candidates_pool_id", "orm_planning_resource_candidates", ["pool_id"])
        op.create_index("ix_orm_planning_resource_candidates_selection_state", "orm_planning_resource_candidates", ["selection_state"])
        op.create_index("ix_orm_planning_resource_candidates_active", "orm_planning_resource_candidates", ["active"])
        op.create_index("ix_orm_planning_resource_candidates_direct_user_reference", "orm_planning_resource_candidates", ["direct_user_reference"])
        op.create_index("ix_orm_planning_resource_candidate_pool_active", "orm_planning_resource_candidates", ["pool_id", "active", "ordinal"])
    else:
        _ensure_columns(bind, "orm_planning_resource_candidates", [
            sa.Column("pool_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("candidate_id", sa.String(180), nullable=False, server_default=""),
            sa.Column("stable_id", sa.String(180), nullable=False, server_default=""),
            sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("selection_state", sa.String(24), nullable=False, server_default="unselected"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("retained", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("direct_user_reference", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        ])
    _ensure_index(bind, "ix_orm_planning_resource_candidates_pool_id", "orm_planning_resource_candidates", ["pool_id"])
    _ensure_index(bind, "ix_orm_planning_resource_candidates_selection_state", "orm_planning_resource_candidates", ["selection_state"])
    _ensure_index(bind, "ix_orm_planning_resource_candidates_active", "orm_planning_resource_candidates", ["active"])
    _ensure_index(bind, "ix_orm_planning_resource_candidates_direct_user_reference", "orm_planning_resource_candidates", ["direct_user_reference"])
    _ensure_index(bind, "ix_orm_planning_resource_candidate_pool_active", "orm_planning_resource_candidates", ["pool_id", "active", "ordinal"])
    _ensure_index(bind, "uq_orm_planning_resource_candidate_identity", "orm_planning_resource_candidates", ["pool_id", "stable_id"], unique=True)
    if "orm_planning_resource_pool_events" not in tables:
        op.create_table(
            "orm_planning_resource_pool_events",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("pool_id", sa.String(64), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(40), nullable=False),
            sa.Column("candidate_id", sa.String(180), nullable=False),
            sa.Column("before_state", sa.String(24), nullable=False, server_default=""),
            sa.Column("after_state", sa.String(24), nullable=False, server_default=""),
            sa.Column("client_request_id", sa.String(160), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_orm_planning_resource_pool_events_pool_id", "orm_planning_resource_pool_events", ["pool_id"])
        op.create_index("ix_orm_planning_resource_pool_event_pool_revision", "orm_planning_resource_pool_events", ["pool_id", "revision"])
    else:
        _ensure_columns(bind, "orm_planning_resource_pool_events", [
            sa.Column("pool_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("action", sa.String(40), nullable=False, server_default=""),
            sa.Column("candidate_id", sa.String(180), nullable=False, server_default=""),
            sa.Column("before_state", sa.String(24), nullable=False, server_default=""),
            sa.Column("after_state", sa.String(24), nullable=False, server_default=""),
            sa.Column("client_request_id", sa.String(160), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        ])
    _ensure_index(bind, "ix_orm_planning_resource_pool_events_pool_id", "orm_planning_resource_pool_events", ["pool_id"])
    _ensure_index(bind, "ix_orm_planning_resource_pool_event_pool_revision", "orm_planning_resource_pool_events", ["pool_id", "revision"])
    if "orm_planning_resource_pool_requests" not in tables:
        op.create_table(
            "orm_planning_resource_pool_requests",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("pool_id", sa.String(64), nullable=False),
            sa.Column("client_request_id", sa.String(160), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("response_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("pool_id", "client_request_id", name="uq_orm_planning_resource_pool_request"),
        )
        op.create_index("ix_orm_planning_resource_pool_requests_pool_id", "orm_planning_resource_pool_requests", ["pool_id"])
    else:
        _ensure_columns(bind, "orm_planning_resource_pool_requests", [
            sa.Column("pool_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("client_request_id", sa.String(160), nullable=False, server_default=""),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("response_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        ])
    _ensure_index(bind, "ix_orm_planning_resource_pool_requests_pool_id", "orm_planning_resource_pool_requests", ["pool_id"])
    _ensure_index(bind, "uq_orm_planning_resource_pool_request", "orm_planning_resource_pool_requests", ["pool_id", "client_request_id"], unique=True)


def downgrade() -> None:
    # Candidate history is audit state and must survive a downgrade. Restore
    # from a backup rather than dropping learner decisions.
    pass

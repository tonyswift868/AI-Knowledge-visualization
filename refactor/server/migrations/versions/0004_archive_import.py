"""add secure archive preview/import metadata"""

from alembic import op
import sqlalchemy as sa


revision = "0004_archive_import"
down_revision = "0003_provider_workspace_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "archive_previews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("archive_name", sa.String(240), nullable=False),
        sa.Column("archive_hash", sa.String(128), nullable=False),
        sa.Column("archive_size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expanded_size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="ready"),
        sa.Column("expires_at", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("archive_mtime_ns", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["workspace_id"], ["orm_workspaces.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "archive_preview_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("preview_id", sa.String(64), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("filename", sa.String(240), nullable=False),
        sa.Column("extension", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compressed_size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(128), nullable=False, server_default=""),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("language", sa.String(32), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("selected_by_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["preview_id"], ["archive_previews.id"], ondelete="CASCADE"),
    )
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("orm_sources")}
    additions = {
        "archive_preview_id": sa.Column("archive_preview_id", sa.String(64), nullable=False, server_default=""),
        "archive_relative_path": sa.Column("archive_relative_path", sa.Text(), nullable=False, server_default=""),
        "parent_archive_name": sa.Column("parent_archive_name", sa.String(240), nullable=False, server_default=""),
        "language": sa.Column("language", sa.String(32), nullable=False, server_default=""),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("orm_sources", column)


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("orm_sources")}
    for name in ("language", "parent_archive_name", "archive_relative_path", "archive_preview_id"):
        if name in columns:
            op.drop_column("orm_sources", name)
    op.drop_table("archive_preview_items")
    op.drop_table("archive_previews")

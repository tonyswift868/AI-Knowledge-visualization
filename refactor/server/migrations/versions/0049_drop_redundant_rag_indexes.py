"""Drop RAG indexes covered by the corresponding primary-key prefixes."""

from alembic import op


revision = "0049_drop_redundant_rag_indexes"
down_revision = "0048_rag_source_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_rag_chunks_account_document")
    op.execute("DROP INDEX IF EXISTS ix_rag_source_chunks_logical")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.create_index("ix_rag_chunks_account_document", "rag_chunks", ["account_scope", "document_id"])
    op.create_index("ix_rag_source_chunks_logical", "rag_source_chunks", ["account_scope", "document_id"])

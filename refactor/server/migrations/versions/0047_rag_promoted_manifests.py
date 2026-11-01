"""Retain source chunk manifests when an alias becomes a RAG canonical document."""
from alembic import op
import sqlalchemy as sa


revision = "0047_rag_promoted_manifests"
down_revision = "0046_account_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.create_table(
        "rag_document_upload_manifests",
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(160), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("chunk_digests", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("account_scope", "document_id", name="pk_rag_document_upload_manifests"),
        sa.ForeignKeyConstraint(
            ["account_scope", "document_id"],
            ["rag_documents.account_scope", "rag_documents.document_id"],
            name="fk_rag_upload_manifest_document",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("total_chunks > 0", name="ck_rag_upload_manifest_total_chunks"),
    )
    op.execute("ALTER TABLE public.rag_document_upload_manifests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.rag_document_upload_manifests FORCE ROW LEVEL SECURITY")
    predicate = "account_scope = NULLIF(current_setting('mentorflow.rag_account_scope', true), '')"
    op.execute(
        "CREATE POLICY tenant_isolation_rag_document_upload_manifests "
        "ON public.rag_document_upload_manifests "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON public.rag_document_upload_manifests TO mentorflow_app"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Forward only: dropping promoted upload manifests would make retried uploads with "
        "different chunking conflict; roll back the application and retain this additive table."
    )

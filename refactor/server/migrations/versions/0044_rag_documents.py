"""Store per-account RAG documents and embeddings."""

from alembic import context, op
import sqlalchemy as sa


revision = "0044_rag_documents"
down_revision = "0043_native_sessions"
branch_labels = None
depends_on = None


class Vector512(sa.types.UserDefinedType):
    def get_col_spec(self, **kw):
        return "vector(512)"


def _local_test_backend() -> bool:
    try:
        return context.get_x_argument(as_dictionary=True).get("rag_vector_backend") == "local-test"
    except (AttributeError, RuntimeError):
        return False


def upgrade() -> None:
    bind = op.get_bind()
    local_test = _local_test_backend()
    if bind.dialect.name == "postgresql" and not local_test:
        installed = bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname='vector'")).scalar()
        if installed is None:
            raise RuntimeError("RAG_PGVECTOR_REQUIRED: install pgvector and CREATE EXTENSION vector before migrating")
    elif bind.dialect.name not in ("postgresql", "sqlite"):
        raise RuntimeError("RAG_PGVECTOR_REQUIRED: RAG storage requires PostgreSQL with pgvector")
    # SQLite is used by the existing Python migration-contract tests only. It gets a
    # harmless binary placeholder so those tests can still walk the full migration
    # chain; the Java service accepts PostgreSQL only. PostgreSQL without pgvector
    # requires the explicit local-test switch and the test-only BYTEA vector store.
    test_binary = local_test or bind.dialect.name == "sqlite"
    vector_type = sa.LargeBinary() if test_binary else Vector512()
    op.create_table(
        "rag_documents",
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(160), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("title", sa.String(320), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("account_scope", "document_id", name="pk_rag_documents"),
        sa.CheckConstraint("total_chunks > 0", name="ck_rag_documents_total_chunks"),
        sa.CheckConstraint("status IN ('uploading','ready')", name="ck_rag_documents_status"),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR length(content_sha256) = 64",
            name="ck_rag_documents_content_sha256",
        ),
        sa.CheckConstraint(
            "(status = 'uploading' AND content_sha256 IS NULL) OR "
            "(status = 'ready' AND content_sha256 IS NOT NULL)",
            name="ck_rag_documents_status_digest",
        ),
    )
    op.create_index(
        "ux_rag_documents_account_content",
        "rag_documents",
        ["account_scope", "content_sha256"],
        unique=True,
    )

    op.create_table(
        "rag_chunks",
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(160), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Text(), nullable=True),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("embedding", vector_type, nullable=False),
        sa.PrimaryKeyConstraint("account_scope", "document_id", "seq", name="pk_rag_chunks"),
        sa.ForeignKeyConstraint(
            ["account_scope", "document_id"],
            ["rag_documents.account_scope", "rag_documents.document_id"],
            name="fk_rag_chunks_document",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("seq >= 0", name="ck_rag_chunks_seq"),
        sa.CheckConstraint("length(text) > 0", name="ck_rag_chunks_text"),
    )
    op.create_index("ix_rag_chunks_account_document", "rag_chunks", ["account_scope", "document_id"])

    op.create_table(
        "rag_document_aliases",
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("requested_document_id", sa.String(160), nullable=False),
        sa.Column("canonical_document_id", sa.String(160), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        # Fixed-size SHA-256 digests in requested seq order, comma-delimited.
        # This lets a deduplicated ID replay its own original chunk batches even
        # when the canonical document used a different segmentation.
        sa.Column("chunk_digests", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "account_scope", "requested_document_id", name="pk_rag_document_aliases"
        ),
        sa.ForeignKeyConstraint(
            ["account_scope", "canonical_document_id"],
            ["rag_documents.account_scope", "rag_documents.document_id"],
            name="fk_rag_document_alias_canonical",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "requested_document_id <> canonical_document_id",
            name="ck_rag_document_alias_not_self",
        ),
        sa.CheckConstraint("total_chunks > 0", name="ck_rag_document_alias_total_chunks"),
    )
    op.create_index(
        "ix_rag_document_aliases_canonical",
        "rag_document_aliases",
        ["account_scope", "canonical_document_id"],
    )

    if bind.dialect.name == "postgresql":
        predicate = "account_scope = NULLIF(current_setting('mentorflow.rag_account_scope', true), '')"
        for table in ("rag_documents", "rag_chunks", "rag_document_aliases"):
            op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
            op.execute(
                f"CREATE POLICY tenant_isolation_{table} ON public.{table} "
                f"USING ({predicate}) WITH CHECK ({predicate})"
            )
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO mentorflow_app")
        if not test_binary:
            op.execute(
                "CREATE INDEX hnsw_rag_chunks_embedding_cosine ON public.rag_chunks "
                "USING hnsw (embedding vector_cosine_ops)"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS public.hnsw_rag_chunks_embedding_cosine")
        for table in ("rag_document_aliases", "rag_chunks", "rag_documents"):
            op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM mentorflow_app")
    op.drop_table("rag_document_aliases")
    op.drop_index("ix_rag_chunks_account_document", table_name="rag_chunks")
    op.drop_table("rag_chunks")
    op.drop_index("ux_rag_documents_account_content", table_name="rag_documents")
    op.drop_table("rag_documents")

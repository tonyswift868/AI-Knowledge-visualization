"""Retain logical-source chunk layout and verified byte ranges for RAG documents."""
from __future__ import annotations

import hashlib

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0048_rag_source_chunks"
down_revision = "0047_rag_promoted_manifests"
branch_labels = None
depends_on = None


TABLE = "rag_source_chunks"


def _replace_function_once(signature: str, old: str, new: str) -> None:
    bind = op.get_bind()
    definition = bind.execute(
        sa.text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": signature},
    ).scalar_one()
    if definition.count(old) != 1:
        raise RuntimeError(
            f"account closure function changed unexpectedly: {signature} anchor count "
            f"was {definition.count(old)}"
        )
    op.execute("SET LOCAL ROLE mentorflow_closure")
    bind.exec_driver_sql(definition.replace(old, new, 1))
    op.execute("RESET ROLE")


def _scope_expression() -> str:
    return (
        "encode(sha256(convert_to(o.issuer, 'UTF8') || decode('00', 'hex') || "
        "convert_to(o.subject_id, 'UTF8')), 'hex')"
    )


def _insert_source_rows(bind, document, chunks) -> None:
    target = "public.rag_source_chunks" if bind.dialect.name == "postgresql" else "rag_source_chunks"
    start = 0
    for row in chunks:
        length = int(row["byte_length"])
        end = start + length
        bind.execute(sa.text(
            f"INSERT INTO {target} "
            "(account_scope,document_id,canonical_document_id,seq,total_chunks,text_sha256,byte_length,"
            "start_byte,end_byte,page,section) "
            "VALUES (:scope,:document,:document,:seq,:total,:digest,:length,:start,:end,:page,:section)"
        ), {
            "scope": document["account_scope"], "document": document["document_id"],
            "seq": int(row["seq"]), "total": int(document["total_chunks"]),
            "digest": row["text_sha256"], "length": length,
            "start": start, "end": end, "page": row["page"], "section": row["section"],
        })
        start = end


def _backfill_postgresql(bind) -> None:
    """Read old all-tenant rows as the closure owner, writing as migrator."""
    last = None
    while True:
        op.execute("SET LOCAL ROLE mentorflow_closure")
        if last is None:
            document = bind.execute(sa.text(
                "SELECT account_scope,document_id,total_chunks,status FROM public.rag_documents "
                "ORDER BY account_scope,document_id LIMIT 1"
            )).mappings().first()
        else:
            document = bind.execute(sa.text(
                "SELECT account_scope,document_id,total_chunks,status FROM public.rag_documents "
                "WHERE (account_scope,document_id) > (:scope,:document) "
                "ORDER BY account_scope,document_id LIMIT 1"
            ), {"scope": last[0], "document": last[1]}).mappings().first()
        if document is None:
            op.execute("RESET ROLE")
            break
        last = (document["account_scope"], document["document_id"])
        total = int(document["total_chunks"])
        chunk_stats = None
        if document["status"] == "ready":
            chunk_stats = bind.execute(sa.text(
                "SELECT count(*) AS n,min(seq) AS low,max(seq) AS high FROM public.rag_chunks "
                "WHERE account_scope=:scope AND document_id=:document"
            ), {"scope": document["account_scope"], "document": document["document_id"]}).mappings().one()
        promoted = bind.execute(sa.text(
            "SELECT 1 FROM public.rag_document_upload_manifests "
            "WHERE account_scope=:scope AND document_id=:document"
        ), {"scope": document["account_scope"], "document": document["document_id"]}).first()
        op.execute("RESET ROLE")

        if (document["status"] != "ready" or promoted is not None or int(chunk_stats["n"]) != total
                or chunk_stats["low"] != 0 or chunk_stats["high"] != total - 1):
            continue

        start_seq = 0
        start_byte = 0
        while start_seq < total:
            op.execute("SET LOCAL ROLE mentorflow_closure")
            batch = bind.execute(sa.text(
                "SELECT seq,encode(sha256(convert_to(text,'UTF8')),'hex') AS text_sha256,"
                "octet_length(text)::bigint AS byte_length,page,section FROM public.rag_chunks "
                "WHERE account_scope=:scope AND document_id=:document AND seq>=:seq "
                "ORDER BY seq LIMIT 256"
            ), {"scope": document["account_scope"], "document": document["document_id"],
                "seq": start_seq}).mappings().all()
            op.execute("RESET ROLE")
            if not batch or any(int(row["seq"]) != start_seq + i for i, row in enumerate(batch)):
                break
            for row in batch:
                length = int(row["byte_length"])
                end_byte = start_byte + length
                bind.execute(sa.text(
                    "INSERT INTO public.rag_source_chunks "
                    "(account_scope,document_id,canonical_document_id,seq,total_chunks,text_sha256,byte_length,"
                    "start_byte,end_byte,page,section) "
                    "VALUES (:scope,:document,:document,:seq,:total,:digest,:length,:start,:end,:page,:section)"
                ), {
                    "scope": document["account_scope"], "document": document["document_id"],
                    "seq": int(row["seq"]), "total": total, "digest": row["text_sha256"],
                    "length": length, "start": start_byte, "end": end_byte,
                    "page": row["page"], "section": row["section"],
                })
                start_byte = end_byte
            start_seq += len(batch)


def _backfill_local(bind) -> None:
    """Small offline SQLite test path; PostgreSQL uses closure-owner reads."""
    inspector = inspect(bind)
    has_upload_manifests = inspector.has_table("rag_document_upload_manifests")
    documents = bind.execute(sa.text(
        "SELECT account_scope,document_id,total_chunks,status FROM rag_documents ORDER BY account_scope,document_id"
    )).mappings()
    for document in documents:
        if document["status"] != "ready":
            continue
        chunks = bind.execute(sa.text(
            "SELECT seq,text,page,section FROM rag_chunks "
            "WHERE account_scope=:scope AND document_id=:document ORDER BY seq"
        ), {"scope": document["account_scope"], "document": document["document_id"]}).mappings().all()
        total = int(document["total_chunks"])
        if len(chunks) != total or any(int(row["seq"]) != i for i, row in enumerate(chunks)):
            continue

        # A promoted ID may have had different page/section metadata even when
        # its text hashes match the current canonical sequence. Its source
        # layout is therefore unknown and must be supplied by a verified upload.
        if has_upload_manifests:
            manifest = bind.execute(sa.text(
                "SELECT total_chunks,chunk_digests FROM rag_document_upload_manifests "
                "WHERE account_scope=:scope AND document_id=:document"
            ), {"scope": document["account_scope"], "document": document["document_id"]}).mappings().first()
            if manifest is not None:
                continue

        local_chunks = []
        for row in chunks:
            encoded = row["text"].encode("utf-8")
            local_chunks.append({
                "seq": row["seq"], "text_sha256": hashlib.sha256(encoded).hexdigest(),
                "byte_length": len(encoded), "page": row["page"], "section": row["section"],
            })
        _insert_source_rows(bind, document, local_chunks)


def _backfill(bind) -> None:
    if bind.dialect.name == "postgresql":
        _backfill_postgresql(bind)
    else:
        _backfill_local(bind)


def upgrade() -> None:
    bind = op.get_bind()
    # Keep the Python-only SQLite migration chain aligned with the legacy
    # contract fixture. Production RAG migrations and RLS run on PostgreSQL.
    if bind.dialect.name != "postgresql":
        return
    op.create_table(
        TABLE,
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(160), nullable=False),
        sa.Column("canonical_document_id", sa.String(160), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("start_byte", sa.BigInteger(), nullable=True),
        sa.Column("end_byte", sa.BigInteger(), nullable=True),
        sa.Column("page", sa.Text(), nullable=True),
        sa.Column("section", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("account_scope", "document_id", "seq", name="pk_rag_source_chunks"),
        sa.ForeignKeyConstraint(
            ["account_scope", "canonical_document_id"],
            ["rag_documents.account_scope", "rag_documents.document_id"],
            name="fk_rag_source_chunks_canonical", ondelete="CASCADE",
        ),
        sa.CheckConstraint("seq >= 0", name="ck_rag_source_chunks_seq"),
        sa.CheckConstraint("total_chunks > 0 AND seq < total_chunks", name="ck_rag_source_chunks_total"),
        sa.CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_rag_source_chunks_digest"
        ),
        sa.CheckConstraint("byte_length > 0", name="ck_rag_source_chunks_byte_length"),
        sa.CheckConstraint(
            "(start_byte IS NULL AND end_byte IS NULL) OR "
            "(start_byte IS NOT NULL AND end_byte IS NOT NULL AND start_byte >= 0 AND "
            "end_byte > start_byte AND end_byte - start_byte = byte_length)",
            name="ck_rag_source_chunks_byte_range",
        ),
        schema="public",
    )
    op.create_index("ix_rag_source_chunks_canonical", TABLE,
                    ["account_scope", "canonical_document_id"],
                    schema="public")
    op.create_index("ix_rag_source_chunks_logical", TABLE,
                    ["account_scope", "document_id"],
                    schema="public")

    # 0047's upload-manifest table was not part of the 0045 closure grants.
    # Grant the closure owner temporary read access only for this migration,
    # then remove both the policy and privilege before committing.
    op.execute("GRANT SELECT ON public.rag_document_upload_manifests TO mentorflow_closure")
    op.execute(
        "CREATE POLICY account_closure_migration_read ON public.rag_document_upload_manifests "
        "FOR SELECT TO mentorflow_closure USING (true)"
    )
    _backfill(bind)
    op.execute("DROP POLICY account_closure_migration_read ON public.rag_document_upload_manifests")
    op.execute("REVOKE SELECT ON public.rag_document_upload_manifests FROM mentorflow_closure")
    op.execute(f"ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{TABLE} FORCE ROW LEVEL SECURITY")
    predicate = "account_scope = NULLIF(current_setting('mentorflow.rag_account_scope', true), '')"
    op.execute(
        f"CREATE POLICY tenant_isolation_{TABLE} ON public.{TABLE} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{TABLE} TO mentorflow_app")
    op.execute(f"GRANT SELECT, DELETE ON public.{TABLE} TO mentorflow_closure")
    op.execute(
        f"CREATE POLICY account_closure_read ON public.{TABLE} "
        "FOR SELECT TO mentorflow_closure USING (true)"
    )
    op.execute(
        f"CREATE POLICY account_closure_delete ON public.{TABLE} "
        "FOR DELETE TO mentorflow_closure USING (true)"
    )

    _replace_function_once(
        "account_closure.guard_row(text,text,jsonb,jsonb)",
        "    ELSIF p_table IN ('rag_documents','rag_chunks','rag_document_aliases') THEN",
        "    ELSIF p_table IN ('rag_documents','rag_chunks','rag_document_aliases','rag_source_chunks') THEN",
    )
    op.execute(
        f"CREATE TRIGGER account_closure_write_guard BEFORE INSERT OR UPDATE OR DELETE "
        f"ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION account_closure.guard_write()"
    )

    scope = _scope_expression()
    clean_anchor = "  DELETE FROM public.rag_document_aliases WHERE account_scope=" + scope + ";"
    _replace_function_once(
        "account_closure.clean(text,text,text)", clean_anchor,
        f"  DELETE FROM public.{TABLE} WHERE account_scope={scope};\n" + clean_anchor,
    )
    complete_anchor = (
        "     OR EXISTS (SELECT 1 FROM public.rag_document_aliases WHERE account_scope=" + scope + ")"
    )
    _replace_function_once(
        "account_closure.complete(text,text,text)", complete_anchor,
        f"     OR EXISTS (SELECT 1 FROM public.{TABLE} WHERE account_scope={scope})\n" + complete_anchor,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Forward only: dropping RAG source chunk metadata would lose verified source layouts; "
        "roll back the application and retain this additive table."
    )

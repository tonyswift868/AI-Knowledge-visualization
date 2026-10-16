"""Include account-scoped RAG data in the existing account-closure transaction."""

from alembic import op
import sqlalchemy as sa


revision = "0045_account_closure_rag"
down_revision = "0044_rag_documents"
branch_labels = None
depends_on = None


RAG_TABLES = ("rag_document_aliases", "rag_chunks", "rag_documents")


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
    # The production migrator has USAGE but no effective CREATE on
    # account_closure. CREATE OR REPLACE must therefore run as the schema
    # owner. Keep the role change local to this migration transaction and
    # restore the migrator before it touches public-table policies or triggers.
    op.execute("SET LOCAL ROLE mentorflow_closure")
    bind.exec_driver_sql(definition.replace(old, new, 1))
    op.execute("RESET ROLE")


def _scope_expression() -> str:
    # Matches RagService.accountScope(): lowercase hex SHA-256 of issuer + NUL + subject.
    # PostgreSQL text cannot contain NUL, so concatenate the zero byte as bytea.
    return (
        "encode(sha256(convert_to(o.issuer, 'UTF8') || decode('00', 'hex') || "
        "convert_to(o.subject_id, 'UTF8')), 'hex')"
    )


def _rag_guard_branch() -> str:
    return "\n".join((
        "    ELSIF p_table IN ('rag_documents','rag_chunks','rag_document_aliases') THEN",
        "      -- RAG writers use this same advisory key. READ COMMITTED gives this",
        "      -- SELECT a fresh snapshot after waiting for the shared lock.",
        "      IF current_setting('transaction_isolation') <> 'read committed' THEN",
        "        RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='CLOSURE_WRITE_REQUIRES_READ_COMMITTED';",
        "      END IF;",
        "      IF p_old IS NOT NULL AND p_new IS NOT NULL AND",
        "         p_old->>'account_scope' IS DISTINCT FROM p_new->>'account_scope' THEN",
        "        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='RAG_ACCOUNT_SCOPE_IMMUTABLE';",
        "      END IF;",
        "      PERFORM pg_advisory_xact_lock_shared(",
        "        hashtextextended('mentorflow.rag:' || COALESCE(r->>'account_scope',''), 0));",
        "      IF EXISTS (",
        "        SELECT 1 FROM account_closure.operations o",
        "        WHERE o.committed_at IS NOT NULL",
        "          AND encode(sha256(convert_to(o.issuer,'UTF8') || decode('00','hex') ||",
        "                            convert_to(o.subject_id,'UTF8')),'hex') = r->>'account_scope'",
        "      ) THEN",
        "        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='ACCOUNT_CLOSURE_IN_PROGRESS';",
        "      END IF;",
    ))


def _rag_lock_line() -> str:
    return f"  PERFORM pg_advisory_xact_lock(hashtextextended('mentorflow.rag:' || {_scope_expression()}, 0));"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Existing SQLite migration-contract tests have no account_closure schema.
        return

    # The closure owner runs these deletes under SECURITY DEFINER. Give it the
    # same explicit table-level grants and per-table policies used by 0041.
    for table in RAG_TABLES:
        op.execute(f"GRANT SELECT, DELETE ON public.{table} TO mentorflow_closure")
        op.execute(
            f"CREATE POLICY account_closure_read ON public.{table} "
            "FOR SELECT TO mentorflow_closure USING (true)"
        )
        op.execute(
            f"CREATE POLICY account_closure_delete ON public.{table} "
            "FOR DELETE TO mentorflow_closure USING (true)"
        )

    # The existing generic write trigger calls guard_row for every mutation.
    # Add the RAG scope branch and attach that same trigger to these tables.
    guard_anchor = (
        "    ELSIF p_table IN ('asset_manifests','asset_revisions','account_import_ledger',"
        "'structured_sync_entities','structured_sync_requests','rehydration_manifests',"
        "'enterprise_provider_sessions','workspace_role_bindings','workspace_memberships',"
        "'organization_memberships') THEN"
    )
    _replace_function_once(
        "account_closure.guard_row(text,text,jsonb,jsonb)",
        guard_anchor,
        _rag_guard_branch() + "\n" + guard_anchor,
    )
    for table in RAG_TABLES:
        op.execute(
            f"CREATE TRIGGER account_closure_write_guard BEFORE INSERT OR UPDATE OR DELETE "
            f"ON public.{table} FOR EACH ROW EXECUTE FUNCTION account_closure.guard_write()"
        )

    scope = _scope_expression()
    clean_anchor = (
        "  DELETE FROM public.documents WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';"
    )
    clean_with_rag = "\n".join(
        [_rag_lock_line(), *(f"  DELETE FROM public.{table} WHERE account_scope={scope};"
                             for table in RAG_TABLES)]
    ) + "\n" + clean_anchor
    _replace_function_once(
        "account_closure.clean(text,text,text)", clean_anchor, clean_with_rag
    )

    complete_anchor = (
        "     OR EXISTS (SELECT 1 FROM public.documents "
        "WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')"
    )
    complete_with_rag = "\n".join(
        (
            f"     OR EXISTS (SELECT 1 FROM public.{table} WHERE account_scope={scope})"
            for table in RAG_TABLES
        )
    ) + "\n" + complete_anchor
    _replace_function_once(
        "account_closure.complete(text,text,text)", complete_anchor, complete_with_rag
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    scope = _scope_expression()
    clean_with_rag = "\n".join(
        [_rag_lock_line(), *(f"  DELETE FROM public.{table} WHERE account_scope={scope};"
                             for table in RAG_TABLES)]
    ) + "\n"
    clean_anchor = (
        "  DELETE FROM public.documents WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';"
    )
    _replace_function_once(
        "account_closure.clean(text,text,text)",
        clean_with_rag + clean_anchor,
        clean_anchor,
    )

    complete_with_rag = "\n".join(
        (
            f"     OR EXISTS (SELECT 1 FROM public.{table} WHERE account_scope={scope})"
            for table in RAG_TABLES
        )
    ) + "\n"
    complete_anchor = (
        "     OR EXISTS (SELECT 1 FROM public.documents "
        "WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')"
    )
    _replace_function_once(
        "account_closure.complete(text,text,text)",
        complete_with_rag + complete_anchor,
        complete_anchor,
    )

    for table in RAG_TABLES:
        op.execute(f"DROP TRIGGER account_closure_write_guard ON public.{table}")
        op.execute(f"DROP POLICY account_closure_read ON public.{table}")
        op.execute(f"DROP POLICY account_closure_delete ON public.{table}")
        op.execute(f"REVOKE SELECT, DELETE ON public.{table} FROM mentorflow_closure")

    guard_anchor = (
        "    ELSIF p_table IN ('asset_manifests','asset_revisions','account_import_ledger',"
        "'structured_sync_entities','structured_sync_requests','rehydration_manifests',"
        "'enterprise_provider_sessions','workspace_role_bindings','workspace_memberships',"
        "'organization_memberships') THEN"
    )
    _replace_function_once(
        "account_closure.guard_row(text,text,jsonb,jsonb)",
        _rag_guard_branch() + "\n" + guard_anchor,
        guard_anchor,
    )

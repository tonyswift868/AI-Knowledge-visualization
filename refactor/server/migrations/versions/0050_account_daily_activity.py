"""Record one UTC activity row per authenticated account and day."""

from alembic import op
import sqlalchemy as sa


revision = "0050_account_daily_activity"
down_revision = "0049_drop_redundant_rag_indexes"
branch_labels = None
depends_on = None

TABLE = "account_daily_activity"


def _scope_expression() -> str:
    return (
        "encode(sha256(convert_to(o.issuer, 'UTF8') || decode('00', 'hex') || "
        "convert_to(o.subject_id, 'UTF8')), 'hex')"
    )


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


def _activity_guard_branch() -> str:
    return "\n".join((
        "    ELSIF p_table='account_daily_activity' THEN",
        "      IF current_setting('transaction_isolation') <> 'read committed' THEN",
        "        RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='CLOSURE_WRITE_REQUIRES_READ_COMMITTED';",
        "      END IF;",
        "      IF p_old IS NOT NULL AND p_new IS NOT NULL AND",
        "         p_old->>'account_scope' IS DISTINCT FROM p_new->>'account_scope' THEN",
        "        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='RAG_ACCOUNT_SCOPE_IMMUTABLE';",
        "      END IF;",
        "      IF r IS NOT NULL THEN",
        "        PERFORM pg_advisory_xact_lock_shared(",
        "          hashtextextended('mentorflow.rag:' || COALESCE(r->>'account_scope',''), 0));",
        "        IF EXISTS (",
        "          SELECT 1 FROM account_closure.operations o",
        "          WHERE o.committed_at IS NOT NULL",
        "            AND encode(sha256(convert_to(o.issuer,'UTF8') || decode('00','hex') ||",
        "                              convert_to(o.subject_id,'UTF8')),'hex') = r->>'account_scope'",
        "        ) THEN",
        "          RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='ACCOUNT_CLOSURE_IN_PROGRESS';",
        "        END IF;",
        "      END IF;",
    ))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.create_table(
        TABLE,
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("account_scope", "day", name="pk_account_daily_activity"),
        schema="public",
    )
    op.execute(f"ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{TABLE} FORCE ROW LEVEL SECURITY")
    predicate = (
        "account_scope = encode(sha256(convert_to(current_setting('mentorflow.actor_issuer', true), 'UTF8') || "
        "decode('00', 'hex') || convert_to(current_setting('mentorflow.actor_subject', true), 'UTF8')), 'hex')"
    )
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

    guard_anchor = (
        "    ELSIF p_table IN ('asset_manifests','asset_revisions','account_import_ledger',"
        "'structured_sync_entities','structured_sync_requests','rehydration_manifests',"
        "'enterprise_provider_sessions','workspace_role_bindings','workspace_memberships',"
        "'organization_memberships') THEN"
    )
    _replace_function_once(
        "account_closure.guard_row(text,text,jsonb,jsonb)",
        guard_anchor,
        _activity_guard_branch() + "\n" + guard_anchor,
    )
    op.execute(
        f"CREATE TRIGGER account_closure_write_guard BEFORE INSERT OR UPDATE OR DELETE "
        f"ON public.{TABLE} FOR EACH ROW EXECUTE FUNCTION account_closure.guard_write()"
    )

    scope = _scope_expression()
    _replace_function_once(
        "account_closure.clean(text,text,text)",
        "  DELETE FROM public.structured_sync_requests WHERE issuer=o.issuer AND subject_id=o.subject_id;",
        "  DELETE FROM public.structured_sync_requests WHERE issuer=o.issuer AND subject_id=o.subject_id;\n"
        f"  PERFORM pg_advisory_xact_lock(hashtextextended('mentorflow.rag:' || {scope}, 0));\n"
        f"  DELETE FROM public.{TABLE} WHERE account_scope={scope};",
    )
    _replace_function_once(
        "account_closure.complete(text,text,text)",
        "     OR EXISTS (SELECT 1 FROM public.structured_sync_requests WHERE issuer=o.issuer AND subject_id=o.subject_id)",
        "     OR EXISTS (SELECT 1 FROM public.structured_sync_requests WHERE issuer=o.issuer AND subject_id=o.subject_id)\n"
        f"     OR EXISTS (SELECT 1 FROM public.{TABLE} WHERE account_scope={scope})",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    scope = _scope_expression()
    clean_activity = (
        "  PERFORM pg_advisory_xact_lock(hashtextextended('mentorflow.rag:' || " + scope + ", 0));\n"
        "  DELETE FROM public.account_daily_activity WHERE account_scope=" + scope + ";\n"
    )
    _replace_function_once(
        "account_closure.clean(text,text,text)",
        clean_activity,
        "",
    )
    _replace_function_once(
        "account_closure.complete(text,text,text)",
        "     OR EXISTS (SELECT 1 FROM public.account_daily_activity WHERE account_scope=" + scope + ")\n",
        "",
    )
    op.execute(f"DROP TRIGGER account_closure_write_guard ON public.{TABLE}")
    op.execute(f"DROP POLICY account_closure_read ON public.{TABLE}")
    op.execute(f"DROP POLICY account_closure_delete ON public.{TABLE}")
    op.execute(f"REVOKE SELECT, DELETE ON public.{TABLE} FROM mentorflow_closure")
    op.drop_table(TABLE, schema="public")

    guard_anchor = (
        "    ELSIF p_table IN ('asset_manifests','asset_revisions','account_import_ledger',"
        "'structured_sync_entities','structured_sync_requests','rehydration_manifests',"
        "'enterprise_provider_sessions','workspace_role_bindings','workspace_memberships',"
        "'organization_memberships') THEN"
    )
    _replace_function_once(
        "account_closure.guard_row(text,text,jsonb,jsonb)",
        _activity_guard_branch() + "\n" + guard_anchor,
        guard_anchor,
    )

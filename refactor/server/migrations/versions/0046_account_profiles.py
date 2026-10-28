"""Store private account nickname, signature and normalized avatar."""
from alembic import op
import sqlalchemy as sa


revision = "0046_account_profiles"
down_revision = "0045_account_closure_rag"
branch_labels = None
depends_on = None


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


def _profile_lock_line() -> str:
    return f"  PERFORM pg_advisory_xact_lock(hashtextextended('mentorflow.rag:' || {_scope_expression()}, 0));"


def _profile_guard_branch() -> str:
    return "\n".join((
        "    ELSIF p_table='account_profiles' THEN",
        "      IF p_old IS NOT NULL AND p_new IS NOT NULL AND",
        "         p_old->>'account_scope' IS DISTINCT FROM p_new->>'account_scope' THEN",
        "        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='ACCOUNT_PROFILE_SCOPE_IMMUTABLE';",
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


def upgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"
    # This feature is PostgreSQL-only (RLS, bytea and closure hooks). Keep
    # non-PostgreSQL migration-chain fixtures executable without pretending
    # SQLite emulates the production schema.
    if not is_postgresql:
        return
    op.create_table(
        "account_profiles",
        sa.Column("account_scope", sa.String(64), nullable=False),
        sa.Column("nickname", sa.Text(), nullable=False, server_default=""),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("avatar_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("avatar_etag", sa.String(80), nullable=True),
        sa.Column("avatar_width", sa.Integer(), nullable=True),
        sa.Column("avatar_height", sa.Integer(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.PrimaryKeyConstraint("account_scope", name="pk_account_profiles"),
        sa.CheckConstraint("account_scope ~ '^[0-9a-f]{64}$'", name="ck_account_profiles_scope"),
        sa.CheckConstraint("char_length(nickname) <= 30", name="ck_account_profiles_nickname_length"),
        sa.CheckConstraint("char_length(signature) <= 60", name="ck_account_profiles_signature_length"),
        sa.CheckConstraint("version > 0", name="ck_account_profiles_version_positive"),
        sa.CheckConstraint("avatar_etag IS NULL OR avatar_etag ~ '^[0-9a-f]{64}$'", name="ck_account_profiles_avatar_etag"),
        sa.CheckConstraint("avatar_bytes IS NULL OR octet_length(avatar_bytes) <= 262144", name="ck_account_profiles_avatar_size"),
        sa.CheckConstraint(
            "(avatar_bytes IS NULL AND avatar_etag IS NULL AND avatar_width IS NULL AND avatar_height IS NULL) "
            "OR (avatar_bytes IS NOT NULL AND avatar_etag IS NOT NULL AND avatar_width = avatar_height "
            "AND avatar_width BETWEEN 1 AND 512)",
            name="ck_account_profiles_avatar_fields",
        ),
        schema="public",
    )

    op.execute("ALTER TABLE public.account_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.account_profiles FORCE ROW LEVEL SECURITY")
    predicate = "account_scope = NULLIF(current_setting('mentorflow.rag_account_scope', true), '')"
    op.execute(
        "CREATE POLICY tenant_isolation_account_profiles ON public.account_profiles "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.account_profiles TO mentorflow_app")
    op.execute("GRANT SELECT, DELETE ON public.account_profiles TO mentorflow_closure")
    op.execute(
        "CREATE POLICY account_closure_read ON public.account_profiles "
        "FOR SELECT TO mentorflow_closure USING (true)"
    )
    op.execute(
        "CREATE POLICY account_closure_delete ON public.account_profiles "
        "FOR DELETE TO mentorflow_closure USING (true)"
    )

    guard_anchor = "    ELSIF p_table IN ('documents','evidence','knowledge_maps','learning_plans','teacher_sessions','review_campaigns','student_events','cost_events','resource_scopes') THEN"
    _replace_function_once(
        "account_closure.guard_row(text,text,jsonb,jsonb)",
        guard_anchor,
        _profile_guard_branch() + "\n" + guard_anchor,
    )
    op.execute(
        "CREATE TRIGGER account_closure_write_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.account_profiles FOR EACH ROW EXECUTE FUNCTION account_closure.guard_write()"
    )

    scope = _scope_expression()
    clean_anchor = "  DELETE FROM public.documents WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';"
    _replace_function_once(
        "account_closure.clean(text,text,text)",
        clean_anchor,
        _profile_lock_line() + f"\n  DELETE FROM public.account_profiles WHERE account_scope={scope};\n" + clean_anchor,
    )
    complete_anchor = (
        "     OR EXISTS (SELECT 1 FROM public.documents "
        "WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')"
    )
    _replace_function_once(
        "account_closure.complete(text,text,text)",
        complete_anchor,
        f"     OR EXISTS (SELECT 1 FROM public.account_profiles WHERE account_scope={scope})\n" + complete_anchor,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Forward only: dropping account_profiles would permanently delete user profile data; "
        "roll back the application and keep this additive schema."
    )

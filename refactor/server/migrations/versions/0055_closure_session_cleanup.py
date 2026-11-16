"""Delete native session material for closed accounts; retain the closure fence."""
import logging
import time

from alembic import op
import sqlalchemy as sa

revision = "0055_closure_session_cleanup"
down_revision = "0054_sync_receipts_without_payload"
branch_labels = depends_on = None

# Read-only deployment rehearsal as postgres (counts only; before new grants):
# BEGIN READ ONLY;
# SELECT count(*) AS sessions_to_delete FROM public.native_session_families f
# WHERE EXISTS (SELECT 1 FROM account_closure.operations o
#   WHERE o.issuer=f.issuer AND o.subject_id=f.subject AND o.state='COMPLETED');
# COMMIT;


def _replace(signature, old, new):
    bind = op.get_bind()
    definition = bind.execute(sa.text(
        "SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
        {"signature": signature}).scalar_one()
    if definition.count(old) != 1:
        raise RuntimeError(f"Unexpected closure function: {signature}")
    op.execute("SET LOCAL ROLE mentorflow_closure")
    bind.exec_driver_sql(definition.replace(old, new, 1))
    op.execute("RESET ROLE")


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    started = time.perf_counter()
    op.execute("GRANT SELECT, DELETE ON public.native_session_families TO mentorflow_closure")
    # Explicit policies also work when FORCE RLS is enabled on this table.
    op.execute("CREATE POLICY closure_sessions_read ON public.native_session_families "
               "FOR SELECT TO mentorflow_closure USING (true)")
    op.execute("CREATE POLICY closure_sessions_delete ON public.native_session_families "
               "FOR DELETE TO mentorflow_closure USING (true)")
    anchor = "  DELETE FROM public.structured_sync_requests WHERE issuer=o.issuer AND subject_id=o.subject_id;"
    _replace("account_closure.clean(text,text,text)", anchor,
             anchor + "\n  DELETE FROM public.native_session_families WHERE issuer=o.issuer AND subject=o.subject_id;")
    # Completion repeats deletion for operations whose clean checkpoint predates this upgrade.
    anchor = "  UPDATE account_closure.operations SET state='COMPLETED',code='CLOSURE_COMPLETED',"
    _replace("account_closure.complete(text,text,text)", anchor,
             "  DELETE FROM public.native_session_families WHERE issuer=o.issuer AND subject=o.subject_id;\n" + anchor)
    op.execute("SET LOCAL ROLE mentorflow_closure")
    op.execute("""DELETE FROM public.native_session_families f WHERE EXISTS (
        SELECT 1 FROM account_closure.operations o WHERE o.issuer=f.issuer
        AND o.subject_id=f.subject AND o.state='COMPLETED')""")
    op.execute("RESET ROLE")
    message = f"closure session cleanup elapsed {(time.perf_counter()-started)*1000:.3f} ms"
    logging.getLogger("alembic.runtime.migration").info(message)
    op.get_context().config.print_stdout(message)


def downgrade():
    raise RuntimeError("Forward only: deleted sessions cannot be restored")

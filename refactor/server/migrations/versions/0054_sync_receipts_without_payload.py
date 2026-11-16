"""Remove payload copies from structured sync idempotency receipts."""
import logging
import time

from alembic import op


revision = "0054_sync_receipts_without_payload"
down_revision = "0053_workbench_question_index"
branch_labels = None
depends_on = None

# Read-only deployment rehearsal (does not expose receipt content):
# BEGIN READ ONLY;
# SET LOCAL ROLE mentorflow_closure;
# SELECT COUNT(*) AS total_receipts,
#        COUNT(*) FILTER (WHERE response::jsonb ? 'payload') AS receipts_to_change,
#        COALESCE(SUM(pg_column_size(response)), 0) AS total_response_bytes,
#        COALESCE(SUM(pg_column_size(response)) FILTER
#            (WHERE response::jsonb ? 'payload'), 0) AS response_bytes_to_change
# FROM public.structured_sync_requests;
# COMMIT;


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    started = time.perf_counter()
    # FORCE RLS also applies to the migration owner; keep this permission local
    # to the migration transaction and remove it before committing.
    op.execute("CREATE POLICY receipt_scrub_0054 ON public.structured_sync_requests "
               "TO mentorflow_migrator USING (true) WITH CHECK (true)")
    op.execute("""
        UPDATE public.structured_sync_requests
        SET response = (response::jsonb - 'payload')::json
        WHERE response::jsonb ? 'payload'
    """)
    op.execute("DROP POLICY receipt_scrub_0054 ON public.structured_sync_requests")
    elapsed_ms = (time.perf_counter() - started) * 1000
    message = f"0054 sync receipt payload scrub UPDATE elapsed {elapsed_ms:.3f} ms"
    logging.getLogger("alembic.runtime.migration").info(message)
    op.get_context().config.print_stdout(message)


def downgrade() -> None:
    raise RuntimeError("Forward only: scrubbed receipt payloads cannot be restored")

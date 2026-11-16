"""Clear learning progress contents retained under deleted plans."""
import logging
import time

from alembic import op


revision = "0055_purge_deleted_plan_progress"
down_revision = "0054_sync_receipts_without_payload"
branch_labels = None
depends_on = None

# Read-only deployment rehearsal: count only answer, judgement, practice,
# and usage rows whose content would be purged.
# The hashed course-plan ID is derived from compact JSON ["plans", plan_key];
# course plan keys are restricted to ASCII ID characters by the sync contract.
# BEGIN READ ONLY;
# SET LOCAL ROLE mentorflow_closure;
# SELECT count(*) AS progress_rows_to_purge
# FROM public.structured_sync_entities e
# WHERE e.deleted = false AND (
#   (e.kind = 'progress' AND EXISTS (
#     SELECT 1 FROM public.structured_sync_entities p
#     WHERE p.issuer=e.issuer AND p.subject_id=e.subject_id
#       AND p.organization_id=e.organization_id AND p.workspace_id=e.workspace_id
#       AND p.deleted=true AND ((p.kind='plan' AND p.entity_id=e.payload->>'plan_id') OR
#         (p.kind='learning_run' AND p.entity_id='refactor.v1:course_record:' || encode(sha256(
#           convert_to('["plans","' || (e.payload->>'plan_id') || '"]','UTF8')), 'hex')))))
#   OR (e.kind='learning_run' AND e.payload->>'record_type'='course_record'
#       AND e.payload #>> '{values,entity}' IN
#           ('judgements','attempt_closed','credits','ledger_events','usage','practice_results','question_answers')
#       AND EXISTS (
#         SELECT 1 FROM public.structured_sync_entities p
#         WHERE p.issuer=e.issuer AND p.subject_id=e.subject_id
#           AND p.organization_id=e.organization_id AND p.workspace_id=e.workspace_id
#           AND p.deleted=true AND p.kind='learning_run'
#           AND p.entity_id='refactor.v1:course_record:' || encode(sha256(
#             convert_to('["plans","' || (e.payload #>> '{values,plan_key}') || '"]','UTF8')), 'hex')))
# );
# COMMIT;

PURGE = """
UPDATE public.structured_sync_entities e
SET revision=e.revision+1, deleted=true, payload='{}'::json
WHERE e.deleted=false AND (
  (e.kind='progress' AND EXISTS (
    SELECT 1 FROM public.structured_sync_entities p
    WHERE p.issuer=e.issuer AND p.subject_id=e.subject_id
      AND p.organization_id=e.organization_id AND p.workspace_id=e.workspace_id
      AND p.deleted=true AND ((p.kind='plan' AND p.entity_id=e.payload->>'plan_id') OR
        (p.kind='learning_run' AND p.entity_id='refactor.v1:course_record:' || encode(sha256(
          convert_to('["plans","' || (e.payload->>'plan_id') || '"]','UTF8')), 'hex')))))
  OR (e.kind='learning_run' AND e.payload->>'record_type'='course_record'
      AND e.payload #>> '{values,entity}' IN
          ('judgements','attempt_closed','credits','ledger_events','usage','practice_results','question_answers')
      AND EXISTS (
        SELECT 1 FROM public.structured_sync_entities p
        WHERE p.issuer=e.issuer AND p.subject_id=e.subject_id
          AND p.organization_id=e.organization_id AND p.workspace_id=e.workspace_id
          AND p.deleted=true AND p.kind='learning_run'
          AND p.entity_id='refactor.v1:course_record:' || encode(sha256(
            convert_to('["plans","' || (e.payload #>> '{values,plan_key}') || '"]','UTF8')), 'hex')))
)
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    if op.get_bind().exec_driver_sql("SELECT current_user").scalar_one() != "mentorflow_migrator":
        raise RuntimeError("Deleted plan progress scrub requires mentorflow_migrator")
    started = time.perf_counter()
    # FORCE RLS also applies to the migration owner. This temporary policy
    # exists only within the atomic migration transaction.
    op.execute("CREATE POLICY plan_progress_scrub_0055 ON public.structured_sync_entities "
               "TO mentorflow_migrator USING (true) WITH CHECK (true)")
    op.execute(PURGE)
    op.execute("DROP POLICY plan_progress_scrub_0055 ON public.structured_sync_entities")
    elapsed_ms = (time.perf_counter() - started) * 1000
    message = f"0055 deleted plan progress scrub UPDATE elapsed {elapsed_ms:.3f} ms"
    logging.getLogger("alembic.runtime.migration").info(message)
    op.get_context().config.print_stdout(message)


def downgrade() -> None:
    raise RuntimeError("Forward only: purged progress payloads cannot be restored")

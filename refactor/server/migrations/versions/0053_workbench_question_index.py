"""Index synced question entities for the administrator account rows view."""
import logging
import time

from alembic import op


revision = "0053_workbench_question_index"
down_revision = "0052_workbench_account_rows"
branch_labels = None
depends_on = None

INDEX = "structured_sync_entities_question_bank_issuer_subject_idx"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    started = time.perf_counter()
    op.execute(f"""CREATE INDEX {INDEX}
ON public.structured_sync_entities (issuer, subject_id)
WHERE kind = 'learning_run' AND deleted = false
  AND payload->>'schema' = 'mentorflow.refactor-sync.v1'
  AND payload->>'version' = '1'
  AND payload->>'record_type' = 'course_record'
  AND payload #>> '{{values,entity}}' = 'question_bank'
  AND payload #>> '{{values,schema}}' LIKE 'mentorflow.course.question_bank.v%'""")
    elapsed_ms = (time.perf_counter() - started) * 1000
    message = f"0053 question partial index CREATE INDEX elapsed {elapsed_ms:.3f} ms"
    logging.getLogger("alembic.runtime.migration").info(message)
    op.get_context().config.print_stdout(message)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP INDEX public.{INDEX}")

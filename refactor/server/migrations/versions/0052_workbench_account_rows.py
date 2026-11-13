"""Expose narrow per-account counts to the read-only administrator view."""
from alembic import op


revision = "0052_workbench_account_rows"
down_revision = "0051_sync_closure_fence"
branch_labels = None
depends_on = None

FUNCTION = """
CREATE OR REPLACE FUNCTION public.mentorflow_admin_account_rows(
    p_issuer text, p_subjects text[])
RETURNS TABLE(
    subject_id text,
    last_sync_day date,
    ready_document_count bigint,
    ready_chunk_count bigint,
    synced_question_count bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
WITH requested AS (
  SELECT DISTINCT subject_id
  FROM unnest(COALESCE(p_subjects, ARRAY[]::text[])) AS subjects(subject_id)
  WHERE subject_id IS NOT NULL
), scopes AS (
  SELECT r.subject_id,
         encode(sha256(convert_to(p_issuer, 'UTF8') || decode('00', 'hex') ||
                       convert_to(r.subject_id, 'UTF8')), 'hex') AS account_scope
  FROM requested r
), activity AS (
  SELECT account_scope, max(day) AS last_sync_day
  FROM public.account_daily_activity
  GROUP BY account_scope
), documents AS (
  SELECT account_scope, count(*) AS document_count
  FROM public.rag_documents
  WHERE status = 'ready'
  GROUP BY account_scope
), chunks AS (
  SELECT d.account_scope, count(*) AS chunk_count
  FROM public.rag_chunks c
  JOIN public.rag_documents d
    ON d.account_scope = c.account_scope AND d.document_id = c.document_id
  WHERE d.status = 'ready'
  GROUP BY d.account_scope
), questions AS (
  SELECT issuer, subject_id, count(*) AS question_count
  FROM public.structured_sync_entities
  WHERE kind = 'learning_run' AND deleted = false
    AND payload->>'schema' = 'mentorflow.refactor-sync.v1'
    AND payload->>'version' = '1'
    AND payload->>'record_type' = 'course_record'
    AND payload #>> '{values,entity}' = 'question_bank'
    AND payload #>> '{values,schema}' LIKE 'mentorflow.course.question_bank.v%'
  GROUP BY issuer, subject_id
)
SELECT s.subject_id, a.last_sync_day,
       COALESCE(d.document_count, 0)::bigint,
       COALESCE(c.chunk_count, 0)::bigint,
       COALESCE(q.question_count, 0)::bigint
FROM scopes s
LEFT JOIN activity a ON a.account_scope = s.account_scope
LEFT JOIN documents d ON d.account_scope = s.account_scope
LEFT JOIN chunks c ON c.account_scope = s.account_scope
LEFT JOIN questions q ON q.issuer = p_issuer AND q.subject_id = s.subject_id
ORDER BY s.subject_id
$fn$;
"""

TABLES = (
    "account_daily_activity",
    "rag_documents",
    "rag_chunks",
    "structured_sync_entities",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    if op.get_bind().exec_driver_sql("SELECT current_user").scalar_one() != "mentorflow_migrator":
        raise RuntimeError("Admin account rows migration requires mentorflow_migrator")
    for table in TABLES:
        op.execute(f"GRANT SELECT ON public.{table} TO mentorflow_migrator")
        op.execute(
            f"CREATE POLICY admin_account_rows_read ON public.{table} "
            "FOR SELECT TO mentorflow_migrator USING (true)"
        )
    op.execute(FUNCTION)
    op.execute(
        "REVOKE ALL ON FUNCTION public.mentorflow_admin_account_rows(text, text[]) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.mentorflow_admin_account_rows(text, text[]) "
        "TO mentorflow_app"
    )


def downgrade() -> None:
    raise RuntimeError("Forward only: retain administrator account rows contract")

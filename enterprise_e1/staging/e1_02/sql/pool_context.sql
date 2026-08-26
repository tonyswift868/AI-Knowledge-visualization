\set ON_ERROR_STOP on

BEGIN;
SET LOCAL ROLE mentorflow_app;
SELECT set_config('mentorflow.organization_id', 'synthetic-org-1', true);
SELECT set_config('mentorflow.workspace_id', 'synthetic-org-1-workspace-1', true);
SELECT set_config('mentorflow.actor_subject', 'synthetic-user-01', true);
DO $$
BEGIN
  IF (SELECT count(*) FROM documents) < 1 THEN
    RAISE EXCEPTION 'tenant A cannot see its own document';
  END IF;
END
$$;
COMMIT;

BEGIN;
SET LOCAL ROLE mentorflow_app;
DO $$
BEGIN
  IF NULLIF(current_setting('mentorflow.organization_id', true), '') IS NOT NULL
    OR NULLIF(current_setting('mentorflow.workspace_id', true), '') IS NOT NULL
    OR NULLIF(current_setting('mentorflow.actor_subject', true), '') IS NOT NULL THEN
    RAISE EXCEPTION 'transaction-local tenant context leaked after COMMIT';
  END IF;
END
$$;
SELECT set_config('mentorflow.organization_id', 'synthetic-org-2', true);
SELECT set_config('mentorflow.workspace_id', 'synthetic-org-2-workspace-1', true);
SELECT set_config('mentorflow.actor_subject', 'synthetic-user-08', true);
DO $$
BEGIN
  IF (
    SELECT count(*) FROM documents
    WHERE organization_id = 'synthetic-org-1'
  ) <> 0 THEN
    RAISE EXCEPTION 'tenant B observed tenant A after connection reuse';
  END IF;
END
$$;
ROLLBACK;

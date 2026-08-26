\set ON_ERROR_STOP on

BEGIN;
SET LOCAL ROLE mentorflow_app;
SELECT set_config('mentorflow.organization_id', 'synthetic-org-1', true);
SELECT set_config('mentorflow.workspace_id', 'synthetic-org-1-workspace-1', true);
SELECT set_config('mentorflow.actor_subject', 'synthetic-user-01', true);

EXPLAIN (COSTS ON, VERBOSE OFF)
SELECT * FROM documents
WHERE organization_id = 'synthetic-org-1'
  AND workspace_id = 'synthetic-org-1-workspace-1'
  AND status = 'ready'
ORDER BY updated_at DESC
LIMIT 50;

EXPLAIN (COSTS ON, VERBOSE OFF)
SELECT * FROM learning_plans
WHERE resource_id = 'synthetic:plan:1:1';

EXPLAIN (COSTS ON, VERBOSE OFF)
SELECT * FROM resource_scopes
WHERE owner_subject_id = 'synthetic-user-01'
  AND scope_type = 'PERSONAL';

EXPLAIN (COSTS ON, VERBOSE OFF)
SELECT * FROM teacher_sessions
WHERE job_id = 'job:synthetic:teacher_session:1:1';

EXPLAIN (COSTS ON, VERBOSE OFF)
SELECT * FROM cost_events
WHERE organization_id = 'synthetic-org-1'
  AND workspace_id = 'synthetic-org-1-workspace-1'
  AND cache_key = 'cache:synthetic:cost_event:1:1';
ROLLBACK;

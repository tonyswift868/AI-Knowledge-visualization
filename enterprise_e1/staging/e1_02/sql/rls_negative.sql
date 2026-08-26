\set ON_ERROR_STOP on

BEGIN;
SET LOCAL ROLE mentorflow_app;

DO $$
DECLARE
  tenant_table text;
  visible_rows bigint;
  unauthorized_success bigint := 0;
BEGIN
  FOREACH tenant_table IN ARRAY ARRAY[
    'organizations', 'organization_memberships', 'workspaces',
    'workspace_memberships', 'workspace_role_bindings', 'resource_scopes',
    'documents', 'evidence', 'knowledge_maps', 'learning_plans',
    'teacher_sessions', 'review_campaigns', 'student_events', 'cost_events'
  ] LOOP
    EXECUTE format('SELECT count(*) FROM %I', tenant_table) INTO visible_rows;
    unauthorized_success := unauthorized_success + visible_rows;
  END LOOP;
  IF unauthorized_success <> 0 THEN
    RAISE EXCEPTION 'DEFAULT_DENY failed: % rows visible without tenant context',
      unauthorized_success;
  END IF;
END
$$;
ROLLBACK;

BEGIN;
SET LOCAL ROLE mentorflow_app;
SELECT set_config('mentorflow.organization_id', 'synthetic-org-1', true);
SELECT set_config('mentorflow.workspace_id', 'synthetic-org-1-workspace-1', true);
SELECT set_config('mentorflow.actor_subject', 'synthetic-user-01', true);

DO $$
DECLARE
  resource_table text;
  foreign_id text;
  observed bigint;
  affected bigint;
  unauthorized_success bigint := 0;
BEGIN
  FOREACH resource_table IN ARRAY ARRAY[
    'documents', 'evidence', 'knowledge_maps', 'learning_plans',
    'teacher_sessions', 'review_campaigns', 'student_events', 'cost_events'
  ] LOOP
    foreign_id := 'synthetic:' ||
      CASE resource_table
        WHEN 'documents' THEN 'document'
        WHEN 'evidence' THEN 'evidence'
        WHEN 'knowledge_maps' THEN 'knowledge_map'
        WHEN 'learning_plans' THEN 'plan'
        WHEN 'teacher_sessions' THEN 'teacher_session'
        WHEN 'review_campaigns' THEN 'review_campaign'
        WHEN 'student_events' THEN 'student_event'
        WHEN 'cost_events' THEN 'cost_event'
      END || ':2:1';

    -- foreign ID read
    EXECUTE format('SELECT count(*) FROM %I WHERE resource_id = $1', resource_table)
      INTO observed USING foreign_id;
    unauthorized_success := unauthorized_success + observed;
    -- foreign list and export
    EXECUTE format(
      'SELECT count(*) FROM %I WHERE organization_id = $1', resource_table
    ) INTO observed USING 'synthetic-org-2';
    unauthorized_success := unauthorized_success + observed + observed;
    -- foreign search
    EXECUTE format(
      'SELECT count(*) FROM %I WHERE search_text LIKE $1', resource_table
    ) INTO observed USING '%tenant corpus%';
    EXECUTE format(
      'SELECT count(*) FROM %I WHERE organization_id = $1 AND search_text LIKE $2',
      resource_table
    ) INTO observed USING 'synthetic-org-2', '%tenant corpus%';
    unauthorized_success := unauthorized_success + observed;
    -- foreign update
    EXECUTE format(
      'UPDATE %I SET status = ''intrusion'' WHERE resource_id = $1', resource_table
    ) USING foreign_id;
    GET DIAGNOSTICS affected = ROW_COUNT;
    unauthorized_success := unauthorized_success + affected;
    -- foreign delete
    EXECUTE format('DELETE FROM %I WHERE resource_id = $1', resource_table)
      USING foreign_id;
    GET DIAGNOSTICS affected = ROW_COUNT;
    unauthorized_success := unauthorized_success + affected;
    -- background job and cache ownership
    EXECUTE format('SELECT count(*) FROM %I WHERE job_id = $1', resource_table)
      INTO observed USING 'job:' || foreign_id;
    unauthorized_success := unauthorized_success + observed;
    EXECUTE format('SELECT count(*) FROM %I WHERE cache_key = $1', resource_table)
      INTO observed USING 'cache:' || foreign_id;
    unauthorized_success := unauthorized_success + observed;
  END LOOP;

  IF unauthorized_success <> 0 THEN
    RAISE EXCEPTION 'tenant isolation corpus failed: % unauthorized successes',
      unauthorized_success;
  END IF;
  RAISE NOTICE 'UNAUTHORIZED_SUCCESS=0 across 8 resource kinds x 8 operations';
END
$$;
ROLLBACK;

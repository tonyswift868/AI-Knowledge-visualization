-- MF-D02 PostgreSQL storage contract v1. Frozen into migration 0041.
-- Run as mentorflow_closure (NOLOGIN, NOSUPERUSER, NOBYPASSRLS).
CREATE TABLE account_closure.settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    issuer text NOT NULL CHECK (length(issuer) BETWEEN 8 AND 512)
);
CREATE TABLE account_closure.operations (
    operation_id text PRIMARY KEY CHECK (operation_id ~ '^[0-9a-f]{32}$'),
    issuer text NOT NULL,
    subject_id text NOT NULL CHECK (subject_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    account_id text NOT NULL CHECK (account_id ~ '^mfa1_[0-9a-f]{64}$'),
    recovery_digest text NOT NULL CHECK (recovery_digest ~ '^[0-9a-f]{64}$'),
    state text NOT NULL CHECK (state IN ('PREPARED','IN_PROGRESS','RETRYABLE_FAILURE','COMPLETED')),
    code text NOT NULL,
    data_cleaned boolean NOT NULL DEFAULT false,
    identity_deleted boolean NOT NULL DEFAULT false,
    claim text,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    committed_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (issuer, subject_id),
    CHECK (NOT identity_deleted OR data_cleaned),
    CHECK (state <> 'COMPLETED' OR (data_cleaned AND identity_deleted AND completed_at IS NOT NULL)),
    CHECK ((state = 'PREPARED') = (committed_at IS NULL))
);
ALTER TABLE account_closure.settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_closure.settings FORCE ROW LEVEL SECURITY;
ALTER TABLE account_closure.operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_closure.operations FORCE ROW LEVEL SECURITY;
CREATE POLICY closure_owner ON account_closure.settings TO mentorflow_closure USING (true) WITH CHECK (true);
CREATE POLICY closure_owner ON account_closure.operations TO mentorflow_closure USING (true) WITH CHECK (true);

CREATE FUNCTION account_closure.lock_key(p_issuer text, p_subject text) RETURNS bigint
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog
AS $$ SELECT hashtextextended('mentorflow.closure.v1:' || length(p_issuer)::text || ':' || p_issuer || p_subject, 0) $$;

CREATE FUNCTION account_closure.view_operation(o account_closure.operations, acquired boolean DEFAULT false)
RETURNS jsonb LANGUAGE sql VOLATILE SET search_path = pg_catalog
AS $$
SELECT (to_jsonb(o) - 'recovery_digest' - 'claim') || jsonb_build_object(
  'acquired', acquired,
  'state', CASE WHEN o.state = 'IN_PROGRESS' AND o.lease_expires_at <= clock_timestamp()
                THEN 'RETRYABLE_FAILURE' ELSE o.state END,
  'code', CASE WHEN o.state = 'IN_PROGRESS' AND o.lease_expires_at <= clock_timestamp()
                THEN 'CLOSURE_LEASE_EXPIRED' ELSE o.code END)
$$;

-- Rows without an issuer belong to the single canonical issuer provisioned for
-- this database. Conflicting evidence for this subject fails closed, not empty.
CREATE FUNCTION account_closure.scope_resolved(p_issuer text, p_subject text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, account_closure
AS $$
SELECT EXISTS (SELECT 1 FROM account_closure.settings WHERE issuer = p_issuer)
 AND NOT EXISTS (SELECT 1 FROM public.asset_manifests WHERE subject_id = p_subject AND issuer IS DISTINCT FROM p_issuer)
 AND NOT EXISTS (SELECT 1 FROM public.asset_revisions WHERE subject_id = p_subject AND issuer IS DISTINCT FROM p_issuer)
 AND NOT EXISTS (SELECT 1 FROM public.account_import_ledger WHERE subject_id = p_subject AND issuer IS DISTINCT FROM p_issuer)
 AND NOT EXISTS (SELECT 1 FROM public.structured_sync_entities WHERE subject_id = p_subject AND issuer IS DISTINCT FROM p_issuer)
 AND NOT EXISTS (SELECT 1 FROM public.structured_sync_requests WHERE subject_id = p_subject AND issuer IS DISTINCT FROM p_issuer)
 AND NOT EXISTS (SELECT 1 FROM public.rehydration_manifests WHERE subject_id = p_subject AND issuer IS DISTINCT FROM p_issuer)
 AND NOT EXISTS (SELECT 1 FROM enterprise_jobs.jobs
    WHERE actor_context_json->>'subject_id' = p_subject
      AND (actor_context_json->>'issuer' IS DISTINCT FROM p_issuer
           OR actor_context_json->>'actor_kind' IS DISTINCT FROM 'HUMAN'))
 -- Legacy file ingestion accepts paths under a shared uploads root. A path
 -- in a job is not proof of exclusive ownership; retain the attribution until
 -- an explicit managed-file ownership adapter exists (metadata-only is fine).
 AND NOT EXISTS (SELECT 1 FROM enterprise_jobs.jobs
    WHERE actor_context_json->>'issuer'=p_issuer AND actor_context_json->>'subject_id'=p_subject
      AND workflow_kind='DOCUMENT_INGESTION'
      AND (payload_json->'request'->'selected_documents')::jsonb IS NOT NULL
      AND (payload_json->'request'->'selected_documents')::jsonb NOT IN ('null'::jsonb,'[]'::jsonb))
$$;

CREATE FUNCTION account_closure.prepare(p_issuer text, p_subject text, p_account text, p_operation text, p_digest text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, account_closure
AS $$
DECLARE o account_closure.operations;
BEGIN
  IF NOT account_closure.scope_resolved(p_issuer, p_subject) THEN
    RETURN jsonb_build_object('error','CLOSURE_SCOPE_UNRESOLVED');
  END IF;
  PERFORM pg_advisory_xact_lock(account_closure.lock_key(p_issuer, p_subject));
  SELECT * INTO o FROM account_closure.operations WHERE issuer=p_issuer AND subject_id=p_subject FOR UPDATE;
  IF FOUND AND o.state <> 'PREPARED' THEN RETURN jsonb_build_object('error','CLOSURE_ALREADY_COMMITTED'); END IF;
  INSERT INTO account_closure.operations(operation_id,issuer,subject_id,account_id,recovery_digest,state,code)
  VALUES(p_operation,p_issuer,p_subject,p_account,p_digest,'PREPARED','CLOSURE_PREPARED')
  ON CONFLICT (issuer,subject_id) DO UPDATE SET
    operation_id=EXCLUDED.operation_id, recovery_digest=EXCLUDED.recovery_digest,
    created_at=clock_timestamp(),updated_at=clock_timestamp()
  RETURNING * INTO o;
  RETURN account_closure.view_operation(o);
END $$;

CREATE FUNCTION account_closure.status(p_operation text, p_digest text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, account_closure
AS $$
DECLARE o account_closure.operations;
BEGIN
  SELECT * INTO o FROM account_closure.operations WHERE operation_id=p_operation AND recovery_digest=p_digest;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','CLOSURE_CREDENTIAL_INVALID'); END IF;
  RETURN account_closure.view_operation(o);
END $$;

CREATE FUNCTION account_closure.acquire(p_operation text, p_digest text, p_claim text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, account_closure
AS $$
DECLARE o account_closure.operations;
BEGIN
  SELECT * INTO o FROM account_closure.operations WHERE operation_id=p_operation AND recovery_digest=p_digest;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','CLOSURE_CREDENTIAL_INVALID'); END IF;
  PERFORM pg_advisory_xact_lock(account_closure.lock_key(o.issuer,o.subject_id));
  SELECT * INTO o FROM account_closure.operations WHERE operation_id=p_operation AND recovery_digest=p_digest FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','CLOSURE_CREDENTIAL_INVALID'); END IF;
  IF o.state='COMPLETED' OR (o.state='IN_PROGRESS' AND o.lease_expires_at > clock_timestamp()) THEN
    RETURN account_closure.view_operation(o);
  END IF;
  IF p_claim !~ '^[0-9a-f]{32}$' THEN RETURN jsonb_build_object('error','CLOSURE_CLAIM_INVALID'); END IF;
  UPDATE account_closure.operations SET state='IN_PROGRESS',code='CLOSURE_IN_PROGRESS',claim=p_claim,
    lease_expires_at=clock_timestamp()+interval '120 seconds',
    committed_at=COALESCE(committed_at,clock_timestamp()),updated_at=clock_timestamp()
    WHERE operation_id=p_operation RETURNING * INTO o;
  RETURN account_closure.view_operation(o,true);
END $$;

CREATE FUNCTION account_closure.check_open(p_issuer text, p_subject text)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER VOLATILE SET search_path = pg_catalog, account_closure
AS $$
SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM account_closure.settings WHERE issuer=p_issuer)
 THEN jsonb_build_object('error','CLOSURE_IDENTITY_CONFIGURATION_MISMATCH')
 ELSE jsonb_build_object('closed',EXISTS(
  SELECT 1 FROM account_closure.operations
  WHERE issuer=p_issuer AND subject_id=p_subject AND committed_at IS NOT NULL)) END
$$;

CREATE FUNCTION account_closure.assert_open(p_issuer text, p_subject text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, account_closure
AS $$
DECLARE closed boolean;
BEGIN
  IF p_subject IS NULL OR p_subject='' THEN RETURN; END IF;
  -- Existing application writers use READ COMMITTED. Disallow stale MVCC
  -- snapshots bypassing a fence inserted after their transaction began.
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='CLOSURE_WRITE_REQUIRES_READ_COMMITTED';
  END IF;
  PERFORM pg_advisory_xact_lock_shared(account_closure.lock_key(p_issuer,p_subject));
  SELECT committed_at IS NOT NULL INTO closed FROM account_closure.operations
    WHERE issuer=p_issuer AND subject_id=p_subject FOR SHARE;
  IF COALESCE(closed,false) THEN
    RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='ACCOUNT_CLOSURE_IN_PROGRESS';
  END IF;
END $$;

-- Trigger checks both caller identity and row ownership. Shared resource owner
-- references are historical, not authority; another active user may still edit
-- that shared resource after the owner closes their account.
CREATE FUNCTION account_closure.guard_row(p_schema text,p_table text,p_old jsonb,p_new jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, account_closure
AS $$
DECLARE canonical text; r jsonb; a jsonb; subject text; issuer text;
BEGIN
  SELECT settings.issuer INTO STRICT canonical FROM account_closure.settings;
  PERFORM account_closure.assert_open(
    COALESCE(NULLIF(current_setting('mentorflow.actor_issuer',true),''),canonical),
    NULLIF(current_setting('mentorflow.actor_subject',true),''));
  FOREACH r IN ARRAY ARRAY[p_old,p_new] LOOP
    IF r IS NULL THEN CONTINUE; END IF;
    IF p_schema='enterprise_jobs' THEN
      IF p_table='jobs' THEN a := r->'actor_context_json';
      ELSE SELECT actor_context_json::jsonb INTO a FROM enterprise_jobs.jobs WHERE job_id=r->>'job_id';
      END IF;
      IF a IS NOT NULL THEN
        PERFORM account_closure.assert_open(COALESCE(a->>'issuer',canonical), a->>'subject_id');
      END IF;
    ELSIF p_table IN ('asset_manifests','asset_revisions','account_import_ledger','structured_sync_entities','structured_sync_requests','rehydration_manifests','enterprise_provider_sessions','workspace_role_bindings','workspace_memberships','organization_memberships') THEN
      PERFORM account_closure.assert_open(COALESCE(r->>'issuer',canonical),r->>'subject_id');
    ELSIF p_table IN ('documents','evidence','knowledge_maps','learning_plans','teacher_sessions','review_campaigns','student_events','cost_events','resource_scopes') THEN
      IF r->>'scope_type'='PERSONAL' THEN
        PERFORM account_closure.assert_open(canonical,r->>'owner_subject_id');
      END IF;
      IF NULLIF(r->>'job_id','') IS NOT NULL THEN
        SELECT actor_context_json::jsonb INTO a FROM enterprise_jobs.jobs WHERE job_id=r->>'job_id';
        IF a IS NOT NULL THEN PERFORM account_closure.assert_open(COALESCE(a->>'issuer',canonical),a->>'subject_id'); END IF;
      END IF;
    END IF;
  END LOOP;
END $$;

-- SECURITY INVOKER is intentional: the nested deletion function runs as the
-- restricted owner; ordinary application/worker writes must execute the guard.
CREATE FUNCTION account_closure.guard_write() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, account_closure
AS $$
BEGIN
  IF current_user <> 'mentorflow_closure' THEN
    PERFORM account_closure.guard_row(TG_TABLE_SCHEMA,TG_TABLE_NAME,
      CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
      CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END);
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION account_closure.clean(p_operation text,p_digest text,p_claim text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, account_closure
AS $$
DECLARE o account_closure.operations;
BEGIN
  SELECT * INTO o FROM account_closure.operations WHERE operation_id=p_operation AND recovery_digest=p_digest FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','CLOSURE_CREDENTIAL_INVALID'); END IF;
  IF o.state<>'IN_PROGRESS' OR o.claim IS DISTINCT FROM p_claim OR o.lease_expires_at<=clock_timestamp() THEN
    RETURN jsonb_build_object('error','CLOSURE_CLAIM_LOST');
  END IF;
  IF o.data_cleaned THEN RETURN account_closure.view_operation(o,true); END IF;
  IF NOT account_closure.scope_resolved(o.issuer,o.subject_id) THEN
    RETURN jsonb_build_object('error','CLOSURE_SCOPE_UNRESOLVED');
  END IF;
  -- The fence was committed by acquire, after draining all guarded writers.
  -- Every deletion and the data_cleaned checkpoint share this one transaction.
  -- Delete children explicitly before parents: PostgreSQL FK actions can run
  -- as the referencing table owner, which must not bypass our write guard.
  DELETE FROM enterprise_jobs.outbox child USING enterprise_jobs.jobs j
    WHERE child.job_id=j.job_id AND j.actor_context_json->>'issuer'=o.issuer
      AND j.actor_context_json->>'subject_id'=o.subject_id AND j.actor_context_json->>'actor_kind'='HUMAN';
  DELETE FROM enterprise_jobs.dead_letters child USING enterprise_jobs.jobs j
    WHERE child.job_id=j.job_id AND j.actor_context_json->>'issuer'=o.issuer
      AND j.actor_context_json->>'subject_id'=o.subject_id AND j.actor_context_json->>'actor_kind'='HUMAN';
  DELETE FROM enterprise_jobs.jobs WHERE actor_context_json->>'issuer'=o.issuer
    AND actor_context_json->>'subject_id'=o.subject_id AND actor_context_json->>'actor_kind'='HUMAN';
  DELETE FROM public.asset_revisions WHERE issuer=o.issuer AND subject_id=o.subject_id;
  DELETE FROM public.asset_manifests WHERE issuer=o.issuer AND subject_id=o.subject_id;
  DELETE FROM public.account_import_ledger WHERE issuer=o.issuer AND subject_id=o.subject_id;
  DELETE FROM public.structured_sync_entities WHERE issuer=o.issuer AND subject_id=o.subject_id;
  DELETE FROM public.structured_sync_requests WHERE issuer=o.issuer AND subject_id=o.subject_id;
  DELETE FROM public.account_daily_activity WHERE account_scope=encode(sha256(convert_to(o.issuer, 'UTF8') || decode('00', 'hex') || convert_to(o.subject_id, 'UTF8')), 'hex');
  DELETE FROM public.rehydration_manifests WHERE issuer=o.issuer AND subject_id=o.subject_id;
  DELETE FROM public.documents WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';
  DELETE FROM public.evidence WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';
  DELETE FROM public.knowledge_maps WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';
  DELETE FROM public.learning_plans WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';
  DELETE FROM public.teacher_sessions WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';
  DELETE FROM public.review_campaigns WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';
  DELETE FROM public.student_events WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';
  DELETE FROM public.cost_events WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';
  DELETE FROM public.resource_scopes WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL';
  DELETE FROM public.enterprise_provider_sessions WHERE subject_id=o.subject_id;
  DELETE FROM public.workspace_role_bindings WHERE subject_id=o.subject_id;
  DELETE FROM public.workspace_memberships WHERE subject_id=o.subject_id;
  DELETE FROM public.organization_memberships WHERE subject_id=o.subject_id;
  UPDATE account_closure.operations SET data_cleaned=true,updated_at=clock_timestamp()
    WHERE operation_id=p_operation RETURNING * INTO o;
  RETURN account_closure.view_operation(o,true);
END $$;

CREATE FUNCTION account_closure.provider_done(p_operation text,p_digest text,p_claim text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, account_closure
AS $$
DECLARE o account_closure.operations;
BEGIN
  SELECT * INTO o FROM account_closure.operations WHERE operation_id=p_operation AND recovery_digest=p_digest FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','CLOSURE_CREDENTIAL_INVALID'); END IF;
  IF o.state<>'IN_PROGRESS' OR o.claim IS DISTINCT FROM p_claim OR o.lease_expires_at<=clock_timestamp() OR NOT o.data_cleaned THEN
    RETURN jsonb_build_object('error','CLOSURE_CLAIM_LOST');
  END IF;
  UPDATE account_closure.operations SET identity_deleted=true,updated_at=clock_timestamp()
    WHERE operation_id=p_operation RETURNING * INTO o;
  RETURN account_closure.view_operation(o,true);
END $$;

CREATE FUNCTION account_closure.complete(p_operation text,p_digest text,p_claim text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, account_closure
AS $$
DECLARE o account_closure.operations;
BEGIN
  SELECT * INTO o FROM account_closure.operations WHERE operation_id=p_operation AND recovery_digest=p_digest FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','CLOSURE_CREDENTIAL_INVALID'); END IF;
  IF o.state='COMPLETED' THEN RETURN account_closure.view_operation(o); END IF;
  IF o.state<>'IN_PROGRESS' OR o.claim IS DISTINCT FROM p_claim OR o.lease_expires_at<=clock_timestamp()
     OR NOT o.data_cleaned OR NOT o.identity_deleted THEN
    RETURN jsonb_build_object('error','CLOSURE_CLAIM_LOST');
  END IF;
  -- Do not trust checkpoints alone: confirm the full private/membership set is
  -- still empty under the permanent write fence. Shared resources are excluded.
  IF EXISTS (SELECT 1 FROM public.asset_manifests WHERE issuer=o.issuer AND subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM public.asset_revisions WHERE issuer=o.issuer AND subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM public.account_import_ledger WHERE issuer=o.issuer AND subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM public.structured_sync_entities WHERE issuer=o.issuer AND subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM public.structured_sync_requests WHERE issuer=o.issuer AND subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM public.account_daily_activity WHERE account_scope=encode(sha256(convert_to(o.issuer, 'UTF8') || decode('00', 'hex') || convert_to(o.subject_id, 'UTF8')), 'hex'))
     OR EXISTS (SELECT 1 FROM public.rehydration_manifests WHERE issuer=o.issuer AND subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM public.documents WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')
     OR EXISTS (SELECT 1 FROM public.evidence WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')
     OR EXISTS (SELECT 1 FROM public.knowledge_maps WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')
     OR EXISTS (SELECT 1 FROM public.learning_plans WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')
     OR EXISTS (SELECT 1 FROM public.teacher_sessions WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')
     OR EXISTS (SELECT 1 FROM public.review_campaigns WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')
     OR EXISTS (SELECT 1 FROM public.student_events WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')
     OR EXISTS (SELECT 1 FROM public.cost_events WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')
     OR EXISTS (SELECT 1 FROM public.resource_scopes WHERE owner_subject_id=o.subject_id AND scope_type='PERSONAL')
     OR EXISTS (SELECT 1 FROM public.enterprise_provider_sessions WHERE subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM public.workspace_role_bindings WHERE subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM public.workspace_memberships WHERE subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM public.organization_memberships WHERE subject_id=o.subject_id)
     OR EXISTS (SELECT 1 FROM enterprise_jobs.jobs WHERE actor_context_json->>'issuer'=o.issuer AND actor_context_json->>'subject_id'=o.subject_id) THEN
    RETURN jsonb_build_object('error','CLOSURE_DATA_REMAINS');
  END IF;
  UPDATE account_closure.operations SET state='COMPLETED',code='CLOSURE_COMPLETED',
    completed_at=clock_timestamp(),updated_at=clock_timestamp(),claim=NULL,lease_expires_at=NULL
    WHERE operation_id=p_operation RETURNING * INTO o;
  RETURN account_closure.view_operation(o);
END $$;

CREATE FUNCTION account_closure.fail(p_operation text,p_digest text,p_claim text,p_code text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, account_closure
AS $$
DECLARE o account_closure.operations;
BEGIN
  SELECT * INTO o FROM account_closure.operations WHERE operation_id=p_operation AND recovery_digest=p_digest FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','CLOSURE_CREDENTIAL_INVALID'); END IF;
  IF o.state='IN_PROGRESS' AND o.claim=p_claim THEN
    UPDATE account_closure.operations SET state='RETRYABLE_FAILURE',
      code=CASE WHEN p_code IN ('CLOSURE_SCOPE_UNRESOLVED','CLOSURE_SESSION_REVOCATION_PENDING',
        'CLOSURE_PROVIDER_UNAVAILABLE','CLOSURE_PROVIDER_OUTCOME_UNKNOWN','CLOSURE_PROVIDER_RESPONSE_INVALID',
        'CLOSURE_PROVIDER_DELETE_UNCONFIRMED','CLOSURE_DATA_REMAINS','CLOSURE_STORAGE_UNAVAILABLE')
      THEN p_code ELSE 'CLOSURE_RETRY_REQUIRED' END,
      claim=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
      WHERE operation_id=p_operation RETURNING * INTO o;
  END IF;
  RETURN account_closure.view_operation(o);
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA account_closure FROM PUBLIC,mentorflow_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA account_closure FROM PUBLIC;
GRANT USAGE ON SCHEMA account_closure TO mentorflow_app;
GRANT EXECUTE ON FUNCTION account_closure.prepare(text,text,text,text,text),
 account_closure.status(text,text),account_closure.acquire(text,text,text),
 account_closure.clean(text,text,text),account_closure.provider_done(text,text,text),
 account_closure.complete(text,text,text),account_closure.fail(text,text,text,text),
 account_closure.check_open(text,text),account_closure.assert_open(text,text),account_closure.guard_write(),
 account_closure.guard_row(text,text,jsonb,jsonb) TO mentorflow_app;
GRANT EXECUTE ON FUNCTION account_closure.guard_write(),
 account_closure.guard_row(text,text,jsonb,jsonb) TO mentorflow_migrator;

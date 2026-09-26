# MentorFlow Enterprise E1-06 Incident Runbooks

These runbooks are staging procedures, not evidence that a real incident or
restore has been executed. Never paste prompts, documents, student answers,
tokens, authorization headers, database URLs, or secret values into tickets,
logs, dashboards, or chat. Use request/trace/job/organization/workspace IDs and
the canonical `enterprise-context.v1` audit envelope instead.

## 1. PostgreSQL outage

**Trigger:** readiness reports `POSTGRES_UNREACHABLE`, the backend is down, or
database latency/error rate rises abruptly.

1. Declare severity and freeze schema changes, deploys, backups, and destructive
   repair commands. Record UTC start time and trace/request IDs only.
2. Confirm the fault from a staging operator host using connection metadata;
   do not copy the database URL or password into evidence.
3. Check server reachability, storage, connection exhaustion, locks, and recent
   E1-02 migration activity. Preserve database and application logs under the
   incident retention policy.
4. If the primary is intact, recover service through the platform's approved
   restart/failover procedure. If data integrity is uncertain, keep traffic
   stopped and move to the Restore runbook with a disposable target first.
5. Exit only after readiness is green, tenant-scoped smoke checks pass, RLS is
   still forced, audit writes succeed, and ordinary API error/latency metrics
   have recovered. Record the exact database version and verification queries.

## 2. Keycloak outage

**Trigger:** readiness reports `KEYCLOAK_DISCOVERY_UNREACHABLE`, OIDC discovery
or token validation fails, or authentication success falls below its SLO.

1. Stop login-dependent changes; do not bypass issuer, signature, audience,
   expiry, organization, or workspace checks.
2. Check discovery, Keycloak health, its PostgreSQL dependency, certificates,
   DNS, and clock skew from the operator network.
3. Existing sessions may continue only under the frozen authentication policy;
   never convert a trace header into authorization context.
4. If realm or database integrity is suspect, restore both the Keycloak database
   artifact and matching realm export into a disposable environment.
5. Exit after discovery, login, refresh, logout, invalid-token rejection, role
   mapping, organization/workspace binding, and canonical audit emission pass.

## 3. Object-store outage

**Trigger:** readiness reports `OBJECT_STORE_UNREACHABLE`, object reads/writes
fail, or manifest/hash verification reports missing or corrupt artifacts.

1. Quiesce document ingestion and object mutation while leaving unrelated
   read-only product surfaces available where safe.
2. Check endpoint/filesystem reachability, free space, permissions, and storage
   versioning. Never delete a corrupt version during triage.
3. Compare the object manifest, size, SHA-256, and required
   `organizations/<org>/workspaces/<workspace>/...` tenant path.
4. Restore only verified objects into an empty temporary target, compare every
   hash, then use the platform's atomic traffic switch procedure.
5. Exit after cross-tenant path probes, representative document retrieval,
   object counts, and backup-freshness metrics pass.

## 4. Job queue stuck

**Trigger:** `MentorFlowQueueStuck`, queue age exceeds ten minutes, depth grows
without completions, or worker failures spike.

1. Stop new work for the affected queue without deleting queued records.
2. Capture queue, job IDs, age, worker version, and bounded error classes. Do not
   copy job payloads into observability evidence.
3. Check worker health, database/object/provider dependencies, leases, locks,
   retry storms, outbox state, and dead letters supplied by E1-05.
4. Restart or scale workers only after the dependency fault is contained.
   Replay through E1-05's idempotent path; never mutate durable state manually.
5. Exit after depth and age return to normal, a synthetic job completes once,
   duplicate effects are absent, and failure rate stays below alert threshold.

## 5. Provider outage

**Trigger:** `MentorFlowProviderOutage`, Teacher availability falls, or TTFT and
total latency exceed their separate AI thresholds.

1. Identify the bounded provider route and error class from metrics/traces.
   Never inspect or export raw prompts or responses for first-line triage.
2. Check provider status, network/DNS/TLS, credential *presence* (not value),
   quota, rate limits, and route-specific failures.
3. Apply only an approved provider-routing or product-degradation policy. E1-06
   does not authorize silent model failover or weakened safety controls.
4. Keep ordinary API latency/error analysis separate from AI latency.
5. Exit after synthetic Provider calls succeed, Teacher TTFT/total latency and
   availability recover, and token/cost/cache metrics remain content-free.

## 6. Secret exposure

**Trigger:** a secret, API key, bearer token, authorization header, credential,
raw prompt, document text, or student answer appears in telemetry or evidence.

1. Treat the value as compromised. Restrict the artifact, stop forwarding, and
   preserve access metadata without copying the exposed value again.
2. Identify secret type, owner, affected systems, time window, and viewers.
   Notify the security owner and privacy owner through approved channels.
3. Revoke/rotate the credential, invalidate affected sessions, and restart only
   the components that need the new secret. Do not commit replacement secrets.
4. Purge or quarantine telemetry according to retention/legal policy; keep an
   audit trail of artifact IDs and actions, never the secret itself.
5. Fix the field boundary and add a regression fixture. Exit after the automated
   trace/log scanner passes and old credentials are demonstrably rejected.

## 7. Cross-tenant incident

**Trigger:** `MentorFlowTenantIsolationAnomaly`, a tenant-mismatch denial, an RLS
denial outside an approved adversarial test, or suspected cross-tenant access.

1. Declare a security incident, stop affected writes, preserve audit/database
   evidence, and do not run broad repair or cleanup commands.
2. Record canonical actor, organization, workspace, resource, request, and trace
   identifiers. Do not include tenant content.
3. Validate issuer/token binding, authorization decision, transaction-local
   tenant context, forced RLS, database role, object tenant path, cache keys, and
   job ownership. Assume exposure until disproved.
4. Revoke affected sessions and credentials; isolate the impacted service or
   tenant according to the security lead's decision. Do not disable RLS.
5. Exit only after E1-02/E1-03 adversarial probes pass across API, database,
   object, cache, audit, and job boundaries and required notifications finish.

## 8. Restore and disaster recovery

**Trigger:** integrity loss, unrecoverable service failure, or an approved drill.

1. Obtain incident commander approval and choose a verified backup ID. Record
   its manifest hash, age, application/schema version, and exact tool versions.
2. Create an empty disposable target. Never restore over a non-empty database,
   object tree, or realm target.
3. Run manifest verification, then restore MentorFlow PostgreSQL, Keycloak
   PostgreSQL, tenant-scoped objects, and the matching realm export. Any hash,
   path, command, or target check failure stops the drill.
4. Compare organizations, members, workspaces, documents, plans, student state,
   audit events, jobs, realm/federated-user counts, object counts, sizes, and
   SHA-256 values against the synthetic seed receipt.
5. Run authentication, authorization/RLS, document, Teacher, review, plan, job,
   audit, health, metrics, trace, and PERSONAL_LOCAL regression checks.
6. Switch traffic only with incident commander approval. Keep the failed target
   intact for forensics and document RPO/RTO measured from observed timestamps.
7. Exit only when every comparison passes and no `PENDING_STAGING` gate remains;
   otherwise roll back traffic and retain the previous service target.

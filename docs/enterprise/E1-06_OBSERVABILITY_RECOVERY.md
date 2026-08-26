# E1-06 Observability and Recovery Boundary

## Delivery scope

E1-06 supplies provider-safe telemetry, W3C trace correlation, a canonical
enterprise audit adapter, dependency-aware health/SLO reads, and an atomic
backup/restore command boundary. It does not own tenant persistence,
authorization policy, durable jobs, migrations, dependency manifests, or an
OpenTelemetry Collector process.

The shared identity and audit contract is exactly `enterprise-context.v1` at
commit `fa488048d2856d687854b50a23137a45bb020d7d`. E1-06 imports
`AuditEnvelope` and `TenantMode` from `app.enterprise.contracts_v1`; it defines
no second `ActorContext`, `TenantContext`, `ResourceScope`, `AccessDecision`,
or `AuditEnvelope`.

## Trace and Provider evidence

- Every integrated FastAPI request accepts a valid W3C `traceparent`, creates
  a new server span ID, and returns `traceparent`, `X-Trace-ID`, and
  `X-Request-ID`.
- Logs contain only the fixed correlation vocabulary. Query strings, request
  bodies, authorization headers, prompts, document text, student answers,
  email addresses, API keys, and arbitrary metadata are never accepted.
- Provider receipts export bounded labels for route, result, cache outcome,
  tokens, latency, and available cost classification. Provider model names and
  prompt/response content are not metric labels.
- When an OpenTelemetry dependency lease is eventually granted,
  `MENTORFLOW_OTEL_ENABLED=true` sends FastAPI, HTTPX, SQLAlchemy, business
  spans, and SDK metrics to the externally managed endpoint in
  `OTEL_EXPORTER_OTLP_ENDPOINT`. E1-06 never starts or embeds a Collector.
- With the current unleased SDK, OTel stays disabled by default and fails
  startup closed if explicitly enabled. No Python or JavaScript dependency
  manifest is changed by this delivery.

## Canonical audit boundary

`EnterpriseAuditBoundary` accepts only the frozen `AuditEnvelope`.

- `ENTERPRISE_SERVER` requires an audit sink and rejects a missing or failed
  sink.
- `PERSONAL_LOCAL` keeps audit optional and does not acquire a login,
  organization, PostgreSQL, or enterprise-audit requirement.
- Request and trace IDs must match the active correlation context.
- Audit metadata remains flat and scalar. E1-06 additionally rejects content-
  bearing keys, bearer values, and email-shaped PII before persistence.
- `JsonlAuditSink` writes canonical envelope JSON with flush and `fsync`; it is
  a local adapter and not a replacement for the E1-03 audit store.

## Backup and restore contract

`app.observability.recovery` owns orchestration only. PostgreSQL and Keycloak
database operations are external executable contracts:

```text
pg_dump --format=custom --no-owner --no-privileges ... --file <artifact> <database>
pg_restore --clean --if-exists --exit-on-error --no-owner --no-privileges ... <artifact>
```

Passwords are read from named environment variables and passed as
`PGPASSWORD`; they never appear in arguments, manifests, receipts, or logs.
Stdout and stderr from real tools are discarded so a database diagnostic
cannot leak a secret into operational evidence.

A backup becomes ready only after both database dumps, the versioned object
tree, the Keycloak realm export, per-file size/SHA-256 evidence, and the
manifest are complete. The temporary directory is renamed on the same
filesystem. A failed attempt retains its non-ready `.tmp` directory for
forensics and never publishes the final backup ID.

Restore verifies every artifact before invoking either database restore. It
rejects corrupted or missing files, non-empty targets, symlinks, overlapping
source/destination roots, and overlapping restore targets. Object storage and
the Keycloak realm restore through temporary paths before publication.

## Focused synthetic drill

The focused test creates synthetic organization, user, workspace, resource,
audit-event, job-state, object, and Keycloak realm records. It then:

1. creates both external PostgreSQL dump artifacts through a deterministic CLI
   contract harness;
2. copies the real filesystem object tree and realm export into the atomic
   backup;
3. deletes the disposable source object tree and realm export;
4. restores both database artifacts through the CLI contract harness;
5. restores the actual object tree and realm file;
6. verifies entity counts, restored JSON, artifact count, size, and SHA-256.

This proves E1-06 orchestration and filesystem recovery. It is not a live
PostgreSQL or Keycloak acceptance claim. A live `pg_dump`/`pg_restore` drill
remains unavailable until E1-02 provides the canonical schema/database and the
host provides leased PostgreSQL tools.

## Failure recovery runbook

1. **Provider outage:** inspect `mentorflow_provider_calls_total`, Teacher TTFT
   and total-latency histograms, trace IDs, and the bounded error class. Do not
   log or copy the prompt/response. Preserve the current provider route; model
   failover is outside E1-06.
2. **Trace export outage:** application requests continue only when OTel is
   disabled or the explicitly configured fail-open flag permits it. The
   in-process bounded metrics and correlation headers remain available.
3. **Audit sink outage:** stop the Enterprise operation with
   `AUDIT_SINK_REQUIRED` or `AUDIT_SINK_UNAVAILABLE`. Personal Local remains
   usable because the frozen mode boundary makes enterprise audit optional.
4. **Backup command failure:** treat the `.tmp` directory as incomplete
   forensic evidence. Do not rename it, reuse its ID, or report backup
   freshness success. Correct the external dependency and use a new backup ID.
5. **Artifact corruption:** `ARTIFACT_HASH_MISMATCH` blocks all restore actions.
   Preserve the backup, investigate storage integrity, and select another
   verified version.
6. **Restore:** restore only into an empty disposable environment first. Verify
   organizations, users, workspaces, resources, audit events, job state,
   objects, realm configuration, and hashes before any traffic switch.
7. **Worker backlog:** queue depth/oldest-age/failure metrics are available,
   but durable replay, outbox, and dead-letter ownership belongs to E1-05.

## Pending dependencies and forbidden claims

- E1-02: canonical PostgreSQL schema, RLS, live database and tool lease.
- E1-03: authorization decisions and authoritative audit persistence.
- E1-05: durable jobs, outbox, dead letters and replay.
- External staging: PostgreSQL tools, Keycloak, object store and OTel Collector.

Until those dependencies are delivered and a real disposable staging drill is
run, this work is not `ENTERPRISE_READY`, `PRODUCTION_READY`, or a compliance
claim.

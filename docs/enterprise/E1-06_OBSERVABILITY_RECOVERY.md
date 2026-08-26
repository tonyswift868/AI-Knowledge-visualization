# E1-06 Observability and Recovery Boundary

## Delivery scope

E1-06 supplies provider-safe telemetry, W3C trace correlation, a canonical
enterprise audit adapter, dependency-aware health/SLO reads, an atomic
backup/restore command boundary, and versioned configuration for an externally
run Collector/Prometheus/Tempo/Loki/Grafana staging stack. It does not own
tenant persistence, authorization policy, durable jobs, migrations, dependency
manifests, or the lifecycle of any observability container.

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
- Provider completion also emits a bounded correlated business span and JSON
  event containing only trace/request/optional tenant IDs, feature, route, and
  outcome.
- `observe_business_operation` is the explicit database, Review, Plan, and
  Provider business-span seam. HTTP, SQLAlchemy, HTTPX, background jobs,
  Document processing, and Teacher paths have direct integration seams.
- Every business span merges the bounded correlation context: trace, request,
  optional job/organization/workspace, feature, and Provider route. Span
  attributes have an exact allowlist; arbitrary attributes are rejected.
- When an OpenTelemetry dependency lease is eventually granted,
  `MENTORFLOW_OTEL_ENABLED=true` sends FastAPI, HTTPX, SQLAlchemy, business
  spans, and SDK metrics to the externally managed endpoint in
  `OTEL_EXPORTER_OTLP_ENDPOINT`. E1-06 never starts or embeds a Collector.
- With the current unleased SDK, OTel stays disabled by default and fails
  startup closed if explicitly enabled. No Python or JavaScript dependency
  manifest is changed by this delivery.

## Metric, SLO, error-budget, and health catalogue

The bounded Prometheus catalogue covers HTTP request/error/latency, database
pool state, job depth/age/failure, Document throughput/failure/duration,
Teacher TTFT and total latency, Provider calls/tokens/cost, cache outcomes,
authorization and RLS denials, authentication outcomes, and backup age. Metric
labels exclude user, document, prompt, answer, model, and arbitrary resource
identifiers.

Staging SLOs are versioned for ordinary API availability, ordinary API latency
(500 ms threshold), authentication success, background-job success, Document
completion, Teacher availability, and backup freshness. AI availability/TTFT/
total latency remain separate from ordinary API latency. `SloWindow` provides
the rolling interval and `ErrorBudgetReadModel` stores only timestamps and
success bits for future dashboards.

Liveness is process-only. Startup reflects completed application bootstrap.
Readiness checks Postgres, OIDC discovery, object storage, and worker/job
dependencies; they become required in staging/production and remain optional in
Personal Local.

## External Collector, dashboards, and alerts

`ops/observability/docker-compose.staging.yml` is a pinned, external-container
target. The Collector accepts OTLP gRPC/HTTP and sends traces to Tempo, logs to
Loki, and metrics to a Prometheus exporter. Prometheus, Grafana provisioning,
the seven System/Identity/Tenant Security/Teacher-AI/Jobs/Documents/Cost-Cache
dashboard sections, and eight required alert rules are versioned beside it.

The application never invokes Docker or supervises the stack. Worker 06 has no
Docker/Linux staging runtime, so syntax/content is offline verified only.
Container startup, OTLP ingestion, datasource queries, dashboard provisioning,
and alert evaluation are `STAGING_VERIFICATION_PENDING`.

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

Every object artifact must be under
`organizations/<organization_id>/workspaces/<workspace_id>/...`; the manifest
records this tenant path beside size and SHA-256, and restore rejects a missing
or mismatched tenant path before any database command.

Restore verifies every artifact before invoking either database restore. It
rejects corrupted or missing files, non-empty targets, symlinks, overlapping
source/destination roots, and overlapping restore targets. Object storage and
the Keycloak realm restore through temporary paths before publication.

## Focused synthetic drill

The focused test creates synthetic organizations, members, workspaces,
documents, plans, student state, audit events, jobs, tenant-scoped objects, and
Keycloak realm/federated-user records. It then:

1. creates both external PostgreSQL dump artifacts through a deterministic CLI
   contract harness;
2. copies the real filesystem object tree and realm export into the atomic
   backup;
3. deletes the disposable source object tree and realm export;
4. restores both database artifacts through the CLI contract harness;
5. restores the actual object tree and realm file;
6. verifies entity counts, restored JSON, artifact count, size, and SHA-256.

The same contracts are exposed as Linux-staging commands through
`python -m app.observability.recovery_cli` for `backup`, `verify`, and `restore`.
Plans contain environment-variable names, never password values.

This proves E1-06 orchestration and filesystem recovery. It is not a live
PostgreSQL or Keycloak acceptance claim. E1-02 is now merged, but a live
`pg_dump`/`pg_restore` drill remains unavailable until a disposable staging
database and leased PostgreSQL tools are present on the Linux host.

## Focused failure notes

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

The complete operator procedures for PostgreSQL outage, Keycloak outage,
object-store outage, job queue stuck, Provider outage, secret exposure,
cross-tenant incident, and restore are versioned in
`docs/enterprise/runbooks/E1-06_INCIDENT_RUNBOOKS.md`.

## Automated PII and secret safety

Structured logs and span attributes use independent exact allowlists.
`scan_telemetry_text` rejects serialized Authorization/API-key/raw-prompt/
student-answer/document-text patterns, bearer values, JSON key forms, and
common API-key prefixes. Focused tests scan
emitted request logs, reject forbidden log/span fields, and confirm metrics omit
model, prompt-version, answer, and content values.

## Requirement status

| Goal area | Status | Evidence |
| --- | --- | --- |
| M1 OTel foundation | IMPLEMENTED / OFFLINE_VERIFIED | Dynamic SDK seam, direct HTTP/job/Document/Teacher/Provider integration, database/Review/Plan/Provider business seam |
| M2 correlation | IMPLEMENTED / OFFLINE_VERIFIED | Bounded context merged into logs and business spans |
| M3 frontend propagation | IMPLEMENTED / OFFLINE_VERIFIED | W3C trace/request headers only; no tenant/auth headers |
| M4 metrics | IMPLEMENTED / OFFLINE_VERIFIED | Bounded catalogue and Prometheus rendering |
| M5-M6 SLO/error budget | IMPLEMENTED / OFFLINE_VERIFIED | `SloWindow`, targets, read model, API |
| M7 health | IMPLEMENTED / OFFLINE_VERIFIED | live/startup/ready plus four dependency probes |
| M8 Collector | IMPLEMENTED_CONFIG / STAGING_VERIFICATION_PENDING | External Compose and Collector pipelines |
| M9 dashboards | IMPLEMENTED_CONFIG / STAGING_VERIFICATION_PENDING | Versioned Grafana provisioning and seven sections |
| M10 alerts | IMPLEMENTED_CONFIG / STAGING_VERIFICATION_PENDING | Eight Prometheus rules |
| M11 PostgreSQL tooling | IMPLEMENTED / REAL_BACKUP_EXECUTION_PENDING_STAGING | External `pg_dump`/`pg_restore`, CLI, atomic manifest |
| M12 restore harness | OFFLINE_VERIFIED / STAGING_VERIFICATION_PENDING | Synthetic entity comparison and real filesystem destroy/restore |
| M13 object backup | IMPLEMENTED / OFFLINE_VERIFIED | Tenant path, manifest, size and SHA-256 |
| M14 runbooks | IMPLEMENTED / OFFLINE_VERIFIED | Eight operator runbooks |
| M15 PII/secret safety | IMPLEMENTED / OFFLINE_VERIFIED | Exact allowlists plus serialized telemetry scanner |

## Integrated dependencies and pending staging acceptance

- E1-02 Delivery Fence feature `b846ad3c5817f432fb5c815ebe0a66048cb5c065`
  is merged for canonical PostgreSQL schema and RLS contracts.
- E1-03 Delivery Fence feature `2af634f1fa81575be63e4fa42635b454a57c2c2b`
  is merged for authorization and authoritative audit contracts.
- E1-05 Delivery Fence feature `258200f84e9a0f4996b3715e62abaf007bd818ba`
  is merged for durable jobs, outbox, dead letters, and replay contracts.
- External staging still must supply PostgreSQL tools/targets, Keycloak, object
  storage, Docker Compose, and a running OTel Collector/backend stack.

Until those dependencies are delivered and a real disposable staging drill is
run, this work is not `ENTERPRISE_READY`, `PRODUCTION_READY`, or a compliance
claim.

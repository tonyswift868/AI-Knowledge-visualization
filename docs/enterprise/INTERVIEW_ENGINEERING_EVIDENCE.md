# Enterprise engineering interview evidence

Status: **PARTIAL**. This is an evidence index, not marketing copy. Real Linux staging, PostgreSQL RLS, k6 benchmarks, scanners, backup/restore, and promotion/rollback are still pending.

## What problem we solved

MentorFlow had a large local application surface but needed a traceable enterprise verification/release seam: supported CI matrices, fast/full lanes, frozen shared contracts, mature security scanners, SBOM/dependency risk, multi-tenant adversarial load, three images, rollback, and evidence that does not turn missing execution into a pass.

Evidence: GitHub workflows, `contracts/enterprise/`, `tests/load/`, machine reports, and the exact-commit Delivery Fence.

## Architecture decisions and trade-offs

Three OCI images separate the backend API, static frontend, and higher-risk document worker. The worker stays offline/non-root with a read-only config seam. This adds image and release coordination but narrows runtime privileges and makes SBOM/scan evidence attributable per target.

The enterprise Actor/Tenant contracts are frozen upstream as `enterprise-context.v1`; E1-07B consumes their hash and does not create a competing model. This prevents local CI work from silently redefining authorization semantics, at the cost of pending integrated tests until owner deliveries arrive.

## Why PostgreSQL RLS

Application authorization alone can regress or omit a predicate. PostgreSQL RLS is planned as a second tenant boundary for data access, with application context and negative tests above it. The database lease and integrated RLS tests have not run here, so this is an architectural decision and configured gate—not accepted behavior.

## Why OIDC / Keycloak

OIDC centralizes issuer, key rotation, session/claim policy, and enterprise identity integration instead of inventing password/session cryptography in MentorFlow. Keycloak is the planned controllable staging IdP seam. E1-04 owner evidence for token/session/role-negative behavior is pending.

## Why OpenTelemetry

Metrics, logs, and traces need correlated request/job/tenant-safe metadata without coupling the application to one backend. OpenTelemetry is the planned vendor-neutral seam. Exporter configuration, dashboards, paging, retention, and redaction evidence remain E1-03 owner work.

## Why durable workflows

Document and AI operations cross process/runtime boundaries and can be retried. Durable attempt state, idempotency keys, checkpoints, cancellation fencing, and audit receipts reduce duplicate writes/cost and support crash recovery. Local durable execution tests exist; integrated queue/redelivery evidence is pending E1-05.

## Why k6

k6 makes failure rate, p95 categories, custom document completion, provider-zero, and unauthorized-success thresholds executable. The corpus uses three organizations, 21 synthetic users, multiple workspaces, and foreign Document/Map/Plan/Progress/Review/Cost/Audit identifiers. k6 is unavailable locally, so only syntax/static corpus evidence exists and `REAL_K6 = NOT_RUN`.

## Threats found

The formal model identifies cross-tenant read/write, IDOR, forged tenant context, role/token/session attacks, provider secret exposure, audit tampering, cache leakage, malicious documents/path traversal, prompt injection, job replay, backup exposure, and supply-chain compromise. Each maps a MentorFlow module/control/test/residual risk/owner.

## Performance gates

Global HTTP failures must stay below 1%; ordinary API, AI Teacher, and tenant-isolation p95 are separate. Document processing uses `document_job_completion_ms`, not request duration. Current numeric limits are provisional with `BASELINE_REQUIRED`; the first real Singapore staging run must retain distributions and receive Architect approval before thresholds are frozen.

## Failure and recovery cases

Offline tests inject candidate health failure and prove the release state machine restores promotion tags, deploys retained images, rechecks health, and runs post-rollback smoke. Archive/migration tests cover tamper, expansion ratio, traversal, links, and atomic publication. Real registry/Compose/migration/rollback and backup restoration remain pending.

## What I can truthfully claim now

I designed and offline-verified the enterprise CI/security/load/release contracts; froze exact evidence; kept provider calls at zero; and made unavailable infrastructure visible as `NOT_RUN`/`PENDING` rather than a green score.

I cannot yet truthfully say “I ran a real Enterprise Staging, RLS, multi-tenant load, scanners, observability, recovery, and production-like release.” That statement becomes supportable only after the pending owner and staging artifacts are retained in a later readiness snapshot and release manifest.

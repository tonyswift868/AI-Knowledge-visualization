# Observability and SLO evidence

Status: **PLANNED / PENDING_OWNER_EVIDENCE**.

The load gate requires global HTTP failure rate below 1% and separate p95 thresholds for AI Teacher, ordinary API categories, and tenant isolation. Asynchronous document completion uses `document_job_completion_ms`; it is not represented by one HTTP duration. Unauthorized successes and real provider calls must both equal zero. Current numeric values are provisional under `BASELINE_REQUIRED`, not measured SLO attainment.

Staging must export request rate, error rate, duration by route category, queue depth/age, migration outcome, document-worker restarts, tenant-denial count, provider-call count, and cost/capacity telemetry without secrets or raw confidential payloads. Alert routing, OpenTelemetry exporter/dashboard configuration, trace sampling, log retention, error budgets, and an observed multi-window SLO report depend on E1-03/E1-06 and remain `PENDING_OWNER_EVIDENCE`.

# Enterprise load-test report

Status: **OFFLINE_VERIFIED / STAGING_EXECUTION_PENDING**. `REAL_K6 = NOT_RUN`; k6 was unavailable and no performance result is claimed.

The committed `tests/load/` pack has separate smoke, average, stress, spike, soak, and tenant-isolation entry points. Shared scenarios exercise login/session, organization/workspace list, Knowledge Map, Plan, Student Progress, Review, Cost/Cache, Audit, document submit/status, and controlled AI Teacher. Provider mode must be fake and the `real_provider_calls` threshold is zero.

Thresholds are executable: global `http_req_failed < 1%`, `checks > 99%`, category-specific ordinary API p95, a separate AI Teacher p95, tenant-isolation p95, `unauthorized_success_count == 0`, and `document_job_completion_ms` for asynchronous completion. HTTP submit/status duration is never presented as the entire document processing latency.

All current p95 values are provisional under `BASELINE_REQUIRED`. The first real DigitalOcean/Linux staging run must retain raw k6 summary/JSON, runner and source/image identity, dataset size, host/runtime telemetry, and timestamps. Architect approval is required before production thresholds are frozen.

No `LOAD_TEST_PASS`, `LINUX_STAGING_PASS`, or capacity claim is made. The machine report remains `STAGING_EXECUTION_PENDING` until k6 actually runs.

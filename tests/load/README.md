# MentorFlow enterprise k6 scenarios

Status: `OFFLINE_VERIFIED`; `REAL_K6 = NOT_RUN`.

Run one entry point from the repository root, for example `k6 run tests/load/smoke/scenario.js`. The six profiles are smoke, average, stress, spike, soak, and tenant-isolation. All use the shared synthetic three-organization/21-user corpus and require credentials only through the `LOAD_USERS_JSON` CI secret. No credentials are committed.

`BASELINE_REQUIRED` is deliberate: initial p95 values are executable safety gates, not a Linux staging benchmark. The first real staging run must retain raw metrics, obtain Architect approval, and then freeze reviewed thresholds. AI Teacher has its own category; document completion uses `document_job_completion_ms` instead of treating one HTTP request as the whole asynchronous job.

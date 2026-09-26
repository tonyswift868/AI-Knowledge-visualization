# Cost and capacity report

Status: **PENDING_OWNER_EVIDENCE / STAGING_EXECUTION_PENDING**; no measured capacity or production cost is claimed.

Capacity evidence will combine k6 request rates and p95 by category with backend CPU/memory, frontend transfer, database utilization, document queue depth, worker service time, and storage growth. Cost evidence will use the integrated E1-06 telemetry and the formula `requests × unit cost + compute time × rate + storage × rate`; no unit price is assumed in this repository.

Required decision points are average sustainable load, stress knee, spike recovery, soak drift, concurrency limit, queue backpressure, monthly scenario bands, alert thresholds, and scale/rollback triggers. Fake-provider runs isolate application capacity but cannot predict live provider latency or billing. E1-06 must supply the cost-ledger/telemetry evidence; those fields remain unknown until separately authorized evidence exists.

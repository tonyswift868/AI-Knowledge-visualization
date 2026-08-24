# Knowledge Map Provider Cache Cold-Start Note

Date: 2026-08-24

Scope: read-only analysis of the current Knowledge Map extraction scheduler. This note does not change batching, concurrency, retries, Provider policy, or business semantics.

## Current fan-out

`backend/app/generic_knowledge_api.py::_provider_nodes` slices source chunks into the existing `PROVIDER_BATCH_SIZE` batches, creates one `asyncio` task for every batch, and awaits them together with `asyncio.gather`. A semaphore bounds simultaneous Provider calls to `PROVIDER_BATCH_CONCURRENCY`, but all batch tasks are submitted before any Provider response completes.

Therefore the first semaphore-sized wave can reach the Provider with the same stable prefix before any sibling request has completed and made that prefix reusable in a Provider-side cache.

## Possible first-wave cache miss

Yes. Provider cache behavior is external and must be measured, but this scheduling shape permits a common cold-start miss across the first wave. Later batches may reuse a prefix only if the Provider has committed a compatible cache unit by then. No hit-rate claim can be made without the AI-01 receipts from real requests.

## Seed-first trade-off

A possible benchmark variant is:

1. submit one representative extraction batch;
2. wait for its validated Provider response;
3. submit the remaining batches with the current bounded concurrency.

For `N` batches, concurrency `C`, and representative request latency `L`, the scheduling-only comparison is roughly current `ceil(N / C) * L` versus seed-first `L + ceil((N - 1) / C) * L`. The idealized delta is therefore `(1 + ceil((N - 1) / C) - ceil(N / C)) * L`: zero for some batch counts and one seed latency for others. Real latency varies by batch size, queueing, retry, and Provider cache behavior, so this is not a performance forecast.

## Recommendation

Run a separate, explicitly authorized small real-Provider benchmark after the stable prompt builder is integrated. Compare current fan-out and seed-first using the same sources, prompt/schema versions, model, and batch boundaries. Record wall-clock latency plus AI-01 prompt-cache hit/miss tokens for every request. Do not change production scheduling until the benchmark shows a material cache benefit that justifies the added seed latency.

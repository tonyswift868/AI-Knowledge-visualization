# Local AI Result Cache Existing Audit

Audit date: 2026-08-24
Scope: AI-03 bounded inspection; no Planning hotspot changes.

## existing_location

- `backend/app/generic_knowledge_api.py` owns the current Planning-only cache helpers.
- `backend/app/orm.py` defines `PlanningAssistantCacheORM`; migration `0025` created its persistence boundary.
- Provider interpretation, candidate review, and public search use related but distinct cache identities, validation rules, TTLs, and authorization semantics.

## existing_scope

- The existing cache is an internal Planning optimization, not a general AI result cache contract.
- It persists Planning request interpretations/search/review artifacts in the application database.
- It includes bounded TTL normalization, schema/policy/provider/request fingerprints, validated payload checks, fail-open reads/writes, hit accounting, and process-local in-flight request sharing.

## reusable_primitives

- Canonical SHA-256 identities built from versioned inputs.
- Explicit TTL and schema validation before reuse.
- Validated-only persistence and fail-open corruption behavior.
- Process-local same-key singleflight as an optional latency/cost guard.
- Telemetry that distinguishes a saved Provider call from Provider prompt-cache usage.

AI-03 reuses these patterns, not the private Planning functions or ORM row.

## why_not_global

- Planning cache keys encode Planning-specific authorization, selected resources, search rounds, candidate review, and provider interpretation contracts.
- Its database schema and invalidation semantics are coupled to Planning behavior and are unsuitable for unrelated deterministic AI artifacts.
- Extracting it would modify a shared hotspot and risk changing current Planning behavior.
- The general foundation therefore uses a separate, rebuildable disk cache under `<user-data>/cache/ai-results`, requires explicit opt-in, and excludes conversational/free-chat tasks by policy.
- This cache is disposable and excluded from portable migration/backup by the current cache/runtime-cache exclusion rules. No database migration is required.

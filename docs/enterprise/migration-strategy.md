# Enterprise migration strategy

## Revisions

- `0029_enterprise_tenancy_rls` is the immutable integrated foundation.
- `0030_enterprise_jobs_v1` is the immutable integrated durable-jobs revision.
- `0031_enterprise_tenancy_completion` is an additive upgrade that creates
  `workspace_memberships`, makes workspace roles membership-backed, renames
  `plans` to `learning_plans`, updates the actor GUC, recreates policies and
  supplies the complete synthetic resource seed.

Fresh databases run the complete Alembic chain. Existing `0029` databases run
`0030` and then `0031`; existing `0030_enterprise_jobs_v1` databases run only
`0031`. No integrated migration is edited in place.

## Commands

Fresh/upgrade without fixtures:

```bash
cd backend
alembic upgrade head
```

Fresh staging database with deterministic fixtures:

```bash
cd backend
alembic -x seed_synthetic=true upgrade head
```

Version detection uses `alembic_version`; application code exposes
`detect_enterprise_schema_version()` and expects
`0031_enterprise_tenancy_completion` at head.

Downgrade the completion revision:

```bash
cd backend
alembic downgrade 0030_enterprise_jobs_v1
```

The old role schema can represent only one role per workspace member. The
downgrade fails closed when multiple roles exist; operators must consolidate
roles explicitly before retrying. Downgrade then restores `plans`, the former
role foreign keys and the former actor-setting policy.

Full removal of the enterprise foundation is available through downgrade to
`0028_video_collection_v1`. Production rollback should prefer restoring a
verified backup and rolling application code back together with schema. Never
drop tenant tables as an ad-hoc rollback.

## Staging package

`enterprise_e1/staging/e1_02` targets Ubuntu 24.04 and PostgreSQL 16. It
requires an immutable backend image containing a separately leased PostgreSQL
driver; it does not install or declare one. The package runs migration, seed,
default-deny/negative RLS checks, query-plan capture and one-slot pool reuse.

No real PostgreSQL execution occurred on this worker. Until the package passes
on Linux staging, `REAL_POSTGRES_RLS_GATE` remains `PENDING_STAGING`.

E1-02B adds no migration revision. Its dual-mode repository, transaction,
cache, object-reference, and job-context integration continues to use the
single `0031_enterprise_tenancy_completion` head. The runnable fresh,
incremental, downgrade-to-`0030_enterprise_jobs_v1`, existing-row, RLS-negative,
and one-slot pool-reuse gate is documented in `postgres-staging-gate.md`; its
real PostgreSQL status remains `PENDING_STAGING` until the driver lease and
Ubuntu staging host are available.

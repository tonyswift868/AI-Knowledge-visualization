# PostgreSQL staging gate

`REAL_POSTGRES_GATE = PENDING_STAGING`

The runnable package is `enterprise_e1/staging/postgres/`. It targets Ubuntu,
Docker Compose, PostgreSQL 16, and an immutable backend image supplied after the
PostgreSQL driver lease is granted. This delivery does not modify dependency or
lock files and does not install a driver in the repository.

## Required invocation

```bash
export MENTORFLOW_BACKEND_IMAGE='registry.example/mentorflow-backend@sha256:<digest>'
cd enterprise_e1/staging/postgres
./run.sh
```

The package performs, in order:

1. PostgreSQL bootstrap and three isolated databases.
2. Fresh upgrade to the single `0031_enterprise_tenancy_completion` head.
3. Incremental upgrade from `0030_enterprise_jobs_v1` with existing-row
   preservation.
4. Downgrade exactly to `0030_enterprise_jobs_v1`, then replay to head.
5. Seed of 3 organizations, 6 workspaces, 20 users, and 8 domains.
6. RLS-negative and application ownership tests with unauthorized successes 0.
7. One-slot physical connection reuse from tenant A to tenant B.
8. Volume and orphan cleanup unless `E1_02B_KEEP_STAGING=1`.

The gate is successful only if the final output contains all of:

```text
MIGRATION_GATE=PASS FRESH=PASS INCREMENTAL=PASS DOWNGRADE_0030=PASS EXISTING_ROW_PRESERVED=PASS
UNAUTHORIZED_SUCCESS_EXPECTED=0 UNAUTHORIZED_SUCCESS=0 OPERATIONS=10 RESOURCE_KINDS=8
CONNECTION_REUSE=PASS
E1_02B_POSTGRES_STAGING=PASS
```

## Current limitation

No real PostgreSQL instance or leased driver is available in this worker. The
scripts and offline assertions are inspectable, but their live outputs are not
claimed. Integration must keep `REAL_POSTGRES_GATE = PENDING_STAGING` until the
staging package actually completes on the required host.

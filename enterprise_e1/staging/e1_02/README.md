# E1-02 PostgreSQL staging gate

This package is the real PostgreSQL acceptance gate for Ubuntu 24.04. It is
not executed on the Windows worker because Docker, PostgreSQL, and a leased
Python PostgreSQL driver are unavailable there.

Prerequisites:

- Docker Engine with Compose v2;
- a Linux MentorFlow backend image containing the Integration tree and the
  separately approved PostgreSQL driver;
- `MENTORFLOW_BACKEND_IMAGE` set to that immutable image reference.

Run:

```bash
export MENTORFLOW_BACKEND_IMAGE=registry.example/mentorflow-backend@sha256:<digest>
./enterprise_e1/staging/e1_02/run.sh
```

The gate creates PostgreSQL 16, runs `alembic -x seed_synthetic=true upgrade
head`, executes default-deny and 80-operation isolation probes through
`mentorflow_app`, captures real `EXPLAIN` plans, and reuses one physical DBAPI
connection across tenants. Any unauthorized success exits non-zero.

The expected linear migration tail is `0030_enterprise_jobs_v1` followed by
the single head `0031_enterprise_tenancy_completion`.

The committed package installs no driver and changes no dependency manifest.
Until this package runs on staging, `REAL_POSTGRES_RLS_GATE` remains
`PENDING_STAGING`.

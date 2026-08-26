# E1-02B PostgreSQL staging gate

`REAL_POSTGRES_GATE = PENDING_STAGING`

This package is ready for an Ubuntu host with Docker Compose and a PostgreSQL
16-capable backend image. The current worktree intentionally does not add or
install a PostgreSQL driver: the dependency lease is still unavailable.

Run only after Integration supplies an immutable Linux image containing the
leased driver:

```bash
export MENTORFLOW_BACKEND_IMAGE='registry.example/mentorflow-backend@sha256:<digest>'
./run.sh
```

The runner creates isolated fresh and incremental databases, validates the
single `0031_enterprise_tenancy_completion` head, upgrades from
`0030_enterprise_jobs_v1`, preserves an existing row, downgrades exactly to the
jobs revision, and replays the upgrade. It then seeds 3 organizations, 6
workspaces, 20 users, and all 8 persistence domains.

The negative gate requires `UNAUTHORIZED_SUCCESS_EXPECTED=0` for foreign ID,
pagination, search, update, delete, export, job, cache, object-reference, and
`CONNECTION_REUSE` attacks. The pool gate reuses one physical DBAPI connection
in the sequence tenant A -> return -> same connection -> tenant B.

Until this package runs on the required staging host, no real PostgreSQL or
driver-backed claim may be promoted beyond `PENDING_STAGING`.

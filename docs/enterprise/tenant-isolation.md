# Enterprise tenant isolation

## Acceptance state

`REAL_POSTGRES_RLS_GATE = PENDING_STAGING`.

The Windows worker has no PostgreSQL server, Docker daemon, or leased Python
PostgreSQL driver. SQLite migration tests prove structural upgrade/downgrade
behavior only; they are not evidence that PostgreSQL RLS works.

## Synthetic corpus

The deterministic fixture contains:

- 3 organizations;
- 2 workspaces per organization;
- 20 users and 40 workspace memberships;
- 48 resources: one row for each of 8 resource kinds in each workspace;
- all three resource scopes and the frozen classification values.

`build_tenant_isolation_corpus()` produces 64 negative probes: each of the 8
resource kinds is checked against foreign ID read, list, search, update,
delete, export, background-job lookup, and cache ownership.

```text
UNAUTHORIZED_SUCCESS_EXPECTED = 0
```

## Evidence levels

| Evidence | Current state |
| --- | --- |
| Frozen contract identity and hash | Offline PASS |
| Migration fresh/upgrade/downgrade and synthetic seed | Offline PASS on SQLite; not an RLS claim |
| Policy SQL contains default-deny predicates and FORCE RLS | Static PASS |
| Fake DBAPI context probe and reset helpers | Offline PASS |
| Real PostgreSQL negative corpus | PENDING_STAGING |
| Same physical PostgreSQL connection reused across tenant A/B | PENDING_STAGING |

The staging gate fails if any tenant-scoped row is visible without context or
if any of the 64 foreign operations succeeds. The pool gate uses a one-slot
SQLAlchemy pool, records the DBAPI connection identity, returns tenant A's
connection, reuses it for tenant B, and requires zero visibility of A.

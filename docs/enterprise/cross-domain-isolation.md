# Cross-domain isolation

## Offline corpus

The deterministic corpus covers three organizations, two workspaces per
organization, twenty users, and all eight persistence domains. Every domain is
tested against ten unauthorized paths:

| Operation | Required result |
| --- | --- |
| Foreign ID read | 0 rows / no record |
| Foreign pagination | 0 foreign rows |
| Foreign search | 0 foreign rows |
| Foreign update | 0 affected rows |
| Foreign delete | 0 affected rows |
| Foreign export | 0 foreign rows |
| Foreign background-job lookup | no record |
| Foreign cache identity | rejected / different key |
| Foreign object reference | rejected |
| Same-connection tenant reuse | no prior-tenant state or rows |

This is 8 domains × 10 operations = 80 probes with
`UNAUTHORIZED_SUCCESS_EXPECTED = 0`. Pagination tokens, cache identities,
object keys, and the tenant-A/tenant-B connection sequence are explicit corpus
fields rather than inferred labels.

## Enforcement layers

The PostgreSQL adapter accepts only frozen `TenantContext` and `ResourceScope`
instances, verifies that the transaction tenant matches, and rejects a foreign
organization or workspace before a low-level call. PostgreSQL RLS remains the
authoritative database boundary. Application-side checks are defense in depth,
not a substitute for RLS.

Enterprise cache identity and object reference proofs are HMAC-authenticated.
The server signing keys are not serialized into either contract. A tenant
cannot relabel an identity or object key and pass verification.

Pool checkout is fail closed. If any tenant GUC survives checkout, the
connection is scrubbed with `RESET ALL` and rejected. The real staging gate uses
a one-slot pool to force tenant A -> return -> same physical DBAPI connection ->
tenant B.

## Evidence boundary

Offline contract, fake-adapter, corpus, migration, and Personal Local evidence
can be accepted without a PostgreSQL driver. Real RLS, application-role, and
physical connection reuse remain `PENDING_STAGING` until the Ubuntu/Docker gate
runs with the leased driver. An offline pass must not be reported as live
PostgreSQL acceptance.

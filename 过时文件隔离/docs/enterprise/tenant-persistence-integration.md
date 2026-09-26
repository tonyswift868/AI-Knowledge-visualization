# Tenant persistence integration

## Status

`E1-02B_CONTRACT = READY_OFFLINE`

`REAL_POSTGRES_GATE = PENDING_STAGING`

The integration has one frozen identity boundary:
`app.enterprise.contracts_v1` (`enterprise-context.v1`). `TenantMode` is also
the persistence mode; the tenancy package does not define parallel tenant,
actor, resource-scope, or persistence-mode contracts.

## Repository architecture

`EnterpriseRepositoryFactory` is the only mode selector. Business callers ask
for a domain repository and provide a frozen `TenantContext`; they do not test
for PostgreSQL. In `PERSONAL_LOCAL` mode the factory retains the caller's Local
store. In `ENTERPRISE_SERVER` mode it requires both the PostgreSQL resource
repository and an already scoped `TenantDbSession`.

All public domain names reuse `TenantDomainRepositoryPort`:

| Domain | Personal Local inventory | Enterprise table |
| --- | --- | --- |
| Document | SQLAlchemy SQLite, atomic JSON checkpoint, filesystem | `documents` |
| Evidence | immutable evidence, filesystem, memory | `evidence` |
| Knowledge Map | SQLAlchemy SQLite, memory | `knowledge_maps` |
| Plan | SQLAlchemy SQLite, memory | `learning_plans` |
| Teacher Session | SQLAlchemy SQLite, memory | `teacher_sessions` |
| Review | SQLAlchemy SQLite, SQLite, atomic JSON | `review_campaigns` |
| Student | SQLite, atomic JSON | `student_events` |
| Cost | SQLite | `cost_events` |

The executable inventory, including existing implementation and port paths, is
`app.enterprise_tenancy.inventory.ENTERPRISE_PERSISTENCE_INVENTORY`.

## Enterprise transaction boundary

Every PostgreSQL operation requires a frozen `TenantContext` plus
`ResourceScope`, and the repository transaction must carry the same tenant.
`EnterpriseUnitOfWork` exposes the domain repository, audit append, and outbox
intent append through the same `TenantDbSession`. The outbox is only a
transactional seam; this delivery does not implement the E1-05 queue.

`TenantJobContext` is the E1-05 handoff envelope. It composes the frozen tenant,
actor, and resource scopes for both human and service identities. It does not
introduce another actor or tenant contract.

## Cache and object ownership

All existing cache layers (provider prompt, local result, evidence, parse,
question bank, and evaluation artifact) receive an HMAC-authenticated tenant
partition before an enterprise key is produced. The same workload identity in
two organizations therefore cannot produce the same cache key, and a foreign
or forged tenant identity is rejected.

Document object references preserve organization, workspace, resource kind,
and a resource digest in the object key. Their ownership proof is signed and
checked against the frozen tenant and workspace scope. This is a database
ownership/reference boundary only; no S3 or other object-store adapter is in
scope.

## Personal Local compatibility

Offline tests exercise Document, Knowledge Map, Teacher Session, Review, Plan,
and Student repositories with `TenantContext(mode=PERSONAL_LOCAL)` and no
organization, workspace, account, PostgreSQL connection, or provider call.
Existing durable Local implementations remain canonical; the in-memory store
is an offline reference bridge, not a replacement persistence system.

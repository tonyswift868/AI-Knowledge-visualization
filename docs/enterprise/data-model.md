# Enterprise canonical data model

## Boundary

E1-02 adds a PostgreSQL repository/adapter layer. It does not copy rows from
Personal Local stores or rewrite existing business tables. `PERSONAL_LOCAL`
continues to use the caller-provided local repository; `ENTERPRISE_SERVER`
selects the PostgreSQL repository through `DualPersistenceSelector`.

The identity boundary consumes the frozen `enterprise-context.v1` models from
`app.enterprise.contracts_v1`. The persistence package defines no parallel
`ActorContext`, `TenantContext`, or `ResourceScope` contract.

## Canonical tables

| Area | Tables | Purpose |
| --- | --- | --- |
| Organization | `organizations`, `organization_memberships` | Organization identity and subject membership |
| Workspace | `workspaces`, `workspace_memberships`, `workspace_role_bindings` | Workspace membership is distinct from its one-or-more roles |
| Scope | `resource_scopes` | Resource ownership, tenant boundary, scope and classification |
| Learning data | `documents`, `evidence`, `knowledge_maps`, `learning_plans` | Canonical enterprise projections for source and learning structures |
| Runtime data | `teacher_sessions`, `review_campaigns`, `student_events`, `cost_events` | Canonical enterprise projections for teaching, review, student and cost activity |

Every resource projection carries `resource_id`, `organization_id`,
`workspace_id`, `scope_type`, `owner_subject_id`, `classification`, `status`,
timestamps, searchable text, job ownership and cache ownership. Payloads remain
adapter-owned JSON; this migration does not duplicate existing domain rows.

`scope_type` supports `PERSONAL`, `ORGANIZATION`, and `WORKSPACE`.
`classification` uses the frozen values `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`,
and `RESTRICTED`.

## Relationships

- A workspace belongs to one organization.
- An organization membership must exist before a workspace membership.
- A workspace role binding must reference a workspace membership. Its primary
  key includes `role`, so a member may have multiple explicit roles.
- Each enterprise resource row has a matching `resource_scopes` row protected
  by a composite integrity foreign key.

## Index rationale

| Lookup | Index strategy |
| --- | --- |
| Exact resource | Resource table primary key on `resource_id` |
| Organization/workspace boundary | Individual organization/workspace indexes plus compound tenant indexes |
| Owner/scope | `(scope_type, owner_subject_id)` and scope-owner index |
| Status feeds | `(organization_id, workspace_id, status, updated_at)` |
| Creation ordering | `(organization_id, workspace_id, created_at)` |
| Background job | `job_id` |
| Cache ownership | `(organization_id, workspace_id, cache_key)` |
| Membership | Subject/status and organization/workspace/status indexes |

The staging file
`enterprise_e1/staging/e1_02/sql/query_plans.sql` runs real PostgreSQL
`EXPLAIN` for the common lookups. No `EXPLAIN ANALYZE` numbers or query-plan
claims were fabricated on the Windows worker.

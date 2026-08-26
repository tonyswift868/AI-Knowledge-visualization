# Identity and tenancy evidence

Status: **PARTIAL / PENDING_OWNER_EVIDENCE**. E1-07B continues without waiting for E1-04; integrated identity/tenant acceptance still requires owner evidence and the database lease.

E1-07 consumes only the frozen `enterprise-context.v1` contract; it does not add, copy, or reinterpret ActorContext or TenantContext. The CI context gate rejects a missing manifest, a changed bundle hash, or a missing upstream contract test.

Required integrated behavior is fail-closed: each authenticated request resolves one upstream actor and tenant context; resource queries remain scoped to that tenant; foreign organization, workspace, plan, progress, review, audit, cost, and document identifiers return no successful response; background jobs retain the initiating tenant; and database RLS supplies a second enforcement layer. These are acceptance conditions, not locally observed results. PostgreSQL RLS and cross-tenant execution remain `STAGING_EXECUTION_PENDING`; `REAL_POSTGRES_RLS = NOT_RUN`.

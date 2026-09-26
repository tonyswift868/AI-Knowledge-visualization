# PostgreSQL row-level security policy

## Transaction context

`TenantTransactionContext` binds the frozen `TenantContext` and `ActorContext`.
Before returning a `TenantDbSession`, the transaction manager sets these
transaction-local PostgreSQL settings with `set_config(..., true)`:

- `mentorflow.organization_id`;
- `mentorflow.workspace_id`;
- `mentorflow.actor_subject`.

The actor organization/workspace must match the tenant context. Enterprise
persistence requires both identifiers and `ENTERPRISE_SERVER` mode.

## Database enforcement

Every canonical tenant table has one `FOR ALL` policy with both `USING` and
`WITH CHECK`, covering SELECT, INSERT, UPDATE and DELETE. Missing settings are
read with `current_setting(..., true)` and become `NULL`; comparisons therefore
deny access by default.

All canonical tenant tables use both:

```sql
ENABLE ROW LEVEL SECURITY;
FORCE ROW LEVEL SECURITY;
```

`mentorflow_app` is `NOSUPERUSER`, `NOBYPASSRLS`, and must not own tenant
tables or run migrations. The migration owner grants only SELECT, INSERT,
UPDATE and DELETE on the canonical tables.

Organization rows match organization context. Workspace and membership rows
match both organization and workspace. Resource rows additionally require the
actor to own `PERSONAL` scope; organization scope ignores the workspace while
remaining organization-bound.

## Pool safety

- Settings are transaction-local and disappear at commit/rollback.
- Pool return performs rollback and `RESET ALL`.
- Pool checkout reads all three settings with `NULLIF`; any retained value
  triggers reset and raises `TenantContextLeakError` before reuse.
- The real one-slot pool-reuse test remains `PENDING_STAGING` until a driver
  and PostgreSQL server lease exist.

API-level `WHERE organization_id = ...` filters remain useful but are not the
security boundary. PostgreSQL RLS is the final database isolation layer.

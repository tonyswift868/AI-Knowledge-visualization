# Enterprise Context Contract v1

`enterprise-context.v1` is the only shared Actor/Tenant/Resource/Decision/Audit
contract for Enterprise E1. Worker implementations import this package; they do
not create parallel actor or tenant models.

Mode boundary:

- `PERSONAL_LOCAL`: no login or organization is required; current single-user
  local stores remain supported.
- `ENTERPRISE_SERVER`: OIDC authentication and organization context are
  required for tenant data; PostgreSQL is canonical persistence and auditable
  access decisions are required.

The package has no dependency on Keycloak, FastAPI, SQLAlchemy, PostgreSQL, or
an authorization engine. Those adapters consume this contract at their own
boundaries. `contract_hash_manifest.json` freezes canonical Pydantic JSON
schemas; changing any model requires an explicit contract-version decision and
manifest update by Integration.

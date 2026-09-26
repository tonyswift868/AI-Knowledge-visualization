# Enterprise Admin control plane

The E1-03B control plane is an independent application feature. Its FastAPI
entry point is `app.governance.admin_api:app`, with routes under
`/api/enterprise-admin`. Integration may mount the exported router later; this
delivery does not modify `app.main` or the central frontend route table.

## Identity and tenant boundary

The API accepts `AdminContextResolver`, a port supplied by E1-02. The resolver
returns `AdminApiContext`, which wraps the single frozen `ActorContext` and
`TenantContext` contracts plus governance adapters. No identity model is copied
into this package. The standalone app uses `StaticAdminContextResolver` with an
explicit demo actor only; a production mount must replace it with the E1-02
authenticated resolver.

## Backend surface

The standalone module exposes organization membership lifecycle and role
operations, workspace creation and reads, Provider reference registration,
rotation, revocation and approved-model policy, quota status, permission-aware
navigation, and authorized audit query/export. Every enterprise mutation uses
authorization and audit before the store changes. Provider responses contain
status metadata only.

## Frontend feature

`frontend/src/features/enterprise-admin` is independently importable. It shows
organization and workspace selectors, workspace-specific role bindings,
Provider status, strictest quota policies, redacted audit activity, and all
eight navigation states. Until Identity UI integration, its default data is an
explicit demo authenticated-actor adapter fixture. The feature has no secret or
credential data field and is not added to central routes by this delivery.

## Database boundary

`ResourceVisibilityFilter` protects application results when a repository
overfetches, but PostgreSQL RLS remains the database floor. The included store
adapters and SQLite audit chain are deterministic foundations, not production
PostgreSQL acceptance. The Integration Manifest and QA requests name the
remaining non-blocking mounts and environment checks.

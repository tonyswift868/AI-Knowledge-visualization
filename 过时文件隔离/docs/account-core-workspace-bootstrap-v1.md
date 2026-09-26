# Account Core MVP workspace bootstrap — backend candidate

Dispatch: `MF-A-ACCOUNT-WORKSPACE-BOOTSTRAP-20260905-01`

Task: `MENTORFLOW_ACCOUNT_CORE_MVP_A_WORKSPACE_BOOTSTRAP_REPAIR_V1`

Source base: `bc9bcf5dc7ca1a8e690abba730eeef2542ab049d`

Date: 2026-09-05. This document describes a backend candidate, not a deployed server.

## Desktop call order

1. Complete the existing Authorization Code + PKCE login and obtain the Desktop access token.
2. `GET /api/enterprise/account/workspaces` with `Authorization: Bearer <token>`.
   Omit `X-MentorFlow-Organization-ID` and `X-MentorFlow-Workspace-ID` on this endpoint.
3. If the list is empty or `initialization_required` is true, explicitly invoke
   `POST /api/enterprise/account/workspaces/initialize` with the same bearer token,
   `Content-Type: application/json`, and body `{}`. Omit both tenant headers here too.
4. Select an entry from the latest response. A previously cached workspace ID is
   only a selection hint: use it only if it is still present in this response.
   `default_workspace_id` suggests the first entry in stable creation-time/ID order;
   it is not a newly persisted account preference.
5. Send the selected entry's `organization_id` as `X-MentorFlow-Organization-ID`
   and its `id` as `X-MentorFlow-Workspace-ID` to the existing
   `GET /api/enterprise/auth/me` and `GET /api/enterprise/account/metadata`.
   These endpoints revalidate effective membership with the unchanged identity system.

No fixed Alice/Bob organization or workspace ID is required. Empty devices and
re-login use the same authenticated discovery contract. Neither new endpoint is
mounted outside the Account Core MVP app.

## Actual response fields

Both new endpoints return HTTP 200 and the same shape, with `Cache-Control: no-store`:

```json
{
  "workspaces": [
    {
      "id": "<existing workspace ID>",
      "organization_id": "<existing organization ID>",
      "title": "<workspaces.name>",
      "status": "active",
      "initialization_status": "READY"
    }
  ],
  "default_workspace_id": "<one returned workspace ID>",
  "initialization_required": false
}
```

An empty account returns `workspaces: []`, `default_workspace_id: null`, and
`initialization_required: true`. A workspace lacking its required profile is
still discoverable, with `initialization_status: "PROFILE_REQUIRED"` and
`initialization_required: true`. GET performs no repair or creation; PostgreSQL
also enforces a read-only transaction for it.

The initialization body accepts no fields. `tenant`, `organization_id`,
`workspace_id`, `owner`, and `role` are rejected with 422, as are tenant selection
headers on either new endpoint. Missing or invalid authentication remains 401;
service-account principals are not eligible. Cookie sessions are not supported
by these two Desktop bearer endpoints and do not bypass bearer authentication.

Existing `/account/metadata` now returns 409
`ENTERPRISE_WORKSPACE_INITIALIZATION_REQUIRED` for a membership-visible workspace
whose profile is absent. Truly absent or inaccessible workspaces retain existing
authorization/not-found handling. It does not fabricate a profile in a read response.

## Existing space repair and first use

`AccountWorkspaceBootstrap` uses the existing canonical tables and configured
issuer, not an account cache. The unchanged OIDC validator supplies the principal;
only a human principal from the configured issuer can enter these operations.
The current directory/schema is single-issuer: existing membership subject IDs
are interpreted only under that configured issuer, not arbitrary token issuers.

If any accessible workspace exists, initialization returns those workspaces and
inserts only their missing profiles. It never creates an additional personal
workspace in that case. Existing profile contents and timestamps are unchanged.

If no accessible workspace exists, initialization creates one isolated personal
organization/workspace using a SHA-256 identity derived from length-prefixed
`issuer + sub`. The IDs are deterministic, with the existing primary/unique
constraints. PostgreSQL takes a transaction-scoped advisory lock for that identity;
the entire discovery/create/membership/profile sequence is a single transaction.
Both organization and workspace roles are `LEARNER`. The display title is the
explicit product default `我的学习空间`. No plans, progress or learning history
are synthesized. A failure rolls back all records.

An existing but inaccessible deterministic personal organization causes 403
`ACCOUNT_PERSONAL_WORKSPACE_UNAVAILABLE`. Initialization does not reactivate a
revoked/suspended membership or join an existing organization. The ordinary
`POST /api/enterprise/workspaces` still requires active organization OWNER/ADMIN.
No Keycloak role or global administration permission is granted.

## Required forward migration

Candidate head: `0035_account_workspace_bootstrap`; parent:
`0034_enterprise_asset_manifests`. Historical 0033/0034 are unchanged.

No new tables are added. Migration 0035 is needed for two changes:

- Backfill missing profiles using only workspace ID/organization ID and the
  authoritative 0033 column defaults: empty description/persona/provider fields,
  `character_id=warm`, external knowledge false, `execution_mode=demo`.
  Existing profiles are never updated. SQL is idempotent (`ON CONFLICT DO NOTHING`).
  Temporary policies apply only to `mentorflow_migrator` inside the migration
  transaction and are removed before completion; FORCE RLS is never disabled.
- Add SELECT policies for subject-only discovery of active own memberships and
  their spaces/organizations, plus INSERT policies for the exact deterministic
  personal workspace and its own LEARNER membership. No profile UPDATE/INSERT
  policy is broadened for learners; selected-workspace policies stay in force.

The existing organization create service already creates profile and memberships
transactionally. The current synthetic tenant initializer previously omitted
profiles; it now invokes the same missing-profile helper when the schema has
0033 profiles. Historical fixtures predating that table remain supported.
All these writes remain inside their callers' existing transactions.

Migration downgrade removes only new policies and retains backfilled/user records.
Deployment must use the existing authorized migrator workflow for this new head;
this task does not migrate or modify any real database and does not stamp revisions.

## Verification and handoff limits

Focused offline tests exercise actual MVP HTTP endpoints with an isolated SQLite
database and a test token adapter. They cover truly empty accounts; legacy learner
spaces missing profiles; existing profile/Plan/Progress/Asset Manifest preservation;
A/B discovery and metadata denial; payload rejection; repeated login and a fresh
client; a two-request initialization; transaction failure; revoked membership; and
forward/backfill idempotency. Existing OIDC and organization-create behavior is reused.

SQLite evidence is not real PostgreSQL RLS evidence and the test tokens are not
live Keycloak tokens. An optional dedicated test uses only
`E1_WORKSPACE_BOOTSTRAP_TEST_DATABASE_URL`, which must identify a pre-provisioned,
isolated PostgreSQL application database already migrated to 0035. It does not
provision infrastructure, execute migrations, or use the production DSN.
Without it, `REAL_POSTGRES_TESTS = NOT_RUN`.

The frozen delivery receipt carries final executed test counts and commit/tree.
Architect/E1-06 must arrange candidate deployment and fresh real Desktop PKCE,
discovery, initialization, metadata and A/B RLS verification. E1-07 can consume
the route/field contract above after architecture handoff. No employee dispatch
is made here. Desktop source, launcher, manifest, package, OIDC configuration,
Worker, Docling, OTel, object store and Web frontend are unchanged.

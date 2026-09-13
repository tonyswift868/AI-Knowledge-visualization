# Batch04 integration boundaries and exact candidate follow-up

Authority: MF-MAIN3-A-BATCH04-INTEGRATION-20260913-01.
Source integration is accepted separately from packaged/live two-device acceptance.
The exact [receipt](../governance/receipts/20260913-main3-batch04-integration.json) owns counts and artifact hashes.

## Producer → consumer mismatch

| Boundary | Integrated producer | Consumer requirement / unresolved decision |
| --- | --- | --- |
| Account/session | 04 client accepts an injected synchronous authenticated transport and tenant context | Login/bootstrap must supply a validated transport and 01 canonical account identity, handle renewal/logout, and invalidate late results. |
| Revision | 04 revisions are per entity, not a global snapshot cursor | 03 requires a nonempty revision; 02 observation requires last_sync_revision. Define an authoritative snapshot/version meaning. |
| Empty account/history | V1 returns only plans, progress, resources and tombstones | 03 requires full-account empty confirmation, review/history counts and support scope. Empty V1 lists cannot establish an empty account. |
| Resource type | V1 filename/media_type/size/SHA256 | 03 requires file/model. MIME or extension cannot invent the product classification policy. |
| Resource cardinality | Multiple plans may reference one stable resource ID | 03 accepts unique resource keys with a single plan_key; define shared-resource presentation without duplicating or changing stable IDs. |
| Progress/cache | Structured mastery/completed_tasks; no trusted cache observation | 03 needs display progress, server_confirmed/trusted_cache/last_sync. An adapter may not invent durable/cache provenance. |
| Observation | 02 can read a candidate/scenario/generation-scoped observation.json | No integrated writer connects real login/sync results to it. Hand-written fixture observations do not prove cloud recovery. |

`POST_BATCH04_WIRING_REQUIRED` remains open. Standalone UX tests verify syncing, complete, offline, failure, true-empty,
missing file/model and preserved plan/progress. Native PG tests verify the V1 producer. These do not prove their product connection.
Portable Pack V2 entry remains readiness-gated; there is no new import executor or real migration.

## Candidate admission gaps

The build attempt pins runtime source `852f8c2c1874bf1dd8b6855da492a97313b0080d`; later integration changes are tests and knowledge.
Frontend is built using the checked-in package-lock and an offline E: npm cache; PyInstaller output and logs are retained in the receipt.
The 703,067,817-byte frozen EXE was produced and its archive/module/frontend/schema structure verified.
Its SHA256 is `2a641ea0a2a981dcacf2a29d9425149ef56efa0377dd5804797715a9561907ab`.
The initial analysis completed but hit its time bound during packaging; a repeated analysis invalidated its resource cache.
Final packaging reused the completed Analysis/PYZ after checking all 457 repo modules against exact Git blobs and omitting generated __pycache__ resource data.
The executable is a build artifact until complete startup and scenario configuration are qualified; packaged runtime launch remains NOT_RUN.
No installed/live candidate is replaced by this build.

- Existing C# development launcher depends on a mutable source checkout and holds one global mutex; it is not the immutable A/B product candidate.
- Signed `desktop_gui_entry.py` clears the process environment through startup profile preparation. The new test-device environment needs an explicitly compatible build path; it cannot be assumed to survive that entry.
- `desktop_entry.py` can freeze the current source, but an Account Core sidecar requires a matching nonsecret endpoint/client config and TLS certificate. The harness deliberately clears inherited settings. No accepted Batch04 Backend deployment or issuer mapping is registered.
- The current harness requires `manifest.source_sha == RepoSource.sha` both for UI compatibility and launch. Committing a target built from S advances branch HEAD to T, so a valid pinned candidate cannot pass that equality. The base launch verifier already checks registered CURRENT authority, exact Git tree, artifact and identity hashes. A subsequent bounded harness change must reconcile build-source identity with the governance publication commit and retain rejection of arbitrary/unapproved sources.
- Backend requires `0037_structured_sync`. A source archive is not a deployed image digest or a tested endpoint.

`TEST_TARGET_STATUS = WAITING_FOR_WIRING_BACKEND_AND_CANDIDATE_QUALIFICATION`.
[ACTIVE_TEST_TARGET](../governance/ACTIVE_TEST_TARGET.json) uses the existing schema's WAITING_FOR_ARTIFACT state;
no CURRENT designation, launch-ready identity, real A/B PASS or user action is fabricated.
Future A and B must use the same artifact/build/source hashes and different owned roots; never substitute the old mutable launcher.

## Combined evidence and skips

Retained 01 suites were 222 PASS / 2 SKIP and 60 PASS / 1 SKIP, overlapping; Keycloak was 32 PASS.
The two unique skipped checks were unavailable Windows symlink creation privilege and an unconfigured isolated PG bootstrap DSN.
Retained 04's 17 specialized native migration cases were not run by that worker. A executed all 17 with an owned native PG cluster,
including full fresh/0035/0033 → 0037 chains, already-current read-only startup, future/drift rejection, ledger RLS and continuity.
A also executed the previously optional PG bootstrap and pooled RLS gate; final skip inventory remains explicit in the receipt.
The unavailable Windows symlink creation test remains a limitation for later privileged migration QA. Junction/hardlink/path-swap
denial and Temp/test-only migration rejection are separate executed checks, not substitutes presented as a symlink PASS.

To run the dedicated pooled RLS fixture, provision one disposable database and supply separate runtime-only references:
`MENTORFLOW_RLS_DSN` for administrator provisioning/seed/SET ROLE probes, and `MENTORFLOW_RLS_MIGRATION_DSN`
for the nonprivileged `mentorflow_migrator` on the same host/port/database. Alembic must never run as administrator.
The fixture's database owner and application role remain NOSUPERUSER/NOBYPASSRLS/NOCREATEROLE.
A's bootstrap probe additionally uses a direct nonprivileged application login. No real external DSN is accepted by the retained A runner.

All real migration, public issuer cutover, account registration/forgot-password activation, main2 mutation, formal main3,
tags, force push and GitHub-hosted Actions remain unexecuted. This document does not dispatch workers or authorize a live gate.

# Main3 release/data readiness — dispatch 27

Status: **PARTIAL / NOT AUTHORIZED TO DEPLOY**. Candidate source is
`69b7e210ce7ff4abeb14337e4d969b0420db476a`, tree
`d55f893a169a63a1f83cf8a9fafa2725796a011a`. No product code changed.
Use the three JSON evidence files beside this document. The worker's commit is
an evidence delivery, not a replacement source candidate.

## What actually ran

- Strict known-host SSH to `mentorflow-admin@89.45.45.28`, read-only backup
  inventory in both documented backup roots; no remote writes or deployment.
- Latest database pair found: application and identity dumps under
  `/opt/mentorflow/backups/e106-import-20260906/`. September 13 directories contain
  configuration backups, not newer database dumps. A host-wide search was not
  performed. Checksums prove remote-to-copy equality, not independently signed
  backup integrity or current recovery-point completeness.
- Copies live in ACL-restricted `E:/Codex/Temp/MF04-RELEASE27`, outside Git.
  PostgreSQL custom format 1.16, originating PostgreSQL/pg_dump 17.6. Both restored
  successfully using pg_restore 17.6 with exit-on-error, retaining ownership/ACLs;
  132 application and 100 identity tables. No globals/password SQL was executed.
  Isolated roles used new disposable credentials; no identity service was started.
- Exact candidate archive migrations upgraded restored application 0035 → 0040.
  Every pre-existing table except alembic_version retained identical row count and
  sorted full-row SHA256 digest. This includes stable IDs, relationship values,
  resource metadata and membership scopes. Empty task/progress tables in this
  server backup are not evidence of populated legacy task/progress migration.
- Actual app-role schema preflight passed. Unscoped application role sees zero
  learning_plans/workspace_memberships/asset_manifests despite superuser counts
  2/7/5. This is an unscoped-denial RLS check, not a complete cross-account UAT.
- A second fresh application restore matched the pre-migration state exactly:
  backup rollback rehearsal passed. All three test databases and their isolated
  cluster were removed using the existing NativePostgres ownership checks.
- Six local SQLite sources were opened mode=ro/query_only and backed up into
  isolated copies using SQLite backup. Source hashes were unchanged, all copies
  passed quick_check. Only aggregate counts/schema and digests enter Git.

## Old-data interpretation and migration limits

PRIMARY `E:/MentorFlowData/data/mentorflow_v2.db` currently contains 530
orm_workspaces, **88 orm_plans**, 83 orm_progress, 330 orm_sources, 73
active_materials, 797 orm_plan_execution_tasks, and 11,363 active_plan_tasks.
Legacy app.db has 36 workspaces and 15 plans. ORM and active/legacy tables have
different roles; never sum them as unique user entities. SECONDARY/LEGACY each
have 83 orm_plans. Full source-separated counts and versions are in inventory.
Primary plans→workspaces, sources→workspaces, progress→lessons and tasks→days
orphan counts are all zero on the snapshot. No row titles, account IDs, passwords
or tokens were exported into evidence.

Current canonical mapping must preserve existing business ORM stable IDs while
registering canonical issuer/subject, organization/workspace and resource scope.
`orm_workspace_ownership.user_id` is a local association; it is not proof of a
Keycloak subject. Local source rows do not automatically become asset_manifests
or uploaded object bytes. Metadata mapping must preserve hashes/source lineage,
and file availability needs separate storage verification. Machine-local paths,
caches, expired sessions and unresolved credential references are not portable
server authority. No accepted cross-source deduplication/owner-binding receipt
or complete legacy-domain importer was identified. **No local legacy→PostgreSQL
data conversion was run or claimed.** Schema upgrade of a restored VPS database
does not resolve that blocker.

## Exact command contract for the next authorized writer

These commands are preparation only. All named inputs must exist, be sealed and
match the JSON manifest before execution. Never replace unresolved digests with
latest or assume a historical artifact matches this source.

Required package root on VPS:
`/opt/mentorflow/releases/69b7e210ce7ff4abeb14337e4d969b0420db476a`.
Required immutable backend/Keycloak/edge/Postgres digests and signed Desktop
artifact are currently **UNRESOLVED**; live realm/native verifier/SMTP/mTLS and
edge overlays are also not sealed. The command set is therefore **not released
for execution**. No service or database name may be guessed from branch names.

1. Named live/database/identity writers and maintenance window; exact previous
   images, configuration hashes, data mounts and current pointers recorded.
2. Host preflight using the existing script from the exact candidate:

```sh
python3 scripts/enterprise/staging_host_preflight.py \
  --host-config /opt/mentorflow/config/host.json \
  --resource-budget deploy/enterprise/staging/resource-budget.initial.json \
  --profile minimal --execute \
  --receipt /opt/mentorflow/evidence/main3-69b7e210-host.json
```

3. Account Core Compose command prefix (Bash array; only after sealed inputs):

```sh
cd /opt/mentorflow/releases/69b7e210ce7ff4abeb14337e4d969b0420db476a/deploy/enterprise/staging/rp05
dc=(docker compose --project-name mentorflow-e1-preview --profile account-core-mvp
    --env-file /opt/mentorflow/config/main3-69b7e210.env
    -f /opt/mentorflow/releases/69b7e210ce7ff4abeb14337e4d969b0420db476a/deploy/enterprise/staging/rp05/compose.account-core-mvp.yml)
"${dc[@]}" config --quiet
"${dc[@]}" pull
```

4. **BACKUP_GATE BLOCKED**: create a NEW application+identity custom-format pair
   in the approved outage/snapshot window plus object/file/config backups. The
   September 6 pair is nine days old and does not cover current writes. Use
   operator-provisioned private pg_service.conf/pgpass references; never command
   line passwords. Example exact service names to provision in that private file:

```sh
umask 077
test ! -e /opt/mentorflow/backups/main3-69b7e210
mkdir /opt/mentorflow/backups/main3-69b7e210
pg_dump --dbname=service=main3_application_backup --format=custom \
  --file=/opt/mentorflow/backups/main3-69b7e210/application.dump
pg_dump --dbname=service=main3_identity_backup --format=custom \
  --file=/opt/mentorflow/backups/main3-69b7e210/identity.dump
cd /opt/mentorflow/backups/main3-69b7e210
sha256sum application.dump identity.dump > SHA256SUMS
sha256sum --check SHA256SUMS
pg_restore --list application.dump >/dev/null
pg_restore --list identity.dump >/dev/null
```

   Restore both NEW dumps into a separately owned cluster with zero egress; gate
   on complete restore, counts/digests, schema/ownership/RLS and object checks.
   List/readability alone is insufficient. Backup service definitions, storage
   coverage and coordinated app/identity consistency remain operator inputs.
5. **MIGRATION_GATE**: repeat exact candidate upgrade on that new disposable
   application clone. Require 0040 exact policy/role/schema preflight, preserved
   populated references and rollback proof. Only then authorize retained writes:

```sh
"${dc[@]}" run --rm runtime-secrets-ready
"${dc[@]}" run --rm migrate
"${dc[@]}" run --rm migration-ready
"${dc[@]}" run --rm backend-tls-ready
"${dc[@]}" up --detach --wait backend oidc-loopback-proxy edge
```

   Do not recreate/provision the existing database or overwrite the retained
   Keycloak realm. Keycloak/native verifier changes need a separately reviewed
   identity writer step; this prepared backend sequence does not perform it.
6. **HEALTH_GATE**: verified TLS GET /health/live, /health/startup, /health/ready
   at the sealed public API origin; require HTTP 200 and ACCOUNT_CORE_MVP ready,
   Postgres + Keycloak ready, correct issuer/audience and 0040 admission. No -k.
   Then native disposable LEARNER login/bootstrap, same-account fresh-device
   restore, admin 200/member 403, foreign-scope denial, and missing-local-file
   continuity smoke. Email transport readiness is not email-delivery acceptance.
   No real account/password mutation is part of this worker's smoke.

## Rollback plan

Never Alembic-downgrade 0040: it is forward-only. Same-schema binary rollback
requires exact previous image/config package and healthy startup. The existing
staging_release_orchestrator.py rollback-drill requires matching migration heads;
rp05_staging.py rollback changes a symlink only. Neither restores DB or identity.
They must not be treated as a mixed-stack automatic rollback.

On schema/data failure: stop the authorized writer's traffic, preserve failed
volumes, restore the sealed backup into NEW isolated databases, and validate
before separately authorized endpoint/volume cutover. After provisioning private
PG service definitions that point ONLY to the new rollback targets:

```sh
pg_restore --exit-on-error --dbname=service=main3_application_rollback_isolated \
  /opt/mentorflow/backups/main3-69b7e210/application.dump
pg_restore --exit-on-error --dbname=service=main3_identity_rollback_isolated \
  /opt/mentorflow/backups/main3-69b7e210/identity.dump
```

Target emptiness, isolated host/port, expected roles and snapshot digests are
mandatory before these commands. No --clean, no restore over retained real DB.
Object storage requires its own verified rollback manifest; restoring PostgreSQL
metadata alone does not restore attachment bytes. Final cutover command cannot
be sealed until the exact rollback release/mount/secret references are supplied.

## Cloudflare operations follow-up

Origin CA expiry supplied/previously observed: 2041-09-07. Main3 blocker: NO.
Named OPS owner should record the live fingerprint/expiry in certificate inventory,
check expiry monthly, and alert 180/90/30 days before expiry: 2041-03-11,
2041-06-09, 2041-08-08. Recompute thresholds after every approved renewal, test
alert delivery, retain encrypted backup and verified rollback of the prior cert.
This is a monitoring plan, not an automation created or certificate changed.

## Remaining blockers / replay evidence

Exact deployable image and Desktop artifacts; sealed identity/edge/native TLS
overlays; fresh coordinated DB and object backups; approved local-data canonical
owner/domain mapping; complete populated legacy import/foreign-tenant UAT; named
writers and exact rollback bindings. Product acceptance remains pending.

Restricted replay scripts and receipts are retained under
`E:/Codex/Temp/MF04-RELEASE27`: `remote_inventory.py`, `ssh_read.py`,
`copy_inventory.py`, `restore_rehearse.py`, and `restore-rehearsal.json`.
The restore script uses the existing `scripts/schema_0036_native.py` cluster
owner, exact candidate archive and pg_restore; no new migration engine exists.
Do not upload raw dumps, SQLite snapshots or private logs. Replays must use new
scratch output identities rather than overwrite retained evidence.

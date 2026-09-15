# Release transaction 29 — prepared, not executed

Transaction: `MF-MAIN3-RELEASE-29-20e976c7`. Control fetch verified
`1087091598037b8b4077fd44328ae3312b9ebe4d`; Frozen source
`20e976c7878401123ca2b20f63b2cf70a73e5f3e`, tree
`bf0e13143177a3ec9e9b602f4bd65635961bc241`. Worker branch is evidence-only.

**READY_FOR_AUTHORIZED_PREDEPLOY = NO**. Commands below remove command discovery
from the backup step, but the complete release transaction remains gated by
unsealed native identity/edge overlays, unsigned TEST Desktop status, canonical
owner decisions, and positive-domain migration qualification. Generated image
digests are deliberately deferred, never invented. Do not execute the next phase
on a failed gate. No automatic credential, identity, or certificate changes.

## Owner actions: two decisions, no username matching

1. Bind the one explicit legacy-owner group to an authenticated canonical
   `mfa1_…` account ID through the existing issuer/subject seam and a signed owner
   decision. The group covers 529 existing workspaces, 83 plans, 330 resources.
   Local-user existence proves only the local ownership link. It supplies no
   Keycloak membership or account binding. One group decision replaces 529
   repetitive workspace confirmations; scope must still be reviewed.
2. Resolve the single unowned workspace separately: 5 plans and 797 execution
   tasks cannot be attached to the first group implicitly. Keep quarantined until
   the owner identifies its account/domain or explicitly elects to retain locally.

55 dangling ownership references point to absent workspaces, not 55 additional
owners. Retain them as quarantined provenance; do not fabricate workspaces or
delete the references. Source evidence is the existing PRIMARY snapshot; no
repeat backup/restore or 0035→0040 rehearsal was performed this dispatch.

Mapping files use SHA256 workspace references with a restricted exact-ID index
at `E:/Codex/Temp/MF04-BINDING29/workspace-resolver.PRIVATE.json`. This prevents
real identifiers entering Git and does not change original stable IDs. The private
resolver plus snapshot digest is required before any approved importer can apply
the mapping. Do not treat a SHA reference as a new product workspace ID.

Admission-only dry-run used a disposable clone, recording 948 decisions
(530 workspaces + 88 plans + 330 resources); admitted canonical rows = 0. Clone
digest and ID/workspace pairing remain unchanged. All unresolved rows are held.
This verifies safe quarantine, **not a successful PostgreSQL domain migration**.
No account was available for a positive import or RLS exercise; those gates are
BLOCKED/NOT_EXERCISED. Prior RLS evidence retains its original scope.

## PRECHECK (future writer)

Acquire the named deployment/database/identity writer leases and outage window.
Compare live image IDs, compose files, config hashes, mounts and PG container to
ROLLBACK_BINDINGS.json; any drift stops and requires a new observed binding.
Preserve separate backend, identity, edge and proxy releases: retained stack is
mixed. Recheck local postgres catalog contains `mentorflow` and
`mentorflow_keycloak` (read-only observed). Verify disk space, protected backup
directory, previous image availability, native provider + mTLS/issuer overlay,
and exact signed artifact before traffic switching.

Freeze application and identity writers for the paired backup window. Merely
sequential pg_dump snapshots are not a coordinated app/identity recovery point.
Do not disable services or accounts in this worker task. After authorization,
the named operator must verify the freeze before running the following commands.

## Fresh backup — exact retained-container commands

Run as the authorized host operator with Docker access; do not use September 6
dumps. No credential values enter arguments, artifacts metadata, or stdout.
The local Unix-socket postgres connection was verified read-only this dispatch.

```bash
set -euo pipefail
umask 077
TXID=MF-MAIN3-RELEASE-29-20e976c7
BACKUP=/opt/mentorflow/backups/$TXID
TX=/opt/mentorflow/artifacts/$TXID
PG=mentorflow-e1-preview-postgres-1
test "$(docker inspect --format '{{.Image}}' "$PG")" = sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3
test ! -e "$BACKUP"  # never overwrite a prior transaction or failed attempt
mkdir -m 700 "$BACKUP"
exec 9>/opt/mentorflow/backups/main3-predeploy.lock
flock -n 9
docker exec "$PG" pg_dump -U postgres -Fc mentorflow > "$BACKUP/application.dump.partial"
docker exec "$PG" pg_dump -U postgres -Fc mentorflow_keycloak > "$BACKUP/identity.dump.partial"
test -s "$BACKUP/application.dump.partial"
test -s "$BACKUP/identity.dump.partial"
mv "$BACKUP/application.dump.partial" "$BACKUP/application.dump"
mv "$BACKUP/identity.dump.partial" "$BACKUP/identity.dump"
(cd "$BACKUP"; sha256sum application.dump identity.dump > SHA256SUMS; sha256sum -c SHA256SUMS)
docker exec -i "$PG" pg_restore --list < "$BACKUP/application.dump" > "$BACKUP/application.toc.PRIVATE"
docker exec -i "$PG" pg_restore --list < "$BACKUP/identity.dump" > "$BACKUP/identity.toc.PRIVATE"
date -u +%FT%TZ > "$BACKUP/captured-at-utc.txt"
printf '%s\n' "$TXID" > "$BACKUP/transaction-id.txt"
docker inspect --format '{{.Image}}' "$PG" > "$BACKUP/postgres-image-id.txt"
docker exec "$PG" psql -U postgres -d mentorflow -Atc 'BEGIN READ ONLY; SELECT version_num FROM alembic_version; COMMIT;' > "$BACKUP/application-schema.txt"
```

Failure/nonzero/empty file/checksum mismatch stops before migration/deploy. Leave
partial evidence protected for review, never silently retry into the same root.
Seal backup metadata (transaction, source, timestamps, image, schema, sizes,
checksums) and config/object-file snapshot references into ROLLBACK_BINDINGS.json
as a new predeploy receipt. Do not put raw DB/TOC/config dumps or secrets in Git.
Backup secret/config volumes through existing protected operator storage; no
public config export. Require object/file backup manifest if rollback must
recover bytes; PostgreSQL metadata backup alone is not that guarantee.

## Isolated restore verify and migration gate

The following restore container has no network and no published port, no retained
database mount, and tmpfs data. Trust authentication is restricted to this empty
disposable namespace; no retained identity service is launched there.

```bash
RESTORE=mf-main3-restore-29
PGIMAGE=postgres@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3
docker run -d --name "$RESTORE" --network none \
  --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=1g \
  -e POSTGRES_HOST_AUTH_METHOD=trust "$PGIMAGE"
for attempt in $(seq 1 30); do
  docker exec "$RESTORE" pg_isready -U postgres && break
  sleep 1
done
docker exec "$RESTORE" pg_isready -U postgres
docker exec "$RESTORE" psql -U postgres -v ON_ERROR_STOP=1 -c \
  'CREATE ROLE mentorflow_migrator LOGIN NOSUPERUSER NOBYPASSRLS; CREATE ROLE mentorflow_app LOGIN NOSUPERUSER NOBYPASSRLS; CREATE ROLE mentorflow NOLOGIN; CREATE ROLE mentorflow_keycloak NOLOGIN;'
docker exec "$RESTORE" createdb -U postgres -O mentorflow_migrator app_restore
docker exec "$RESTORE" createdb -U postgres -O mentorflow_migrator identity_restore
docker exec -i "$RESTORE" pg_restore -U postgres -d app_restore --exit-on-error < "$BACKUP/application.dump"
docker exec -i "$RESTORE" pg_restore -U postgres -d identity_restore --exit-on-error < "$BACKUP/identity.dump"
```

Any missing role/extension/collation or restore error stops. Do not add --no-owner
or disable RLS to conceal it. Reconcile table counts, sorted ID/full-row digests,
declared references and asset manifest against the fresh backup. Retain identity
restore checksum/count results without exporting accounts/credentials. Complete
the exact artifact-binding phase below before invoking the following migration
command. Apply the bound candidate only to app_restore, using the same namespace:

```bash
NEW_BACKEND=$(cat "$TX/backend.iid")
docker run --rm --network container:"$RESTORE" --entrypoint sh \
  -e MENTORFLOW_DEPLOYMENT_MODE=ENTERPRISE_SERVER \
  -e MENTORFLOW_DATABASE_URL=postgresql+psycopg://mentorflow_app@127.0.0.1/app_restore \
  -e MENTORFLOW_MIGRATION_DATABASE_URL=postgresql+psycopg://mentorflow_migrator@127.0.0.1/app_restore \
  "$NEW_BACKEND" -ec 'cd /app/backend; alembic upgrade head; python -c "from app import orm; orm.configure_enterprise_orm(); orm.verify_enterprise_runtime_schema()"'
```

Require 0040, exact RLS contracts, stable populated-row/reference digests and an
independent second restore rollback match before passing migration gate. The
namespace has no external egress; URL strings above contain no passwords and
must never be reused against retained or exposed PostgreSQL. Clean up only this
named container after evidence and inspect confirm its disposable mount identity.

## Exact artifact binding

`SOURCE` must be a verified archive extraction of the Frozen source; `TX` must be
the new protected artifact directory `/opt/mentorflow/artifacts/$TXID`. Check every
archive hash against the source receipt. Build commands for backend/edge and
digest output slots are in MAIN3_RELEASE_BINDINGS.json; they are not run here.
Resolve and record base-image digests before build, retain build metadata/SBOM,
and verify org.opencontainers.image.revision. An image config ID is not a
registry manifest digest; retain archive checksum and registry digest separately
if pushed. Never substitute the Frozen Desktop EXE for a server image.

Keycloak blocker is concrete: the source Dockerfile keycloak-image target only
optimizes upstream Keycloak. It does not package the required native verifier
provider. Use the identity owner's reviewed native build/overlay and seal its
hash before proceeding; a new guessed provider build is outside this dispatch.
Versioned realm/edge file hashes are recorded but are not current realm-state or
live TLS/issuer overlay acceptance. TEST Frozen hash is verified, but signature
and production acceptance remain separate gates.

## Deploy, smoke, and rollback trigger (not executed)

After ALL gates above and a sealed Compose/env overlay, use the Account Core
profile, not rp05_staging.py's fixed compose.minimal.yml. Bind Compose to the
exact retained data/secret mounts and immutable image IDs, then execute:

```bash
dc=(docker compose --project-name mentorflow-e1-preview --profile account-core-mvp
    --env-file "$TX/runtime.env" -f "$TX/compose.account-core-mvp.yml")
"${dc[@]}" config --quiet
"${dc[@]}" run --rm runtime-secrets-ready
"${dc[@]}" run --rm migrate
"${dc[@]}" run --rm migration-ready
"${dc[@]}" run --rm backend-tls-ready
"${dc[@]}" up -d --wait backend oidc-loopback-proxy edge
```

Do not re-provision PostgreSQL or import a realm over retained identity. Identity
rollout, if required, needs the reviewed owner step before dependent backend
switching. `runtime.env`/Compose remain unsealed, so this step cannot run today.

Health: verified TLS /health/live → /health/startup → /health/ready at the sealed
public origin, plus Keycloak discovery/JWKS issuer/signature and backend mTLS.
Require native disposable LEARNER login/bootstrap, admin/member distinction,
foreign-account/workspace denial, structured A-write/B-empty-root rehydrate,
stable IDs/revisions and missing-local-model visibility. Stop on any failure.
Email readiness does not establish inbox or lifecycle acceptance.

Rollback trigger: migration/health/identity/RLS/login/sync smoke failure, any
unexplained data count/reference change, or mismatched image/config/mount hash.
Keep writers stopped. Do not downgrade 0040. Restore fresh paired backups to
NEW verified DB targets, compare to predeploy, then switch the approved previous
backend/identity/edge/proxy bindings from ROLLBACK_BINDINGS.json. Preserve failed
volumes and evidence; do not pg_restore --clean into retained real DB. A symlink
rollback alone cannot undo schema/identity mutations. Exact cutover runtime.env
and rollback target bindings still require the live writer's seal.

## Boundaries and completion

No VPS deploy, fresh backup, real old-data migration, role change, build, or
certificate change occurred. Origin CA expiry 2041-09-07 is NOT a Main3 blocker;
retain prior 180/90/30-day OPS monitoring follow-up, no renewed research.
Two data-owner decisions plus native/edge overlay sealing and release acceptance
remain. Positive mapped migration/RLS must run only after those data decisions;
safe quarantine cannot be promoted to full migration PASS.

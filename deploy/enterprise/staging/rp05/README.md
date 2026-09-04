# RP-05 current-base minimal staging package

This package is a deployment asset, not evidence that host `89.45.45.28` was
changed or that a real TLS/staging run passed. It deliberately excludes
SeaweedFS, Prometheus, Grafana, Tempo, Loki, and k6 from the default Compose
graph. Docling is installed in the durable Worker image because the Preview
profile enables the Document Ingestion handler; model/network access remains
disabled at runtime.

## Host contract

The release builder targets `/opt/mentorflow` and preserves these operator-owned
areas: `releases`, `current`, `config`, `secrets`, `data`, `backups`, `evidence`,
and `logs`. Secrets and certificates are never generated into or copied from the
Git tree. Required secret filenames are listed by `rp05_staging.py preflight`.

The durable worker uses `app.enterprise.jobs_v1.production:build`. Engineering
Preview binds the real Document Ingestion, Knowledge Pipeline, and Plan Creation
product adapters. Export and Account Deletion have no Preview callback or
environment key: they fail closed without blocking factory or worker startup.
Document uploads are shared read-only with the worker under
`/var/lib/mentorflow/document-import`.

The backend keeps the public Keycloak issuer unchanged and mounts the public
TLS certificate read-only as the trust anchor for its namespace-local OIDC
route. This preserves TLS, signature, issuer, audience, and expiration
validation while allowing the backend to fetch the public issuer's JWKS.

## Reproducible sequence

1. Build the repository `keycloak-image` Docker target, publish it to the
   approved registry, and record its immutable repository digest. This target
   runs the Keycloak production augmentation during image construction; an
   unmodified upstream Keycloak image is not compatible with this release.
2. Copy `runtime-images.template.json` and replace every image with an exact
   `name@sha256:<64 lowercase hex>` reference. `KEYCLOAK_IMAGE` must be the
   repository digest produced from the `keycloak-image` target above.
3. Copy `compose.env.example` and set the public HTTPS origin, hostname, and
   project name. Keep the production factory and three supported handler paths
   unchanged. Do not put secrets in this file.
4. Create the nine external secret files under `/opt/mentorflow/secrets`.
   `materialize-secrets` copies them into consumer-isolated named volumes with
   explicit UID/GID/mode metadata. Services never depend on Compose remapping a
   host file's ownership, and no service receives unrelated secrets.
5. Build a release with `build_rp05_staging_release.py`; transfer the resulting
   directory without changing its manifest-covered files.
6. On the Linux host, run `python3 rp05_staging.py preflight`, `config`, `pull`,
   `materialize-secrets`, `fresh-postgres`, `migrate`, `up`, `readiness`,
   `restart-readiness`, and `evidence` in that order. `migrate` and `up` also
   rerun the materializer fail-closed before starting consumers.
   `fresh-postgres` starts the exact PostgreSQL digest
   against an isolated tmpfs data directory and never mounts the persistent
   database path. `restart-readiness` recreates backend, worker, frontend, and
   edge in bounded phases and re-runs the complete readiness contract after
   each phase.
7. Use `activate` only after readiness succeeds. `rollback` changes only the
   atomic `current` symlink to a previously validated release.

`/health/startup` is used only for backend process startup. RP-05 classifies
object storage as `OPTIONAL_DISABLED`: the profile does not advertise object
storage operations, and `/health/ready` reports
`OBJECT_STORE_DISABLED_NOT_REQUIRED` as a non-required dependency. The public
readiness gate still requires PostgreSQL, Keycloak/OIDC, OpenTelemetry, a fresh
durable-worker heartbeat, the migration head, and the backend/edge path.

## Account Core MVP profile

`compose.account-core-mvp.yml` is a bounded profile of this same RP-05
deployment framework. Start it only with `--profile account-core-mvp`. Its
resident services are PostgreSQL, Keycloak, the Account Core backend, the
namespace-local OIDC transport proxy, and the TLS edge. Secret materialization,
PostgreSQL provisioning, Keycloak verification, Alembic `head` migration,
schema verification, and TLS trust materialization are one-shot services.

The profile starts `app.account_core_mvp:app` and exposes only health, the
Desktop bearer principal endpoint, Workspace/Goal metadata, Account
Plan/Progress projections, and Asset Manifest metadata registration/read
operations. Worker, document-worker, Docling, Procrastinate processing,
document ingestion, OpenTelemetry, object storage, the Web frontend, Teacher,
Assessment, and AI Providers are neither built nor readiness-gated. The
readiness response labels each of them `NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP`.

Build only the Docker targets `backend-image`, `keycloak-image`, and
`account-core-edge-image`; use the exact PostgreSQL base digest separately.
`runtime-images.account-core-mvp.template.json` lists the four immutable image
inputs. Copy `compose.account-core-mvp.env.example`, replace every placeholder,
then render without starting containers:

```sh
docker compose --profile account-core-mvp \
  --env-file compose.account-core-mvp.env \
  -f compose.account-core-mvp.yml config
```

The Windows Desktop's current implementation uses Authorization Code + PKCE
with an IPv4-loopback callback and the public `mentorflow-desktop` Keycloak
client. Configure the Desktop with `MENTORFLOW_DESKTOP_OIDC_CLIENT_ID` set to
`mentorflow-desktop`, `MENTORFLOW_DESKTOP_OIDC_AUDIENCE` set to
`mentorflow-api`, the public issuer/API URL, and a server-provisioned
organization/workspace selection. The Desktop stores refresh/access material
only through its Credential Locker. The backend receives the bearer access
token plus selected organization/workspace headers and resolves membership
server-side; it does not accept account or tenant fields in Account metadata
payloads.

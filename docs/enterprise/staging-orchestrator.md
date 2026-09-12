# MentorFlow staging release orchestrator

## Durable writer and rollback boundaries

Authority: `MF-MAIN3-A-KNOWLEDGE-INTEGRATION-WEB-HANDOFF-20260912-02`.
Before any mutation, record one authorized writer for each resource domain:
live service/release pointers, application database/migrations, Keycloak/identity,
and formal user data. A writer needs a named owner, exact scope, evidence and
handover boundary; another worker cannot infer ownership from source access.
Unknown ownership blocks that domain's writes. Current owners/leases must come
from the latest formal receipt and [board](../governance/ACTIVE_BOARD.json), not
old E1 employee numbers. This knowledge task holds none of these write leases.

Source rollback != server rollback != database rollback != identity rollback.
Each requires its own authorized writer, exact recovery inputs, backup/restore
or migration plan and post-recovery checks. A Git revert proves only source
state. Keep actual credentials, databases and backups outside the repository;
retain only non-sensitive ownership and evidence references.

Status: **IMPLEMENTED / OFFLINE_VERIFIED / STAGING_EXECUTION_PENDING**. This package is executable after an Ubuntu host and immutable image refs exist. It does not claim a RackNerd deployment, Docker execution, PostgreSQL RLS result, k6 result, container result, or Linux staging result in this office.

## Host contract and bootstrap

`deploy/enterprise/staging/host.racknerd.example.json` is the current `StagingHostConfig`: RackNerd is an informational provider label only; `provider_api_binding` is null and every script remains vendor-neutral Linux. The target is Ubuntu 24.04, 3 vCPU, 4 GB RAM, and 60 GB disk. The host must use a dedicated non-root `mentorflow-deploy` account, public-key SSH, reviewed IPv6 policy, explicit firewall ports, UTC time sync, and a reviewed swap policy.

Run `scripts/enterprise/bootstrap_staging_host.sh` without `--apply` to inspect its machine-readable plan. On the named host, review it and run as root with `--apply --authorized-key-file /root/operator.pub` (or pre-provision a valid key in the deploy user's `authorized_keys`). Before restricting SSH, the script fails closed unless it can validate at least one deploy-user public key, preventing a first-run lockout. It installs supported Ubuntu packages, Docker Engine and Compose plugin, locks the deploy account password, installs an SSH hardening drop-in, validates `sshd`, enables UFW, creates the directory layout, and writes an atomic receipt. It never calls a RackNerd API, creates a cloud account, stores a private key, or embeds a password.

Directory ownership is explicit:

```text
/opt/mentorflow/
  releases/   mentorflow-deploy:mentorflow-deploy 0750
  current/    atomic symlink to one exact release package
  data/       mentorflow-deploy:mentorflow-deploy 0750
  data/secrets/ mentorflow-deploy:mentorflow-deploy 0700
  backups/    mentorflow-deploy:mentorflow-deploy 0750
  artifacts/  mentorflow-deploy:mentorflow-deploy 0750
  logs/       mentorflow-deploy:mentorflow-deploy 0750
```

## Preflight and resource budget

`staging_host_preflight.py` checks Ubuntu version, CPU, total/available RAM, total/free disk, writable storage, candidate loopback port, Docker daemon, Compose plugin, DNS seam, time synchronization, IPv6 review, and memory headroom. The candidate port must be free or owned exactly by the existing `mentorflow_staging/frontend` Compose service; an unrelated listener fails closed while a repeat release remains possible. The budget distinguishes advertised RAM from a bounded minimum observable through Linux (`3800 MiB` for the 4 GB target); formatted disk accepts at most a 10% advertised-capacity delta while retaining a separate 15 GB free-space gate. A live receipt binds the host node, host-config hash, resource-budget hash, and profile; stale/cross-host replay is rejected. Live deployment requires an atomic `LIVE_HOST` PASS receipt; an offline fixture cannot authorize deployment.

`ContainerResourceBudget` is explicitly `INITIAL_STAGING_BUDGET`, not a production optimum. The minimal profile fits the current 4 GB target with 768 MB reserved for the host and bounded CPU/memory for Postgres, Keycloak, backend, frontend, worker, and a minimal OTel collector. The full profile adds object storage, Prometheus, Grafana, testing services, and a separately bounded optional Docling worker; the initial all-in budget requires an advertised 12 GB host. A 4 GB host cannot silently start full, and a registered Docling hook cannot escape the profile's 2 GB container cap.

## Exact release package and profiles

`build_staging_release_package.py` consumes one `EnterpriseReleaseManifest`, verifies its canonical content hash and exact source identity, requires its `enterprise-context.v1` identity to match `.github/enterprise-context.lock.json`, rejects `latest`, requires resolved immutable refs for infrastructure and the independent core worker, copies only versioned compose/config/load assets, and hashes every artifact. The committed runtime-image file is intentionally `TEMPLATE_NOT_RESOLVED`; it cannot deploy until an operator supplies exact digests or 40-character commit tags in a non-secret `RESOLVED` copy.

The package contains no credentials. Runtime secrets must already exist as mode 0600 files under `/opt/mentorflow/data/secrets`. Command receipts retain phase, argv, exit state, and output sizes but never stdout/stderr bodies, avoiding accidental connection-string capture. The minimal and full Compose profiles expose only the candidate frontend on loopback. Keycloak, Postgres, OTel, Prometheus, Grafana, and object storage have no public host ports. The minimal OTel collector uses a no-op exporter rather than logging trace payloads; a real reviewed backend remains a later configuration seam. Stable data volumes are not scoped to a release checkout.

## Deploy and migration gate

`staging_release_orchestrator.py deploy` performs this fail-closed order:

1. Verify package and artifact hashes plus a live preflight receipt.
2. Validate secret-file existence and permissions without logging values.
3. Validate Compose, pull exact images, and start/wait for the private Postgres service (including a fresh host). The first-volume initializer creates a separate `mentorflow_keycloak` database.
4. Write custom-format backups of both the application and identity databases under `/opt/mentorflow/backups`.
5. Run the migration service.
6. Run `migration-verify`; failure stops before application startup or pointer switch.
7. Start the selected bounded profile and wait for health.
8. Run the small provider-zero k6 smoke against the candidate loopback port.
9. Atomically replace `/opt/mentorflow/current` only after health and smoke.
10. Run the optional proxy reload command and retain version/container receipts.

Migration failure is never followed by continued release. The receipt points to the pre-migration backup and requires isolated restore, checksum/schema/tenant verification, and an approved switch. It does not perform a blind downgrade or claim that `git checkout` rolls back the database.

## Smoke, load, tenant isolation, and provider boundary

The smoke contract names health, login, workspace, knowledge, plan, progress, and cost seams. Paths and credentials come from protected host environment files, not source. `PROVIDER_ADAPTER=fake` is mandatory and `real_provider_calls=0` remains a hard threshold.

The containerized k6 runner defaults to `smoke`. The `average`, `stress`, `spike`, and `tenant-isolation` profiles require `--approve-expanded-load`; this prevents a first run from overwhelming the 4 GB VPS. The existing three-organization, 21-user corpus randomly probes foreign IDs and keeps `unauthorized_success_count = 0`. Document submit/status and foreign-document probes are enabled only when `DocumentStagingVerificationHook` is registered.

## Security, evidence, and rollback drill

`staging_security_runner.py` consumes retained Gitleaks or GitHub secret-scanning evidence; it does not invent a scanner. It validates public ports, admin-port exposure, Compose hardening, TLS certificate verification, default-credential rejection, and required response headers. Results are an atomic machine receipt.

`collect_staging_evidence.py` requires deploy, versions, containers, migration, tests, k6, security, tenant-isolation, backup-reference, and OTel-reference categories. It derives the release ID and source commit from the validated exact `release-package.json`, records both the package file hash and canonical content hash, hashes every retained file, rejects symlinks and sensitive-looking filenames, permits only JSON references for backup/OTel categories, and writes an atomic index plus ZIP bundle. Raw backups, telemetry payloads, credentials, and tokens are excluded.

`rollback-drill` requires exact candidate B and rollback A packages with identical migration heads. It executes B → health → provider-zero smoke → atomic pointer, then A → health → provider-zero smoke → atomic pointer and emits a machine receipt. Different migration heads are rejected and must use the documented backup/restore drill.

## CI and E1-04 hook

`.github/workflows/staging-orchestrator.yml` is a future GitHub staging-environment hook. Environment protection supplies approval; a self-hosted Linux staging runner performs preflight, package, deploy, smoke, optional expanded load, security, and evidence. The reviewed runner-local host-config input is where operators resolve DNS, IPv6, and proxy reload seams; the committed example intentionally keeps them pending. The workflow references environment variables/secrets but contains no SSH private key, token, password, or cloud credential.

`DocumentStagingVerificationHook` is `OPTIONAL_UNTIL_E1_04`, `required_for_release=false`, and `E1_04_DEPENDENCY=NO`. Once E1-04 publishes its exact hook, Integration replaces the disabled config with a registered compose overlay, required document smoke names, and evidence refs. A registered hook is accepted only with the full profile so its Docling budget cannot bypass the host gate. The core orchestrator is independently auditable and deployable without that hook.

## Current environment truth

This original delivery recorded `LOCAL_WINDOWS_DOCKER = NOT_RUN`, `LOCAL_WINDOWS_COMPOSE = NOT_RUN`, `REAL_POSTGRES_RLS = NOT_RUN`, `REAL_K6 = NOT_RUN`, `REAL_CONTAINER = NOT_RUN`, and `REAL_LINUX_STAGING = NOT_RUN`. These are dated evidence, not permanent live state. Later exact receipts may establish new runtime results; syntax, unit tests, dry-run plans and offline fixtures cannot do so. Read [CURRENT_STATE](../project/CURRENT_STATE.md) before inferring today's environment.

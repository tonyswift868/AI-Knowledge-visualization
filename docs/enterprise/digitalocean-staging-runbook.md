# DigitalOcean Singapore staging runbook

Status: **CI_CONFIGURED / STAGING_EXECUTION_PENDING**. Target seam: DigitalOcean, Singapore `SGP1`, Ubuntu 24.04 LTS. This document does not purchase a server, create an account, or contain credentials.

## 1. Provisioning request and identity

Request one reviewed staging resource through the approved DigitalOcean project/IaC flow with region `sgp1`, Ubuntu 24.04, an exact size selected from measured capacity, backups policy, tags, owner, expiry, and cost alert. Record droplet/resource ID in the environment inventory. Do not use a personal password or embed a token.

Create an Ed25519 SSH key locally or through the organization key process; add only the public key. Verify the host fingerprint through a second trusted channel before first login. Disable password and root SSH login after the non-root sudo path is proven.

```text
SSH_PUBLIC_KEY=<approved public key only>
STAGING_HOST=<assigned DNS or IP>
STAGING_USER=mentorflow-deploy
```

Create `mentorflow-deploy` as a non-root user, grant limited sudo through a reviewed sudoers drop-in, and retain an emergency access procedure owned by platform operations. Never commit the private key, sudo password, registry token, or DigitalOcean token.

## 2. Network, firewall, and IPv6

Apply a DigitalOcean Cloud Firewall first: SSH only from approved operator/VPN ranges; HTTP/HTTPS from intended test sources; no public database, Redis, worker, Docker socket, or metrics-admin port. Mirror the policy with `ufw`, verify default deny incoming/default allow outgoing, then test a second SSH session before closing the first.

Review IPv6 explicitly. If enabled, add equivalent IPv6 firewall rules and verify services do not bind more broadly than IPv4. If the environment cannot maintain equivalent policy, keep application ingress IPv6 disabled and document the decision; never assume an IPv4 rule protects IPv6.

## 3. Docker Engine and Compose

Use Docker's official Ubuntu apt repository and pinned package versions. Verify repository signing key fingerprint, apt source, installed versions, and `docker compose version`. Add only the deployment user to the docker group after acknowledging that Docker access is root-equivalent. Configure daemon log rotation and restart policy. Do not install from an unreviewed convenience script.

Record exact versions in the staging evidence artifact. `REAL_CONTAINER` stays `NOT_RUN` until all three images build/scan and the host actually runs them.

## 4. Volume and configuration layout

Use explicit owned directories, for example:

```text
/srv/mentorflow-staging/releases/<source_commit>/
/srv/mentorflow-staging/shared/data/
/srv/mentorflow-staging/shared/document-worker/
/srv/mentorflow-staging/backups/
/etc/mentorflow-staging/
```

Application data and backups must not live inside a Git checkout or container writable layer. Make the deployment user owner only where required; configuration containing secrets is root-readable and mounted read-only. Validate free space, inode capacity, backup destination, and restore permissions before deployment.

## 5. Domain and TLS seam

Create a staging-only DNS name after ownership approval. Terminate TLS with the approved reverse proxy/load balancer, automate certificate renewal, redirect HTTP to HTTPS, and configure secure headers. Keep the backend/frontend Compose ports on loopback or the private network. Verify certificate chain, hostname, expiry, renewal timer, and that origin/forwarded-proxy allowlists match the real proxy address.

## 6. Deploy and verify

1. Obtain the `EnterpriseReleaseManifest` and verify source commit/tree, `enterprise-context.v1` hash, three immutable image refs, SBOM/security artifacts, migration heads, and known blockers.
2. Authenticate to the registry with a short-lived scoped credential; never use `latest` as the only reference.
3. Pull and retain all three rollback image digests before touching staging.
4. Take and verify a pre-migration backup. Record checksum, encryption status, migration head, and restore command seam.
5. Run the machine release procedure: migration → backend/frontend → health → k6 smoke → promotion.
6. Confirm provider mode is fake and real provider calls remain zero.
7. Upload release receipt, logs with redaction, exact Compose/Docker versions, and readiness snapshot.

## 7. Backup and restore

Use encrypted, checksum-verified database/object/config backups with the encryption key stored separately. Define retention and expiry with the data owner. A backup is not accepted until restored into an isolated target and validated for migration head, tenant counts, object checksums, application health, own-tenant smoke, and foreign-tenant denial. Record measured RPO/RTO; do not estimate them.

## 8. Monitoring and incident seams

Ship allowlisted metrics/logs/traces to the approved backend with TLS. Monitor request/error/duration categories, queue depth/age, document-worker failures, disk/inodes, database health, backup jobs, certificate expiry, container restarts, provider calls, unauthorized-success count, and cost. Pager/contact routing is `PENDING_OWNER_EVIDENCE` until E1-03 supplies it.

## 9. Upgrade and rollback

Upgrade only from another reviewed immutable manifest. Retain prior images and pre-migration backup, run schema compatibility checks, health, smoke, and a short observation window before promotion. On any gate failure restore promotion tags to prior digests, deploy prior images, apply the documented database strategy, then require rollback health and post-rollback smoke.

Database rollback is not `git checkout`: prefer forward-fix when the migration is backward-compatible. For a destructive/incompatible migration, stop writers, preserve failed-state evidence, restore the exact encrypted pre-migration backup into an isolated target, verify checksum/schema/tenant isolation, then atomically switch the staging data endpoint under approval. Never run an unreviewed Alembic downgrade against production-like data.

## 10. Safe staging destruction

Require owner approval and an exact DigitalOcean resource ID. Freeze deploys, export required evidence, confirm retention/legal hold, take or explicitly waive the final backup, revoke short-lived credentials, remove DNS/TLS routing, and verify no production dependency points at the resource. Destroy only the named staging resource through the approved console/IaC plan after reviewing the plan output. Then revoke SSH/registry access, remove orphaned volumes/snapshots/firewall rules by exact ID, record cost cessation, and retain the destruction receipt. Never use a wildcard or account-wide delete.

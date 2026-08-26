# Enterprise release and rollback procedure

Status: **CI_CONFIGURED / STAGING_EXECUTION_PENDING**. `LOCAL_WINDOWS_DOCKER = NOT_RUN`; `LOCAL_WINDOWS_COMPOSE = NOT_RUN`; `REAL_CONTAINER = NOT_RUN`; `REAL_K6 = NOT_RUN`; `REAL_LINUX_STAGING = NOT_RUN`.

## Release contract

Input is one `EnterpriseReleaseManifest` binding the exact source commit/tree, semantic application version seam, frozen enterprise contract, migration heads, three immutable candidate refs, per-image SBOM/security refs, load/QA refs, blockers, and pending gates. Three previously healthy immutable refs and three explicit non-`latest` promotion tags are also required.

The machine sequence is:

1. **build** backend/frontend/document-worker at the source commit;
2. **test** fast/full CI and contract/release evidence gates;
3. **scan** CodeQL, secret, dependency, SAST, Trivy, and per-image SBOM gates;
4. **deploy staging preparation** retain three rollback images and verify manifest/ref immutability;
5. **migration** take/verify pre-migration backup, then run the one-shot migration service;
6. **health** wait for backend/frontend and `/api/health` status `ok`;
7. **smoke** run controlled-provider k6 smoke;
8. **promote** move all three explicit promotion tags to candidate digests;
9. **rollback on any failure** restore promotion tags and deploy rollback images;
10. **post-rollback smoke** require rollback health and k6 smoke before recording `rolled_back`.

`scripts/enterprise/release_rollback.py` exits nonzero after a candidate failure even when rollback succeeds; the receipt preserves both facts.

## Database migration strategy

Database state is not rolled back by checking out an old Git commit.

- Prefer additive/backward-compatible migrations and a forward-fix when the new application has not irreversibly changed data.
- Before migration, record exact Alembic head(s), database/storage version, encrypted backup reference/checksum, backup key seam, and a tested restore command.
- If migration fails before the candidate accepts traffic, stop writers and retain the failure evidence. Use a reviewed forward-fix or restore into an isolated target; do not improvise a downgrade.
- If the candidate writes data and an incompatible schema/data transformation must be reversed, stop writers, preserve the failed database, restore the exact pre-migration backup into an isolated target, verify checksum/schema/tenant counts/object links, then atomically switch the staging data endpoint under approval.
- An Alembic downgrade is allowed only when the revision has an explicitly reviewed, tested, loss-aware downgrade contract. Destructive downgrades are prohibited as an automatic fallback.
- After any database recovery, run migration-head verification, health, same-tenant smoke, foreign-tenant denial, and post-rollback smoke. Record measured data loss/RPO and time/RTO; never estimate them.

## Evidence and unresolved gates

The staging JSON receipt plus EnterpriseReleaseManifest are retained for 90 days or the approved release-evidence policy. Docker/registry/Linux/real PostgreSQL/k6/promotion/backup restore have not run in this office, so their results remain `STAGING_EXECUTION_PENDING`. Observability, backup ownership, cost, and AI evaluation owner artifacts remain `PENDING_OWNER_EVIDENCE`.

Integration consumes ordered exact commits from the new Delivery Fence and never a branch tip or modified old receipt.

# MF-R2-A-INTEGRATE — source convergence and bounded service application

Status: **SOURCE_INTEGRATED_SERVICES_APPLIED_SMTP_BACKEND_EGRESS_PENDING**.
User GUI/email/password acceptance: **NOT_RUN / PENDING_USER**.

## Exact source

Remote start: `fad2c1abbce3be44cc69d7a6f9c1b4dbf893244a` on `control/main3-current`.

| Owner | Exact delivery | Local integration |
| --- | --- | --- |
| 03 | `73282b12b7c30dfb959258991b81a30e63c78309` | `83efd83c84dde8baa150adffb4e19af272183a2f` |
| 02 | `0cca6de0d3879ee2f8c8242b50557911392bfbbe` | `90d63e6e6a066c513cf0aeb2d49178b35e6b3c9a` |
| 04 independent settings/admin | `79d24e8f593af90904b4681683ea4cb4ba1947b5` | `da090bb7e26180073ffb4337e9def2ee2dea3a1a` |
| 01 | `664993e4351085d4eed6ee9915943fe0a948b264` | `e79e9271c279c0f65daa23131044bda6156b4214` |
| 04 final email wiring | `879f19287ffa675413a75a681b98134b2f67c97b` | `6a90b7e2e871ac8571c28da30b48c35fe2455849` |

All exact patches applied without conflicts. No branch-tip history was merged.
02 lightweight login import/startup preparation, 03 saved-password hint, E startup
journals and 04 captured-account password cleanup are retained. The last 04 patch
changes only Desktop/tests/receipt: backend, production SPI and its build script
are identical between deployment source and final product source. No Frozen build.

## Service deployment and provenance

- Deployed source: `e79e9271c279c0f65daa23131044bda6156b4214`.
- Source tree: `b5985661cca816159c036bd750a2a8094951e5b9`.
- Built from exact Git archive; 924 backend files verified byte-for-byte in the
  running image, zero mismatches. Requirements and Alembic sources were unchanged
  from the previous deployed `7719096b` source; no dependency/schema migration.
- Backend image: `sha256:f519cfd4fa138be55d624d1214372e7d28dd6c1c6187a2714faca81f60c58a69`.
- Keycloak image: `sha256:85ae0326fbc157607e8e224d4d1ea85c1f5163b0c25ab06b00b51f06b8a8088a`.
- Production SPI SHA256: `43e92afad4d0eac9152246679a7088b8e0fa9d318aa6726858036ed5fab0586f`.
- SPI compiled against exact Keycloak 26.7.3 / JDK 21. Contains EmailCodeBinding;
  no fixture observer/control classes. Running JAR digest matches the build.
- Backend, Keycloak and namespace-dependent oidc-loopback-proxy are healthy.
  Existing static addresses, mounts, security settings and network memberships
  preserved; the proxy was recreated only to share the replacement backend's
  namespace. No new service topology or account authority.
- Durable deployment evidence and incremental runtime override:
  `/opt/mentorflow/artifacts/r2-a-e79e9271/` and `runtime.override.json`.
  Apply that override after existing overrides in any later authorized redeploy;
  it changes only these two images and the SMTP config/mount.

## Backup, rollback and data preservation

Private configuration snapshots and PostgreSQL custom-format backups are retained
under `/opt/mentorflow/artifacts/r2-a-e79e9271/private`; both dumps passed
`pg_restore --list`. No database restore/migration was executed in this task.

- Application backup SHA256: `7ee21596e513e6031816e5580cff59346fa3bc32d5b330dda28a74d7b0731af5`.
- Identity backup SHA256: `30406f8dab60fb630199279d181db0a48dc4512c67d18c8f0cb885615191edcf`.
- Old containers retained with `-r2-before-<old-id>` suffix, old image IDs in
  `prepare-proof.json`, original network/config snapshots and replacement journal
  in `private/`. Service rollback restores those containers/images and their
  original networking; do not blindly restore a database over newer user work.
- Before this deployment there were **2942** structured rows: all historical
  2941 IDs present, all historical revisions unchanged, zero tombstones; one
  additional review already existed. No attribution of that extra row is inferred.
- Before/after digests match for structured rows, workspaces, memberships,
  profiles, resource scopes, Alembic revision, Keycloak users, credentials,
  role mappings, realm and required-action policy. Schema remains
  `0040_admin_overview_aggregate`.
- Existing canonical issuer/subject/account and SUPER_ADMIN configuration remain
  unchanged. The four original local files were not accessed or changed; this
  task did not requalify their historical content hashes.

## SMTP application: configuration applied, backend network still blocks probe

Approved authority remains `E:/Codex/Secrets/MentorFlow/smtp.env`, read via existing
`brevo_smtp.load_private_store()` with private ACL/contract validation. No values
appear here. Existing sender, SMTP key and Keycloak realm SMTP settings unchanged.

The existing server `e109/config/smtp.json` was not a Brevo REAL_RELAY config and
was not reused or overwritten. A private runtime projection under
`/opt/mentorflow/artifacts/r2-a-e79e9271/smtp` is mounted read-only at
`/run/mentorflow/smtp`; `MENTORFLOW_SMTP_CONFIG` points to `config.json`. Owner
10001:10001, directory 0500, files 0400; secret reference resolves only in backend.
This is a deployment projection, not another credential authority. No desktop
credential, new secret, rotation, role grant or identity setting change.

Actual observations with existing `observability.health._email_probe`:

| Execution location | Result |
| --- | --- |
| Actual Backend internal-only network | `EMAIL_TRANSPORT_UNAVAILABLE` |
| Same image/config/probe, one-shot container sharing existing Keycloak egress | `EMAIL_TLS_AUTH_READY` |

The one-shot probe container was removed. Protocol: EHLO/TLS/EHLO/AUTH/NOOP only,
zero messages. Backend cannot connect directly to Brevo:587 because its existing
network is internal-only. Its network authority was not broadened. A bounded
approved egress route is still required for the **backend** admin readiness probe.
This does not claim a failure of Keycloak email submission, which was not tested.

The 04 follow-up receipt's statement that SMTP application was reported resolved
was not a live verification by 04. The observations above are A's current result;
do not interpret that historical statement as backend readiness PASS.

## Focused verification and limits

- 02/03 merged seams: 8 pytest passed; no windows or network.
- 04 independent integration: 4 focused backend/probe tests passed and four plain
  callback/response cases passed. A first callback harness used `request` instead
  of production `post`; the harness was corrected, no product change required.
- 01 + overview interface checks: 25 passed.
- Final 04 adapter/transport boundary checks: 7 passed with `--noconftest`, no
  QApplication/windows. Full worker suites retained with their original scope;
  not mechanically repeated or summed into a new acceptance total.
- One existing Starlette/httpx deprecation warning; `git diff --check` passed.
- Verified public HTTPS `/health/ready` 200 and identity discovery 200; public
  transport issuer unchanged. Anonymous GET email/admin overview and empty POST
  email request/verify/password change return 401; invalid synthetic bearer 401.
- Private SPI without client certificate rejected; pinned backend mTLS read-only
  lookup of a nonexistent synthetic subject returns expected INVALID. No real
  user session, credential test, email send or account mutation.
- Initial helper targeted unused local TLS port 8443; corrected to the existing
  loopback HTTP listener 8000, readiness 200. External TLS remained verified.

## User entry and remaining acceptance

Existing desktop `.lnk` target and WorkingDirectory still resolve to
`E:/Codex/Data/worktrees/2735/MentorFlow/Start-MentorFlow-Dev-Workbench.cmd` and its
repository. On a manual reopen, Workbench loads the new local launcher/window
code; development selection resolves remote `control/main3-current`, formal
selection remains remote default main2. No GUI was opened, closed or automated.

User will manually reopen **MentorFlow Dev Workbench** and select **启动最新开发版**
after publication. Native first paint, experienced startup time, saved-password
manual login, actual email receipt/verification and password changes remain user
acceptance, not claimed by fixtures or these service checks.

Only control/main3-current publication is authorized. integration/main3, main2,
default, formal main3, tags and GitHub Actions unchanged. No subagents/delegation.

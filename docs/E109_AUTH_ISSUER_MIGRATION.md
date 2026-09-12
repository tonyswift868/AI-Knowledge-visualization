# E109 authentication issuer migration — A → 05

> 2026-09-12 product authority update: normal desktop login now uses MentorFlow
> native username/password controls and the controlled native-session API.
> [ADR: native credential authentication](ADR_NATIVE_CREDENTIAL_AUTH_V1.md)
> supersedes the browser UX in this document and commit `03d979710a4cde44917c4f10c2f08d2390c8426d`.
> The issuer migration material below remains a **legacy protocol/infrastructure**
> contract, not the normal login UX or approval to activate a migration window.
> Native identity verification is disabled until the ADR's identity bridge gate
> is satisfied. There is no browser fallback. `auth-authority.json` remains prepared.

Batch02 authority `MF-MAIN3-A-BATCH02-INTEGRATION-20260913-01` also supersedes
Batch01's System Browser normal-login requirement. Historical browser/PKCE/state/nonce
checks remain applicable only to explicit legacy protocol consumers. Native login
requires secure backend verification and successful workspace bootstrap before the
main window; no password/direct grant, password persistence or browser fallback.

Scope: `MF-E109-INTEGRATION-CONTROL-20260910-01 / AUTH_ISSUER_MIGRATION_BIND`.
Source/config qualification only. No installer, live backend deployment, Keycloak
change, email, GitHub Actions or remote push is performed by this delivery.

## Audited authority

| Consumer | Before activation | Prepared migration |
| --- | --- | --- |
| Desktop auth base | `https://localhost:18080` | `https://auth.clannad.win` |
| Desktop expected primary issuer | `https://localhost:18080/realms/mentorflow` | `https://auth.clannad.win/realms/mentorflow` |
| Desktop discovery | old issuer + `/.well-known/openid-configuration` | new issuer + same path |
| Desktop callback | `http://127.0.0.1:<allocated-port>/oauth/callback` | unchanged |
| Desktop API base | `https://localhost:18080` | unchanged; auth host is not an API proxy |
| Backend primary issuer | old issuer | new issuer |
| Backend token/JWKS transport | `https://localhost:18080/realms/mentorflow` | unchanged, same controlled realm |
| Backend JWKS | transport + `/protocol/openid-connect/certs` | unchanged |
| Backend strict preflight discovery | transport + `/.well-known/openid-configuration` | unchanged, validates bounded old/new issuer |
| Backend health-only discovery | `http://keycloak:8080/realms/mentorflow/.well-known/openid-configuration` | unchanged; not JWT trust authority |
| Backend browser callback | existing public-origin `/api/enterprise/auth/callback` | unchanged |
| Account identity namespace | old issuer | old issuer, after cryptographic validation only |

The desktop registered callback remains `http://127.0.0.1/oauth/callback`;
the existing callback port setting `0` allocates an ephemeral IPv4 port.
Do not replace the callback host with the authentication domain.

Old literals deliberately remain in `scripts/windows_installed_launch.py` and
`desktop/startup_connection.py`: they are inactive-build defaults and the retained
API/tunnel authority. `compose.account-core-mvp.yml` and `compose.minimal.yml`
derive the existing issuer from `MENTORFLOW_PUBLIC_ORIGIN` and transport from
`MENTORFLOW_PUBLIC_HOSTNAME`. Do not change those shared variables to the auth
host: they also control Keycloak, API, callback and network routing.

05 exact infrastructure reference: `a7bd1f8f3ffbc94f2db25c26f1d5b4ceb4322950`,
tree `07c6d29af6c4a765e5086d2c9ce364beb82d8fcf`.
Receipt `DLV-W05-20260911T022357Z-08`, SHA-256
`f361e468b04c5fdb49783c336060b67940800b2f2dc2325ebfd194aef155599f`.
Its migration contract SHA-256 is
`5662cb790710dde77eaf1d8d49edd6e4d476426def0c2ded73146d38d8d0d550`.
This reference was audited, not merged together with unselected 05 history.

2026-09-11 read-only HTTPS checks of `auth.clannad.win` and `89.45.45.28`
returned the same OLD discovery issuer and the same RSA signing-key set.
Canonical public signing-key SHA-256:
`58b30e499176955656b3f6edf0b3cfaa305eed75933c2245315ca8ba10ee5a24`.
Canonicalization: RSA/use=sig, sorted kid; ordered fields kid/kty/alg/n/e;
compact JSON array, UTF-8. Combined with 05's unchanged `keycloak:8080`
upstream contract, this binds the two URLs to the same realm/signing authority.
This is evidence at audit time, not a claim that the new issuer is already live.

## Closed migration contract

`backend/app/enterprise/identity/issuer_migration.py` accepts only the two exact
issuer strings above and the exact 05 authority hash. The explicit UTC Unix-second
window is `[not_before, not_after)`, maximum 172800 seconds. No automatic renewal,
wildcard, Host-header trust, arbitrary key URL or issuer-validation bypass exists.
Outside that window only the configured primary issuer is trusted.

Use NEW as primary before cutover. Keep OLD as `identity_issuer`, not as an
unbounded token allowlist. Verified principals then preserve existing membership,
service grants, desktop issuer hash, vault and account fingerprint. No database
rows or user files are migrated. Signature/RS256, audience/client, expiry, nonce,
state and S256 PKCE remain enforced. JWKS requests use configured transport, never
the untrusted token's issuer/jku. TLS certificate and hostname verification remain on.

After approved activation, desktop requests discovery from the new HTTPS host.
Before Keycloak switches, the discovered OLD auth/token endpoints remain usable
only inside the window.
After switching, a new login discovers NEW endpoints, so its browser target is
`https://auth.clannad.win/realms/mentorflow/protocol/openid-connect/auth`.
Each callback transaction binds the exact issuer returned by its own discovery;
it does not accept an arbitrary issuer just because a migration is active.

The E109 OLD-primary PREPARED desktop and NEW-primary desktop open that
authorization URL in the system browser from both the startup login shell and
account settings. OLD system-browser selection does not activate migration or
accept NEW discovery/tokens; it permits pre-cutover login without assigning live
window dates. This behavior remains after migration expiry/removal.
Browser launch failure closes the callback and fails login; it never falls back
to embedded authentication. The OS browser must
already trust both the retained OLD HTTPS endpoint (before cutover) and NEW host;
Python CA environment variables do not configure the external browser. The
PREPARED build retains OLD issuer, API, identity and inactive migration defaults.

## Required deployment order — do not skip

1. 05/authorized deployment owner must preserve the old API and loopback proxy,
   same realm/client/signing authority, callback registration, and existing user
   data. Recheck public realm/signing-key agreement before activation; if it has
   diverged, STOP cutover and request review. A's current source is not live.
2. Freeze one approved window for backend and desktop. Set backend
   `MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON` and desktop
   `MENTORFLOW_DESKTOP_ISSUER_MIGRATION_JSON` to the SAME JSON object with integer
   `not_before`, `not_after`, and the exact `authority_sha256` above. This document
   intentionally supplies no active dates. Configuration is not a secret.
3. Deploy the compatible backend before Keycloak hostname changes. The optional
   `deploy/enterprise/staging/rp05/compose.auth-issuer-migration.yml` is an
   Account Core backend-only overlay, not an executable deployment instruction.
   It binds the new primary, old identity namespace and retained TLS transport.
   Supply `MENTORFLOW_E109_ISSUER_MIGRATION_JSON` explicitly. Inspect the effective
   merged config; do not recreate Keycloak or run migrations as a side effect.
   Run the existing strict `oidc-ready` probe in that deployed backend and verify
   old-token login/refresh plus existing account membership before proceeding.
   The invalid-code negative probe must return HTTP 400 `invalid_grant`;
   `invalid_client`, `unauthorized_client` or another error is not readiness.
4. Activate the compatible desktop consumer before cutover. Direct runtime keys:
   `MENTORFLOW_DESKTOP_OIDC_ISSUER=NEW`,
   `MENTORFLOW_DESKTOP_IDENTITY_ISSUER=OLD`, and the same migration JSON.
   For a later authorized build, `release/auth-authority.json` is the single
   build input: change `PREPARED_NOT_ACTIVE` to `ACTIVE` and fill its migration
   object. The build rejects an inactive/expired window and records its input
   hash; the manifest supplies `auth_authority` to both desktop entry paths.
   Current file is deliberately PREPARED_NOT_ACTIVE, so current login defaults
   are not changed. This task does NOT build/install that consumer.
5. CUTOVER EXECUTION GATE: both compatible consumers must be deployed/activated
   and read back, old login must pass, and sufficient window time must remain.
   Otherwise 05 must NOT change hostname. An old installed desktop that has not
   received this change cannot be assumed compatible. This prerequisite prevents
   the server-new/client-old gap; source tests alone do not satisfy it.
6. Only then 05 performs its separately authorized hostname cutover. Confirm NEW
   discovery, actual code+PKCE login, same existing account/vault, API access and
   refresh. Keep old cached tokens accepted until the window deadline. A login
   already in flight during cutover may need one fresh transaction; do not relax
   its state/nonce/issuer checks to rescue it.
7. Keep `register=false` and `forgot_password=false`. Only after 05's NEW-issuer
   live login, real verification email and real reset-password acceptance may A
   enable the separate `release/account-capabilities.json` build switches.
8. After successful cutover, the window expires old-token trust automatically.
   Later remove migration config and move backend JWKS transport to NEW only
   after verifying network/TLS reachability. Retain `identity_issuer=OLD` for
   account continuity; removing that identity alias is a separate data migration,
   not this task. Do not confuse identity namespace with JWT issuer acceptance.

Rollback belongs to the authorized deployment owner: keep the old routing and
pre-change config available, restore compatible consumer/Keycloak settings as a
coordinated unit, and never rewrite realm/users or disable issuer verification.

## Evidence limits

Automated tests use generated RSA keys, mock HTTPS, isolated test databases and
ephemeral loopback callbacks. They cover both issuers, membership continuity,
unknown issuers, signature/audience/client/expiry, bounded window expiry, fixed
JWKS source, desktop callback/PKCE and source preflight before/after cutover.
They are not real SMTP, a deployed-backend check, packaged desktop QA, or a live
new-issuer login. Those remain required at the deployment gate above.

MAIN3 consumer preflight and the next activation prerequisites are recorded in
`reports/enterprise/e109-auth-consumer-preflight-main3-01-20260912.md`.

# ADR: native credential authentication v1

Date: 2026-09-12. Status: **identity SPI implemented; live deployment remains gated**.
Authority: `MF-MAIN3-01-NATIVE-CREDENTIAL-LOGIN-20260912-04` /
`MENTORFLOW_NATIVE_USERNAME_PASSWORD_LOGIN_CONTRACT_V1`.

Batch02 integration authority: `MF-MAIN3-A-BATCH02-INTEGRATION-20260913-01`.
`NORMAL_DESKTOP_LOGIN_UX = NATIVE_IN_APP_USERNAME_PASSWORD`;
`NORMAL_LOGIN_EXTERNAL_BROWSER_REQUIRED = NO`. This also supersedes Batch01's
System Browser normal-login requirement. The [Batch02 receipt](governance/receipts/20260913-main3-batch02-integration.json)
binds integrated native fixture results and independent action-token confirmation;
production login, SMTP and installed-candidate acceptance remain separate gates.

## Decision and current ownership

Normal login is MentorFlow's own username/password form. The desktop sends credentials
once over verified TLS to the configured **API authority**, which delegates verification
over mutually authenticated TLS to an identity-owned bridge. The controlled backend
issues opaque first-party sessions. No OAuth password grant, Keycloak Direct Access
Grant, provider-page automation, browser fallback, new password database, or database
migration is introduced.

Follow-up authority: `MF-MAIN3-01-NATIVE-CREDENTIAL-VERIFIER-20260912-05`.
The repository now contains a Keycloak 26.7.3 `RealmResourceProvider` in
`deploy/enterprise/native-identity-verifier/NativeIdentityVerifier.java`.
It calls Keycloak's credential manager inside the identity authority. The bridge
caller remains `MtlsIdentityVerifier`; this is the same native login architecture.
The isolated acceptance receipt, rather than this ADR alone, establishes tested behavior.
Without explicit bridge configuration, native endpoints return `503 NATIVE_AUTH_NOT_READY`.
No installed candidate or live deployment is qualified by source/fixture acceptance.

Ownership audit at `03d979710a4cde44917c4f10c2f08d2390c8426d`:

| Component | Existing responsibility | Decision |
| --- | --- | --- |
| `desktop/login_shell.py` | Native shell around provider UI | Own actual account/password fields |
| `desktop/in_app_oidc.py` | QWebEngine provider page or E109 system browser | LEGACY / NON_DEFAULT / NOT_USER_VISIBLE |
| `desktop/enterprise_oidc.py` | OIDC protocol, token validation, API workspace and `/auth/me` checks | Reuse API checks and result type; keep strict legacy JWT/refresh handling |
| `desktop/account_backend.py` | Post-authentication local personal-data API | Not a password verifier; unchanged |
| `backend/app/enterprise/identity` | Keycloak JWT/cookie validation and trusted identity directory | Add a separately gated native session boundary |
| `backend/app/enterprise/account_workspaces.py` | Account discovery/initialization before tenant selection | Same issuer+subject and existing authorization |
| Retired local auth modules/tables | Historical local login | Remain retired; no identity duplication |
| Keycloak realm | Passwords, account status, required actions, MFA | Identity SPI uses existing credential and required-action providers |

The former normal path was LoginGate → `InAppOidcLoginDialog` → provider credentials
in QWebEngine, or system browser for OLD/NEW E109 issuers → dynamic IPv4 callback →
OIDC token validation → workspace discovery/initialization → `/auth/me` → local account
bootstrap → local-service readiness → main window. Backend never accepted a native password.

The new normal path is:

```mermaid
sequenceDiagram
    participant U as Native LoginGate
    participant D as NativeCredentialClient
    participant B as Controlled account backend
    participant I as Identity-owned verifier (deployment gate)
    U->>D: Submit username/password; clear masked field
    D->>B: HTTPS POST native/login
    B->>I: mTLS authenticate (no OAuth grant)
    I-->>B: Canonical issuer + subject, verified credential policy
    B-->>D: Short access + rotating refresh session
    D-->>U: AUTH_SUCCESS_BOOTSTRAPPING
    D->>B: workspace discover/initialize + /auth/me
    B-->>D: Current membership and selected workspace
    D-->>U: Validated account result
    U->>U: Locker consent + account metadata/Vault bootstrap
    U->>U: Open main only after local service readiness
```

The user product authority supersedes `03d979...` as a normal-login UX decision, the
provider-form privacy wording, and the old registration/reset-to-provider route.
Legacy protocol tests do not count as native login acceptance. Explicit test injection
in account settings remains non-default; production account switching returns to LoginGate.
Register/reset controls remain visible and disabled with current false capabilities.
An old provider capability cannot reopen the browser route.

## Desktop/backend contract

Only exact paths below are public to authentication middleware. Requests require
HTTPS, JSON with exact keys and a bounded 4096-byte body. No Origin, cookies, or
Authorization header is accepted on these credential/session-operation endpoints.
Responses carry `Cache-Control: no-store`. The backend rejects plain HTTP independently
of the desktop, including native bearer use on existing account routes.

| Endpoint | Request | Response |
| --- | --- | --- |
| `POST /api/enterprise/auth/native/login` | `username`, `password` (transient strings, limits 320/1024) | session v1 |
| `POST /api/enterprise/auth/native/refresh` | `refresh_token` | rotated session v1; old access invalidated |
| `POST /api/enterprise/auth/native/logout` | `refresh_token` | `{"logged_out":true}`; idempotent local session-family revocation |

Session v1 contains exactly `schema=mentorflow.native-session.v1`, `access_token`,
`refresh_token`, `expires_in` (1–300 seconds), and `identity` (`issuer`, `subject`,
`display_name`). Tokens contain independent 256-bit random values with `mfn1_a_`
and `mfn1_r_` prefixes. They are opaque; the prefix alone grants no authority.
Backend stores only SHA-256 token hashes and the trusted principal. A family lasts
at most eight hours, with at most 128 rotations. Access lasts at most five minutes
and no longer than the trusted principal/family. Restart destroys all families.

Native access uses the existing bearer binding and directory checks, not the JWT
validator. Existing JWTs still use the unchanged signature/audience/client/issuer/expiry
validator. The identity issuer is the configured canonical identity namespace (OLD
for E109), never an arbitrary value provided by desktop. API/auth hosts are not
interchanged. Session issuance alone does not authorize any workspace.

## Identity bridge contract and credential boundary

Only an exact configured HTTPS URL ending `/mentorflow-native-identity/v1` is allowed,
without userinfo, query, fragment, or redirects. The backend uses a verified CA and
its own client certificate/private key; no secret enters the desktop or build input.

Authenticate request: exact `operation=authenticate`, `username`, `password`.
Refresh revalidation request: `operation=revalidate`, canonical `issuer`, `subject`,
and a private `verification_handle` issued by a successful password verification.
Success response has exactly `schema=mentorflow.native-identity.v1`,
`outcome=authenticated`, `issuer`, `subject`, `display_name`,
`authentication_strength=password_verified`, `verification_handle`. The strength certifies that the
identity-owned policy has passed; it is **not** a client-controlled claim.
`401` means a generic credential rejection. `outcome=challenge_required` is a hard
stop, not authorization; no password-only success is allowed for accounts requiring
MFA, required actions, federation steps, or recovery.

The implemented SPI enforces raw TLS client-certificate pinning, exact realm/version,
bounded rate limits, Keycloak password validation and its brute-force protector.
It supports the stock password/conditional-2FA flow for local password-only accounts;
custom flows, enabled organizations, client consent, federation, extra credential types,
required actions and policy-evaluation failures stop with a controlled challenge.
Enabled Keycloak required-action providers evaluate triggers, including password age.
No browser flow is silently skipped when it contains an unsupported requirement.

Only a random handle leaves Keycloak. Its credential binding remains inside Keycloak,
and revalidation checks password changes, account/lockout/policy status and realm/user
not-before revocation. Handles expire at the earliest of eight hours and the realm's
positive SSO maximum/idle timeout. This is a fixed lease, so active use cannot extend
it; shorter realm policies can require login sooner than the backend family limit.
The handle never appears in desktop session JSON or `/auth/me`. Refresh preserves
the original authentication time. Account
revalidation is not a new password/MFA event. Required-action/MFA challenge continuation
needs a separately designed native contract before such accounts can be supported.

Passwords exist briefly in the masked field and request memory only. Desktop clears
the field and show-password state on submit/cancel/close, drops worker references at
completion, and sends only allowlisted error text to Qt. Neither side logs request
bodies or passwords or writes them to QSettings, Locker, receipts, or diagnostics.
Python/Qt/HTTP libraries may create transient memory copies; this is not a claim of
reliable memory zeroization. Full-memory dumps, HTTP body capture, debug request
logging and proxy/body telemetry must be disabled in the credential deployment path.

## Storage, revocation and abuse resistance

With explicit Remember consent, existing Windows Credential Locker stores only
access/refresh material scoped by issuer-hash + subject-hash. QSettings retains the
existing nonsecret allowlist. Automatic restore requires both consent and actual
saved refresh material. No consent means the current session remains in memory.
Native refresh rotates and saves the new pair before workspace network work; failure
later cannot roll back to a consumed refresh. Per-account process/Windows locks
serialize desktop recovery. Expired sessions clear only that account's saved material
and return to the native form. Existing OIDC refresh may still recover a previously
saved legacy session without a browser; opaque native tokens never go to an OIDC endpoint.

Logout revokes the native family and all its access/refresh hashes. Refresh replay or
concurrent consumption revokes the entire family, including an in-flight rotation.
Failed identity revalidation also revokes it. Current membership is checked on every
tenant request. Identity-account changes may leave existing access valid for up to
five minutes; immediate global identity revocation/backchannel integration is an open
deployment requirement if the operational policy requires a shorter interval.

The bounded in-memory implementation limits attempts before verification: five per
normalized account, twenty per peer, one hundred total per minute, for both unknown
and known names. It limits tracking/session capacity and stores hashed throttle keys.
Wrong-password and unknown-account responses have identical HTTP status and JSON.
The SPI performs a Keycloak-provider dummy hash for unknown, disabled and locked
accounts and uses Keycloak's brute-force protector for real failures/success.
Response-shape tests and a dummy hash do not establish statistical timing equivalence.

This store is **single process only**. Configuration requires an explicit
`MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS=1` acknowledgement and `WEB_CONCURRENCY=1`.
That flag does not prove the process topology. Multi-worker/replica deployment requires
a shared atomic rotation/revocation/rate-limit store and corresponding tests first.
This delivery adds no persistence migration.

## Alternatives

System-browser Code+PKCE remains a supported legacy protocol but conflicts with the
new explicit product UX. Embedded provider pages also conflict and do not give
MentorFlow native controls. OAuth password grant/Keycloak Direct Access Grant are
explicitly prohibited. Reimplementing passwords in a local application database would
split identity ownership and is rejected. Scraping Keycloak forms/admin APIs is not
an authentication contract and is rejected. A first-party identity-owned bridge keeps
the bounded integration in the existing account system but requires identity-team work.

## Gates before any real native activation

1. Independently review and qualify the identity SPI against the actual deployment's
   exact Keycloak version/provider configuration. Test real normal, unknown,
   disabled, locked, federated, MFA and required-action accounts. Verify comparable
   rejection timing and no provider-form/password-grant fallback.
2. Provision backend-only mTLS credentials using `MENTORFLOW_NATIVE_IDENTITY_ENDPOINT`,
   `MENTORFLOW_NATIVE_IDENTITY_CA_FILE`, `MENTORFLOW_NATIVE_IDENTITY_CERT_FILE`, and
   `MENTORFLOW_NATIVE_IDENTITY_KEY_FILE`. Confirm allowed peer certificates and deny
   direct public/desktop access to the bridge. Do not commit these values or secrets.
   Pass these settings and certificate mounts into the backend process through a
   reviewed deployment override; the existing compose defaults do not activate them.
3. Qualify actual single-process topology, rate limits and restart behavior; otherwise
   provide the shared store first. Verify server time, trusted proxy scheme/client-IP
   handling, end-to-end TLS and redacted diagnostics. A forged forwarded header must
   not establish the HTTPS trust boundary.
4. Test native end-to-end with real backend/identity and existing issuer+subject
   membership. Exercise successful bootstrap, wrong/unknown password, network loss,
   Locker consent/rotation, replay, logout, expiry and denied workspace. Qualify
   renewal of tokens used by an already-open main window and the required revocation
   latency; startup recovery tests alone are not a long-session qualification.
5. Build and inspect a desktop candidate containing `desktop.native_auth` (added to
   the delivery spec) and the current `app.enterprise.identity.native` dependency
   of the rebound runtime (added to exact auth-source binding); stage compatible backend and desktop together. The old installed
   browser candidate is not evidence for native UX. Keep registration/reset disabled
   until their own native contracts and capability acceptance pass.
6. Obtain the next task's concrete live activation authorization. This source delivery
   changes no Keycloak hostname, nginx/DNS, SMTP, live capability, or migration dates.
   `release/auth-authority.json` remains `PREPARED_NOT_ACTIVE`, `migration:null`.
   Issuer cutover is a separate operation; no ≤48h window starts here.

Reference guidance: [OWASP authentication controls](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
for generic failures and throttling; [OWASP session controls](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
for session lifecycle and rotation. These references do not certify this
identity adapter or replace its deployment security review.

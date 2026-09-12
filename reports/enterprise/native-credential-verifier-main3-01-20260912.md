# Native credential real verifier acceptance — MAIN3 / 01

Dispatch: `MF-MAIN3-01-NATIVE-CREDENTIAL-VERIFIER-20260912-05`.
Task: `MENTORFLOW_NATIVE_CREDENTIAL_REAL_VERIFIER_AND_LOGIN_ACCEPTANCE_V1`.
Start: `5eaeff56d8cf494a4d262348efbc5a3ed88bcbc2`, branch `worker/main3/01`.
The delivery commit is the commit containing this report; its exact SHA is in the final receipt.

## Isolation and scope

Phase 0 confirmed the clean existing office before edits:

| Field | Value |
| --- | --- |
| Toplevel | `E:/Codex/Data/worktrees/8831/MentorFlow` |
| Git directory | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1` |
| Common directory | `E:/Codex/Projects/MentorFlow/.git` |
| Index | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1/index` |
| Starting branch / HEAD | `worker/main3/01` / `5eaeff56d8cf494a4d262348efbc5a3ed88bcbc2` |
| Starting status | Clean; branch owned by this worktree |
| Office reuse / new worktree | YES / NO |

No branch switch, reset, rebase, merge, shared-ref mutation, live hostname/DNS/nginx,
production DB, SMTP, account capability, remote push, or GitHub Actions operation occurred.
Root performed all writes; no child agents were created. Other workers were read only.

## Implemented behavior

The existing call chain is retained:

`LoginGate native form → NativeCredentialClient → HTTPS account-core native/login →
MtlsIdentityVerifier → private certificate-pinned Keycloak RealmResourceProvider →
Keycloak credentialManager.isValid → native session family → workspace discovery/
initialization and /auth/me → Windows Credential Locker with consent → account metadata/
local Vault bootstrap → existing main-window gate`.

The identity-side SPI is compiled and exercised against Keycloak **26.7.3**, with JDK 21.
The password is validated by Keycloak's own credential provider. Backend implements
neither password hashing nor an OAuth password grant. The realm disables Direct Access
Grants for the fixture client, and no token-grant/admin-login path is called by this fixture.

The private bridge now requires an opaque identity verification handle for refresh.
Only Keycloak stores the credential binding; password change/disable/lockout/policy and
realm/user not-before checks can invalidate revalidation. Handles obey a fixed lease
bounded by eight hours and the realm's positive SSO maximum/idle settings. No handle
or credential hash appears in desktop session JSON or `/auth/me`.

The SPI requires the exact realm/version, raw TLS client certificate and approved leaf
SHA-256 pin. It rejects forwarded-certificate substitutes. It invokes Keycloak brute-force
handling and required-action trigger providers, and fails closed for unsupported flow
shapes, configured/required MFA, federation and other unsupported account policy.
The backend streams at most 8192 response bytes and enforces a ten-second total deadline.

A regression check exposed Windows timestamp conversion rounding: in 100,000 local
comparisons, 1,205 `datetime.now().timestamp()` values were fractionally greater than the
immediately following `time.time()` (maximum 0.238 microseconds). New bridge issuance
uses integer seconds, with expiry rounded down as well; no future/expiry tolerance was
added. The dedicated boundary regression reproduces the rounding condition.

## Real isolated acceptance

The runner is `scripts/native_identity_fixture.py`. It uses a fresh local Keycloak/H2
realm, random credentials, private mTLS, a real HTTPS account-core server, isolated
SQLite workspace storage, and the actual native Qt form. Realm import is removed after
startup, private keys are deleted and owned processes are stopped. No verifier is mocked.

The Qt checks exercise real Windows Credential Locker and QSettings. Authentication,
server workspace/me checks and account metadata/Vault bootstrap are real. Only the
business window renderer and local inference service are substituted after successful
bootstrap; the actual `_show_main` guard executes. This is not installed-binary UAT,
multi-replica qualification, PostgreSQL RLS acceptance, or long-running main-window renewal.

The final machine-readable receipt and artifact manifest are stored under:
`E:/Codex/Temp/MF-MAIN3-01-NATIVE-CREDENTIAL-VERIFIER-20260912-05/`.
Final passing fixture: `E:/Codex/Temp/mf-native-c04669c59d45/receipt.json` — **28/28 checks**.
Deployable artifact: `mentorflow-native-verifier-26.7.3.jar` in the delivery directory,
SHA-256 `2c13c985848f62f00fd7250c5fe7b02793a0dcb3b9ece97fda0f0094e9149a0e`.
The artifact is copied from the passing fixture and contains no fixture-control classes.
`delivery-manifest.json` records the source hashes, JAR entries and receipt location.

Final regression results: **225 pytest cases passed** — 162 desktop, 57 backend and
6 build/source-binding cases. Logs are `desktop-tests.log` and
`final-backend-build-tests.log` in the delivery directory. The existing Starlette/httpx
deprecation warning remains; Java also reports its available Jackson `fields()` API as
deprecated. Neither was suppressed by changing dependencies or security checks.
Python runtime syntax was also checked against Python 3.11.15; `git diff --check` passed.

The real fixture checks:

* Anonymous and wrong-service-certificate denial; real discovery and credential provider.
* Wrong password, unknown account and disabled account produce the same native UI message.
* Real Keycloak brute-force lockout; temporary password, email verification, configured OTP,
  required MFA setup, mandatory MFA flow and password-age policy all stop authentication.
* Native form → real verifier → MentorFlow session → existing workspace → account bootstrap
  → main gate; existing membership is reused.
* Windows Credential Locker holds the returned access/refresh pair only; QSettings does
  not contain the password. The form clears its password; fixture Locker entries are removed.
* Real refresh/restore, rotation, logout/revocation, access/family expiry, password-change
  revocation and disabled-identity refresh rejection.
* Missing verifier, a real mTLS peer that stalls, and stopped Keycloak all fail closed.

Expiry assertions advance only the disposable backend's injected clock; they do not
change the host clock or wait eight hours. Identity verification and issued sessions
are real in those checks.

Required-action UX follow-ups are `VERIFY_EMAIL`, `UPDATE_PASSWORD` (including temporary
and expired password), and MFA setup/OTP/mandatory MFA continuation. None is bypassed.
The normal UI opens no system browser or provider QWebEngine login.

## 04 token authority audit

Read-only evidence: `E:/Codex/Temp/mf-e109-sec04-15df052cc2a2-result.json` and worker 04's
`scripts/e109_native_lab/keycloak_checks.py` / `NativeTokenFixture.java`. No 04 fixture
or worker process was run or changed. Its credential-grant probes were not reused.

| Case | 04 recorded result | This delivery |
| --- | --- | --- |
| Verification concurrent single-use | Two successful verification events, both HTTP 302 | Not fixed; follow-up required |
| Verification used after rearm | Used token causes one new successful verification event | Not fixed; follow-up required |
| Reset concurrent loser | One success; other response HTTP 500; optimistic/stale transaction exceptions | Not fixed; follow-up required |

Exact identity components / owner for the next bounded task (Keycloak 26.7.3):

1. `services/src/main/java/org/keycloak/authentication/actiontoken/verifyemail/VerifyEmailActionTokenHandler.java`,
   `handleToken` (lines 73–119), together with
   `services/src/main/java/org/keycloak/authentication/actiontoken/AbstractActionTokenHandler.java`,
   `canUseTokenRepeatedly`. The verify-email handler inherits the repeatable-token default;
   its state check depends on current email/required-action state. This source explains
   why a rearmed action is not an irrevocable consumed-token record. [Exact source](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/actiontoken/verifyemail/VerifyEmailActionTokenHandler.java).
2. `services/src/main/java/org/keycloak/services/resources/LoginActionsService.java`,
   lines 708–717, conditionally checks prior use and sets the later invalidation note;
   atomic claim/consume behavior belongs to this native action-token pipeline and its
   `LoginActionsServiceChecks` / `AuthenticationManager` store lifecycle. [Exact source](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/resources/LoginActionsService.java).
3. `services/src/main/java/org/keycloak/authentication/actiontoken/resetcred/ResetCredentialsActionTokenHandler.java`,
   lines 60–74, enters the reset flow and marks the token nonrepeatable. The controlled
   loser response also needs the Keycloak required-action/password update transaction
   boundary (`UpdatePassword`, `JtaTransactionWrapper`) reviewed against 04's captured
   optimistic-lock exceptions. The exact throw/commit-to-response mapping is a follow-up,
   not established by this read-only audit. [Exact handler](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/actiontoken/resetcred/ResetCredentialsActionTokenHandler.java).

Owner: **identity/token authority (01 or a separately assigned identity owner), not 04's
nginx/RLS ownership**. These are separate action-token handlers and transaction boundaries
from the new credential verifier. Its `mf-native:` session-handle keys neither intercept
nor consume verification/reset action tokens. Changing the verifier cannot repair those
handlers; a complete atomicity proof would expand this task. Therefore
`TOKEN_ATOMICITY_FOLLOWUP_REQUIRED = YES` and no token fix is claimed. Next validation
should rerun only those three failing cases, with at most one SUCCESS and no HTTP 500 loser.

## Next activation gates

Follow the exact deployment prerequisites in
`deploy/enterprise/native-identity-verifier/README.md` and the updated native ADR:
review the production JAR, raw private mTLS/pins/ACLs and target realm policy; prove
single-instance topology; qualify revocation latency, renewal and certificate rotation;
stage native-compatible backend/desktop artifacts; obtain a separate live activation
authorization. Default configuration remains off. A fresh grant must never be substituted.

The fixed identity lease can require login earlier than the backend's eight-hour family.
Global Keycloak session logout without not-before revocation is not wired to native
sessions; existing access can remain usable for up to five minutes. Operational policy
must accept that bound or require a separate backchannel change before deployment.

`release/auth-authority.json` remains `PREPARED_NOT_ACTIVE`, `migration:null`.
OLD canonical identity / NEW auth hostname separation, API authority, legacy issuer
allowlist and dynamic IPv4 callback are unchanged. No migration window was started.

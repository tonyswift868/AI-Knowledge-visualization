# Canonical password reset completion — source/fixture receipt

Dispatch: `MF-MAIN3-01-RESET-COMPLETION-SIGNAL-20260913-12`.
Task: `MENTORFLOW_CANONICAL_PASSWORD_RESET_COMPLETION_SIGNAL_V1`.
Starting branch: `worker/main3/01`.
Starting HEAD: `e6a553ee178fa679cd7e96ed8dd52ef42861dbb0`.

Status: **PASS_CANONICAL_RESET_COMPLETION_SIGNAL** for source and disposable fixtures.
No live server, hostname, DNS, SMTP, account, deployment or capability mutation occurred.
`release/account-capabilities.json` still has both capabilities false.
`release/auth-authority.json` remains `PREPARED_NOT_ACTIVE`, migration null.

## Actual identity evidence

The initial observer recorded all real fixture user events before selecting a production seam.
Keycloak **26.7.3** emitted both `UPDATE_PASSWORD` and `UPDATE_CREDENTIAL`, with
`credential_type=password`, client `mentorflow-desktop`, the actual synthetic user ID,
and no user session ID. At event delivery the transaction was active, and the
authentication session still carried required action `UPDATE_PASSWORD`. After commit,
that required action was absent. Successful event delivery itself is therefore insufficient.

The checked-in [evidence JSON](MAIN3_01_RESET_COMPLETION_EVIDENCE_20260913.json)
contains actual event metadata, committed/rollback observations, receipt paths and hashes,
and every final H2/PostgreSQL check. No passwords, action JWTs, cookies or URLs containing
tokens are included. Event detail **keys**, not potentially sensitive detail values, are recorded.

The exact upstream implementations corroborate the fixture result:
[UpdatePassword 26.7.3](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/requiredactions/UpdatePassword.java)
emits the current credential event and deprecated password event;
[DefaultKeycloakTransactionManager 26.7.3](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/DefaultKeycloakTransactionManager.java)
invokes after-completion commit callbacks only after its primary transactions commit.

## Authority and correlation

The existing `AtomicResetPassword` delegates the actual credential update and password policy
to Keycloak. Only `RequiredActionContext.Status.SUCCESS` enlists the completion callback.
The callback publishes the positive fact after the enclosing transaction commits. Rollback
and prepare/commit failure publish nothing. No HTML, HTTP status, redirect or client assertion
is used to determine whether the password changed.

1. Desktop prepares its existing random OIDC state and PKCE transaction.
2. Before navigating to forgot-credentials, Desktop asks Backend to register an observation.
   Backend uses its existing certificate-pinned mTLS identity bridge. Keycloak creates a fresh,
   unrelated 256-bit opaque `transaction_id`. The request contains no account identifier.
3. A verified reset action token identifies its original authentication session through its
   signed compound session ID. Only that original server-side session supplies the OIDC state.
   An arbitrary state on the redemption URL or a fresh browser session is ignored.
4. A one-time binding fixes the watch to the exact native action key, user, realm and client.
   New completion storage contains SHA-256 hashes of state, ticket, action key and subject;
   it never contains the raw reset JWT or password. The pre-existing native anti-replay store
   remains unchanged. An action issued before the watch cannot bind it.
5. Only a committed update matching that binding can insert the completion marker. The insert
   is atomic. Reads do not consume or re-arm either the reset action or the completion fact.

Each new forgot-password attempt must create a **new OIDC transaction/state and watch**.
Do not register a watch after opening the emailed action or reuse state on resend. Duplicate
registration is rejected. Another action for the same user cannot satisfy the new watch.

## Consumer contract

Both public observation endpoints require verified HTTPS native requests, reject browser
Origin/cookie/Authorization channels, bound JSON bodies, reject duplicate/extra fields,
rate-limit requests, and return `Cache-Control: no-store`. Tickets travel in POST bodies,
never URL paths or query parameters. The identity SPI remains behind the existing raw TLS
client certificate pin. No public endpoint can write `COMPLETED`.

| Endpoint | Request | Response |
| --- | --- | --- |
| `POST /api/enterprise/auth/reset/watch` | `{"oidc_state":"<fresh OIDC state>"}` | schema, opaque transaction_id, PENDING |
| `POST /api/enterprise/auth/reset/status` | `{"transaction_id":"<opaque observation ticket>"}` | same schema and transaction_id, status |

Schema: `mentorflow.reset-completion.v1`.

| Status | Meaning |
| --- | --- |
| PENDING | No committed matching password update has been attested. |
| COMPLETED | The bound password update committed; stable repeated reads within retention. |
| EXPIRED | The 900-second observation lease elapsed without completion. |
| INVALID | Unknown ticket or lost/evicted/expired observation metadata. |

An invalid/expired action token never yields completion; the independent observation can
remain PENDING until its own deadline. Completed metadata is retained for at most 4500 seconds
from watch registration. Cache loss/restart returns INVALID and never reconstructs success
from historical user events. This is an ephemeral observation mechanism, **not a durable
outbox**: a crash between credential commit and marker publication can lose the positive
notification. The user can log in with the new password or start a fresh reset; do not retry
or infer completion from a page. Missing original authentication sessions also fail closed.

Desktop strictly validates the schema and matching transaction_id. It polls Backend without
exchanging any reset callback for tokens. Its local terminal outcome is
`ENTERPRISE_PASSWORD_RESET_COMPLETED`, with fixed text:

> 密码已重置，请使用新密码登录 MentorFlow。

The main application clears the reset surface/password input and returns to LOGIN_READY.
There is no automatic login, bootstrap or main-window transition after reset. A generic error,
late login result, stale generation or provider-supplied success text cannot trigger this message.

## Validation

- Final focused Backend + Desktop + native-login regression command: **84 passed**.
- Real Keycloak/H2: **103/103 checks passed**.
- Real Keycloak/disposable PostgreSQL: **103/103 checks passed**.
- Both fixtures exercise real Desktop consumer → HTTPS Backend → mTLS SPI → committed native
  credential state, with no SMTP and no OAuth password/direct grant.
- Cases include invalid signature/malformed/wrong-email/expired token, used replay, parallel
  redemption and same-form POSTs at 2/5/10 concurrency, failed policy then correction, real
  rollback and prepare failure, pending while commit is blocked, completion after release,
  idempotent reads, old action versus new watch, lease expiry, restart and no auto-login.
- All production JAR entries match between final H2 and PostgreSQL runs. Neither production
  JAR contains the fixture observer/control classes or service registration.
- Both final fixtures stopped Keycloak, backend and disposable database and removed private keys.

Commands use `scripts/native_identity_fixture.py --action-tokens`, with exact Keycloak zip/JDK
and Desktop Python paths; add `--postgres-bin` for the fresh PostgreSQL variant. The checked-in
evidence records the local toolchain and output paths. The fixture's isolated password policy
also tests password expiry; it does not change the product realm policy.

A supplemental old OIDC surface suite produced 6 passes and 2 failures. Both failing tests
expect `login_enterprise_account_in_app_from_env` on `desktop/account_settings_page.py`.
That helper is already absent at the exact starting HEAD, and both the account page and these
tests are unchanged by this delivery. This receipt does **not** claim that suite is green.

## Integration and next real-email gate

03's password-pair theme work remains on its branch. Integrate this completion protocol with
that UI while retaining native normal login from the current worker branch. In overlapping
Desktop hunks, retain `ResetCompletionClient.begin` **before** navigation, polling independent
of callback/page text, the canonical terminal code, generation guards, and the return to login.
The one-line LOGIN_READY detail support is the same behavior already present in 03's work.
Do not restore browser/OIDC normal login while resolving those overlaps.

Before 04's real-email acceptance:

1. Integrate and build Backend, Desktop and the exact production Keycloak 26.7.3 SPI together.
2. Keep the SPI route private and the existing mTLS pin, TLS, issuer/client/audience checks,
   token signature/expiry/nonce/single-use checks and password policy intact.
3. Confirm the emailed native reset token retains the original authentication-session compound
   ID, state, authenticated user and `mentorflow-desktop` client. Test same- and fresh-browser
   opening; missing sessions must produce no completion.
4. Verify the deployed API path reaches the new narrow endpoints and status survives the actual
   cluster routing/observation lease assumptions. Cache restart is intentionally INVALID.
5. Apply 03's double-password UI and verify the fixed success message and return to native login,
   including cancel, resend, replay, rollback and no automatic session creation.
6. Only the separately authorized real-email gate may enable account capabilities/send SMTP.
   No live SMTP, capability enablement or issuer migration window is part of this receipt.

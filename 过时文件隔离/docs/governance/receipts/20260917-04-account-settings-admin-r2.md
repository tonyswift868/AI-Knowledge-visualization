# MF-R2-04-ACCOUNT-SETTINGS-ADMIN

Status: **PARTIAL_READY_FOR_01_CONTRACT_AND_A_CONFIGURATION**.
Base: `fad2c1abbce3be44cc69d7a6f9c1b4dbf893244a`.
Branch: `worker/main3/04-settings-admin-r2`.
Previous clean delivery remains on `worker/main3/04-data-r1`.

## Independent work delivered

- Settings now has an explicit **账号与安全 · 邮箱状态 / 修改密码** entry;
  the existing account page is scrollable, with a discoverable password form.
- Password requests run off the UI thread. The existing authenticated endpoint
  must return `PASSWORD_CHANGED`; failures/uncertain outcomes do not claim
  success. Confirmed success calls `LoginSession.forget_password` for the
  captured account profile only. Other accounts and learning data are untouched.
  A local credential cleanup failure is distinguished from a failed server change.
- Email presentation and in-page code controls use callback seams. Without a
  canonical server adapter they stay unavailable; no cached/null `/auth/me`
  value is presented as an unbound or verified email.
- Admin overview refreshes on entry, once per minute while visible, and on
  explicit refresh. Hidden pages do not poll. Missing counters show `—`, and
  incomplete statistics are explicitly unavailable rather than a successful
  refresh with `None` values.
- A successful Keycloak discovery observation is no longer overwritten by a
  missing/failed statistics reader. `identity_statistics` separately reports
  `ADMIN_READ_OK`, `ADMIN_READER_NOT_CONFIGURED`, or `ADMIN_READ_FAILED`.
- With no separate reader configured, overview reuses the already approved
  lifecycle Admin API connection for **GET /users only**. It verifies the same
  realm, observes lifecycle expiry, and never owns/closes the shared connection.
  Dedicated reader configuration remains supported independently of lifecycle,
  with optional paired mTLS certificate/key references. No new credential store.
- Health and identity reads run concurrently within the existing ten-second
  route deadline, followed by bounded database aggregation. Every refresh reads
  current user/verification counters; there is no permanent statistics cache.
  Server-side SUPER_ADMIN authorization is unchanged.

## Read-only runtime findings and A application requirements

Observed container: `mentorflow-e1-preview-backend-1`, VPS `89.45.45.28`.
Only allowlisted configuration references and presence flags were printed.

1. `MENTORFLOW_SMTP_CONFIG` is absent from container environment; the mounted
   runtime entrypoint does not insert it. The existing email registry probe
   consequently returns `EMAIL_AUTHORITY_NOT_CONFIGURED`. This is a real missing
   readiness configuration, not evidence that delivery succeeded or failed.
2. `MENTORFLOW_ADMIN_OVERVIEW_CONFIG` is also absent. Existing code therefore
   never created its reader. The approved lifecycle configuration is present
   at `/run/mentorflow/lifecycle/config.json`, using internal mTLS administration
   at `https://keycloak:8443`, realm `mentorflow`. The new borrowed-reader path
   reuses that connection, resolver and privileges; no role grants are added.
3. Identity transport issuer is public `https://auth.clannad.win/realms/mentorflow`;
   its transport override is empty. Do not copy internal lifecycle configuration
   blindly into the old dedicated-reader contract: that contract deliberately
   validates the exact transport authority. Borrowing the approved connection
   handles the internal mTLS path without changing identity namespace.

For email readiness A must mount the **existing controlled Brevo resolver/config**
into the backend and set `MENTORFLOW_SMTP_CONFIG` to its container-visible absolute
path, following `deploy/enterprise/auth-email-security/smtp.example.json`.
`SMTP_SECRET_FILE`/`SMTP_SECRET_ENV` must resolve only inside the backend, with
private file permissions. Do not put a credential in Desktop config or this
receipt. Reuse `observability.health._email_probe`; do not create another mail
monitor or infer health from old receipts. It performs TLS/AUTH/NOOP, never sends
mail. A can then inspect readiness and the authorized overview after deployment.
No controlled SMTP reference suitable for this container was established here;
this remains an explicit configuration application requirement, not a live PASS.

Worker 04 did not apply configuration, deploy, change roles/passwords/email,
create accounts, send mail, operate GUI, or touch migrated learning data.

## Remaining 01 contract dependency

Baseline native `/auth/me` builds profile email from `binding.session.principal`;
native bearer sessions have no such principal, and there is no verified-email
field in that view. It cannot truthfully populate this page.

After the coordinator supplies the exact 01 commit and
`20260917-01-account-security-r2.md`, wire its authenticated current-account API
to these **local UI callbacks**, not invented server routes:

- `read_account_security()` returns normalized `{email: str, email_verified: bool}`;
  an actually unbound email is an empty string. Unknown/error must raise.
- `request_email_code(address)` succeeds only after confirmed provider/API
  acceptance; adapter retains any required transaction reference in memory.
- `verify_email_code(code)` succeeds only on canonical verification completion;
  UI then re-reads current-account state. Already bound email is read-only;
  complete email replacement is outside this task.

Adapter must validate actual response states and sanitize errors before returning;
ambiguous network results must raise and are not automatically retried. No code,
proof or credential is persisted by the page. These callbacks are deliberately
not bound in `MainWindow` until the formal API contract exists. Therefore **real
tonyswift email display/bind/verify is not claimed complete** in this delivery.
Also reconcile the password completion response with 01 if its contract changes.

## Verification

Isolated scratch: `E:/Codex/Temp/MF-R2-04`; headless Qt, disposable local databases,
no SMTP or real identity operations. **30 distinct tests pass, zero failures**:

- `backend/tests/test_admin_overview.py`: 8 tests, including new registration and
  verification refresh, concurrent reads, missing authority, read-only connection
  reuse/ownership, same-realm denial, mTLS references and MEMBER denial.
- `backend/tests/test_email_health.py`: 16 existing readiness/fail-closed tests.
- `desktop/tests/test_account_security_r2.py`: 6 tests, covering page callbacks,
  absent email authority, password success/failure cleanup and admin visibility.

Final focused same-realm test was rerun after adding that guard. Logs:
`tests-backend.log`, `tests-ui.log`, `tests-authority.log` under the scratch root.
One preexisting Starlette/httpx deprecation warning; no builds or large suites.

## User acceptance after A/01 integration

1. Settings -> 账号与安全: the signed-in account shows its actual email/verification
   state; any necessary verification code is entered on this page.
2. Modify password using current password and matching new entries: success
   removes only this account's saved login credentials, without losing plans.
3. Enter/revisit admin overview after registration/verification: counts refresh;
   service observations and unavailable reasons remain truthful.

待调度问题：请在独立改动接纳后转交 01 的 exact 邮箱接口提交；A 应用现有受控 SMTP
配置引用并复验 readiness。本轮无其他用户决策需求。

# MF-R2-04 account email client wiring — follow-up

Status: **SOURCE_READY_FOR_A_INTEGRATION_AND_USER_ACCEPTANCE**.
Base: `79d24e8f593af90904b4681683ea4cb4ba1947b5`.
Branch: `worker/main3/04-settings-admin-r2`.
Required Backend/SPI delivery: `664993e4351085d4eed6ee9915943fe0a948b264`.
Contract read from that exact commit's
`docs/governance/receipts/20260917-01-account-security-r2.md`.
No 01 backend code was copied/cherry-picked into this worker branch.

This closes the **client API dependency** recorded in the earlier
`20260917-04-account-settings-admin-r2.md`. Its SMTP application requirement was
reported resolved by the coordinator; Worker 04 did not recheck or modify it.

## Actual wiring

`MainWindow` creates one window-owned `AccountSecurityClient` over its existing
authenticated rehydration transport. The account page callbacks now call:

- `GET /api/enterprise/auth/lifecycle/email` for canonical profile, normalizing
  only null email to empty string and validating all four response fields.
- `POST .../email-code/request` with exactly `{email}`. Only 202 plus the exact
  CODE_SENT/transaction/expiry/cooldown contract is accepted. The page says the
  request was submitted, never that the inbox received it.
- `POST .../email-code/verify` with exactly `{transaction_id, code}`. The code
  stays a six-digit string (including leading zeros). Only EMAIL_VERIFIED and
  verified canonical profile count as completion; the page then re-reads email.
- Existing `POST .../change-password` through the same adapter; PASSWORD_CHANGED
  remains mandatory. Both error envelopes are parsed. Same-password feedback is
  exactly **新密码不能与当前密码相同**, in the password form. Successful change
  retains the previous captured-account credential cleanup callback.

The transport preserves bearer and existing organization/workspace headers;
extra scope/subject/role fields are rejected locally. It renews an expired session
before a request if necessary, but **never replays these mutations on 401 or
network uncertainty**. Existing read/sync renewal behavior is retained.

## Form and lifecycle behavior

- Unbound: initial binding input. Bound/unverified: authoritative email shown,
  same-email verification only. Verified: actual status shown, no binding action.
- 60-second resend delay and 600-second local code deadline, with server limits
  remaining authoritative. No background mail request or automatic resend.
- Transaction reference is adapter memory only; the page has no persistent
  transaction/code store. Cancel, hide, close or account-profile change invalidates
  the operation generation and discards the reference and entered code.
- A completed network operation from a previous generation cannot overwrite a
  later account/page. Cancelling during SMTP submission cannot resurrect a code
  transaction when its response arrives. Account switching constructs a new
  window/client; old callbacks are disabled if a page profile is replaced.
- Password and email errors stay in their respective forms, using known safe
  messages. Unknown/ambiguous results never show success; refresh/retry is explicit.
- No browser handoff, new token generator, email replacement product, credential
  persistence, monitoring subsystem or new sync behavior was introduced.

## Targeted checks

**23 passed, 0 failed**, headless synthetic HTTP/Qt checks:

- `desktop/tests/test_account_security_client_r2.py`: exact paths/headers/bodies,
  null/unbound and bound/verified display, fresh read after verification, error
  envelopes, same-password feedback, expiry/cooldown, false-success rejection,
  no mutation replay, session/scope denial and close/switch/cancel races.
- Updated affected `desktop/tests/test_account_security_r2.py` callbacks and form
  assertions.
- Only the relevant existing transport tests: authority override, renewal/context
  fencing and late response rejection. The previous unrelated 30-test suite was
  not mechanically rerun.

Log: `E:/Codex/Temp/MF-R2-04/tests-email-final.log`.
`git diff --check` passed. No live login, SMTP, real email/password mutation, GUI
operation, deployment, migrated-data access or model download was performed.

## Integration and actual acceptance still required

A must integrate this follow-up after `79d24e8`, together with 01's exact delivery;
01's Backend and native SPI application requirements still apply. This worker
does not claim live email display, receipt or user verification from fixtures.

User checks: (1) existing account displays its real email/verification state;
(2) an eligible account receives a code and verifies entirely in the page;
(3) same-password rejection and successful current-password change show correct
feedback and preserve other accounts and learning data.

Remaining 01 contract mismatch: **NONE**. No additional dispatch decision needed.

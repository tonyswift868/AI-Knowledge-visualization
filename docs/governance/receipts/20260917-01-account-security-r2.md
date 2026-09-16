# MF-R2-01-ACCOUNT-SECURITY — Backend interface receipt

Base: `fad2c1abbce3be44cc69d7a6f9c1b4dbf893244a`.
Owner branch: `worker/main3/01-account-security-r2`. Delivery SHA/tree are in the final Git receipt.
Scope: Backend/identity source and isolated fixtures only; no Desktop, admin overview, deployment, real account/data mutation or real SMTP.

## Authenticated account settings contract (04/A)

All routes below use prefix `/api/enterprise/auth/lifecycle`, HTTPS, native `Authorization: Bearer mfn1_a_…`, and existing organization/workspace headers when selecting a membership. They reject browser Origin/cookies and revalidate the current product actor/membership. No public request accepts subject, account ID, verified flag or roles. Responses have `Cache-Control: no-store`.

| Route | Exact request | Success response |
| --- | --- | --- |
| `GET /email` | No body | 200 `{email: string|null, email_verified: boolean, can_bind: boolean, can_verify: boolean}` |
| `POST /email-code/request` | `{email: string}` | 202 `{state: "CODE_SENT", transaction_id: string, expires_in: 600, resend_after: 60}` |
| `POST /email-code/verify` | `{transaction_id: string, code: string}` | 200 `{state: "EMAIL_VERIFIED", email: string, email_verified: true, can_bind: false, can_verify: false}` |
| `POST /change-password` (existing) | `{current_password: string, new_password: string}` | 200 `{state: "PASSWORD_CHANGED"}` |

`GET /email` reads Keycloak through the existing pinned mTLS identity provider, not cached login claims or a second profile store. Existing register-created accounts therefore use their existing Keycloak email/verified fields. Empty email means initial binding is available. Bound/unverified email means verification of that same email only. Bound/verified email requires no action. Case-insensitive comparison allows the same email spelling; existing authority spelling is retained. This round has no email-change endpoint.

Numeric code is a six-digit string (keep leading zeros). Transaction ID is opaque, 43 URL-safe characters. The request sends through the existing Keycloak realm SMTP configuration. An initial binding does not change the authoritative email before code redemption. SMTP submission is not delivery confirmation; UI must not report inbox receipt from HTTP 202.

Existing error serialization is retained: local input/current-password/same-password errors return `{code, message}`; raised identity/provider/rate/session errors use the application's `{error: {code, message, details, request_id}}` envelope. Read `payload.error ?? payload`. UI displays `message`, remains on the relevant form, and does not auto-login:

| HTTP | Code | Handling |
| --- | --- | --- |
| 400 | `EMAIL_INPUT_INVALID` | Correct email input. |
| 400 | `EMAIL_CODE_INVALID_OR_EXPIRED` | Incorrect/expired/used code, wrong transaction/subject, or exhausted five-attempt limit; request another code. |
| 409 | `EMAIL_ALREADY_BOUND` | Existing email cannot be overwritten; refresh profile and verify that email. |
| 409 | `EMAIL_ALREADY_VERIFIED` | Refresh authoritative profile. |
| 429 | `EMAIL_CODE_RATE_LIMITED` | Wait before requesting another code. |
| 429 | `RESET_RATE_LIMITED` | Existing shared request throttle; retry later. |
| 503 | `ACCOUNT_ACTION_UNAVAILABLE` | Provider/send/commit confirmation unavailable; do not infer success. |
| 400 | `CURRENT_PASSWORD_INVALID` | Correct current password. |
| 400 | `NEW_PASSWORD_SAME_AS_CURRENT` | Exact message: `新密码不能与当前密码相同`. Correct new password. |
| 400 | `PASSWORD_INPUT_INVALID` | Existing 8–128 character policy. |
| 401/403 | Existing session/membership/transport errors | Follow existing authentication handling. |

Password change still verifies the current password with the existing native verifier, matches issuer+subject against the actor, and calls the existing private Keycloak administration operation. It does not require an email code, introduce password history, or weaken identity-owned required-action/MFA policy. Same-password rejection happens after successful current-password verification and before mutation. This does not authorize bypassing a realm policy that prevents an account from logging in in the first place.

## Identity/security and deployment scope

- Private `email-binding-profile/request/verify/status` operations remain behind the existing raw backend-certificate pin and realm allowlist. Only Backend passes the authenticated subject.
- Email-binding and reset-code purposes have separate realm-scoped key namespaces. Codes/proofs cannot cross purposes or subjects. Only code digests are stored.
- Existing Keycloak single-use ledger supplies replay claims; 600-second expiry, five wrong attempts, 60-second subject/address cooldown and ten sends per subject/hour apply. Verification is checked against the original email and password credential generation.
- A pessimistic lock on the existing local Keycloak user row serializes initial bindings; compare the current stored email under that lock before setting it. Another request cannot overwrite an already bound email. No new schema or identity system.
- Verification success requires a separate after-commit fact and a fresh authoritative profile. Rollback never publishes completion; a consumed failed intent requires a fresh code. Verification issues no session and changes no canonical account ID/workspace/data.
- Forgot password remains unchanged: anonymous `/reset-code/request` → `/reset-code/verify` → `/reset-code/complete`; only an enabled account with verified bound email can receive recovery code. Existing anti-enumeration and same-password retry behavior remain.
- A must rebuild/restart Backend and the Keycloak native SPI together. `scripts/build_native_identity_verifier.py` now includes `EmailCodeBinding.java`. Reuse existing lifecycle enable/config, mTLS pin and realm SMTP configuration; no new secret, DB migration, issuer change or product capability flag. No deploy was performed by 01.
- Keycloak local user storage is required, consistent with the existing native verifier's federation rejection. UI integration belongs to 04. Real SMTP/GUI/user acceptance are not run here.

## Validation

Focused Python contracts: 26 passed (`test_account_email_binding.py`, `test_change_password_binding.py`, `test_email_code_reset.py`); native authentication/security regression: 45 passed (`test_native_auth.py`). One existing Starlette/httpx deprecation warning. Initial new test mock incorrectly combined empty email with verified=true after completion; corrected the mock to match the identity contract, without weakening production validation.

`scripts/account_security_fixture.py`: 19 passed against a fresh local Keycloak 26.7.3, real backend HTTP + pinned mTLS and in-memory loopback SMTP. Includes authoritative no-email read, current-password verification without email, initial bind, existing email verification, canonical identity continuity, wrong subject, both directions of purpose isolation, replay/expiry/attempt cap, concurrent single completion, rollback, before/after-commit proof, no auto-login and logout. The fixture initially omitted the existing required organization/workspace request headers; corrected the fixture, not the product middleware.

Fixture realm permits accounts without email (`verifyEmail=false`, UPDATE_PROFILE trigger disabled) so initial binding can be tested; production login/required-action policy remains unchanged. This is not evidence that a realm configured to require email will admit email-less login. Fixture code and fixture control JAR are never packaged in the deployable SPI. The password mutation admin call remains covered by its existing isolated contract suite; the new real fixture uses a read-only username resolver only to exercise real current-password validation and reject same-password before mutation.

`scripts/email_code_reset_fixture.py`: 18 passed, including three-step verification, old/new password authentication, old-session rejection, same-password/policy/confirmation retry, replay, expiry, wrong-attempt limit, rollback, concurrent single write and allowing a previously used password (no added password-history rule).

Fixtures used fresh Keycloak dev-file storage and an isolated Backend SQLite tenancy fixture, not deployed PostgreSQL. All fixtures are fresh under `E:/Codex/Temp` (`mf-r2-security-fixture-04`, `mf-r2-reset-regression-01`); none use an old acceptance runtime, real SMTP or real user data. No GUI, deployment or user acceptance was executed. JAR compilation against exact Keycloak 26.7.3 and `git diff --check` passed.

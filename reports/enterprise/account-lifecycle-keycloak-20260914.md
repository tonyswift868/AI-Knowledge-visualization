# Keycloak real account lifecycle — partial implementation

Task: MENTORFLOW_REAL_ACCOUNT_LIFECYCLE_KEYCLOAK, employee 04.
Base: `8a98d46cac4e193eb71da64d8ebdd8a2408c11e6`.
Worktree: `E:/Codex/Data/worktrees/b18c/MentorFlow`, `worker/main3/04`.
The linked worktree has its own git directory and index. Integration is read-only;
no integration commits were consumed. This is not real-email acceptance PASS.

## Authorization and observed boundary

The user authorized disposable accounts and real verification/reset messages to
the previously approved test inbox, in the existing acceptance environment.
Production configuration must not change. Preserve the acceptance environment
and return immediately when a human needs to click; no chat polling or automatic
cleanup. No further send permission is needed.

Read-only observations on 2026-09-14 (Asia/Shanghai):

- Existing preview Keycloak reports 26.7.3. Its native verifier is enabled for
  `mentorflow`, with issuer `https://localhost:18080/realms/mentorflow`.
- Its hostname remains `https://localhost:18080`.
- Windows GET of `https://auth.clannad.win/realms/mentorflow/.well-known/openid-configuration`
  returned 200 and the same localhost issuer. The server-origin request returned
  403; this does not mean the Windows/public browser path is unavailable.
- No local listener was found on port 18080. No MentorFlow-named root was found
  in the Windows current-user or machine root stores. This is not an exhaustive
  certificate audit; no trust store was modified.
- An authenticated kcadm session was not found at the bounded known locations:
  container `/opt/keycloak/.keycloak/kcadm.config`, `/root/.keycloak/kcadm.config`,
  host `/root/.keycloak`, `/home/mentorflow-admin/.keycloak`, the existing e109
  directory and the two gatea artifact directories inspected. This does not
  establish that no management authority exists elsewhere.
- Existing `deploy/enterprise/auth-email-security/admin.py` authenticates using
  `grant_type=password`. It was read, not run. AGENTS.md line 38 forbids password
  grants. A permitted existing administrative resolver/session remains needed;
  desktop/native credentials must not be repurposed as administrative authority.
- Existing controlled Brevo resolver source was located and read, not invoked.
  SMTP credentials were not loaded. Current realm SMTP configuration was not
  re-read, and historical Mailpit configuration is not treated as current truth.

## Implemented private port

`backend/app/enterprise/identity/account_lifecycle.py` wraps the official
Keycloak administration operations with an injected administrative token
resolver and verified HTTPS, bounded responses/deadlines, no redirects,
no automatic retries, and sanitized errors:

- `POST /admin/realms/{realm}/users`, user-chosen username, permanent password
  length 8–128, email unverified and VERIFY_EMAIL required. The realm's native
  password and username policies still apply; there is no composition rule here.
- `PUT .../send-verify-email`, lifespan 600 seconds.
- `PUT .../execute-actions-email`, `["UPDATE_PASSWORD"]`, lifespan 600 seconds.
- Private exact username reconciliation for ambiguous create outcomes.

No public routes, second UI, token generator, login authority, credential store
or capability override was added. This port is not connected to the 03 shell or
production runtime. Default LEARNER role/group configuration must be qualified
before using the port; this adapter does not itself assign or prove that role.
An email 204 is submission, not mailbox visibility, verification or reset success.

## Remaining implementation and acceptance gates

1. Establish an authorized, reachable action-link origin for the acceptance
   environment and a permitted management resolver. Do not rewrite signed links,
   disable TLS validation, or mutate the identity migration to fabricate PASS.
2. Qualify effective default LEARNER roles/groups, username uniqueness and native
   password policy. Confirm that realm-default outstanding actions do not require
   an unintended password change after successful email verification.
3. Connect the existing 03 shell through a backend capability-gated, bounded
   register/forgot API. The anonymous forgot flow must return the existing uniform
   message for absent/present accounts and provider failures, with peer/account/
   global rate limits and a qualified timing boundary. The private reconciliation
   method is never an anonymous API response. None of this new flow is claimed PASS.
4. Extend and test the existing atomicity/completion seam for the official
   **execute-actions** token. Existing AtomicResetCredentialsActionTokenHandler
   and ResetCompletions.bind accept **reset-credentials** tokens bound to an
   original OIDC authentication session. AtomicResetPassword also explicitly
   checks that token type. The official admin API's ExecuteActionsActionToken
   does not satisfy that existing binding; old reset tests are not proof for it.
5. In a retained disposable acceptance environment, create exactly one new
   account through createUser, confirm pre-verification login rejection, then
   call sendVerifyEmail once to the approved inbox. Record only bounded status
   and timestamp. On an uncertain write/send result, reconcile rather than retry.
   Immediately return USER_ACTION_REQUIRED for the human click; preserve the
   environment, credentials only in its controlled resolver, and no action URL
   or token in receipts/chat.
6. After user confirmation, read emailVerified from Keycloak and perform native
   login plus canonical workspace bootstrap. Never mark emailVerified by admin
   mutation to satisfy the gate.
7. Call executeActionsEmail([UPDATE_PASSWORD]) once, immediately hand off the
   human click and double-entry form. Require the canonical after-commit signal,
   reject the old password and prove new-password native login. Keep the same
   account/workspace. Explicitly test expiry, reuse and concurrent submissions
   for this token type. Only after all stages pass may capabilities be enabled.

## Current receipt

Validation: 86 focused tests passed across the new private adapter, existing
disabled-account routes, reset-completion contract and native authentication.
After the final JSON content-type/response-shape adjustment, all 27 adapter tests
passed again. These use synthetic transports and do not establish real Keycloak
administration, role policy, SMTP delivery or execute-actions completion.
`git diff --check` passed. The local compact handoff generated successfully
(1119 bytes). Knowledge lint and web-handoff check returned ValueError while
the new sources were untracked; the generator also requires an integration
checkout for its web output. No A-owned web handoff or integration ref was changed.

```text
STATUS = PARTIAL
REGISTER_ACCOUNT_CREATED = NOT_RUN
REAL_VERIFY_EMAIL_SENT = NO
EMAIL_VERIFIED = NOT_RUN
LOGIN_AFTER_VERIFY = NOT_RUN
REAL_RESET_EMAIL_SENT = NO
RESET_DOUBLE_ENTRY_UI = NOT_RUN_FOR_EXECUTE_ACTIONS
CANONICAL_RESET_COMPLETION = GAP_FOR_EXECUTE_ACTIONS
OLD_PASSWORD_REJECTED = NOT_RUN
NEW_PASSWORD_LOGIN_PASS = NOT_RUN
REGISTER_CAPABILITY = FALSE
FORGOT_PASSWORD_CAPABILITY = FALSE
REAL_SMTP_SEND_COUNT = 0
LIVE_KEYCLOAK_MUTATION = 0
PRODUCTION_CONFIG_MUTATION = 0
SECRET_VALUES_EMITTED = 0
REAL_USER_DATA_USED = NO
TEST_ENVIRONMENT_CREATED_OR_REMOVED = NO
WORKER_STATUS = SILENT
```

Official API references:
[UsersResource 26.7.3](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/resources/admin/UsersResource.java),
[UserResource 26.7.3](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/resources/admin/UserResource.java).
The latter constructs ExecuteActionsActionToken and deprecates reset-password-email
in favor of execute-actions-email. No deprecated reset-password-email call was added.

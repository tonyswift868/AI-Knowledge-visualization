# Account lifecycle UX shell receipt

DISPATCH_ID = MF-MAIN3-03-ACCOUNT-LIFECYCLE-UX-SHELL-20260913-05

TASK_ID = MENTORFLOW_REGISTER_VERIFY_FORGOT_MULTIACCOUNT_UX_SHELL_V1

STATUS = PASS_ACCOUNT_LIFECYCLE_UX_SHELL

This receipt accepts **UI, presentation state and offline tests only**. No
account feature, backend flow, provider deployment, SMTP or credential storage
has been enabled. Reset success deliberately remains pending owner 01's
canonical completion signal, as required by this dispatch.

## Phase 0

- Existing managed office: `E:/Codex/Data/worktrees/bb70/MentorFlow`.
- Own branch: `worker/main3/03`; clean starting HEAD:
  `1ad0ed79e986396334821c9196be838c7d365222`.
- Independent HEAD/index:
  `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow3/`.
- `integration/main3` read-only at `d5169081bfcc3510ba2e4a644872294cd70ce3d7`.
- No new worktree and no writes to shared Local or other worker files.
- Root performed all edits and validation; no child workers were opened.

## Audit and completed contract

| Requirement | Result |
| --- | --- |
| Register form | Account, email, password, confirmation; no username pattern/composition requirement. Only uniqueness guidance. |
| Duplicate account | `username_taken` presentation shows “这个账号已被使用，请换一个。”; no local availability lookup. Editing the account clears that state without clearing passwords. |
| Email syntax | Native email-input validity with Chinese correction text. No additional username/email identity policy. |
| Password presentation | 8–128 Unicode code points, no composition rules. Tested 7/8/128/129 boundaries, letters only, digits only, symbols only, Chinese and emoji. |
| Shared confirmation | Native reset and future register/reset forms use one FreeMarker macro and one JS binder. First masked by default and revealable; confirmation always masked without a reveal control. |
| Verify pending | Registration waits for its correlated presentation result; then shows “验证你的邮箱”, the masked email and resend/back controls. It never authenticates or opens main. |
| Email privacy | `21234534@qq.com` is presented as `2****34@qq.com`. State retains only the masked address. Form values are discarded on leaving the form. |
| Resend | Ready, countdown, sending, failure and retry presentations. A click emits an intent only. No mail/API call. |
| Forgot receipt | Both existing and missing-account fixtures show exactly “如果该账号存在，我们会向绑定邮箱发送密码重置邮件。” No existence input/branch in the state method. |
| Reset | Invalid/expired/used state disables input/submit. A valid presentation state permits the shared pair. Submission stops at `RESET_WAITING`; no invented reset success transition. |
| Multi-account | Welcome heading, account display name, safe identifier, selection and add-account controls. Selection emits only the nonsecret account key; no login, refresh, migration or Vault switch. |
| Future preferences | “记住密码” and “自动登录”, default OFF; auto depends on remember. Both are memory-only and reset on changing/adding accounts. Current runtime/token preferences are unchanged. |
| Capability gates | Default and malformed capabilities fail closed. Register/forgot controls remain visible, disabled, with human tooltips when FALSE. |
| Asynchronous results | Request ids bind register/forgot/resend results to the current request. Duplicate, stale and abandoned results cannot replace a new request. |
| Navigation/lifecycle | Leaving a form clears typed credentials, disposal removes listeners/timers, remount restores masked defaults. One document scroll at 320px width; account labels use textContent. |

## Presentation API for the next integration task

The custom `mentorflow-account-lifecycle.ftl` is **not a routed native Keycloak
page**. Current `login.ftl`, realm settings, capabilities, backend and SMTP
configuration do not import or activate it. Its forms have no POST destination
and use `method="dialog"`; module failure cannot fall through to an HTTP form
submission. Source tests require this boundary.

`mountAccountLifecycle(root, options)` returns `{model, render, dispose}`.
Default options keep both capabilities FALSE. Browser tests explicitly inject
synthetic capabilities/accounts for presentation testing; there is still no
network or credential adapter. `initialScreen` is an offline presentation seam,
not an authentication or authorization decision.

Saved-account inputs contain only `{key, displayName, safeId}`. Extra fields
are not copied to model state. The caller supplies a nonsecret opaque key and
safe account label from the eventual canonical account-list contract. Display
names and safe identifiers are rendered as text, never HTML. Owner 04 retains
ownership of persistence, identity continuity, data and Vault migration.

The root element emits `mentorflow-account-intent` with these allowlisted data:

| Intent | Detail |
| --- | --- |
| login / add_account | intent only |
| account_selected | intent, accountKey |
| register / forgot_password / resend_verification / reset_password | intent, requestId |

No event contains password, confirm password, raw email, username, token or
session. These events are **UI intents, not service requests**. The future
provider integration must use the canonical native credential submission path;
it must not add a Desktop DOM/password bridge.

Result presentation methods:

| Method | State contract |
| --- | --- |
| `registrationResult(outcome, {requestId, email, retryAfter})` | For the current REGISTER_SUBMITTING request: verification_pending enters VERIFY_PENDING and masks the email; username_taken returns to REGISTER with duplicate text; other failures use generic retry text. |
| `resendResult({requestId, accepted, retryAfter})` | For the current resend request only: accepted starts the bounded cooldown; failure offers retry. |
| `forgotResult(requestId)` | Enters FORGOT_SENT with the single anti-enumeration message. There is no account-existence parameter. |
| `presentReset(valid/invalid/expired/used)` | Displays the supplied state only; it does not inspect/validate a token. |
| Reset completion | **No completion method or success state yet.** Wait for owner 01's canonical reset-completion contract. |

Request ids are local, nonsecret correlation ids scoped to one mounted
instance. The future adapter must capture the emitting instance and request id
when starting work; it must not route an old result to a replacement instance.

The future register presentation minimum **8** intentionally differs from the
current native realm minimum **15**. This dispatch does not change that realm
policy or put the 8-character UI on its native registration route. Owner 01's
future canonical policy must be aligned before activation. The native reset
page still uses server-side canonical policy without this presentation rule.

The current remembered-session implementation and its labels are unchanged.
The future checkbox labels are not a claim that password persistence exists;
Credential Locker password storage needs its own authorized implementation task.

## Validation

- **45 Python/unit/static/Qt-offscreen checks passed**: regular login theme,
  reset theme, lifecycle source boundaries, existing canonical realm policy,
  Desktop reset no-auto-login and capability configuration.
- **14 FreeMarker fixtures rendered** using the existing pinned runtime and
  actual theme/macro source. Layout chrome is an offline fixture, not a server.
- **22 lifecycle browser checks passed**, including capability FALSE, register
  fields, all password boundaries, email/username errors, verify/resend,
  anti-enumeration, reset waiting, multi-account layout, stale request ids,
  consent reset, safe text and no network/storage effects.
- **23 existing reset browser checks passed** after shared-component extraction.
- Empty-field screenshots of register, selector and future login preferences
  were inspected locally. No user screenshots, password screenshots, browser
  traces or form/request-body dumps were collected.

Reproduction uses existing local tools; no install or provider is required:

```text
pytest -q -p no:cacheprovider
  tests/theme/test_login_theme.py tests/theme/test_reset_password_theme.py
  tests/theme/test_account_lifecycle_shell.py
  deploy/enterprise/auth-email-security/test_policy_contract.py
  desktop/tests/test_reset_password_boundary.py
  desktop/tests/test_e109_capability_config.py
```

Use `QT_QPA_PLATFORM=offscreen`, isolated E: TEMP/TMP/pytest directories and the
same Pydantic public-type preload as the previous dispatch. Render fixtures with
`tests/theme/ResetPasswordThemeFixture.java` plus the pinned FreeMarker JAR,
then run both `account_lifecycle_preview.cjs` and `reset_password_preview.cjs`
with the rendered directory. Browser tools are supplied through the existing
`MF_PLAYWRIGHT_MODULE` and `MF_CHROMIUM_EXECUTABLE` environment variables.

REGISTER_CAPABILITY = FALSE

FORGOT_PASSWORD_CAPABILITY = FALSE

BACKEND_CHANGED = NO

KEYCLOAK_MUTATION = 0

SMTP_SEND = 0

LIVE_ACCOUNT_QA = NOT_RUN

GITHUB_ACTIONS_EXECUTED = 0

WORKER_STATUS = SILENT

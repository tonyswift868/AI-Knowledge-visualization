# Reset password confirmation UI source receipt

DISPATCH_ID = MF-MAIN3-03-RESET-PASSWORD-CONFIRM-UX-20260913-04

TASK_ID = MENTORFLOW_RESET_PASSWORD_DOUBLE_ENTRY_UX_V1

STATUS = BLOCKED_CANONICAL_RESET_COMPLETION_SIGNAL

The double-entry UI and Desktop no-auto-login boundary are implemented and
tested. The complete requested contract is not claimed: the pinned provider
does not distinguish reset success from other required-action completion in
its generic info-page message. A callback is also not evidence of a successful
password reset. No invented client-side success state was added.

## Isolation and scope

- Continued `E:/Codex/Data/worktrees/bb70/MentorFlow`, branch `worker/main3/03`.
- Starting HEAD: `3eba9c3e0198d6b485226737e59d594cb773a12c`, initially clean.
- Private HEAD/index: `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow3/`.
- `integration/main3` stayed read-only at `d5169081bfcc3510ba2e4a644872294cd70ce3d7`.
- No new worktree, deployment, live reset, email, issuer/security config edit,
  installer, package rebuild or GitHub Actions run.
- No child workers; Root performed all edits and validation.

## Gap audit and result

| Contract | Source gap and result |
| --- | --- |
| Independent masked inputs | Added native `UPDATE_PASSWORD` template override; keeps both native field names and POST action. |
| First show/hide | Visible Chinese button toggles only the first input; masked again on page restoration. |
| Confirmation never revealed | Removed upstream confirmation reveal; suppressed built-in Edge reveal; no confirm type mutation. |
| Empty / partial / mismatch | No empty/partial mismatch error; live equality error blocks submit; first value stays untouched. |
| Retype / matching | Matching clears the equality error immediately and allows native policy submission. |
| Password policy | Native Keycloak realm policy remains authoritative on POST. No UI password-rule engine. Policy rejections have separate human messages. |
| Paste / password manager | Standard password inputs with `autocomplete=new-password`; no clipboard or copy restrictions. |
| Password confidentiality | No DOM bridge into Desktop, analytics, logging, serialization or storage of either input. |
| Invalid / expired / used reset | Native error page inherited; offline fixtures contain no password form or submit control. Token enforcement unchanged. |
| Automatic login after reset | Fixed a real preexisting gap: forgot-password callbacks previously reached `complete_login`. They now use canonical callback validation once without token exchange, then return to ordinary login. Late success signals also cannot create a session. |
| Exact success wording | Translation exists for native `accountPasswordUpdatedMessage`. Offline fixture displays the exact requested text. **Actual force-login reset emits generic `accountUpdatedMessage`, so the real reset-specific success trigger remains blocked.** |
| Return to login | Offline Desktop callback/result tests return to `LOGIN_READY`, with no main window/session and a fresh ordinary login on click. Real reset-completion routing is not claimed. |
| Capabilities | REGISTER_CAPABILITY = FALSE; FORGOT_PASSWORD_CAPABILITY = FALSE. |

## Remaining canonical boundary

Keycloak 26.7.3's [reset authenticator](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/authenticators/resetcred/ResetCredentialEmail.java)
honors the existing `force_login=true` configuration. Its
[required-action completion](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/managers/AuthenticationManager.java)
ends the authentication session and creates a generic account-updated info page.
It supplies neither a reset-specific success message nor a Desktop completion
event. Changing the generic translation to “密码已重置” would mislabel other
account changes. The auth owner must supply an authoritative reset-only
completion contract before that last UI transition can be wired and accepted.

The requested double-entry and ordinary-login behavior agrees with the
[OWASP forgot-password guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).
This dispatch preserves native reset single-use, expiry and replay handling;
it does not implement or claim to retest live token atomicity.

## Validation

- **121 passed**: theme source checks, existing canonical password-policy tests,
  reset callback/return tests, closed capability tests, prelogin update gates,
  login lifecycle, in-app OIDC and canonical OIDC tests.
- **13 offline FreeMarker fixtures rendered** with the existing pinned
  `freemarker-2.3.32.jar`, official Keycloak 26.7.3 templates and a fixture layout.
  Native password template syntax, error/info templates and logout macro were
  exercised. This is not a running Keycloak server.
- **23 headless browser checks passed** against those fixtures and the actual
  `password-pair.js`. Includes every empty/partial/mismatch/reveal/retype state,
  server policy-error display, invalid/expired/used error states, cancellation,
  duplicate submit, storage absence, specific success fixture, generic success
  not being falsely relabeled, and normal-login link navigation.
- No browser screenshots, traces, request bodies or password values enter the
  browser receipt. Only check names/counts are printed.
- `git diff --check` passed.

Reproduce the Python suite with the existing project test environment:

```text
pytest -q -p no:cacheprovider
  tests/theme/test_login_theme.py tests/theme/test_reset_password_theme.py
  deploy/enterprise/auth-email-security/test_policy_contract.py
  desktop/tests/test_reset_password_boundary.py desktop/tests/test_e109_capability_config.py
  desktop/tests/test_prelogin_update_ux.py desktop/tests/test_login_smooth.py
  desktop/tests/test_in_app_oidc.py desktop/tests/test_enterprise_oidc.py
```

Set `QT_QPA_PLATFORM=offscreen`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and put
TEMP/TMP/pytest output in an isolated E: scratch directory. In the existing
Python 3.13 environment, preload Pydantic's public types before importing the
Qt suite, as in the earlier MAIN3 receipts.

For rendering, run `ResetPasswordThemeFixture.java` with the FreeMarker JAR on
the Java classpath and arguments: repository root, extracted official theme
directory, output directory. Run `reset_password_preview.cjs` with that output
directory; set `MF_PLAYWRIGHT_MODULE` and `MF_CHROMIUM_EXECUTABLE` to existing
local tools. No dependency installation or provider connection is required.

REAL_RESET_QA = NOT_RUN

LIVE_TOKEN_ATOMICITY_QA = NOT_RUN

GITHUB_ACTIONS_EXECUTED = 0

WORKER_STATUS = SILENT

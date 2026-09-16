# MF-R1-03-EMAIL-CODE-UI

EMPLOYEE_SLOT = 03
STATUS = PASS_DESKTOP_EMAIL_CODE_UI_FIXTURE
BASE_SHA = b08893cc7a9947ff74ac28b8b9fa19d0b6cc658d
BRANCH = worker/main3/03-login-r1

This delivery continues the existing login implementation at
`c10137169d52d36b084d133a7a534812d631c3ac`. The intervening b08893c commit is
historical blocker evidence only, not part of this feature. No reset, backend
cherry-pick, deployment, Workbench change or account mutation was performed.

## Product behavior

The existing local FreeMarker lifecycle pane, QWebChannel Bridge, transport and
password pair now implement bound email → request six-digit code → verify →
show new/confirm password → canonical completion → return to the same login gate.
The forgot-password entry no longer calls the mail-link completion observer.
Registration and the login capability guard are retained.

The three anonymous JSON POSTs follow owner 01's ADR at backend prerequisite
`85bb4386b680a3f58916d759bebb540e8a069f25`. Authorization, Cookie and Origin
are removed, redirects are disabled, responses are bounded and validated. Error
text comes from a fixed local allowlist, not arbitrary service bodies.

Opaque transaction/proof values stay in the Python window's memory. Internal
worker results are carried by an object not registered with QWebChannel. Cancel,
expiry and success discard the state; cancelled callbacks cannot revive a pane.
Resend uses a visible cooldown. Neither sends nor password writes auto-retry.
An uncertain completion discards proof and offers login or a fresh code request.

`NEW_PASSWORD_SAME_AS_CURRENT` displays exactly “新密码不能与当前密码相同”.
It, policy rejection and confirmation mismatch keep the password stage and the
original proof deadline, allowing a corrected submission without another email.
No password-history restriction was added. Confirmation remains masked.

Only a validated `COMPLETED` response triggers credential cleanup. The returned
canonical account ID selects the registry entry, then calls
`LoginSession.logout(profile, remote=False, forget_password=True)`. Unknown local
IDs cause no credential edits. Account history/data and other accounts remain;
new passwords are not saved and no automatic login occurs.

## Executed validation

Environment: Windows, existing isolated Python at
`E:/Codex/Temp/mf-recovery-desktop-r1-venv/Scripts/python.exe`, PySide6 6.11.1,
Qt WebEngine with `QT_QPA_PLATFORM=offscreen`, httpx MockTransport synthetic API.
No installed dependencies, real email, real credential mutation or external browser.

Generated the actual existing pane assets using:

```text
E:/Codex/Temp/mf-account-core-r1-venv/Scripts/python.exe scripts/render_account_lifecycle_ui.py --keycloak E:/Codex/Temp/mf-e109-sec04-tools-20260912/keycloak-26.7.3 --jdk E:/Codex/Temp/mf-e109-sec04-tools-20260912/jdk-21.0.12.1+1
```

Focused checks (pytest invoked with the isolated Desktop Python above):

```text
python -m pytest desktop/tests/test_email_code_recovery.py desktop/tests/test_login_r1_repair.py tests/theme/test_account_lifecycle_shell.py -q
node tests/theme/native_lifecycle_bridge.cjs
```

Combined result: **16 passed, 0 failed, 0 skipped**, plus Node bridge PASS.
After the internal signal isolation change, all 6 new pane checks passed again;
the success/secret-boundary check passed once more after adding a metaobject
assertion. These repeats are not extra unique test cases.

The real rendered pane/channel tests cover same-window success; wrong code,
resend and expiry; all three correctable password errors; uncertain completion;
cancel and fresh-pane isolation. They assert no navigation/new window, target-only
token/password cleanup, preserved registry, no auto-login, and no opaque values
or password in public channel events/settings. Existing cleanup checks also cover
an unknown returned account ID and a different issuer with the same username.

## Integration notes for A

Consume the exact feature commit, not the worker tip by inference. Re-render
`artifacts/account-lifecycle-ui` from these source files before final packaging;
generated assets are ignored build output. Use 02's existing renderer/build wiring
(`6c573287`) and include this updated template/JS. No alternative QWidget auth UI
or new external entry was introduced.

A owns normal-launch configuration and bundled-resource placement. Preserve the
canonical identity issuer and use the approved API/public OIDC configuration from
the dispatch. Desktop tests use synthetic configuration and prove no live routing.

ACTUAL_CURRENT_WINDOW_EXECUTED = YES_OFFSCREEN_RENDERED_PANE
EXTERNAL_NAVIGATION = 0
REAL_OWNER_EMAIL_UAT = NOT_RUN
FINAL_FROZEN_PRODUCT_ACCEPTANCE = NOT_CLAIMED
BACKEND_CHANGED = NO
REAL_ACCOUNT_MUTATION = 0
SMTP_SEND = 0

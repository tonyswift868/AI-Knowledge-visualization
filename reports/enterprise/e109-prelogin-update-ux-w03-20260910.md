# E109 pre-login and in-app update UX — worker 03

EMPLOYEE_SLOT = 03
DISPATCH_ID = MF-E109-W03-PRELOGIN-UPDATE-UX-20260910-01
TASK_ID = MENTORFLOW_PRELOGIN_AND_INAPP_UPDATE_UX_V1
BASE_DELIVERY = e6b87529e7dbe59198623bedcb87120b23fb33d8
STATUS = SOURCE_READY_INTEGRATION_PENDING
NO_FOREGROUND_USER_TEST = true
WORKER_STATUS = SILENT

The commit containing this new report is the complete linear UI feature batch
on the requested base. Its exact commit/tree are emitted in the final receipt.
No old receipt, dependency, lockfile, 07 core, migration or shared contract was
changed. No merge, rebase, push, installation or deployment was performed.

## Delivered UI

PRELOGIN_UPDATE_ENTRY = SOURCE_PASS
INAPP_UPDATE_ENTRY = SOURCE_PASS
MANDATORY_UPDATE_UI = SOURCE_PASS_CORE_POLICY_BINDING_PENDING
REPAIR_UI = SOURCE_PASS_CORE_REPAIR_BINDING_PENDING
REGISTER_ENTRY = CAPABILITY_GATED_SOURCE_PASS_DEFAULT_DISABLED
FORGOT_PASSWORD_ENTRY = CAPABILITY_GATED_SOURCE_PASS_DEFAULT_DISABLED
DUPLICATE_UPDATE_LOGIC = 0

- Login has a direct version/check footer. An optional release displays
  `发现 <version>` and `更新到最新版本`, without blocking login.
- A mandatory result displays `当前版本需要更新后才能继续`. The shell stays
  visible; interactive login, remembered login, late OIDC/bootstrap results
  and local-service startup cannot bypass the restriction. Old optional
  snapshots cannot release the restriction during a pending operation.
  A later healthy/optional result restores the ability to log in.
- A failed post-shell installation check or canonical repair result displays
  `MentorFlow 需要修复` and `修复并检查更新`. Failed repair remains an inline,
  fixed human error and never unlocks login or falls back to Setup.
- Login footer, pre-login About and the actual logged-in SystemPage card use
  the same UpdatePanel implementation and application UpdateController.
  Compact presentation does not add a second updater or transaction.
- One click downloads, prepares and calls apply/relaunch once. Cancellation,
  window close, session ending, security failure and explicit core deferral
  retain the existing safe behavior. There is no second restart button.
- All seven requested UI states are rendered offscreen. The update card's
  existing CHECKING and explicit DEFERRED_TO_NEXT_LAUNCH states are retained.
  Progress, error text, version and release notes remain presentation-only.

## Single adapter and remaining core ownership

`desktop/update_lifecycle.py` remains the only UpdatePort/CoreUpdatePort
adapter. Existing check/download/apply behavior continues to delegate to the
frozen core; no manifest parser, downloader, transaction or repair engine is
implemented by this delivery. The GUI/controller never launches an external
program. Existing 07 admission and session fencing remains
owned by the core, not by a UI flag.

The optional `repair_and_check(progress, cancel)` seam delegates only when a
service provides that callable capability. Missing support fails closed.
This is a proposed binding slot, **not a claim that 07 published this API**.
Any final signature adjustment belongs in this adapter.

The frozen base does not expose a public mandatory/repair policy result or
a repair capability. The newer read-only 07 semantic reference at
`E:\Codex\Worktrees\MF-e1-account-core-e107-final\docs\ONE_CLICK_UPDATE_CORE_V1.md`
also did not provide an E109 exact delivery during this task. No newer core
source, private mandatory metadata or dependency commit was consumed.

Therefore signed mandatory-policy delivery, real installed repair and actual
process exit/update/relaunch are **not end-to-end accepted by this receipt**.
They require 07's frozen public delivery and integration verification. The UI
work is delivered now, without waiting or broadening the authorized scope.

An installation rejected before Qt can safely load still follows the
unchanged trusted bootstrap failure path. This UI does not bypass that
boundary in order to render code from a damaged package. The repair shell
gate covered here is the safely loaded shell's background SECURITY_GATE.
Service configuration and health notification stay behind a successful gate;
queued initial health callbacks also recheck current trust and visibility.

## Account actions

Native `注册账号` / `忘记密码` entries use the existing embedded OIDC login
pipeline. PKCE verifier/challenge, state, nonce, redirect URI, loopback
receiver and token validation are preserved. Registration uses `prompt=create`;
reset uses the documented OIDC `/protocol/openid-connect/forgot-credentials`
endpoint. No internal `login-actions` URL is constructed, and password change
for an already authenticated account is not substituted for password reset.

Reference: [Keycloak client-requested registration/reset](https://www.keycloak.org/docs/latest/server_admin/#registration-or-reset-credentials-requested-by-client).

`LoginGateWindow.set_account_capabilities()` accepts only explicit Boolean
`register` and `forgot_password` values. The packaged assembly can receive the
same dictionary through `attach_updates(..., account_capabilities=...)`.
Missing, false, numeric and string values keep the respective action disabled.
An account action also rechecks the current login/update restriction on click.

05 has not supplied an authoritative runtime capability binding for this
delivery; both actions consequently remain disabled by default. No realm
configuration was inferred from a development export, and no server policy
was changed. 05/Integration must bind capabilities after confirming deployed
Keycloak version/realm support. Real registration/email reset is untested.

## Reproducible verification

All UI runs used `QT_QPA_PLATFORM=offscreen`, isolated Qt settings and E:-drive
temporary directories. New UI fixtures fail immediately on external browser,
QDesktopServices, subprocess launch, os.startfile or os.system attempts.
Protocol/action tests use recording fakes; core download regressions use
synthetic signed localhost fixtures. No real identity/provider calls occur.

```powershell
$env:PYTHONPATH='E:\Codex\Worktrees\MF-e108-updater-ui-w03'
$env:QT_QPA_PLATFORM='offscreen'
$env:TEMP='E:\Codex\Temp\u109'
$env:TMP=$env:TEMP
& 'E:\aiPlay\AI-Knowledge visualization\.venv\Scripts\python.exe' -m pytest `
  desktop/tests/test_prelogin_update_ux.py `
  desktop/tests/test_update_ui_flow.py `
  desktop/tests/test_update_ui_core_regression.py `
  desktop/tests/test_mature_update.py `
  desktop/tests/test_login_smooth.py `
  desktop/tests/test_enterprise_oidc.py `
  desktop/tests/test_in_app_oidc.py `
  scripts/test_single_window_startup.py `
  scripts/test_login_import_boundary.py -q --tb=short `
  --basetemp=E:/Codex/Temp/u109/delivery `
  --junitxml=E:/Codex/Temp/u109/delivery.xml --maxfail=3
```

Seven login-state PNGs and the existing full-card renders are retained under
the test basetemp. These are source-rendering evidence, not foreground or
installed-Windows qualification. No installer, browser or CMD was launched.

Final focused gate: **183 passed, 0 failed, 0 skipped in 76.69 seconds**.
This includes 38 E109 source cases plus the existing one-click/core, login,
OIDC and startup/import-boundary regressions. The seven requested login states
and mandatory/repair layouts were inspected as offscreen PNGs.

Evidence: `E:\Codex\Temp\u109\delivery.xml`

SHA-256: `bebe511da1914fbfc90463031dc10be877dddf66aeab8a80693e140ea6888150`

`git diff --check` and Python compilation of all changed Python modules/tests
also passed. Worktree cleanliness and the exact delivered commit/tree are
verified after committing, rather than inferred from test success.

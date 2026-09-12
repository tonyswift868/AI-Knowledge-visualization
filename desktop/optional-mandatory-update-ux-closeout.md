# Optional / mandatory update UX closeout

```text
STATUS = PARTIAL
EMPLOYEE_SLOT = 03
GENERATION = MAIN3
DISPATCH_ID = MF-MAIN3-03-MANDATORY-OPTIONAL-UX-20260912-02
TASK_ID = MENTORFLOW_OPTIONAL_MANDATORY_UPDATE_UX_V1
EXPECTED_START_HEAD = b4c34ab592ce98a7e7abf13fad73e152073c9008
BRANCH = worker/main3/03
```

## Ownership and boundary

The existing managed worktree is `E:/Codex/Data/worktrees/bb70/MentorFlow`.
HEAD and index are private to
`E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow3`.
The starting HEAD and branch matched the dispatch, with a clean index/worktree.
No new worktree was created; no shared Local checkout, integration branch,
02 updater core, issuer migration, or live email/security configuration was written.
Root performed every edit and validation. No child workers were opened.

The existing UpdateController / CoreUpdatePort / shared UpdatePanel architecture
remains the only update route. `main.py` changes are limited to enforcing the
gate around main-window creation, suspending an already open main window, and
binding the active window. `update_lifecycle.py` changes only adapt public core
results. Neither file implements download, version policy, apply, or recovery.

## Exact gap matrix

| Contract | Starting evidence | Result |
| --- | --- | --- |
| Optional login/use | Existing login allowance and failure behavior | Retained, including late login and existing main-window tests |
| Optional choices | No later or exit-time choice; shared generic update label | Separate 稍后 / 更新并重启 / 退出时更新 actions |
| Later | No postponement presentation state | Collapses the offer; same-offer automatic check preserves choice; manual check can reopen it |
| Exit-time update | No close-triggered consent handling | Arms the existing controller; normal close uses the same download/apply/relaunch route once; consent follows the active window |
| Exit races | Newly relevant to exit-time choice | Handles in-flight check, duplicate close, cancellation, failure, no-longer-needed update, session shutdown and account-switch boundary |
| Release presentation | Compact view hid notes/size; sparse startup result replaced plan metadata | Shows latest, notes, expected size and human incremental/full fallback text; unknown size is explicitly pending confirmation |
| Mandatory UI | Generic text with ordinary check action | Required support-floor text, latest target, update/retry and explicit Exit; no later/exit-time/skip choice |
| Login region | Existing disabled login guard | Hides login card and account controls, preserves guards against programmatic activation and late auth completion |
| Main window | Initial login guard existed; already open window was not suspended | Disables/hides existing main, shows gate; checks again after construction and before showing a new main |
| Mandatory failures | Existing transient-state latch | Retains gate, latest and reason across cancel/network/validation/exception; retry uses existing core |
| Policy-check uncertainty | Core can return HEALTHY files with unavailable feed policy | Adapter presents this as recoverable check failure; it cannot revoke a known mandatory gate |
| Missing service | Update click could be a no-op | Retry creates the same configured service on the controller worker, then continues the requested core action; cancelled/closed retries cannot apply late |
| Repair distinction | Existing repair action; fatal recovery state lacked a blocked mapping | Repair remains separate, both repair+mandatory arrival orders stay blocked; unverified recovery runtime maps to repair gate |
| Historical mandatory | UI consumed the state, but had no latest-optional regression | Core-supplied MANDATORY_UPDATE with latest optional still has mandatory controls and latest target; floor is never displayed/compared in UI |
| Mandatory reason | Public core startup/plan snapshots omit the reason | UI accepts/translates the three core enums; generic support-floor wording until 02 exposes the field. **DEPENDS_ON_02** |

Both packaged `desktop_gui_entry.py` and ordinary `main.py` attach the shared
presenter before displaying the login shell. The legacy unbound shell helper
is not a second product update path.

## Core dependency and exit semantics

`DEPENDS_ON_02 = PUBLIC_MANDATORY_REASON`

At this worktree's base, `get_startup_update_state()` returns state/current/latest/feed,
and `make_plan()` returns notes/size/path but neither publishes `mandatory_reason`.
Read-only inspection of `worker/main3/02` at
`7b6caf58de601adabf26eced4f94dd1e1f2a5994` showed the same startup omission.
02 needs to supply the applicable reason in its canonical public result,
including when the latest release itself is optional but a historical floor
requires this client to update. UI does not inspect `service.release.signed`,
verify a feed, or reconstruct historical version policy to supply it.

Supported public values are `security`, `protocol`, and `data_compatibility`.
Missing reason renders “当前版本已低于受支持的版本要求。” without inventing a category.
This missing production metadata is why the complete product contract is
reported PARTIAL despite passing UI behavior checks.

“退出时更新” starts the existing canonical close → apply → relaunch → health flow
when the user normally closes the active window. The UI explicitly states that
MentorFlow restarts after updating. It does not introduce an exit-without-relaunch
engine, an on-disk pending receipt, or a claim of healthy completion. OS session
shutdown does not start a new transaction. Cancellation/failure preserves the
current window and old installation; actual file safety remains core-owned.

## Validation

**291 passed in 92.40 seconds**, including 37 new optional/mandatory cases.
`git diff --check` passed. The final log is
`E:/Codex/Temp/MF-MAIN3-03-MANDATORY-OPTIONAL-UX-20260912-02/accepted-tests.log`.

After setting `QT_QPA_PLATFORM=offscreen`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONUTF8=1`, and TEMP/TMP to that evidence directory:

```powershell
& 'E:/Codex/Temp/mf-e1-02b-verify-py313/Scripts/python.exe' -c 'from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator; import pytest,sys; sys.exit(pytest.main(sys.argv[1:]))' -q desktop/tests/test_optional_mandatory_update_ux.py desktop/tests/test_prelogin_update_ux.py desktop/tests/test_update_ui_flow.py desktop/tests/test_update_ui_core_regression.py desktop/tests/test_login_smooth.py desktop/tests/test_e109_integration_binding.py desktop/tests/test_e109_capability_config.py desktop/tests/test_login_gate_bootstrap.py desktop/tests/test_mature_update.py scripts/test_startup_update_repair.py scripts/test_one_click_update_core.py --basetemp E:/Codex/Temp/MF-MAIN3-03-MANDATORY-OPTIONAL-UX-20260912-02/accepted-tmp -o cache_dir=E:/Codex/Temp/MF-MAIN3-03-MANDATORY-OPTIONAL-UX-20260912-02/pytest-cache --tb=short
```

Final contract evidence:

```text
OPTIONAL_LOGIN_ALLOWED = YES
OPTIONAL_LATER = YES
OPTIONAL_UPDATE_RESTART = YES
OPTIONAL_EXIT_UPDATE = YES (canonical relaunch/health semantics)
MANDATORY_LOGIN_BLOCKED = YES
MANDATORY_MAIN_WINDOW_BLOCKED = YES
MANDATORY_SKIP_AVAILABLE = NO
MANDATORY_TARGET_LATEST = YES (core-supplied latest, including historical mandatory case)
MANDATORY_CLOSE_BEHAVIOR = EXIT
MANDATORY_CANCEL_BYPASS = NO
MANDATORY_NETWORK_BYPASS = NO
MANDATORY_REASON = CORE_ENUM_RENDERING_READY; PUBLIC_CORE_FIELD_MISSING
```

The suite uses Python 3.13.14, PySide6 6.11.1, pytest 8.4.2, Qt offscreen,
isolated INI QSettings and temporary fixtures under
`E:/Codex/Temp/MF-MAIN3-03-MANDATORY-OPTIONAL-UX-20260912-02`.
Pydantic public types are preloaded before pytest to avoid the existing
PySide/Pydantic import-order failure in this environment.
Loopback feeds and inert binaries are test fixtures; users are not asked to
provide a local feed. Launches are blocked or replaced with recording fakes.

The settings-card test now creates and closes its own application updater;
previously it could silently reuse a controller left by another test and never
exercise its own offline service. Its assertion now verifies the service identity.

```text
REAL_INSTALL_QA = NOT_RUN
FINAL_PRODUCT_PASS = NOT_CLAIMED
GITHUB_ACTIONS_EXECUTED = 0
INSTALLER_POPUPS = 0
UAC_PROMPTS = 0
DPI_CHANGES = 0
FINAL_PACKAGE_REBUILD = NOT_RUN
WORKER_STATUS = SILENT
```

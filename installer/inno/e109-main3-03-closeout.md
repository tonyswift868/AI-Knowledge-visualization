# E109 Installer / Update UI / Login-shell source closeout

```text
STATUS = PASS_INSTALLER_UPDATE_UX_SOURCE_CLOSEOUT
EMPLOYEE_SLOT = 03
GENERATION = MAIN3
DISPATCH_ID = MF-MAIN3-03-INSTALLER-UPDATE-UX-20260912-01
TASK_ID = E109_INSTALLER_UPDATE_UX_CLOSEOUT_V1
BASE_SHA = 04faf2a9fbb50c5f9403dad5abf46354c9b53d82
BRANCH = worker/main3/03
```

## PHASE 0 evidence

- Managed worktree: `E:/Codex/Data/worktrees/bb70/MentorFlow`.
- Its `.git` points to `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow3`;
  that directory's `gitdir` points back to this worktree.
- HEAD and index are private files under that worktree Git directory, distinct
  from the common repository. Both existed; no index lock or Git path overrides.
- Initial HEAD and `refs/heads/worker/main3/03` both matched BASE_SHA. Initial
  worktree/index were clean. No other worktree owned the requested branch.
- Attached from detached HEAD with `git switch worker/main3/03` only after these
  checks. No shared Local checkout was written. Root performed all edits.

## Exact gap matrix

| Contract | Base finding | Closeout evidence |
| --- | --- | --- |
| One polished installer | Already implemented | One `MentorFlow-Setup.exe`, native modern/dynamic branded pages, SetupMutex, stable UserRuntime launch/repair links; existing build/design/wiring tests |
| Existing parent, one final MentorFlow component | Already implemented | `ValidateParent` rejects missing drive/parent before appending; signed core preflight owns creation; source and inert filesystem tests |
| Desktop shortcut / auto-start separate, default OFF | Already implemented | Two unchecked tasks, previous tasks disabled, cleared on first finish display, separate opt-in actions and ownership tests |
| Progress percentage | **Gap fixed** | Core publishes 0–100 but bar maximum is 1000. Core callback now uses the same `UpdateBrandedProgress(Percent, 100)` renderer as Inno |
| Cancel / X during core installation | **Gap fixed** | Inno disables Cancel before `PrepareToInstall`. Adapter enables it only during the owned core wait and restores the original state in `finally`; existing cancellation signal remains the only cancellation route |
| X on finish page | **Gap fixed** | Inno disables SC_CLOSE at wpFinished and native cancellation handles only preinstall/install. Finish X now enables that menu item and uses the existing non-launching Close action; all earlier close events delegate to the original handler |
| Installer error copy | **Gap fixed** | Core status contained arbitrary exception details. Installer now displays fixed cancellation/failure text; raw details remain in core-result.json |
| Pre-login update / repair | Already implemented | Compact footer and About share application UpdateController; repair, unavailable capability and trust failure tests |
| Post-login update / repair | Already implemented | Actual SystemPage uses the same UpdatePanel/controller; no second engine |
| Optional update | Already implemented | Login stays enabled, including direct optional-footer test |
| Mandatory update | Already implemented | Controlled inline state, no raw exception, blocks remembered/interactive login and late identity/bootstrap results; restriction survives failed/pending update operations |
| One click through apply / restart / health | Already implemented at source boundary | Controller consumes prepared result once, canonical adapter delegates to existing core; signed inert lifecycle tests verify close/relaunch/health ordering and recovery |
| No browser / CMD / user-supplied local feed | Already implemented | UI imports/launches are prohibited by tests; hidden canonical runtime invocation, configured service and no feed-input UI. Loopback HTTP fixtures are test data only |

The Inno event findings were checked against the exact
[7.1.0 WizardForm source](https://github.com/jrsoftware/issrc/blob/is-7_1_0/Projects/Src/Setup.WizardForm.pas)
(`PrepareToInstall`, `UpdateCurPageButtonState`, `FormClose`) and
[MainForm.Close](https://github.com/jrsoftware/issrc/blob/is-7_1_0/Projects/Src/Setup.MainForm.pas).
Existing tests that prohibited every OnClose hook were narrowed to require the
original native handler for all non-finish pages. No native cancel handler was
replaced, and no process termination or installer engine was added.

Desktop owner files and `desktop/update_lifecycle.py` have no code changes.
`scripts/per_user_update_core.py`, issuer migration and live email/security
configuration were not edited. DEPENDS_ON_02 = NONE_FOUND_IN_SOURCE_AUDIT.

## Verification

Evidence directory:
`E:/Codex/Temp/MF-MAIN3-03-INSTALLER-UPDATE-UX-20260912-01`.

The three new core progress/cancellation/error tests failed on the original
adapter before the fixes. The new finish-X test also failed on original source.
The final suite includes all `installer/inno/tests`, Desktop pre-login/update
flow/core regression tests, `scripts/test_startup_update_repair.py`,
`scripts/test_one_click_update_core.py`, and the existing-parent/unknown-target
inert filesystem cases. The final combined run passed **203 tests**, with zero
failures or skips, in 48.74 seconds. Evidence is in `final-tests.log` and
`final-tests.xml`.

Runtime: `E:/Codex/Temp/mf-e1-02b-verify-py313/Scripts/python.exe`.
The test process sets `QT_QPA_PLATFORM=offscreen`, `PYTHONUTF8=1`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and redirects
TEMP/TMP, pytest basetemp/cache and JUnit output to the evidence directory.

Direct baseline runs on existing Python 3.12 and 3.13 environments each produced
132 passes and the same pre-existing PySide/Pydantic circular-import failure in
the registration test. The final runner preloads Pydantic's public model imports
before pytest/Qt (`BaseModel`, `ConfigDict`, `Field`, `StringConstraints`,
`field_validator`, `model_validator`). With that test-process import ordering,
the 140 Desktop/lifecycle cases passed before the final combined run. No
dependency, account implementation or import hook was modified for this issue.

The three compiler tests use real Inno 7.1 with `--output=no`, including >2 GiB
and >4 GiB required-space constants, and assert no warning or installer output.
Their inputs are tiny inert compiler fixtures. This is not a package rebuild.

```text
REAL_INSTALL_QA = NOT_RUN
FINAL_PRODUCT_PASS = NOT_CLAIMED
GITHUB_ACTIONS_EXECUTED = 0
INSTALLER_LAUNCHES = 0
UAC_PROMPTS = 0
DPI_MUTATIONS = 0
WORKER_STATUS = SILENT
```

Physical X/cancel behavior, final package rendering and real process
exit/relaunch/health remain release QA. The source receipt does not assert a real
installation, public feed deployment, issuer readiness or final product pass.

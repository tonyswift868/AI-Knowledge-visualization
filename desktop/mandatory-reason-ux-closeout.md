# Mandatory reason consumer closeout

```text
STATUS = PASS_MANDATORY_REASON_UX_CLOSEOUT
DISPATCH_ID = MF-MAIN3-03-MANDATORY-REASON-UX-CLOSEOUT-20260912-03
TASK_ID = MENTORFLOW_MANDATORY_REASON_HUMAN_UX_CLOSEOUT_V1
OFFICE_REUSED = YES
WORKTREE = E:/Codex/Data/worktrees/bb70/MentorFlow
BRANCH = worker/main3/03
START_HEAD = 3443f84130c0b114c24f68997c5a23958f1e27a5
02_CONTRACT_READ_ONLY = YES
02_EXACT_REFERENCE = 521ba9fb0248c2dc973cbba6cc44e6d8f0f6baeb
```

## Office and public contract

Initial HEAD/branch matched the dispatch and the index/worktree were clean.
The `.git` file and Git worktree registration point to the private HEAD/index
under `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow3`; its `gitdir`
points back to this office. No Git directory/index overrides or index lock were
present. No worktree was created. Root performed all changes; no children ran.

Read-only `git show` of the exact 02 reference confirms that the public startup
snapshot supplies `state`, `current`, `latest`, `mandatory_reason`, and
`min_supported_version`. Its reason is `security`, `protocol`,
`data_compatibility`, or `support_floor` for a mandatory gate, and empty for
other startup states. The effective floor and historical reason evidence remain
02's responsibility. A retained mandatory gate can target an optional latest
release; the consumer displays `latest` without choosing an intermediate target.

The existing adapter already forwards the reason to this branch's presentation
layer. This dispatch changes neither `desktop/update_lifecycle.py` nor either
core policy file. No 02 commit was merged, cherry-picked, or checked out.
No integration branch or other worker ref was written.

## Exact changes

| Consumer gap | Closeout |
| --- | --- |
| Three short category messages; no explicit support-floor message | Four stable Chinese messages with the requested meaning |
| Generic explanation incorrectly described unknown reasons as a support floor | Unknown/empty/non-string values use the neutral fallback; internal values are never displayed |
| Same mandatory target could retain an obsolete reason when a fresh public reason was empty | Fresh MANDATORY_UPDATE snapshots replace the explanation, including unknown/empty values |
| Transient snapshots omit/clear the reason | The previous mandatory explanation survives downloading, network/validation failure and cancellation |
| Optional snapshot might carry stale/future reason text | Only core state controls the existing gate; optional choices and login remain available |

The mandatory status contains “必须更新到最新版本 X” and the reason below it.
The existing current-version label, latest-version/size label, release notes,
update/retry button and Exit button remain in the same presenter. The general
support-ended paragraph was replaced by the appropriate reason, avoiding a
duplicate or unsupported claim for security/protocol/unknown categories.

| Public reason | Visible explanation |
| --- | --- |
| security | 当前版本存在必须修复的安全问题，需要更新到最新版本后才能继续使用 MentorFlow。 |
| protocol | 当前版本已无法兼容最新服务，需要更新到最新版本后才能继续使用 MentorFlow。 |
| data_compatibility | 当前版本的数据兼容性已发生变化，需要更新到最新版本后才能继续使用 MentorFlow。 |
| support_floor | 当前版本已停止支持，需要更新到最新版本后才能继续使用 MentorFlow。 |
| Unknown/missing value | 当前版本需要更新到最新版本后才能继续使用 MentorFlow。 |

Changed files:

- `desktop/update_panel.py`: public reason → Chinese presentation only.
- `desktop/update_controller.py`: reason presentation retention/replacement only.
- `desktop/tests/test_mandatory_reason_ux.py`: 22 new public-consumer cases.
- `desktop/tests/test_optional_mandatory_update_ux.py`: update three copy expectations.
- `desktop/tests/test_prelogin_update_ux.py`: retain mandatory/latest assertions with the new wording.
- `desktop/mandatory-reason-ux-closeout.md`: this receipt.

## Validation boundary

The focused suite passed **97 tests**, including the 22 new cases. It covers
all four categories, unknown string/empty/null/list/object values, current 1.0.0
with historical floor 1.2.0 and latest 1.4.0, optional residual reasons both before
and after a gate, and an empty reason during network/validation/cancel failures.
Existing tests cover main-window blocking, no skip, close exits, later,
update/restart and exit-time update. Offscreen layout evidence was also inspected.

Final regression: **313 passed in 91.86 seconds**. `git diff --check` passed.
Evidence is under
`E:/Codex/Temp/MF-MAIN3-03-MANDATORY-REASON-UX-CLOSEOUT-20260912-03`:
`focused-tests.log`, `regression-tests.log`, and the offscreen PNGs in the pytest
temporary directories.

Runtime: Python 3.13.14 / PySide6 6.11.1 / pytest 8.4.2. Qt ran offscreen with
isolated INI settings, temporary data, and synthetic public/core fixtures.
TEMP/TMP point to the evidence directory; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONUTF8=1`, `QT_QPA_PLATFORM=offscreen`.
Pydantic types are preloaded to avoid the existing local PySide/Pydantic import
order failure. The final command was:

```powershell
& 'E:/Codex/Temp/mf-e1-02b-verify-py313/Scripts/python.exe' -c 'from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator; import pytest,sys; sys.exit(pytest.main(sys.argv[1:]))' -q desktop/tests/test_mandatory_reason_ux.py desktop/tests/test_optional_mandatory_update_ux.py desktop/tests/test_prelogin_update_ux.py desktop/tests/test_update_ui_flow.py desktop/tests/test_update_ui_core_regression.py desktop/tests/test_login_smooth.py desktop/tests/test_e109_integration_binding.py desktop/tests/test_e109_capability_config.py desktop/tests/test_login_gate_bootstrap.py desktop/tests/test_mature_update.py scripts/test_startup_update_repair.py scripts/test_one_click_update_core.py --basetemp E:/Codex/Temp/MF-MAIN3-03-MANDATORY-REASON-UX-CLOSEOUT-20260912-03/regression-tmp -o cache_dir=E:/Codex/Temp/MF-MAIN3-03-MANDATORY-REASON-UX-CLOSEOUT-20260912-03/pytest-cache --tb=short
```

Tests consume synthetic public snapshots matching the exact 02 contract through
the current adapter/controller/panel. They do not import the unmerged 02 policy
implementation or fabricate signed-feed evidence. Batch02 combination and its
regression remain A-owned, as requested. The previous dispatch's PUBLIC_MANDATORY_REASON
dependency is resolved for this consumer against the supplied exact reference;
this is not a claim that the branches have already been integrated.

```text
MANDATORY_REASON_CONSUMED_FROM_PUBLIC_SNAPSHOT = YES
SECURITY_REASON_UX = PASS
PROTOCOL_REASON_UX = PASS
DATA_COMPATIBILITY_REASON_UX = PASS
SUPPORT_FLOOR_REASON_UX = PASS
UNKNOWN_REASON_FALLBACK = PASS
MANDATORY_TARGET_LATEST = YES
OPTIONAL_LOGIN_ALLOWED = YES
MANDATORY_LOGIN_BLOCKED = YES
MANDATORY_MAIN_WINDOW_BLOCKED = YES
CORE_POLICY_CHANGED = NO
LOGIN_AUTH_CHANGED = NO
SIGNED_FEED_PARSED_BY_UI = NO
SEMVER_OR_HISTORICAL_FLOOR_CALCULATED_BY_UI = NO
INTERMEDIATE_TARGET_SHOWN = NO
MANDATORY_SKIP_AVAILABLE = NO
MANDATORY_NETWORK_BYPASS = NO
REAL_INSTALL_QA = NOT_RUN
BATCH02_COMBINED_REGRESSION = NOT_RUN_A_OWNED
REMOTE_PUSH = 0
GITHUB_ACTIONS_EXECUTED = 0
WORKER_STATUS = SILENT
```

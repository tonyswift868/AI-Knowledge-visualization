# E108 one-click update UI — source delivery 03

DISPATCH_ID = MF-E108-W03-ONECLICK-UPDATE-UI-20260909-03
TASK_ID = MENTORFLOW_ONE_CLICK_UPDATE_UI_V1
EMPLOYEE_SLOT = 03
BASE_DELIVERY = 7235a4408c80baafd3e81156ca0ec26ffe066c41
UPDATE_UI_USER_READY = SOURCE_READY
NO_FOREGROUND_USER_TEST = true
WORKER_STATUS = SILENT

## Scope and dependency binding

This is a new, linear UI feature commit on the requested base, not a repair of
an old Enterprise receipt. No earlier receipt/report is edited. There is no
merge, rebase, additional core commit, dependency mutation, push or deployment.

Worktree: `E:\Codex\Worktrees\MF-e108-updater-ui-w03`
Branch: `codex/e108-updater-ui-w03`
The delivery commit and tree are emitted with the final receipt; the commit
containing this document is the complete feature batch.

07 has not supplied a new frozen high-level delivery commit. Per the explicit
user instruction, this does not block UI/source acceptance. The semantic
reference was read from
`E:\Codex\Worktrees\MF-e1-account-core-e107-final\docs\PER_USER_CORE_INTERFACES_V1.md`.
No source or private implementation from that newer worktree was consumed.

`desktop/update_lifecycle.py` defines the narrow `UpdatePort` boundary:

- `check_latest()`
- `download_latest(progress, cancel)`
- `update_to_latest()` (optional progress/cancel/before-apply hooks)
- `apply_and_relaunch()`

The snapshot contains presentation state, current/latest version, release
prose, transferred/total bytes and availability/busy/error category only.
Canonical states are CHECKING, UP_TO_DATE, UPDATE_AVAILABLE, DOWNLOADING,
PREPARING, DEFERRED_TO_NEXT_LAUNCH and RECOVERABLE_ERROR.

The frozen adapter delegates to the existing service check/download/cancel/apply
methods. Existing per-user `remember_download` and `handoff` remain responsible
for admission, pending persistence, exact App identity, session fencing and
the installed coordinator. Protected mode retains its existing handoff. The
adapter passes opaque stage/envelope arguments; UI code does not parse them.
The existing read-only transport busy scheduling seam is confined to this
adapter so cancellation cannot enable retry while the socket still owns a
request. Neither the downloader nor that seam's implementation was changed.

When 07 supplies its exact delivery commit, bind the agreed capabilities here;
the panel/controller flow does not need a second redesign. Installed-package
validation and actual coordinator exit/update/relaunch qualification remain
separate from this SOURCE_READY receipt.

## Behavior

ONE_PRIMARY_UPDATE_BUTTON = YES
CLICK_STARTS_DOWNLOAD = YES
SECOND_RESTART_BUTTON_REQUIRED = NO
BROWSER_NAVIGATION = 0
EXTERNAL_DOWNLOAD_PAGE = 0
CMD_USAGE = 0
SETUP_INSTALLER_LAUNCH = 0
AUTO_RELAUNCH_UI_FLOW = SOURCE_PASS_CORE_HANDOFF
OFFLINE_INLINE_ERROR = PASS

- Settings and About share one application controller. The single primary
  button is `更新到最新版本`; one click starts download on the worker.
- The download view has percentage, transferred/total MB or GB, and `取消`.
  Duplicate clicks are serialized. Cancel, close or session ending stop the
  automatic continuation, including cancellation just before the Qt callback.
- A successful ready result automatically consumes the continuation once,
  saves UI geometry/navigation on the GUI thread and calls apply/relaunch on
  the worker. There is no second restart action or quit-time checkbox.
- Preparing text is `正在准备更新…` / `MentorFlow 将自动重新启动`.
  Frozen core preparation before download still displays the download state.
- Only an explicit core deferred/pending result displays
  `更新已下载，将在下次启动时完成。`; a local window/session guard cannot
  invent that promise. Deferred results override stale ready hints.
- Up-to-date text is `✓ 已是最新版本`; the current version comes from the
  runtime snapshot, not a hardcoded 1.4.1.
- Offline errors display `暂时无法检查更新，请稍后重试。`; validation errors
  display `更新文件验证失败，当前版本不会受到影响。`. Interrupted network
  requests are not mislabeled as user cancellation.
- Release notes default to at most three logical lines / 240 characters.
  `查看更新内容` expands a bounded read-only text area inside the same card.
  Diagnostic lines, URLs, manifest/hash/receipt/helper details and arbitrary
  exception messages are not rendered. No webview/browser navigation is used.
- Old UI cache/pending/hash flags are not authority for automatic application.
  Core remains the owner of next-launch validation and pending work.

## Verification

Final focused gate: **95 passed, 0 failed, 0 skipped in 58.57 seconds**.

```powershell
$env:PYTHONPATH='E:\Codex\Worktrees\MF-e108-updater-ui-w03'
$env:QT_QPA_PLATFORM='offscreen'
$env:TEMP='E:\Codex\Temp\u108'
$env:TMP=$env:TEMP
& 'E:\aiPlay\AI-Knowledge visualization\.venv\Scripts\python.exe' -m pytest `
  desktop/tests/test_update_ui_flow.py `
  desktop/tests/test_update_ui_core_regression.py `
  desktop/tests/test_mature_update.py -q --tb=short `
  --basetemp=E:/Codex/Temp/u108/oneclick-delivery `
  --junitxml=E:/Codex/Temp/u108/oneclick-delivery.xml --maxfail=3
```

Evidence: `E:\Codex\Temp\u108\oneclick-delivery.xml`. Six offscreen card PNGs
are retained below the same basetemp. They are synthetic source rendering, not
an installed-Windows or foreground acceptance claim. The offscreen fixture
explicitly loads existing Windows fonts; no font/tool installation occurs.

The gate includes real signed localhost download, cancel/retry, offline,
signature and withdrawal negatives, shared Settings/About behavior, deferred
normalization, duplicate/cancel/close races and absence of external launch
routes. Existing session, coordinator, transaction-order and rollback tests
are retained in `test_update_ui_core_regression.py`.

The existing script-level update-card test was changed only to wait for the
one-click chain instead of clicking a removed second button. Its E:-isolated
source wrapper uses the unchanged signed transaction with fixture
registry/shortcut/process boundaries. It does not execute an installed App.
The historical MainWindow supervisor was made offscreen and its obsolete
second-click/next-start UI exercise removed; that supervisor was **not run**
for this delivery.

The source-test launch guard rejects browser, desktop URL, shell and process
launch APIs. Coordinator command tests replace launches with recording fakes.
No setup executable, CMD, browser or foreground App was launched. All data and
settings fixtures are synthetic and isolated under E:. No real account data,
provider or installed product was touched.

Compilation and `git diff --check`: PASS. Production per-user core, service,
downloader, GUI runtime binding and the previous 20260908 receipt are unchanged
from BASE_DELIVERY. Shared Enterprise contracts, migrations, dependency/lock
files and installer/transaction implementations are not part of this batch.

No foreground/package acceptance is claimed. The current requirement is
SOURCE_READY, with final 07 binding confined to the adapter.

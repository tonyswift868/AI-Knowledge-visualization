# E1-07 mature updater continuation: source-only partial qualification

STATUS = PARTIAL_RETURNED
DISPATCH_ID = MF-E107-DESKTOP-MATURE-UPDATER-V1-20260907-04
TASK_ID = MENTORFLOW_WINDOWS_MATURE_SELF_UPDATE_V1
RESUMED_FROM_QUOTA_INTERRUPTION = YES

This is the same dispatch, not a new updater root or installer rebuild. The quota
interruption is not recorded as a product-test failure. No production acceptance,
packaged end-to-end acceptance or ready-for-user-update claim is made.

## Source and scope

- BASE_SHA: 4c6cf8ccc5d96dbbf97675c71b08faed7474da98
- BASE_TREE: d389fbb08a54a2c6dec1a3386db94cc7f3199021
- WIP_CHECKPOINT_COMMIT: 315a582a0676e63a48f94d8454e3a6313a0f65b5
- WIP_CHECKPOINT_TREE: 8d9bc9f7752b8acd367921cb60b00d0799d65306
- Checkpoint followed syntax/import sanity and 27 focused passing tests. It is
  explicitly WIP_CHECKPOINT_NOT_QUALIFIED, not final product acceptance.
- Final source commit/tree are returned in the terminal chat receipt; this report
  is part of that commit and does not embed a self-referential commit identity.

Owned files: desktop/system_page.py, desktop/update_service.py,
desktop/update_protocol.py, desktop/update_download.py, desktop/update_panel.py,
desktop/tests/test_mature_update.py, scripts/windows_install_transaction.py,
scripts/windows_update_apply.py, scripts/test_mature_update_transaction.py,
docs/adr/004-windows-incremental-updater.md,
docs/windows-update-release-contract-v1.md and this report.

Unrelated dirty edits preserved and excluded from both commits:

| File | SHA-256 of preserved working bytes |
| --- | --- |
| scripts/measure_installed_login_gate.py | C4B66F8F06FAD62E41F4AB31B953BF4BA27B05D6D9FAE89D4A41BEBDDF3522E6 |
| scripts/test_login_form_probe.py | 3B192B802E82FD8B5377DD4279DBC30A580650DFF6B0ADBBE6972935A1A8D868 |

No dependency/lock changes, installs, server operations, formal user-data writes,
live E:\MentorFlow operations, push, merge, reset or revert. No workers delegated.

## Decisions and implemented boundary

Existing Settings update card and update_service facade/cache are reused. The old
full-Setup API remains for explicit compatibility callers but is not the new card's
default action. Missing signed production configuration disables the new workflow;
it does not silently launch the legacy installer.

FILE_LEVEL_INCREMENTAL, not block/binary delta: hash and copy unchanged local
files, download new/changed objects, omit deleted files only in new staging,
verify exact inventory, then reuse the existing installer transaction. Full
fallback uses the complete signed object set and the same update transaction,
never uninstall/first-install wizard. Corrupt signature/hash never becomes a
successful fallback.

ADR 004 qualifies Velopack as a mature documented reference (Python, custom
installation location and per-machine MSI supported) without migrating the current
App/receipt/ACL/NSIS ownership. MSIX remains a differential reference, not adopted.
HTTP_RANGE implements a transport Protocol; BITS is a documentation-qualified
production candidate/follow-up, not a runtime-tested job implementation.

Ed25519 uses existing cryptography; strict canonical metadata binds identity,
version/channel, paths/hashes, freshness and rollout. Per-channel monotonic signed
witnesses reject replay/equivocation. Helper-side witness is independent of the
ordinary cache. Key rotation uses overlapping build-provisioned public keys;
no downloaded key is trusted. No production private keys or endpoints added.

## Reproducible focused checks

Python: E:\aiPlay\AI-Knowledge visualization\.venv\Scripts\python.exe.
Run from the bound worktree with QT_QPA_PLATFORM=offscreen and TEMP/TMP under:
D:\CodexScratch\MentorFlow\MF-E107-DESKTOP-MATURE-UPDATER-V1-20260907-04.
Use -B -m pytest -p no:cacheprovider; no full payload copy or installer run.

| Evidence log | Result / scope |
| --- | --- |
| sanity-resume.log | 27 passed; checkpoint sanity |
| protected-final.log | 34 passed: 12 transaction fixture tests + 22 original installer transaction tests |
| receipt-tests.log | 81 passed, 1 failed; sole failure was beta fixture publisher retaining stale signed data |
| feed-fixture-recheck.log | 44 passed after publisher fixture correction; includes beta and all protocol/download/UI tests |

The unchanged legacy update-service tests (4) passed in receipt-tests.log. The
final tested groups total 82 distinct passing tests across the latest applicable
runs; this is not falsely represented as one 82-pass run.

Earlier apply failures exposed delayed NTFS notification snapshots and the test
subprocess's base-Python import path. Existing whole-snapshot verification retry
was reused; the child harness received the parent import path. Five affected tests
then passed in apply-recheck.log. No verification check was disabled.
An exploratory settings-navigation run printed an unrelated incomplete navigation
fixture AttributeError; it is not cited as full main-window acceptance. The focused
suite instead exercises the actual SystemPage update card with an offline source.

## Actual network measurements

Fresh synthetic 1.1.0 -> 1.1.1 with an unchanged runtime and tiny frontend change:

| Metric | Value |
| --- | --- |
| FULL_BYTES | 4,719,121 |
| ACTUAL_DOWNLOADED_BYTES | 505 |
| SAVED_BYTES | 4,718,616 |
| SAVING_PERCENT | 99.98929885459602 |

Measured localhost HTTP response-body asset bytes, excluding metadata/HTTP/TLS
framing; no prior cache prefetch. This is not a measured saving for the existing
2,100,456,271-byte, 14,040-file shipped payload.

Separate tiny transaction fixture: full logical size 1,631 bytes; incremental
download 1,345 bytes, full fallback 1,618 bytes (identical-object deduplication).
Actual Python fixture processes launch version 1.1.1 after apply and 1.1.0 after
explicit rollback. This is not MentorFlow.exe, native UAC or real registry/shortcut
qualification. ACL/elevation and integration adapters are mocked in those tests.

## Qualification outcomes and honest limits

PASS_FIXTURE: no update; offline and TCP-blackhole bounded return with Qt timer
responsive; cancel while old fixture process remains alive; disconnect/Range/200
resume with completed-object reuse; corrupt asset; tampered metadata; expiry;
replay including separate helper witness; wrong app/channel; downgrade/SemVer;
unknown key and overlap rotation; rollout/withdrawal/recheck-before-apply; OS lock;
low disk; new/deleted/corrupt-base reconstruction; full fallback; fixture user-data
hash preservation; source Settings card download/apply button wiring.

PASS_FIXTURE: transactional apply/receipt/restart and explicit rollback; controlled
helper termination after swap, before receipt publish and after receipt publish;
recovery preserves launchable old/new plus previous; retained previous blocks a
second update while health is pending; actual early-exit detection and ready
PID/version observation. E:/D: selectable-root checks are contract-only, not actual
installations on both volumes.

NOT_RUN / PARTIAL in production:

- No signed production feed/public-key bundle or Authenticode certificate provisioned.
- Source helper is not packaged or wired to an actual once-only UAC handoff.
- Real product multi-instance close/wait, ordinary-user restart and LoginGate/main
  ready nonce/binary/receipt attestation are not complete. Production apply disabled.
- Automatic product crash rollback and mark-healthy/retention cleanup unfinished;
  retained staging also safely blocks uninstall until health/recovery is resolved.
- Update-on-exit and repair UI not active; damaged-base reconstruction is tested,
  not same-version transactional repair acceptance.
- Multi-instance UI does not share persistent live progress; locks prevent concurrent
  mutations. Backoff is in-memory; HTTP jobs do not continue after App exit.
- No BITS idle/metered policy, broad cache cleanup or durable updater log sink.
- Qt tests are offscreen source tests; shipped installer/EXE was not rebuilt,
  launched, installed or newly qualified by this updater task.

## Existing artifacts preserved

Root: E:\Downloads\MentorFlow-UAT-Current. Read-only SHA-256 checks this round:

| Artifact | SHA-256 |
| --- | --- |
| Installer/MentorFlow-1.1.0-win-x64-Setup.exe | 44F5054747708A0598F1EBC210DDE4911A12454682A179611748B2967FE2D849 |
| Installer/MentorFlow-1.1.0-win-x64-Setup-SpaceFixed.exe | E5B1027823137DFD6C4ECFEAC3B21D155C177F1105F9058EB1E8666E980F87FA |
| APP/MentorFlow.exe | C0F0A9B7C9921FFB1C185B3AD026A3A7C9E32046F4405C67972B66B4C72BB833 |
| APP/SELF-CHECK/package-manifest.json | 73ECC2E0BFBC1CDF481CC28E5A832527C811A48EC5CC725E1EF26E61C654C9EE |

NEXT_BLOCKER: separately qualify production trust/build/helper/UAC/restart/health
and activate the signed release configuration before user-facing self-update use.
Existing binary artifacts do not contain this source-only updater delivery.
WORKER_STATUS = SILENT

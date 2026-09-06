# E107 Desktop closure — bounded source progress, not final package acceptance

Dispatch: MF-E107-DESKTOP-CLOSURE-CONTINUE-20260906-02.
Task: MENTORFLOW_ACCOUNT_DESKTOP_COMPLETE_DELIVERY_V1.
Starting clean source: 89315a1f66a4af4a1522f19e3a93008f37a144ed,
tree dafafb85170e65fe28009e6d556d1b3cd2a9e4bd.

Result: PARTIAL_RETURNED. Resource indicator fixed; startup source overhead
reduced, but the 3-second response / 8-second interactive target is NOT proven
or met by the available package. Server import contract remains PENDING_A.
No polling of A, server change, formal SQLite read/write, formal data import,
push, branch deletion, shared Git configuration change or package build.

## Source changes

- Package path validation resolves each candidate once instead of resolving
  candidate/root repeatedly; symlink detection reuses the lstat result. One
  resolved inventory supplies privacy checks. After all hashes, a no-follow
  component-by-component lstat scan still rejects late additions, reparse
  points, hardlinks, nonregular files and path escapes under a revalidated root.
- Every immutable manifest payload is still read and SHA-256 checked on every
  verification. No persistent cache, mtime/size-only trust, manifest exclusions,
  TLS changes, or execute-before-verification path was introduced.
- MainWindow and business page imports move from application construction to
  authenticated `_show_main`. Login, session restoration, bootstrap and account
  isolation behavior are retained. API/runtime imports are unchanged.
- Account settings consumes the same reconciled resource snapshot as the
  recovery banner. AVAILABLE/RELINKED, missing, partial missing and unknown
  states are distinct. Counts alone cannot imply missing files. Profile change
  clears the projection; refresh/relink replaces it without deleting resources.

## Timings and limitations

All package scans below are read-only against the preserved old package:
E:/Downloads/MentorFlow-Login-Final-20260906/MentorFlow.
It was built from 1531c65116063ec606ed1c623acd29fb68e9e8b7, not this source.
No OS cache flush or idle-machine qualification occurred; concurrent host work
and cache differences prevent claiming a stable percentage improvement.

- Baseline source verifier: 175.434 s, PASS, 14,038 manifest payloads.
- Intermediate source measurement before final late-addition scan: 16.158 s.
  This is not the final source timing.
- Final source measurement 1: 105.293 s; resolved inventory 4.604 s,
  final no-follow scan 1.257 s; PASS.
- Final source measurement 2: 19.779 s; inventory 3.709 s, privacy 2.860 s,
  hashing 11.053 s, final no-follow scan 1.271 s; PASS.
- Separate fresh Python process: desktop.main import 0.252 s with
  desktop.main_window absent from sys.modules; deferred business import 1.030 s.
  This is source import timing, NOT a packaged interactive-form measurement.

Actual unchanged desktop shortcut measurement at 2026-09-06T04:21:12.7928114Z:
connection phase 8.948 s; native integrity phase 105.075 s; app process started
at 105.450 s from launcher start. LoginGate was visibly present/responding in
the process inspection, but the harness did not certify an editable login form
within 240 seconds. Its first close wait exceeded 20 seconds and aborted before
saving the window timestamp. Exact first-visible and editable-form times for
this run are therefore NOT_MEASURED / NOT_QUALIFIED, not zero or PASS.
A second normal CloseMainWindow request exited successfully, without force kill;
launcher recorded exit code 0 at 276.521 s. App, launcher and owned SSH process
IDs 57128 / 46608 / 81200 were confirmed absent afterwards.
The trace remains in the isolated package-UAT runtime-temp directory.

Previously recorded old-package repeat window/form times 35.471 / 42.501 s
remain historical evidence only. No initial-install or true cold-start claim.
Full hashing remains material work; further startup qualification is required
after the one authorized final integrated build. Do not mark startup complete.

## Regression evidence

- Focused run: 80 PASS, zero SKIP/XFAIL, including account switching, login,
  original `test_recent_history_must_not_cross_accounts`, late nonempty callback
  isolation, immutable EXE/DLL tamper rejection, legal/invalid external config,
  fixed TLS/environment boundary, single resolved scan and late-file rejection.
  E:/Codex/Temp/e107-closure-02-focused.xml.
- Additional final tests: 2 PASS (real LocalAssetLifecycle + metadata sync + Qt
  indicator with zero Vault assets; lazy business-import boundary).
  E:/Codex/Temp/e107-closure-02-binding-final.xml.
  The new real-binding test initially used positional arguments for a keyword-only
  relink API; that test-call error was corrected, then both tests passed.
- Full package privacy module on final verifier: 37 PASS, 4 SKIP due Windows
  symlink privilege WinError 1314. The combined earlier run also included the
  above not-yet-corrected test-call failure and one passing lazy-import test;
  it must not be represented as an entirely green run.
  E:/Codex/Temp/e107-closure-02-security.xml preserves that raw result.
  Symlink skips are limitations, not passed security checks. No original
  isolation failure was changed to SKIP/XFAIL.
- Existing A 31 / E107 55 historical evidence reused, not added as new results.

No final source-to-package binding, packaged login/registration completion,
remember/restart, live A/B isolation or owner import is claimed by this receipt.
The existing package and fallback remain untouched. Final integration/build is
deferred until both sides' source/contracts are ready, as explicitly requested.

## Desktop binding inventory for A's later delivery (no proposed endpoint)

Source inspection after 13334cf10c020b75320b7d56e4cf43df2b1346f4:

- `desktop/main.py::_start_account_bootstrap` calls
  `startup_flow.bootstrap_enterprise_account` on the login generation's worker;
  accept the verified identity only through the existing generation gate.
  Do not use a username hint as import ownership.
- `startup_flow.AccountBootstrapResult` carries identity/profile, metadata_api,
  metadata_sync, snapshot, Vault manager and local lifecycle. These are the
  existing account-scoped handles available to a future import UI action.
- `AccountMetadataSnapshot` reads `workspace`, `plans`, `progress` and
  `resource_manifest.assets`. `SyncedResource` holds resource_id,
  expected_fingerprint_sha256, pathless server_metadata and current device state.
  The service validates account issuer/subject and selected organization/workspace.
- `EnterpriseAccountMetadataApi.account_metadata` currently caches its first
  response. `_PreloadedMetadataApi` also retains bootstrap's first payload.
  `AccountMetadataSyncService.refresh_device_state` only reconciles local
  bindings against the cached snapshot; it does NOT fetch newly imported plans
  or progress. Contract binding must explicitly refresh/recreate the accepted
  account's metadata snapshot after a successful server import. Merely calling
  refresh_device_state would show stale learning metadata.
- `main._show_device_migration` connects the center's snapshot_changed signal
  to `MainWindow.set_device_recovery_snapshot`. The resource banner and account
  indicator now consume that same snapshot; retain this single publication path
  and its account generation ownership when wiring import completion.
- `account_portability.import_account_package(archive_path, target_vault_root,
  expected_account_identity=..., allow_personal_local=False)` is the existing
  local archive boundary, not a server Plan import transport. Source snapshots,
  source hashes, per-item mappings, confirmation binding and durable import
  receipts must be reconciled with A's actual contract before implementation.
  Original bytes, device paths and local resource bindings remain local.

No import route, request schema, idempotency key or authorization semantics were
invented here. No real source inventory was reopened. This is a source call-site
handoff, not an implemented import adapter or a claim of recovered owner data.

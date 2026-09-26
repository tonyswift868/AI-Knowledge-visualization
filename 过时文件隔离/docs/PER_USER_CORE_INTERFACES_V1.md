# E108 core consumption contract v1

Scope: per-user core, not final installer/update-panel UX acceptance. The only
default downloader is `desktop.update_service.MatureUpdateService`. Protected
mode remains separate; no protected-parent ACL is required by this contract.

## 02: installer boundary

Module `scripts.per_user_install`:

| Call | Contract |
| --- | --- |
| `validate_parent(parent)` | Absolute, existing, local, non-reparse, writable parent; returns `parent/MentorFlow`. Creates no parent. |
| `validate_target(root)` / `detect_existing_install(root)` | `{root, existing, version}`; signed installed identity required; unknown nonempty target rejects. |
| `install_per_user(parent, source, envelope)` | Signed full source -> independent staging -> full verify -> swap -> real health. Same-version signed repair is allowed; downgrade/equivocation is not. |
| `write_installed_state(root, envelope)` | Fresh signed admission and complete App verification before publishing identity. Never caller-provided trust keys. Install performs this step within its transaction; UI normally does not publish state directly. |
| `uninstall_per_user(root)` | Requires stopped App and signed exact current tree. Recoverably quarantines App and identity inside the selected root; preserves unknown files, user data, updater/previous and diagnostics. Returns quarantine path. No recursive deletion or registry write. |

Frozen `UserRuntime.exe` CLI (ordinary medium token only):

```
preflight --root <existing-parent>/MentorFlow --result <json>
preflight --parent <existing-parent> --result <json>
install --root <root> --source <signed-App-tree> --release <envelope.json> --result <json>
uninstall --root <root> --result <json>
```

`install` also accepts `--parent`, mutually exclusive with `--root`, computing
the final child inside core. `--status-file` emits UTF-16 INI stage/percent/error for an installer adapter.
`--cancel-file` cancels owned staging work; no success receipt on failed initial
health. Cancellation during health waits for the bounded health phase before
rollback. 02 owns shortcuts, product registration, installer appearance and final
deletion policy. Remove registration only after core uninstall succeeds. A root
with quarantined/unknown remnants is deliberately not silently reused as empty.

## 03: update/lifecycle boundary

`scripts.per_user_update_core.configured_service(root)` returns the existing
MatureUpdateService using signed installed configuration and compiled keys.
Invoke blocking methods on the existing worker, not the Qt GUI thread.

| UI need | Core contract |
| --- | --- |
| Check | `service.check(manual=True)`, inspect `snapshot.state` and human message |
| Download/progress | `service.download()`; snapshot actual/expected/full bytes, update_path; poll snapshot from UI |
| Cancel/retry | `service.cancel_download()`; retry `download()` resumes verified objects; `close()` cancels owned work |
| Ready/restart | `prepare_update(root, service=service)` returns UPDATE_DOWNLOADED and ui_state READY_TO_APPLY |
| Apply/relaunch | `apply_update(root, pid=AppPID, hwnd_hex=AppHWND, service=service)` validates exact running App, closes it and automatically starts/health-checks target |
| Pending next launch | `apply_update(root, reason='shutdown'|'logoff'|'unsafe')` records PENDING_INSTALL without starting destructive work |
| Next normal launch | Stable updater calls `apply_update(root, reason='next_launch')` **before App starts**; requires no App process running; fetches fresh signed metadata and checks cached bytes again |
| State | `status(root)` returns current/target and lifecycle state; UI need not parse receipt/manifest |

Lifecycle states: UPDATE_DOWNLOADED, PENDING_INSTALL, APPLYING, CURRENT_HEALTHY,
PREVIOUS_HEALTHY. The Windows native shutdown guard records QUERYENDSESSION and
ENDSESSION independently of panel code. System teardown is checked again before
the destructive swap. A failed/offline/changed/withdrawn fresh feed does not
authorize cached installation; pending remains available for later recheck.

Equivalent lower-level methods: `request_app_shutdown`, `launch_health`
(`launch_updated_app`), `notify_health` (`record_health`), `recover`
(`rollback_previous`). Real health binds release, nonce, native PID, ordinary
token and responsive Qt window; no marker file substitutes for health.

CLI: `prepare-update`, `update-status`, `apply-pending --pid N --hwnd-hex HEX`,
`apply-pending --exit-reason next_launch`, `shutdown --pid N --hwnd-hex HEX`.
All take `--root` and optional `--result`. No shell/runas/UAC is used. An unsafe
exit reason needs no PID because it does not close or launch anything.

Integration responsibility: 03/A wire the next-launch hook into the sole stable
launcher. This core delivery provides/tests that callable/CLI, not a second
default launcher or a redesigned update panel.

## 04/A: release boundary

- Protocol: `mentorflow.signed-file-release.v1`, envelope `{signed,key_id,signature}`.
- `signed.schema_version=1`; this core v1 uses channel `stable`; SHA-256 / Ed25519.
  The protocol parser also recognizes beta, but beta install routing is not
  qualified by this delivery and must not be inferred from parser support.
- Manifest `mentorflow.windows-package-manifest.v1`: product/version/executable,
  exact file paths, byte sizes and SHA-256; canonical UTF-8 JSON is signed.
- `incremental_assets`: declared paths; `base_versions`: supported source versions.
- `full_release.format=file-set-v1`: complete signed path/size/hash/URL assets;
  fallback remains available when local inventory or base applicability fails.
- Verification: `desktop.update_protocol.verify_release`; signatures use
  updater-code-provisioned keys. `witness_version` validates signed monotonic
  metadata history. App ID/channel/expiry/minimums/withdrawal/rollout apply.
- Applied bytes are independently fully verified again by `transact` after
  reconstruction and immediately before swap; mutable receipts supply no key.
- Local UAT signing identity/feed is not a production release credential/feed.

No files in another employee's office or deployment are changed. Any pipeline
mismatch must be reconciled by A against this schema, not guessed by 07.

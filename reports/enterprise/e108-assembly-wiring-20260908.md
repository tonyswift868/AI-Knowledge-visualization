# E108 distribution assembly — wiring checkpoint, not a user candidate

Dispatch: `MF-E108-DISTRIBUTION-ASSEMBLY-20260908-02`.

Status: `IN_PROGRESS_WITH_RETURN_TO_OWNER`. No final App rebuild or user-facing
installer has been produced at this checkpoint. `READY_FOR_USER_TEST=false`.

## Exact intake already applied

Canonical source: `56bccbdec53014b602fb6ecf3997119d768c44bc` /
`9af3a97d72c2ed1b1fe9ce385123d95ec13d1b36`.

| Source | Exact input | Local integration commit |
| --- | --- | --- |
| Necessary historical installer/update dependency | `bcb4cb9e46e00b434c6c228e310e355500943428` | `273d0d46ef4b66508470adf275aceae505e521da` (exact merge, not branch tip) |
| 07 frozen per-user core | `b6e1d5da34814578d4f59309073a64fcf4711c21` | `e176ba8579d2fae24cbf8d57aafd9280e0f5e08d` |
| 02 Inno shell | `5a03eb36fbde8eaa3dfc2bbc3c92334421b650de` | `36f4e06964140cfd71a8c2f66bde79a265873c3c` |
| 03 replacement UI | `7235a4408c80baafd3e81156ca0ec26ffe066c41` | `c97a0dc464252adb473608e2918762c1180ffba6` |
| 04 publisher | `cad7469c7e17a72913dc69f62f14a823cb0ada57` | `922b4ee14fe9c16c8e4f2296e4e1bfad5fff10f3` |
| 04 evidence documentation | `ebef384d197d46fedd4fdd20faa645f9624b489d` | `aeaf16f3f00712a11e647da20bb17e1a19a8e206` |

The 03 superseded `4d541842` and the 07 cleanup-only `0b766ca` were not applied.
The 07 packaged source `422783b753d459b5e82c527aa789f4ac8d56eb98` and frozen
`b6e1d5da` have identical product/runtime code; the only difference is the
external qualification driver `scripts/qualify_per_user_core.py`.

07's original visible-window and timebox deviations remain **PARTIAL** in
`E:/Codex/Temp/MF-PerUser/E108-CORE-FREEZE-RECEIPT-20260908.md` and the original
execution-deviation evidence. They are not erased by source admission or by
these offline tests. Source admission is not final package acceptance.

## Integration wiring in this checkpoint

- User-selected Inno is the sole intended user-facing shell; NSIS stays as
  historical/backup source. Production Inno extracts into private scratch and
  invokes signed core `preflight` and `install`, not a flat copy into the root.
- The default parent is the existing `%LOCALAPPDATA%/Programs`; a missing parent
  is refused, with no implicit ancestor creation or alternate fallback. Browse
  always means an existing parent, previewing its final `MentorFlow` child.
- App/Updater/installation receipts and transactional health stay core-owned.
  Inno owns maintenance/uninstall registration and shortcuts. Shortcuts and the
  explicit Finish action call `Updater/UserRuntime.exe launch --root ...`.
- Native health is not bypassed: the shell requests closure of only the exact
  newly health-validated peer so its Finish action can explicitly launch it.
- Uninstall calls the core after user confirmation, before Inno removes its
  maintenance files. Core failure aborts shell cleanup. Unknown/user data is
  not handed to Inno as an owned application-file list.
- Stable launch calls signed recovery and next-launch pending apply before App.
  A successful update or live validated recovery does not launch a second App.
- The coordinator retains 03's deny-only session event and hidden broadcast
  listener across GUI exit. Existing 07 swap checks and the final/rollback
  health-launch boundary consult that signal. No new transaction engine.
- Frozen App PYZ closure explicitly includes the new UI/controller/session/
  launcher modules; actual rebuilt-byte correspondence remains to be checked.
- 04 now has explicit `--uat-loopback` wiring for the compiled UAT identity and
  a reusable external test key shared by BASE/TARGET. It is restricted to
  literal-loopback HTTP, disallows production Authenticode claims, records
  `EXTERNAL_UAT_ONLY`, and does not relax ordinary production HTTPS policy.

## Evidence (no installer or product runtime execution)

- 88 passed: Inno build/design/source contract, stable launcher/session adapter,
  existing per-user core/install tests, and offscreen update UI tests.
  XML: `E:/Codex/Temp/MF-E108-combined-wiring-01.xml`.
  SHA-256: `b9f2016ecc1d97406ce7e18e3f367d08e96a8ff09b458811fe2948e706ef57a4`.
- 42 passed: original 04 publisher tests plus UAT loopback/key-mode negatives.
  XML: `E:/Codex/Temp/MF-E108-publisher-wiring-01.xml`.
  SHA-256: `d01165fe3b6d01d4a64c6ec6ff4d95c143f883cc72166c7b0aaee0ad2a1c7256`.
- Inno 7.1 production sections compile successfully with a deliberately inert,
  incomplete compiler fixture. This artifact was **not run**, is not signed
  release evidence, and must not be distributed as the candidate. The earlier
  fixture-only compile receipt predates the receipt-label clarification and is
  not a final product receipt.

## Blocking original core acceptance failure: I06

`test_verified_failed_first_install_can_be_repaired_in_place` fails both against
this assembly and against exact original `b6e1d5da` engine/core Git blobs. After
an injected `NEW_ACTIVE` first-install fault, the same signed package cannot
repair the same root: `per_user_install.py:306` rejects it as unknown non-empty.
The signed highest-release is present but STATE/CURRENT were removed.

This failing acceptance check is committed without xfail/skip. It must become
green through an owner repair; the 130 passing focused tests do not waive it.

One precise review was sent to 07, limited to original per-user initial-failure
recognition/repair and regression tests. The immutable feedback is:
`E:/Codex/Coordination/MentorFlow-core-v2-20260824/enterprise_e1/distribution/MF-E108-DISTRIBUTION-ASSEMBLY-20260908-02/RETURN_TO_OWNER_W07_I06.v1.json`
SHA-256 `bf270149dfc871624c3402ce0071e22d513612e4ef5fd2f93c52e9771d65a42f`.

No core business repair is implemented by A here. New exact owner repair must
be reviewed before a final-source App/BASE/TARGET/Inno build. Still required:
actual 04 static releases consumed by the rebuilt client, packaged module and
manifest/provenance checks, ready local source launch tooling, one final user
installer with hash, and visible acceptance explicitly left to the user.

## Preserved boundaries

No push/Actions/server/formal installation or data access; no visible App/Setup/
UAC or DPI modification. No project/storage cleanup. Test fixture lifecycle is
limited to newly generated test scratch. No old package/worktree/cache/session
was deleted. The two unrelated 07 login observation files remain unchanged:

- `measure_installed_login_gate.py`: `c4b66f8f06fad62e41f4ab31b953bf4ba27b05d6d9fae89d4a41bebddf3522e6`
- `test_login_form_probe.py`: `3b192b802e82fd8b5377dd4279dbc30a580650dff6b0adbbe6972935a1a8d868`

`WORKER_NEW_TASK_ASSIGNMENT_AUTHORITY=ARCHITECT_ONLY`;
`A_INTEGRATION_REVIEW_FEEDBACK=ENABLED`; no new feature task dispatched.

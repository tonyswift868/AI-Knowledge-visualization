# E107 real install/update delivery — implementation in progress

Dispatch MF-E107-DESKTOP-DELIVERY-CLOSELOOP-20260908-14. This document is NOT
an acceptance receipt. No real install/update/recovery PASS is asserted.

## Reuse decision and component inventory

- Actual NSIS: `installer/ProtectedInstall.nsh`, using the shared
  `windows_install_transaction.Transaction` engine (extract/copy → full verify
  → commit/receipt → journal-owned rollback). Preserve directory/cancel/details.
- Real product: `desktop_gui_entry.py`, `LoginGateWindow`, StartupController and
  MentorFlowApplication. Existing packaged UAT UpdatePanel-only/offscreen entry
  is a fixture, not the final real application acceptance path.
- Existing updater: UpdatePanel → MatureUpdateService → ObjectCache + signed
  file release protocol. Reuse the engine; fix staging outside protected App.
- Existing apply/rollback module is not yet a real UAC coordinator. Complete
  APPLY_COMMITTED/health/recovery coordination before the first installer UAC.
- Installed authority: only `installation-receipt.json`. New v2 receipts bind
  lossless Win32 identity and start PENDING_HEALTH. File verification is not
  proof of a real event-responsive window. Legacy receipt bytes remain intact.
- Release generation exists in qualification fixture scripts; a single reusable
  delivery builder with actual source → binaries → manifest → signed feed is
  still required. Keys stay outside source and packages.

Use new D:\MF14I (not yet created) and D:\MF14W (ordinary evidence/build root).
Do not write D:\MFUAC, E:\MentorFlow or E:\MentorFlowData. Namespace is compiled
`MentorFlow.UAT.MF14`; no runtime/CLI namespace override. The shared installer
must not sweep a historical failed formal installation when installing elsewhere.

## Identity contract

`mentorflow.win32-file-id.v1`: an object with exactly `schema`, `volume_hex`,
`file_id_le_hex`. uint64 numeric VolumeSerialNumber is 16 lowercase hex digits.
FILE_ID_128.Identifier is 16 native bytes, represented by 32 lowercase hex
digits in array order (NOT GUID field order). Python's legacy st_ino is
little-endian and has an explicit exact-integer read-only adapter. Invalid
numbers/floats/widths/case/unknown fields are rejected, never rounded/repaired.

The old failed seed's Python-generated request contained volume
728480046474634923. The subsequently refreshed request contained
728480046474634900. That refresh used functions.exec JSON.parse/JSON.stringify
while changing timestamps, introducing a JavaScript Number boundary. Existing
before/after files under E:\Codex\Temp\MF13-build are retained. This scratch
refresh is retired, not ported to production. The new producer creates frozen,
create-only attempts; expiration produces a new nonce/file. Request hashes are
consistency records, not authorization. Release signatures and protected root
authority must still be independently verified by the helper.

## Sealing and executable lifetime

User staging remains untrusted. Incremental hardlinks may be read under native
deny-write leases, but are copied into independent, create-only protected files
before full verification and commit. No ACL/owner changes touch shared source
objects. The coordinator and elevated runtime must run outside swapped App.
The helper must signal APPLY_COMMITTED while alive, allow the Medium coordinator
to launch the real target, then finalize health or recover previous in that
same bounded elevated lifetime. These native delivery gates remain to be built
and qualified; source tests must not be presented as protected acceptance.

## Runtime implementation and pre-build evidence (not acceptance)

The shared handoff now uses a retained Medium coordinator process, local
message-mode pipes with OS peer PID checks, suspended Medium target creation,
native process/token/image/window checks, and a High callback that retains the
installer lock until healthy publication or recovery. A durable health receipt
intent distinguishes post-publication interruption from failed first launch.
Stable UpdateBridge/UpdateRuntime control files live outside swapped App.

Protected sources are independently copied, not hardlinked into user staging:
the existing canonical receipt prohibits aliases and binds metadata. A real
filesystem regression reproduced that adding a staging hardlink invalidates
the current receipt. The prior unprotected hardlink timing is not reusable as
this delivery's protected preparation timing.

The installer still uses NSIS 3.12 and the existing shared transaction engine.
Its complete onedir InstallRuntime is embedded and materialized into NSIS's
restricted plugin directory. Elevated PyInstaller onefile execution is removed
from this build route (official PyInstaller operating-mode security guidance;
NSIS 3.11+ fixed restricted-plugin-directory creation failure handling).

Source-only test batches most recently passed: 75 identity/request/preflight/
IPC/namespace tests; 36 health/controls/GUI/build-contract tests. Counts overlap
earlier batches and are not a unique aggregate. ACL/metadata test adapters and
offscreen UI are explicitly not real protected packaged acceptance.

`python -B -m scripts.build_real_update_delivery` provides ordered binaries,
base, releases, installer phases. It refuses dirty owned source, records exact
source identities, excludes the two unrelated probes, generates only public
UAT authority into the PYZ, and retains the private signing key outside all
release roots. BASE uses the real desktop entry and existing full dependency
closure; TARGET changes the protected version and real About-page notice.
The isolated signed bad version exits before health. Production builds do not
honour that UAT-only fault. Actual compilation and full runtime acceptance are
still pending at this source commit.

# Final source verification — 2026-09-09

Dispatch: MF-E108-W02-SINGLE-POLISHED-INSTALLER-20260909-03.
Base delivery: 5a03eb36fbde8eaa3dfc2bbc3c92334421b650de.

## Result

**MODERN_UI_SOURCE_READY = YES** for this source-only dispatch.
This supersedes the previous visual-acceptance task's scope, not its missing
runtime evidence. No old 1.2.3 payload or historical EXE is a final candidate here.

33 focused checks pass, none skipped:

| Check | Count | Evidence and scope |
| --- | ---: | --- |
| Build boundary | 12 | Mocked compiler/verifier; one user output file, separate private evidence, required explicit payload, rejected alternate modes/old outputs/nested candidates, immutable source/manifest gates |
| Actual ISCC compilation | 3 | Production source with --output=no at 256 MiB, >2 GiB and >4 GiB required-space values; no warnings; no installer emitted |
| Design contract | 7 | Dynamic Windows 11, no legacy sidebar, real callback progress, no technical detail controls, explicit launch/close, source geometry, palette contrast and asset reuse |
| Directory source contract | 9 | Existing known folders, drive/parent rejection, final-child-only creation, unknown tree rejection, identity-based repair/update, overlay ownership retention, reparse fail-closed, no debug dialogs, display path separated from actual path |
| Memory-only native font metrics | 2 | Four virtual DPI values (96/120/144/192); no window, host setting or process DPI-awareness changes |

Command:
`python -m unittest discover -s installer/inno/tests -v`

The compiler check uses the **same production source**, not a separate styled
installer or test-shell variant. ISCC's log explicitly reports that output was
disabled. The temporary .exe-named payload input is non-PE data, not executable
software. No old artifact or payload is needed to run these checks.

A read-only Windows known-folder lookup confirmed LocalApplicationData exists
on the current machine. The installer independently resolves it on its user's
machine and checks DirExists; no path from this workstation is frozen into UI.

## Requirement traceability

| Requirement | Authoritative source |
| --- | --- |
| Exactly MentorFlow-Setup.exe | Constant OutputBaseFilename in MentorFlow.iss; OUTPUT_NAME and empty-output guards in build_inno_installer.py |
| No normal/beautified/NSIS/test alternative | Fixture/appearance CLI and runtime probes removed; NSIS is not invoked; source check has --output=no |
| No two windows | No automatic launch or elevation; official SetupMutex in product setup |
| Existing parent; one new root component | ValidateParent and PrepareToInstall/CreateDir(Target) |
| Unknown nonempty rejected | ClassifyTarget + KnownTree + matching executable/ownership checks |
| Legal existing install recognized | tkRepair/tkUpgrade classification for the stable Inno.v1 identity; old no-Version records supported |
| Default actual Windows known folder | ExistingKnownParent checks LocalAppData/AppData/Documents; no param fallback |
| Three branded stages | CreateCustomPage + ShowBrandedPage; original logo, whitespace, native primary action |
| Enough disk → no confirmation/debug popup | Int64 free-space guard, inline feedback, DirExistsWarning=no, no MsgBox/TaskDialogMsgBox call in owned code |
| Smooth engine-derived progress | CurInstallProgressChanged → UpdateBrandedProgress; 1,000 bar steps, no synthetic timer |
| Launch on finish | Explicit RunList selection + original Inno handler; Close deselects |
| No raw technical display | Raw filename copy/detail panel removed; diagnostics remain file-only |
| Source/config only | No product build/installer execution/publication in this dispatch; explicit input required for A's later build |

Depth=1 is the application root under the user's selected parent. Inno may unpack
normal product-owned nested subdirectories inside that root. It does not create
missing parent ancestors.

Repair/update recognition covers the validated existing Inno.v1 shell contract,
not unverified NSIS folders. Overlay update preserves owned paths without
deleting stale payload files or restoring old Product identity fields.

## Deliberately not claimed

- No native screenshots, foreground user test, installation, repair, updater
  migration or actual application launch was performed.
- Source invariant tests do not simulate a successful real filesystem install.
- Compilation of >2/4 GiB arithmetic does not prove extraction of a large payload.
- Virtual geometry/font metrics do not establish live VCL rendering at all DPIs.
- Final payload/signing/licensing/runtime acceptance belongs to A after integration.
- Historical native-policy tests used a separate executable UX probe. That entry
  point is retired; their prior runtime pass claims are not reused or silently
  converted to today's source checks. Base Git history preserves them.

These are release-stage limits, not a block on the explicitly requested
source/config freeze. Source ready and final product ready are separate claims.

## Scope and safety

Only installer/inno and scripts/build_inno_installer.py change.
No account/database/server/updater/feed/trust/core change.
No dependency, desktop DPI/theme or foreground-window change.
No NSIS build or installer process started, including silent tests.
No old 1.2.3 product packaging or final output-directory mutation.

Unchanged NSIS SHA-256:
`6ad0608cb162a7fdacd6b7aa221f83b643db5b63a845857d836278cc2926c678`.

The existing unrelated untracked docs/windows-distribution-reference-v1.md is
preserved and excluded from delivery. Owned paths must be clean after commit;
do not claim the entire worktree is clean while that unrelated file remains.

# MentorFlow single installer — final source contract

Dispatch: `MF-E108-W02-SINGLE-POLISHED-INSTALLER-20260909-03`.
Task: `MENTORFLOW_SINGLE_POLISHED_INSTALLER_FINAL_SOURCE_V1`.
Base: `5a03eb36fbde8eaa3dfc2bbc3c92334421b650de`.

**This delivery freezes source/config only.** A supplies the final integrated
payload later. The earlier 1.2.3 binary is historical and is **not** this
delivery's final candidate. No foreground test, installer execution, host setting
change, final payload packaging or user artifact publication is authorized here.

## One user file

The only product entry point is `scripts/build_inno_installer.py`.
Both the wrapper and Inno source fix the output name to **MentorFlow-Setup.exe**.
There is no normal/beautified choice, no fixture-candidate mode, no forced-theme
binary variant and no NSIS build. The existing NSIS source remains unchanged as
history/fallback source, not a user-facing candidate.

The user output directory must be empty. The wrapper refuses to overwrite or
retain an old/alternate candidate, or to pack an installer inside a payload.
Private compiler inputs, logs and receipts go into a separate work directory;
only the EXE is copied to the user output. No auto-launch exists.
Official `SetupMutex=MentorFlow.Installer` prevents concurrent wizard progression.

## Source-only verification (allowed now)

Windows, Python 3.12+, Pillow and official Inno Setup 7.1 are the existing tools.
No new dependency, plugin DLL, font or installer engine is installed.

```powershell
python scripts/build_inno_installer.py --check-source --work-dir E:\Codex\Temp\mf-installer-source-check
python -m unittest discover -s installer/inno/tests -v
```

Source check invokes ISCC with **--output=no** and covers the actual production
sections. Its temporary payload is a 33-byte non-PE input file, not a test shell.
It emits no runnable installer and runs no GUI. Two additional compiler tests
check Int64 required-space values above 2 GiB and 4 GiB, without claiming real
payload installation. Font tests use memory-only Windows metrics; they do not
create windows or change display settings.

The obsolete fixture CLI and UXPROBE runtime-entry file are removed. The old
tests that launched the separate UX candidate are retired with that product
variant (recoverable in base Git history), not skipped to produce a green test
count. Their filesystem-runtime claims are not carried forward. Current
directory checks explicitly test **source invariants**, and native compilation
checks syntax/types. A's later integration acceptance must test the real install.

## A's integration handoff (not run in this dispatch)

After integrating this exact source and freezing the final product package:

```powershell
python scripts/build_inno_installer.py --payload E:\path\to\final-payload --output E:\path\to\empty-user-output --work-dir E:\Codex\Temp\mf-final-installer-build
```

These are placeholders, never defaults embedded in the installer.
The final payload must contain MentorFlow.exe, its version JSON and the existing
verified package manifest. A must keep that input immutable throughout packing.
The existing package verifier runs with --no-smoke --no-data-check. The wrapper
also binds source/brand and manifest hashes. No product or database is opened.
User output and work directory must be disjoint from each other and the payload.

A distributes only the resulting **MentorFlow-Setup.exe**, not the private
work directory, receipts, old 1.2.3 package, NSIS artifact or source-check input.
Final signing/licensing, payload acceptance and any allowed isolated native
acceptance remain A's integration/release responsibility.

## Frozen directory behavior

- Resolve the user's existing Windows known folder at startup: LocalAppData,
  then Roaming AppData, then Documents, each with an explicit existence check.
  No development /PARENT override or hard-coded developer drive exists.
  If none exists, leave the selection empty and block until the user chooses
  a real folder; never manufacture a default directory.
- The user chooses an **existing parent**; the final child is always MentorFlow.
  Reject missing drive/parent before target processing.
- Inno's native picker returns a selected folder; its final argument is reserved,
  not a folder-creation switch. Every returned path is revalidated.
- Reject unknown nonempty targets, target files, traversal, reparse ancestors
  and foreign files/directories. Never infer ownership from an EXE name alone.
- Recognize a valid existing **MentorFlow.Inno.v1** installation through its
  ownership record, matching executable identity, and owned file tree.
  Same incoming version/executable means repair; changed payload means update.
  Records created by the base shell without Version remain recognized via
  executable identity. An unrelated or unverifiable legacy installation remains
  unknown; no new 07 migration/trust contract is invented.
- Overlay updates preserve previous owned file/directory names in the new
  ownership record so a later repair does not reject leftover old payload files.
  Only Files/Directories are merged; the new Product version/hash remain intact.
  No old payload files or user data are deleted by this merge.
- Revalidate immediately before installation. The only application-root mkdir is
  `CreateDir(Target)` for the last MentorFlow component. Payload-owned internal
  subdirectories are unpacked normally by Inno; the depth=1 promise concerns
  the selected parent → application root, not the payload's internal tree.
- Check available space using Int64 and payload size + 256 MiB reserve.
  Sufficient space produces no success dialog. Errors are inline, human-readable
  MB/GB text. Unique write probes are immediately removed.
- Default per-user, no automatic elevation or second wizard. Explicit existing
  ALLUSERS mode is retained without rewriting 07's product integration.

## Frozen UI behavior

Inno Setup 7.1 `modern dynamic windows11 hidebevels includetitlebar`.
Only location → progress → finish for the normal flow; native exceptional engine
pages remain available. No welcome boilerplate, giant sidebar or debug modal.

The official product icon is reused byte-for-byte from
`frontend/public/teacher2d/tsubaki-live2d/icon.png`; palette follows the existing
warm theme. Native controls, whitespace, prominent title and accessible primary
button provide the hierarchy. Light/dark palettes and high-contrast fallback
remain. Geometry/font metrics scale without changing host DPI.

Long paths are shortened only for display, with the full path in the tooltip;
the actual validated target is never replaced by the shortened string.

Progress uses the real Inno extraction callback with 1,000 native bar steps and
an integer percentage label. No fake timer. Technical filenames are never copied
to visible controls, even through a Details button (removed); the Inno file log
retains diagnostics. No raw-byte/manifest/SHA/ACL/receipt modal exists in this UI.

Finish shows **启动 MentorFlow** and **关闭**. Launch explicitly selects the
unchecked Inno Run entry; Close leaves it unchecked. The original user's identity
and mature Inno engine handle actual launch and finalization.

## Meaning of READY

`MODERN_UI_SOURCE_READY=YES` is a source/config handoff, **not** screenshot,
real installation, runtime smoothness or final payload release approval.
`USER_VISIBLE_INSTALLER_COUNT=1` describes the frozen output contract.
No user installer is built or launched by this dispatch.

See `verification.md` and `source-freeze.json` for evidence and limits.

## Official references

- [Compiler output-disabled mode](https://jrsoftware.org/ishelp/topic_compilercmdline.htm)
- [WizardStyle](https://jrsoftware.org/ishelp/topic_setup_wizardstyle.htm)
- [Native support classes](https://jrsoftware.org/ishelp/topic_scriptclasses.htm)
- [Windows directory constants](https://jrsoftware.org/ishelp/topic_consts.htm)
- [Directory-exists warning](https://jrsoftware.org/ishelp/topic_setup_direxistswarning.htm)
- [SetupMutex](https://jrsoftware.org/ishelp/topic_setup_setupmutex.htm)
- Installed official Examples/CodeClasses.iss.

# E109 installer progress / finish UX — source handoff

## Integration binding (2026-09-10)

The owner evidence below is historical for exact e6fffaf, not final candidate QA.
A retained the current signed-core installer and bound the normal/repair entries
to `Updater/UserRuntime.exe launch|repair-update --root`. Optional ownership is
`Maintenance/finish-options.ini`; shortcut/Run names use the existing build's
ShortcutName namespace. Native core uninstall must succeed before optional
cleanup. Directory policy is unchanged. The earlier flat-root executable and
inventory descriptions below are superseded by this binding. Final installer
build and final candidate QA remain pending real SMTP; no installer was launched.

## Original owner handoff

Dispatch: `MF-E109-W02-INSTALLER-FINISH-UX-20260910-01`.
Task: `MENTORFLOW_INSTALLER_PROGRESS_FINISH_AND_GUIDANCE_V1`.
Base: `b362c22a6025fd76a5387b4842efc249a2b895ef`.

## Implemented source contract

- Progress **取消安装** is horizontally centered from the actual client/button
  width, with native centered anchoring on resize. It calls the same original
  Inno Cancel handler as before; X still shares the engine's safe cancel route.
- The finish page has a small **可选设置** section using the real Inno `[Tasks]`
  controls. `desktopicon` and `startup` both have `unchecked`; previous task
  inheritance is disabled and first finish-page activation clears selections.
  The normal Tasks page is skipped, not added as a separate step.
- Finish retains **关闭** and **启动 MentorFlow** buttons. Applying options
  precedes either action; errors remain inline so users can deselect and finish.
  Close deselects RunList; Launch selects the existing `runasoriginaluser`
  postinstall entry. `PrivilegesRequired=lowest` is unchanged. Launch is not a
  task checkbox. No new launcher, wrapper, shell or app process is introduced.
  Already-elevated callers are explicitly rejected by both the Launch button
  and the native Run Check; Close remains available and explains using the
  ordinary-user Start Menu entry. Inno's original-user flag alone cannot safely
  undo a caller that was already elevated. No elevated fallback is permitted.
- Fixed Start Menu entries are MentorFlow, MentorFlow 修复与更新, 卸载 MentorFlow.
- User contract supplied during this task: repair entry is **MentorFlow.exe**,
  arguments **--repair-update**, working directory **{app}**. No exact 07 commit
  is required for this source handoff. A checks these two public fields against
  07's final delivery; W02 neither waits for nor edits 07's internal implementation.
- The existing-parent directory policy is unchanged byte-for-byte. `E:\` maps
  to `E:\MentorFlow`; existing `E:\Apps` maps to `E:\Apps\MentorFlow`. Only the
  final app-root component is created.

## Finish-option timing and ownership

Inno processes installation `[Icons]` entries **before** the finish page. Merely
changing task selection there cannot create a late desktop shortcut. The finish
handler therefore uses native task selection as the authority and, only when
desktopicon is selected, copies the **real `[Icons]`-generated Start Menu link**
to the user's desktop. It does not reconstruct another shortcut target, create
a second installer, or fake a replay of installation sections. The desktop gets
the exact same official application target and arguments.

The optional startup action writes only
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\MentorFlow`, with a quoted
path to the installed MentorFlow.exe. This is a normal per-user Windows startup
entry that Task Manager can disable. No machine-wide value, scheduled task,
StartupApproved modification or elevated startup mechanism is created.
An administrative install mode or already-elevated caller cannot apply these
per-user finish options to an unintended user profile.

With both options unchecked, neither new action runs. Unchecked during repair
also does not erase an existing user choice. Ownership metadata is preserved
across an admitted repair/update; the preservation step itself does not create
a shortcut or enable startup. Optional actions can be retried without replacing
foreign same-name shortcuts or another installation's startup command.

Before uninstall removes the inventory, cleanup verifies the recorded desktop
path and exact shortcut hash before deleting that one file. It deletes only the
exact recorded Run value when it still points to this installation. Changed or
foreign files/values and the containing registry key are preserved.

## Validation

`python -m unittest discover -s installer/inno/tests -v`

**54 passed, 0 failed, 0 skipped**:

- 14 build boundary tests, including native INI key reading.
- 3 real Inno 7.1 compilations with `--output=no`, including >2 GiB and >4 GiB.
- 7 design/source geometry checks and 3 memory-only Windows font checks,
  including both native finish-task labels within their available rows.
- 9 directory source checks and 7 existing parent/close source regressions.
- 11 E109 checks: centering, unchecked native tasks, independent opt-in guards,
  official icon reuse, quoted HKCU Run contract, explicit finish ordering,
  original-user launch flags, all three public shortcuts, guarded uninstall,
  user-guide topic/terminology limits, and already-elevated caller rejection.

No installed application, installer window, app build or final user package is
executed/created by this task. The real compiler is used only with output
disabled. Tests do not modify host DPI/theme, real desktop shortcuts or startup
registry entries. Native click/render/token acceptance remains A's integrated
build check; source tests are not claimed to be actual foreground execution.

The user guide contains only location selection, checking/updating (including
the login page), the repair/update Start Menu entry, no need to redownload an
installer, uninstall, and separation of application files from user data. No
server address, technical receipt/manifest/hash or product-feature tutorial is
included in that document.

## Scope

Only installer-owned source/tests/internal handoff documentation and the one
requested user guide change. The builder, directory policy, App and update core
are unchanged. Historical E108 receipts and its binary remain untouched.
The pre-existing untracked `docs/windows-distribution-reference-v1.md` remains
unmodified and excluded; owned paths can be clean without claiming that this
unrelated file disappeared. No push is performed.

## Official references

- [Inno Tasks: unchecked and checkablealone](https://jrsoftware.org/ishelp/topic_taskssection.htm)
- [Inno Icons: target, parameters and working directory](https://jrsoftware.org/ishelp/topic_iconssection.htm)
- [Inno runasoriginaluser](https://jrsoftware.org/ishelp/topic_runsection.htm)
- [Inno IsAdmin: actual administrative privileges](https://jrsoftware.org/ishelp/topic_isxfunc_isadmin.htm)
- [Inno 7.1 task-control and page construction](https://github.com/jrsoftware/issrc/blob/is-7_1_0/Projects/Src/Setup.WizardForm.pas)

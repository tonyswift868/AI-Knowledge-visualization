# Desktop shortcut repair — bounded partial delivery

Dispatch: MF-E107-SHORTCUT-LAUNCH-DIAG-20260907-01
Task: MENTORFLOW_WINDOWS_SHORTCUT_DOUBLECLICK_REPAIR_V1
Result: PARTIAL_RETURNED, not final product or foreground UX acceptance.

## Before repair (actual COM fields)

- Shortcut: `C:\Users\21533\Desktop\MentorFlow.lnk`
- Target: `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
- Arguments: `-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "E:\Downloads\MentorFlow-Login-Final-20260906\Start-MentorFlow.ps1"`
- Working directory: `E:\Downloads\MentorFlow-Login-Final-20260906`
- Icon: `E:\Downloads\MentorFlow-Login-Final-20260906\MentorFlow\MentorFlow.exe,0`
- WindowStyle: 7. Description: `MentorFlow — verified personal test launch; isolated account data`.
- Target, launcher, working directory and icon exist. No missing-path/quoting defect established.
- Runtime JSON: exact schema `mentorflow.desktop-runtime.v1`, profile `final-20260906-device1`; no workspace selection.

Preserved pre-existing applications: PIDs 41588 and 49516, with launcher PIDs 49480 and 35204. Existing SSH PID 38484 belongs to 35204. None was closed or modified by this dispatch.

## Reproduction and exact error

An existing same-user non-elevated shell token was verified (same SID, TokenElevation false). A clean diagnostic child used Windows Shell `os.startfile` on the actual link; a direct control used the exact target/arguments/working directory. No developer PATH/PYTHONPATH was supplied to the launcher or app.

Shell invocation produced launcher timing record 20168, stopping after `connection_started_ms=176.66`. The direct control exited 2 after 5.026 seconds; a stderr-capturing control exited 2 after 5.318 seconds:

`MENTORFLOW_LAUNCH_REJECTED: 检索不到变量“$LASTEXITCODE”，因为未设置该变量。`

The failure was an ambient native exit-code variable read under StrictMode inside `Test-SupportedEndpoint`, before application launch. Hidden stderr gave no user-visible error. Older retained launch records also show long invisible full-check waits (157.292 and 60.536 seconds); this dispatch does not reopen performance optimization.

The preliminary asynchronous process enumerator could miss short-lived children and later observe a separately started control. Its mixed pre-repair process list is NOT an authoritative single process chain. The exact direct exit handle/stderr and separate launcher timing records support the failure claim.

## Minimal repair

- Probe uses its own `Diagnostics.Process.ExitCode`, async output drains and a five-second wait bound, with disposal. Nonzero exit, malformed/missing/wrong issuer, timeout or process-start failure remains false.
- Existing CA, HTTPS issuer, curl timeout, SSH host/key reference and strict host-key checks unchanged. No secrets emitted or read.
- Frozen deployed launcher retains its shipped `verify --no-data-check --no-smoke` command and all original hash anchors; it now displays a startup progress window, pumps it during verification and presents a generic failure dialog. No unchecked product execution.
- Source template receives the same probe fix and failure dialog; its pre-existing future `launch-gate` command is deliberately NOT copied over the deployed legacy verifier command.
- Actual link WindowStyle changed from 7 to 1; all target/argument/workdir/icon fields preserved. The creator now emits 1. PowerShell remains hidden internally; no terminal is required from the user.

Deployed launcher SHA-256 before: AB167EB9568B0530C9B085C4E26568684B803BAE14746C6CA4806F5148282B8E

Deployed launcher SHA-256 after: 9CBCA63B4CD69C2DF8B41ED8BDD125F2104572C5909937297288EC7BAC5F5931

EXE unchanged: 4235AB853C4EB45F848AD92F8F87943C064B0B3BE280EA2556700C6E9D98C496

Manifest pin: A786BD894ABEA739B3B3F1A8A797B160CC8B56E88CF4F40CE580D8AD78BA9380

Verifier pin: BDB4FA23422BA25159F63D53757A718031E4AC3C61D9B2A89E7B9F3127197A67

Public CA pin: EF9EA28E93E89C83B2379FCA65CFAC2D945CCE856EC814D63CBA018B0362C005

## After repair evidence

1. Same normal-user Shell activation: launcher 37812 -> verifier 51432/13648 -> app 52364. Integrity PASS at 38.016 seconds, app process at 38.073 seconds (launcher clock, not visual readiness). App main-window metadata: `登录 MentorFlow`, Responding true. Owned process CloseMainWindow returned true; app exited 0, recorded by launcher.
2. Second same-link activation after closing first: launcher 19180 -> verifier 45400/39164 -> app 15928. Integrity PASS at 28.367 seconds, app process at 28.403 seconds. Same login-window title/Responding metadata; owned close request true and app exit 0.

No account input, authentication, data import, server change, formal database operation, dependency install, product rebuild or push.

## Focused checks and remaining qualification

`test_personal_endpoint_probe.ps1 -Launcher <source or deployed script>`: seven cases PASS for each. Extracts UTF-8 function AST and substitutes process creation only; no application or network is started by these tests. This does not replace real TLS testing. An initial Windows PowerShell default-encoding ParseFile test failed; explicit UTF-8 ParseInput corrects the test reader, without rewriting the pre-existing source template.

The computer-use skill was read and its required setup/recovery attempted. `@oai/sky` failed to import before any input due to missing `tslib/tslib.es6.js`, including after session reset. No custom screenshot/click substitute was used. Therefore no literal desktop double-click or actual rendered/non-minimized LoginGate visibility is claimed. Window title and process existence are weaker evidence.

Both repaired launches could reuse the pre-existing SSH listener. Terminal-independent cold connection preparation and offline same-window rendering are NOT dynamically qualified. The existing owner process was deliberately preserved.

Prior uncommitted `scripts/measure_installed_login_gate.py` and `scripts/test_login_form_probe.py` changes were preserved, not included in this focused commit. Worktree is not clean.

Scratch is restricted to `D:\CodexScratch\MentorFlow\MF-E107-SHORTCUT-LAUNCH-DIAG-20260907-01`. Final observed files totaled 6,546 bytes; continuous peak was not measured. This report retains essential evidence before removing regenerable scratch. No new E project cache was intentionally created; this Markdown is a retained evidence artifact, not a cache.

Cleanup outcome: the tool rejected the validated scratch removal command as `blocked by policy` before execution. No deletion occurred. Scratch is retained; D_SCRATCH_CLEANED=false. No alternate deletion mechanism was attempted.

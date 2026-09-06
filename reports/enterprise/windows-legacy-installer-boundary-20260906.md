# E107 reject incompatible legacy installer — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 735763fb02b3fe8b1f12ef11178ef581f2dfea34.

Inspection confirms installer/MentorFlow.nsi is still the legacy per-user entry:
RequestExecutionLevel user, LOCALAPPDATA/Programs, direct MentorFlow.exe shortcuts
and a legacy optional-data-removal uninstaller. It cannot supply the new protected
installation trust boundary. Merely changing it to elevated is unsafe and was
not attempted.

The build wrapper now refuses a payload containing MentorFlowInstallGate.exe
before compiler invocation or output directory creation. Direct makensis invocation
also fails at compile time through the documented NSIS
[!if /FileExists conditional](https://nsis.sourceforge.io/Docs/Chapter5.html#newif).
Both report PROTECTED_INSTALLER_REQUIRED. There is no force/skip override.

One focused test PASS in 1.539 seconds, exercising three real subprocess checks:

- Windows PowerShell 5 build wrapper rejects a synthetic new-gate payload before
  its deliberately missing compiler can be reached; creates no output directory.
- Installed NSIS 3.12 compiler rejects the same payload directly; creates no EXE.
- Removing only the test-owned placeholder gate makes a legacy fixture reach the
  existing missing-compiler check, demonstrating the new guard is scoped to the
  protected-gate payload, not a universal build disable.

Fixture files remain under a new E:/Codex/Temp/e107-installer-boundary-* directory.
No installer or payload executable was run. No production shortcut, registry,
database, retained release, dependency/lock or old receipt was changed.

This prevents silently shipping the new payload through an incompatible installer;
it is NOT completion of the elevated outer bootstrap. That implementation remains
required before a new protected product installer can be built. Prior performance
and 58-case runtime tests retain their scope and were not rerun here. Final product
build/push not performed; <=3s gate stability and <=8s interactive form remain
unqualified. This receipt does not claim SOURCE_READY or user-test readiness.

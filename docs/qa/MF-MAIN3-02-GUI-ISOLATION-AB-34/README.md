# Dispatch34 — GUI isolation passed; third defect stop

The exact rebuilt Frozen test package completed native login, server rehydration into fresh A, and GUI short-path / Chinese-filename uploads. Both new resources have durable server revision 1. All actions were bound to EXE hash, PID, HWND and a freshly resolved element; no global mouse/keyboard input was used. Qt list selection patterns did not navigate, so the harness used HWND-targeted messages derived from the selected element rectangle.

The 352-character file was selected through the native file picker but the product reported `Errno 2`. The file exists and its hash matches via an extended-length Windows path. Ordinary path lookup fails on this host (`LongPathsEnabled=0`); the EXE already declares `longPathAware=true`. `desktop/api.py:254` opens the unprefixed path before HTTP submission. No registry change, file relocation or product fix was attempted. This is the third independent defect after the input harness and exact assigned-root admission fixes, so dispatch section 17 requires stopping. This is **not** local GUI success with absent server data: the long-path GUI explicitly failed before upload.

B, reverse sync, full restart, second-account isolation, tombstones and interrupted-command acceptance were not run. Big gate 1 remains open. A and its child backend were closed; synthetic UAT and scratch remain for diagnosis. Prior fixture records are setup, never counted as this dispatch GUI writes. No screenshots or credentials were copied into this evidence.

## Evidence

- `receipt.json`: complete gates and storage receipt. Delivery commit/tree are the containing Git commit, reported in the final response to avoid a self-referential commit hash.
- `candidate-identity.json`: prior/copy identity and new exact build.
- `gui-evidence.json`, `uia-events.jsonl`: synthetic native UI observations and pattern/action records.
- `resources.json`: specimen hashes, stable server IDs and revisions; no file contents.
- `long-path-failure.json`: first failure and read-only Windows path evidence.
- `tests.json`, `focused-final.xml`: scoped regression results and initial fixture failure disclosure.

## Architecture references

[Microsoft UI Automation patterns](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-control-patterns-overview), [pywinauto process/window specification](https://pywinauto.readthedocs.io/en/latest/getting_started.html), [FlaUI](https://github.com/FlaUI/FlaUI). Built-in .NET UIA was reused; no new runtime dependency.

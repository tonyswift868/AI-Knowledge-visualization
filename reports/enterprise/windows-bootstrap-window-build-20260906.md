# E107 elevated installer window/build source — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 2df4e6ed432a1c189ca3cc07057a22aac4150763.

Added build_protected_bootstrap.py: prepare verified embedded bytes and fixed pins,
then compile the system-.NET windowed installer with an explicit requireAdministrator
manifest. Compiler sources are explicitly selected, excluding diagnostic Main
entry points. No signing key is generated; output reports UNSIGNED. Release
source/tree inputs remain operator-provided identities, not a new provenance
attestation. The old per-user installer refusal remains intact.

BootstrapWindow offers new installation/shortcut paths, prefers E: only after
checking its actual NTFS parent boundary, and does not overwrite existing targets.
If the default shortcut exists, a version/source-specific new name is offered.
Installation runs on a background task, with a marquee and phase text on the UI
thread. Close during work is refused with an explicit wait message; there is no
forced child termination or fake cancellation result. Errors remain visible and
allow path correction/retry; success asks the user to close the elevated installer
and later use the normal shortcut. The product is never auto-launched elevated.

Actual synthetic window test: prepared/compiled the new window, parsed its PE
manifest resource and confirmed requireAdministrator, observed the visible
MentorFlow installation window, then closed it without pressing Install. Exit 1
correctly represents closing before installation, not successful installation.
The test ran in the already-elevated environment; an actual UAC consent interaction
was NOT exercised. Only synthetic bytes were embedded, not the final product.

Thirteen focused installer-component tests PASS in 8.663 seconds, including the
real native installation orchestration test from the preceding change. This does
not turn the window's untested Install-button completion path into a UI PASS.
Scratch is under E:/Codex/Temp/e107-bootstrap-window-*. No production shortcut,
data root, registry entry, retained package or dependency/lock was modified.

Remaining: actual full installation through the window, staging cleanup/error
handling, complete-chain long paths, final integrated product and startup/form
performance qualification. No final UAT/product build, remote push, server
mutation or formal data write. Earlier timing samples, 58-case runtime checks
and four historical WinError 1314 skips retain their previous scope/status.

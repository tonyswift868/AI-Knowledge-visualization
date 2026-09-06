# E107 native installation feedback — PARTIAL

Dispatch: MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task: MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base: 8750ae23828ebd8e38414a5cd91e04369e608ad1.

## Rejected scheduling experiment

Six sequential source FAST checks used the same protected isolated payload,
same 13,661 critical hashes and same four readers. Order alternated between
lexical and largest-first (A/B/B/A/A/B). Lexical totals were 8.249, 9.765, 10.203
seconds; largest-first totals were 9.378, 9.572, 10.429 seconds. There was no
consistent benefit; scheduling was NOT changed. Evidence:
E:/Codex/Temp/E107-TrustBoundary/hash-order-results.json. These are experiments,
not improved native launch or form-interactive acceptance measurements.

## Implemented installation feedback

New `windows_install_progress.py` uses Windows system STATIC controls and the
native message loop; it imports no Qt, application backend or database code.
The window is visible with nonempty Chinese status text for preparation, copying,
ACL/inventory inspection, full SHA-256 verification and receipt publication.
This is installation feedback, not a claim that LoginGate or its form is ready.

Native `install` and `activate` commands use this status by default. An explicit
`--quiet-install` is available for a trusted installer host that supplies its own
visible feedback; it does not change verification or elevation requirements.
Normal `gate` and `launch` paths do not create an installation window.

Closing the status window causes cancellation at the next status update. A
cancellation raised during copying/full hashing does not publish a new receipt
or execute the application. Already-created files remain in the protected test
installation and cannot pass normal activation without a valid receipt. Progress
callbacks do not suppress directory mutation detection or verification errors.

## Checks

25 standalone unittest cases passed, exit 0, no SKIP/XFAIL. New checks exercise
an actual visible, nonblank Windows status control; clean destruction and close
cancellation; cancellation during full hashing with no receipt; and activation
command wiring. Existing ACL, tamper, path, receipt, new-version, configuration
and installer-bootstrap tests remain green. No backend conftest or user DB used.

No final package or updated native helper was built for this source-only change.
The historical onedir native P50 9.516 s / MAX 9.632 s remains historical, not a
fresh measurement. Security <=3 s is unmet; the complete installed LoginGate and
form-interactive <=8 s chain is still unqualified. Real installer entry wiring,
full installed-launch acceptance and further performance work remain required.

No production shortcut changes, server mutation, dependency/lock changes, push,
formal database writes or retained-package modifications. This is PARTIAL,
not SOURCE_READY_FOR_FINAL_INTEGRATION or permission to build the final product.

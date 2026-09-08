# E107 hash reader optimization — PARTIAL

Dispatch: MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task: MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Previous source: f98a485fdebdb347181ec420382bec7b2ecd1d12.

## Change and safety

Replace allocating buffered reads with raw readinto using one reusable 256 KiB
buffer per hashing thread. All bytes still feed a fresh SHA-256. No digest,
file contents, metadata trust or successful verification result is reused.
The SQLite privacy prefix accumulates across short reads from those same bytes.
Empty files clear a previous prefix; invalid chunk sizes reject explicitly.
Four readers and the critical/static classification remain unchanged.

The native measurement harness accepts a separate helper/output path and records
the helper SHA-256, so this run preserves the previous native helper and results.

## Controlled measurements

Same isolated package: E:/MentorFlow-TrustTest-20260906/large/App.
13,661 critical files; every benchmark digest compared with the manifest.
Serial trials baseline/new/new/baseline/baseline/new, four readers each:

| Reader | Hash-only seconds |
| --- | --- |
| Previous | 10.780072, 6.421970, 6.353095 |
| Reusable buffer | 1.739467, 1.841570, 1.903905 |

These are sequential warm-system measurements, not a cold-cache guarantee or
form-interactive timing. Scratch evidence and comparison implementation:
E:/Codex/Temp/E107-TrustBoundary/bench_hash_buffers.py and hash-buffer-results.json.

Complete source verification after implementation:

- INSTALL_FULL: 5.401703 s total / 2.051784 s hashing, all 14,038 files.
- FAST: 5.361260 / 5.236395 / 5.231380 s total.
- FAST hashing: 2.024894 / 1.958955 / 1.969399 s.
- FAST P50 5.236395 s; MAX 5.361260 s.

New small onedir native helper, not the final product package:
E:/MentorFlow-TrustTest-20260906/native-hash-buffers/MentorFlowInstallGate.exe.
SHA-256 d8ab578b6e351b13d36ddb99cb51abe24aeef1dfa35c46a4395bda5cbf138b2d.
Three same-user normal-token processes exited 0:
5.281958 / 4.352654 / 4.589172 s. P50 4.589172 s; MAX 5.281958 s.
Evidence: E:/Codex/Temp/E107-HashBuffers-20260906/native-results.json.
Metric is process start-to-exit security gate only; not LoginGate or interactive
form. The previous native P50 was 9.516039 s; this remains above the 3 s goal.

## Regression and remaining work

29 standalone tests PASS, 0 skip, including existing protected-installation
tamper/receipt/ACL/unknown-file checks and four reader regressions. No backend
conftest, application database or live account flow is imported/exercised.
Historical four WinError 1314 skips are not rerun or reclassified.

Reproduce focused suite:
`python -m unittest scripts.test_package_hash_buffers scripts.test_windows_install_trust.InstallTrustTests scripts.test_windows_installed_launch.InstalledLaunchTests scripts.test_install_shortcut_bootstrap.ShortcutBootstrapTests scripts.test_windows_install_progress.InstallProgressTests -q`

Still incomplete: <=3 s gate, actual protected installer wiring, installed
LoginGate/QtCore/main-hidden/close acceptance and <=8 s interactive form.
No final product build, push, server mutation, formal data write or retained
product-package modification. This is PARTIAL, not integration-ready or UAT-ready.

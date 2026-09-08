# E107 bounded ACL read batches — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 1aa7b23609bd8d18f5c75a51eeb820b161835412.

## Change and safety

Snapshot retains immediate root protection and per-entry lexical/stat/link
validation. It collects paths, then freshly reads every entry's ACL in batches
of 64 with at most four threads. Small installations under 64 entries remain
serial. Thread-local Windows security-descriptor buffers are retained. No file's
authorization result is cached across verifications. Receipt is included in the
ACL checks even though excluded from payload inventory.

All batches must succeed before returning the snapshot, loading manifest/receipt
contents or hashing payload. Exceptions propagate, executor shutdown joins its
workers, and the existing directory watcher covers collection and ACL phases.
No unknown application code runs in either phase. Critical hashes and TLS rules
are unchanged. Enumeration now precedes descendant ACL rejection; it still rejects
aliases/reparse entries and never treats a collected path as trusted before ACL
completion. This does not introduce a new claim of resistance to administrator
or SYSTEM attackers.

## Controlled source experiment

Nine calls ran serially on the isolated protected large package. Hash workers
remained four throughout; only ACL readers varied. All FAST PASS, 13,661 critical
hashes and 14,038 manifest entries each time.

| ACL readers | Total runs (s) | P50 (s) |
| --- | --- | --- |
| Serial | 3.769238 / 4.206837 / 4.284496 | 4.206837 |
| 2 | 3.541298 / 3.756323 / 3.686077 | 3.686077 |
| 4 | 3.466734 / 3.221670 / 3.349437 | 3.349437 |

Raw evidence: E:/Codex/Temp/E107-TrustBoundary/acl-batch-results.json.
The source improvement justified testing the bounded implementation natively.

## Native and regression results

Small helper only, existing dependencies; no final product build:
E:/MentorFlow-TrustTest-20260906/native-acl-batches/MentorFlowInstallGate.exe.
SHA-256: 8c989e232048df073b3bbeeb942d43658f7b7f48c92281f2f531b7bdb6a117fd.
Three normal-token gate process start-to-exit runs, all exit 0:
17.483957 / 2.855407 / 2.771540 seconds. P50 2.855407, MAX 17.483957.
Raw evidence: E:/Codex/Temp/E107-AclBatches-20260906/native-results.json.

The first slow run is retained. Its cause was not established; do not label it
an antivirus or cold-cache artifact without evidence, exclude it from acceptance,
or claim the <=3 second target is met solely because P50 is below 3 seconds.
No new full-install, LoginGate-visible or form-interactive timing was taken.

58 focused standalone tests PASS in 9.773 seconds, zero skips in this selection.
Four new checks cover all paths/tail batches, fresh re-reads, failure propagation
from every batch position, small-install calling-thread behavior, and a real
multi-batch synthetic installation whose ACL drift rejects before payload hashes.
Historical four WinError 1314 skips were not rerun/promoted. Only a comment was
clarified after the final test run; runtime behavior is unchanged.

Remaining: investigate first-start latency, qualify actual interactive form and
complete secure outer installer/bootstrap integration. No final package build,
remote push, dependency/lock change, server mutation, formal data write or retained
release modification. Not a user-hands-on-ready release.

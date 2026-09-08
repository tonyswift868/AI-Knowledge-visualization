# E107 batched hash scheduling — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 358ca7a6269ba6c06180c50c0c4393835e17a8e1.

## Change

Threaded hashing submits 32-file batches instead of one Future per file. Same
four reader threads, same caller order, same bytes and SHA-256 per file, same
SQLite prefixes, and no additional I/O parallelism. Any read failure still
aborts verification. For 13,661 critical files this reduces futures from 13,661
to 427. The serial reader path is unchanged. No trust/digest result is cached.

## Controlled evidence

Same isolated package and full security checks in every run. A/B/B/A/A/B trials:

| Strategy | Total seconds | Hash seconds |
| --- | --- | --- |
| One-file tasks | 4.287380, 5.159945, 4.951003 | 2.151151, 2.173133, 2.063052 |
| 32-file batches | 4.457800, 4.703016, 4.632518 | 1.595807, 1.740290, 1.759082 |

Hash-stage benefit reproduced across the trials; total time remains variable.
Evidence E:/Codex/Temp/E107-TrustBoundary/hash-batch-results.json; comparison
script bench_hash_batches.py beside it. An initial scratch result-label collision
raised TypeError before recording a batch; fixed the benchmark label and ran the
six reported trials. It is not counted as a passing measurement.

After implementation, source INSTALL_FULL 4.892981 s total / 1.727765 s hashing,
all 14,038 files. FAST totals 4.485898 / 4.523641 / 4.483315 s; hash times
1.646848 / 1.640921 / 1.620582 s, all 13,661 critical files. Source P50 4.485898 s,
MAX 4.523641 s, still above 3 s. These do not supersede earlier native metrics
or establish cold-cache, final EXE, or interactive-form startup performance.
No native helper or product package rebuilt for this change.

## Checks and remaining gates

39 standalone tests PASS, 0 skips. New regression compares serial/threaded
digests, prefixes and order at 0/1/31/32/33/64/67 files, including the last
partial batch, and verifies a missing file inside a later batch fails closed.
Existing actual ACL, tamper, receipt, unknown-file, new-version and import-fence
tests remain green. Historical four WinError 1314 cases not rerun/promoted.

No server mutation, formal data write, dependency/lock change, retained-release
change, final build or push. Actual installer wiring and <=3 s gate / <=8 s
interactive form remain incomplete. This is PARTIAL, not integration-ready.

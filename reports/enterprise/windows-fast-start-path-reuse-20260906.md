# E107 protected gate path reuse — PARTIAL

Dispatch: MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task: MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base: 53eef773c499bee1f7130961a3a0f5962f773baa.

Profiling identified repeated pathlib parent iteration in the protected
inventory: 18.1 million calls, with a profiled snapshot time of 8.719 seconds.
The walker now carries relative names from its validated scandir chain and
reuses its known App subtree context. The validator checks a supplied relative
name against the exact candidate/root, rejects absolute/drive/parent-traversal
names, and still resolves and checks containment when resolution is requested.
An already-proven lexical relationship no longer triggers another parent walk.
ACL queries, real lstat, link/reparse checks, privacy classification, directory
notifications and critical content hashes remain in place.

An intermediate profiled snapshot fell to 7.2 million calls / 4.541 seconds.
Profiler numbers are diagnostic, not ordinary launch timings. Further relative
name reuse was validated with the final ordinary gate measurements below.

22 standalone Windows unittest cases passed, exit 0, no SKIP/XFAIL. New tests
prove cached metadata/relative names cannot authorize sibling paths, absolute
paths, forged names or parent traversal. Existing install/ACL/receipt/version/
tamper and actual installer-bootstrap publication tests remain green. No backend
conftest or application database was used. The historical four symlink permission
skips remain historical skips, not newly passing tests.

Same isolated large package; final-source full verification 9.145 seconds,
including 5.845 seconds hashing all 14,038 payloads. Ordinary source FAST runs:
8.758 / 8.808 / 9.099 seconds (P50 8.808, MAX 9.099). Critical hashes still cover
13,661 files. No extra whole-package verifier ran concurrently with these scans.
No cold-cache or controlled percentage speedup claim is made.

The small onedir native gate was rebuilt for this source change; no final unified
product package was built. Its isolated normal-token process results are recorded
in E:/Codex/Temp/E107-TrustBoundary/native-gate-results.json. These clocks cover
native gate process start-to-exit, not launch-click-to-form.

Final native runs: 9.632 / 9.516 / 9.421 seconds, all exit 0; P50 9.516,
MAX 9.632. Compared with the earlier native P50 of 9.502, these measurements
do not establish a native startup latency improvement. Reduced profiled path
processing must not be advertised as a proven end-user speedup.

Security <=3s remains unmet; the new full installed LoginGate/form chain remains
unqualified. Real elevated installer UI wiring, full launch acceptance and further
performance work are still required. This is not SOURCE_READY_FOR_FINAL_INTEGRATION.
No push, server mutation, formal-data write or production shortcut change.

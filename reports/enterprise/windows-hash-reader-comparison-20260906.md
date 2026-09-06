# E107 hash reader comparison — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Runtime commit d7966db7e9d56966c3430f21513f3f06e0ba30dd remains unchanged.

Nine source verification calls ran serially against the protected isolated
E:/MentorFlow-TrustTest-20260906/large installation. Order: 4,2,8,8,4,2,2,8,4
readers. Each call freshly validated ACLs, receipt, manifest, inventory and all
13,661 critical hashes out of 14,038 manifest files. All nine returned FAST PASS
with full_hash false. No activation, receipt mutation or application launch.

| Readers | Total seconds | Total P50 | Hash seconds | Hash P50 |
| --- | --- | --- | --- | --- |
| 2 | 4.910223 / 4.916613 / 5.149872 | 4.916613 | 2.263253 / 2.231770 / 2.351923 | 2.263253 |
| 4 | 3.481819 / 4.155908 / 4.439212 | 4.155908 | 1.472064 / 1.534753 / 1.627862 | 1.534753 |
| 8 | 3.978620 / 3.989415 / 4.143808 | 3.989415 | 1.348267 / 1.395627 / 1.412114 | 1.395627 |

Raw evidence: E:/Codex/Temp/E107-TrustBoundary/hash-reader-results.json.
Harness: E:/Codex/Temp/E107-TrustBoundary/measure_hash_readers.py.

Two readers regress. Eight readers lower hash time by about 0.139 seconds at the
median, but whole-gate ranges overlap and even their best total exceeds 3 seconds.
Do not claim packaged/native improvement: this is a same-process source experiment,
not a new helper or form-interactive measurement. Retain default four readers
pending stronger evidence; there is no runtime adjustment in this commit.

The remaining non-hash phase is substantial. Candidate containment checks in
the walker were inspected and preserved, not removed for a timing win. Next work
should address that phase safely or complete the secure outer installer/bootstrap
wiring, rather than escalate disk readers without a meaningful startup benefit.

Previous 50 focused tests and native timings retain their original scope; not
rerun or relabeled here. No final package build, push, dependency change, server
mutation, formal data write or retained release modification. Historical four
WinError 1314 skips remain unqualified. <=3 second gate and <=8 second interactive
form acceptance are still incomplete, as is the outer installer integration.

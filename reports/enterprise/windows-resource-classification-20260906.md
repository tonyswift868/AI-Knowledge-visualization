# E107 canonical resource classification — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 100b7b0ecaa6aabf4b70a01f8665633af09901a1.

Profiling identified 41,738 category calls, repeatedly constructing pathlib
objects. Classification now uses canonical slash-separated strings, accepting
only the existing Qt Designer PNG path shape. Noncanonical aliases, dot/dotdot,
empty components and backslashes remain CRITICAL rather than normalizing into
the static allowlist. No filesystem, ACL, receipt or digest result is cached.

Compared all 14,038 actual retained-copy manifest paths against the old classifier:
identical results, 377 static and 13,661 critical. Critical files still hash on
every gate; collection, ACL, identity and mutation checks remain unchanged.

Alternating old/new source gate experiment (seconds; all PASS):

| Classifier | Total runs | P50 | Hash runs |
| --- | --- | --- | --- |
| Old | 4.026836 / 4.575110 / 4.795945 | 4.575110 | 1.700258 / 1.730491 / 1.750465 |
| New | 4.364298 / 4.376887 / 4.551135 | 4.376887 | 1.582333 / 1.558413 / 1.771992 |

Evidence: E:/Codex/Temp/E107-TrustBoundary/category-results.json.
This modest source difference is not proof of improved packaged startup.

Built only a small native gate, placed in a new protected test directory:
E:/MentorFlow-TrustTest-20260906/native-category/MentorFlowInstallGate.exe.
SHA-256: 1c7bf63824712fcc8e754b15bb4d3e264504068a9df98e8423ebe214c6ff50cb.
Three same-user normal-token process start-to-exit gate runs all exit 0:
4.514859 / 3.935267 / 3.593567 seconds. P50 3.935267, MAX 4.514859.
Evidence: E:/Codex/Temp/E107-Category-20260906/native-results.json.
Native P50 did not improve against the previous turn's 3.863494 seconds.
The <=3 second goal is NOT met; no new form/visible/full-install timing is claimed.

50 focused standalone tests PASS in 8.386 seconds, zero skips in this selection.
Two new classifier tests cover accepted canonical paths and 18 conservative
rejections. Historical four WinError 1314 skips were not rerun/promoted.
Diff whitespace check passes. An optional shell one-line microbenchmark failed
Python parsing before execution; it supplies no timing or acceptance evidence.

Secure outer installer/bootstrap integration remains incomplete. No final product
build, dependency change, remote push, server mutation, formal data write, or
retained release modification. Final integrated form-interactive target remains
unqualified. This is not a hands-on-ready release.

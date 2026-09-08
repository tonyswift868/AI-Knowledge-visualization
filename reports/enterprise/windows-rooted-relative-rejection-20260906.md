# E107 rooted-relative path rejection and rejected prototype — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 3e3cdc6a7470096b118543db7020d39925a2761c.

## Shipped correction

On Windows Path('/outside') has root '\\' but no drive, so is_absolute() is
false. Joining it to E:/isolated-package discards the package directory. A
cached-relative caller passing that matching external candidate and cached stat
was accepted by the original lexical guard. The normal scandir walker does not
generate such rooted inputs; this is a defense-in-depth validation defect, not
evidence of an exploit against the ordinary installed launch chain.

Replace the is_absolute check with the root check, retaining the drive, parent
traversal, candidate equality, metadata, link, namespace and suffix checks.
Regression reproduced the original failure before the correction and now rejects
both slash and backslash rooted-relative inputs.

54 focused standalone tests PASS in 8.931 seconds, zero skips in this selection.
Four new tests check generic/cached parity, escape rejection, metadata rules and
dot-prefixed stale suffixes. Historical four WinError 1314 skips were not rerun.

## Performance prototype NOT retained

An attempted pure string replacement of the cached-path parsing branch showed
old source totals 3.308152 / 4.069432 / 4.208285 and prototype totals
3.844294 / 3.804831 / 4.059403 seconds. The first prototype used splitext; review
found different '..pyc' semantics, corrected before its native build/testing.

Corrected prototype native normal-token timings: 5.807235 / 4.128880 / 3.902847
seconds, all exit 0. P50 4.128880, MAX 5.807235. No native improvement established.
The performance rewrite was completely removed. The only runtime diff delivered
is the rooted-relative guard correction described above.

Prototype evidence (NOT a delivered helper or final source measurement):
E:/Codex/Temp/E107-TrustBoundary/cached-entry-results.json
E:/Codex/Temp/E107-CachedEntry-20260906/native-results.json
E:/MentorFlow-TrustTest-20260906/native-cached-entry/MentorFlowInstallGate.exe

Do not reuse that helper as a release artifact. No final corrected helper/product
build or new form/full-install timing is claimed. <=3 second gate and <=8 second
interactive form acceptance remain unmet/unqualified. Secure outer installer
integration remains incomplete. No push, final package build, dependency/lock
change, server mutation, formal data write or retained release modification.

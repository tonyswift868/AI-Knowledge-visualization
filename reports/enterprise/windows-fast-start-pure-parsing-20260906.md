# E107 pure parsing reuse — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base bfe161cebc0e75842615f3a937063bcdb9a98160.

## Implementation

Cache only parsing of exact owner/DACL descriptor text, bounded to 128 entries,
returning immutable tuples. Every installation path still calls Windows
GetNamedSecurityInfoW on every verification. No path-to-ACL, authorization,
receipt, metadata, or digest result is cached. Changed or malformed permissions
still reject. Reuse already-normalized components for application-private path
rules; compute each snapshot entry's static classification once instead of twice.

## Evidence

Standalone suite: 32 PASS, 0 skips; new tests prove repeated ACL OS reads despite
cache hits, changed permissions rejection, immutable parsed results, malformed
ACL rejection, and matching normalized path rules. All prior tamper, receipt,
ACL, unknown-file, new-version, external-config and hash-reader cases passed.

Snapshot profiler calls: 4,706,722 before / 3,276,258 after. Profiled cumulative
snapshot time 3.787 / 3.179 s is diagnostic, not a launch performance metric.
An initial timing batch overlapped profiler inspection and is not used below.
The reported batch ran after both prior processes exited, without another task
benchmark or profiler running:

- Complete source INSTALL_FULL: 4.932601 s, hashing 1.957709 s, 14,038 files.
- Complete source FAST: 4.794344 / 4.791095 / 4.798746 s.
- Source FAST P50: 4.794344 s; MAX: 4.798746 s.
- FAST hashing: 1.929608 / 1.921681 / 1.904854 s; 13,661 critical files each.

Isolated source: E:/MentorFlow-TrustTest-20260906/large.
Evidence: E:/Codex/Temp/E107-FastStart-20260906-03/protected-gate-results.json.
Previous source P50 5.236395 s; this is a source measurement, not proof of a
new native improvement. No native helper or final product was rebuilt this turn.
Historical native P50 4.589172 s / MAX 5.281958 s belongs to bfe161c-era code.

## Remaining gates

Security gate <=3 s remains unmet. Installed LoginGate/QtCore/hidden main/clean
exit and <=8 s interactive form are unqualified. Protected installer wiring is
still unfinished. No final product build, push, live login, server mutation,
formal data write or retained release change. Historical four WinError 1314
skips were not rerun or promoted to PASS. Return PARTIAL, not integration-ready.

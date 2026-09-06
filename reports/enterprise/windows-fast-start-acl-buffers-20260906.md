# E107 fresh ACL reads with bounded conversion reuse — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 50ec1b863bc2f98cf443e73c2d460f67b0a38c5b.

## Implementation and boundary

Use GetFileSecurityW for each actual path's owner/DACL on every verification,
with a reusable per-thread output buffer. Cache only conversion of exact complete
self-relative descriptor bytes to SDDL, bounded to 128 entries. There is no
path-to-permissions or successful-authorization cache. Existing strict owner,
DACL, ancestor, file identity, hash, manifest and receipt checks remain intact.

Microsoft documents that [GetFileSecurityW](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-getfilesecurityw)
returns a self-relative descriptor, supplies the complete required length, and
returns no descriptor when capacity is insufficient. The implementation checks
success and length, expands once only on ERROR_INSUFFICIENT_BUFFER, and rejects
oversize (>128 KiB), subsequent growth and all other read failures. Conversion
failure rejects; unknown ACL structure continues to fail closed.

## Measurements

Isolated root E:/MentorFlow-TrustTest-20260906/large, unchanged payload.
Six snapshot-only trials, baseline/candidate/candidate/baseline/baseline/candidate:

- Baseline 2.585512 / 2.528233 / 2.535966 s.
- Sized-copy buffer candidate 2.381280 / 2.334540 / 2.257467 s.
- Every snapshot matched the baseline inventory, identity and file paths.
- An earlier version unnecessarily copied the entire buffer before slicing;
  that variant is not used. Evidence: acl-buffer-results.json and
  acl-buffer-sized-results.json under E:/Codex/Temp/E107-TrustBoundary.

Implemented complete source gate:

- INSTALL_FULL 15.165286 s (hash 12.493783 s), all 14,038 files. This slow
  measurement is retained, not replaced with a faster warm run.
- FAST 3.565166 / 3.601728 / 3.484681 s; P50 3.565166, MAX 3.601728 s.
- Hash 1.593419 / 1.636239 / 1.575461 s; all 13,661 critical files each time.

New small onedir native helper, ordinary same-user token, three exits 0:
4.842929 / 4.142239 / 3.994898 s; P50 4.142239, MAX 4.842929 s.
These measure process start-to-gate-exit, not form interactivity. No <=3 s claim.
Helper E:/MentorFlow-TrustTest-20260906/native-acl-buffers/MentorFlowInstallGate.exe.
SHA-256 d73bff5c7d4238c848900f3536f77f1c2e855d597f70afcb3282449686b6b790.
Evidence E:/Codex/Temp/E107-ACLBuffers-20260906/native-results.json.

## Checks and remaining work

37 standalone tests PASS, 0 skips. Five new tests cover fresh repeated reads,
complete-length copying, single bounded retry, access denial, invalid/oversize
length, descriptor growth during retry and concurrent buffer isolation. Existing
real ACL mutation, tamper, receipt and unknown-file checks run through the new
Windows API. Historical four WinError 1314 skips are not rerun/promoted.

Only a small diagnostic native gate was rebuilt; no final product build, push,
dependency/lock change, live login, server mutation, formal data write or retained
release modification. Prior isolated LoginGate observations are historical, not
fresh UI timings for this helper. Installer wiring, <=3 s gate, final integrated
package and <=8 s form-interactive acceptance remain incomplete.

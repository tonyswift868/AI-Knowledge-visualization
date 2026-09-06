# E107 installation bootstrap follow-up — PARTIAL

Dispatch: MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task: MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base: 8aed43219548796b84dd0da63b925aa8d6975cb1.

The latest admin-owned installation decision remains in force. No return to
DPAPI provenance or permission to skip critical hashes.

## Changes

Shortcut publication previously executed the candidate gate before independently
checking the candidate installation. Installer-stage publication now validates
the NTFS boundary, owners/DACLs, parent replacement permissions, all installed
entries, the caller-pinned manifest, full payload hashes and file set before
starting any candidate process. This full hashing is installer-only, not added
to daily startup. PowerShell imports are restricted to its own system modules.

Parent checks in both the core and bootstrap now also reject ordinary-user
DELETE permission on non-volume ancestors: renaming an ancestor can replace
the executable path even without DELETE_CHILD on its immediate parent. Volume
roots retain their separate treatment. No volume-root ACL was changed.

## Evidence

21 standalone unittest cases passed on Windows, exit 0, no SKIP/XFAIL. They
include four actual PowerShell publication tests with a harmless compiled marker
fixture: a manifest-bound protected package publishes a test link; writable root,
renameable parent and mismatched gate hash each reject with the expected rule
before the marker process executes. Links/markers are confined to newly created
E:/Codex/Temp/e107-bootstrap-* test directories. No production shortcut changed.

The first positive test exposed an inherited PowerShell module-path conflict;
system-only module imports fixed it. Negative tests require the specific rejection
reason, not merely a nonzero exit. No backend conftest or application DB was used.

## Not completed

No new latency or Qt/LoginGate timing was performed. The last 8aed432-era native
onedir gate results remain 10.790 / 9.502 / 9.470 seconds, P50 9.502, MAX 10.790;
they are historical measurements, not a fresh measurement of this repair.
The <=3s security and <=8s form targets remain unqualified/unmet as previously
reported. Real elevated installer UI wiring and complete installed login-chain
acceptance remain unfinished. This fixture smoke is not a final product test.

Source only: no final package build, remote push, server mutation, formal database
write or modification of a retained user package. The previous small native
helper artifact was not rebuilt for this source repair. Integration must rebuild
from the final frozen sources, not reuse that historical helper as current.

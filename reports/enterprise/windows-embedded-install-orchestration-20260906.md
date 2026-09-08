# E107 embedded payload to native installation — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 5d973efe0de3863cddec0bc1eb81a225ce7eabfd.

ProtectedStage now hashes incoming bytes, flushes the newly created file and
reads it back through the same exclusive handle for SHA-256 equality before
returning. This verifies the stored file bytes as observed by the OS, not an
independent physical-media or hardware-fault certification. Empty, multi-block
and deep-path files are covered; failed length/hash checks cannot return success.

InstallFromEmbedded.Run connects compiled archive/manifest/source/tree constants
to protected extraction and the verified stage's native install command. It
requires a status callback, new installation and shortcut paths with safe parents,
and creates separate protected child TEMP/TMP outside the payload. Child environment
is cleared and populated only with Windows system paths and that scratch directory.
No inherited account/database/module/plugin hints or credentials are forwarded.
Windows argument quoting handles spaces; no shell is used.

The child performs install/full verification/receipt/shortcut publication with
the exact compiled identities. Nonzero exit is failure; receipt and shortcut
must exist after success. The elevated orchestrator never launches the product.
Existing target/shortcut entries are refused, not overwritten. Protected staging
and partial output are currently retained; cleanup/update UX is not implemented.

Twelve installer-component tests PASS in 7.294 seconds. The new end-to-end test
uses a synthetic product with the actual previously built gate and runtime:
E:/MentorFlow-TrustTest-20260906/native-acl-batches/MentorFlowInstallGate.exe,
SHA-256 8c989e232048df073b3bbeeb942d43658f7b7f48c92281f2f531b7bdb6a117fd.

The prepared archive and fixed identities are compiled into a diagnostic host.
It actually installs into a new protected test target, creates a space-containing
shortcut, passes independent source FAST verification, and rejects a repeated
target without changing its receipt hash. Only the trusted native installer is
executed; the synthetic product EXE is never launched. All test writes remain
under E:/Codex/Temp and E:/MentorFlow-TrustTest-20260906.

This is a callable orchestration component, NOT a complete production installer.
Still required: visible elevated UI, production build/provenance entry, bounded
cancellation/error UX, protected staging cleanup and destination/shortcut update
policy, plus complete-chain long-path and final integration qualification. The
legacy build rejection remains in force. No new startup measurement, final
product build, dependency change, push, server mutation, formal data write or
retained release change. Historical runtime tests/skips retain their scope.

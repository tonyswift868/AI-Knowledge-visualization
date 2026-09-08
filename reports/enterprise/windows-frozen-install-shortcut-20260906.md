# E107 frozen install-to-shortcut check — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Runtime base b5a6625c062e1e538f3b533a26985247567b4f8b unchanged.

Added a reproducible synthetic diagnostic, check_frozen_install_shortcut.py,
which invokes the already-built windowless helper rather than trust.main in
source Python. The controller was already elevated, matching installation stage.
All targets are freshly created under the protected isolated test container.
The diagnostic never executes its installed placeholder product/gate payload.

Helper: E:/MentorFlow-TrustTest-20260906/native-acl-batches/MentorFlowInstallGate.exe
SHA-256: 8c989e232048df073b3bbeeb942d43658f7b7f48c92281f2f531b7bdb6a117fd.

Actual outcomes:

- Valid synthetic payload: frozen install returns 0, receipt exists, source
  verifier independently returns FAST PASS, protected shortcut has one hardlink.
- Independent Shell COM readback targets installed App/MentorFlowInstallGate.exe,
  with launch command and exact install root/manifest/source/tree pins. No shell
  or product-direct shortcut; no Resolve/Execute operation performed.
- Repeated install into the existing target returns 3; receipt and shortcut
  SHA-256 unchanged, not silently overwritten.
- Tampered Qt6Core.dll source copy: frozen install returns 3, with no receipt or
  shortcut. Its new protected partial directory is deliberately retained.

Evidence:
E:/MentorFlow-TrustTest-20260906/links-da89cb2088574cf7a9e8f20b5ca4ae52/frozen-install-result.json

Installed fixture:
E:/MentorFlow-TrustTest-20260906/frozen-installed-76e392a2109f4d31ae0623693ae66457

The receipt and manifest have synthetic provenance, not product release IDs.
The fixture has ten manifest files; its timing is NOT large-package performance
evidence. Controller exit 0. No new helper build or product rebuild this turn.

This closes the source-only evidence gap for the frozen installation command and
native COM shortcut module. It does NOT qualify secure outer extraction/bootstrap,
UAC installation UX, actual desktop shortcut placement, updates, final integrated
product or <=3s/<=8s performance. Those outstanding requirements remain.

Prior 58 focused tests and startup measurements retain their original scope;
they were not rerun or relabeled. Historical four WinError 1314 skips remain.
No production shortcuts, dependencies/locks, server, formal database, retained
release or old receipts changed. No final package build or remote push.

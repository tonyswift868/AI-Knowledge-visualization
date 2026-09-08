# E107 windowed installation failure feedback — PARTIAL

Dispatch: MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task: MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base: e249186294e45b53af2f2d5eed5d618d110a70b5.

Windowed install/activate failures previously only printed to an unavailable
console. They now show the native repair dialog and retain failure exit code 3.
Quiet installation keeps error presentation with its hosting installer. No
verification, identity, ACL or TLS acceptance rule was relaxed.

48 focused standalone tests PASS (8.543 seconds), zero skips in this selection.
Two added tests cover visible install/activate failure and quiet-host behavior.
Historical four WinError 1314 skips were not rerun or promoted to PASS.

Built only the small native helper using existing dependencies, not the final
product. An actual windowless helper invocation with missing release identities
showed the repair dialog, returned 3 after dismissal, and created no destination.
Evidence: E:/Codex/Temp/E107-InstallFeedback-20260906/error-dialog-result.json.

Protected helper:
E:/MentorFlow-TrustTest-20260906/native-install-feedback/MentorFlowInstallGate.exe

SHA-256: 2c395458f3177911e45c1e9bfb5c9e3c50b97e85814c499f453f36d17b9f8e9b.

Three normal-token gate process start-to-exit measurements, all exit 0:
3.723344 / 3.923071 / 3.863494 seconds; P50 3.863494, MAX 3.923071.
Evidence: E:/Codex/Temp/E107-InstallFeedback-20260906/native-results.json.
These are security gate timings, not click-to-visible or interactive form times.
The <=3 second gate target remains unmet. No new full activation or LoginGate
timing was taken in this follow-up. Earlier measurements retain their original
source/helper/payload scope and must not be relabeled as this build's results.

Secure outer installer/bootstrap wiring and final integrated product acceptance
remain incomplete. Existing NSIS entry is not qualified for the new protected
installation architecture. Native shortcut source tests do not establish a
completed outer installer. Form-interactive <=8 seconds remains unqualified.

No final product build, remote push, dependency/lock change, server mutation,
formal data write, retained release change or production shortcut modification.

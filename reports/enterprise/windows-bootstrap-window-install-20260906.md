# E107 actual installer-window install — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 777c0174b49bc9458ea5d68dd9e37e75d1dae0c9.

BootstrapWindow now stages within the selected installation directory's existing
parent, independently checked by the protected-stage boundary, rather than the
whole volume root. It still requires a new target and does not change parent ACLs.

Added an actual Win32 window interaction regression. It builds the production
windowed installer with a synthetic product payload and the existing frozen native
gate (SHA-256 8c989e232048df073b3bbeeb942d43658f7b7f48c92281f2f531b7bdb6a117fd).
Only windows belonging to that exact process are addressed. Both destination
fields are set to isolated paths and their current contents verified before the
Install button is pressed. The initial attempt stopped before clicking because
GetWindowText returned the cached edit caption; the test now uses WM_GETTEXT to
read the current control contents. That failed attempt was not an install PASS.

Successful run observed the actual visible completion message, clean exit 0,
independent FAST receipt verification and protected shortcut. A second run using
the same targets observed the visible installation error and exit 3; receipt and
shortcut hashes remained unchanged. This does not launch the placeholder product.
No production desktop shortcut or data root was used. Synthetic installation
targets are under E:/MentorFlow-TrustTest-20260906; builder scratch is under
E:/Codex/Temp/e107-window-install-*. Staging remains retained, not silently deleted.

The focused installer suite passed 14 tests in 14.170 seconds:
test_bootstrap_window_install, test_bootstrap_window, test_embedded_install_native,
test_prepare_bootstrap_payload, test_verified_payload_native,
test_protected_stage_native, test_installer_trust_model (explicit test classes).
No account-wide suite, dependency install, final product build, remote push,
server mutation or formal data write occurred. The environment was already
elevated, so actual UAC consent interaction remains untested.

Remaining qualification gaps: staging cleanup/error handling, complete-chain long
paths, final integrated product and startup/form performance. Latest previously
recorded native repeat runs were 3.792764/2.885852/3.054308 seconds (P50 3.054308,
MAX 3.792764); the earlier 17.483957-second outlier is not discarded. Security
target is not met. Old-product isolated LoginGate visibility 5.401283 seconds is
not a form-interactive measurement or final-integrated-product qualification.
Prior runtime 58-case checks and four historical WinError 1314 skips retain their
scope and are not represented as rerun here. PARTIAL_RETURNED only.

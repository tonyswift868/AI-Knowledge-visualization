# E107 passive dwell control — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 5994c05657b0a7f5f30714a0c494c4b56eee11fe.

Added --observe-seconds (0..30, finite) to the diagnostic observer. It is mutually
exclusive with --form-probe. The default remains immediate show/close observation.
The passive mode continues top-level window sampling and waits the specified
duration after first LoginGate visibility before posting close. It does not
access UI Automation or enter credentials. Invalid options reject before any
fixture/data access. Two focused tests PASS in 3.061 seconds (including the
existing synthetic probe and four invalid argument cases).

Actual 25-second passive control on the SAME isolated old-product installation
and native helper used by the preceding failed UI Automation observation:
- Original payload copy hashes PASS.
- Full activation 3.010324 seconds; hash 1.189507 seconds.
- LoginGate visible 4.170182 seconds; bundled QtCore verified.
- No MainWindow observed in continuing pre-auth window samples.
- Close after the dwell succeeded in 0.551289 seconds, launcher exit 0.
- No owned children still running; no forced cleanup needed.

This contradicts attributing the prior close timeout simply to keeping the window
open for approximately 25 seconds. UI Automation intervention is associated with
the failed run, but a single sequential control is not proof of causation. No
product shutdown changes are justified by this evidence alone. The UI Automation
probe remains diagnostic-only and UNQUALIFIED for QtWebEngine form acceptance;
its synthetic Windows Forms PASS must not be promoted to product compatibility.
The earlier failed run and its forced cleanup record are retained, not overwritten.

Actual form-interactive time remains unmeasured. This control proves neither an
editable form nor a successful login. Future observation must avoid perturbing the
Qt accessibility/event loop or establish compatibility before using such a probe.
No final product build, dependency/lock change, push, account/workspace/server
mutation, credential submission or formal data write. Preserved EXE SHA remains
4235ab853c4eb45f848ad92f8f87943c064b0b3be280ea2556700c6e9d98c496.
Historical performance outliers and four symlink skips retain their status.

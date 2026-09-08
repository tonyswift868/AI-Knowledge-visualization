# E107 ACL-batch helper follow-up — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Runtime base 360f5e6b0bca25d23a5317f4242542bb2db01e6d unchanged.

The diagnostic harness now records the duration of CreateProcessWithTokenW and
the interval from its return to observed exit, while retaining the original
complete start-to-exit metric. These are observer intervals, not independently
measured child execution phases; do not subtract the creation interval to claim
a shorter application startup. No production verifier or launcher change.

Same protected native-acl-batches helper, unchanged SHA-256:
8c989e232048df073b3bbeeb942d43658f7b7f48c92281f2f531b7bdb6a117fd.

Follow-up normal-token native totals: 5.746180 / 2.790178 / 3.189195 seconds.
Instrumented totals: 3.792764 / 2.885852 / 3.054308 seconds, P50 3.054308,
MAX 3.792764. All six exit 0. Creation API calls in the instrumented sample:
0.011802 / 0.004509 / 0.005568 seconds. Most observed time occurred after the
API returned. This does not establish the cause of the prior 17.483957-second
sample, which remains in the record. No <=3-second acceptance claim.

Evidence: E:/Codex/Temp/E107-AclBatches-20260906/native-repeat-results.json and
native-split-results.json. Three split intervals sum to their corresponding
original total by construction; the successful run exercised the changed harness.

## Actual isolated LoginGate observation

Used existing protected login-8ea6931bafba fixture and isolated data directory
E:/Codex/Temp/e107-installed-login-8ea6931bafba, not formal data or old user UAT.
Before launch, checked retained EXE pin, original manifest, all original payload
copy hashes, exact augmented set and public CA pin. Automatic remembered login
was checked and absent. No credentials entered or login/recovery action invoked.

Full activation of the augmented 14,039-file fixture: 4.361066 seconds, hash
1.794985 seconds. Then actual normal-token helper launch: LoginGate visible
5.401283 seconds; main window before login false; QtCore loaded from
App/_internal/PySide6/Qt6Core.dll. Closing login exited 0 in 0.525876 seconds,
with no owned child processes remaining.

Scope: current helper with the retained OLD product EXE, immediately following
full activation (warm observation). It is not a current integrated product build,
cold/daily-start qualification or FORM_INTERACTIVE measurement. All prior samples
remain valid in their original scope. Retained EXE hash checked before and after:
4235AB853C4EB45F848AD92F8F87943C064B0B3BE280EA2556700C6E9D98C496.

Prior 58 focused tests not rerun or relabeled; this commit changes only diagnostic
timing fields and evidence. Historical four WinError 1314 skips remain unqualified.
Secure outer installer/bootstrap and final interactive-form acceptance remain
incomplete. No final product build, dependency change, push, server mutation or
formal data write. Existing connection preparation was exercised, not account
or workspace operations.

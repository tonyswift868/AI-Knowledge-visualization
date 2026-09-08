# E107 actual form-readiness observation — NOT QUALIFIED

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base a4dad8e3015738238274076e9321aee00125d653.

Added an optional diagnostic UI Automation probe to the existing isolated
packaged observer. It binds an explicit HWND to the expected product PID, reads
only control properties, requires visible/enabled/focusable password and writable
non-password edits, and confirms focus on the non-password edit. It never reads
Name/Value text, types credentials, changes field values or invokes buttons.
There is a bounded 25-second observation loop; the controller bounds the probe
process at 35 seconds. This is a readiness upper bound including diagnostic
startup overhead, not evidence of a successful authenticated workflow.
API reference: https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.automationelement

Actual frozen old-product observation used the existing isolated login fixture
E:/MentorFlow-TrustTest-20260906/login-8ea6931bafba with the new native helper
ce4207257834521d3718a93d884db22d84d07674846049c1403030c16cce546b.
Original copy hashes PASS; full activation 3.734578 seconds, hash 1.371043 seconds.
LoginGate visible at 4.891470 seconds; bundled QtCore found. No MainWindow was
observed before login in the visibility sampling (the synchronous probe does not
continuously enumerate top-level windows while it runs).

FAILURES, not converted to PASS:
- Probe exit 4: FORM_NOT_OBSERVED edits=0. No form-ready time is established.
- After posting close, launcher did not exit within 15 seconds.
- Owned child PIDs 37280/35420/74184 were still live at that deadline. Existing
  harness cleanup terminated its own remaining tree; subsequent process checks
  confirmed those children absent. This is forced test cleanup, NOT clean exit.
- Observation file: E:/Codex/Temp/e107-installed-login-8ea6931bafba/observation-65608.json.

One actual synthetic WinForms regression PASS in 1.509 seconds: visible password
and non-password controls are recognized, focus acknowledged, wrong PID rejected,
synthetic window closed. This validates the basic probe mechanism, not QtWebEngine
accessibility or actual product readiness. Zero observed edits may be a provider
accessibility limitation or an unloaded form; cause is not yet determined. Do not
infer that the application lacked a form solely from this accessibility result.
Actual close timeout remains an independent unqualified result to investigate.

Preserved EXE SHA after test still
4235ab853c4eb45f848ad92f8f87943c064b0b3be280ea2556700c6e9d98c496.
No credentials or login submission, workspace/recovery action, server deployment,
formal data mutation, dependency install, push or final product build. The existing
launcher may prepare the TLS connection and app may request its authorization page;
this is not a claim of zero network requests. Only isolated receipt/data touched.
Historical four symlink skips unchanged. Current result PARTIAL_RETURNED.

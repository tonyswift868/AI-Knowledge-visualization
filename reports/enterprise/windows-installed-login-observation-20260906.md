# E107 isolated packaged LoginGate observation — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 09db2ed58ad32437eadb5b0a8f93f65ad46738f3.

## Scope and trust

Executed the actual retained EXE through the existing native protected launch
helper, using a physical copy, not source Python application startup. No final
product build or production shortcut was created. No login click, credentials,
workspace operation, import or authenticated account flow was performed.

- Retained EXE SHA-256 before/after:
  4235ab853c4eb45f848ad92f8f87943c064b0b3be280ea2556700c6e9d98c496.
- Original manifest pinned to
  a786bd894abea739b3b3f1a8a797b160cc8b56e88cf4f40ce580d8ad78ba9380.
- All original copied file digests and complete file set verified against that
  original manifest BEFORE accepting the isolated augmented manifest.
- Only addition: existing public pinned CA at App/Config/edge-tls-ca.crt.
- Isolated manifest:
  ccd8db105cd285c6f7591421648c1bee1eef59061f8ea2b644be26d268b9c177.
- Synthetic receipt source/tree f*40/e*40: test identity, NOT release provenance.
- Root: E:/MentorFlow-TrustTest-20260906/login-8ea6931bafba.
- New data root: E:/Codex/Temp/e107-installed-login-8ea6931bafba.
- Native helper: E:/MentorFlow-TrustTest-20260906/native-hash-buffers/MentorFlowInstallGate.exe.
- Helper SHA-256:
  d8ab578b6e351b13d36ddb99cb51abe24aeef1dfa35c46a4395bda5cbf138b2d.
  This is the existing bfe161c-era auxiliary build, not current-source final UI.

The harness first checks the actual global auto-login boolean and refuses if
enabled. It does not change account preferences. Existing psutil is used only
by the diagnostic observer; no dependency was installed or added to the product.

An initial preparation was stopped BEFORE any child/application execution when
review found original-manifest verification needed to precede re-manifesting.
The harness was corrected and resumed only the exact owned test directory.
No aborted preparation is counted as successful launch evidence.

## Observed actual native launches

All three use the verified same-user normal token. Win32 observation detects
the visible window titled 登录 MentorFlow, watches other titled child windows,
then sends WM_CLOSE without interacting with account controls.

| Run | Process start to visible LoginGate | Close to helper exit | Exit |
| --- | --- | --- | --- |
| 1 | 8.972697 s | 0.699937 s | 0 |
| 2 | 6.685318 s | 0.890028 s | 0 |
| 3 | 6.542075 s | 0.304826 s | 0 |

Visible-window P50 6.685318 s / MAX 8.972697 s. No pre-login main window observed;
no owned children remained. QtCore loaded in all runs; runs 2/3 additionally
verify its actual mapping is inside the protected payload at
App/_internal/PySide6/Qt6Core.dll, not a system/developer installation.

Each observation followed a complete installation activation, which warms
filesystem caches. These are warm show/close observations, NOT cold-start or
three unmodified daily-start performance acceptance runs. Complete activation
times were 5.875733 / 5.513278 / 5.933050 s for 14,039 files. They are outside
the reported process-to-window interval; preparation itself is not included.
Normal helper launch still uses the non-elevated repeat gate, not activation.
No form-ready signal or interactive login form was measured. Thus <=8 s form
interactive remains UNQUALIFIED; do not equate a visible LoginGate with success.

Evidence files under the isolated data root:
observation.json, observation-21336.json, observation-49276.json.

## Delivery and remaining gates

32 focused standalone tests PASS after observation, 0 skips. Historical four
WinError 1314 cases were not rerun/promoted. No formal database writes, account
mutations, dependency/lock changes, push or retained-package changes. Existing
connection preparation was exercised by the native launcher; no server config,
membership or deployment was changed. All harness-owned processes exited.

Protected installer wiring, <=3 s security gate, current integrated final package
and <=8 s interactive form remain incomplete. This closes a bounded actual-binary
show/close evidence gap, not final integration or user-test readiness.

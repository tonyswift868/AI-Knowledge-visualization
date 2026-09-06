# E107 frozen native path-reuse gate — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Runtime source ffd6237f38a8e7075d3636bf0c9422156af6e6ab,
tree 29565e9ec9606cf184915ec32323c5c11dedbee6.

Built only the small windowed installation gate, with existing Python 3.13.14 /
PyInstaller 6.21.0 and the existing build_windows_install_gate.ps1 script.
Build output E:/Codex/Temp/E107-HashPathReuse-20260906. No dependencies installed,
no final product rebuilt. The build warned about running PyInstaller as admin;
this was the existing elevated environment, not a new elevation request.

Copied to a new isolated protected helper directory, preserving the old helper:
E:/MentorFlow-TrustTest-20260906/native-hash-path-reuse/MentorFlowInstallGate.exe.
SHA-256 ce4207257834521d3718a93d884db22d84d07674846049c1403030c16cce546b.
Root descriptor observed:
O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU).
No volume-root ACL was modified.

Actual native gate processes used the verified same-user normal token against
the existing isolated large installation, without activation or product launch.
All exit codes 0:

| Run | Process start-to-exit seconds | Process creation call | Post-creation wait |
| --- | ---: | ---: | ---: |
| 1 | 3.652612 | 0.304528 | 3.348085 |
| 2 | 2.653088 | 0.005561 | 2.647526 |
| 3 | 2.629414 | 0.005121 | 2.624292 |

P50 2.653088, MAX 3.652612. Target <=3 seconds remains NOT QUALIFIED.
Do not subtract first-process creation overhead to claim a passing sample.
Earlier native/source outliers remain valid. This is not a simultaneous paired
native baseline experiment, so the source median improvement must not be reported
as a quantified native speedup. Raw evidence:
E:/Codex/Temp/E107-HashPathReuse-20260906/native-results.json.

Existing 59 focused runtime tests passed before freezing this unchanged source;
not rerun in this documentation-only turn. Historical four WinError 1314 skips
not rerun or promoted. No Qt/LoginGate/form interaction run in this turn.
The existing observer closes on visible LoginGate plus bundled QtCore; that does
not establish embedded identity-provider form readiness or editable controls.
Do not relabel its historical 5.401283-second visibility measurement as interactive.

No final product/UAT build, push, server mutation, formal database or preserved
product write. Remaining native long-tail and actual form-interactive qualification
are still required. No goal completion claim.

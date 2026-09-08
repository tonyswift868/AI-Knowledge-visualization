# E107 frozen deferred-import gate — three-run gate target observed

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Runtime source f0c33931dd89f4f4ac8a9b7e2cb4818e72fba868,
tree f30e004b7e16df4e04b98467332b711f56c99060.

Built only the small native gate with the existing build script and installed
PyInstaller 6.21.0 / Python 3.13.14. New protected helper, older helpers preserved:
E:/MentorFlow-TrustTest-20260906/native-deferred-utilities/MentorFlowInstallGate.exe.
SHA-256 156fad0c0bd7ac008269b716fa4bdfab1a9a05682b1c8dbb6dfdb1ad36bfdc4c.
Root ACL: O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU).
No volume ACL changed. _sqlite3.pyd remains present, and inspection of the actual
embedded PYZ confirms sqlite3, sqlite3.dbapi2, urllib.request and urllib.error.
Thus deferred imports were not stripped from the helper. This archive check is
not a new runtime updater/download test; the four source updater tests are prior
evidence associated with this unchanged runtime source.

Actual normal-token native gate processes on the existing isolated large install:

| Run | Start-to-exit seconds | Process creation call | Post-creation wait |
| --- | ---: | ---: | ---: |
| 1 | 2.974892 | 0.212665 | 2.762227 |
| 2 | 2.286175 | 0.004517 | 2.281658 |
| 3 | 2.432933 | 0.005533 | 2.427399 |

All exit 0. P50 2.432933; MAX 2.974892. The <=3-second target was met in this
three-run measurement window, including process creation. No run discarded.
Raw evidence: E:/Codex/Temp/E107-DeferredUtilities-20260906/native-results.json.
Earlier 17/26/34-second native/source outliers remain in history and are not
explained by this result. No universal/cold-start performance guarantee is made.
This is not a concurrent paired native comparison, so do not assign the entire
observed improvement to deferred imports alone.

No product/LoginGate/form run this turn. Interactive form readiness still lacks
valid evidence; prior window visibility is not interchangeable with it. Overall
goal remains PARTIAL_RETURNED, not final user-test ready. Source regression status
is prior 61 PASS plus updater 4 PASS; historical four WinError 1314 skips unchanged.
No final product build, new dependencies, remote push, server mutation or formal
data write. Build output E:/Codex/Temp/E107-DeferredUtilities-20260906.

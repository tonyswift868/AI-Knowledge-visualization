# E107 bounded scheduling experiments — not adopted

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Unchanged runtime source: cf3244cdc84e5ae8deacd67e598ca905d76aca48.

## Overlap hypothesis and result

Prototype pipelined snapshot inspection with hashing only after the individual
file path/ACL checks. It retained the directory mutation watcher, complete file
set/receipt comparisons and all 13,661 critical digest comparisons. Four reader
threads, at most 64 pending futures; no code execution or receipt publication
before final verification. It was opt-in while testing, never the default.

Six sequential A/B/B/A/A/B FAST runs on the isolated protected large package:

| Strategy | Total seconds |
| --- | --- |
| Existing sequential snapshot then hash | 4.223147, 5.233031, 4.954285 |
| Overlap with per-submit future polling | 5.668716, 5.648499, 5.516944 |

One bounded revision polled pending work only when the queue reached 64:

| Strategy | Total seconds |
| --- | --- |
| Existing sequential snapshot then hash | 4.109255, 4.657070, 4.688243 |
| Overlap with bounded polling | 5.174814, 5.212020, 5.233887 |

All twelve runs PASS with unchanged critical hash counts. Neither candidate
improved latency. Prototype and its API/metric fields were removed using an
exact reverse of this turn's owned patch. Runtime source diff against cf3244c
is empty. There is no pipeline path or dormant scheduling option in delivery.

## Reader-count confirmation after buffer optimization

On the restored implementation, sequential 1/4/4/1/1/4 reader trials:

| Readers | Total seconds | Hash seconds |
| --- | --- | --- |
| 1 | 5.568971, 6.535170, 6.604006 | 3.361155, 3.603942, 3.547288 |
| 4 | 4.905009, 4.857612, 5.006750 | 2.029018, 1.966854, 1.982523 |

Four readers remain justified on this isolated payload/host. This does not
establish cold-cache timing or a guarantee for other disks. All runs verified
the same 13,661 critical files; none executed the product or used live accounts.
Three four-reader runs P50 4.905009 / MAX 5.006750 s, source only, still >3 s.

Evidence under E:/Codex/Temp/E107-TrustBoundary:
pipeline-results.json, pipeline-bounded-results.json,
buffer-reader-count-results.json. Runtime root:
E:/MentorFlow-TrustTest-20260906/large.

## Delivery state

32 standalone tests PASS after prototype removal; 0 skips. Historical four
WinError 1314 cases not rerun/promoted. This commit records rejected candidates,
not a speedup or integration readiness. No new native/final build, push,
server mutation, formal data write or preserved-release change. Installer wiring,
<=3 s gate and installed LoginGate/form/QtCore/close acceptance remain incomplete.

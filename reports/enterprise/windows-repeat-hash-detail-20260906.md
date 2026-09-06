# E107 per-file hash diagnosis — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base cf5bdd9eff330e7bcddbd9cbd00c4760b6dc8ed4.

Optional --hash-detail wraps the actual sha256_file without replacing any byte
read, digest, prefix or exception. Records are synchronized across readers;
summary sorting and extra size reads occur after the measured gate. This adds
instrumentation overhead; reader-time sums overlap and are NOT wall time.

Read-only source gate on the existing isolated large installation, three runs:

| Metric (seconds) | Run 1 | Run 2 | Run 3 |
| --- | ---: | ---: | ---: |
| Gate wall | 4.388422 | 2.586187 | 2.782104 |
| Hash phase wall | 3.113752 | 1.338864 | 1.467303 |
| Sum of per-call reader time | 12.304273 | 5.225431 | 5.719307 |
| Top 20 call reader time, rounded | 2.57 | 1.06 | 1.10 |

Gate P50 2.782104; MAX 4.388422. Every run FAST/PASS, 13,661 critical entries
hashed out of 14,038 files; 13,662 instrumented calls include the manifest.
Largest individual calls include torch_cpu.dll, Qt6WebEngineCore.dll, MentorFlow.exe
and cv2.pyd. However the top 20 account for only approximately 19-21% of aggregate
reader time. Evidence does not support treating only a few large DLLs as the
overall bottleneck. Nor does it establish which I/O/cache/CPU/scheduling effect
caused the prior 34.275783-second run. That sample remains valid and was not
reproduced or explained by this diagnostic. No performance PASS is claimed.

Raw output: E:/Codex/Temp/e107-repeat-hash-detail-20260906-01.json.
Three diagnostic regression tests PASS, including actual synthetic-file digest
and prefix preservation, exact call delegation, exception propagation and wrapper
restoration. Runtime security regressions were not rerun; no runtime algorithm
or static allowlist changed. No final package, dependency install, push, server
mutation or formal data write. Form-interactive timing remains unmeasured.

Further performance work should distinguish cumulative file-open/read/CPU costs
before choosing a runtime change. This result is diagnosis, not a speedup.

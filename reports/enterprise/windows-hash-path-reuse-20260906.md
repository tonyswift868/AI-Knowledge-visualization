# E107 reuse validated snapshot paths during hashing — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 44e0dd3cf40417a5662722d2116aa1f61f3e9b7e.

The installed gate previously rebuilt root/key Path objects for every hash even
though its completed snapshot already held those same validated paths. The gate
now passes that per-invocation dictionary to _hash_gate_entries. The generic
callers retain their existing path construction. This is internal trusted data
flow from _snapshot, not a caller-supplied manifest field or external config.
No path map persists between verifications. All namespace, link, file identity,
ACL, inventory, watcher and hash checks remain. Every critical file is reread.
No hash result or authorization is cached by this change.

Read-only interleaved comparison on the fixed isolated large installation:
baseline means calling the same hash routine without the snapshot map; reuse
means supplying it. One process, original four readers, original batches of 32.

| Order | Variant | Gate seconds | Hash seconds |
| --- | --- | ---: | ---: |
| 1 | baseline | 2.726996 | 1.409255 |
| 2 | reuse | 2.441215 | 1.163001 |
| 3 | reuse | 2.352990 | 1.067484 |
| 4 | baseline | 2.720356 | 1.425057 |
| 5 | baseline | 2.726186 | 1.432480 |
| 6 | reuse | 2.471248 | 1.151228 |

Baseline P50 2.726186, MAX 2.726996; reuse P50 2.441215, MAX 2.471248.
Source median reduction 0.284971 seconds (~10.5%) in this comparison. All six
FAST/PASS with 13,661 critical hashes and 14,038 total manifest files. These are
warm source-process observations, NOT a native bundle acceptance result. The
earlier 34.275783-second long tail is neither explained nor fixed by this change.
Do not claim the overall <=3s target is met or compare these to form readiness.

59 focused runtime tests PASS in 11.244 seconds. Coverage includes serial/batch
digest and prefix equivalence with and without path reuse, partial batch tails,
missing files and missing map entries fail closed, changed bytes produce changed
hashes, ACL drift prevents payload hashing, existing tamper/receipt/inventory
rejections, external config boundary, shortcuts and deferred imports. The map
does not introduce any config/public-input path. Existing four historical
WinError 1314 skips were not rerun or promoted. No account-wide suite run.

Only verifier/startup/test/report source changed. No native helper rebuilt this
turn; no final product build, dependency/lock change, push, server mutation or
formal data write. Native benefit and interactive-form timing remain unqualified.

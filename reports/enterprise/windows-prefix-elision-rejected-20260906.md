# E107 prefix-elision experiment rejected — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 4b23e0c86c1e189012f4ac0fe4dadfbab83e0dcc.

Tested avoiding per-file header extraction during FAST mode, while keeping all
critical byte reads and SHA-256. Full activation still collected headers for its
privacy check. An interleaved experiment compared extraction enabled/disabled
using the same existing validated-path mapping and four hash workers. Both arms
used the temporary FAST caller without storing the prefix dictionary, so this
isolates extraction, not a complete before/after production comparison.

| Order | Header extraction | Gate seconds | Hash seconds |
| --- | --- | ---: | ---: |
| 1 | on | 26.798128 | 25.165558 |
| 2 | off | 3.006338 | 1.274504 |
| 3 | off | 2.979161 | 1.286074 |
| 4 | on | 3.040319 | 1.212159 |
| 5 | on | 2.876401 | 1.246690 |
| 6 | off | 2.829449 | 1.130848 |

All six FAST/PASS with 13,661 critical hashes / 14,038 manifest files. On P50
3.040319/MAX 26.798128; off P50 2.979161/MAX 3.006338. Warm hash durations overlap,
and the first long tail must not be discarded or blamed on header copying.
No stable benefit established. Both temporary runtime changes were completely
removed; Git confirmed the verifier/runtime files equal the base. No new flag,
branch or altered privacy behavior is delivered. Existing path reuse stays.

This is a rejected experiment, not a speedup or a passing performance gate.
No native helper or final product rebuilt, no form observation or security suite
rerun, no dependencies, push, server mutation or formal data write. Prior native
<=3s and form-interactive qualification remain incomplete. Historical four
WinError 1314 skips retain their status.

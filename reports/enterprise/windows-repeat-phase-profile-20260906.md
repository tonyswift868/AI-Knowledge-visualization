# E107 repeat gate phase diagnosis — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 5982778ef92123231fb3260a79b8666c303d7c49.

Added a read-only source diagnostic for the existing isolated large installation.
It wraps real verification functions to measure durations, does not skip checks,
activate receipts, change ACLs or touch the retained product. It records exactly
three runs and uses exclusive output creation so earlier evidence is not replaced.
This is source-process timing with instrumentation, not native launcher timing.

Actual results (seconds):

| Phase | Run 1 | Run 2 | Run 3 |
| --- | ---: | ---: | ---: |
| Total | 34.275783 | 2.570014 | 2.677588 |
| Critical hashing | 31.382239 | 1.286254 | 1.407679 |
| Snapshot including entry ACL | 2.736466 | 1.196977 | 1.177814 |
| Entry ACL (included above) | 0.614905 | 0.491894 | 0.478115 |

P50 2.677588; MAX 34.275783. All FAST/PASS, all 13,661 critical files hashed
out of 14,038 total. No sample discarded. This run's long tail is primarily in
critical hashing, not ACL checks. The underlying I/O/cache/other-process cause
is NOT established; do not infer antivirus causation or claim performance PASS.
Next investigation should attribute hashing latency before further ACL tuning.
Raw evidence: E:/Codex/Temp/e107-repeat-phases-20260906-01.json.

Two focused diagnostic regressions check wrappers invoke real delegated functions,
restore them and propagate RepairRequired. No runtime verifier changes or final
package rebuild in this turn. Prior security regressions and historical four
symlink skips remain historical, not rerun. Form-interactive timing remains
unmeasured; old LoginGate visibility is not an interactive-form measurement.
No dependency/lock, server, formal database or product data changes. Goal remains
incomplete, PARTIAL_RETURNED.

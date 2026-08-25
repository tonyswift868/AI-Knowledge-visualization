# MentorFlow W6-01 Delivery Fence

DELIVERY_ID = MF-W6-01-20260825-01

STATUS = PASS_OFFLINE_REAL_PROVIDER_QA_PENDING_NON_BLOCKING

BRANCH = core-v2/w5-teacher-fastpath-cache

BASE_COMMIT = 32b6f8eed55d5d07dbfc2faba92621bfd8e8e0fa

FUNCTIONAL_HEAD = 750771de1c150c7d63d88caa81f607ad417fb3dc

FENCE_COMMIT = the Git commit containing this receipt

## Delivered scope

- `TeacherExecutionService` composes the FAST/DEEP resolver, prompt envelope, single streaming Provider port, validated Teacher result, existing Application transaction, and Classroom Session lifecycle without endpoint wiring.
- `TeacherContextBudget`, `ContextBudgetPlanner`, and `ContextSelectionReceipt` retain the current question, concept, and required evidence before optional learner context and recent history. Required evidence overflow fails before the Provider call.
- `TeacherResponseLengthPolicy` supplies shorter FAST targets and a larger DEEP allowance in the original prompt. It never summarizes through a second Provider call.
- `TeacherExecutionTrace` records route, prompt identity, stable-prefix hash, estimated input tokens, sanitized cached-input metadata, relative monotonic Provider/chunk timings, TTFT, total latency, Provider calls, cancellation, timeout, and the degradation decision.
- `TeacherDegradationDecision` permits the current slow FAST stream to continue, surfaces timeout/cancellation/invalid-stream outcomes, and requires caller choice before retry. Automatic additional Provider calls are fixed at zero.
- The offline harness executes 10,000 fake turns across confused, why, example, related tangent, explicit DEEP, long evidence, short/large history, duplicate replay, cancellation, Session resume, and 100 concurrent fake Sessions.

## Provider and endpoint fence

- REAL_PROVIDER_CALLS = 0
- ROUTING_PROVIDER_CALLS = 0
- FAST normal turn = at most 1 primary streaming call
- DEEP normal turn = at most 1 primary streaming call
- Duplicate replay = 0 repeated calls
- Automatic fallback/retry calls = 0
- `backend/app/main.py` unchanged from base
- `backend/app/active_teaching.py` unchanged from base
- No endpoint wiring or real Provider adapter was added.

## Offline 10,000-turn benchmark

Command: `python -m app.teaching.fastpath_v2.benchmark --turns 10000 --fast-ttft-ms 1000 --fast-total-ms 5000 --deep-ttft-ms 2000 --deep-total-ms 10000 --timeout-risk-ms 20000 --prompt-token-budget 8000 --evidence-token-budget 4000 --history-token-budget 2000`

- completed streams = 9,800
- duplicate replays = 100
- cancellations = 100
- concurrent fake Sessions = 100
- resumed Sessions = 1
- FAST / DEEP = 8,912 / 1,088 (89.12% / 10.88%)
- primary Provider calls, actual / expected = 9,900 / 9,900
- stable-prefix unique count = 1
- orchestration p50 / p95 = 1.0371ms / 3.4121ms
- fake TTFT p50 / p95 = 1.0220ms / 3.3941ms
- estimated prompt tokens p50 / p95 = 1,716 / 3,600
- context items p50 / p95 = 17 / 54
- prompt token budget = 8,000

These measurements cover local orchestration with zero-network fake chunks. They are not real Provider latency, production TTFT, or billable token usage.

## Hardening evidence

- malformed chunk: fail-closed, one call, no retry
- empty output: fail-closed, one call, no retry
- late first chunk: `FAST_SLOW`, continue the same stream, no fallback
- oversized required evidence: fail before Provider, required evidence not dropped
- minimum context-budget overflow: fail before Provider
- missing required evidence: fail before Provider
- missing required citation in final output: invalid stream, no state commit or retry
- duplicate Session turn: idempotent replay with zero repeated calls
- Session pause/resume: existing transaction state preserved
- timeout: explicit decision, retry requires caller choice
- cancellation: `CancelledError` semantics preserved with a safe trace
- 100 concurrent fake Sessions: isolated SessionRuntime executions and one call per original turn

## Verification

- W2-W6 endpoint-neutral package chain: 236 passed in 14.79s.
- Focused W6 execution/context/telemetry/hardening set: 20 passed in 0.46s.
- Full 10,000-turn benchmark test: 2 passed in 13.41s.
- Python compileall for Teacher Runtime v2 and Fast Path v2: passed.
- `git diff --check`: passed.
- An attempted legacy `test_teacher_turn_v1.py` collection was not executed because the isolated lightweight environment does not install FastAPI; this endpoint test is outside the endpoint-neutral W6 package gate.

## Frozen identities

- stable Teacher prefix = `sha256:5910e3a9f83142c2c2f2947d66f4245171044a52f01826fc505f6bf44b9ec641`
- `backend/app/teaching/composer.py` SHA-256 = `266aa76bbaa5a58ccc5a8f2c18e6f7bd04f73b4342b8cd27202862cb153a5abc`
- `backend/app/teaching/fastpath_v2/prompt.py` SHA-256 = `a8b3b49b20b6666db2af33327793801de9716ca05c90780fdb07f2fab104854d`

## Non-blocking QA handoff

- request = `TRQ-W01-20260825-203004-01`
- queue state = `inbox\01\TRQ-W01-20260825-203004-01.ready.md`
- request SHA-256 = `bf4f05f8cd9f3dc8a9212b9fa4afd6bc3d6f1bfa6c58d8d31ce6247181a47d95`
- requested evidence = real FAST TTFT, real DEEP TTFT, Provider prompt-cache signal, and actual Provider token usage
- blocking = NO
- real Provider results = PENDING and not claimed by this Delivery Fence

## Functional commits

1. `f41d12969d47ecb04ab1100455cf4d9da48c047a` — production classroom execution composition
2. `d023915` — context budget and response-length policy
3. `1f8a579` — execution telemetry and single-call degradation decision
4. `750771de1c150c7d63d88caa81f607ad417fb3dc` — 10,000-turn offline benchmark and hardening

## Acceptance boundary

Offline W6 core milestones M1-M8 are complete. Real Provider latency, prompt-cache behavior, and billable token measurements remain `PENDING` under the non-blocking QA request and must not be inferred from the fake benchmark.

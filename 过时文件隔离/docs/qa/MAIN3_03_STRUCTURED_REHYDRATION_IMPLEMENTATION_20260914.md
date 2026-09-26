# Structured account rehydration — implementation closeout

DISPATCH_ID = MF-MAIN3-03-STRUCTURED-REHYDRATION-IMPLEMENTATION-20260914-01

TASK_ID = MENTORFLOW_MAIN3_STRUCTURED_REHYDRATION_FULL_IMPLEMENTATION_V1

STATUS = PARTIAL

## Phase 0

`worker/main3/03` was a private linked worktree with a private HEAD/index at
`E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow3/`. The original local
delivery was preserved as `worker/main3/03-pre-structured-rehydration`. The
worktree was aligned to foundation `7b447f1d0500ce5d4c977702316df0a3ac04a494`
and consumed the immutable 01/02/04 inputs:

```text
01 a553ba35dc92509d77a708dd96a766d0e4c370b4
02 ac75a90b4c6f26d4ecb381ca3ea23cdcd2b8f0f8
04 37362076dacff5bcefbc90a1e0c6fc300e96e370
```

No integration/main3, main2, or other worker branch was modified. The three
inputs share the foundation parent; their actual files and tests are now in
this branch.

## Delivered implementation

- `RehydrationDispatcher` validates complete snapshots, exact issuer/subject/
  organization/workspace scope, nonempty revision, unique entities, supported
  kinds, payload shape, and tombstones before invoking caller-supplied domain
  adapters. It performs no commit, network call, tutor call, reward,
  notification, or generated history side effect.
- `PlanTaskMutationService` is a canonical ORM PlanTask mutation boundary for
  create/update/start/complete/reopen/reschedule/archive. It rejects scope and
  stale mutations, flushes the canonical `PlanExecutionTaskORM`, then invokes
  compatibility projection and outbox callbacks in the same caller-owned
  transaction.
- The generic HTTP adapter accepts all ten registered structured kinds.
- Structured payloads recursively reject password/token/secret/credential and
  key material fields before persistence.
- `SUPPORTED_SCOPE` now includes plan, progress, resource metadata, task,
  calendar, learner profile, learning run, history, and non-secret settings;
  model binaries remain metadata-only.
- Existing immutable domain adapters for PlanTask/Calendar, LearningRun/
  History, and Profile/Settings are present in the branch and exported through
  the tenancy package.

## Honest remaining gaps

This is not the final six-domain PASS. Existing product repositories and
active-teaching writers have not all been migrated to the new mutation service;
there is no completed compatibility projection implementation, durable
structured-sync outbox relay/ACK path, Device-B real-store dispatcher wiring,
or canonical startup snapshot→apply chain. Calendar still has no domain
revision and therefore concurrent writes remain `UNSUPPORTED_FAIL_CLOSED`.
No PostgreSQL service/fixture was available in this local run, so RLS and
real A→server→A-offline→fresh-B qualification, duplicate/stale/wrong-scope
end-to-end evidence, and real product-visible restore were not claimed.

The existing jobs-v1 outbox was inspected and remains a generic job/event
system; it was not silently treated as a structured entity outbox without a
matching payload and ACK contract. No SQLite, PostgreSQL, Keycloak, Vault,
source file, or user data was mutated.

## Validation

```text
17 passed — immutable PlanTask/Calendar, LearningRun/History,
             Profile/Settings and new dispatcher focused tests
8 passed  — dispatcher, mutation boundary, registry and authenticated
             rehydration regression set
```

One initial test invocation stopped during fixture setup because its requested
temporary parent directory did not exist; after creating the dispatch-specific
temporary directory, the complete focused run passed. No GitHub Actions,
PostgreSQL, live installation, migration, or external service was executed.

```text
PLAN_TASK_AUTHORITY = BOUNDARY_ADDED; active_teaching direct writers remain
ACTIVE_PLAN_TASKS_ROLE = COMPATIBILITY NOT YET FULLY CLOSED
CANONICAL_TRANSACTION = PARTIAL (service boundary, product callers pending)
COMPATIBILITY_PROJECTION_TRANSACTION = CALLBACK CONTRACT ONLY
ROLLBACK_ATOMICITY = CALLER-OWNED TRANSACTION CONTRACT
SYNC_OUTBOX = EXISTING JOB OUTBOX INSPECTED; STRUCTURED RELAY PENDING
GENERIC_SYNC_WIRING = REGISTRY + API + SNAPSHOT SCOPE EXPANDED
DEVICE_B_DISPATCHER = ADDED FAIL-CLOSED ADAPTER DISPATCH CONTRACT
STARTUP_REHYDRATION = PENDING FULL PRODUCER/APPLY WIRING
POSTGRES_BACKED = NOT RUN
RLS_SCOPE = EXISTING SCHEMA CONTRACT, NOT QUALIFIED THIS RUN
REHYDRATION_SIDE_EFFECT_FREE = PASS (dispatcher contract/tests)
MULTI_DEVICE_CONCURRENT_WRITE = UNSUPPORTED_FAIL_CLOSED
REVIEW_FSRS = NOT_CLAIMED
FREE_CHAT_HISTORY = NOT_CLAIMED
COURSE_HISTORY = NOT_CLAIMED
INTEGRATION_MAIN3_MUTATED = NO
MAIN2_MUTATED = NO
MAINLINE_MERGE_EXECUTED = NO
GITHUB_ACTIONS_EXECUTED = 0
```

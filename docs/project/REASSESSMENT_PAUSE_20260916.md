# MentorFlow Main3 — paused for architecture reassessment

Status: `PAUSED_FOR_REASSESSMENT`

Date: 2026-09-16

Authoritative review branch: `control/main3-current`

This document freezes the current execution state. It does **not** authorize any new implementation, migration, deployment, email delivery, branch promotion, account mutation, or cleanup of Codex global storage.

## Why execution is paused

Main3 has accumulated repeated local fixes and acceptance discoveries across account lifecycle, structured sync, Windows packaging/storage, cross-device rehydration, old-data ownership, migration, and deployment. Gate1 is now formally passed, but Gate3 exposed two additional structural admission problems before real legacy migration can begin. The project is therefore paused for a fresh architecture review rather than continuing defect-by-defect dispatching.

## Canonical state at pause

### Gate 1 — Frozen GUI A/B + full account rehydration

`PASS`

Integrated authority before this pause:

- `control/main3-current @ 63731b6e062ff02264d1a08441a9d7c5d79bce0d`
- Gate1 worker delivery: `57c94162090ef0f15e00cd0c1ba37c976d8c0fc2`
- Final Frozen source: `a1190ca18753fdedb3e4c155efeb3a111cfbb94f`
- Final Frozen build: `MF-02-FROZEN-35-a1190ca1`
- Final Frozen SHA256: `a31849225ba6a7becee8116b579b4d4bce35be6984673cbf2af97b320313c2de`

Accepted Gate1 evidence includes:

- 352-character Windows long-path GUI upload PASS
- resource metadata/revision committed to server
- A -> Server -> B -> Server -> A PASS
- A/B complete process restart rehydration PASS
- account isolation PASS
- delete/tombstone and sibling boundaries PASS
- interrupted-command UI reports uncertainty explicitly and does not blindly replay
- OS-wide SendInput count 0
- filesystem copy A<->B count 0

`P0_PRODUCT_SYNC_WRITE_GAP = CLOSED_SOURCE`

### Gate 2 — Email lifecycle E2E

`BLOCKED_EXTERNAL_AUTHORITY`

Current blocker:

`QQ_TEST_MAILBOX_READ_CREDENTIAL_REFERENCE_REQUIRED`

SMTP TLS/AUTH/NOOP readiness has been qualified. Real inbox receipt, verify/reset link consumption, replay, expiry, and same-user continuation are not accepted.

### Gate 3 — Canonical old-data ownership / data target stability

`BLOCKED_PROJECTION_ADMISSION_AND_OWNER_BINDING`

Latest Worker04 local delivery:

- result SHA: `bf8c35999e2c6752a928cf375b9091d9c68bd363`
- result tree: `8f2074633ec16d199d7ae0c1d81f31be99537b17`
- worker branch: `worker/main3/04`
- delivery is recorded here as evidence; it was not remote-visible through the GitHub review connection at pause time and is not claimed as integrated product authority.

Verified/latest Gate3 findings:

- legacy baseline: 530 workspaces / 88 plans / 330 resources
- stable ID mapping for those workspace/plan/resource records has been audited
- 55 dangling ownership references were classified and quarantined individually
- 4 non-privileged PostgreSQL control tests PASS
- real canonical legacy import/RLS acceptance is **not complete**
- real old-data migration has **not run**

New blocking issue A — projection admission:

56 historical records exceed the current 256 KiB projection limit:

- calendar: 17
- replan history: 35
- plan activation: 4

These records require an explicit architectural decision before migration: keep current projection limit and split/normalize payloads, raise/replace the projection contract with bounded evidence, or redesign the representation. No automatic truncation or silent drop is accepted.

New blocking issue B — owner binding:

The primary legacy owner group is still not proven to a canonical account identity. Username/display-name/email text is not sufficient authority. Ownerless/quarantined data must remain fail-closed until ownership is proven.

Gate3 therefore remains `NOT_PASS`.

### Gates 4-8

Not accepted / not executed:

4. Portable Pack V2 + classification/recommendation
5. Real old-data migration
6. VPS exact Main3 deployment/live activation
7. Final signed installer/updater acceptance
8. Final QA + acceptance bundle + Main3 promotion

## Execution freeze

Until reassessment is explicitly completed by the Owner, all workers and integration roles are considered `SILENT / NO_DISPATCH`.

Forbidden during the pause:

- no new feature implementation
- no defect-chasing dispatches
- no real old-data migration
- no VPS deployment or service mutation
- no real SMTP send
- no TonySwift account/credential/role mutation
- no public issuer cutover
- no `integration/main3`, `main2`, `main`, or formal `main3` promotion
- no Workbench CURRENT mutation
- no global Codex storage mutation
- no cleanup of global Codex SQLite/WAL/SHM

Allowed during the pause:

- read-only repository inspection
- architecture review
- evidence review
- comparison against mature open-source/reference architectures
- preparation of a new review report / decision record

## Mandatory reassessment questions

Before resuming implementation, the review should answer these questions explicitly:

1. Is the current local-first + custom structured-sync architecture still the right product architecture, or should parts be replaced/adapted using mature patterns such as PowerSync-style local mutation/upload queues?
2. Which user-data domains truly require server canonical state, and which must remain local/portable/rebuildable?
3. Why do 56 legacy records exceed the 256 KiB projection contract, and is the projection representation itself wrong for those domains?
4. What is the canonical identity/account binding model for legacy ownership, and what evidence is sufficient to authorize migration?
5. Should Portable Pack V2 remain a required release gate, be reduced, or be replaced by a simpler large-resource continuity design?
6. Which account/email requirements are product-critical versus staging/UAT infrastructure requirements?
7. Can Gate4-8 be simplified into a smaller release path without weakening data integrity, account isolation, rollback, or user recovery?
8. Which existing custom mechanisms duplicate mature open-source components and should be kept, adapted, or replaced?

## Reference architecture review policy

Future reassessment must compare relevant MentorFlow subsystems against mature references before proposing new code. At minimum:

- account/credential/email lifecycle: Keycloak contracts and required actions
- local/server sync and retry/idempotency: PowerSync-style architecture invariants
- tenant isolation: PostgreSQL RLS fail-closed semantics
- large-resource continuity/conflict handling: Syncthing-style content/hash/conflict concepts where applicable
- Windows packaging/update/path behavior: Microsoft/Windows documented contracts and mature installer/updater patterns

Reference projects are comparison baselines, not automatic dependencies. Each recommendation must state `KEEP / ADAPT / REPLACE` and why.

## Storage policy remains active during reassessment

- C = system/application runtime
- D = high-churn scratch/test/temp/disposable DB/cache
- E = durable source/assets/accepted evidence/final artifacts
- no D -> E fallback when D is full
- each worker gets an independent D scratch root
- ordinary workers must not mutate global Codex storage

## Resume condition

Do not resume implementation from this pause document alone.

Resume requires an explicit Owner decision after architecture reassessment and a new bounded plan that names:

- architecture decisions
- gates retained/removed/merged
- exact acceptance criteria
- migration ownership authority
- release path
- dispatch budget

Until then: `STOP`.

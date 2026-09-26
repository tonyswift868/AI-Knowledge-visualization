# MF-R1-04-CONFIRMED-GROUP-PREP

Base: `afed403e974f6823d138217311f81bc831c67754`.
Worker branch: `worker/main3/04-data-r1`.
This receipt supersedes the previous selection and 58-file package. Prior code and
native PostgreSQL/independent-B coverage remain at that commit; they are not new
live acceptance. No source database, server, account/password or deployment changed.

## Owner confirmation and exact scope

The Owner explicitly confirmed the entire `internet-interview-20260815` group.
All **1,173 records** are admitted, including five ORM planning versions, two active
representations, 797 execution tasks, three materials, knowledge graph, history and
activation records. This ownership question is closed; do not ask it again.

`prepare(..., confirmed_workspace_ids=[exact_id])` now accepts explicitly confirmed
existing ORM roots. It rejects nonexistent roots and any competing owner record.
It does not add source ownership rows, alter server permissions or derive authority
from usernames. The existing authenticated target-scope check still applies.

`excluded_workspace_ids` selects reviewed exclusions before existing FK closure.
Confirmed/excluded overlap and nonexistent excluded roots fail closed. No business
IDs or payloads are rewritten. Unrelated ownerless rows are not admitted.

## Product selection after test/demo cleanup

| Disposition | Source records |
|---|---:|
| Admissible after confirmation, before cleanup | 29,207 |
| Proven test/demo groups and their excluded dependencies | 23,030 |
| Ten unresolved old trial groups, retained outside product | 3,236 |
| **Final selected source records / prepared records** | **2,941 / 2,941** |
| Unparented ws-sidecar runs, separately retained | 6 |

There are **573 excluded test/demo workspace roots**: 529 ORM roots and 44 active
acceptance/rehearsal roots. Evidence combines known constructors/explicit demo
material/fake generation, matching fixture metadata, acceptance IDs and artifacts.
This is not a title-only, empty-table or repeated-import deletion rule.

Examples: seven `t` / `hash` metadata-only sources match
`backend/tests/test_v2_features.py`; their roots/dependents are excluded.
Archive fixtures match `backend/tests/test_v6_archive_import.py` and the exact
frontend E2E ZIP fixture. Default demo workspaces contain the explicit App.tsx
`Demo 学习资料` content, not just a default workspace title. V5 real-mode fixtures
also match their `v5` goal and mocked provider constructor. Generic-e2e IDs and
materials match the historical test in
`a4521d2584bc452be5624ff06dbe5d9c7e702be9^:frontend/e2e/active_teaching_chain.spec.ts`.
The older Learning Run Idempotency constructor is in
`ea8e4b7da0cadc1e94fd7c959091ddb91eb6d6f1^:backend/tests/test_learning_run.py`.
Exact workspace IDs and evidence references are in `MIGRATION_INPUTS.json`.

The 23,030 excluded count includes **one mixed-provenance local_default profile**;
it is retained in backup because it references excluded demos. It is not asserted
to be entirely synthetic. The 34 scoped personal detailed memories remain selected.

Selected content includes:

- Confirmed internet interview: five ORM plans, two active representations, 797 tasks.
- Existing authorized `ws_eb4425c22355-active`: one active plan, 648 tasks,
  150 progress snapshots, 150 knowledge points, calendar, reviews and histories.
- In total: 79 teaching messages, 35 replan-history rows, 33 review items,
  185 parsed active chunks, four ordinary materials. Full counts are in the manifest.

The existing current 150-question workspace stays even though its material is
reusable demo content: a sample file alone does not prove the user's learning is
fake. Original source records remain unchanged in the pinned backup.

## One unresolved classification question

Ten roots remain outside the selected manifest: four LeetCode-e2e roots, four
browser/one-click trial roots, and two first-test roots from July 24–27. Naming and
sample material alone cannot establish they are disposable. No rows are deleted.

For the coordinator, only if needed:
**“2026年7月24–27日的10个旧试用计划（4个 LeetCode-e2e、4个通用资料浏览器/一键试用、2个 first-test）是否都是验收试用数据，可以不恢复？备份已保留；当前150题学习计划和已确认互联网面试计划照常保留。”**

This is classification, not a repeated ownership question. The six ws-sidecar
orphan runs stay separate. Do not report complete cleanup of unresolved groups.

## Current private execution artifacts

- Prepared: `E:/Codex/Temp/MF-R1-04-COVERAGE/prepared-confirmed-clean.json`
- Original package: `E:/Codex/Temp/MF-R1-04-COVERAGE/canonical-originals-confirmed-clean.mentorflowpack`
- Package bytes: **21,524**; four original resources; missing selected originals: **0**.
- SHA256: `b9d9a1b928d0b4c2ed3e4f4b1616fe128e701e2f274c9c17b325d5cb99c70866`.
- Independent empty canonical B: **4/4 stable IDs and SHA256 PASS**, A unmounted.

The prior 58-file package includes excluded test/demo/trial resources and is
**historical evidence only**. Do not execute the previous prepared.json or package.
The pinned PRIMARY SHA and current prepared SHA are in MIGRATION_INPUTS.json.
No new metadata records are needed: all four resources already have stable IDs.

## Reproduction and actual execution prerequisites

Read the existing manifest's `intake_selection`. Supply its exact confirmed list
and combined `test_demo_exclusions` + `pending_classification` workspace ID lists
to `prepare` using the pinned source SHA and owner hash. Use the existing
`attach_original_files` with explicit known data roots and exact hash checks.
The CLI exposes repeated `--confirmed-workspace` / `--excluded-workspace` flags;
selection is operator-reviewed input, never source-provided authorization.

After A deploys matching code: take/verify fresh pre-write backups, confirm the
sealed canonical account/org/workspace and source freshness, and call the existing
`execute(prepared, initialized_empty_product_db, authenticated_relay,
expected_scope=sealed_scope)`. Keep **the same durable journal on every retry**.
The changed manifest needs its own initially empty journal; an already-started
older-manifest journal correctly fails `INTAKE_JOURNAL_MISMATCH` and must not be
reset to bypass that guard. No real execution journal was started this round.
A newly changed source needs newly reviewed preparation; do not silently repin it.

Current validation: three focused preparation tests (including complete confirmed
row/payload/stable-ID/FK closure, competing-owner refusal, exact fixture exclusions,
and preservation of the authorized plan/tasks/progress) plus the actual four-file
canonical package roundtrip. Capacity, large-model, SMTP and live migration tests
were not run. Source SHA unchanged; original files were read only.

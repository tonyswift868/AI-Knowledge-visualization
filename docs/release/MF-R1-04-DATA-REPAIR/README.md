# MF-R1-04-DATA-COVERAGE

Base: `9e430b7373907fa7be2f8ebb02493dbb18ff1314`; branch
`worker/main3/04-data-r1`. This updates the existing delivery, not a new sync engine.
Capacity/edge/IPC/model work from that base remains intact and was not retested.
No real server data, account/password, schema, deployment or source database changed.

## Admission defect and correction

The prior intake incorrectly intersected `orm_workspace_ownership.workspace_id`
with `orm_workspaces.id`. The ownership table actually covers both ORM and active
workspace namespaces. All **55** references previously called dangling have owned
active plans/materials/settings; absence of a duplicate ORM workspace is expected.
They now seed admission directly from their existing owner record. No ORM workspace,
enterprise membership, account, business ID or second ownership table is invented.
Declared foreign-key closure and the authenticated expected-scope check remain.

The one `local_default` learner profile is also traceable: the existing
`compact-cross-task-v1` producer stores eight owned workspace references in its
current state/learned points; all detailed-memory workspaces are owned. Its adapter
admits it only when these provenance checks hold. A foreign reference test fails
closed; this is not permission to import arbitrary account-global JSON.

| Existing domain | Admitted | Held |
|---|---:|---:|
| ORM business workspaces | 529 | 1 |
| ORM plans | 83 | 5 |
| Active plans | 55 | 2 |
| Active plan tasks | 11,363 | 0 |
| Active progress snapshots | 3,654 | 0 |
| Active knowledge points | 3,654 | 0 |
| Active calendars | 48 | 0 |
| Active replan history | 38 | 0 |
| Active learner profile | 1 | 0 |
| Active materials | 70 | 3 |
| ORM source metadata | 330 | 0 |

Original registry: **23,831 / 24,975** admitted, formerly 2,923.
Existing parsed-text/memory stores now use the same typed registry/producer/restore:
`orm_chunks` 500; `active_chunks` 3,669; `active_knowledge_memories` 34.
Thus **28,034 source records admitted**, **1,179 held**. Three independently verified
archive originals add existing-format asset metadata, for **28,037 prepared records**.
Complete per-table counts are in `MIGRATION_INPUTS.json`; private prepared records
remain in restricted scratch. ORM and active plan counts are distinct domain records,
not a claim that the user has 138 unrelated learning plans.

## Text is structured data, not a missing file

Of the 292 `text` sources, 285 have stored parsed text; the source/chunk relationship
and all 500 ORM chunk rows (also covering all 38 archive sources) are preserved.
The other seven rows have title `t`, content hash `hash`, no chunks, and match the
metadata-only fixture constructor in `backend/tests/test_v2_features.py`. None of
18 corresponding retained ORM backups contains their missing chunks either. Their
metadata stays; no nonexistent body is fabricated. **None of the 292 text sources
is reported as a missing original file.** Active parsed chunks and detailed memory
are preserved as existing business rows, not binary attachments in PostgreSQL.

## Actual independent-device proof

Using an isolated clone of the retained source, scoped to the owned current active
workspace `ws_eb4425c22355-active` and one actual text-source workspace:

- Authenticated HTTP writes commit through the disposable application-role PostgreSQL.
- An independent, empty B restores through the existing product producer.
- `active_teaching.current_plan()` reads the real restored product plan successfully.
- **648 tasks, 150 knowledge points, 150 progress snapshots, 150 active text chunks,
  and the selected ORM text chunk** are present. Every admitted slice projection
  and stable ID matches; foreign-key checks are clear.
- A second account sees no rows. No new real realm account was created.

The entire admission set is projected/validated locally. This is not a claim that
all 28,037 records have been sent to the real server; that execution awaits A.

## Original-file investigation and recovery

Bounded search used the known PRIMARY/SECONDARY/legacy app indexes and retained
backup roots: **18 app databases**, their storage paths, backup manifests, and the
known data/snapshot directories. No whole-disk scan was performed. No existing ZIP
or MentorFlow pack was found in those bounded locations. The old archive importer
explicitly deletes the preview ZIP after successful import.

Three `study_bundle.zip / notes/java.md` references retain their original ZIP-member
size/SHA256 in indexes. The stored UTF-8 text matches both exactly; those **three
original byte streams** are safely recovered into scratch. Parsed text without that
independent raw-member proof is never presented as an original.

The **35 archive originals not found** are:

| Recorded member of study_bundle.zip | References |
|---|---:|
| notes/java/jvm.md | 7 |
| src/main.ts | 7 |
| one.md | 7 |
| good.md | 7 |
| lesson.ipynb | 7 |

All their structured text remains in the source/chunk intake. Counts are distinct
source references from repeated imports, not 35 unique filenames.

For owned active materials, 14 originals match directly. Another 41 text/JSON files
were changed from LF to CRLF on disk. Reversing that normalization matches BOTH the
recorded byte length and SHA256 exactly; only those verified bytes are staged. Source
files are untouched, and mismatching candidates remain missing. Fifteen active
originals remain unavailable in the bounded sources/backups, while their parsed
learning state remains restorable.

Final package: **58 verified originals** = 3 archive + 14 direct active + 41 verified
line-ending recoveries. It uses the real canonical account identity in an isolated
Vault and the existing Full Portable Export. No personal-local package is relabeled.

- Path: `E:/Codex/Temp/MF-R1-04-COVERAGE/canonical-originals-final.mentorflowpack`
- Bytes: 195,503
- SHA256: `5e38b4776042b69ace07e110f532eb2b4614090288c010736166e7e4168018cb`
- Closed A -> independent empty canonical B: **58 / 58 resource IDs and SHA256 PASS**.

Real packing exposed two bugs fixed here: local resource binding/portable metadata
rejected legitimate colon-bearing server IDs; and retrying identical-content assets
could collapse distinct resources onto one local ZIP member. Only `asset_id` metadata
now accepts colons; local/package/artifact path IDs retain strict rules. Staging
preserves each resource's catalog local ID and avoids shared-member collisions.

## Held groups and the single Owner question

**1,173 rows** belong to the unbound `internet-interview-20260815` group: five ORM
planning versions, two active representations, 797 execution tasks and related
materials/knowledge/history. The recognizable goal is “三周建立互联网软件工程师面试基础与表达能力”,
created/revised Aug 15–18. Existing reference evidence does not bind it to the known
owner. **Six** other rows are unparented `ws-sidecar` runs (July 23/Aug 12; names suggest diagnostics),
with no matching task/workspace parent; they remain separately retained as orphans.
Neither group blocks the proven primary/active intake and neither is deleted.

One question for the coordinator to ask, if needed:
**“2026年8月15–18日的‘三周建立互联网软件工程师面试基础与表达能力’计划（五个规划版本），是否也并入 tonyswift？”**
No technical-table decision or repeated approval of the already-owned groups is needed.

## Execution preparation for A

The existing `prepare_owner_data_intake.py` entry now accepts repeated
`--archive-index <known-app.db>` and `--file-root <known-data-root>`. It performs only
read-only preparation, exact-hash recovery and private scratch output. The concrete
prepared manifest is `E:/Codex/Temp/MF-R1-04-COVERAGE/prepared.json` (private data;
never commit it). Recovered originals and proofs are alongside it.

Before real execution: integrate/deploy matching backend and Desktop contract code,
including the previous route capacity changes; take/verify fresh backups; confirm the
sealed canonical scope in `MIGRATION_INPUTS.json`; then reuse
`execute(prepared, initialized_empty_product_db, authenticated_relay,
expected_scope=sealed_scope)` with the SAME durable local journal on every retry.
The live account should stay out of normal use during the coordinated intake.
A changed fresh source requires preparation from that new pinned backup, not reuse
of a stale row set. There is no new login, credential resolver or migration protocol.

This round: **7 distinct focused cases PASS**, plus actual 58-file canonical package
recovery. Native PostgreSQL was disposable and cleaned up. Tests covered active
namespace ownership, real product read after independent restore, foreign scope,
profile provenance denial, text recovery hash refusal, package resource/path ID
separation, and repeated same-content resource staging. Capacity and the 2.5GB
model tests/downloads were not repeated. No real migration or deployment ran.

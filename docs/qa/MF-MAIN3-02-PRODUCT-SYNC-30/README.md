# Worker 02 — product sync source repair checkpoint

Dispatch: `MF-MAIN3-02-PRODUCT-SYNC-WRITE-CONVERGENCE-20260916-30`.

**STATUS = PARTIAL. P0_PRODUCT_SYNC_WRITE_GAP = OPEN.**

This is a tested source checkpoint, not a completed whole-product convergence
delivery and not authority to promote a Frozen candidate. The
[domain matrix](domain-write-matrix.csv) deliberately retains `MISSING` and
`PARTIAL` entries. It must not be summarized as all supported product domains
being wired.

## Source and isolation

- Approved control input: `1087091598037b8b4077fd44328ae3312b9ebe4d`.
- Worker alignment merge: `c975ab23ecd5efca98ffd8846353860833d336c8`.
- Own worktree: `E:/Codex/Data/worktrees/08ac/MentorFlow`, branch `worker/main3/02`.
- Private Git directory: `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow2`;
  index is in that private directory. No other branch, shared control ref,
  Workbench CURRENT, server or real account was modified.
- No Frozen build, GUI automation, screenshot, real login, VPS deployment or
  physical payload copy between devices was performed.

## Root cause and implemented boundary

The native account bootstrap consumed structured sync snapshots, while ordinary
active-teaching/ORM writers continued committing only to account-local SQLite.
Registered server kinds and isolated adapter tests did not connect these product
transactions to a server producer. There are additional independent writers:
legacy `app.db`, mounted in-memory Plan/Review services and QSettings preferences.

The new producer records row identity and delivery identity in the same SQLite
transaction as an allowlisted domain write. It uses the existing product database,
not a second domain database. The journal is delivery bookkeeping; the existing
PostgreSQL structured-sync transaction still owns server revision, membership,
idempotency and tombstones.

`product_sync_contract.py` contains explicit record and field allowlists. Existing
product IDs remain inside the record. Protocol IDs use the existing material ID
for resource metadata and a namespaced deterministic key for other records.
Plan relationships retain the material IDs. Required relationship records have
an explicit auxiliary flag; they remain in the authoritative envelope but do not
inflate the task summary or appear as user tasks.

The child account API sends bounded JSON over an owned multiprocessing pipe.
The parent retains the existing authenticated transport and credentials. Normal
API boundaries authorize, drain prior delivery, invoke the business handler and
drain its commits. A lifecycle-owned periodic drain also covers background ORM
writes. Server ACK identity/revision/payload are checked before recording success.
Timeout, denial, conflict, invalid ACK and rejected contract remain distinguishable.

Payloads are frozen before a possibly ambiguous network attempt. A lost ACK
reuses the same request identity. This qualifies **delivery retry**, not arbitrary
re-execution of a whole HTTP business command. Whole-command idempotency remains
an explicit unfinished item.

Rehydration validates the authenticated scope, complete PostgreSQL envelope,
record schema, IDs and revisions before applying to existing product tables in
a transaction. Foreign-key constraints are deferred until the complete apply.
Apply does not echo intents and does not copy files. It preserves an existing
device's local locator; a fresh device receives metadata with an empty locator
and reports `MISSING_ON_THIS_DEVICE`. Older snapshots and omissions of previously
acknowledged identities fail closed. Existing unbound domain data is rejected
before installing the sync journal: this delivery is not a migration grant.

The server accepts the optional validated product-record extension through the
existing structured-sync endpoint. Legacy payload shapes remain accepted.
The registered review kind is now included in the supported manifest scope.
No server migration, authentication redesign or RLS change is introduced.

The response ACK header is explicitly scoped to ORM/active-teaching records.
Other API paths receive `PARTIAL_PRODUCT_COVERAGE`, not a global sync success
claim. This does not repair their missing producers.

## What remains before P0 can close

1. Legacy `/api/workspaces`, source, plan/day/task and related readers still
   maintain `app.db` separately from the synchronized ORM records. Their actual
   write and read authority must converge; copying the database is not a fix.
2. The mounted `PlanProductApiService` stores previews, publications and runtime
   state in dictionaries. The mounted Review page uses `DemoReviewMemory` and
   an offline question-bank adapter. Neither is a durable account aggregate.
   Serializing a demo helper and calling it production sync would be false.
3. `LocalProfileSettingsStore` has a passing standalone PostgreSQL contract, but
   normal desktop preference actions still write QSettings. The explicit
   nonsecret field mapping and actual product consumer are unfinished.
4. Delivery UUIDs do not make repeated HTTP create commands idempotent. The
   original command identity/result must survive an ambiguous whole-command
   response before universal CREATE/UPDATE/RETRY can be claimed.
5. The complete per-entry CRUD/conflict matrix, every ORM learning-run transition,
   and all model/asset metadata producers have not been qualified. The matrix
   contains 18 audited entry groups, not an exhaustive count of individual HTTP
   and background write entry points.

These gaps are not hidden behind a “source PASS”. All five requested top-level
domain families remain `PARTIAL` when the whole product is considered, although
the concrete active-teaching subpaths listed as `WIRED` now have source evidence.
Keep the existing 1000-entity snapshot limit in mind; this adapter does not claim
unbounded history or a pagination implementation.

## Verification and reproducibility

The source harness is `scripts/product_sync_native.py`, reusing
`scripts/schema_0036_native.NativePostgres`. It creates a unique owned cluster
under `E:/Codex/Temp`, admits no external DSN, uses non-superuser application roles
and tears down its owned cluster. Its guarded entry point is safe for Windows
spawn. Use a short scratch root to leave room for account hash namespaces.

```powershell
python scripts/product_sync_native.py `
  --pg-bin E:/Codex/Temp/postgresql-17.6-windows-x64/pgsql/bin `
  --scratch E:/Codex/Temp/p30
```

Runtime used for this qualification:
`E:/Codex/Data/worktrees/2735/MentorFlow/.venv/Scripts/python.exe` (read-only
dependency use; all product imports and edits come from worker 02). The existing
pytest-qt dependency was made available for its two qtbot cases; Qt was offscreen.
This was not native-window or Frozen acceptance.

Verified product cases include actual upload, reversible archive and explicit
unreferenced purge; receipt/profile updates; an allowed deterministic plan fixture
followed by real completion/progress/calendar/history writers; a real review start
handler; independent B product reads; and B workspace-setting modification restored
to A. Further cases cover lost ACK, invalid ACK, denied account before mutation,
four scope mismatches, stale/omitted snapshots, unbound existing data, record schema
and private-field rejection, parent/child transport, a real spawned account API
upload and A/B/A API process isolation.

The review-create precondition is a fixture. Plan generation does not contact a
Provider. Full review submission, all commands and the incomplete matrix entries
are not upgraded to PASS by these representative cases.

Retained regressions: existing 22 native PostgreSQL protocol cases plus three
Profile/Settings PostgreSQL cases; 26 desktop transport cases; six desktop
rehydration integration cases; 35 rehydration UX cases; three long-account-path,
three path-convergence, six material-pipeline, eleven upload-limit, eleven
resource-reference and eight missing-resource recovery cases. Additional login
handoff and process-isolation cases exercise the changed startup seam.

Machine-readable final test identities/counts are retained in `test-results.json`.
Initial fixture failures (missing pytest-qt, an overlong scratch directory and an
incorrect assumption that ordinary DELETE means purge) were corrected and their
affected checks rerun. They are not counted as final passes until the rerun passed.

`REAL_USER_DATA_MUTATED = NO`, `PHYSICAL_PAYLOAD_UPLOADED = NO`,
`FILESYSTEM_COPY_A_TO_B = 0`, `VPS_DEPLOYED = NO`, `SECRET_EXPOSURE_COUNT = 0`.

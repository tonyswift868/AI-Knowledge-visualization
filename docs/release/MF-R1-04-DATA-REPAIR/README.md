# MF-R1-04-DATA-REPAIR / DATA-COMPLETE

Continuation base: `89756ae14bcc096c9887a7622b9d971631dbd5d1`.
Branch: `worker/main3/04-data-r1`. Earlier read-boundary and original-file recovery
fixes remain intact. This delivery is source + isolated rehearsal; A must integrate
and deploy before any real intake. No real user data or server configuration was
written. `desktop/main.py`, login and the workbench were not edited.

## Capacity closure

The same typed projector now accepts all 24,975 records in the pinned PRIMARY
snapshot, including all 56 previously rejected records. Largest measured JSON is
6,458,439 bytes. Only `active_plan_calendars`, `active_replan_history` and
`orm_plan_activation_records` receive an 8 MiB record limit; other types retain
256 KiB. Schema, column, stable-ID, finite scalar, secret/path and typed-domain
validation remains unchanged. No truncation, raw-file transport or chunk protocol.

Two additional real transport limits were found and repaired:

- Both named nginx configurations grant 9 MiB only to the existing exact-shaped
  `/api/enterprise/sync/v1/entities/{kind}/{entity_id}` route. Other routes retain
  1 MiB. Configs are delivered for A; live nginx was not changed or executed.
- The private product pipe accepts a 9 MiB write envelope. Snapshot replies use
  UTF-8 with a 160 MiB pipe ceiling. The previous 1 MiB write would reject the
  historical aggregates before they reached HTTP.
- Actual PRIMARY intake hit `SYNC_SNAPSHOT_LIMIT_EXCEEDED` after 1,000 committed
  rows. Snapshot now permits 32,768 items and 128 MiB estimated JSON bytes;
  PostgreSQL checks both before constructing the JSON aggregate. The same single
  SQL statement still supplies the MVCC observation. Over-budget snapshots fail
  explicitly, never return a truncated `complete=true`.

## Concrete migration entry and admission

`backend/app/product_intake.py` reuses `project`, `record_id`,
`ProductSyncProducer`'s durable frozen-intent journal, CAS/replay, and real product
store restore. `prepare()` reads an immutable SHA-pinned snapshot. Explicit
workspace ownership seeds the admission set; declared FK children follow admitted
parents. Unresolved ownership and dependencies remain outside the intake.

Current admitted set: **2,923 records**, including **529 business workspaces,
83 plans, 330 source metadata rows and 83 progress records**. Business IDs remain
unchanged. The single existing canonical enterprise workspace is the transport
scope; no 529 enterprise memberships/accounts are created. Complete per-table
counts remain in the protected execution scratch `intake-summary.json`.

`execute(prepared, initialized_empty_product_db, authenticated_relay,
expected_scope=sealed_scope)`:

1. Compares the authenticated relay scope to the coordinator's exact sealed scope.
2. Authorizes and inspects the target snapshot; different existing values fail
   collision checks, including tombstones.
3. Persists deterministic request IDs and frozen projections in the existing
   producer journal. Retry must reuse the same local journal DB.
4. Flushes using normal authenticated sync writes. A dropped ACK after server
   commit replays the same request rather than creating a duplicate.
5. Verifies every expected server payload, restores the real local domain tables,
   then verifies every restored projection before setting `completed=1`.

An interrupted batch is NOT declared complete. Retrying preserves the journal and
reuses committed rows. Do not discard the journal or use a fresh intake ID midway.
This does not promise atomic visibility for the whole migration; keep the account
out of normal use during the coordinated write/verification window.

Preparation command (prints only counts; output contains private source records
and belongs in restricted scratch, never Git):

```powershell
python -B scripts/prepare_owner_data_intake.py `
  --snapshot <read-only-prewrite-backup.sqlite3> `
  --sha256 <verified-backup-sha256> `
  --owner-sha256 1fafc6b6212ca2ba594bcf72aea9764092688ab44b1aa8f75813265dd2c1aee5 `
  --output E:/Codex/Temp/<restricted-run>/prepared.json
```

A's execution prerequisites: integrate this commit, deploy both applicable route
capacity and backend changes, confirm 0040 + canonical scope, preserve verified
pre-write application/identity backups, initialize an empty local product DB with
normal ORM and active-teaching initialization, then inject the existing authenticated
`ProductSyncRelay`. No credential CLI or new identity mapping is introduced.
The coordinator reported 01's schema/identity completion at
`069ab91341bcfe6555bbeeae507ef175cb8b9d34`; 04 did not mutate/redeploy it.

## Originals and canonical Portable Export

`attach_original_files()` adds typed ordinary-file metadata only for present,
readable admitted source files; model weights are excluded. `stage_original_files`
requires the mounted **canonical account ID**, imports locally without duplicate
cloud publication, binds each real server resource ID, and updates the existing
portable asset catalog. The existing System Page Full Portable Export reads that
catalog. No personal-local archive is relabeled as an enterprise archive.

Synthetic proof passes: intake original -> canonical Vault -> normal Full Portable
Export -> A closed/source removed -> independent empty B -> exact stable ID/hash
and bytes. Wrong-account staging fails; retries do not duplicate local assets.

**Current actual file gap:** all 330 admitted source rows have an empty `local_path`:
292 are `text`, 38 are `archive_file`. No ordinary original file is available from
those locator records. Structured rows restore, but this evidence cannot claim
38 archived originals were recovered. Existing archives/original files must be
located by the coordinator before real byte staging; nothing is fabricated.

## Held data distinction

The unowned business workspace is `internet-interview-20260815`, created
2026-08-15 11:58:28; its five plans were created Aug 15 and Aug 18, with 797
execution tasks. Exact ID search found no fixture in repository tests/scripts.
Absence of a fixture match does not prove user ownership. It remains separate,
as do the 55 dangling ownership references and other active-runtime rows whose
workspace ownership is not in the proven primary set. They are not silently dropped
from source. SECONDARY/LEGACY are not appended as duplicate batches.

Coordinator's one concrete Owner question, if inclusion is desired: does the
`internet-interview-20260815` learning workspace (five Aug 15/18 plans) belong to
your learning data? The already approved 83-plan primary group needs no new approval.

## Public model recovery and composition

The existing builder's exact public Qwen material catalog moved to
`backend/app/model_material_catalog.py`, shared by builder and runtime. It pins
`Qwen/Qwen3-TTS-12Hz-0.6B-Base` revision
`5d83992436eae1d760afd27aff78a71d676296fc`, eleven exact size/SHA256 files.
Server-provided URLs cannot extend that trust; unknown/private weights remain
local relink-only. The existing voice pack catalog is empty and grants no extra
public model authority.

`LocalAssetLifecycle.install_public_model()` installs the eleven-member tree
under the current Vault's `models/<revision>/`, streaming and verifying each
member; existing verified members/cache are reused. Size limits come from the
pinned member declarations. Temporary partial files are removed; completed verified
members survive cancellation/retry. Only after full material verification does the
existing `reacquire_resource()` bind the original server resource ID. No model
bytes or duplicate USER_FILE metadata are uploaded. This installs model material;
it does not build a new Python/inference runtime or install private trained voices.

Recovery Page's real button runs on a bounded background executor following the
existing data-action pattern, supports cancel/retry, and rejects stale account
completion. A's composition call, after authentication and current Vault mount:

```python
rehydration_page.bind_resource_recovery(
    current_account_asset_lifecycle,
    model_cache_root=operator_selected_existing_public_model_cache,  # optional
)
```

The optional cache must be selected by the existing runtime/composition owner;
there is no hardcoded discovery of another project in product code. Every reused
file still needs the trusted size/hash. The already-present cache was read-only
verified: **11 files / 2,516,100,892 bytes / all pinned hashes matched**. No large
model was downloaded again. Tests cover bounded HTTPS response streaming, exact
size, cancellation/retry, trusted cache install, hash/source rejection and no cloud
publication. Packaged/main composition is for A; no runtime build is claimed.

## Validation and remaining execution boundary

- Typed capacity/edge-contract suite: 11 PASS, including all three exceptional types and ordinary
  256 KiB rejection. Current retained-source projection audit: 24,975 / 24,975 PASS.
- Native PostgreSQL: measured-size HTTP write -> CAS/retry -> snapshot -> empty B;
  full 2,923-row PRIMARY intake -> lost ACK retry -> verified restore -> independent
  empty B with all admitted counts/FKs; foreign account sees no rows; snapshot
  item-budget rejection. No external DSN or real realm test accounts were used.
- Asset/UI affected suites and focused follow-ups: 52 distinct PASS, covering canonical package export,
  empty B original recovery, public model execution, cancellation and account fences.
- Private-pipe tests: 7 PASS, including the measured 6.46 MB record, existing
  authenticated relay/error behavior and the spawned account backend.
- Native suite: 3 PASS; the full intake/scope case was repeated after the final
  explicit target-scope guard. Total: 73 distinct passing cases, zero unresolved
  failures. Initial test-runner/Windows test-path issues were repaired in the
  scratch harness and affected cases rerun; these were not product acceptance.
- Scratch/logs: `E:/Codex/Temp/MF-R1-04-COMPLETE`; short-path local asset fixture:
  `E:/Codex/Temp/4`. Old D evidence remains read-only in place. No global environment
  or Codex storage setting changed.

A must integrate/deploy and explicitly dispatch real intake execution. Real user
migration, VPS deployment, SMTP, schema mutation, build and Actions remain NOT_RUN.

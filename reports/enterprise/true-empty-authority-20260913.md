# Scoped true-empty server authority V1

Dispatch: `MF-MAIN3-04-TRUE-EMPTY-AUTHORITY-20260913-15`.
Base: `851ddaeef8d6dd9a2e9c7ab992b8bbcaf25bd6b4`; retained integration authority:
`04e2d61e3e39729886b3a2e6c7aff022b65a382a`. Work stays on `worker/main3/04`.

## Scope and consumer contract

`SUPPORTED_REHYDRATION_SCOPE_V1` is exactly **plans, progress, resource_metadata**
using the existing V1 DTO fields. It does not include review, full learner model,
learning runs, messages, history, settings or a user's unsent local data.
`global_account_empty` remains UNKNOWN. A future addition requires an explicit
new scope version and initialization/coverage contract; it cannot inherit V1's
empty assertion. No full-history sync, real-data migration or 03 UI is changed.

Snapshot `contract_version` is now 3; the 04 transport adapter accepts versions
2/3 and preserves server state. Consumers must use `learning_state.status`, never
list length, to choose empty presentation. Version 2 remains partial/unknown.

| State | Authority |
| --- | --- |
| TRUE_EMPTY_SUPPORTED_SCOPE | Committed initialized V1 manifest and zero live supported entities in the same observation |
| NONEMPTY | Committed initialized V1 manifest and at least one live supported entity |
| NOT_INITIALIZED | No matching initialized manifest; even existing workspace/profile/entity rows cannot imply initialized empty |
| PARTIAL_UNSUPPORTED | Requested entity/scope version or stored manifest scope is unsupported |
| QUERY_FAILED | PostgreSQL/membership query failure; HTTP 503 with no successful state or empty assertion |
| NOT_AUTHORIZED | Existing auth/membership rejection; HTTP 401/403, not empty data |

`GET /api/enterprise/sync/v1/snapshot` defaults to the complete V1 supported scope.
Optional `required_scope=plans,progress,resource_metadata` is a comma-separated
list; adding `review` yields PARTIAL_UNSUPPORTED. Optional `scope_version` must
match V1. The older `supported_entity_types` names remain plans/progress/resources;
`supported_scope` is the new exact product-scope vocabulary. The observation
contains both, avoiding a silent field-name reinterpretation.

`known_empty` is true only for TRUE_EMPTY_SUPPORTED_SCOPE and is explicitly bound
to `scope_version`, `supported_scope`, `snapshot_scope` and `snapshot_revision`.
It is not a GLOBAL_ACCOUNT_EMPTY flag. `supported_scope_empty` is null when
uninitialized/unsupported, so consumers cannot use it to erase that distinction.

## Persisted initialization and generation

New canonical Alembic revision `0038_rehydration_manifest` adds
`rehydration_manifests`; **0037 is unchanged**. Its primary key is canonical
issuer/subject/organization/workspace. A composite FK binds the workspace to its
organization. Stored fields are scope_version, initialized, generation, created_at
and updated_at. PostgreSQL owns timestamps. ENABLE + FORCE RLS applies to all
four scope dimensions; the non-owner application role has SELECT/INSERT/UPDATE,
not DELETE, and runtime startup validates columns, PK, FK, checks, ownership,
grants and both policy expressions.

There is deliberately no migration backfill. Workspace/profile existence or a
legacy sync request receipt is insufficient. The existing normal
`AccountWorkspaceBootstrap.initialize()` creates the manifest only after verified
membership and profile initialization, inside the same synchronous commit. A
failure rolls back workspace/profile/manifest changes together. No client body,
QSettings or Desktop initialization claim can create the authority.

Discovery remains read-only. A profile-ready workspace with no learning manifest
sets the existing `initialization_required=true` flag, while preserving the
established READY/PROFILE_REQUIRED vocabulary consumed by the normal Desktop
bootstrap adapter. The normal explicit initialize operation repairs the missing
learning initialization; it neither imports data nor proves unsupported history
empty. SQLite legacy tests do not establish this PostgreSQL authority.

Initialization and writes acquire the same account/workspace advisory transaction
lock, so first-use and a concurrent write have an explicit commit order.
Generation starts at 1. New successful entity writes/deletes advance it in the
same transaction as entity revision and durable idempotency receipt. Replayed
requests and repeated already-initialized bootstrap do not advance it. Generation
does not replace per-entity CAS or the MVCC marker. Pre-initialization writes remain
readable for compatibility but cannot create an initialized/empty assertion.
Unsupported future manifest versions are not overwritten by V1 initialization.

## Consistent observation and deletion

One PostgreSQL SELECT observes manifest, every supported entity including
tombstones, `pg_current_snapshot()` and server observation time. Counts, empty
state, inverse resource references and provenance derive only from that result.
There are no independently timed plan/progress/resource queries to combine.
PostgreSQL's single-statement committed snapshot supplies the stronger-than-needed
consistency for this one read; no extra transaction-isolation mode is required.

The opaque MVCC marker is scoped to that endpoint/database/account/workspace,
not an incremental cursor or global account version. A pending concurrent insert
may correctly leave the old snapshot empty. After commit the next snapshot is
NONEMPTY with a different marker; tests verify the writer transaction's visibility
using PostgreSQL `pg_visible_in_snapshot`, not a client timestamp or max revision.

Deleting all supported records retains explicit revisioned tombstones, advances
generation, and returns TRUE_EMPTY_SUPPORTED_SCOPE with nonzero tombstone count.
This is observable deletion, not temporary omission or missing resource bytes.
An over-bound snapshot still returns 413 and never fabricates empty state.

## Executable verification

`scripts/sync_candidate_native.py` now builds/runs the 0038 acceptance matrix from
an exact extracted Git archive, reusing its owned native PG and real Account Core
HTTP harness. No external database URL is accepted. It exercises fresh → 0038,
0037 → 0038, already-current read-only startup, future rejection and sync/manifest
drift. It runs the retained protocol/producer cases plus the dedicated
`tests/server_sync/test_empty_authority.py` matrix.

The real HTTP smoke performs bootstrap → empty, unsupported scope → partial,
write → nonempty and legal delete-all → empty, saving each actual runtime
observation. A separate client recovers the committed state. The signed test
bearer/JWKS issuer is disposable and loopback-only; no live Keycloak or native
credential-login PASS is inferred. An actual DB shutdown also must return
QUERY_FAILED. Processes and owned DB files are cleaned up afterward.

The focused fault test first verifies an empty live plan list, then deliberately
makes PostgreSQL fail while evaluating progress rows in the owned fixture. The
whole snapshot fails with QUERY_FAILED; the working plan result is never returned
as an empty success. Separate tests cover no/foreign authorization, manifest RLS,
initialization rollback, no-manifest workspaces and concurrent commit visibility.

Full legacy history coverage, product GUI/cache wiring, live deployment and
Workbench candidate identity remain outside this dispatch. Exact execution
results and artifact hashes are recorded in the adjacent receipt and runtime
observations, not hand-authored as test evidence.

## Recorded result

STATUS=PASS_TRUE_EMPTY_SERVER_AUTHORITY within the versioned supported scope.
Source SHA: `830b4b61b067a6775e605febc26c7e652d32a01d`; tree: `89aac4db2aba15aa4475a55b4d788fb64dcf5e9f`.
Executed archive SHA256: `18e2b8ca7baa332d9456b06e7266ec01894cab5fddc2300e35254a12a8b6855b`.
The real candidate ran 22 PostgreSQL cases without failure, plus fresh/0037-to-head,
read-only current, future rejection, 12 drift cases and actual DB-outage rejection.
Local checks: 50 backend tests and 4 contract tests passed; 18 optional/historical
native cases were skipped and one broad RLS case deselected in that local invocation.
Those are not counted as passes. Dedicated 0038 native gates were executed separately.
All owned candidate services and database files were removed; the recorded loopback
endpoint is historical runtime identity, not a deployed or currently running service.

The final shared-lock regression proves that initialization cannot overtake an
uncommitted sync write and later writes advance the initialized generation.

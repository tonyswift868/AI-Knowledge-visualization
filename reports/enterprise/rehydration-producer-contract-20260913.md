# Rehydration producer contract and disposable 0037 candidate

Dispatch: `MF-MAIN3-04-REHYDRATION-PRODUCER-CONTRACT-20260913-14`.
Exact integration consumed: `04e2d61e3e39729886b3a2e6c7aff022b65a382a`.
This report describes the producer. It does not authorize live deployment,
identity cutover, old-data import, or Desktop GUI/cache implementation.

## Public snapshot

`GET /api/enterprise/sync/v1/snapshot` retains the existing entity schema and adds
`contract_version: 2`. `complete: true` means **SUPPORTED_ENTITIES_ONLY**.
The Desktop adapter requires this contract version and fails closed on an older
snapshot lacking authoritative observation fields. A and B must use a compatible
server/client version; this is not silent compatibility with the former producer.

| Field | Authority / interpretation |
| --- | --- |
| snapshot_revision | `pg-snapshot:xmin:xmax:xip-list` from the same PostgreSQL SELECT that reads all data |
| snapshot_scope | Existing authenticated issuer, subject, organization, workspace; no new identity mapping |
| snapshot_authority | POSTGRESQL_SINGLE_STATEMENT_MVCC |
| revision_ordering | Opaque observation, not an incremental cursor or globally unique ID |
| supported_entity_types | plans, progress, resources (only the retained V1 DTO fields) |
| unsupported_entity_types | tasks_calendar, review, learner_model, learning_runs, messages, teaching_sessions, nonsecret_settings, model_metadata |
| learning_state | Full-account status and separate supported-scope initialization/empty facts |
| resources | One entry per live stable resource ID, explicit product classification, complete sorted plan_ids[] |
| progress_provenance | Per-progress revision plus SERVER_CONFIRMED, snapshot_revision, server_confirmed_at; trusted_cache=false |
| observation | Machine-readable counts, support scope, server observation marker/time and resource-availability boundary |

The entities, request-ledger existence, `pg_current_snapshot()` and server
`statement_timestamp()` are evaluated in **one SQL statement**, including empty
results. Counts, inverse references and provenance are derived only from that
materialized result, with no later database reads. The original 1000-entity bound
includes tombstones; over-limit is 413, not a truncated successful snapshot.

PostgreSQL documents the marker as transaction visibility information and
READ COMMITTED as a per-statement committed view. See the official
[snapshot function](https://www.postgresql.org/docs/17/functions-info.html) and
[isolation contract](https://www.postgresql.org/docs/17/transaction-iso.html).
Do not sort these strings, compare them across databases/endpoints/account scopes,
use them as retained snapshot handles, or treat an unchanged marker as a general
cache freshness guarantee. Timestamp is the server observation time, not the
time of the last entity commit. Consumers still own request-generation/logout
invalidation; the marker does not replace those controls.

## Empty, incomplete and failed results

| Result | Meaning |
| --- | --- |
| TRUE_EMPTY | Reserved for an authoritative full-account initialization/known-empty fact; **not emitted by current V1** |
| PARTIAL_UNSUPPORTED | The query succeeded, but unsupported or local history prevents a full-account empty/completeness claim |
| QUERY_FAILED | HTTP 503 on DB failure; no entities, counts or empty-account assertion returned |
| NOT_AUTHORIZED | HTTP 401/403 from the existing auth/membership boundary; not a zero-count snapshot |

`learning_state.account_initialized` is UNKNOWN and `known_empty` is false.
`supported_scope_initialized` means a committed request receipt exists in exactly
this account/workspace; it survives deleting the last entity. `supported_scope_empty`
means there are no live **supported** entities in that same observation. Neither
proves a full account was newly created, or that local/unsupported history is absent.
Unsupported counts are null, never invented zeros. Existing workspace bootstrap
is membership/profile authority, not proof that the complete learning account is
empty. No identity-owned initialization marker or account table is invented here.

The true-empty producer gap is therefore contained safely but not fully implemented.
Adding an actual full-account initialization/coverage authority requires a separately
agreed persisted lifecycle contract; accepting client testimony would be unsafe.

## Resources and provenance

Resource writes optionally declare `product_classification` as USER_FILE, MODEL,
OTHER or UNKNOWN. This is authenticated, committed resource metadata, not MIME or
extension inference. It is a product label, not evidence of model licensing,
verified re-downloadability or possession of bytes. Legacy absent labels project
UNKNOWN with `UNCLASSIFIED_LEGACY_METADATA`; explicit declarations project
`AUTHENTICATED_RESOURCE_METADATA`. Omitting the new field preserves the old request
fingerprint, so existing idempotency receipts remain replayable.

One shared stable ID appears once with all live referencing plan IDs. Deleting a
plan removes it from the inverse live-reference projection; entity tombstones stay
explicit. No resource IDs are duplicated or rewritten for presentation.

The server returns NOT_OBSERVED_BY_SERVER for device file availability. The existing
Desktop client adapter may apply its injected device-local resolver and emits
MISSING_ON_THIS_DEVICE plus an observation `missing_resources` list. This does not
fabricate a trusted cache. It is separate from SERVER_CONFIRMED structured progress.
The observation JSON is generated from the runtime result, never typed as evidence.

## Candidate reproduction and acceptance boundary

Build a Git archive of the exact source commit into an owned E: scratch directory,
record SHA/tree and archive SHA256 in a manifest, and extract it into a fresh directory.
Run `scripts/sync_candidate_native.py` **from that extraction** with `--pg-bin`,
`--scratch`, and `--source-manifest`. The manifest contains `source_sha`, `source_tree`,
`artifact`, and `artifact_sha256`; the runner verifies every archive file against
the runtime source before importing the candidate application.

The runner accepts no external DB URL. It owns a fresh loopback-only native PG
cluster, nonprivileged migrator/application roles and disposable databases.
It runs fresh → 0037, 0036 → 0037, already-current read-only admission, future
rejection and sync schema drift probes. The immutable 0037 migration is unchanged;
new contract metadata uses its existing typed JSON payloads and request ledger.

The exact archive's unmodified `app.account_core_mvp:app` runs under a real Uvicorn
process on an owned loopback endpoint. An ephemeral RSA/JWKS test issuer supplies
signed test bearers: the production signature/claims validation, DB membership and
workspace bootstrap execute. This is **not** a real Keycloak password-login test.
No signing key, bearer or database password is printed or committed.

Device A writes a shared MODEL resource, two plans and progress through real HTTP.
A closes; a separate HTTP client B rehydrates. The runner saves its actual
observation and checks provenance, references, classification and missing-resource
projection. Finally an actual owned DB shutdown must yield QUERY_FAILED, not an
empty success. Backend, JWKS server and PG cluster are cleaned up. The endpoint
identity in the receipt is historical execution evidence, not a running staging URL.

The artifact is an executed source-archive candidate with an external Python
runtime, **not** a container image, self-contained installer or registered CURRENT
product target. Record its digest and runtime identity separately. Detailed results
and remaining gates are in the adjacent machine-readable receipt.

## Focused regression scope

The retained full 0036 native runner executed 18 cases: 17 passed, including
fresh/0035/0033 upgrades, read-only current startup, future/ledger drift rejection
and workspace continuity. One worker-schema case could not import
`procrastinate` in the available Account Core test runtime; it is not a PASS.
The dedicated candidate runner separately executes the required fresh/0036/0037
matrix and new sync drift tests; its results are recorded independently.

Database connection timeouts remain deployment configuration. The disposable
candidate uses a three-second PostgreSQL connect_timeout so an actual outage is
bounded. The sync API maps DB failures in both membership lookup and state reads
to QUERY_FAILED without serializing SQL or connection details.

## Final executed result

The accepted disposable candidate source is
`7c697d0399ae0126a2bd62b7512aad3e0498558b`, tree
`c6ff1eccc471133e2b274a77fdb283868c902637`. Executed archive SHA256:
`e9dcd07e986b6d8c2ed0c645b478df3db1e8eacb30767aa9db2f337d27ec5271`.
All 13 PG protocol cases and every dedicated candidate gate passed, including
real HTTP bootstrap/writes, independent B recovery, and actual DB-outage rejection.
The first two attempts passed normal recovery but timed out during the extra
outage check; the final source fixes the pre-router membership error boundary.
Four contract tests and 39 Account Core/tenancy tests also passed.

The compact generator passed (1119 bytes). Knowledge lint and web handoff check
refused this worker checkout with the exact reason `wrong integration checkout`.
Their A-owned integration guard was retained, and no global integration handoff
was republished from worker source. This remains an explicit unrun integration
publication gate.

STATUS=PARTIAL: the producer is safely consumable and the 0037 candidate ran,
but no durable full-account TRUE_EMPTY authority exists. Unsupported history
remains explicitly unknown; no product-wide completeness claim is made.

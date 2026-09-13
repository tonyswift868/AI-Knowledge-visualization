# Structured sync V1: source/API vertical slice

Authority: MF-MAIN3-04-SERVER-SYNC-REHYDRATION-MVP-20260913-13.
Consumed exact integration: `1ef95a71c5e7422cb3fe1435ccd703193629b591`.
This delivery does not upload legacy data, deploy to the resident service, alter
identity namespaces, or select a durable desktop root. Migration 0037 is required
before deploying the new Account Core build; an unmigrated server fails closed.

## Coverage matrix

`SERVER_CANONICAL_ALREADY` below means the **explicit V1 DTO subset** has a committed
write/read protocol in this delivery. It does not mean the full legacy ORM entity
or the installed Desktop screens are synchronized. All paths are authenticated;
the Account Core middleware supplies canonical Actor/Tenant, never a body identity.

| Entity / supported scope | Status | Write path | Read / rehydrate path | Stable ID | Revision | Delete | Idempotency | Conflict |
|---|---|---|---|---|---|---|---|---|
| Plan V1: title + resource references | SERVER_CANONICAL_ALREADY (new slice) | PUT sync/v1/entities/plan/{id} | GET sync/v1/snapshot; StructuredSyncClient.plans projection | Caller-retained scoped ID | integer CAS | revisioned tombstone | durable per-scope request receipt | 409; refresh snapshot |
| Full ORM/active plan, tasks/calendar, goals and schedules | PARTIAL | Existing local creation / enterprise generic learning_plans repository | No full legacy-to-sync adapter | Several model namespaces | No unified CAS | Legacy/generic deletes differ | No full protocol | GAP |
| Progress V1: plan_id, mastery 0..1, completed_tasks | SERVER_CANONICAL_ALREADY (new slice) | PUT sync/v1/entities/progress/{id} | snapshot; client.progress | caller-retained scoped ID | integer CAS | tombstone | receipt | 409 |
| Full knowledge progress, mastery evidence, hints/minutes | LOCAL_ONLY for full native history | active/ORM SQLite learning paths | No full account restore adapter | local stable IDs | heterogeneous | no common tombstones | GAP | GAP |
| Review state | PARTIAL | Enterprise review_campaigns persistence; local active_review_items / orm_reviews | Generic server records, no complete review-history rehydrate | per-model IDs | no unified CAS | inconsistent | GAP | GAP |
| Learner / student model | LOCAL_ONLY for learner profile | active_learner_profiles / local progress-derived state | Not in sync V1 snapshot | local profile ID | no server CAS | GAP | GAP | GAP |
| Learning runs | PARTIAL | enterprise/jobs_v1 run/job seams, local orm_learning_runs | Full runtime run API exists, not Account Core sync snapshot | job/run IDs | job-state semantics | no sync tombstone | job semantics only | not V1 user-state CAS |
| Supported messages / teaching history | PARTIAL | enterprise_tenancy/teaching.py session persistence; native messages remain local | No all-session/message restore in Account Core | session/message IDs | no unified revision | no complete propagation | GAP | GAP |
| Nonsecret settings | PARTIAL | enterprise workspace_profiles / goals; local presets | Existing workspace read, no complete whitelist sync | workspace ID | no V1 setting revision | GAP | GAP | GAP |
| Resource metadata V1: filename, MIME, size, SHA256 | SERVER_CANONICAL_ALREADY (new slice) | PUT sync/v1/entities/resource/{id} | snapshot; client.resources | provided stable ID preserved | integer CAS | tombstone | receipt | 409 |
| Existing asset manifests / revisions | PARTIAL | existing asset_manifests registration service | manifest export incl. tombstones | existing asset/local IDs | existing immutable asset revisions | existing tombstone | not unified request/CAS contract | full adapter GAP |
| Model metadata, verified downloads/licenses/rebuild inputs | MISSING as a dedicated sync DTO | not admitted by V1 strict payload whitelist | not restored by V1 | GAP | GAP | GAP | GAP | GAP |
| Stable resource IDs / plan linkage in V1 | SERVER_CANONICAL_ALREADY (new slice) | references validated in same account/workspace transaction | same IDs in snapshot even without bytes | same supplied ID on A and B | resource revision | deletion explicit | reference write shares transaction | foreign-scope references rejected |

All sync paths above have prefix `/api/enterprise/`. No server success is inferred
from the presence of a table. Existing asset registration has useful metadata and
tombstone behavior, but lacks the new request/CAS contract, so it is not presented
as fully synchronized. V1 does not automatically mirror old asset tables or old
plan/progress tables; callers must explicitly use this protocol and retain IDs.

## Supported history matrix

| Record type | Server-restorable through new slice | Remaining gap |
|---|---|---|
| learning_runs | NO | run state/history adapter and full restoration |
| orm_learning_run_messages | NO | message retention, revision/delete protocol |
| active_teaching_sessions | NO | native session adapter |
| active_teaching_messages / orm_messages | NO | ordered message history, retention and deletion |
| enterprise teacher session metadata | Existing partial service only | not advertised by sync snapshot |
| active_review_items / orm_reviews / attempts | NO | review-state adapter and conflict model |
| active_learner_profiles | NO | profile schema and merge policy |
| V1 current plan/progress/resource state + tombstones | YES | no historical revision browsing; receipts exist only for replay |

## Protocol and durability

The two new tables are `structured_sync_entities` and `structured_sync_requests`.
Their primary keys include issuer, subject, organization and workspace. Existing
`PostgresTenantTransactionManager` binds canonical Actor/Tenant; the issuer GUC
follows the existing asset metadata pattern. Both tables have ENABLE + FORCE RLS,
USING + WITH CHECK for all four scope fields, and a non-owner application role.
No identity hashing/alias/root-selection implementation is added.

A write has `request_id`, `expected_revision`, and a strict typed `payload`.
Creation requires expected_revision=0. The service takes a transaction-scoped
PostgreSQL advisory lock for the account/workspace, checks replay, checks CAS and
same-scope live references, writes the entity and exact response receipt, and
exits the transaction before returning success. `synchronous_commit=on` is set
locally; a commit error cannot return HTTP 200. The lock also serializes distinct
requests racing to create the same stable ID and reference/delete races.

An identical request key and canonical request returns the original committed
result. Reusing the key with another body/ID/revision returns 409. A timeout after
commit can retry the same request unchanged. Replay is a historical write result,
not an assertion that its revision remains current: refresh the snapshot to get
current state. No last-write-wins behavior or automatic conflict overwrite exists.

`POST .../entities/{kind}/{id}/delete` requires a revision and request_id, increments
the revision and stores an explicit tombstone with an empty current payload.
Resurrection of that stable ID is refused, including a write against its latest
tombstone revision. Receipts and tombstones are retained in V1; this is not a
physical-erasure API. Existing progress may remain as supported current state
when its parent plan is tombstoned; it does not recreate the plan.

Snapshot is one server SELECT (including tombstones) and a complete-state response.
V1 accepts at most 1000 scoped entities per snapshot; above that it returns 413,
never silently truncates. Pagination/retention beyond this bound is a future task.
Client rehydration rebuilds lists from the complete snapshot, not by unioning an old
cache. It does not wait for Device A or for any binary import. Resource availability
is computed on Device B; default is `MISSING_ON_THIS_DEVICE`, while plans and
progress remain visible in the returned client projection.

## Acceptance boundary

Focused tests use an independently created disposable PostgreSQL container and a
non-superuser NOBYPASSRLS application role. They run the actual 0037 migration,
actual SQL transactions, HTTP router, canonical `enterprise_request_context`, and
the actual client adapter. Test sessions/membership directory are fixtures, not a
real Keycloak credential login. Device A transport and server engine are closed;
Device B creates a fresh transport/server engine and recovers committed state.

This is an executable protocol/client-adapter slice, not final installed-app UAT.
Automatic login → GUI plan/progress store wiring and the full native learning
writers are not added here; no packaged two-device login or real-user migration
PASS is claimed. The current API supports only the explicitly listed V1 fields.
The full SERVER_SYNC_TIER remains PARTIAL. See the dispatch receipt for exact test
counts and final status; no real 88-plan/83-progress dataset is accessed.

## Reproduction and recorded checks

Use a fresh, empty, explicitly disposable PostgreSQL database for every suite.
Install the repository backend test dependencies and expose its admin and
non-superuser application URLs only to the test process. Never use a resident
service database. The suite creates two tables and, if absent, the test application
role; it deliberately refuses an existing sync table. It does not drop data.

```powershell
# MF_SYNC_TEST_ADMIN_URL / MF_SYNC_TEST_APP_URL: owned empty fixture only.
python -B -m unittest discover -s tests/server_sync -p test_vertical_slice.py -v
python -B -m unittest discover -s tests/server_sync -p test_contract.py -v
# For backend checks set PYTHONPATH=backend and all MentorFlow data roots to scratch.
python -B -m pytest --noconftest -q backend/tests/test_account_core_mvp_runtime.py
python -B -m pytest --noconftest -q backend/tests/test_enterprise_tenant_persistence.py -m 'not rls'
python -B -m pytest --noconftest -q backend/tests/test_account_import_ledger_schema.py
```

Recorded disposable PostgreSQL results: 7 initial tests passed (422.542s),
2 follow-up cases passed (133.937s), and 2 final cases passed (86.641s):
**9 unique cases, 11 executions**. Follow-ups add an actual lost-response exception
and concurrent different-request CAS race, expanded resource cross-workspace
rejection, and the bounded snapshot rejection. Final cases rechecked independent
Device B recovery after client cleanup changes. No PostgreSQL server power-loss
or native password-login test is claimed; durability evidence is committed real
PostgreSQL state, synchronous commit, and injected transaction failure rollback.

Additional results: 4 contract tests, 27 tenant persistence tests, 11 Account Core
runtime tests, and 1 retained migration source/linear-graph test passed.
The 17 specialized 0036 native-runner cases were skipped in this invocation,
not counted as passes; historical 0036 source hash remains unchanged.
Existing deprecation/pytest-marker warnings do not affect these results.

Delivery status is PARTIAL for the requested product outcome. PLAN, PROGRESS and
RESOURCE_METADATA V1 protocol tests pass, but installation/login to automatic GUI
rehydration and full native entity/history adapters remain open. Deployment and
0037 application are separate from this source delivery. The migration is forward
only; do not roll back the server build without a compatible schema plan.

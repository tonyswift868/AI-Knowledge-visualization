# Account import ledger 0036 source recovery

Dispatch: `MF-MAIN3-04-SCHEMA-0036-RECOVERY-20260913-07`

Task: `MENTORFLOW_ACCOUNT_IMPORT_LEDGER_0036_SOURCE_RECOVERY_V1`

Status: **PASS_0036_SCHEMA_SOURCE_RECOVERED**. This is a source delivery. It does
not authorize or perform a deployment, staging migration, capability enablement,
issuer cutover, or Gate A continuation.

## Office and root cause

The existing isolated worktree `E:/Codex/Data/worktrees/b18c/MentorFlow` was reused
on `worker/main3/04`, initially clean at
`f9634e24d759eb268c56d45b301046dc503ef714`.
Its Git directory/index are under `.git/worktrees/MentorFlow4`; the shared Git
directory is `E:/Codex/Projects/MentorFlow/.git`. No new worktree was created.

The exact read-only integration reference was
`102f734890d76bc7c079a75a14958299c2882269`. Its migration files were exported
from Git objects into a scratch directory and loaded using Alembic
`ScriptDirectory`: the only head was `0035_account_workspace_bootstrap`, 0035
existed, and 0036 was absent. Its backend expected-head constant also named 0035.
Thus the failure combined missing migration source with the old runtime contract
(root-cause category D), rather than an unregistered existing 0036 file.

## Authoritative recovery and read-only comparison

The retained staging release supplied the original source:

`/opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/backend/alembic/versions/0036_account_import_ledger.py`

The running backend independently supplied identical bytes at
`/app/backend/alembic/versions/0036_account_import_ledger.py`.
The container's image revision label was
`21337c1eda64f7e087fcade77c3d4cd7dce4a0af`; its image identity was
`sha256:bda13c3a476ee2cccd5b2da0d6e332e425b0fa60e292dcceda778c983eb0384c`.
The release Git object was unavailable locally; no replacement migration was
inferred from database structure.

Both authoritative files and the recovered LF source are 2,789 bytes, SHA-256:

`45ff4a9483a072fb6299b0ae4c7e2634e5c616db72a6a761fa14058aafe6a5ac`

The recovered revision is `0036_account_import_ledger`, with down revision
`0035_account_workspace_bootstrap`. Its upgrade and forward-only downgrade are
unchanged. Staging reported the same 0036 current revision and running script
head. Metadata was collected using the non-owner application role with
`default_transaction_read_only=on` and a five-second statement timeout. Only
revision and schema catalogs were queried; no account/import records were read.

The sanitized [metadata snapshot](../backend/tests/fixtures/account_import_ledger_0036_metadata.json)
records columns, validated constraints, indexes, ownership, policies, and ACLs.
A fresh native PostgreSQL database built from the recovered migration matched
every recorded category exactly. **STAGING_SCHEMA_MATCH = PASS**.

## Ledger contract

`public.account_import_ledger` has 14 non-null columns, including scoped identity,
request/source digests, JSONB snapshot/summary, state, and timestamp defaults.

| Contract | Recovered behavior |
| --- | --- |
| Primary key | `import_id` |
| Request uniqueness | `(issuer, subject_id, workspace_id, idempotency_key)` |
| Snapshot uniqueness | `(issuer, subject_id, workspace_id, source_digest)` |
| Foreign key | `(organization_id, workspace_id)` → `workspaces(organization_id, id)` |
| Indexes | Three unique btree indexes supporting the primary/unique constraints |
| State constraint | `PREVIEWED`, `PLANNED`, `COMPLETED` |
| Ownership | `mentorflow_migrator` |
| Application role | `mentorflow_app`, neither superuser nor BYPASSRLS |
| Application grants | SELECT, INSERT, UPDATE; no DELETE or other elevated table grants |
| Tenant isolation | ENABLE and FORCE RLS; issuer, subject, organization and workspace must all match transaction context |

The two unique keys prevent duplicate durable intent within their recorded
scope, while allowing equal keys in separate tenant workspaces. The state CHECK
limits values; it does not enforce transition order. Transaction rollback
preserves the previous state when completion fails. Existing provenance is
protected by the application role's lack of DELETE and the migration's explicit
refusal to downgrade. This delivery does not invent import business logic.

## Runtime changes

0036 is now the sole canonical repository head and is included in the required
FORCE-RLS table set. Backend startup and worker verification require that exact
known head plus the ledger's actual columns, constraints, indexes, RLS, owner,
and privileges. An unknown future revision or a correct revision with missing
or weakened schema fails closed. The existing deployment preflight consumes
the updated canonical constant and backend verification.

Alembic grants version-table visibility only when a migration changes the
revision. `current --check-heads` and an already-current `upgrade head` therefore
perform no repeated GRANT. Native tests enforce read-only transactions for both
migration and application roles during already-0036 verification/startup.

Historical reversible migration tests explicitly target 0035. New tests cover
0036 forward migration and its refusal to downgrade, preserving both contracts.

## Verification and reproduction

The [native receipt](E109_ACCOUNT_IMPORT_LEDGER_0036_NATIVE_RECEIPT.json) records
**18 passed**, PostgreSQL **17.6**, source hashes, and successful owned-cluster
cleanup. The cases cover fresh/0035/0033 → 0036; already-0036 read-only startup;
unknown 0037 rejection; ten schema/permission drift variants; worker schema and
startup; A/B isolation in all four scope dimensions; default LEARNER; workspace
bootstrap/discovery continuity; uniqueness errors; rollback; and no downgrade.

Two affected offline regression groups each passed 64 tests. Total:
**146 passed, 2 skipped**. The skipped older tests require their own externally
provisioned test DSNs; they are not counted as native passes. Alembic `heads`,
`history`, linear-graph validation, and `git diff --check` passed. Test setup
initially needed fixture corrections and the already-locked Procrastinate 3.9.0
dependency; final runs above passed without changing dependency lock files.

From the repository root, using Python with the repository backend test
dependencies (including psycopg and Procrastinate 3.9.0):

```powershell
python -B scripts/schema_0036_native.py --pg-bin E:/path/to/postgresql-17.6/bin --scratch E:/Codex/Temp/schema-0036-recheck
```

The runner accepts no database URL. It creates a new random loopback-only
cluster, separate migration/application roles, and per-case databases; uses
bounded PostgreSQL statement timeouts; captures private logs; emits status-only
receipts; verifies ownership and closed ports before removing its fixture.
It starts PostgreSQL only. Running the test module without this runner skips
the native cases, rather than claiming database acceptance.

Offline affected groups:

```powershell
python -m pytest backend/tests/test_enterprise_migration_head_contract.py backend/tests/test_account_workspace_bootstrap.py backend/tests/test_enterprise_account_core_persistence.py backend/tests/test_enterprise_tenant_persistence.py backend/tests/test_account_core_mvp_runtime.py -q
python -m pytest backend/tests/test_enterprise_worker_runtime.py backend/tests/test_enterprise_worker_factory.py backend/tests/test_enterprise_account_core_api.py backend/tests/test_enterprise_identity_runtime.py -q
```

Staging DB mutations, backend deployments, Keycloak mutations, SMTP sends,
production-data access, remote pushes, and GitHub Actions executions were all
zero. `release/account-capabilities.json` was unchanged. The integration ref,
issuer migration files, and live configuration were unchanged. A newly
integrated source remains required before the separately owned Gate A resumes.

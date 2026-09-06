# E107 source/launcher preparation — partial return

Dispatch: MF-E107-OWNER0-LAUNCH-ISOLATION-20260906-01
Task: MENTORFLOW_DESKTOP_LAUNCH_AND_OWNER_DATA_PREPARATION_V1
Base: 1531c65116063ec606ed1c623acd29fb68e9e8b7

This is NOT owner0 migration authorization, not a complete online isolation
qualification, and not a new packaged build. No server mutation or formal
database write was performed. No 06 identity was inferred or polled.

## Launch changes and evidence

`scripts/enterprise/install_personal_test_shortcut.ps1` installs the desktop
MentorFlow.lnk, preserving an unrelated existing shortcut. Target is Windows
PowerShell with NoProfile, hidden window and the final release launcher; the
working directory is the outer release and the icon is the final MentorFlow.exe.

`scripts/enterprise/start_personal_test_package.ps1` records the exact personal
test launcher shipped externally as Start-MentorFlow.ps1. Its manifest/verifier
hash anchors are pinned to the existing 20260906 release. It is NOT a general
distribution template: the protected SSH key reference stays outside the release.
No private key content, password, token, tenant or workspace hint is embedded.

After validating all trust anchors, trusted Windows SSH/curl connection preparation
overlaps the complete immutable payload check. The application starts only after
the checker succeeds. Fixed issuer, CA, host-key checks, environment allowlist,
runtime-key validation and isolated data roots remain. ValidateOnly opens no tunnel.
The launcher records nonsecret phase times in its isolated runtime-temp directory.

Actual desktop-shortcut runs (milliseconds; end-to-end from Shell launch):

| Run | Full check duration | Connection duration | Qt window | Real form interactive |
| --- | ---: | ---: | ---: | ---: |
| Initial, before | 164188.74 | 6478.09 | 175707.43 | NOT REACHED within 180000 |
| Before, repeat | 35457.56 | 6686.07 | 44612.72 | 51414.98 |
| After, first | 34497.59 | 5954.35, overlapping | 38016.87 | 44546.66 |
| After, repeat | 33222.05 | 6727.84, overlapping | 35471.17 | 42500.67 |
| Final close check, concurrent regression load | 65877.21 | 6725.59, overlapping | 73134.52 | 80178.53 |

UIAutomation checked the real enabled, visible username/password/submit controls,
not merely a Qt shell. Final close check recorded application exit code 0 and no
remaining owned application/tunnel process. These are new-process observations;
OS disk cache was NOT flushed. Strict cache-cold time remains unqualified. The
initial timeout and loaded 80.2-second run are retained, not discarded as outliers.
The ~6–9 second improvement in the comparable repeat runs is not a startup SLA.

The source verifier also removes one redundant per-entry path resolution used
solely to compare the manifest filename. The existing security iterator still
checks resolved containment, reparse points, links, special files and privacy;
size/hash/inventory checks are unchanged. This source optimization is NOT in the
frozen native checker until a later coordinated rebuild. No performance claim is
made for an unrebuilt checker.

Final package/config source regression: 73 PASS, 4 SKIP (Windows symlink creation
privilege unavailable), no failures/errors. Modules: test_windows_package_privacy,
test_account_core_package_launch, test_personal_test_launcher. Includes same-size
EXE/DLL/checker tampering, external-config allowlist/invalid values, environment
sanitization and overlap ordering. Skips are not security PASS claims. The added
comparison test checks the personal launcher's config/environment functions are
identical to the PowerShell-exercised template. Initial test-harness teardown and
comparison-slice mistakes were corrected and the complete suite rerun successfully.

Existing executable remains:
`E:\Downloads\MentorFlow-Login-Final-20260906\MentorFlow\MentorFlow.exe`

SHA-256: 4235AB853C4EB45F848AD92F8F87943C064B0B3BE280EA2556700C6E9D98C496

## Isolation findings — full gate remains blocked

New synthetic test uses the real AccountVaultManager, LocalAssetLifecycle,
AccountMetadataSyncService, QSettings INI and MainWindow plan rendering method.
Only HTTP metadata and credentials are fixtures. A and B have distinct nonempty
plans, progress values, archived states, resource IDs/hashes and local files.
A→B→A and remount preserve each binding; old Vault handles close; wrong-account
metadata is rejected; current-account progress writes go only to that API fixture;
no Personal Local folder is created. Archived filtering and displayed progress
counts are asserted with real Qt list widgets.

**Acceptance blocker:** MainWindow constructs global QSettings("MentorFlow",
"MentorFlow") and reads/writes `analogy/recent_cards` without account scoping.
Changing AccountPreferenceStore from synthetic A to B leaves A's recent-history
content visible to `_load_recent_analogy_cards`. This is recent content memory,
not proof of a separately persisted recent-plan-open feature (none was located).
The isolated INI reproduction is a strict expected failure and explicitly does
NOT count as PASS. The affected acceptance chain was stopped. No UI clearing was
used to disguise the leak. Proper account-bound history storage needs repair and
subsequent integrated requalification before owner0 use.

The focused run was 43 PASS / 1 known XFAIL across the new test plus
test_login_smooth, test_account_switching, test_account_metadata_sync. Existing
synthetic login tests cover remember/rotation, automatic restore, logout, stale
login/bootstrap/service signals and drain ordering. Those are separate fixture
tests, NOT an end-to-end nonempty online A/B lifecycle or real API-process drain
test in this dispatch. A further nonempty bootstrap callback test uses the real
AccountBootstrapResult.close and mounted A/B Vaults: rejecting stale A closes
only A's handles/API object, preserves B's plan/progress snapshot and mounted
Vault, does not bind A's paths and creates no Personal Local fallback. The API
object is still a fixture, not an OS server process. Do not aggregate these checks
into full live qualification.

## Current formal source — read only

Authoritative storage migration record: E:\MentorFlowData\storage-migration-from-localappdata.json.
It records the 20260814 migration from LocalAppData to E:\MentorFlowData.
The legacy original-data-copy launcher independently names this same formal root.
No original-plan-copy, package UAT root or empty personal-local sub-Vault was
treated as the formal source.

Formal ORM: E:\MentorFlowData\data\mentorflow_v2.db
Revision: 0034_enterprise_asset_manifests (not the historical 0032).
SHA-256: 41e2685986aa74646cb81b3f0bcafc9bbe4c25a4c9f0fc0019aa55aa2eabbdcc

Formal legacy: E:\MentorFlowData\data\app.db
SHA-256: 6dbf8a3c1d77712c06a2b3e2b0ae904c8d27a57961e3d119b133e6f555278d3f

Inspection used SQLite mode=ro&immutable=1 only after verifying no WAL exists,
query_only=ON, no application import/migration, and before/after SHA-256 checks.
Both databases quick_check OK, foreign_key_check 0 violations.

The actual UI workspace union (materials/receipts/plans/current knowledge maps)
contains 56 rows: 1 active and 55 archived. active_plans has 57 version rows;
orm_plans has 88 rows. These are different units, not a count contradiction.
Plan IDs, workspace archive flags, resource IDs/paths and row digests are in a
private reconciliation ledger outside the repository and distribution package.

active_plan_tasks: 11363 (173 completed, 2 in_progress, 10761 planned, 427
unscheduled); no task-to-active-plan orphan. Knowledge progress snapshots: 3654;
orm_progress: 83; teaching sessions/messages: 51/282; materials: 73 ready records.
Stored local resource paths: 58 currently exist, 15 currently missing. Existence
was checked without opening large resource bytes; content-hash verification and
relink recovery remain pending. SQL status=ready is NOT proof of file availability.
Empty Enterprise tables are not a server import of this history.

21 existing backup database files under E:\MentorFlowData\backups were hashed and
quick_check OK. Latest named backup is 20260819-0130-pre-planning-cache-0025;
its ORM actually reports revision 0024_knowledge_map_job_progress, SHA-256
9d981426da156fad602df4ec59d2cb1c7c8f3df7b7675c170c43a9597b2b70b2.
Historical E:\Downloads\MentorFlow_Plan_Recovery_20260901T113847Z is absent on this
host. No backup was deleted, restored or rewritten. Existing old DB backups do
not prove complete current-0034 recovery or recoverability of all resource bytes.

## Supported seams and exact missing contracts

1. `backend/app/enterprise_tenancy/import_seam.py` provides
   PersonalLocalImportSource and LocalToEnterpriseImportService preview→plan→confirm.
   It uses explicit destination TenantContext, confirmation token and transaction
   actor ownership. No concrete formal Plan/Progress/Resource adapter or caller
   was found outside tests. Preview/plan reservations are in memory; payloads are
   not frozen by the descriptor digest. Confirm performs deterministic resource
   ID upserts, not fail-on-conflict target reconciliation. It lacks a persistent
   once-only owner0 issuer+subject+workspace/source-provenance ledger.
2. `desktop/account_portability.py::import_account_package` verifies checksums,
   identity fingerprint and a new/empty target, stages and publishes atomically,
   recognizes an identical manifest receipt as duplicate, and refuses a different
   nonempty target. It is an existing same-account portable package mechanism,
   not an owner0 account-core metadata transformation/import. Its identity
   fingerprint covers issuer/subject but not workspace; the legacy fallback can
   use local username/id. That fallback must NOT authorize owner0 migration.
   Serialized DB copying and local portability snapshots alone cannot establish
   server-canonical Plan/Progress/Resource relationships or cross-target once-only
   import. Do not relabel a Personal Local archive with an arbitrary account.

## Preparation sequence, NOT executed

After verified 06 issuer+sub+organization/workspace and explicit final authorization:

1. Bind the source provenance and stable IDs to the verified owner0 identity using
   a durable reservation/import ledger; reject any other account or workspace.
   No username, first-login, empty-root or fixed workspace inference.
2. Produce a consistency backup with SQLite Backup API into a new private target,
   preserving original data. Capture revision/hash/relationship baseline and
   resolve the 15 missing local paths without uploading raw bytes or Windows paths.
3. Implement/reuse the missing formal-data adapter through the supported seam,
   with a defined Plan/Progress/Resource relation mapping and persistent checkpoint.
   Preflight ID collisions and reject rather than upsert-overwriting target data.
4. Prepare an independent target; reconcile IDs/counts/archive/progress/resource
   relationships, device bindings and hashes. Publish/switch only after explicit
   success. Same-source/same-owner replay returns the prior receipt; different
   owner/target must fail. A new account gets no inherited historical data.
5. Complete history isolation fix, live lifecycle tests and one coordinated build
   before actual owner0 import. No new shared contract or business SQL was written
   in this preparation dispatch.

Private evidence root: E:\Codex\Data\MentorFlow-Owner0-Preparation-20260906
Timing harness/results: E:\Codex\Temp\E107-Owner0-Preparation-20260906

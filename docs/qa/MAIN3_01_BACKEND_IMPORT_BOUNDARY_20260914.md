# Canonical Desktop backend import boundary

Task: ELIMINATE_FROZEN_BACKEND_IMPORT_PATH_SURGERY, worker/main3/01.
Start: d06f7f67e7fde6a40294b3ccd1c79f8a69160183 (clean).
Read-only candidate: bcc9f2509673daccfa39ad67c5771a50d1cdfb61.
No integration, candidate, acceptance-runtime or real-user-data writes.

## Root cause and chosen boundary

Prepending `_MEIPASS/backend` lets PyInstaller resolve the top-level `app`
name through the partial `backend.app` archive namespace. The previous login
failure and the Account Backend `app.main` failure are the same import-identity
problem, not missing server source. Loading `backend.app.main` instead would
also duplicate the backend's absolute `app.*` contracts and module-global state.

`desktop.backend_imports` is now the only Desktop backend loading boundary.
Source creates the canonical `app` package from the checked-in `backend/app`
file spec, using normal importlib package creation and registration. It does
not edit an existing package's `__path__`, global `sys.path`, or `PYTHONPATH`.
Frozen imports the same `app` name using the normal PyInstaller importer.
The boundary checks file origins, namespace locations, and the PYZ key where
available. Conflicting already-loaded modules fail closed; no module is cleared
or renamed. The empty package initializer does not open databases. `load_app()`
remains after storage binding in both local service launch routes.

This is smaller than introducing a new process protocol: account APIs already
use spawn, and source/frozen can safely use the same canonical package name
without changing their process/lifecycle model. Normal server `app.main:app`
startup is untouched. `_MEIPASS` resources and PyInstaller hooks remain intact.

## Complete startup-path audit (baseline line numbers)

Search: `sys.path|PYTHONPATH|__path__|backend_root` over all Desktop Python/C#,
root Desktop entries, spec, launcher/bootstrap/build scripts; broader scripts
search used to distinguish product startup from standalone tooling.

| Hit | Classification / disposition |
| --- | --- |
| desktop/account_backend.py:32–36 | Runtime backend prepend; replaced with load_app |
| desktop/runtime.py:323–326 | Runtime backend prepend; replaced with load_app |
| desktop/enterprise_oidc.py:47–55, callers :60/:387 | Auth contract backend prepend; both callers use shared preparation |
| MentorFlow.spec:30 | Build interpreter backend prepend; removed, canonical hidden modules enumerated from source filenames |
| scripts/bootstrap.ps1:12 | Local installation DB initialization prepend; uses shared preparation |
| backend/app/orm.py:266/:273–282 | Transitive local startup Alembic prepend, explicit sys.path insert/remove; removed. Existing canonical app.orm is required and Alembic prepend is disabled |
| backend/app/orm.py:247–258 | Frozen backend.app aliases into app/app.orm/app.expedition_orm; removed, no duplicate engine namespace repair |
| desktop/main.py:46–47 | Repository-root prepend, not backend; redundant for package entry and removed |
| MentorFlow.spec:148 | Declarative Analysis.pathex; retained build-only module discovery, never a runtime prepend |
| scripts/start_mentorflow_local_voice_backend.ps1:24 | Server subprocess cwd=backend and `-m uvicorn app.main:app`; no path mutation, unchanged |
| desktop/MentorFlowLauncher.cs | `-m desktop.main`, cwd=project; no path mutation |
| desktop_entry.py, desktop_gui_entry.py, desktop_update_uat_entry.py, scripts/windows_installed_launch.py | No backend path mutation; installed launch creates a clean environment |

No product startup `__path__` mutation or backend `PYTHONPATH` assignment was
found. Remaining search hits outside product startup (not run or changed):

* `scripts/native_identity_fixture.py:27` prepends project/backend/backend-tests
  for its disposable identity server and test helpers; :453 sets root-only
  child PYTHONPATH. This is a separate acceptance fixture, not a shipped entry.
* `scripts/build_e108_user_candidate.py:198`, `scripts/e108_release_input.py:156`
  set generated-authority/project PYTHONPATH for build inputs, not backend paths.
* Backend prepends in standalone maintenance/smoke tools:
  `archive_import_smoke.py:16`, `create_minimax_teacher_voice_clone.py:26`,
  `install_expedition_art_pack.py:25`, `orchestrate_qwen3_tts_training.py:42`,
  `provider_real_mode_acceptance.py:14`, `run_expedition_balance.py:23`,
  `run_expedition_balance_v1.py:20`, `security_smoke.py:9`, `seed_demo.py:4`,
  `smoke_test.py:6`, `store_provider_credential.py:10` under scripts;
  `run_qwen_base_clone_real_e2e.py:39–40` adds its configured helper roots.
* `scripts/test.ps1:51`, `scripts/test_core.ps1:118`, and
  `scripts/enterprise/run_windows_pytest_diagnostic.ps1:183` configure backend
  test discovery. Desktop test helpers add a repository root or propagate test
  paths (`test_classroom_theme_manifest.py:6`, `test_windows_update_pipe.py:18`).
* `scripts/enterprise/verify_rp05_staging_assets.py:98/:119/:121` creates empty
  test-stub package paths; `scripts/trace_qwen_runtime_testlike_imports.py:203`
  sets a tracing stub path. Neither loads the Desktop local backend.
* Other script hits add only repository/script/portable dependency roots for
  voice/content tooling, not backend startup. `native_desktop_smoke.py:15–16`
  adds its repository root so it can import the Desktop test target.
* `backend/app/sources/document/ingestion/execution.py:84–88` prepares an
  external document-conversion worker's PYTHONPATH. Its pool starts a separate
  configured Python runtime, not the Desktop Account Backend. The local voice
  supervisor removes inherited PYTHONPATH. Neither is an app.main launch seam.

## Resource companion fix

The first real Frozen child reached canonical app.main but failed loading
`_MEIPASS/config/knowledge_policy_v1.json`: existing resources are actually at
`_MEIPASS/backend/config`. Source-relative `parents[2]` would similarly escape
the extraction directory for frontend/catalog assets. Thus changing only the
three import call sites was insufficient.

`app.runtime_resources` gives the canonical project and backend resource roots:
source checkout and checkout/backend; Frozen _MEIPASS and _MEIPASS/backend.
The top-level backend consumers' existing depth-based root expressions now use
these functions. Their relative asset names, environment overrides, storage
binding, schemas, and business logic are unchanged. This mechanical companion
change covers the same failure family for config, catalog, frontend, migrations,
and teacher assets without duplicating resources or moving packaged files.

## Regression and reproduction

`test_backend_import_boundary.py` scans every product Desktop Python module,
root entry and spec, forbidding sys.path access/aliasing and package-path edits;
it also checks the bootstrap and both service callers, build module enumeration,
migration resource retention, conflicting package rejection, and real source
spawn startup. No test clears sys.modules. Conflict tests run fresh interpreters
and verify that the foreign module stays in place after rejection.

`python -m scripts.account_backend_import_smoke <NEW-disposable-directory>`
starts the real Account Backend through spawn on a reserved dynamic IPv4 socket,
waits for MentorFlow health plus canonical origin evidence, stops and joins it,
and writes result.json. Source runs remove PYTHONPATH. A Frozen diagnostic entry
calls freeze_support then this same module; no server substitute or mocked app
is used. Only newly created fixture data directories are accepted.

Frozen fixture assets/logs live in `E:/Codex/Temp/MF-MAIN3-01-IMPORT-16`.
Its dependency manifest is the existing 2906893 frozen build manifest; canonical
app and Desktop code entries are rebound to this worker's checked-in files.
This is a fixture, not a rebuilt acceptance candidate or deployment.

## Final evidence

* 56 Desktop tests passed (boundary, frozen-auth contract, real account backend,
  local service startup, enterprise OIDC, native login, account switching).
* 9 backend tests passed (knowledge policy and knowledge-map/active-teaching
  migrations). The repository-root migration regression now enters through
  the canonical Desktop boundary rather than requesting a second backend.app
  ORM identity; it additionally asserts unchanged sys.path after migration.
* Ordinary server startup from cwd=backend passed real FastAPI lifespan and
  `/api/health` without importing desktop.backend_imports. Evidence:
  `E:/Codex/Temp/MF-MAIN3-01-IMPORT-16/server-source-final/result.log`.
* Source Account Backend spawn passed with PYTHONPATH removed; canonical origin
  `E:/Codex/Data/worktrees/8831/MentorFlow/backend/app/main.py`, SourceFileLoader,
  unchanged search paths, clean exit 0. Repeated in the 56-test suite after
  the resource/ORM changes.
* Frozen windowed EXE spawn passed with PYTHONPATH removed. Evidence:
  `E:/Codex/Temp/MF-MAIN3-01-IMPORT-16/run5/result.json`.
  Origin `_MEI922842/app/main.py`, loader PyiFrozenLoader, PYZ key `app.main`,
  unchanged search paths, health service MentorFlow, catalog.ready=true,
  clean exit 0. Synthetic SQLite files were initialized only inside run5/data.
  EXE SHA-256: `9ffe62a6f0a21236bc94c597cb508834fc07ef3c7012ba41f535cfc853c78b25`.
* The final fixture combines current worker canonical app/Desktop modules,
  current product spec resource declarations, and its explicit OR-Tools DLL
  collection with the earlier frozen dependency manifest. Earlier fixture
  runs exposed missing resources/DLLs in that auth-only input manifest; none
  of those failed runs is counted as a pass. The retained fixture.spec and
  build5.log in the same scratch directory document the exact construction.
* Final boundary rerun: 8 passed, including the strengthened alias/Alembic
  guard and source smoke's failure-cleanup guard. No fixture processes remain.

No PyInstaller hooks were disabled, no source implementation was copied into
a new product namespace, and no existing account or acceptance process was
used. Only the pre-existing resource-copy declaration is retained in the spec.

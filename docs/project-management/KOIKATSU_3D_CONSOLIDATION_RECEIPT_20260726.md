# Koikatsu 3D consolidation receipt — 2026-07-26

## Repository context

- Workspace: `E:\aiPlay\AI-Knowledge visualization`
- Branch: `recovery/session-replay-20260723`
- Remote: `origin`
- Remote URL:
  `https://gitee.com/tonyswift888/mentorflow-learning-expedition.git`
- No commit or push was performed during consolidation.

## Physical moves

All moves were same-volume and preserved their contents. Nothing was deleted.

| Previous location | Consolidated location | Git policy |
| --- | --- | --- |
| `data/kks-assets/` | `legacy/koikatsu-3d/resources/runtime/` | ignored, local-only |
| `data/koikatsu_teachers/` | `legacy/koikatsu-3d/resources/teacher-registry/` | lightweight registry versioned |
| `data/teacher_avatars/` | `legacy/koikatsu-3d/resources/avatar-registry/` | ignored, local-only |
| `frontend/public/assets/kks/` | `legacy/koikatsu-3d/resources/web/` | lightweight JSON versioned |
| `content/teacher_avatar_cards/1.0.0/` | `legacy/koikatsu-3d/resources/card-presets/1.0.0/` | lightweight JSON versioned |
| `artifacts/legacy-koikatsu/` | `legacy/koikatsu-3d/local/archive/` | ignored, local-only |
| `output/` | `legacy/koikatsu-3d/local/evidence/output-20260726/` | ignored, local-only |

An empty `output/` directory was recreated as the normal workspace for future
non-KKS generated output.

## Runtime inventory and Git boundary

- Heavy runtime: 16,679 files, 5,541,368,413 bytes (5,284.66 MiB).
- Trackable files under the new resource root: 11 files, 1.40 MiB.
- No unignored untracked file exceeds 10 MiB.
- Git 2.53.0 and Git LFS 3.7.1 are installed, but the current Gitee endpoint
  reports no authenticated LFS upload/download access. The heavy runtime and
  generated evidence must not be added to a normal commit.

## Path migration

- Added `backend/app/koikatsu_paths.py` as the single backend resource path
  contract.
- Updated backend registries, indexes and runtime inspection to use the unified
  root.
- Updated KKS conversion, packaging, prewarm and verification tools.
- Updated the React runtime to import bundled KKS drivers directly from the
  unified resource root, avoiding a second public/static copy.
- Updated tests and current KKS documentation to the consolidated paths.

Historical GPT recovery manifests retain their old path strings because they
are snapshots of the old workspace, not executable configuration.

## Validation

- Backend focused smoke: 50 passed.
- Backend KKS/Koikatsu/teacher-avatar suite excluding one stale collector:
  100 passed.
- Frontend complete Vitest suite: 18 files, 186 tests passed.
- Frontend production build: passed.
- Real Chrome smoke:
  - AI teacher library opened and the legacy 3D/Koikatsu panel expanded;
  - local card list, presets, avatar packs and Spine endpoints returned HTTP
    200;
  - browser console reported zero errors after the current backend was
    restarted.

Known pre-existing collection defect:
`backend/tests/test_kks_runtime.py` imports `build_card_index` from
`backend/app/koikatsu_card_index.py`, but that application module does not
export the symbol. The unchanged stale test fails during collection before any
test executes. It is not treated as evidence against the path migration and
should be repaired in a separate compatibility change.

## Commit preparation boundary

Before committing, review/stage the consolidation paths and their exact hunks;
the shared worktree also contains older unrelated changes. Do not use
`git add -A`. Do not commit or push until the user approves the reviewed scope.

Recommended commit title:

`refactor: consolidate legacy Koikatsu 3D resources`

Recommended staging groups:

1. Resource moves:
   - remove the six tracked files from their old `content/`, `data/` and
     `frontend/public/` locations;
   - add the 11 lightweight files under `legacy/koikatsu-3d/`;
   - never stage `resources/runtime/`, `resources/avatar-registry/` or
     `local/`.
2. Central path/API contract:
   - `.gitignore`;
   - `backend/app/koikatsu_paths.py`;
   - the path-only hunks in `backend/app/koikatsu_*.py`,
     `backend/app/teacher_avatar_*.py` and `backend/app/main.py`;
   - `backend/tests/test_koikatsu_paths.py` and the path-only test hunks.
3. Frontend contract:
   - the resource URL hunks in `KoikatsuPmxRig.tsx` and
     `kksGltfRuntime.ts`;
   - the three KKS driver test imports.
4. Offline tools and documentation:
   - path-only hunks in `backend/tools/`, `tools/` and
     `scripts/prewarm_kks_runtime.py`;
   - `docs/legacy-koikatsu/`, `docs/project-management/` and this receipt.

`backend/app/main.py`, `frontend/src/features/teachers/KoikatsuPmxRig.tsx` and
several KKS modules were already dirty before consolidation. Stage only the
reviewed hunks in those files unless the user explicitly approves including
the entire previous KKS milestone in the same commit.

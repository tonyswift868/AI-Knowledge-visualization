# Legacy Koikatsu / KKS 3D subsystem

This directory is the project-management index for the experimental Koikatsu
3D teacher pipeline. The current product-facing teacher path is the 2D
illustration / Spine path; Koikatsu remains available only as a legacy import
and migration subsystem.

## Runtime boundary

- Current default UI:
  - `frontend/src/features/teachers/TeacherCompanionStage.tsx`
  - `frontend/src/features/teachers/SpineTeacherPlayer.tsx`
  - `backend/app/spine_teacher_registry.py`
- Legacy 3D UI:
  - `frontend/src/features/teachers/KoikatsuCardEditor.tsx`
  - `frontend/src/features/teachers/KoikatsuPmxRig.tsx`
  - `frontend/src/features/teachers/KksGltfRig.tsx`
  - `frontend/src/features/teachers/TeacherAvatarRig.tsx`
- Legacy 3D backend:
  - `backend/app/koikatsu_*.py`
  - `backend/app/teacher_avatar_cards.py`
  - `backend/app/teacher_avatar_registry.py`
- Conversion and audit tools:
  - `backend/tools/kks_*.py`
  - `backend/tools/*kks*.py`
  - `tools/*kks*.py`
  - `scripts/prewarm_kks_runtime.py`
- Unified resource root:
  - `legacy/koikatsu-3d/resources/runtime/`
  - `legacy/koikatsu-3d/resources/avatar-registry/`
  - `legacy/koikatsu-3d/resources/web/`
  - `legacy/koikatsu-3d/resources/card-presets/`
- Tests:
  - `backend/tests/test_kks_*.py`
  - `backend/tests/test_koikatsu_*.py`
  - `frontend/src/test/kks_*.ts`
  - `frontend/src/test/koikatsu_*.ts`

Do not scatter runtime data back into `data/`, `frontend/public/assets/kks/`,
`content/teacher_avatar_cards/` or the repository root. Resource paths are
centralized by `backend/app/koikatsu_paths.py`.

## Local artifact policy

- New KKS experiments must not create files in the repository root.
- Disposable conversion workspaces belong under
  `legacy/koikatsu-3d/local/archive/<date>/batch-workspaces/`.
- Face-compositor probes belong under
  `legacy/koikatsu-3d/local/archive/<date>/face-compositor-debug/`.
- Browser screenshots, reports and generated runtime evidence belong under
  `legacy/koikatsu-3d/local/evidence/`; this directory is local and ignored by
  Git.
- Files under `legacy/koikatsu-3d/resources/runtime/` are runtime state, not
  scratch output.
- Never delete a legacy artifact until its owner, replacement and runtime
  references have been checked.

## Documents

- `CODEX_MODEL_TASK_ALLOCATION_KKS.md`: historical task allocation.
- `KKS_SPARK_TASK_COMPLETION_REPORT.md`: historical automated-test receipt.
- `kks-native-web-runtime.md`: native web runtime notes.
- `kks-route2-batch-packaging.md`: batch packaging notes.
- `kks-runtime-pack.md`: runtime-pack format notes.
- Root `HANDOFF_NEW_SESSION_KKS_UNIVERSAL_RUNTIME_20260726.md`: exhaustive
  handoff and unresolved visual-acceptance evidence. It intentionally remains
  at the path referenced by prior sessions.

## Current status

The pipeline reached real WebGL and automated checks, but the ten-card visual
gate did not pass. It must not be presented as the active production teacher
or as completed work. The 2D teacher path is now the default product surface.

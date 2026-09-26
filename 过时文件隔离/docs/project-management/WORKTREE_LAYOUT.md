# Workspace layout and artifact policy

## Product source

- `backend/`: FastAPI application, registries, learning engine and tests.
- `frontend/`: React application, public web assets and browser tests.
- `content/`: versioned content packs.
- `story_packs/`: versioned narrative content.
- `scripts/` and `tools/`: repeatable project utilities.

## Runtime state

- `data/`: local databases, imported teacher models and generated runtime
  indexes. Treat this as mutable local state unless a specific fixture is
  intentionally versioned.
- `frontend/public/assets/`: assets served directly by the frontend. Only
  product-consumed assets belong here.
- `legacy/koikatsu-3d/`: the single physical root for all Koikatsu 3D
  resources, local conversion artifacts and visual evidence. See its README
  for the tracked/local-only boundary.

## Documentation

- `docs/legacy-koikatsu/`: all focused Koikatsu/KKS design notes and receipts.
- `docs/2d-ai-teacher-spine-integration.md`: current 2D teacher integration.
- Root handoff/recovery files remain temporarily at their historically
  referenced paths; new handoffs should go under `docs/handoffs/`.

## Generated and local-only files

- `output/`: generated reports, screenshots, logs and image-generation
  intermediates.
- `legacy/koikatsu-3d/local/`: recoverable archive, evidence and retired KKS
  experiments.
- `.playwright-cli/`: browser automation cache.
- root `node_modules/`: accidental local cache; project dependencies belong in
  `frontend/node_modules/`.

These locations are ignored by Git. New experiments must use a named
subdirectory under `output/` or `artifacts/`; no new `.tmp-*` directories or
`tmp_*.png` files may be placed in the repository root.

## 2026-07-26 cleanup receipt

- Moved 17 root `.tmp-kks-*` directories into
  `legacy/koikatsu-3d/local/archive/2026-07-25/batch-workspaces/`.
- Moved 26 root `tmp_*.png` face/material probes into
  `legacy/koikatsu-3d/local/archive/2026-07-25/face-compositor-debug/`.
- Preserved every filename and all contents; nothing from those sets was
  deleted.
- Moved the accidental root Vitest cache (`node_modules/.vite`) and empty
  `.tmp-ilspy` staging tree into `artifacts/local-caches/2026-07-26/`; neither
  contained product dependencies or source files.
- Consolidated `data/kks-assets/`, `data/koikatsu_teachers/`,
  `data/teacher_avatars/`, `frontend/public/assets/kks/`,
  `content/teacher_avatar_cards/1.0.0/`, the legacy artifact archive and KKS
  output evidence under `legacy/koikatsu-3d/`.
- Updated runtime contracts to resolve the new root through
  `backend/app/koikatsu_paths.py`; source files and tests remain in their normal
  application directories.

After local generated output and caches were ignored, the worktree still had
138 visible status entries: 51 modified tracked files and 87 untracked entries.
Of those, 46 paths explicitly contain `kks` or `koikatsu`. This is shared
unfinished work, not disposable clutter. It should be checkpointed or split
into deliberate commits only after a scope-by-scope review; branch switching
alone will not isolate it.

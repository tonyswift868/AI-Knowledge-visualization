# Koikatsu 3D resource root

All MentorFlow resources that imitate, import or render Koikatsu Sunshine 3D
characters live below this directory. Application source remains in
`backend/`, `frontend/`, `tools/` and `scripts/`; it resolves resources through
`backend/app/koikatsu_paths.py`.

## Layout

- `resources/web/`: lightweight body/head driver JSON imported directly by the
  frontend build. Versioned.
- `resources/card-presets/1.0.0/`: built-in lightweight character cards and
  manifest. Versioned.
- `resources/teacher-registry/`: small local Koikatsu teacher registry.
  Versioned in its current empty/default form.
- `resources/runtime/`: generated models, textures, indexes, archives and
  conversion queues. Local-only and ignored by Git.
- `resources/avatar-registry/`: imported/generated 3D avatar models and local
  registry state. Local-only and ignored by Git.
- `local/archive/`: retired experiments and batch workspaces. Local-only.
- `local/evidence/`: screenshots, logs, probes and visual acceptance evidence.
  Local-only.

## Configuration

The default root is this directory. It can be relocated outside the checkout
with `MENTORFLOW_KOIKATSU_ROOT`. The heavy runtime root can be overridden
independently with `MENTORFLOW_KKS_ASSET_ROOT`.

Do not place new KKS files in the repository root, `data/`,
`frontend/public/assets/kks/`, `content/teacher_avatar_cards/` or `output/`.
New generated evidence belongs under `local/evidence/<date-or-task>/`.

The 5.3 GB runtime tree is intentionally excluded from the repository. The
configured Gitee remote does not currently provide authenticated Git LFS
upload/download access, so these binaries must not be added to a normal commit.

Project notes and acceptance status are indexed in
`docs/legacy-koikatsu/README.md`.

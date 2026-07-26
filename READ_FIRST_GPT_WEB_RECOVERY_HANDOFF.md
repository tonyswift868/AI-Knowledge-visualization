# MentorFlow pre-Spark recovery handoff (2026-07-24)

## Recovery target

Restore the local project to the last evidence-backed state immediately before the July 22 Geist Spark takeover. Preserve the universal Koikatsu female rig, arms-down pose, native card parameters, local card indexes, and cache-only browser performance contract. Do not replace this with per-card patches or a new avatar architecture.

## Current workspace

- Root: `E:\aiPlay\AI-Knowledge visualization`
- Branch: `recovery/session-replay-20260723`
- Base commit: `becc82b2f28cb08f6faa71c16f2661a4439de0ac`
- Frontend: `http://127.0.0.1:5173/`
- Backend: `http://127.0.0.1:8000/`
- Original KKS files: `E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine`
- Local female card index: 1529 cards

The working tree is intentionally dirty because the pre-Spark work mostly existed as uncommitted Codex edits. Do not run `git reset --hard`, `git clean`, or delete untracked KKS assets.

## Recovery completed in this pass

### Learning Expedition official art

The art files were never deleted. The loss was the database registry:

- `orm_expedition_art_packs`: 0 rows
- `orm_expedition_art_pack_versions`: 0 rows
- `orm_expedition_hero_assets`: 0 rows
- `orm_expedition_art_reviews`: 0 rows
- `orm_expedition_active_art_pack`: 0 rows

The tracked official pack at
`content/expedition_art_packs/mentorflow_expedition_art_v1/1.0.0`
was revalidated and registered atomically:

- validation: `VALIDATED`
- official files: 528
- runtime assets: 96 (24 heroes × portrait/avatar/skill_cutin/skill_icon)
- active version: `mentorflow_expedition_art_v1@1.0.0`
- reviews: 96/96 `APPROVED`
- API fallback: `false`

The 1.1.0 pack remains untouched because its README identifies it as generated pending review.

This restores the character portraits, battle avatars, skill cut-ins, and skill icons consumed by `BattleReplay.tsx`. Browser inspection confirmed the rich character art is visible again in “学习远征 → 角色与训练”.

Rollback database backup:

`E:\aiPlay\开发指南\study RAG\recovery_backups\mentorflow_v2.before-artpack-restore.20260724-111156.db`

Backup SHA-256:
`BE7EFFA8F3A673D3B928C5B4648141667F4CA746786277D997B8F09F804B2FB9`

### AI Teacher visible head

The direct “headless” failure was a stale hard-coded URL in:

`frontend/src/features/teachers/KoikatsuPmxRig.tsx`

It requested:

`/api/koikatsu-cards/shared-base/head-skeleton/hierarchy.json` (404)

The validated asset actually exists at:

`/api/koikatsu-cards/shared-base/neutral-head/hierarchy.json` (200, 28,938 bytes)

The constant now points to the real asset. Browser verification confirmed that Takamaki Ann renders with a visible shared female head and the existing arms-down body pose. The previous fatal status `native head geometry/ShapeAnime atomic commit failed` is gone.

Static verification also confirmed the shared head assets are internally coherent:

- face geometry: 2,695 vertices
- hierarchy: 113 nodes
- required head target bones: 59/59
- shape drivers: profiles 0/200/201
- face sliders: 52/52

## Truthful remaining boundary

The visible head is currently the official shared female head fallback, not an exact per-card native head.

The pre-Spark `backend/app/koikatsu_teacher_registry.py` was roughly 2,900 lines and returned a `headRuntime` contract. The available historical evidence contains exact slices and patch events, but no single same-time, lossless whole-file snapshot. The recovered current file is only 978 lines and does not emit `headRuntime`. Do not splice asynchronous historical fragments together and claim byte-exact recovery.

The separate reconstructed `koikatsu_card.py` candidate with SHA-256
`0EE6CD1D3C6992303123779F82AB29983E14FCECBDA99E74F36A98EECB4C591C`
also failed live integration: `_face_texture_references()` expected string field names while the recovered `FACE_TEXTURE_SLOTS` entries contain tuple paths. A real runtime-manifest request raised `AttributeError` and returned HTTP 500. The workspace therefore retains the compatible tuple-path implementation that was verified against a real indexed card (HTTP 200, 13 items, about 0.12 seconds). Compile-only and hash-only evidence is not sufficient to replace it.

Consequences:

- The selected card's exact head geometry/ShapeAnime pair is not yet declared by the backend.
- The frontend correctly degrades atomically to the shared female head.
- 52 facial curves are not applied to a mismatched geometry.
- Expressions and per-card head matching are therefore not yet restored.

This is the next high-priority planning item, but it was intentionally not invented in this recovery pass.

## Open-source findings (do not repeat solved work)

- `great-majority/KoikatuCharaLoader` is a maintained open-source parser/serializer for Koikatsu and Koikatsu Sunshine cards. It is a good reference or replacement candidate for card schema coverage, but it does not reconstruct Unity meshes for the browser.
- `Snittern/KoikatsuPmxExporterReverseEngineered` exports a complete character from the in-game character maker. It requires launching the game and therefore does not satisfy the low-CPU, no-game browser-import path by itself.
- `FlailingFog/KK-Blender-Porter-Pack` and `MMD-Blender/blender_mmd_tools` are useful offline reference pipelines for rig/morph/material fidelity. They should inform offline prewarm/export, not become request-time browser work.
- `IllusionMods/KK_Plugins` and Koikatsu Sunshine modding tools are authoritative references for MaterialEditor, sideloader assets, and shader semantics.

Recommended architecture remains:

1. parse the card offline;
2. resolve exact base-game/zipmod assets through SQLite indexes;
3. convert/cache only selected physical assets offline;
4. serve compact immutable geometry/material/shape-driver atoms;
5. assemble them on one shared browser rig;
6. never launch Unity or scan all cards during an HTTP request.

## Verification performed

- `npm.cmd --prefix frontend test -- koikatsu_runtime_semantics.test.ts`
  - 56 passed
- `npm.cmd --prefix frontend test -- expedition_assets.test.ts`
  - 3 passed
- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_expedition_art_pack.py backend/tests/test_expedition.py -q`
  - 20 passed, 1 deprecation warning
- `npm.cmd --prefix frontend run build`
  - passed
- Expedition active-pack API
  - active `mentorflow_expedition_art_v1@1.0.0`
  - fallback `false`
- Four `jinxiao` runtime art URLs
  - portrait/avatar/skill_cutin/skill_icon all HTTP 200
- Browser/WebGL
  - Learning Expedition official art visible
  - Takamaki Ann visible with head
  - shared hierarchy bad URL 404 and corrected URL 200 were independently reproduced
- Post-restart KKS runtime API
  - 1529 indexed cards
  - first card runtime manifest HTTP 200
  - status `partial`, 13 runtime items, about 0.12 seconds

Build still reports large chunk warnings for Phaser and the Koikatsu renderer; these are warnings, not build failures.

## Start commands

Backend:

```powershell
cd "E:\aiPlay\AI-Knowledge visualization"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd "E:\aiPlay\AI-Knowledge visualization"
npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

## Next planner must preserve

- Official Expedition art pack 1.0 activation and rollback semantics.
- Shared neutral body/head assets and the arms-down pose.
- 44 body parameters and 52 face parameters as native card data.
- SQLite card/zipmod indexes.
- Cache-only HTTP behavior.
- Atomic head fallback: geometry and ShapeAnime must commit together.
- No character-name-specific compatibility branches.
- No claim of exact head restoration until a real `headRuntime` payload is verified in browser.

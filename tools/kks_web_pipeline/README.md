# KKS Web model pipeline

This pipeline converts one **already complete** KKBP `Export_PMX` directory
into MentorFlow's browser GLB. It never starts Koikatsu, edits a character card,
or overwrites the source export.

Required source files:

- `model.pmx`
- `Outfit 00/model.pmx`
- the textures/material metadata produced by KKBP Exporter

Example validation without launching Blender:

```powershell
.venv\Scripts\python.exe tools\build_kks_web_model.py `
  --export-dir "E:\...\Export_PMX\20260718153724_早坂愛" `
  --card-id 01413f1d1ea31292 `
  --dry-run
```

Run one conversion by removing `--dry-run`. Existing valid `model.glb` files
are skipped unless `--force` is supplied. Intermediate work uses a temporary
directory and is retained for resumable stages; pass `--work-dir` to select a
stable location when investigating or resuming a failure.

`tools/build_kks_web_model.py` is the canonical, resumable entry point. The
smaller module-local wrapper is retained only as an implementation receipt and
must not be used for new batches.

On Windows the KKBP import and material-bake stages intentionally run Blender
with a hidden GUI context. KKBP Importer 9.4.2 toggles Blender's console during
both operators, and Blender 5.2 crashes with `EXCEPTION_ACCESS_VIOLATION` when
either is executed with `-b`. The pure GLB export stage remains headless.

The publish step validates:

- GLB 2.0 header and length;
- `EXT_meshopt_compression` and `EXT_texture_webp`;
- at least 800 retained nodes;
- required KKS head/arm deformation nodes;
- body, outfit and hair mesh/material groups.
- all 146 Web body/face driver destinations;
- the six neutral/smile/blink expression morphs consumed by the Web runtime;
- the baked KKS light/dark toon maps and at least one normal atlas.

Only after validation is the generated file atomically copied to
`legacy/koikatsu-3d/resources/runtime/web-models/<card_id>/model.glb`.

Turn a validated Web model into the customer-deliverable resource pack:

```powershell
.venv\Scripts\python.exe tools\build_kks_character_pack.py `
  --card-id <16-hex-card-id> `
  --display-name "<character name>" `
  --install
```

The resulting ZIP contains `mentorflow-kks-pack.json` and `model.glb`. Its
path-independent native card fingerprint lets the customer import the normal
card PNG first or the pack first; when both exist, the app matches them
automatically. Koikatsu is not installed or launched on the customer machine.

For a card that has not yet been exported by KKS, prepare a bounded,
resumable KKBP batch instead of attempting to reconstruct it in the browser:

```powershell
.venv\Scripts\python.exe tools\kks_native_conversion_queue.py `
  --card-id <16-hex-card-id>
```

The queue command does **not** launch KKS. It stages only the requested card
(using a hardlink when possible), prints the exact folder for the installed
KKBP Exporter's existing “Bulk export all cards in folder” action, and records a JSON
receipt. After KKBP finishes, rerun the same command. It matches
`card_data.png` by the card's native body/face/ABMX/appearance payload and
converts at most one model per invocation by default, limiting CPU load. A
validated existing GLB is skipped.

# KKS native web character runtime

## Customer delivery model (authoritative)

The customer application never requires, downloads or launches Koikatsu
Sunshine. Customer-side character selection has two tiers:

1. Every native card PNG can be read directly for its preview image, 44 body
   values, 52 face values, ABMX metadata and resource references. The shared
   parameter model remains available as the lightweight fallback.
2. Full hair, clothing, accessories and KKS materials come from an optional
   downloadable MentorFlow KKS character pack. A pack contains a validated
   compressed GLB plus `mentorflow-kks-pack.json`. Importing the ZIP registers
   a path-independent SHA-256 fingerprint of the card's native
   body/face/ABMX/appearance payload. Importing or selecting the matching card
   then activates the pack automatically.

The application bundles or offers an initial complete pack (currently
Hayasaka) and can publish additional packs later. Koikatsu/KKBP may be used by
the developer or content publisher as an offline authoring tool, but is never
part of the customer runtime or the card-import interaction.

The physical bundle-extraction/shared-white-model work documented below is a
fallback and research path. A cache miss must not pretend to be a completed
character. If a matching character pack is not installed, the UI shows the
card preview and universal parameters and offers pack import instead of
displaying mismatched clothing or a broken head shell.

This document is the implementation contract for the universal Koikatsu
Sunshine character-card preview.  It intentionally forbids character-specific
geometry fixes: a card selects data and assets, while one shared runtime owns
all assembly rules.

## Product invariant

1. Start from the vanilla KKS shared body skeleton and one coherent neutral
   preset. The current white model is the game's complete
   `ill_Default_Female` atom: head id 0, body typeBone 0, all 44 body values
   and all 52 face values. Never mix hand-tuned body fields with another face.
2. Import one original character PNG without launching the game.
3. Resolve the card's selected head, hair, clothes and accessories from the
   installed base-game bundles and zipmods.
4. Apply the card's 44 body channels, 52 face channels, typeBone correction,
   selected-head transforms and active-coordinate ABMX modifiers.
5. Keep every skinned garment on the same shared skeleton and preserve one
   private hierarchy per hair/accessory prefab.
6. Keep the shared skeleton, neutral body and WebGL context resident. Replace
   the card-owned head/hair/clothes/accessory subtree atomically; do not retain
   a slot across cards until its geometry, material, texture and private-bone
   ownership all have bounded reference counting.

If a physical asset is absent, the runtime reports that exact reference as
missing.  It must not substitute another character, a similarly named slot, an
arbitrary PMX, or a guessed transform.

The current extracted core is the female KKS hierarchy. `Parameter.sex=1` is
therefore an explicit compatibility requirement. Male cards (`sex=0`) are
reported as unsupported rather than being silently fitted to the female body;
a future male core must be a separate template selected by the same field.

## Native assembly order recovered from the local KKS assemblies

The browser/offline converter must preserve this order:

1. Load `chara/oo_base.unity3d:p_cf_body_bone`.
2. Create the body reference table, initialize body/hand shape drivers, and
   load `p_cf_body_00` onto that skeleton.
3. Load the canonical `p_cf_head_bone` below the body head parent.
4. Load the selected head.  Copy local position, rotation and scale from every
   selected-head node to the same-named canonical node, then bind the selected
   head renderers to the canonical hierarchy.
5. Parent the four hair slots below `cf_J_FaceUp_ty`.  Renderers from one hair
   prefab share that prefab's private bones; they do not receive independent
   duplicate chains.
6. Load clothes with shared-body weight remapping by exact bone name.  Use the
   source renderer's root-bone name when present, otherwise `cf_j_hips`.
7. Parent accessories to the card's exact `parentKey` socket.  Apply addMove
   position in centimetres (`value * 0.01`), degree rotation and direct scale.
8. Evaluate body shape, face shape, then accessory/hair visibility.

Static preview may defer DynamicBone simulation, gaze, blink and breast-normal
updates.  It may not defer shared-bone binding, selected-head transform copy,
typeBone correction, ABMX, card-authored accessory placement, or material
selection.

## Shape contract

- SliderUnlocker values outside 0..1 are preserved and extrapolated from the
  first/last native animation records.  They are never clamped during inspect,
  editing, preview or card rewrite.
- `typeBone=1` adds the vanilla `shapecorrect` values to the body driver.
- Face evaluation happens after body evaluation.  For `cf_J_FaceBase`, sample
  the actual parent world scale for each skeleton and apply the native formula:

  ```text
  factor = typeBone == 1 ? 1 / (1 + bodyCorrection[2].scaleY) : 1
  worldY = parentWorldScaleY * factor
  faceBaseLocalScale = [
    worldY / parentWorldScaleX + sourceFaceWidth - 1,
    worldY / parentWorldScaleY,
    worldY / parentWorldScaleZ
  ]
  ```

- ABMX is read from `KKABMPlugin.ABMData`, version 2, key `boneData`.  Only the
  currently selected coordinate is active.  Apply once from the post-native-
  shape local baseline: scale and length multiply, position and Euler rotation
  add.  Repeated draws must not accumulate modifiers.
- ABMX `BoneLocation` is part of the identity: `1` means BodyTop (body/head and
  non-accessory children); `10 + n` means accessory slot `n`; old value `0`
  may be resolved only when the bone name is unique across the whole character.

## Resource resolution

### Shared neutral shell publication

The neutral body and head are one compatibility unit. Export them together
while the machine is idle; never update only one side and leave a stale
body paired with a legacy head (or the reverse):

```powershell
py -3.13 backend/tools/kks_native_base_export.py `
  --body-bundle "E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine\abdata\chara\oo_base.unity3d" `
  --body-output "legacy\koikatsu-3d\resources\runtime\shared-base\neutral-body" `
  --head-bundle "E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine\abdata\chara\bo_head_00.unity3d" `
  --head-output "legacy\koikatsu-3d\resources\runtime\shared-base\neutral-head"
```

The command stages both conversions before publishing any manifest. It uses
runtime manifest version 2, material schema 6 and hierarchy schema 1; every
item records exact skin/hierarchy/dependency sizes and `runtimeFiles`.
Renderer JSON preserves Unity-authored normals and the same shader-family,
alpha/depth-write, base/shade/rim and ColorMask semantics used for card-owned
assets. Files are moved into place with `os.replace`, and both manifests are
replaced last. Existing generated neutral-body/head files are not upgraded by
source-code changes; the current legacy artifacts must be regenerated with
this explicit command before visual acceptance.

- UniversalAutoResolver identity is `(ModID, CategoryNo, original Slot)`.
  Runtime `LocalSlot` is session-local and must never be persisted.
- Numeric slot `0` is not globally empty.  For example, category 101 slot 0 is
  the vanilla back-hair prefab; `p_dummy`/empty rows in other categories are
  omitted.
- Immutable conversion output is content addressed by physical source bundle,
  asset name and converter schema.  Card manifests contain only semantic slot
  instances and card-authored metadata.
- Unity bundle extraction and conversion are offline/cache operations.  The
  HTTP manifest path must not synchronously scan every zipmod or launch an
  unbounded converter process.
- Zipmod discovery is also offline. Runtime requests execute bounded point
  queries against the generated
  `legacy/koikatsu-3d/resources/runtime/zipmod-index-v1.sqlite3` snapshot and point-check only the archive
  selected by a card.  A missing, incompatible or stale index reports missing
  assets; it must never fall back to walking the live `mods` directory. Build
  the snapshot explicitly while the machine is idle:

  ```powershell
  py -3.13 backend/tools/build_kks_zipmod_index.py --mods-root "E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine\mods"
  ```

  The index records the normalized mods root and is rejected after a game-root
  switch. Equal ListInfo text in two physical archives is a conflict, not proof
  that the bundled meshes are identical; no archive path is guessed until an
  exact content hash or native Sideloader priority is available.

- Card-library discovery follows the same rule. Runtime list/search uses the
  generated `legacy/koikatsu-3d/resources/runtime/card-index-v1.sqlite3`; selecting a card stats
  only that PNG. Until the first index is built, the existing readiness
  snapshot is the no-scan compatibility catalog. Build or refresh explicitly
  while the machine is idle:

  ```powershell
  py -3.13 backend/tools/build_kks_card_index.py --cards-root "E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine\UserData\chara"
  ```

- The runtime-manifest API is cache-only. Missing entries are reported as
  `conversion-cache-miss`/`extraction-cache-miss`; it never launches Python or
  Unity extraction as a side effect of a card click. First reconcile the
  immutable card index into the coverage journal without opening PNG files,
  then evaluate a bounded cache-only batch:

  ```powershell
  py -3.13 scripts/update_kks_runtime_coverage.py --reconcile-only
  py -3.13 scripts/update_kks_runtime_coverage.py --max-cards 60 --time-budget-seconds 60
  ```

  Offline conversion resumes from that journal with one cross-process worker,
  below-normal Windows priority, a free-memory gate, per-card leases and a
  time budget. It can be stopped between cards and safely resumed:

  ```powershell
  py -3.13 scripts/prewarm_kks_runtime.py --from-coverage --limit 12 `
    --time-budget-seconds 900 --min-free-memory-mib 4096
  ```

  The independent `runtime-coverage-v1.sqlite3` stores `pending`, `ready`,
  `partial`, `error`, and `unsupported-male` states keyed by card mtime/size
  and the complete runtime contract. It never replaces live manifest checks.

- A successful local-card selection decodes the selected PNG once, resolves
  its requested IDs once, and returns the parsed card plus runtime manifest in
  the same response. The editor passes that immutable payload to the renderer;
  neither `/inspect` nor a second manifest request belongs on the success path.

- A manually uploaded card follows the same content path. The POST manifest
  endpoint hashes and parses the selected bytes once, then resolves that parsed
  card against the same base/zipmod cache. Filenames are display metadata only
  and are never used to substitute a library card. Upload, inspect and export
  accept at most 96 MiB. This covers the current library maximum of 82.22 MiB;
  the largest measured card contains a legitimate 76.03 MiB MaterialEditor
  texture dictionary. MessagePack item/depth caps remain independent, a single
  embedded texture is capped at 32 MiB, and retained bytes plus synchronous
  card work share a one-slot server limiter so rapid selection cannot fill the
  general worker pool or hold multiple large decoded cards in memory.

- Normal inspection walks KKEx through bounded `memoryview` slices and only
  projects UAR, ABMX and the two small MaterialEditor binding lists. It skips
  MaterialEditor `TextureDictionary` and unrelated plugin payloads without
  materialising their binary values. Offline prewarm locates the dictionary
  separately and copies only texture IDs referenced by the active coordinate.
  On the current 82.22 MiB maximum card this reduced measured Python allocation
  peak from 250.45 MiB to 4.23 MiB while preserving 96 resolver entries, 82
  ABMX modifiers and all 24 active MaterialEditor bindings.

- Every card, including one with a complete KKBP PMX export, renders through
  the same native template. A PMX export may be an offline extraction source;
  it is never a second body or skeleton in the editor.
- If the selected head is unresolved or its main face fails to decode, discard
  that head's remaining layers and use the complete shared head 0 plus profile
  0. Do not mix eye/line renderers from two head prefabs. This is an explicit
  placeholder state in the editor and is never reported as a complete visual
  reconstruction of the selected card.

## Browser performance contract

- Exactly one WebGL context for the editor.
- No `.pmx` request in universal-runtime mode.
- On-demand rendering only: an idle visible preview submits no new frames.
- Camera orbit/zoom/view changes request a draw but do not evaluate 44+52 shape
  channels.
- Body/face/ABMX changes mark shape dirty and evaluate once before the next
  draw.
- One global bounded network/decode queue. Runtime texture requests are capped
  at four across every semantic slot; per-prefab `Promise.all` fan-out alone is
  not an acceptable concurrency limit.
- Every runtime texture request carries the active card generation's
  `AbortSignal`. A superseded card removes queued work, aborts same-origin
  fetches and closes any late ImageBitmap. Because `createImageBitmap` itself
  is not cancellable, an in-flight decode keeps its bounded slot until it
  really finishes; a rapid switch must wait rather than oversubscribe hidden
  decoders beyond the four-slot CPU limit.
- Main-thread mesh JSON/BufferGeometry work is capped at two semantic slots.
  The offline converter exports Unity's authored normals, so current payloads
  avoid `computeVertexNormals()` on card load. Moving numeric arrays to a
  versioned binary/GLB worker pipeline remains the next large peak-memory
  optimization; the HTTP path must not add speculative in-memory caches.
- Keep immutable payload URLs content-addressed so HTTP caching can reuse them.
  Decoded-object caching is optional: if enabled it must be bounded and use
  reference counts; an unbounded geometry/texture cache is worse than an
  atomic card-subtree replacement on a long editing session.
- Local-card search waits 200 ms after text changes, cancels superseded
  requests and rejects stale response generations. Page changes remain
  immediate. Until the SQLite card catalog exists, this prevents repeated
  full readiness-snapshot filters for every keystroke.
- Runtime publication integrity uses a bounded manifest cache and a five-second
  validation TTL. Rapid reuse avoids repeating every small-file check, while
  dependencies are periodically re-statted and every manifest signature
  change invalidates immediately.

## Material translation contract

- Translate KKS shader properties by shader family and semantic property name,
  never by character/card name.  Unknown third-party shaders use a documented
  albedo/normal/alpha fallback.
- Offline conversion emits only compact browser semantics: shader family,
  main texture, optional normal, base/shade/rim colours, shading threshold,
  alpha mode and cutoff.  It does not send the complete Unity material object
  to the browser.
- Material schema 6 normalizes UnityPy `FastPropertyName` keys, reuses decoded
  Texture2D sources within one offline conversion, distinguishes KKS
  cutout/alpha and MToon blend modes, preserves depth-write semantics, and
  records the authored normals. Opaque/cutout materials ignore packed
  `Color.a`; only an explicit blend material receives card-tint opacity. Legacy
  schemas are a cheap manifest-level cache miss; an HTTP request never parses a
  multi-megabyte renderer JSON merely to decide whether prewarm is required.
- Use the already-installed MIT `@pixiv/three-vrm` MToon material for lit
  body, hair, clothes and accessories.  Preserve the existing face-overlay
  path until its layer ordering has separate visual acceptance.
- Bake `_AlphaMask` into base alpha offline.  The first fidelity pass may add
  one normal-map sample, but it omits detail normal, matcap, animated UV and
  outline.  MToon outlines normally require another raster pass and are not
  acceptable for the default low-load preview.
- Material and texture instances are created on card/slot change, never in the
  draw loop.  A persistent material must restore its authored baseline before
  applying the next card's appearance colours.

## Acceptance gates

Use a neutral model first, then exercise at least these contrasting local
cards: Ram (`8abffa4944ed21c1`), Ann (`eb8136372690f12d`), Rikka
(`0d9b09950bc75a03`), then Ram again.

The implementation is not visually accepted until all gates pass:

- zero universal-runtime `.pmx` requests;
- the WebGL renderer instance and shared-skeleton object remain stable across
  card changes;
- ten seconds idle produces zero new frames;
- camera interaction does not increment shape-evaluation count;
- one physical asset is fetched/decoded once even when used by multiple slots;
- 20 repeated card changes show no monotonic CPU/GPU-memory growth;
- fixed body/upper/face views show no detached hair roots, clothes, eyes or
  accessories against a native KKS reference;
- changing representative body and face sliders visibly changes the intended
  native bones while clothes stay attached.

## Open-source components and license boundary

- BepisPlugins (LGPL-3.0): ExtensibleSaveFormat, Sideloader/UAR and
  SliderUnlocker are format/behaviour references.
- ABMX (LGPL-3.0): ABMX's documented serialized and transform contracts are
  implemented independently in the app's engine-neutral codec.
- KK_Plugins MaterialEditor (GPL-3.0): use as a compatibility oracle only; do
  not copy its implementation into this codebase.
- UnityPy (MIT): suitable for selective offline AssetBundle extraction.
- three-vrm MToon (MIT): reusable browser toon-material implementation for the
  compact KKS material semantics; no KKUTS/TCP2 shader source is copied.
- koikatu.js (MIT): useful for browser/Node card parsing and rewriting, not for
  mesh or material assembly.
- AssetRipper (GPL-3.0), KKBP and KKPMX: reverse-engineering and golden-reference
  tools only; none belongs in the request-time web runtime.

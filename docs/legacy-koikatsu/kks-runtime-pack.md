# Native runtime pack

Cards with `runtime-ready` status can be packaged without a replacement GLB:

```powershell
.\.venv\Scripts\python.exe tools\build_kks_runtime_packs_batch.py --queue legacy\koikatsu-3d\resources\runtime\kks-batch-conversion-queue.json --force
```

Each ZIP contains the original card, a rewritten runtime manifest, and the
offline skin/hierarchy/texture files referenced by that manifest. Importing
the ZIP through the normal teacher-avatar import form installs the pack. The
card editor matches it by `native_fingerprint`, keeps the universal body, and
loads the pack manifest from `/api/koikatsu-runtime-packs/...`.

This path is cache-only on the customer side: it does not launch Koikatsu or
Blender. It is only valid for cards whose offline coverage status is
`runtime-ready`; partial or card-only entries remain white-model fallbacks.

# Relocatable UAT boot and attempt health

Dispatch: MF-E107-UPDATER-UAT-LOCATION-HEALTH-COMPAT-20260907-12.

## Binding audit

| Location | Classification | Before | After |
|---|---|---|---|
| `desktop.packaged_update_uat.startup` | UAT_ONLY / HEALTH_CONTRACT | Public config build root, disk allowlist, executable containment, fixed startup marker | Delegate to signed per-attempt ONEDIR boot/health |
| `fast_materialization` | UAT_ONLY benchmark | MFU7/MFU8 allowlist; MFU8 receipt special case | Signed fixture root; receipt existence |
| `main` | UAT_ONLY benchmark | MFU7 / CodexScratch allowlist | Signed fixture root; relative benchmark layout |
| `cross_volume` | UAT_ONLY benchmark | MFU8 and absolute E scratch destination | Signed fixture and cross-volume destinations |
| `scripts/qualify_update_source_acceptance.py` | QUALIFICATION_SCRIPT | MFU8 fixture root | Preserved historical benchmark; not packaged location authority |
| Prior external repack specs/build scripts | BUILD_SCRIPT | Absolute worktree/build paths | Historical artifacts preserved; new spec gets build inputs explicitly |
| `_internal/update-uat/public.json` | UAT_ONLY build data | Included absolute build root | New package has public fixture key and source identity, no root |
| `desktop_gui_entry` / `gui_bootstrap_trust` | PRODUCT_RUNTIME | Actual executable + protected installation receipt | Unchanged; no UAT environment override |

The old location gate was introduced to keep qualification-only actions in
isolated scratch fixtures. It incorrectly conflated that fixture with the
bundle's identity. The actual frozen rejecting function was `startup` in
`desktop.packaged_update_uat`; the prior EXE bytecode was verified against source.

Old benchmark output files are benchmark evidence only (`real_product_health=False`).
They are not inputs to the new health validator and cannot mark a version healthy.
No downloader, hardlink, acceptance, path budget, ACL or installer engine is changed.

## Two different authorities

Boot uses the real `sys.executable` App directory and matching `_MEIPASS` ONEDIR
internal directory. The existing path budget still applies. A signature from a
build-injected TEST-ONLY fixture key authorizes each test attempt and binds its
expected location, root FileId/volume, manifest and executable hashes. There is
no developer drive/root allowlist. The request endpoint is in a unique external
attempt directory; neither App nor its installation root is made writable.

The generated `mentorflow_uat_build_authority` module is supplied only by the
explicit UAT spec. Production entry never consumes `MENTORFLOW_UAT_ATTEMPT`.
The normal product protected receipt/GUI checks remain intact. Test fixture
keys are not product release keys. A test key or marker does not grant a copied
bundle installed authority.

Health includes schema, app ID, random attempt/nonce, runtime version, manifest,
release identity, optional receipt hash, actual root identity, actual process
token/PID, bounded timestamps and four Qt milestones. The coordinator must
retain its own signed request and actual child PID, then compare every binding.
An altered request's health is rejected against that original request. Existing
markers cannot be overwritten; stale, wrong-version/hash/nonce/PID signals fail.

`installed_health_authority` is always false. This module does not implement
commit or CURRENT_HEALTHY. A privileged transaction helper must independently
validate protected install authority, signed release and all target content;
nonce-bearing user-writable evidence alone is not a security attestation.

## Qualification

`scripts.qualify_uat_relocation` builds one EXE from the existing collected
dependency closure, verifies its PE manifest and embedded modules, independently
copies the old bundle, regenerates its manifest, then makes two read-only
hardlink replicas of that new bundle. The old package is never linked for writes.
Three actual frozen processes use offscreen Qt, independently signed attempts,
and separate output directories. This proves Qt event-loop boot, not foreground
UX, authenticated main-window readiness, protected update apply or health acceptance.

The same EXE is also launched with four wrong/stale contracts. Resulting signals
are rejected by the coordinator validator, or the package rejects before Qt.
Full 2.11 GB performance qualification is not repeated; package manifest hashing
is a build-integrity operation, not a performance acceptance run.

The base-seed/update UAC authorizations remain valid and separately counted.
Relocation/health success alone does not satisfy the protected seed receipt gate.

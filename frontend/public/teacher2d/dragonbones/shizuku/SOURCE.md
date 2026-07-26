# Source receipt

- Repository: https://github.com/DragonBones/DragonBonesJS
- Commit: `64b6c69ae35777c2404be68c9192e2c56906079e`
- License: MIT (see `LICENSE`)
- Source skeleton: `Pixi/Demos/resource/shizuku/shizuku_ske.json`
- Source textures: `Pixi/Demos/resource/shizuku/shizuku.1024/texture_00.png` through `texture_03.png`
- Retrieved: 2026-07-26
- Local use: replaceable prototype AI teacher for MentorFlow
- Runtime: DragonBonesJS Pixi 8 demo runtime, loaded locally from `/vendor/dragonbones/dragonBones.js`

## Source discrepancy

The official skeleton JSON contains a `texture_04.png` atlas entry and uses
`shizuku_04` in its slot data, but the same official commit contains no
`texture_04.png`. The application does not synthesize or download a substitute;
the runtime receives the four official PNGs and exposes the discrepancy in its
debug status. A complete production character should provide a self-consistent
skeleton/atlas/texture set.

# Third-party assets

## DragonBonesJS / Shizuku prototype

- Source repository: <https://github.com/DragonBones/DragonBonesJS>
- Retrieved: 2026-07-26
- Source commit: `64b6c69ae35777c2404be68c9192e2c56906079e`
- License: MIT; the original license is copied to `frontend/public/teacher2d/dragonbones/shizuku/LICENSE`.
- Original skeleton path: `Pixi/Demos/resource/shizuku/shizuku_ske.json`
- Original texture paths: `Pixi/Demos/resource/shizuku/shizuku.1024/texture_00.png` through `texture_03.png`
- Local runtime source: `Pixi/Demos/8.x/libs/dragonBones/dragonBones.js`, vendored byte-for-byte as `frontend/public/vendor/dragonbones/dragonBones.js`
- Runtime receipt: `frontend/public/vendor/dragonbones/SOURCE.md`; adjacent MIT license: `frontend/public/vendor/dragonbones/LICENSE`
- Local use: open-source prototype AI teacher / runtime integration only. It is not MentorFlow's final character art.
- Not used: no GameKee character assets, network requests, textures, skeleton files, or commercial game materials.

The official skeleton contains five `textureAtlas` entries but the selected upstream commit contains four PNG pages. MentorFlow preserves that evidence and reports the mismatch; no replacement texture is included.

To replace it with a user-created character, replace the manifest, skeleton, texture pages, license/source note, and catalog entry. The unified `Teacher2DRenderer` interface and learning business flow remain unchanged.

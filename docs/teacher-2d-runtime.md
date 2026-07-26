# Teacher2D runtime

MentorFlow 当前把 DragonBonesJS MIT 运行时和官方 Shizuku 示例作为可替换的 2D AI 教师站位实现。它是 prototype，不是最终原创美术；KKS/Three.js 代码仍保留，默认不会和这个 Pixi 舞台同时初始化。

## Runtime boundary

业务页面只使用 `Teacher2DStage` 和 `TeacherPresentationController`。控制器通过 `Teacher2DRenderer` 接口选择适配器：

- `DragonBonesTeacherAdapter`：当前默认，加载项目内的骨架 JSON、四张本地纹理和 vendored DragonBonesJS Pixi 8 runtime。
- `PlaceholderTeacherAdapter`：future `spine2d` / `none` 的安全占位，不接触 DragonBones 对象。

每个舞台只创建一个 `PIXI.Application` 和一个 armature。适配器使用自己的 Pixi ticker，并在卸载时移除 ticker、ResizeObserver、事件、canvas、armature、DragonBones data 和纹理缓存。页面隐藏时暂停，恢复时继续；React StrictMode 的第一次挂载会在第二次挂载前完整卸载。

Learning Run 的 2D 路径不会加载或初始化旧版 Koikatsu/PMX 模块。旧版导入编辑器保留在角色库的折叠迁移工具内，并通过 `React.lazy` 独立分包；真实浏览器进入 Learning Run 时只出现一个 DragonBones canvas，也没有 Koikatsu/PMX/MMD 网络请求。

## Local prototype

- Manifest: `/teacher2d/dragonbones/shizuku/manifest.json`
- Skeleton: `/teacher2d/dragonbones/shizuku/shizuku_ske.json`
- Armature: `shizuku`
- PixiJS: exact `8.9.2`
- DragonBonesJS source commit: `64b6c69ae35777c2404be68c9192e2c56906079e`
- Local runtime: `/vendor/dragonbones/dragonBones.js`
- Runtime source: `Pixi/Demos/8.x/libs/dragonBones/dragonBones.js` (byte-for-byte identical at the recorded commit)

The skeleton reports version `5.6`, bounds `{x:-640,y:-1380,width:1280,height:1380}`, one default skin, and 489 real animation names. The complete authoritative list is the `armature[0].animation[*].name` sequence in the local skeleton JSON; the production toolbar enumerates the same runtime list without inventing names. The first interaction animations are `idle_00`, `idle_01`, `idle_02`, `flick_head_00..02`, `tap_body_00..02`, `pinch_in_00..02`, `pinch_out_00..02`, and `shake_00..02`; the remainder are the official parameter/material tracks (`PARAM_*`, `B_*`, `D_*`).

The official skeleton advertises five atlas pages, including `texture_04.png`, while the official repository snapshot ships only `texture_00.png` through `texture_03.png`. The adapter loads the four shipped pages exactly as the official Pixi EyeTracking demo does, logs the discrepancy once, and does not fabricate a fifth texture.

## Semantic states

The manifest maps only names present in the real runtime:

| state | real animation | behavior |
| --- | --- | --- |
| idle | `idle_00` | loop |
| greeting | `flick_head_00` | one-shot, returns to idle |
| thinking | `idle_01` | loop |
| talking / explaining | `idle_02` | shared loop fallback |
| questioning | `tap_body_00` | one-shot, returns to idle |
| praising | `pinch_in_00` | one-shot, returns to idle |
| encouraging | `shake_00` | one-shot, returns to idle |
| confused | `shake_01` | one-shot, returns to idle |
| ending | `flick_head_02` | one-shot, returns to idle |

Missing mappings fall back to `idle_00`; repeated state/animation requests do not restart the current animation. Learning Run already supplies `thinking`, `explaining`, and result-driven `encouraging` states to the compact stage. The full teacher page uses the same controller and exposes manual mood controls.

桌面 Learning Run 使用约 58/42 的内容/教师分栏；窄屏改为单列，教师舞台可折叠。工具栏支持真实动画选择、缩放、镜像、复位、暂停和调试。手动动作与点击动作按运行时的真实时长播放，结束后回到 `idle_00`；`prefers-reduced-motion` 用户默认暂停。

## Replacing the prototype

1. Put the user-owned DragonBones skeleton and its texture pages under a new `frontend/public/teacher2d/<id>/` directory.
2. Copy the source license and write a manifest with the real `armature`, default animation, texture URLs, and aliases only for names present in that skeleton.
3. Add the manifest to `frontend/public/teacher2d/catalog.json`.
4. Keep the page code unchanged; select the new manifest URL at the controller boundary. A future Spine implementation adds `SpineTeacherAdapter` without changing Learning Run or provider state.

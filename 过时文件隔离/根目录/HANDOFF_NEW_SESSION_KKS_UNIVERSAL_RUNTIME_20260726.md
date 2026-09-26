# MentorFlow / 恋活通用 AI 教师模型：新会话完整接手文档

> 生成日期：2026-07-26（Asia/Shanghai）  
> 项目根目录：`E:\aiPlay\AI-Knowledge visualization`  
> 当前分支：`recovery/session-replay-20260723`  
> 当前结论：通用 3D 链路已经真正接通，自动化测试通过，但十卡肉眼验收仍未通过。当前最明显的剩余问题是脸部合成/UV 呈现与材质颜色，绝不能扩大到 1500 张卡，也不能声称完成。

---

# 0. 新会话必须先读的结论

本项目不是要“针对每张角色卡修一个模型”，而是要实现一套接近《恋活 / Koikatsu Sunshine》原生体验的通用链路：

1. 网站内置一个通用、可调身体参数的基础模型。
2. 用户选择或导入本地恋活角色卡。
3. 系统从角色卡及本机已有恋活资源中解析身材、脸型、头发、服装、附件、材质和纹理。
4. 浏览器端直接组装出自然、匹配且可继续调整的 AI 教师模型。
5. 客户端用户不需要安装或启动恋活，也不应每导入一张卡就启动一次游戏。
6. 重资源允许由开发/制作者离线批量生成“角色资源包”；终端用户按需下载并导入即可。
7. 性能优先：闲置时不能白白占满 CPU，但不能以牺牲贴图、蒙皮、材质、自然附件位置和参数调整能力换性能。

当前工作已经证明角色卡数据、头部资源和服装资源并非全部缺失；真正问题集中在通用浏览器运行时的组装、材质语义、脸部合成以及部分颜色/透明度处理。

不要再走以下错误路线：

- 不要为每一张卡单独写补丁。
- 不要把角色卡 PNG 当作最终 3D 效果。
- 不要用一个“看起来差不多”的白模或简化参数人偶冒充恋活角色。
- 不要因为某一张 exact GLB 正常，就声称通用链路正常。
- 不要在十卡仍有怪脸、缺鼻嘴、错误发色时扩到 1500 张。
- 不要无证据地反复改 UV 翻转、缩放、深度或透明度。
- 不要启动恋活、CharaStudio 或 Blender，除非新会话先解释必要性且获得用户认可。
- 不要执行 `git reset`、`git clean`、删除临时目录或覆盖当前工作区。

---

# 1. 用户的最终验收目标

## 1.1 产品目标

在 MentorFlow 学习平台左侧提供 AI 教师；用户从分类角色库选择角色，或上传恋活角色卡后，网站直接显示可交互 3D AI 教师。角色应继承卡内：

- 身体比例；
- 脸型和五官；
- 发型和发色；
- 服装、鞋袜；
- 附件；
- 纹理和材质；
- 合理的骨骼/附件位置；
- 可继续调整的身体和面部参数。

## 1.2 性能和交付约束

- 不能要求最终客户安装恋活。
- 最终客户不应启动恋活才能导入卡。
- 可以由开发侧离线批量提取和生成资源包。
- 1500 多张卡必须分类展示，使用中文角色名或合理分类，不能全部挤在一起。
- 浏览器空闲时要降低计算；不可牺牲视觉质量与参数调整。
- 先通过 10 张代表卡的人工验收，再扩大覆盖。

## 1.3 当前十卡验收门槛

至少覆盖：

1. 高卷杏 / Takamaki Ann
2. 藤原千花
3. 拉姆
4. 小鸟游六花
5. 长瀞
6. 早坂爱
7. 高木同学
8. 比嘉光
9. アマリル シャニリア
10. 浅野佳奈

每张都必须检查：

- 全身外观；
- 脸部近景；
- 眼睛、眼白、瞳孔、眉毛、上下眼线；
- 鼻子、嘴唇/嘴；
- 头发与发色；
- 胸部是否居中且自然；
- 服装是否错位、穿模或缺失；
- 腿部是否出现异常黑块；
- 鼠标滚轮缩放和视角控制；
- 身体/面部滑块是否真实作用于 3D；
- 切卡后是否串用上一张卡资源；
- 空闲 CPU 和切卡时峰值。

“自动化状态 ready”不等于视觉通过。必须截图或由用户肉眼确认。

---

# 2. 当前真实运行现场

## 2.1 服务状态（生成本文档时）

- 前端：`http://127.0.0.1:5173`
  - PID：`25928`
  - 命令：`node ...\vite.js --host 127.0.0.1 --port 5173`
- 后端：`http://127.0.0.1:8000`
  - PID：`29128`
  - 命令：`E:\aiPlay\AI-Knowledge visualization\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
- 临时端口 `8010`：没有监听，先前子代理误启动的临时后端已停止。
- 恋活 / CharaStudio / Blender：本轮没有启动。

新会话不要相信 PID 永久有效，先执行：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 5173,8000,8010 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

## 2.2 手动启动命令

后端：

```powershell
Set-Location "E:\aiPlay\AI-Knowledge visualization\backend"
& "E:\aiPlay\AI-Knowledge visualization\.venv\Scripts\python.exe" `
  -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端：

```powershell
Set-Location "E:\aiPlay\AI-Knowledge visualization\frontend"
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

不要重复启动已经监听的服务。先查端口及对应进程命令行，确认确实属于本项目。

## 2.3 浏览器验证入口

1. 打开 `http://127.0.0.1:5173/`。
2. 左侧进入“AI教师角色库”。
3. 从顶部分类按钮中选择十张试点卡。
4. 使用“全身 / 上半身 / 脸部”切换检查。
5. 在模型区域检查滚轮缩放、旋转和滑块。

上个会话使用了 Codex 内置浏览器进行真实 WebGL 检查。浏览器对象和 Node REPL 变量不会跨新会话可靠保留，新会话应重新连接浏览器，不要假设旧变量仍存在。

---

# 3. Git 与工作区安全警告

## 3.1 当前分支与基线

当前分支：

```text
recovery/session-replay-20260723
```

当前 HEAD：

```text
becc82b test: restore current browser acceptance flow
```

最近提交：

```text
becc82b test: restore current browser acceptance flow
5751185 test: add W6 fast-track acceptance matrix
40f6bc4 fix: preserve validated progress and harden KKS fallback
ecef608 test: harden W6 data recovery and security boundaries
9b7950d feat: add explainable memory and skill model foundations
b295e46 feat(w2): extend bounded document conversion
826f68e feat: complete tutor submission loop in learning flow
7a263f5 feat: wire fast-track learning flow and runtime contracts
c60c9c6 feat: add reusable learning flow surfaces
35429e4 feat: add truthful KKS readiness and cache switching contracts
```

## 3.2 工作区非常脏

本文档生成时：

- 48 个已跟踪文件有修改；
- `git diff --stat` 约为 `19704 insertions / 1499 deletions`；
- 存在大量未跟踪源码、测试、运行资产、文档和临时图片；
- `data/kks-assets/`、`data/teacher_avatars/` 包含耗时生成的成果；
- 还有另一个会话正在处理“主动教学 / Hot150”链路，相关修改可能同时存在于同一工作区。

绝对禁止：

```text
git reset --hard
git clean
git checkout -- .
删除 data/kks-assets
删除 data/teacher_avatars
用远端仓库直接覆盖本地
```

不要把所有脏文件都归因于当前恋活任务，也不要贸然提交整个工作区。若要提交，必须先按文件范围审计，并和用户确认。

## 3.3 与另一教学任务并行的文件

以下内容很可能来自“主动 AI 教学 / LeetCode Hot150”并行任务，当前建模新会话不要改坏：

- `backend/app/active_teaching.py`
- `backend/app/code_practice.py`
- `backend/app/generic_knowledge.py`
- `backend/app/generic_knowledge_api.py`
- `backend/tests/test_active_teaching_chain.py`
- `backend/tests/test_active_teaching_state_machine.py`
- `backend/tests/test_code_practice.py`
- `backend/tests/test_generic_knowledge_*.py`
- `frontend/src/features/active-teaching/`
- `frontend/e2e/active_teaching_chain.spec.ts`
- `scripts/*active_teaching*`
- `scripts/hot150_blackbox_acceptance.py`
- `demo/leetcode_top_interview_150_*`

恋活修复应尽量只碰教师角色卡、KKS runtime、对应后端注册表/合成器和相关测试。

---

# 4. 当前通用 3D 调用链

## 4.1 角色卡列表与检查

核心后端：

- `backend/app/koikatsu_teacher_registry.py`
- `backend/app/koikatsu_card.py`
- `backend/app/koikatsu_card_index.py`
- `backend/app/koikatsu_mod_assets.py`
- `backend/app/teacher_avatar_registry.py`
- `backend/app/main.py`

大致调用链：

```text
本地角色卡 / 上传角色卡
→ 后端解析角色卡参数和资源引用
→ 查找本地 zipmod / 共享原生资源
→ 生成或读取 runtime manifest
→ 返回身体、头部、服装、附件、贴图、shape/ABMX 合约
→ 前端选择 exact GLB 或 native-template 路由
→ TeacherAvatarRig
→ KksGltfRig 或 KoikatsuPmxRig
→ WebGL 组装和实时参数调整
```

## 4.2 前端路由分流

关键文件：

- `frontend/src/features/teachers/KoikatsuCardEditor.tsx`
- `frontend/src/features/teachers/TeacherAvatarRig.tsx`
- `frontend/src/features/teachers/KksGltfRig.tsx`
- `frontend/src/features/teachers/KoikatsuPmxRig.tsx`
- `frontend/src/features/teachers/api.ts`

两条合法 3D 路线：

### A. exact 完整 GLB

例如早坂爱当前有验证过的完整导出模型：

```text
角色卡
→ provenance 验证
→ asset_profile=kkbp-web
→ 完整 model.glb
→ TeacherAvatarRig
→ KksGltfRig
```

这条路线是独立完整角色，不应再混入通用 runtime manifest。

### B. 通用 native-template + runtime manifest

其余试点卡应走：

```text
角色卡
→ runtime manifest ready
→ modelUrl=/api/koikatsu-cards/shared-base/native-template
→ nativeExport=false
→ 保留 manifest URL / payload
→ KoikatsuPmxRig
→ 共享身体 + 该卡头部/服装/附件/贴图/shape
```

上个子代理最初发现这条路线虽然计算了 `runtimeAssemblyReady`，却未被消费，manifest 还会被 `commitInspection(..., false)` 清空。这是“页面仍像白模/2D”的核心选择层问题。

该问题随后已经在当前工作区修复：

- 新增并使用 `selectKksPreviewRoute`；
- runtime-ready 会选择 `/api/koikatsu-cards/shared-base/native-template`；
- `nativeExport=false`；
- `commitInspection` 会原子保留 runtime manifest URL 和 payload；
- exact GLB 仍保持优先且独立；
- `matchingInstalledKksRuntimePack` 可按 native fingerprint 匹配路径无关的已安装 runtime pack；
- `asset_profile=kkbp-export` 的 exact PMX 识别也已补入。

证据位置：

- `frontend/src/features/teachers/KoikatsuCardEditor.tsx`
  - `matchingInstalledKksRuntimePack`
  - `selectKksPreviewRoute`
  - `loadLocalCard`
  - 上传卡检查/提交逻辑
- `frontend/src/test/kks_runtime_assembly_gate.test.ts`

注意：此前子代理“其余九卡只显示 2D”的结论是修复前审计结果，不再代表当前浏览器状态。当前浏览器已证实 native-template 真正加载。

---

# 5. 本轮已完成的通用修复

以下修改已经写入当前脏工作区，但尚未提交。

## 5.1 选择层 / 路由层

文件：

`frontend/src/features/teachers/KoikatsuCardEditor.tsx`

完成内容：

- 新增 `selectKksPreviewRoute`。
- runtime manifest core-ready 时选择共享 native template。
- 保留对应卡 manifest，而不是因 `extractedOutfit=false` 清空。
- exact GLB 与 runtime pack 分开匹配。
- installed runtime pack 可按 native fingerprint 匹配。
- exact 完整导出继续优先。

这一步已经让浏览器不再只是显示角色卡 PNG 或空白白模，而是真正出现 WebGL canvas 和 runtime 组装状态。

## 5.2 脸部材质语义

文件：

`frontend/src/features/teachers/KoikatsuPmxRig.tsx`

完成内容：

- 材质名会去掉尾部 `(Instance)` 后再分类。
- `face_composite` 使用 `depthTest=true`、`depthWrite=true`。
- 脸部 overlay 使用 `depthTest=true`、`depthWrite=false`。
- 嘴部使用深度测试。
- 原生头部多材质 renderer 按“材质”分类，而不是粗暴按整 mesh。
- `cf_m_eyeline_kage` 只隐藏该材质，不再把同 renderer 的有效上眼线一起隐藏。
- `kks:transparent` 被视为 optional overlay 的 intentional-none：
  - nose
  - eyeline
  - eyebrow
  - eye_white
- 主脸和瞳孔仍不能被 transparent sentinel 无条件吞掉。
- 只有真实可用的脸部 composite ready 后才压制原生鼻部辅助显示。
- `hasKksBakedLipReplacement` 现在要求：
  - 存在非透明 `lip_line`；
  - composite URL 来自真实 `/face-composites/`；
  - 不能是 raw face_base fallback。
- `shouldSuppressKksNativeFaceItem` 不再删除鼻子；
- 只有证明确实烘焙了嘴线时才删除 `o_tang`。

这些修改修复了已确认的通用逻辑错误，但没有让最终脸部达到肉眼合格。

## 5.3 后端脸部 composite 合约

文件：

- `backend/app/koikatsu_teacher_registry.py`
- `backend/tools/kks_face_compositor.py`

完成内容：

- `faceCompositeCompatible` 改为基于精确 face UV digest，而不是硬编码源 shader 白名单。
- face UV contract schema 从 2 升到 3。
- cache miss 时不再把原始 `face_base` 冒充 `face_composite`。
- Ann 的 Shader Forge 脸因为 UV digest 精确匹配而允许使用 composite；浏览器会替换为可控 Three.js 材质。

## 5.4 十卡缺失 composite 预热

只预热了十卡中一个确定缺少真实 composite 的卡：

```text
native fingerprint: ba5fbec78a8c6c9c
face composite:
/api/koikatsu-cards/runtime-shared/face-composites/99c14962cd6d7c626186667b.png
```

没有批量重跑 1500 张。

一次错误的直接 cache 尝试还生成了两个未引用文件：

```text
data/kks-assets/runtime-skins/_shared/face-composites/401e3a1c417e2c3886ce4ac8.png
data/kks-assets/runtime-skins/_shared/eye-composites/4faab1a53c11949b28768201.png
```

删除操作被策略拦截，因此它们仍在磁盘中。它们是未引用的无害缓存；不要为了“整理”而执行广泛删除。

---

# 6. 自动化验证结果

以下是当前会话在完成上述通用修改后实际取得的结果。交接文档阶段没有重新跑全套，因此新会话若继续改代码，应重新执行。

## 6.1 前端相关测试

结果：

```text
97 / 97 passed
```

重点测试：

- `frontend/src/test/koikatsu_runtime_semantics.test.ts`
- `frontend/src/test/kks_runtime_assembly_gate.test.ts`
- 与 KKS body/head/runtime 路由相关的既有测试

## 6.2 后端相关测试

结果：

```text
70 / 70 passed
```

重点测试：

- `backend/tests/test_kks_shared_runtime.py`
- `backend/tests/test_kks_face_compositor.py`
- `backend/tests/test_kks_universal_transform_contract.py`
- KKS cache / texture / registry 相关测试

## 6.3 前端构建

结果：

```text
passed
```

只有 Vite chunk-size warning，不是构建失败。

## 6.4 变换合约

早坂爱 exact transform verifier：

```text
147 / 147 passed
max matrix error ≈ 4.57e-7
```

## 6.5 diff 检查

`git diff --check`：

- 没有 whitespace error；
- 有大量 LF → CRLF 警告，属于 Windows 工作区换行提示。

建议新会话在任何后续修改后运行：

```powershell
Set-Location "E:\aiPlay\AI-Knowledge visualization\frontend"
npm.cmd test -- `
  src/test/koikatsu_runtime_semantics.test.ts `
  src/test/kks_runtime_assembly_gate.test.ts `
  src/test/kks_body_shape_driver.test.ts `
  src/test/kks_gltf_runtime.test.ts
npm.cmd run build
```

```powershell
Set-Location "E:\aiPlay\AI-Knowledge visualization"
& ".\.venv\Scripts\python.exe" -m pytest `
  backend/tests/test_kks_shared_runtime.py `
  backend/tests/test_kks_face_compositor.py `
  backend/tests/test_kks_universal_transform_contract.py
```

不要因为这些测试通过就声明视觉合格。

---

# 7. 十卡后端/运行时状态

离线 manifest 检查：

- 10/10 头部 runtime 为 ready；
- 10/10 face UV compatible；
- 10/10 有真实 `/face-composites/`；
- 9/10 整体 ready；
- 早坂爱整体 partial，仅因 `humble_bianka` 上衣引用未解析，脸部 ready；
- 长瀞有一个非阻塞的 optional lip `definition-not-found`；
- 早坂爱使用 exact GLB；
- 其余卡走 native-template 通用组装。

浏览器对十卡的运行时抽查：

- 所有通用卡都出现 WebGL canvas；
- `runtimeMode=native-template`；
- `modelState=ready`；
- head/body/face/faceTextures 大多为 ready；
- 无普遍 missing/error；
- 早坂爱为 `runtimeMode=kkbp-web`；
- アマリル シャニリア 在一次只等待 3.2 秒的抽查中 `extraState=loading`，当时已加载 36 个资源，需要重新等待 8–10 秒确认是否只是慢加载。

Ann 浏览器 dataset 曾记录：

```text
modelUrl=/api/koikatsu-cards/shared-base/native-template
runtimeMode=native-template
modelState=ready
extraState=ready
head=ready
bodyShapeState=ready
faceShapeState=ready
nativeFaceTextureState=ready
runtimeSkinLoaded=26
runtimeBodyMatches=88
runtimeFaceBoneMatches=59
selected head copies=113
canvas=1
```

这证明通用 3D 链路已接通，但视觉仍失败。

---

# 8. 当前肉眼效果：仍然不合格

## 8.1 Ann / 高卷杏

原角色卡应是：

- 金发；
- 蓝眼；
- 有清晰且自然的鼻子和粉色嘴唇。

当前网页实际：

- 头发偏灰，不是正确金色；
- 眼睛方向/眼白观感异常；
- 脸接近一张浅色平面；
- 鼻子和嘴的存在感极弱，近似缺失。

浏览器检查显示相关纹理和材质“已加载”，因此问题不是简单的 URL 404。

Ann 的真实 composite 文件：

```text
data/kks-assets/runtime-skins/_shared/face-composites/d0a6ee39cedc7208353947ab.png
```

文件中能看见：

- 脸部肤色；
- 耳部；
- 很小的鼻部提示；
- 很小的嘴唇提示。

它可能确实被应用了，但在当前 UV/合成位置下鼻嘴小得不合理。下一步应审计“compositor 放置与头部 UV 的一致性”，而不是继续加 visibility 补丁。

## 8.2 其他用户已明确指出的问题

历史截图中出现：

- 眼睛错位、眼白不自然；
- 眉毛/眼线像白色或黑色面片；
- 鼻子和嘴消失；
- 脸部扭曲；
- 胸部偏左或比例异常；
- 服装错位；
- 腿部黑块；
- 头上像面具；
- 某些卡只有局部材质；
- 早坂爱曾不能用滚轮调整。

本轮通用逻辑修复解决了其中一些“确定的代码错误”，但还没有完整重拍十卡对比图证明这些现象都消失。

---

# 9. 当前最可能的剩余根因

按优先级排序。

## P0：脸部 composite 与实际头部 UV 的位置/尺度不一致

证据：

- 真实 composite 文件存在；
- 浏览器报告 face texture ready；
- Ann 仍像没有正常鼻嘴；
- composite 本身的鼻嘴提示非常小。

重点检查：

`backend/tools/kks_face_compositor.py`

- `_neutral_nose_layer`（约 144–170 行附近）
- lip line 合成（约 255–270 行附近）
- 输出 canvas、anchor、scale、翻转规则

以及：

`frontend/src/features/teachers/KoikatsuPmxRig.tsx`

- `kksFaceTextureFlipY`
- 创建 `THREE.Texture` 后的 `flipY`
- face composite 的 UV/texture transform

禁止只凭肉眼猜 `flipY=true/false`。应做可复现验证：

1. 给 composite 生成带网格/象限/关键点的临时 debug texture；
2. 在同一标准头上渲染；
3. 确认额头、鼻、嘴的 UV 落点；
4. 对照 Blender/导出 UV 数据或 manifest 内 digest/UV bounds；
5. 再修改通用变换。

## P0：眼部层级/材质与卡内参数不一致

虽然资源存在且 ready，部分卡仍出现巨眼、视线异常或眼白过多。

已知数据：

- 常见 eye-white Alpha 覆盖率约 29%–34%；
- 某些卡高达约 64.7% 或 92.2%；
- 这不一定代表源文件坏，也可能是材质混合、UV 或形变重复应用。

需要确认：

- 原生 head shape 是否已包含眼睛比例，而前端又重复应用卡片 face shape；
- pupil/eye-white 是否使用相同 UV 方向；
- mesh/morph 与 ABMX 是否重复叠加；
- 上下眼线是否被同时以几何和 composite 烘焙两次；
- 瞳孔颜色和 blend mode 是否正确。

## P0：头发颜色/材质遮罩未正确应用

Ann 应为金发，网页偏灰。

需要检查：

- runtime manifest 中发色/base color/mask color；
- shader 依赖的 main texture、detail、color mask；
- MeshStandard/Basic 材质替代时是否丢掉 tint；
- 颜色是否处于错误 color space；
- alpha/transparent 是否导致底色变灰；
- 是否只加载 diffuse、未应用卡片 material color。

重点文件：

- `frontend/src/features/teachers/KoikatsuPmxRig.tsx`
- `backend/tools/kks_runtime_skin_export.py`
- `backend/app/koikatsu_teacher_registry.py`
- runtime manifest 对应 Ann hair material entries

## P1：身体形变是否重复或坐标空间错误

胸部偏左不应通过单卡修正。应检查：

- 左右骨骼映射是否对称；
- bone local/world transform 的应用顺序；
- ABMX 与 body shape driver 是否重复；
- 旋转/位移是否在绑定姿势与当前姿势之间混用；
- 服装是否应用与身体相同的 shape transform；
- 左右名称归一化是否错配。

已有 exact transform verifier 通过只证明某条精确导出路线，不证明所有 runtime attachment 都正确。

## P1：附件/服装绑定和黑块

需要检查：

- 黑块是错误材质、透明遮罩、背面、法线还是未解析附件；
- 服装骨骼重定向是否完整；
- 缺少 zipmod 定义时是否应降级而不是显示错误几何；
- body/cloth 是否使用同一套尺寸参数；
- 是否存在深度写入或排序错误。

---

# 10. 推荐的新会话执行顺序

## 阶段 A：只做证据采集，不改代码

1. 核验端口、分支和 `git status`。
2. 打开当前网页。
3. 对十卡逐张：
   - 等待至少 8 秒；
   - 拍全身；
   - 拍脸部；
   - 记录 `.koikatsu-model.dataset`；
   - 记录 console error 和 Network 404；
   - 记录发色、鼻嘴、眼睛、胸部、腿部、服装。
4. 形成十卡表格，明确每张的同类问题。
5. 不要在采集过程中边看一张边修一张。

验收表建议字段：

```text
卡名
runtimeMode
model/head/body/face/texture state
全身是否合格
脸部是否合格
发色
眼睛
鼻子
嘴
胸部对称
服装绑定
腿部黑块
滚轮
滑块
错误/缺失 URL
截图路径
```

## 阶段 B：验证一个通用根因

优先选择“脸部 composite UV/anchor”：

1. 用标准头和 debug texture 明确 UV 落点；
2. 比较 Ann、长瀞、拉姆至少三张；
3. 证明同一变换可修复多张；
4. 写纯函数或离线图片测试；
5. 再做最小代码修改；
6. 只重跑十卡需要的缓存；
7. 重跑相关测试和浏览器对比。

如果同一修改只能让一张好看、让其他卡变差，应立即撤销该局部修改，不继续扩大。

## 阶段 C：发色/眼睛/身体对称

每类问题分别建立：

- 输入资源证据；
- runtime manifest 证据；
- 浏览器材质/骨骼证据；
- 最小通用修复；
- 至少三张卡的回归截图；
- 自动化测试。

## 阶段 D：十卡人工验收

只有在十卡全部达到以下条件后才能扩容：

- 无明显怪脸；
- 鼻嘴存在且自然；
- 眼睛不突兀、不漂浮；
- 发色接近原卡；
- 胸部与身体对称；
- 无明显黑块；
- 服装不严重错位；
- 滚轮与参数调整可用；
- 切卡不会串资源；
- 无持续高 CPU。

## 阶段 E：再讨论批量资源包

十卡通过后再做：

- 分类与中文命名；
- 1500 卡批量包；
- 按需下载；
- manifest 版本和校验；
- 缓存/卸载；
- 失败报告；
- 性能分层和 LOD。

---

# 11. 推荐使用子代理的方式

用户允许根据需要开合理数量子代理，但不要浪费。

本轮三个子代理均已结束：

- `head_selection_gate_audit`
- `material_editor_full_contract`
- `ten_card_face_asset_audit`

新会话最多建议并行 2–3 个独立只读任务：

1. 浏览器十卡截图与 dataset 记录；
2. face compositor / UV 离线数学审计；
3. hair material / color-mask 数据审计。

主代理负责：

- 合并证据；
- 决定唯一优先根因；
- 写最小补丁；
- 运行测试；
- 做最终肉眼回归。

不要让多个代理同时编辑 `KoikatsuPmxRig.tsx`。不要让代理自行启动恋活、Blender 或额外后端。

---

# 12. 关键文件地图

## 前端

### `frontend/src/features/teachers/KoikatsuCardEditor.tsx`

职责：

- 本地角色卡选择；
- 上传卡检查；
- exact GLB / native-template 路由；
- runtime manifest 保留；
- installed pack 匹配；
- 将模型、参数和 manifest 传入渲染器。

当前价值：

- 通用链路选择层修复已在这里完成。

风险：

- 切卡时异步响应竞态；
- exact model 与 runtime manifest 混用；
- 旧缓存匹配到错误角色。

### `frontend/src/features/teachers/TeacherAvatarRig.tsx`

职责：

- 根据模型类型分流至 `KksGltfRig`、`KoikatsuPmxRig` 或其他 rig。

风险：

- 路由判断出错会让 exact GLB 走通用组装或反之。

### `frontend/src/features/teachers/KksGltfRig.tsx`

职责：

- 渲染完整 exact GLB；
- 早坂爱当前主要走这条路线。

注意：

- exact GLB 的成功不能代表通用链路成功。

### `frontend/src/features/teachers/KoikatsuPmxRig.tsx`

职责：

- 加载共享 native body；
- 加载 selected head；
- 加载服装和附件；
- 建立材质/纹理；
- 应用 body shape、face shape、ABMX；
- 调度渲染并降低空闲计算；
- 暴露运行状态 dataset。

风险最高：

- 文件已超过 6000 行；
- 多材质 mesh、透明、深度、颜色、UV、骨骼、附件都集中在此；
- 不适合无证据大改；
- 任何通用修复必须配多卡回归。

重点函数：

- `kksFaceTextureFlipY`
- `shouldApplyKksFaceTexture`
- `hasKksBakedLipReplacement`
- `shouldSuppressKksNativeFaceItem`
- 材质分类与 face texture application
- selected-head 合约与 fallback

### `frontend/src/features/teachers/kksBodyShapeDriver.ts`

职责：

- 身体参数到骨骼/形变映射。

### `frontend/src/features/teachers/kksHeadShapeDriver.ts`

职责：

- 面部参数到头部形变映射。

### `frontend/src/features/teachers/kksAbmx.ts`

职责：

- ABMX 参数解析/应用。

### `frontend/src/features/teachers/teacher-avatar.css`

职责：

- 角色库与 3D 编辑器布局。

用户截图显示页面存在横向溢出、顶部角色按钮区域拥挤；当前不是 P0，但十卡视觉通过后要修。

## 后端

### `backend/app/koikatsu_teacher_registry.py`

职责：

- 角色卡目录；
- exact model provenance；
- runtime manifest；
- 头部/身体/服装资源；
- face composite / eye composite 清单；
- ready/partial/failure 状态；
- shared asset URL。

风险：

- cache-ready 不代表浏览器视觉正确；
- fallback 不得把原始纹理冒充完成合成图；
- UV digest 和 schema 变更会影响旧缓存。

### `backend/tools/kks_face_compositor.py`

职责：

- 生成脸部 composite；
- 合成肤色、鼻部、lip line 等。

当前 P0 审计对象。

### `backend/tools/kks_eye_compositor.py`

职责：

- 眼部/瞳孔合成。

### `backend/tools/kks_runtime_skin_export.py`

职责：

- 导出浏览器运行时需要的共享/卡片皮肤和材质信息。

### `backend/tools/kks_native_base_export.py`

职责：

- 原生共享基础模型导出。

### `backend/app/teacher_avatar_registry.py`

职责：

- 已安装 teacher avatar/runtime pack 注册；
- runtime pack 可只有 manifest、没有独立 model URL。

## 数据

### `data/kks-assets/`

包含：

- runtime skin；
- shared head/body；
- face/eye composites；
- 角色卡索引；
- 解析缓存；
- 可能耗时数小时生成的资产。

禁止删除或整体重建。

### `data/teacher_avatars/`

包含已安装角色/运行时资源包和注册信息。

---

# 13. 已知半成品、临时文件和风险

## 13.1 大量 `.tmp-kks-*`

根目录存在多个未跟踪目录：

```text
.tmp-kks-ann-pack-final-20260725/
.tmp-kks-ann-upgrade-20260725/
.tmp-kks-ann-upgrade-20260725-b/
.tmp-kks-batch-sync-*/
```

它们可能含批处理/同步中间成果。没有逐个完成归属审计前不要删除。

## 13.2 临时图片

存在：

```text
tmp_face_*.png
tmp_lip*.png
tmp_comp*.png
tmp_blend*.png
tmp_body_tex.png
tmp_tang.png
tmp_diff.png
```

这些是脸部合成/纹理调试证据。可用于新会话分析，但不能当正式验收产物。

## 13.3 installed runtime pack

注册表曾统计：

- 44 个 `kks-runtime-pack`
- 1 个旧 `kks-character-pack`

十卡中只有部分 fingerprint 对应已安装 runtime pack。当前已实现 matcher，但需要浏览器切卡和路径独立匹配回归。

## 13.4 早坂爱

- 目前使用 exact GLB，视觉相对较好；
- 不能用它证明 shared native-template 成功；
- 整体 manifest partial 的 `humble_bianka` 上衣问题与脸部无关；
- 鼠标滚轮/视角需要重新人工验证。

---

# 14. 当前可以可信声称什么

可以声称：

- 网站和后端当前能启动；
- 1529 张本地卡能被列出和分类分页；
- 通用 native-template 路由已经被前端实际消费；
- 浏览器出现真实 WebGL canvas，而非单纯 2D 图片；
- 十卡头部资源不是普遍缺失；
- 通用材质语义中多个已确认 bug 已修复；
- 相关前端、后端测试和构建在本轮通过；
- exact transform verifier 通过；
- 没有启动恋活、CharaStudio 或 Blender；
- 用户的十卡视觉验收还没有通过。

不能声称：

- 通用白模已经完成；
- 十卡已经合格；
- 1500 张卡已经可用；
- 鼻嘴、眼睛、发色已全部修好；
- 服装/胸部/腿部错误已全部消失；
- 性能已经达标；
- 所有滑块都已逐项验证；
- 角色资源包已可面向客户交付；
- 当前工作区可以安全 reset/clean；
- 所有未跟踪文件都属于本轮。

---

# 15. 新会话第一条建议提示

可以直接把本文档交给新会话，并附上：

> 先完整阅读 `E:\aiPlay\AI-Knowledge visualization\HANDOFF_NEW_SESSION_KKS_UNIVERSAL_RUNTIME_20260726.md`。不要开发新功能，不要重置或清理工作区，不要启动恋活/Blender。先核验 Git、端口和十卡当前浏览器视觉状态。通用 3D 路由已接通、自动化测试已通过，但肉眼仍不合格。先用十卡截图和 runtime dataset 找到“脸部 composite 与真实头部 UV/anchor 不一致”的通用证据；未证明根因前不要改代码，十卡未通过前不要扩到 1500 张。任何修复必须是通用修复，并用至少三张卡回归。

---

# 16. 最终交接状态

本轮在收到“上下文已满，写转手文档”后：

- 已停止扩大功能；
- 没有继续修改业务代码；
- 没有启动恋活、CharaStudio 或 Blender；
- 核验了分支、dirty worktree、最近提交和监听端口；
- 收口了三个已结束子代理的结论；
- 明确标记了一个子代理的“九卡仍为 2D”结论已经被后续路由修复取代；
- 记录了真实浏览器中“通用 3D 已加载但脸仍不合格”的关键边界；
- 新增的唯一文件就是本交接文档。

新会话应从十卡证据矩阵和 face compositor/UV 契约开始，不应重新发明路线，也不应继续对单卡试错。

# MentorFlow 教师模型能力报告

状态：`LOCAL_RUNTIME_VERIFIED / PUBLIC_REDISTRIBUTION_BLOCKED`

审计日期：2026-08-22  
审计对象：当前工作树的教师 manifest、Live2D model3/CDI/physics/expression/motion、Pixi/Cubism 运行时、能力扫描器、动作注册表和真实浏览器渲染。

## 1. 能力判定

MentorFlow 只把以下三类计为教师动作：

1. model3 真正登记并由 Cubism motion manager 播放的 `.motion3.json`；
2. model3 真正登记并由 expression manager 播放的 `.exp3.json`；
3. 基于当前 Core 参数真实索引和 min/default/max、且不写 physics destination 的内部参数曲线。

舞台 fit、镜像、用户缩放、摄像机偏移不是动作。整模型/Canvas 的 scale、translate、rotate、skew 不得冒充眨眼、点头、呼吸或教学状态。

## 2. 当前模型和发布边界

| 模型 | Renderer | 能力 | 发布边界 |
| --- | --- | --- | --- |
| `mentorflow-blonde-fang-v9` / 艾莉丝 | `layered2d` | 项目自有分层视觉；不是 Live2D 参数模型 | 可作为公开默认教师；不宣称 Live2D motion |
| `dragonbones-shizuku-prototype` | `dragonbones2d` | 只播放资源中真实存在的 armature 动画 | 原型/降级参考；缺失纹理必须如实报告 |
| `tsubaki-live2d` / 椿 | `live2d2d` | 本机真实 Cubism 模型、物理、5 表情、1 motion、内部安全参数动作 | 仅本机开发验收；原包无明确再发布授权，公开包默认排除 |

全新安装不得继承开发者的 Tsubaki、主题或 Provider 预设。`MentorFlow.spec` 仅在显式 `MENTORFLOW_INCLUDE_PRIVATE_TEACHER_ASSETS=1` 时纳入私有教师目录。

## 3. Tsubaki 本机能力实测

模型描述：`frontend/public/teacher2d/tsubaki-live2d/tsubaki.web.model3.json`

- Core 参数实测：36；
- model3 登记 motion：`Cry:0`，其 motion3 `Meta.Duration=6s`、`Meta.Loop=true`；
- model3 登记 expression：`dark-face`、`blush`、`heart-eyes`、`no-highlights`、`cry`；
- CDI 显示信息共 110 项，但 CDI 名称不等于安全可写能力；
- Physics 输出由扫描器从 `physics3` 自动收集并从可写集合剔除；
- 浏览器 Inspector 显示 36 parameters、1 native motion、5 expressions；
- `BLINK` 使用 `ParamEyeLOpen` 与 `ParamEyeROpen` 的真实范围曲线，浏览器冻结图可见双眼闭合；
- 动作期间舞台 bounding box 保持完全相等。

唯一 motion 文件名虽然是 `idle.motion3.json`，实际驱动眼泪参数，因此只登记为 `Cry`，不能冒充正常待机或说话动作。

## 4. 当前运行时实现

依赖：`untitled-pixi-live2d-engine@1.3.5`、`pixi.js@8.13.1` 与本机 Cubism Core。

当前实现包括：

- `live2dCapabilities.ts`：读取 model3、motion metadata、CDI、physics 与 Core 参数；
- `live2dMotionRegistry.ts`：原生 motion > 原生 expression > 完整安全参数曲线；缺参数即不暴露；
- `Live2DMotionInspector.tsx`：仅调试模式显示，可枚举、单播、停止、冻结、恢复和回到课堂权威状态；
- `Live2DTeacherAdapter.ts`：动作曲线只写模型内部参数，physics output fail-closed；
- Freeze 停止共享 Pixi/Cubism ticker，防止下一帧把已渲染闭眼恢复成待机；Resume/Stop/Restore 在非全局暂停且页面可见时恢复 ticker；
- 原生 motion 使用 motion3 的真实 duration/loop/fade，缺失 metadata 时不发明超时；
- 原生 motion/expression 拒绝或失败会明确降级并报告，不伪造成功。

## 5. 已验证证据

### 自动测试

- 7 个 Live2D/舞台/禁假动作测试文件：**59 passed**；
- Qt 主题/设置/课堂/原生桌面：**68 passed**；
- 无认证/KYC 路由及发布隐私组合：**41 passed, 4 skipped**；
- `npm run build`：**PASS**，3158 modules。

### 真实浏览器

Playwright：`frontend/e2e/live2d_motion_inspector.spec.ts`，**1 passed**。

证据目录：`E:\Codex\Temp\mentorflow-w36-live2d-final3-evidence`

- `live2d-motion-inspector-idle-baseline.png`：真实模型、face camera、双眼睁开；
- `live2d-motion-inspector-blink-center-frozen.png`：调试信息为 `animation: BLINK`，双眼闭合；
- 测试同时断言 renderer=`live2d2d`、runtime=`ready`、fallback=`false`、原生 `Cry:0`、5 个表情、舞台边界不变、无 page error/console error。

## 6. 硬门禁

| 门禁 | 状态 | 结论 |
| --- | --- | --- |
| `OPEN_SOURCE_REUSE_VERIFIED` | VERIFIED_SOURCE_LEVEL | 固定版本/commit、采用/适配/拒绝矩阵已记录；没有从候选仓库复制模型资产 |
| `REAL_LIVE2D_MOTION_VERIFIED` | VERIFIED_LOCAL_MODEL_BOUNDED | 本机真实模型 BLINK 已完成浏览器视觉验收；全动作逐项人工质量仍未完成 |
| `NO_FAKE_WHOLE_MODEL_MOTION` | VERIFIED | 静态守护、59 测试和浏览器舞台边界证据均通过 |
| `MULTI_ART_STYLE_VERIFIED` | VERIFIED_LOCAL_QT | 16 套结构性主题、68 测试、16 个唯一渲染签名和截图通过 |
| `THIRD_PARTY_LICENSES_AUDITED` | AUDITED_WITH_BLOCKERS | 清单已建立；Cubism Core/Tsubaki 再分发授权和远程字体字节仍阻塞公开发布 |

## 7. 仍未验证

- 31 个契约动作和 13 个课堂语义状态尚未全部做逐项人眼质量验收；
- 真实 Windows 打包窗口、不同 GPU、高 DPI 与长时间运行；
- Tsubaki 和 Cubism Core 的公开/商业再分发权；
- 正式安装包仍未基于这次工作树重建和签名。

因此当前结论是“本机真实动作链路已经成立”，不是“所有动作质量完成”或“公开版本可分发”。

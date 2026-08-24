# MentorFlow W36 课堂美术与 Live2D 动作验收报告

日期：2026-08-22  
源码基线：分支 `codex/knowledge-map-teacher-v1`，HEAD `a16cd2981d6ed8007ddda04ff7195994278b7231`，`WORKTREE_CLEAN=false`。  
结论：**本地源码/Qt/浏览器验收通过；公开发布仍被第三方再分发授权和未重建安装包阻塞。**

## 1. OSS RESEARCH REPORT

- 文件：`docs/OSS_RESEARCH_REPORT_LIVE2D_CLASSROOM_V2.md`；
- 10 个候选固定到精确 commit；
- 保留 `untitled-pixi-live2d-engine@1.3.5`，不再引入第二套 Live2D runtime；
- Open Props、pattern.css、Catppuccin 采用 token/纹理/调色板组织思路；daisyUI 仅作主题结构参考；
- 没有从研究仓库复制模型、动作、背景或品牌资产。

## 2. THIRD PARTY ASSET MANIFEST

- 文件：`docs/THIRD_PARTY_ASSET_MANIFEST.json`；
- 13 项代码/运行时/模型/主题来源记录可由 JSON 解析；
- Pixi、Live2D engine、Three 等代码依赖与模型资产权利分开审计；
- Tsubaki 原包授权、Cubism Core 再分发条款、远程 Google Fonts 实际字节仍是公开发布阻塞项。

## 3. MODEL CAPABILITY REPORT

- 文件：`docs/MODEL_CAPABILITY_REPORT.md`；
- 本机 Tsubaki 浏览器实测：36 Core parameters、1 native motion、5 expressions；
- physics destination 自动剔除；缺 physics 能力时内部曲线 fail-closed；
- 原生 motion metadata 使用真实 Duration/Loop/Fade；
- 私有模型默认从公开 PyInstaller 包排除。

## 4. REAL ACTION MATRIX

- 文件：`docs/REAL_ACTION_MATRIX.md`；
- 31 个稳定契约动作，加 13 个课堂 semantic state、原生 motion 与 expression 枚举；
- 实现优先级：registered motion > registered expression > complete safe parameter curve > unsupported；
- Inspector 提供 Play/Stop/Freeze/Resume/Restore；
- `BLINK` 已完成真实浏览器视觉闭环；其余动作自动契约通过但不是全部已人工验收。

## 5. THEME MATRIX

- 文件：`docs/THEME_MATRIX.md`；
- 16 套结构主题，公共默认 `sakura`；
- 设置页画廊选择只保存在本机，不进入迁移包或新电脑默认值；
- 离线验收目录：`E:\Codex\Temp\mentorflow-w36-theme-structural-df3370f8b30d419d807dc775d56991e6`；
- `summary.json`：`render_signature_unique=16`、`render_signature_theme_count=16`；
- 16 个普通主题截图和 1 个 Sakura 沉浸模式截图均保持草稿和课堂会话；沉浸模式只隐藏上层壳，保留编辑器与恢复键。

## 6. FAKE MOTION AUDIT

- 文件：`docs/FAKE_MOTION_AUDIT.md`；
- image2d 整图呼吸、Spine fallback `teacher-breathe`、layered root pelvis 跳动已从语义动作路径移除；
- Live2D action 曲线只写安全 Core 参数，physics output 零写入；
- Playwright 断言动作前后 stage bounding box 完全相同；
- 浏览器截图同时证明 idle 睁眼和 BLINK 闭眼，排除“按钮变化但模型没动”的假通过。

## 7. ACTUAL SCREENSHOTS

Live2D 证据目录：`E:\Codex\Temp\mentorflow-w36-live2d-final3-evidence`

| 文件 | SHA-256 | 结果 |
| --- | --- | --- |
| `live2d-motion-inspector-idle-baseline.png` | `1C01E283AE59DBEA7EC5CB18D63C760E07E2E60D68DDF8F8BBEA48B5E5DFC12C` | renderer ready、fallback false、双眼睁开 |
| `live2d-motion-inspector-blink-center-frozen.png` | `764D807AE26864809EC612AD77D8F8CEF4B7D779312307DD9419A2D32517017C` | `animation: BLINK`、双眼闭合、真实冻结帧 |

没有制作视频，实际交付为两张同场景对照截图；全动作演示录屏仍未完成。

## 8. TEST RESULTS

| 范围 | 命令摘要 | 结果 |
| --- | --- | --- |
| Live2D/action/舞台/禁假动作 | 7 个指定 Vitest 文件 | **7 files / 59 passed** |
| 真实本地模型浏览器 | `playwright test e2e/live2d_motion_inspector.spec.ts --workers=1` | **1 passed / 11.0s** |
| Qt 主题/设置/课堂/原生桌面 | 4 个 pytest 模块 | **68 passed** |
| 无 auth/KYC + 发布隐私 | 3 个 pytest 模块 | **41 passed, 4 skipped** |
| 差异格式 | `git diff --check` | 见最终收口检查 |

所有浏览器网络均限制为 `127.0.0.1/localhost`；没有真实 Provider、Bilibili 或 YouTube 请求；真实 `E:\MentorFlowData` 未用于测试。

## 9. BUILD RESULTS

- `npm run build`：PASS；
- TypeScript project build：PASS；
- Vite：3158 modules，1.62s；
- 现有大 chunk warning 保留，未把它伪装成失败或在本任务扩展性能重构；
- 正式 Windows 安装包未按当前脏工作树重建、签名或替换。

## 10. HARD GATES AND UNVERIFIED ITEMS

| Gate | 状态 | 说明 |
| --- | --- | --- |
| `OPEN_SOURCE_REUSE_VERIFIED` | `VERIFIED_SOURCE_LEVEL` | 研究、固定版本、采用/拒绝和许可证边界已记录 |
| `REAL_LIVE2D_MOTION_VERIFIED` | `VERIFIED_LOCAL_MODEL_BOUNDED` | BLINK 真实模型视觉通过；全动作人工质量仍未完成 |
| `NO_FAKE_WHOLE_MODEL_MOTION` | `VERIFIED` | 静态、单元和浏览器 stage invariant 通过 |
| `MULTI_ART_STYLE_VERIFIED` | `VERIFIED_LOCAL_QT` | 16 结构主题、68 测试、17 张截图通过 |
| `THIRD_PARTY_LICENSES_AUDITED` | `AUDITED_WITH_BLOCKERS` | 审计已完成，但 Tsubaki/Cubism Core/远程字体权利未清除 |

仍未验证：

1. 全部动作逐项真实设备人眼质量、长时间 idle、动作打断组合和录屏；
2. 最新源码打包后的 Windows 桌面窗口、高 DPI、不同 GPU 和 reduced-motion 实机；
3. Tsubaki 和 Cubism Core 的公开/商业再分发许可；
4. 正式安装包重建、签名、更新链路；
5. 真实 Provider 与真实 Bilibili 大合集端到端；YouTube 不是当前发布阻塞项；
6. 主题在正式打包字体与所有窗口尺寸下的逐套人工验收。

因此本报告允许用户继续本地功能和视觉验收，但不构成公开发布 PASS。

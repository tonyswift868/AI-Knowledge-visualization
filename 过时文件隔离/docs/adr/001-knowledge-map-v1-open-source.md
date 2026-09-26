# ADR-001：通用知识地图 v1 的开源组件边界

- 状态：Accepted
- 日期：2026-08-13
- 适用范围：Windows 本地 MentorFlow 知识地图、材料转换、教师教学链

## 决策

MentorFlow 只维护产品特有的修订、证据、审核、计划激活和教学状态机；格式转换、结构化模型输出、图算法、图形交互和凭据存储使用成熟开源组件。

| 能力 | 组件与固定版本 | 许可证 | 使用边界 |
|---|---|---|---|
| 文档结构与 OCR 编排 | Docling 2.119.0（可选运行包） | MIT | PDF/Office/EPUB/扫描件转换；不使用其模型构建课程语义 |
| 结构化 LLM 输出 | Instructor 1.15.4 | MIT | Pydantic 验证与受限重试；Provider 生命周期仍由 MentorFlow 管理 |
| 图校验 | NetworkX 3.6.1 | BSD-3-Clause | 环检测、拓扑和图约束；不负责用户数据持久化 |
| 图形交互 | `@xyflow/react` 12.11.3 | MIT | 节点、边、键盘与缩放交互 |
| 有向布局 | `@dagrejs/dagre` 3.1.1 | MIT | 客户端确定性布局 |
| Windows 凭据 | keyring 25.7.0 | MIT | 仅接受 Windows Credential Locker 后端；无明文回退 |
| 图片 OCR | RapidOCR 3.x | Apache-2.0 | 本地图片及扫描页面文本化 |
| 音频转写 | SenseVoice + sherpa-onnx | 对应仓库/模型许可证 | 复用现有只读运行时，MentorFlow 只管理进程、上传根与证据时间段 |
| 视频目录与字幕适配 | yt-dlp 2026.8.19（Python 包） | Unlicense | 只做用户确认后的公开目录元数据和字幕轨道适配；禁止下载视频媒体，课程摘要、证据、断点和状态机仍由 MentorFlow 管理 |

Docling 放在可选依赖组中：轻量文本、代码和结构化格式继续使用现有本地适配器；需要复杂文档时才安装。适配器能力和健康状态必须通过统一注册表暴露，不能在 API 或前端再维护扩展名清单。

## 不采用整套框架

- Docling Graph：不作为主依赖。它会同时引入 Docling 标准栈和 LiteLLM，且没有提供 MentorFlow 所需的边级证据、用户审核与计划原子激活。
- Microsoft GraphRAG：面向检索索引与社区摘要，运行成本和数据模型不适合本地课程/掌握度产品。
- LlamaIndex PropertyGraph：会复制现有 Provider、存储和索引抽象，仍需自建产品审核层。
- OpenTutor：只参考其持久任务、可恢复教学阶段和显式偏好优先级，不复制实验性的 LOOM 图或领域阈值。
- OR-Tools、FSRS、pyBKT：v1 不替换已经工作的平均任务时长调度，也不在缺少校准事件时声称概率掌握度。

## 反硬编码规则

生产核心只能包含通用 schema、状态机和算法。关系类型、格式/MIME、模型配置、提示词版本、阈值、任务分段和补救次数来自版本化策略注册表；课程名称、固定知识点、固定边、固定模型 ID、领域示例和类比句不得出现在核心路径。

任何可选依赖不可用时，能力接口必须返回稳定的 unavailable/rejected 原因；禁止静默生成结构兜底知识点。

## 升级要求

升级任一组件前必须验证：Python 3.13/Windows 支持、许可证变化、能力注册表输出、混合材料证据、Provider 错误映射、前端构建及 last-good 修订不变。依赖版本只能在本 ADR、requirements/package lock 和 SBOM 同步更新后合并。

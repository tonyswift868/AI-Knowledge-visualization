# 创建计划与教师教学实施计划

本计划把 `LEARNING_PLAN_TUTOR_FEATURE_SPEC_FOR_CODEX` 的 14 项正式契约拆成可验证阶段。产品代码位于 `refactor`；测试源码、缓存、报告和临时构建输出放在 `E:\Codex\Temp\MentorFlowTests` 或其他项目外临时目录，不进入重构区。

## Phase 0：事实盘点与边界

- 只读审计现有仓库的旧管理员数据库、owner 标识、资源存储、Live2D 配置、知识地图和本地/服务端同步入口。
- 只在证据明确时建立迁移 adapter；没有明确 owner 时返回 `LEGACY_DATA_NOT_FOUND`，不猜 ID。
- 扫描不到 `.model3.json` 或真实资源地址时返回 `LIVE2D_ASSET_NOT_FOUND`，课堂保留文字模式。
- 记录当前工作树的真实降级，不把环境缺失伪装成产品完成。

## Phase 1：模型调用与三 Worker

- Worker A：User & Evidence Analyst，分析用户目标、约束和私有资料；没有私有资料时仍输出目标/约束分析。
- Worker B：Curriculum Researcher，检索公开资料并保存 URL、标题、抓取时间、来源类型、摘要/证据片段和 hash；不可用时返回 `research_unavailable`。
- A/B 条件允许时并行，Worker C Planner/Synthesizer 等待二者后生成 Draft；三者是三次独立模型调用。A/B/C 均只接受 JSON 对象，C 的坏输出最多修复一次，解析失败不会写入草稿。
- 默认统一使用 `deepseek-flash`。连接层实现 OpenAI-compatible Chat Completions、错误分类、退避重试、一次结构化 repair retry、业务幂等键和无状态推进失败策略。

## Phase 2：Draft、Plan 和资源域

- `confirmDraft(planId, expectedRevision)` 在服务端原子写入确认事件；前端 checkbox 只能表达意图。当前草稿缓存通过稳定账号作用域的 `draft_plan` 同步记录跨设备恢复，仍保留 draft revision 和确认事件。
- Plan/Draft/Curriculum/Learning Contract 带 revision；编辑用新 revision，普通删除为 archive/soft delete。
- Resource 与 PlanResourceLink many-to-many；detach/soft delete 保留 tombstone 和历史 Evidence 引用。
- 跨设备资源上传到服务器对象存储；用户选择本机或上传失败时标记 `local_only`，Git/网页只保存版本化引用和提取结果。

## Phase 3：排程、Daily Mission 和课堂 checkpoint

- Plan 保存 IANA timezone；每日任务开始前可按计划和进度重排。
- 用户点击开始当天学习后写入带 `plan_revision` 的 Daily Mission/Learning Contract snapshot；随后后台不静默改变。
- 用户调整顺序、暂停/继续等业务修改写入服务器并经过 expected revision；冲突返回 409。
- Classroom 同步教学 checkpoint，不同步设备瞬态 UI；设备切换恢复“学到哪、完成什么、下一步是什么”。

## Phase 4：Canonical Curriculum / 知识地图

- canonical graph 直接来自 Formal Curriculum 的 Milestone、Competency 和 prerequisite edges。
- 现有知识地图只做 adapter 投影；没有现有地图则提供最小 Curriculum endpoint，不另造大型地图服务。
- 节点 ID 由服务端生成并稳定；布局/折叠是 presentation metadata，不触发课程 mutation。

## Phase 5：验收和清理

- 自动验收使用 mock Provider、临时本地/测试 DB 和两个独立客户端身份，覆盖 A/B/C、Draft/Confirm、Tutor、Evaluator、Error Ledger、Daily Mission、revision conflict、资源关联和 checkpoint 恢复。测试源、缓存和报告统一归档在 `E:\Codex\Temp\MentorFlowTests`，不进入 `refactor`。
- 真实 smoke 使用现有 dev/staging；找不到 staging URL 时只用本地服务，不猜生产地址；Secret 仅由环境注入。
- 测试源码与结果全部放在 `E:\Codex\Temp\MentorFlowTests`；清理 `refactor` 下的 `tests`、`__pycache__`、pytest 缓存和临时报告。需要运行的前端 bundle 只保留产品运行所需的构建资产。

## 完成判定

每个 Phase 必须同时有：产品代码、契约级自动验收、真实事实/降级记录和对应 Integration Map 更新。缺少真实 provider、旧 owner、Live2D 资源或 staging 凭据时，只能完成可运行降级和明确错误状态，不能声称真实环境已验收。

## 当前核对结果（2026-09-24）

交接核对（2026-09-25）：本地产品代码、同步写入链和契约级局部证据已留在当前工作树，但不能把 Phase 0–5 写成全部完成。正式 provider session、服务端 confirmDraft/Daily Mission 权威写入、知识地图生成/激活、真实两设备和真实模型教学仍未验收；洁净扫描确认 refactor 只保留产品运行文件，测试源/缓存/报告位于 `E:\Codex\Temp\MentorFlowTests`。

# 创建计划与教师教学集成图（正式契约版）

> 这份文档记录当前工作树的真实接线、正式契约和明确降级。代码实施计划见同目录的 `PLANNING_IMPLEMENTATION_PLAN.md`。测试源文件、测试缓存和测试报告不属于 `refactor` 产品区，统一放在 `E:\Codex\Temp\MentorFlowTests` 或临时目录。

## 1. 运行入口

```text
account_shell_main.py
  -> LearningWindow / 主导航
    -> PlanningPage.create_page(context)
      -> PlanService(context)
      -> PlanningWebView
        -> planning/web/dist/index.html
        -> QWebChannel: PlanningBridge
```

`PlanningPage.create_page` 会在 `context["_planning_page"]` 中复用未释放的页面。`PlanningWebView` 使用本地 `dist`，请求拦截器只允许规划 bundle 和 Qt WebChannel；页面不启动旧 HTTP 应用，也不直接持有账号凭据。`PlanningBridge` 的后台任务使用单线程执行器，结果按请求 id 返回，账号作用域由 `PlanService.require_current()` 再检查。

## 2. 创建计划数据流

| 阶段 | 前端动作 | Bridge action | 服务层 | 持久化/输出 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| 读取草稿 | 页面加载 | `get_state` | `PlanService.ui_state()` | `planning-ui.json`、`MaterialStore` | 已接 |
| 需求对话 | 用户发送自然语言 | `assist` | 已验证 provider session 时走 `server_planning_assistant`，否则走本地模型客户端 | 仅写入账号草稿中的对话记录；模型提案先进入待确认区 | 已接；正式生成阶段使用 A/B/C 三个独立 Worker |
| 目标确认 | 用户确认提案 | 前端更新表单并 `stage_draft` / `save_draft` | `normalize_form`、`save_draft` | 账号本机 `planning-ui.json` | 已接 |
| 资料检索 | 根据目标搜索 | `search_sources` | `ResearchService.search` | 候选不直接入计划 | 已接 |
| 资料读取 | 勾选候选或输入链接 | `fetch_sources` | `ResearchService.fetch` | `research-sources.json`，保留正文片段、chunk id、hash | 已接 |
| 本机资料 | 文件选择或粘贴 | `import_materials` / `import_text` | `MaterialStore.import_file` | 账号资料元数据、正文片段及按策略保存的原件（本机或账号对象存储） | 已接 |
| 目标与成果 | 填写成果、标准、水平 | 表单字段随草稿提交 | `normalize_form` | 进入生成请求 `details` | 已接 |
| 时间约束 | 起止日期、学习日、每日分钟、单次时长、约束 | `analyze` | `PlanService.analyze` | 返回可用学习日和分钟容量；不写计划 | 已接 |
| 生成 | 用户核对后生成 | `generate` | Worker A（用户/私有资料分析）与 Worker B（外部研究）可并行；Worker C（Planner/Synthesizer）等待 A/B 后生成 Draft；严格 JSON 校验后 `compose` | 只形成当前会话 Draft；调用带业务幂等键 | 本地/模拟契约已接；外部研究不可用时返回 `research_unavailable` |
| 草稿确认 | 用户确认最终 Draft | `confirm_draft` | 目标契约为服务端原子写入 `confirmed_at/by/revision`，需 expected revision；当前桌面实现先写账号草稿投影并经同步链提交，真实服务端原子确认未在本次环境验收 | 正式业务事件，可跨设备读取 | 本地/模拟契约已接；前端 checkbox 不是事实源，确认事件落到可同步草稿 |
| 保存 | 用户确认预览 | `save` | `save_plan` 使用 revision + optimistic concurrency | `plans.sqlite3` / 服务器聚合，随后同步计划、任务、资源关系 | 本地/模拟契约已接；真实服务器与付费模型仍需 smoke |

表单的主要字段为 `goal_statement`、`expected_output`、`success_criteria`、`start_date`、`deadline`、`weekdays`、`minutes_per_day`、`preferred_session_minutes`、`current_level`、`constraints`、`material_ids` 和 `source_ids`。模型生成时会收到本机资料 chunk 与已读取网页 chunk，并要求每个任务给出 objective、acceptance_condition 和合法 source_chunk_ids；解析后服务端拒绝未知引用。

## 3. 计划输出与资料/知识路线

`compose()` 根据日期、每周学习日和每日分钟把任务排入日程。Plan 保存 IANA timezone 和 revision。计划预览当前展示：

- 每日任务分钟汇总；
- 任务前置关系形成的“知识路线草图”；
- 关联本机资料和网页资料的“资料地图”；
- 任务目标与可验证完成条件。

计划详情页的“编译今日任务”调用 `PlanService.daily_mission()`，以已保存的计划和最新进度为唯一输入，按 Plan 自己的 IANA timezone 计算日期和可用分钟；逾期未完成部分会进入 reflow，进行中的任务会扣除已用分钟，休息日默认不排任务。用户点击开始当天学习后，服务端保存带 `plan_revision` 的 Daily Mission/Learning Contract snapshot；之后后台排程不能静默改变已开始快照。用户显式修改任务顺序、暂停或继续时走 revision mutation 并同步到其他设备。

这里的“知识路线草图”来自生成任务的 `prerequisite_task_ids`。主导航“计划完成情况”替换旧资料管理表：先列出账号全部计划，选择后在同页展示按稳定知识引用分组的完成/进行中/待学习节点及已保存的前置连线。计划详情页的“知识地图”标签仍通过 canonical adapter 读取/生成/激活服务端地图，不创建第二个地图服务。`PlanService.plan_detail_projection()` 统一输出计划、任务、资料归属、缺失原件和地图状态；地图生成和激活不会在创建计划时偷偷替换计划快照。

## 4. 教师教学数据流

```text
已保存 CurrentPlan
  -> 原生课堂选择计划/任务
    -> PlanService.learning_materials(plan_key)
    -> classroom.teaching.build_messages / generate
    -> model_client
    -> 课堂消息与 turn 记录
    -> 用户点击“这个知识点我已学会”
    -> mark_knowledge_learned(plan_key, task_id, note)
    -> 同一 knowledge_ref 的阶段任务原子保存进度
```

教师样式、预期产出、关联资料正文和当前任务阶段会进入课堂请求。Tutor 使用低延迟模型策略；课堂状态同步到最近教学 checkpoint（当前计划、Learning Contract、active session、objective、attempt、Evidence/Error、Tutor checkpoint、课堂摘要和 pause/resume），不跨设备同步输入框、滚动、表情或半截流式 token。Live2D 仅复用现有仓库资源；扫描不到资源时返回 `LIVE2D_ASSET_NOT_FOUND`，右侧区域降级为静态占位/隐藏，文字课堂仍可用。

课堂事实链还提供一个可选的隐藏 Evaluator 接线：

```text
student attempt
  -> EvaluationService（严格 JSON、契约/ID/资料引用校验）
  -> EvidenceEvent / ErrorEpisode
  -> BlockResolver
  -> TutorCheckpoint + VerificationScheduler queue
```

Evaluator 未配置时，原有课堂仍只保存学习者输入这一事实，不能把 Tutor 文本或“我会了”自述当成通过证据。配置 Evaluator 后，评估结果才会在宿主校验成功后写入同一账号的课堂库；验证队列使用真实错误、提示依赖和缺失证据动态排优先级，不采用固定复习比例。课堂页面已提供紧凑的 Session Recap、掌握检验队列、开始/提交/补救操作；这些记录会随计划和账号同步。`ClassroomRecords.start_verification` / `resolve_verification` 只接受宿主决定的状态迁移。

## 5. 账号、跨设备与文件边界

- `PlanService` 在构造和每个写操作前校验当前账号、组织和 workspace 作用域；作用域变化会拒绝旧请求，不把旧账号草稿写入新账号。
- `plans.sqlite3` 保存本机缓存；正式 Plan、Curriculum、Learning Contract、Daily Mission、任务、进度、课堂 checkpoint 和资料关系以服务器聚合为权威，同步使用 revision + optimistic concurrency。
- 核心聚合修改提交 `expected_revision`，版本不一致返回 409 Conflict；Evidence、Error Episode、completion event 等 append-only 记录用 UUID + idempotency key 去重，笔记也采用 revision 冲突，不静默覆盖。
- 原始文件按资源策略保存：本地上传进入 `refactor/server/account_object_storage.py` 服务器对象存储并按账号隔离；后端只保留薄装配，部署配置持久卷。客户端对下载的完整字节做 SHA-256 和大小校验，失败不 ACK，下轮继续重试。Git/网页资源保存 URL、版本/commit、提取结果和 hash，不复制整个互联网。
- `Resource` 与 `PlanResourceLink` 是 many-to-many；普通删除是 soft delete/detach，保留 tombstone 和历史 Evidence 引用。
- `planning-ui.json` 仍是账号本机 UI 缓存；当前草稿会投影为账号作用域稳定键的 `draft_plan` 同步记录，带 draft revision 与确认事件，设备切换可恢复已确认草稿。原始文件字节仍按 `local_only` 或对象存储策略处理，不能把本机路径当成云端文件。大型草稿、聊天、工具活动、研究来源、回执与错误记录使用受限大小的 `history_blob` 对象并通过摘要清单同步；对象缺失或校验失败时保持待同步并重试，不 ACK。
- 设置、工具活动、自由聊天会话和检索来源都投影为稳定账号同步记录；大型 Live2D/模型包仍是设备本地资源，不混入账号业务数据。

## 6. 当前边界与后续风险

1. **正式知识地图生成/激活**：canonical graph 是 Formal Curriculum 的 Milestone、Competency 和 prerequisite edges；现有地图则用 adapter 投影，不另造地图后台。节点 ID 由服务器生成，布局变化不触发课程语义变更。
2. **服务器规划助手联调**：连接页需要可验证 provider session；没有 session 时保留本地 mock/客户端降级。真实 DeepSeek smoke 使用环境 Secret，不能写入仓库。
3. **资料归属展示**：资料按 `Resource + PlanResourceLink` 留在计划详情；主导航已替换为计划完成情况。原件未成功下载时保持明确失败/可重试状态，不把正文片段说成完整原件。
4. **计划编辑与删除**：普通删除是 archive/soft delete，历史 Learning Contract、Evidence、Error Ledger、课堂记录、笔记和资源引用保留；编辑创建新 revision。
5. **课堂 UI 验收**：Evaluator/Verification 的 recap、队列和补救入口接到原生课堂；跨设备只恢复 checkpoint，不续接设备瞬态 UI。
6. **真实事实边界**：管理员本机 owner 已确认，2026-09-24 按用户授权清理其本机学习目录及服务器管理员工作区同步投影，账号及模型连接预设保留；服务器实体/请求和对象存储均已清空，回执位于 `E:\Codex\Temp\MentorFlowTests\admin-cleanup-20260924.json`。真实椿资源位于 `frontend/public/teacher2d/tsubaki-live2d`，缺失时允许手动选择本地包目录；仓库没有可信在线下载 URL，来源说明也没有明确再分发许可，因此不伪造下载服务。
7. **研究与真实端点**：`ResearchService` 只使用显式 `MENTORFLOW_SEARXNG_URL` 或已安装 `ddgs`，仓库没有固定生产研究 endpoint；provider smoke 必须使用环境注入配置。

### 已确认的产品边界

- 不恢复旧总应用、旧全局状态或旧页面的隐式同步副作用。
- 不在创建计划或普通刷新时自动读取旧计划来替代当前账号同步；账号同步才是服务器事实源。旧历史恢复只允许显式的受控迁移动作，并且不能把本机缓存当成云端已确认数据。
- 不把模型生成成功当作计划保存成功；必须经过服务端 `confirmDraft(planId, expectedRevision)` 正式事件。
- 不用标题相似度猜测资料、任务或知识点归属，关系来自稳定 id、引用和账号作用域。

## 7. 模型调用追踪与状态迁移

`ModelClient` 的正式默认配置是 `deepseek-flash`、`https://api.deepseek.com/chat/completions`、OpenAI-compatible Bearer API。400/401/402/422 不自动重试；429/500/503 和瞬时网络错误使用 exponential backoff + jitter，最多 3 次总尝试；结构化输出非法最多 1 次 repair retry。所有会写状态的调用携带业务幂等键，失败不得推进学习状态。调用遥测只记录账号、计划、会话、任务、role、provider、model、UTC、耗时和受控错误码，不保存 prompt、响应、API Key 或 provider 错误正文。

这是本地桌面侧的轻量追踪边界，用于定位账号、计划、会话和模型调用之间的关系。服务端 provider、跨设备日志汇聚和真实凭据下的延迟/失败告警属于部署环境验收，不在本地重构区伪造完成。

课堂当前设备的页面瞬态（输入框、滚动、动画和半截流式 token）不进入同步；active session/status、objective、attempt、Evidence/Error、Tutor checkpoint、摘要和 pause/resume 属于服务器同步合同。动态 Daily Mission 在开始前可重编译，开始后固定为服务器 snapshot；任何用户业务修改都必须经过 revision 和冲突处理。

## 8. 14 项正式契约实施状态

实施顺序、证据要求和明确降级见 `PLANNING_IMPLEMENTATION_PLAN.md`。当前工作树包含 A/B/C、Draft、Plan/Note/Curriculum/Learning Contract revision、Daily Mission、Resource/PlanResourceLink、课堂 checkpoint、Live2D 降级及账号同步的本地/模拟接线；这不等于真实服务端原子确认、provider session、正式知识地图、双设备或真实模型教学已验收。管理员清理仍以 2026-09-24 事务回执为证据。所有契约测试源、缓存和报告都在 `E:\Codex\Temp\MentorFlowTests`，重构区只保留产品运行文件。

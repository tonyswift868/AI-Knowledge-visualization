# MentorFlow｜GPT Web 当前项目主上下文（2026-08-24）

> 本文件是给 GPT Web、产品协作者和验收代理上传的**当前本地工作树事实说明**。它不是路线图，也不是发布声明。
> 本轮以 `E:\aiPlay\AI-Knowledge visualization` 在 2026-08-24 的磁盘源码、manifest、脚本、测试证据和现有安装包为准；工作树很脏，不能把 HEAD、远端、旧报告当成现状。

## 0. 上传后启动提示词

将本文件上传后，先发送：

```text
你现在是 MentorFlow 的产品、交互、架构和验收协作者。请完整阅读我上传的《MentorFlow｜GPT Web 当前项目主上下文》，先建立项目模型，再回答后续问题。

必须遵守：
1. 当前对话中的最新用户指令优先；若能访问仓库，当前本地工作树、实际数据库契约、运行输出和真实界面优先于本文件、README、旧报告和远端历史。不要要求 reset/clean/checkout，也不要覆盖未知脏改动。
2. MentorFlow 是 Windows 本地单用户个人学习软件。不要添加或建议注册、登录、账号、KYC、OAuth、JWT、多租户、云端账户或远程认证体系；历史 auth 表/迁移名不代表现行功能。
3. 不要虚构资料、引用、节点、课程、搜索结果、Provider 成功、教师动作、进度或测试通过。未验证内容必须标记“未验证”。
4. 当前正式规划资料是文本/文档/结构化数据/代码/OCR 图片/安全 ZIP；视频、视频合集、音频、字幕、转录不属于 canonical planning 合同。源码中的低层遗留 media/converter 路由不得被写成当前正式支持。
5. AI 只能提出目标解释、检索查询和候选；用户负责选择资料、可调整 anchor/support、审核知识地图候选并激活地图和计划。首个已选资料可能按 UI/legacy 规则默认成为 anchor，但这不是 Provider 决策，用户仍可在确认前修改角色。
6. 真实 Provider 的原始 API Key 只能存入 Windows Credential Locker；浏览器、HTTP、SQLite、日志、备份和迁移包只能出现凭据引用。Fake 只能用于 demo/test，不能冒充真实语义生成。
7. 测试必须使用隔离临时根，不能读写真实数据库、上传目录、资料、备份或凭据。不要把示例标题、学科词、BV 号、夹具名称写成业务规则。
8. 每个改动建议都要说明用户交互、请求/响应、持久化、失败与恢复、隐私边界、测试方法和未验证项。先复述事实和缺口，再问一个具体问题；不要擅自扩大范围。

先用不超过 12 条要点复述你确认的项目事实，然后等待任务。
```

## 1. 身份、事实优先级与快照

| 项目 | 当前事实 |
|---|---|
| 产品 | MentorFlow，Windows 本地、单用户、个人学习工作流 |
| 工作目录 | `E:\aiPlay\AI-Knowledge visualization` |
| 分支 | `codex/knowledge-map-teacher-v1` |
| HEAD（仅身份，不代表工作树） | `a16cd2981d6ed8007ddda04ff7195994278b7231` |
| 版本 manifest | `mentorflow-version.json`：`1.1.0`，channel `stable` |
| 工作树 | `DIRTY=true`；有大量已修改、删除和未跟踪文件 |
| 本文日期 | 2026-08-24；以后使用时仍须重新核对本地源码 |

冲突判断顺序：

1. 当前用户的明确指令；
2. 当前本地源码、schema、数据库和实际运行证据；
3. 本文；
4. 当前 ADR/安全与数据边界文档；
5. README 和脚本说明；
6. 带日期的验收报告、交接文档、Git 历史和远端。

“已实现”只表示代码/契约存在；“已验证”只表示本轮列出的命令或界面证据确实通过；“可发布”还需要独立的 Windows 生命周期、网络和发布核查。本文不声称远端 GitHub Release 已发布，也不声称真实 Provider 或完整 Windows 升级流程已经验证。

## 2. 产品边界和主要链路

一句话：用户提供目标和资料，系统安全导入并切成证据 chunk，规划助手给出可审核资料候选；用户选择资料，首个选择可由 UI 默认成为纲领资料并可再调整为明确的 anchor/support；系统生成 staged 知识地图和待审核项，用户显式激活后才可预览/激活学习计划，课堂按证据和学习状态逐步教学、评估、记录掌握与复习。

产品不是：

- 没有资料也能凭空生成整套课程的聊天机器人；
- 只画节点而不连接每日任务、课堂和掌握度的图谱；
- 自动替用户决定资料、纲领、冲突或地图版本的黑箱；
- 账号制、云端空间、组织协作或多租户 SaaS；
- 当前正式支持的视频、视频合集、音频、字幕或转录规划系统。

端到端概览：

```text
用户目标/时间/验收标准
          │
          ▼
安全导入：文本/URL/文件/安全 ZIP ──► 转换注册表 ──► chunk、页码、hash、revision
          │                                      │
          └──────────────► 规划助手（真实 Provider 语义 + 代码硬门）
                                           │
                         候选池（3 卡页）──┴──用户 select/anchor/support/reject
                                           │
                                           ▼
                             receipt（学习条件 + role scope）
                                           │
                                           ▼
                    知识地图 job → staged revision → review candidates
                                           │                 │
                                  source fingerprint      用户处理 blocker
                                           ▼                 ▼
                         显式 activate（last-good 保留）──► 计划 preview
                                                               │
                                                               ▼
                                     显式 plan activate → execution tasks
                                                               │
                                                               ▼
             TeacherTurnV1 状态机：讲解 → 示例 → 作答 → 评估/补救 → 完成或 blocked
                    │                       │                         │
                    ├─ evidence/citations ├─ mastery/mistake/review └─ replan/review
                    └─ free_chat（独立，不改变 mastery）
```

## 3. 技术栈和进程架构

| 层 | 当前实现 | 边界/事实 |
|---|---|---|
| 语言/桌面运行时 | 当前 `.venv` 为 Python `3.13.14`；PySide6 `6.11.1`/Qt 6 | 原生窗口、导航、表单、资料、地图、计划、课堂、设置、迁移/备份；版本是本机当前虚拟环境快照，不等于最低兼容版本声明 |
| 本地服务 | FastAPI `0.139.2` + Uvicorn `0.51.0` + httpx `0.28.1` | Desktop `LocalService` 先绑定随机端口，仅 `127.0.0.1`；健康检查要求 `{"status":"ok","service":"MentorFlow"}` |
| 桌面 HTTP 客户端 | `desktop/api.py` `DesktopApi` | loopback 请求；`session_token` 保持空值，不读取/写入/发送 cookie |
| Web 表面 | React `19.2.7` + TypeScript `7.0.2` + Vite `8.1.4` + Zustand `5.0.14` + React Router `7.18.2` | `BrowserRouter` 外壳存在，主导航实际由 `App` tab 状态和 localStorage 驱动 |
| Web 子工具 | 隔离 `QWebEngineView` | Live2D/复杂图形/部分旧工具；profile 禁止持久 cookie，外链由 Qt 处理 |
| 后端 ORM/Schema | SQLAlchemy `2.0.51` + Alembic `1.18.5` + Pydantic `2.13.4` | canonical active domains 和 planning/map/plan 表；SQLite |
| 兼容存储 | 原生 sqlite3 | `app.db` 的 legacy/application 路由、检索和旧计划/会话 |
| 转换/检索 | PyMuPDF、python-docx、BeautifulSoup、defusedxml、Pillow、RapidOCR/ONNX Runtime、jieba、rank-bm25、DDGS 等 | 文本、Markdown、HTML、PDF、DOCX、PPTX、XLSX、EPUB、结构化数据、代码、OCR 图片、安全 ZIP 由 adapter registry 判定；低层遗留媒体 adapter 不等于 planning 支持 |
| Provider | Fake、OpenAI-compatible/DeepSeek、Gemini、Anthropic adapter | 真实连接必须 credential ref + Base URL + model + 显式测试；JSON/Pydantic 结构化输出 |
| 图形/教师舞台 | React Flow `12.11.3`、Dagre `3.1.1`、Pixi `8.13.1`、Three/VRM、Live2D engine、Spine、Phaser | 图谱/教师舞台为渲染能力，不改变学习状态和资料事实；第三方运行时和模型许可另见风险章节 |
| 测试/打包 | pytest 8 范围、Vitest `4.1.10`、Playwright `1.61.1`、PyInstaller spec、NSIS | focused 测试、浏览器 E2E、冻结目录和 Windows 离线安装包；没有持续 CI 通过声明 |

开发模式的 Python 默认 DB 是仓库 `data/app.db` 和 `data/mentorflow_v2.db`；桌面/安装运行会先选择应用数据根，默认 `E:\MentorFlowData`（若 E: 不可用则使用 `%LOCALAPPDATA%\MentorFlow`），实际 DB 位于该根下的 `data\app.db` 与 `data\mentorflow_v2.db`，上传和运行缓存也位于 `data\` 子树。测试脚本应改到 `E:\Codex\Temp` 隔离目录。

桌面进程边界：

```text
MentorFlow.exe / desktop_entry.py
        │
        ├─ PySide6 MainWindow（原生导航、资料、地图、计划、课堂、系统设置）
        │      └─ 隔离 QWebEngineView ──► http://127.0.0.1:<ephemeral>/?tool=...
        │
        └─ LocalService（同机子线程/本地服务）
               └─ Uvicorn + FastAPI，随机 loopback 端口
                      ├─ active/generic/legacy API routers
                      ├─ app.db + mentorflow_v2.db + active_* schema
                      ├─ Windows Credential Locker（只用 credential ref 取 key）
                      └─ 用户明确授权后，才可能出站到 Provider/公开资料 URL/GitHub update
```

## 4. 代码地图与维护风险

主要入口：

- `backend/app/main.py`：legacy/application 路由、工作区/资料/目标/旧计划/Provider/检索/冲突/静态资源，并注册 active teaching、generic knowledge 等 router。
- `backend/app/active_teaching.py`：当前课堂、active materials、receipt、知识地图/计划兼容边界、TeacherTurnV1 状态和附件/语音/复习/重排；当前是超大模块。
- `backend/app/generic_knowledge_api.py`：规划助手、候选池、material capability、知识地图 v1 job/revision/candidate、canonical mastery plan。
- `backend/app/planning_resource_pool.py`：候选池 durable rows、page cursor、revision、幂等和选择状态。
- `backend/app/knowledge_map_scope.py`、`knowledge_map_v1.py`：anchor/support scope、source fingerprint、证据和图质量门。
- `backend/app/conversion_adapters.py`：唯一 capability registry；`ingestion_secure.py`、`archive_import.py`、`url_fetcher_secure.py` 实现输入边界。
- `backend/app/provider_runtime.py`、`provider_service.py`、`credential_store.py`、`provider_url_policy.py`：Provider session、适配器、keyring 和 URL policy。
- `backend/app/orm.py`、`db.py`、`database_convergence.py`：两套 DB 连接和只读对账。
- `frontend/src/App.tsx`：tab 外壳、工作区选择、旧/兼容页面、Provider 连接和本地草稿。
- `frontend/src/features/active-teaching/ActiveTeachingChainView.tsx`：当前核心链 UI（资料→候选→地图→计划→课堂衔接）。
- `frontend/src/features/planning/PlanningCandidatePool.tsx`：三卡候选池和批量选择。
- `desktop/main_window.py`、`desktop/materials_page.py`、`desktop/plans_page.py`、`desktop/knowledge_map_view.py`、`desktop/classroom_page.py`、`desktop/system_page.py`：原生桌面主要页面。

数量级（当前脏树会变动）：`active_teaching.py` 约 1.2 万行，`generic_knowledge_api.py` 约 1.1 万行，`main.py` 约 3.4–3.9 千行，`ActiveTeachingChainView.tsx` 约 6.3–6.5 千行，`App.tsx` 约 2.5 千行，`desktop/main_window.py` 约 3.0–3.2 千行。它们是当前维护和回归风险事实，不是拆分计划。

源码还保留 expedition、story/campaign、KKS/Spine、Live2D、voice、旧 `video_collection*`/转录文件和很多兼容路由。它们可以被测试或旧入口引用，但不应自动升级为本轮 canonical 学习资料合同。

## 5. 数据模型、持久化和版本边界

### 5.1 两个 SQLite 层

| DB | 当前职责 | 典型表/对象 | 当前主写入 |
|---|---|---|---|
| `app.db` | legacy/application 兼容面 | `workspaces`、`sources`、`chunks`、`goals`、`assessments`、`plans`、`study_days`、`tasks`、`sessions`、`jobs`、archive previews | legacy 主表仍是 workspace/source/goal/plan/session 镜像的 primary |
| `mentorflow_v2.db` | SQLAlchemy/Alembic canonical active domains | `orm_*` workspace/source/goal、map revisions/jobs/review candidates、plan activation/execution/mastery、learning runs、provider metadata 等 | map、active plan、learning run 和新 canonical 对象以 ORM 为主 |
| active teaching ad-hoc schema | `active_teaching.py` 创建/维护的课堂表 | `active_materials`、`active_receipts`、`active_knowledge_*`、`active_teaching_sessions/messages/attachments`、`active_replan_history` | 课堂 reducer 和兼容 active route 直接读写 |

`database_convergence.py` 只读比较 legacy/ORM 的约定字段、计数和 issue；它是诊断/审计，不是原子迁移，也不提供跨 DB 事务。一个请求可能先写一层、再写另一层；进程崩溃时需要由状态、revision、last-good 和恢复工具判断，而不能假设跨库全有或全无。

### 5.2 版本和证据字段

- source 有 content hash、原始文件/URL信息、转换 adapter/version、chunk 和页/段落信息；源内容变化应产生新 revision/fingerprint。
- planning pool 有 `planning_session_id`、整数 `revision`、候选稳定 ID、`selection_state`、retained、event/request 幂等记录和 cursor。
- map revision 保存 `anchor_material_id`、support 列表、role provenance、source fingerprints/revisions、Provider payload、状态和 pending review candidates。
- plan preview/activation 保存 map revision、任务快照、计划 revision、activation audit 和 current/last-good 关系；执行任务按知识点/日期/阶段保存 status、分钟和 completion evidence。
- teaching session 保存完整 transcript、TeacherTurnV1 state、citations、attachments、当前 task/point、remediation、mastery/review/replan 记录。
- 主题、QSettings、本地草稿和 tab 记忆是 UI 偏好，不是学习事实；不会改变 Provider、教师、session 或 mastery。

## 6. 资料能力和安全导入矩阵

能力报告的唯一来源是 `ConversionRegistry().capability_report()`，不是标题、扩展名猜测或测试夹具。健康依赖缺失时报告 unavailable；视频 adapter 显式 rejected。

| 输入 | canonical planning 当前状态 | 处理/证据 |
|---|---|---|
| 纯文本/TXT、Markdown、HTML | 支持 | 文本 chunk、标题和 hash；HTML 去脚本/样式/导航等 |
| PDF | 支持（依赖可用） | 页码、文本；必要时 PDF OCR，输出页/框坐标和警告 |
| DOCX/PPTX/XLSX/EPUB | 支持（依赖/结构有效） | Office/EPUB XML 安全解析，章节/幻灯片/表格等 part |
| CSV/TSV、JSON/JSONL、XML、YAML | 支持 | 结构化路径（如 `$/row/…`）和安全 XML；不是把样例字段当业务规则 |
| 代码文件 | 支持 | `CodeAdapter`，保留语言/文件边界；不会自动执行代码 |
| PNG/JPEG/WEBP 等 OCR 图片 | 支持（RapidOCR 可用时） | OCR line chunk；无文字或依赖缺失则明确失败 |
| ZIP | 支持安全预览后导入 | 用户勾选成员；文档/代码文本解析和去重；内部引用保留 |
| 音频、字幕、转录、视频、视频合集/播放列表 | **canonical planning 不支持** | 规划池和 URL 资格层拒绝/过滤，稳定错误包括 `VIDEO_UNSUPPORTED_IN_V1`、`VIDEO_MATERIAL_DISABLED`、`AUDIO_MATERIAL_DISABLED`、`SUBTITLE_MATERIAL_DISABLED`；源码中低层 adapter/旧路由仅作遗留兼容，不能进入正式 planning/map handoff |

输入限制和保护：

- 普通 upload 走 chunked reader，拒绝超过 per-file/aggregate 上限；当前 ZIP 契约为上传 100 MB、解压总计 500 MB、单成员 50 MB、2000 成员、深度 12、单文件压缩比 100:1、整体压缩比 50:1。
- ZIP 拒绝 Zip Slip、绝对路径、符号链接/reparse、设备节点、加密 ZIP、可执行/不可读二进制和 ZIP bomb；预览有过期 token、archive hash/size/mtime 复核。
- 本地路径要求安全根、父链和文件身份；写入使用 no-follow/exclusive/create-new 等检查，防止 TOCTOU、symlink 和替换文件。
- URL 只允许 HTTP/HTTPS；公开资料抓取拒绝 localhost、loopback、保留/私网 DNS（Provider Base URL 是另一个策略，允许本地模型但禁止 query、fragment、userinfo）；最多 4 次无自动 redirect 的逐跳校验，正文/二进制有大小限制，content-type 必须在文档白名单。
- 失败保存错误 code/message 和可重试性；失败成员不应让安全 ZIP 的其他成员越界，空 ZIP 或无可读成员明确失败。

## 7. 规划助手和候选池

`POST /api/active-teaching/workspaces/{workspace_id}/planning-assistant` 是规划对话，不直接写入 source、map、plan 或 mastery。真实 Provider 负责目标解释、已知事实、检索 query 和候选语义；服务器负责 direct user reference、主题锚点、notes/docs-only 过滤、稳定身份、安全/relevance/完整性检查和 durable pool。

### 7.1 候选页面合同

- 默认 `page_size=3`，界面每页显示三张卡；每张卡可以是用户精确引用或系统发现的文档候选，精确引用也占一张卡。
- 候选有 stable identity（规范 URL、upload/source/hash/direct identifier 或完整 bounded payload hash），不能只用 title 去重。
- explicit `pool_action` 的 `page/list/refresh` 是只读；`select`、`set_anchor`、`support`、`deselect`、`clear_anchor`、`reject`、`undo` 等动作由服务端在 revision 下持久化，并写 event/idempotency。
- `anchor` 只能有一个；`support` 是辅助证据；普通 selected/retained 仍需用户决定是否纳入材料；拒绝和撤销不会被下一页恢复。
- `fill_page`：当前活动合格候选已经覆盖一个完整 3 卡边界时返回现有页，不调用 Provider；不足时只补当前页空位。
- `add_page`：必须提供 `selected_material_count >= page_size`；target 是**下一页边界**，例如 page size 3 时选 3→target 6，选 4→target 9，选 7→target 12。服务端取整并限制上限，不能用浮动 count 绕过约束。
- request 带 `expected_revision`/`client_request_id` 时，旧 revision 返回 `stale_revision`/`idempotency_conflict`；旧 cursor 返回 `stale_cursor`。恢复方式是刷新池页和当前 revision，再重放用户明确动作，不能盲目覆盖。
- Provider candidate review 只能在已提供的 candidate ID 中逐项 `qualified/rejected`，不能产生新 URL/title；超时或无效 JSON 不会自动把候选变成已选。

### 7.2 用户选择如何进入正式资料

用户确认候选后走 upload/URL/material 资料流程，转换生成 immutable evidence chunks。规划助手的候选输出本身不是资料、不是证据、也不应直接写入地图。用户可以撤销/归档未引用资料；被 map/plan/session 引用的资料物理删除会被阻止或要求显式安全条件。

## 8. 知识地图 v1：scope、staged、review、activate

1. 用户在 receipt 中确认学习条件和资料范围；`knowledge_map_scope.v1` 最终要求单一 `anchor_material_id`，其余为 `supporting_material_ids`，保存 selected_by/selected_at、role provenance、source revision/fingerprint 和 policy。UI 会把首个已选资料显示为默认 anchor；legacy flat `material_ids` 未提供角色时，后端也会把第一项归一为 anchor（`selected_by=legacy_default`）。用户可以在确认前显式改设 anchor/support。
2. `POST /api/active-teaching/workspaces/{id}/knowledge-map/jobs`（202）创建异步 job；Provider 必须为已验证真实 Provider 才能做新的语义图生成。未验证 Provider、无资料或 unsupported media 直接阻断，不创建结构兜底课程。
3. 代码校验 Provider JSON/Pydantic schema、theme→concept `contains` 结构、evidence segment、关系类型、孤立比例、来源覆盖和 contradiction/scope 候选；代码不替 Provider 发明主题、关系或证据。
4. 结果先为 `staged`/`ready_with_review` revision，不等于当前地图。`GET .../knowledge-map/revisions/{revision}` 可查看 graph、diff、source fingerprint 和 blockers。
5. 用户在 `GET .../knowledge-map/candidates` 查看 `scope_conflict`、`contradiction`、`material_disposition` 等候选；单个或批量 decision 写审核理由和 revision。
6. `POST .../knowledge-map/revisions/{revision}/activate` 要求 expected current revision（若提供），再验证 source ready、scope union 和 fingerprint 未变；任何变更返回 `KNOWLEDGE_SOURCE_CHANGED`/stale，不静默激活。
7. 激活是显式的 current promotion；旧 current 保留为 last-good/历史 revision。目标 revision 不可激活或 blocker 未处理时，当前 last-good 不被破坏。

旧 `active_knowledge_*` 只读地图可能返回 `legacy_unverified`，不应当被当作新计划输入；要用新 v1 job 重新生成并通过质量门。

## 9. 计划、预览、激活和执行

计划同样不是 Provider 一次输出后立即生效：

```text
active map + receipt + capacity
        │ POST .../mastery-plans/preview 或 /plans/generate
        ▼
preview/draft：日历、任务、分钟、review/final buffer、缺口、blockers、extension draft
        │ 用户确认 / expected map revision
        ▼
POST .../mastery-plans/{id}/activate
        ▼
current plan + immutable execution task snapshots + activation audit
```

计划使用目标、deadline、日容量、每周可用日、不可用日期、当前级别、练习/验收标准、review rounds 和 material evidence；不写死学科或课程名。`activation_blockers`、unscheduled minutes、source/map revision mismatch 会阻止激活。`replan`/material-change 先给 preview，保存 before/after snapshot、reason 和 history；不会把旧 completion/mastery 当成新证据。

执行任务由 `status=planned/in_progress/completed/…`、task type（initial learning/review 等）、phase、分钟和 evidence 驱动。完成任务后写 mastery event/progress；仅看 UI 按钮、空回答或自由聊天不能宣称掌握。

## 10. TeacherTurnV1 课堂状态机

开始课堂：`POST /api/active-teaching/workspaces/{id}/sessions/start`，读取 current plan/task、evidence 和 receipt，创建 session/transcript。每一步通过 `POST /api/active-teaching/sessions/{session_id}/advance`，后端 reducer 固定 route、当前 point、next action、引用和评分，Provider 只负责受边界约束的表达层。

主要状态（字段可能同时存在于 session state 和 `teacher_turn_v1.state`）：

```text
opening / knowledge_briefing
        ▼
waiting_for_answer ──正确/有证据──► guided_practice / assessment
        │                                  │
        ├─提问/要例子──────────────► 保留原位置的解释回合
        └─错误────────────────────► targeted_correction
                                             │
                         达到补救上限仍失败 ─► blocked（掌握度不变）
                                             │
                         合格 evidence ─────► task completed → next point
                                                             │
                                                             └─全 route 完成→review
```

- 回答经过观察内容、规则/Provider evaluator 和 acceptance method。`verify_completion` 走可验证评估；`force_complete` 是学习者显式自我声明的覆盖路径，可以推进当前任务，但会记录为 `learner_override`/self-attested、`provider_verified=false`，不能冒充独立作答或 Provider 已验证证据。
- 失败回答进入 targeted correction/alternate example/prerequisite；达到策略上限进入 `blocked`，保留 unresolved confusion，可稍后补资料或 replan。
- `GET /sessions/{id}` 返回可恢复 transcript、当前任务、引用、附件、teacher presentation state、progress 和 review 队列；重启后从 durable state 继续。
- `POST /sessions/{id}/reviews/{review_id}/start|submit` 处理间隔复习；`POST /sessions/{id}/replan` 只生成/记录重排，不篡改已完成证据。

### 独立自由聊天

`interaction_mode=free_chat` 或 Web 的“自由聊天”走 `_advance_free_chat`：可引用当前上下文和显式附件，但输出是 `free_chat_response`，写入 bounded free-chat history/transcript；`mastery_changed=false`、不推进 task/route、不替代 learning evidence。系统 prompt 只保留 JSON 返回格式和学习状态隔离，不设置聊天主题限制；下一次显式 `learning_evidence` 才回到课堂状态机。没有真实 Provider 时只回显“当前没有连接可用模型”的事实提示，不生成假回复；Fake 不冒充 AI 教师。

### 附件和语音

- `POST /sessions/{id}/attachments` 对文本/图片/代码/文档等附件做大小、类型和 ownership/session 检查，保存附件 ID、hash、原始文件受控路径；分析结果只进入本回合上下文，不自动成为 map source。
- “发送附件”需把 attachment IDs 随 advance 提交，服务器重新校验 session 归属；下载 route 不应只相信客户端 ID（当前 ownership 强度仍是风险项）。
- 语音录音可走本地语音/ASR supervisor 或上传后转写；当前真实麦克风全生命周期和所有动作未本轮完整实机验证，不能把有录音控件写成已验证的实时语音能力。

## 11. 逐界面用户操作响应矩阵

下表描述当前代码的请求、成功状态、持久化影响和失败恢复。按钮名称可能因原生/Web 表面略有差异，语义以 API 和 reducer 为准。

| 界面/操作 | 请求与后端处理 | 成功可见结果 | 持久化/恢复 |
|---|---|---|---|
| 新建任务/工作区 | `POST /api/workspaces` 或 active workspace create；保存 title/目标描述等本地 workspace | 工作区出现在列表，进入资料步骤 | 写 legacy/active 对象；重复点击需使用客户端幂等或刷新，失败不应生成半份任务 |
| 资料上传 | multipart `.../sources/upload` 或 active materials route；chunk reader→hash→registry→chunks | 转换状态、页数/段数、warnings、引用预览 | source/material、revision、chunks 落本地 DB；超限/格式/依赖错误显示 code，可换文件重试 |
| 本地路径 | `.../sources/local-path`/安全 preview；复核 path identity/mtime/hash | 先预览，确认后复制/转换 | 不直接信任路径；TOCTOU/symlink/reparse 失败要求重新预览 |
| URL 导入 | `.../sources/url` 或 active material URL；公开 URL policy、无 redirect 跟随、content type/大小/哈希 | 返回 final URL、标题、转换状态/错误 | 保存快照 hash 和 source；DNS/私网/类型/超限失败可换公开文档 URL；不会把 Provider Base URL policy 混用 |
| ZIP 预览/导入 | `POST .../sources/archive/preview`→用户勾选→`.../archive/import` | 显示成员、大小、类型、拒绝原因；确认后单成员转换 | 预览 token/mtime/hash/过期；Zip Slip、链接、加密或无可读成员安全失败，可重新预览 |
| 规划助手发送 | `POST .../planning-assistant`；Provider JSON 解析、事实合并、查询和候选资格 | reply、known_facts、queries、候选页/警告；不显示“已生成地图” | Provider cache/搜索 cache 可写；source/map/plan 不变；Provider timeout/invalid JSON 保留旧候选，用户可重试 |
| 候选翻页 | planning `pool_action=page/list/refresh`，带 cursor/revision | 三卡页、next/previous cursor、selected/anchor/support 摘要 | 只读；stale cursor/revision 要刷新，不调用 Provider |
| 候选 select/anchor/support/reject | planning pool decide，带 candidate IDs、expected revision、client request ID | 卡片状态、计数、anchor 标记、警告 | event/request/revision durable；幂等重放不重复；冲突返回 409 并要求刷新 |
| `fill_page`/`add_page` | planning assistant structured mode；fill 只补边界缺口，add 要求至少选 3（默认页大小）并目标下一页边界 | 新的 3 卡页或“已有完整页，未重新发现” | 保留旧选择和拒绝；不会因 Provider 重排丢卡 |
| 用户确认学习条件/receipt | `PUT .../workspaces/{id}/receipt` | 条件回执、资料 role/时间/能力选择 | active receipt 保存目标/容量/anchor/support/external policy；修改会让旧 map/plan 需要重算 |
| 生成知识地图 | `POST .../knowledge-map/jobs`（202）+ polling job | job progress、staged graph、质量统计、review candidates | ORM map revision/job/review；无 verified real Provider/证据不足则阻断，不创建 fallback |
| 地图候选审核 | `POST .../knowledge-map/candidates/{id}/decision` 或批量 | blocker 数量、decision reason、diff 更新 | 绑定 map revision；旧 revision/stale decision 拒绝；未解决 blocker 不能激活 |
| 激活地图 | `POST .../knowledge-map/revisions/{revision}/activate` | current map、activation status、last-good 提示 | 检查 source ready + fingerprint + expected current；失败保持旧 current |
| 计划预览 | `POST .../mastery-plans/preview` 或 legacy `/plans/generate` | 日历、任务、分钟、review/final buffer、blockers/extension | preview/draft 保存；不改 current plan、progress、mastery |
| 激活计划 | `POST .../mastery-plans/{id}/activate` | current plan、可执行任务和今日入口 | activation audit + immutable task snapshot；expected map revision 不符则 409 |
| 开始课堂 | `POST .../sessions/start` | transcript opening、当前知识点、教师 stage | active session、state 和第一回合写入；无可执行计划时 409 |
| 课堂“发送回答/继续” | `POST .../sessions/{id}/advance` `learning_evidence` | 教师讲解/反馈、citations、next action、正确/补救/完成状态 | message/TeacherTurnV1/evidence/mastery event；Provider 失败可重试，重复 request 要有幂等边界 |
| 课堂“提问/换例子” | 同一 advance，后端识别 pedagogical request | 回复问题但保留 route/当前点，不跳过验收 | 写 turn/transcript，不推进 mastery；网络失败可重新发送 |
| 自由聊天 | advance `interaction_mode=free_chat` | 独立聊天回合、明确非掌握提示 | 只写 free-chat history/transcript，`mastery_changed=false` |
| 附件 | `POST .../attachments`→advance attachment IDs | 附件列表、可选分析/引用 | session 绑定文件；大小/类型/ownership 失败可重新上传；当前 ID ownership 是风险边界 |
| 语音/录音 | voice status/warm/transcribe routes；本地 supervisor | 转写预览或错误/重试提示 | 临时录音受控目录，转写结果是否计入 evidence 由课堂 action 决定；完整实机链未验证 |
| 掌握/退出课堂 | 学习回答触发 mastery/review；显式“我已掌握”可走 learner self-attested override；“退出课堂”只关闭视图/返回任务 | 下次打开可 resume；完成点进入 review | 退出不删除 transcript/记录；verified evidence 与 learner override 在记录中区分，不能把后者显示成 Provider 已验证 |
| Provider 连接 | 原生“模型连接”写 credential→`POST /api/provider-sessions`→`/test` | host/model/verified、latency、错误分类；不会回显 key | Locker 存 raw key，QSettings/DB 只存 `mfcred_...` ref；进程重启 session 需重建/重验证 |
| 设置/主题 | QSettings `appearance/classroom_theme`、自由聊天提示词、UI 偏好 | Sakura 默认或 16 套 native classroom theme；可预览/取消 | 仅本机视觉/提示词；不改教师角色、Provider、session、progress/draft事实 |
| 备份 | “创建安全备份”调用 SQLite 在线备份、完整性检查、行数报告、hash 清单 | 备份路径和清单；可打开最近备份 | 排除 raw key；恢复前退出程序，先保留当前备份；不能把备份当安装包 |
| 迁移导入/导出 | 本地 migration ZIP、SHA-256 清单、目标目录安全检查 | 新设备可选择新/空目录导入，下一次启动使用该 root | 不覆盖正在使用目录、不合并未知目录；路径/reparse/hash 不符时中止 |
| 更新检查 | 用户点击后才调用 GitHub latest；校验版本、精确 asset 名、官方 `sha256:` digest | 显示当前/最新版本、release 页面；无更新保持当前 | 不下载时不写；下载到 `E:\MentorFlowUpdates\<version>`，`.partial` 完成后原子落地 |
| 更新下载/安装 | 显式确认后下载并 hash verify，启动已校验 `.exe`；不替换运行中的数据 | 用户确认后启动安装包；失败保留当前版本和 staging | 安装包不带用户 DB/secret；当前未做真实升级中断/回滚全生命周期验证 |

## 12. AI/Provider 责任边界

Provider session 是进程内字典 `_sessions` 的 handle，不是持久化账号。真实 provider 建立条件：`provider`、合法无 query/fragment/userinfo 的 HTTP/HTTPS Base URL、model、有效 `credential_ref`，先从 Locker 取 key 并通过 adapter test；HTTP 请求有 timeout、`follow_redirects=False`、错误分类和有限响应。

当前 adapter 族：Fake、OpenAI-compatible（包括 DeepSeek 风格 endpoint）、Gemini、Anthropic。真实 Provider 的语义职责：

- 规划助手：解析用户目标/事实、提出 notes/docs 查询、对已提供候选做语义互补和质量说明；不得创建未提供的 URL/ID。
- 知识地图：按 schema 生成候选 theme/concept/relation/evidence；代码做 Pydantic、证据、scope、冲突和质量门。
- TeacherTurnV1：只表达代码确定的 action、引用、路由和状态；不能自改评分、进度、时间或引用。
- 自由聊天：可自然回应，但显式标为 free-chat，不改 mastery。

代码/应用职责：安全、格式、大小、页数、hash、source fingerprint、scope role、revision、幂等、数据库事务边界、任务路由、评分阈值和隐私。当前尚无统一 tokens/USD/cost、trace ID/SLO/端到端可观测性；`request_id`/latency 只在部分 Provider 元数据中存在，不能当完整账单或性能指标。

## 13. 桌面、视觉和教师舞台

- 默认视觉是 Sakura 粉色；`desktop/classroom_theme.py` 提供 16 套 native classroom theme。主题预览/提交使用 QSettings，仅改颜色、背景、边框、文字、图案、课堂 stage/transcript 样式。
- 课堂 transcript 是主表面；教师舞台是 sidecar/独立 QWebEngine 窗口，可隐藏/恢复，不应挤压对话布局。
- Live2D adapter 有浏览器测试证据证明 BLINK 等部分能力；其余动作、模型资产、许可和实机全链路不能以“已完成”表述。Presentation state（talking/explaining/questioning 等）只影响表达/视觉，不可改变 grading/route/mastery。
- 原生页面负责资料、地图、计划、课堂、设置和数据边界；复杂图谱/Live2D/旧工具可以在隔离 Web 表面运行，不代表产品是远程网页或有云账户。

## 14. 安装包、数据保留和更新

### 14.1 当前 1.1.0 工件

- 路径：`artifacts/windows-installer/MentorFlow-1.1.0-win-x64-Setup.exe`
- 字节数：`648817657`
- SHA-256：`659889B5AE6DFDCFE4B95C02952937A818F6AC011698DB4F5CE58F86F990B792`
- NSIS per-user offline，默认程序目录 `%LOCALAPPDATA%\Programs\MentorFlow`；数据根 `E:\MentorFlowData` 和 `%LOCALAPPDATA%\MentorFlow`。
- 安装/卸载不需要管理员权限；默认卸载保留数据。只有交互式卸载勾选并经过第二次确认，才会对上面两个**精确根**做删除；自定义/导入根不自动删除。静默卸载强制保留数据。程序树遇到 symlink/reparse/device 或枚举异常会 fail-closed。
- 工件未签名（没有 Authenticode 证书）；这是当前事实，不是“已签名发布”。本轮只核对了本地工件/静态合同/包隐私，未验证 GitHub release 已发布、真实用户安装升级、中断恢复或卸载全生命周期。

### 14.2 更新边界

`mentorflow-version.json` 的 update 配置指向 GitHub owner `tonyswift868`、repo `AI-Knowledge-visualization`、精确资产模式 `MentorFlow-{version}-win-x64-Setup.exe`。只有用户点击“检查更新”才联网；latest 必须是非 draft/prerelease、版本高于当前，且 asset 有官方 URL、合理大小和 GitHub `sha256:` digest。下载上限 2 GiB，在 `E:\MentorFlowUpdates\<version>` 中先写 `<asset>.partial` 临时文件，hash 成功后再原子 replace 为最终安装包。应用不替换运行目录和用户数据，须用户确认后启动安装包。

## 15. 当前验证证据（focused，不是 Full Gate）

以下是本轮提供的当前聚焦证据摘要；数字只适用于对应隔离范围和当时工作树，不得外推为所有测试或发布声明：

| 范围 | 结果 |
|---|---|
| W50 scoped backend（active/planning/map/provider/upload 等） | `99 passed, 9 skipped` |
| 前端 active chain | `40/40` |
| 前端 pool + integration + upload | `36/36` |
| 前端生产 build | pass |
| release/security focused suite | `106 passed, 4 skipped` |
| Windows package verifier | pass；包扫描约 `13,454` files，无用户 DB/secret |
| 本地 1.1.0 安装包 hash/大小 | 与第 14 节一致 |

推荐复核入口（必须在隔离临时根执行）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test_core.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
python .\scripts\verify_windows_installer.py --artifact .\artifacts\windows-installer\MentorFlow-1.1.0-win-x64-Setup.exe
python .\scripts\verify_windows_package.py verify --package-root .\release\distributable\MentorFlow --no-smoke
npm.cmd --prefix .\frontend run test -- --run
npm.cmd --prefix .\frontend run build
```

`test.ps1` 是更宽的后端/前端/Playwright/smoke/security 组合；它不是本文列出的 focused 99/106 数字。旧审计报告中出现的 Full Gate 失败数字只代表旧运行，不应改写成当前 focused 通过或当前代码已修复。真实 DeepSeek/网络抓取、全量浏览器、完整 Windows 安装/升级/中断保数和大多数 Live2D 动作本轮均未验证。

## 16. 已知缺口和风险（当前，不是未来方案）

- `active_teaching.py`、`generic_knowledge_api.py` 等巨型模块同时承载 active/canonical/兼容路径，遗留路由和低层媒体代码并存，维护和回归边界复杂。
- `app.db`、`mentorflow_v2.db`、active ad-hoc schema 双层/三层并存；convergence 仅只读对账，跨库写入不是原子事务。
- Provider sessions、部分课堂 session/jobs/cache 是 process-local；服务重启后需从凭据引用/数据库重建，不能把进程 handle 当永久凭据。
- 没有统一 trace、tokens、USD cost、SLO 和端到端 Provider 可观测性。
- 无持续 CI 证据；本文 focused 数字不能替代每次当前工作树的完整门禁。
- Provider URL policy 允许本地模型，公共 URL policy 与之不同；DNS rebinding、代理/解析时序仍是安全审查风险。
- query token 可能在错误配置或外部输入中落入数据库/错误上下文的风险需持续审计；原始 API key 的设计边界仍是 Locker-only。
- loopback API 没有通用 owner authentication；桌面本机信任边界依赖进程/端口/OS 用户，不能当远程安全接口。
- 课堂附件 ID ownership 校验仍偏弱，不能把任意 attachment ID 当作可靠授权。
- 安装包 unsigned；SBOM/第三方 license 清单可能随脏树和依赖漂移，需要以构建时产物为准。
- 没有本轮真实 DeepSeek 请求、公开网络资料、完整 Windows 安装升级/中断/卸载保数生命周期证据。
- Live2D 只有部分动作（包括 BLINK）有浏览器级证据；其余动作、授权资产、实机性能和许可未全验证。
- 音频转换、字幕/转录、旧视频/合集模块和依赖即使在源码/requirements 中存在，也不改变 canonical planning 的 document-only 合同。

## 17. 术语表

| 术语 | 含义 |
|---|---|
| workspace/任务 | 一组学习目标、资料、receipt、地图、计划和课堂记录；不是云账户 |
| material/source/资料 | 用户导入并成功转换的本地/URL快照；有 hash、revision、chunks |
| evidence chunk | 带 source/chunk/page/quote/hash 等身份的不可变证据片段 |
| anchor | 最终唯一的纲领资料，决定地图主题/概念主范围；可由首个选择按默认规则产生，也可由用户显式改设 |
| support | 辅助资料；可由其余已选资料默认归类或由用户显式指定，不能悄悄扩大 anchor scope |
| candidate pool | 规划阶段的 durable 候选卡集合；候选不是正式资料 |
| receipt | 用户确认的学习条件、目标、容量、资料角色和发现策略快照 |
| staged revision | 已生成但未成为 current 的地图/计划版本 |
| last-good | 上一个可用 current 版本；新版本失败不应破坏它 |
| source fingerprint | 资料身份、revision、hash 和 role 的组合摘要，用于防止陈旧激活 |
| TeacherTurnV1 | 课堂回合 schema + 后端 reducer；固定 route/action/state，Provider 只表达 |
| mastery evidence | 由有效回答、评估或明确完成证据产生的掌握事件；自由聊天不属于它 |
| Fake Provider | demo/test 提供者；不代表真实模型调用，也不能用于真实语义生成门 |
| focused evidence | 有明确文件/范围/隔离根的聚焦测试结果；不等于全仓库 Full Gate |

## 18. 权威文件索引

按问题查当前源码，不要只查报告：

- 版本/更新：`mentorflow-version.json`、`scripts/verify_windows_package.py`、`desktop/update_service.py`、`installer/MentorFlow.nsi`
- 后端路由：`backend/app/main.py`、`backend/app/active_teaching.py`、`backend/app/generic_knowledge_api.py`
- 转换/输入安全：`backend/app/conversion_adapters.py`、`ingestion_secure.py`、`archive_import.py`、`url_fetcher_secure.py`、`upload_limits.py`、`safe_xml.py`
- 候选池/scope/map：`planning_resource_pool.py`、`knowledge_map_scope.py`、`knowledge_map_v1.py`、`knowledge_map_service.py`
- Provider/密钥：`provider_runtime.py`、`provider_service.py`、`provider_url_policy.py`、`credential_store.py`
- 存储/迁移：`db.py`、`orm.py`、`database_convergence.py`、`desktop/local_migration.py`、`docs/native-desktop-user-guide.md`
- UI/桌面：`frontend/src/App.tsx`、`frontend/src/features/active-teaching/ActiveTeachingChainView.tsx`、`frontend/src/features/planning/PlanningCandidatePool.tsx`、`desktop/main_window.py`、`desktop/classroom_page.py`、`desktop/system_page.py`
- 验证脚本：`scripts/test_core.ps1`、`scripts/test.ps1`、`scripts/verify_windows_installer.py`、`scripts/verify_windows_package.py`、相关 `backend/tests`/`frontend/src/test`/`desktop/tests`

## 19. 给其他 AI 的评审问题清单

读取源码和真实运行证据后，至少回答：

1. 当前用户操作是否能沿“资料→候选→anchor/support→staged map→activate→plan→课堂”完整恢复？每一步失败后 last-good/旧状态是什么？
2. 哪些端点仍是 legacy，哪些是 canonical active domain？是否有一条请求写两库而没有原子保护？
3. candidate pool 的 revision/cursor/idempotency 是否覆盖翻页、批处理、撤销、刷新和重启？是否可能把同标题不同 URL 合并？
4. map/plan activate 是否检查 expected revision、source fingerprint、pending blockers 和 ready material？
5. Provider 原始 key 是否可能出现在浏览器、URL query、日志、SQLite、错误或迁移包？真实/ fake 是否在每个语义入口明确区分？
6. 文档/结构化/OCR/ZIP 的 capability report 与 URL/upload 过滤是否一致？低层遗留媒体代码是否可能绕过 canonical planning gate？
7. TeacherTurnV1 是否由代码决定 grading/route/mastery，Provider 是否只表达？free_chat 是否确实不改变 mastery？
8. 附件、语音、回放、replan 和退出课堂重启后是否能用 durable ID/状态恢复？当前 ownership 弱点在哪里？
9. 桌面 loopback、QWebEngine profile、QSettings、数据根和迁移是否避免远程访问、持久 cookie、覆写未知目录和真实数据污染？
10. 安装包是否无 DB/secret、hash 可复核、未签名事实明确；卸载默认保数、静默保数、显式删除精确根是否有实机证据？
11. 更新是否严格 opt-in、精确 asset/digest、<= 2 GiB staging、partial 原子完成，并且不替换运行中数据？远端 release 是否真的存在必须另查。
12. 当前测试数字属于哪些文件/隔离根/版本？哪些仍是 focused 或未验证，不能被写成 Full Gate/release/真实 Provider 结论？

本文只记录当前事实和可复核边界；任何未来设计都必须另起任务并明确标为计划，而不能反向修改本文件的已实现状态。

# MentorFlow 通用主动式教学链实现与验证报告

日期：2026-07-25  
当前结论：**通用资料摄取、结构知识地图、完全掌握估时、容量计划、主动补救、同日历复习/补救任务、权威完成报告和来源验证的受限 Python 判题链已实现，并通过 22 项后端测试、171 项前端测试、生产构建、三组 API 黑盒验收和真实 Chromium 端到端验证；真实 DeepSeek 语义抽取/教学仍未现场验证。不得把结构降级结果称为 AI 生成结果。**

## 纠偏说明

LeetCode 面试经典 150 只应是黑盒测试资料，不应进入产品规则。曾经尝试加入的 `hot150-*` 题号、固定 2026-07-25～08-10、第一题专用判题器、Hot150 API 和专用 UI 已全部撤销。当前产品代码不包含课程名称、题号、固定题量、固定日期或题目答案。

当前通用链路是：

```text
上传用户资料
→ 保存原始文件、SHA-256、大小、类型和解析状态
→ 解析成带页/段落/字符范围/hash 的 chunk
→ 保存用户基础、时间条件和默认“完全掌握”目标
→ 有真实 Provider：生成语义知识地图并强制校验 chunk 证据
→ 无真实 Provider：明确使用 structural_fallback，不冒充 AI
→ 为每个知识点估算理解/回忆/引导练习/独立应用/复习/总掌握分钟
→ 按依赖和逐日容量生成计划
→ 系统状态机主动讲解、检测、补救并恢复主路线
→ 有真实 Provider 时仅让模型负责知识抽取、教学表达和来源约束下的 rubric 评分
→ 持久化任务分钟、完成分钟、显式实际学习分钟和掌握证据
```

## 实现范围

### 通用资料摄取

`backend/app/active_teaching.py` 当前支持：

- 文档：PDF、DOCX、Markdown、TXT、HTML；
- 结构化/文本：CSV、JSON、YAML、XML、RTF；
- 源代码：Python、JavaScript/TypeScript、Java、C/C++、Go、Rust、SQL、Shell、PowerShell；
- 压缩 XML 文档：PPTX、XLSX、EPUB。

文本编码按 UTF-8 BOM、UTF-16、GB18030 顺序识别。解析失败、空正文、不支持格式都会明确失败。原始文件按 workspace + SHA-256 去重并持久保存。每个 chunk 保存材料 ID、页码、段落、字符范围、内容 hash 和 quote。

已自动验证：

- UTF-8 Markdown；
- GB18030 TXT；
- JSON；
- 最小 PPTX XML；
- 重复文件去重；
- 不支持格式和损坏文件失败。

未验证：

- 扫描 PDF OCR；
- 图片、音频、视频的内容识别；
- 复杂 XLSX 公式/图表语义；
- 复杂 PPTX 图形、备注和嵌入对象；
- 加密/损坏 Office 文件。

这些范围不能描述为“任意二进制文件都能理解”。

### 通用知识地图

新增：

- `backend/app/generic_knowledge.py`
- `backend/app/generic_knowledge_api.py`

新 API：

- `POST /api/active-teaching/workspaces/{workspace_id}/knowledge-map/generate`
- `GET /api/active-teaching/workspaces/{workspace_id}/knowledge-map`

两种模式：

1. `provider_grounded`
   - 需要已验证的真实 Provider 会话；
   - Provider 只能返回结构化候选；
   - 每个知识点必须引用本次输入的真实 chunk ID；
   - 无来源节点被拦截；
   - 非法依赖、未知节点、自环和循环依赖不会落库；
   - 长资料按 24 chunks 分批后会全局合并规范化同名节点、证据和依赖；
   - 跨批次自环、重复边、悬空边和成环边会被丢弃并返回 warning；
   - Provider 元数据和警告持久化。
2. `structural_fallback`
   - 未提供真实 Provider 时使用；
   - 从文档标题/段落生成可追溯节点；
   - 不把相邻章节伪装成前置依赖；
   - 节点无显式依赖时标记为孤立；
   - 页面明确显示该模式不是 AI 语义抽取。

Fake Provider 被明确拒绝用于语义知识地图，不能冒充 AI。

### 完全掌握时长

每个知识点的 `mastery_estimate` 包含：

- `understand_minutes`
- `recall_minutes`
- `guided_practice_minutes`
- `independent_application_minutes`
- `review_minutes`
- `total_mastery_task_minutes`
- `basis`

这些是标准任务分钟，不是页面停留时间。估算基于资料正文体量、难度、用户当前基础和资料中的显式估时；所有值有上下界并可审计。

### 通用容量计划

新 API：

- `POST /api/active-teaching/workspaces/{workspace_id}/mastery-plans/generate`

计划输入来自回执：

- 默认完全掌握目标（用户可修改）；
- 当前基础；
- 开始/截止日期；
- 可学习星期；
- 常规每日标准任务分钟；
- 额外不可学习日期；
- 特殊日期分钟；
- 学习偏好、备注和资料范围。

计划：

- 使用真实知识点与依赖；
- 基础/简单节点优先于困难节点；
- 每天不超过当天容量；
- 将首学明确拆成 `understand`、`recall`、`guided_practice`、`independent_application` 四类持久化任务；
- 初始计划不创建 review task；review 分钟只计入完全掌握总量和容量可行性预测；
- 只有最终独立应用阶段通过后，才把该知识点写入持久化会话的 `pending_reviews`；
- 阶段任务超过容量时最多按 120 分钟拆段，并保存 phase part 序号；
- 保留完整逐日日历，包括 0 容量日；
- 容量不足时返回未排标准任务分钟，不伪造完成。

新增主动链重排：

- `POST /api/active-teaching/sessions/{session_id}/replan`
- `GET /api/active-teaching/sessions/{session_id}/replan-history`

重排只移动未完成的标准任务，已完成分钟和实际耗时不变；可追加不可学习日期，重新使用逐日容量排入剩余任务，并把 before/after、移动任务 ID 和未排分钟写入历史。

复习与补救现在不是游离队列：每次 +1/+3/+7 复习、失败后的提前复测和“不懂”产生的深化讲解都会创建独立 `active_plan_tasks`，保存来源任务、触发原因、not-before 日期、预计/完成/实际分钟，并进入同一份持久化日历。三次复习的分钟会精确拆分知识点的总复习预算，不会把总预算重复计算三遍。补救插入、复习到期和未完成主线会共同重排；主路线索引只计算 `initial_learning` 任务，动态任务不会错误推进或打乱路线。

### 主动教学与时间

现有持久化状态机继续提供：

```text
explain → example → check_understanding
→ remediation_explain → remediation_example
→ remediation_recheck → resume_main_route
```

用户输入“不懂/不理解/为什么”等会保存路线位置并压入补救栈；补救后恢复。消息和引用写 SQLite，刷新后恢复。

教学文案已移除算法专用的“复杂度、数组边界、写代码”等固定模板。理解检查现在直接使用当前知识点自己的 `acceptance_method`，因此物理、历史、语言或其他资料不会被硬套算法话术。

真实 Provider 的职责被限制为两层：

- `expression_only`：只改写系统已确定的教学草稿，不能改变 action、路线、分钟、掌握度或引用；
- `grounded_rubric_evaluation`：只能依据知识点验收条件和引用原文返回候选评分；是否通过由系统固定阈值 `0.8` 决定。

Fake Provider 不能冒充上述两层。没有真实 Provider 或领域验证器时，“我理解了”和长文本只会保存为 `unverified_recall`，不会完成任务、增加 `completed_task_minutes` 或提高掌握度。

即使真实 Provider 返回高分，系统仍要求回答同时通过“内容足够”和“命中当前知识点”两个确定性预检；极短的“我懂了”不能被宽松 Provider 放行。同一教学会话会绑定首次真实 Provider session，不能在同一证据链里静默更换评分来源。

实际学习分钟已修正为显式客户端活动区间：不再把“距离上次消息的墙钟时间”当作学习时间。未提供显式活动分钟时记 0。非零耗时必须携带唯一 `activity_event_id` 并写入 `active_task_time_events`；同一事件重放不会重复累计。

当前真实 Provider rubric 评分只证明“回答符合资料与验收 rubric”，仍不能代替代码执行、数学符号校验、实验观察、语言口语等领域专用验证器；这些验证器仍是未完成项。

### 持久化 1/3/7 复习执行

新增真实表与 API：

- `active_review_items`：关联最终独立阶段的通过证据、来源任务、到期日、间隔步骤、估时和状态；
- `active_review_attempts`：逐次保存分数、是否独立、提示依赖、标准/完成/实际分钟和下一次到期日；
- `GET /api/active-teaching/sessions/{session_id}/reviews`；
- `POST /api/active-teaching/sessions/{session_id}/reviews/{review_id}/start`；
- `POST /api/active-teaching/sessions/{session_id}/reviews/{review_id}/submit`。

状态迁移为：

```text
最终独立阶段通过
→ +1 天首次复习
→ 独立通过后 +3 天
→ 再次独立通过后 +7 天
→ 第三次延迟独立复测通过后完成复习链
```

提示或引导后即使内容评分通过，也不会记为独立复测；失败不会增加完成分钟或掌握度，并把下次复习提前到次日。启动复习时保存主路线，复习通过后恢复。队列、逐次尝试和路线位置均持久化，前端已提供真实队列、到期日选择、启动和提交操作。

### 来源验证的代码练习与四级提示

新增 `backend/app/code_practice.py`。代码题不能由模型临场编造测试：只有用户上传的 JSON 明确包含 `problem_specs`、至少一组 sample/boundary/hidden 测试、三层提示、参考实现并声明 `reference_status=verified` 时，系统才开始校验。该声明不再直接等于可信：参考实现必须由隔离执行器跑过全部测试，之后系统才落库为 `reference_status=system_verified` 并保存验证器、测试包哈希、通过数和时间戳。普通学习资料没有可靠测试包时，页面明确显示“不可判题”。

当前首版执行范围：

- Python；
- 可编辑代码区和持久化草稿 revision；
- “运行”只执行样例与边界测试；
- “提交”执行全部测试，包括隐藏测试；
- 错误答案、运行异常、语法错误和超时分别返回；
- 隐藏测试结果不返回输入、期望值或实际值；
- 有隐藏测试时不向提交者返回捕获输出，异常只返回通用错误类型，防止通过 print/异常文本探测隐藏输入；
- 每次运行/提交要求 `idempotency_key`，重放不会重复执行；
- 同一幂等键若更换题目、模式或代码哈希会返回 409，不会错误复用旧判定；
- 代码 hash、测试包 hash、通过数量、隐藏测试汇总、耗时和提示层级持久化；
- 四级提示由上传资料提供，第四级完整参考实现必须二次确认；
- 只有零提示且全部测试通过才记为独立代码掌握；提示后通过会要求无提示重试。

执行器采用 AST 禁止导入、文件操作、动态执行和双下划线逃逸，再使用 `python -I -S` 的独立临时目录进程、2 秒超时和输出上限。它能满足本地可信学习代码验收，但不是生产级对抗沙箱；没有 Windows AppContainer、容器、独立低权账号或硬内存限制，不能用于执行不可信互联网代码。

## 真实测试结果

后端通用管线：

```text
.\.venv\Scripts\python.exe -m pytest \
  backend/tests/test_generic_knowledge_merge.py \
  backend/tests/test_generic_knowledge_pipeline.py \
  backend/tests/test_active_teaching_chain.py \
  backend/tests/test_active_teaching_state_machine.py -q

22 passed, 1 warning
```

其中通用专项单独执行：

```text
.\.venv\Scripts\python.exe -m pytest \
  backend/tests/test_generic_knowledge_pipeline.py -q

6 passed, 1 warning
```

覆盖：

- Markdown → 3 个资料驱动知识点；
- 完全掌握分项分钟求和；
- 逐日容量、不可用日和特殊短时日；
- GB18030、JSON、PPTX；
- Fake Provider 拒绝；
- 模拟已验证真实 Provider；
- Provider 无来源节点拦截；
- Provider 元数据与来源证据。
- 25 chunks 跨批次同名节点与证据全局合并；
- 重生成知识图时清理旧冲突/计划/会话，但保留资料和回执；
- Provider 只能改写表达、系统仍控制补救路线；
- 无真实 Provider 时自报理解不会伪造掌握通过；
- 模拟真实 Provider rubric 达到系统阈值后才完成任务。
- 宽松 Provider 对“我懂了”返回 1.0 仍不能绕过系统预检；
- Provider session 中途替换被 409 拒绝；
- 初始计划没有 review task，首学任务之和与预计复习分钟分开；
- 未完成任务重排保持 estimated/actual 不变并持久化历史；
- activity event 重放不重复累计实际分钟。

前端：

```text
cd frontend
npm.cmd test -- --run src/test/active_teaching_chain.test.tsx
=> 5 passed

npm.cmd test -- --run
=> 16 files, 171 passed

npm.cmd run build
=> passed
```

真实浏览器自动验收命令：

```text
cd frontend
npx.cmd playwright test e2e/active_teaching_chain.spec.ts --config=playwright.active.config.ts
=> 1 passed (14.4s)，Chromium，console error 0
```

该用例在全新随机 workspace 中验证：

- 选择任意 Markdown 学习资料后只点击一次“上传并自动生成知识地图与计划”；
- 网络链真实执行 `materials → receipt → knowledge-map → mastery-plan`；
- 显示原文件、SHA-256、chunk、`structural_fallback`、资料决定的知识点、五段掌握分钟、预计真实耗时范围和完整日历；
- AI 教师引用原文主动开场；
- 输入“不懂”后进入 `remediation_explain`，补救栈为 1，并显示 `remediation_inserted` 重排记录；
- 请求更简单示例后进入 `remediation_example`，理解后以 `resume_main_route` 恢复；
- 刷新后 workspace、主线位置、补救状态和完成报告仍存在；
- 问资料无法支持的量子问题时明确回答“资料中无法确认”；
- 追加上传结构化 JSON 测试包，系统实际校验参考实现后导入通用 Python 题；
- 错误实现 `return 0` 显示 `wrong_answer`，修正为 `return sum(values)` 后显示 `accepted`、4/4、隐藏测试 1/1；
- 页面不显示隐藏输入、期望值或实际值；
- 连续请求四级提示时，第四级必须经过浏览器确认才显示完整参考实现；
- 草稿、接受结果和“已由后续独立通过解决”的错题记录刷新后仍存在；
- 权威完成报告仍诚实显示“仍在学习或验证中”，没有因一题通过伪报课程完成。

另用隔离 TestClient 对非算法领域做黑盒验证：

- 物理 Markdown：2 chunks → 2 nodes，总掌握估时 82 分钟；
- 工业革命历史 TXT：2 chunks → 2 nodes，总掌握估时 92 分钟；
- 节点、验收、估时和教师提示扫描 18 个中英文算法专用词，两组命中均为空。

真实 DeepSeek 未配置，因此该次浏览器验收只验证了诚实结构降级路径，**没有验证实时 AI 语义抽取**。

### Hot 150 通用黑盒验收

`demo/leetcode_top_interview_150_zh.md` 只作为上传输入，使用
`scripts/hot150_blackbox_acceptance.py` 生成真实结果：

```text
workspace: hot150-blackbox-aae3ecd08a
日期: 2026-07-25 至 2026-08-10
原始文件 chunks: 150
知识点: 150，规范化名称不重复
抽取模式: structural_fallback
日历: 17 个自然日，13 个有效学习日
固定休息日: 2026-07-26、2026-08-02、2026-08-09
额外不可用日: 2026-07-29
短时日: 2026-08-01，容量 120，实际计划 120
完全掌握总量: 9355 标准任务分钟
首学: 7957
预计复习: 1398
可用容量: 8760
未容纳: 595
可行性: false
```

系统没有为了显得可行而压缩或丢弃任务，而是明确报告 595 分钟缺口。150 个节点全部有上传资料证据；初始任务全为首学阶段，未学内容没有提前进入复习。由于没有真实 Provider，结构降级没有声称推断出语义先修边，当前边数为 0。

可复现 API 验收：

```text
.\.venv\Scripts\python.exe scripts\generic_active_teaching_acceptance.py http://127.0.0.1:8010

workspace: generic-acceptance-ad8902fc48
material SHA-256: ca25b632e594bc5b2911d336dc7f420a9d0cc86bd2b98ee0b8725e626222aeb3
chunks: 3
knowledge points: 3
total mastery task minutes: 128
calendar days: 15
available capacity minutes: 1200
unscheduled task minutes: 0
remediation depth after refresh: 1
actual elapsed minutes: 2.5
10/10 acceptance assertions: true
```

真实代码验收：

```text
.\.venv\Scripts\python.exe scripts\generic_code_practice_acceptance.py http://127.0.0.1:8010

workspace: generic-code-acceptance-2ddb769310
参考实现系统验证: system_verified，4/4
错误代码: wrong_answer
修正代码: accepted
全部测试: 4/4
隐藏测试: 1/1
隐藏数据未泄漏: true
刷新后提交仍存在: true
来源证据存在: true
```

## 可复现样例

- `demo/generic_physics_learning_material.md`
- `demo/generic_physics_receipt.json`
- `demo/generic_python_learning_material.md`
- `demo/generic_python_problem_spec.json`
- `demo/leetcode_top_interview_150_zh.md`（仅作为大资料黑盒样例，不进入产品规则）

## 未完成项与风险

1. 真实 DeepSeek 调用未验证；只有模拟真实 Provider 的结构/证据门禁测试。
2. 结构降级模式不能可靠推断语义前置依赖，这是故意的诚实边界。
3. Provider 跨批次目前按规范化名称合并；真正的语义同义词（不同名称）仍依赖 Provider 自身或后续词汇归一化服务。
4. Provider rubric 不能证明数学、实验和口语等领域的“完全掌握”；代码目前只有来源验证的 Python 函数测试适配器。
5. 代码独立通过已能形成掌握证据和错题解决记录，但尚未把“该代码题对应的独立应用 phase”自动闭环为完成并立即创建复习任务；目前仍需教学状态机完成该 phase。
6. 当前重排按完整 phase task 滚动，不能把一个既有 task 再细拆到比它更短的特殊学习日。
7. 受限 Python 执行器只支持简单同步函数，不支持 LeetCode `class Solution`、链表/树节点适配器、多语言编译，也不是生产级对抗沙箱。
8. 图片/扫描件 OCR、音视频转写未接入。
9. Office 复杂对象的语义解析未验证。
10. Provider 引用门禁能证明引用的 chunk 存在，但尚无独立 NLI/事实蕴含校验器证明每个生成断言都被 quote 充分支持。
11. 当前脏工作树还有本任务开始前的 KKS/教师模型等大量修改；本任务未触碰这些模块。
12. 只运行了本链路 22 项后端测试，未运行整个脏工作树的全仓库后端套件，不能声称其他模块无回归。

## 禁止的完成声明

在真实 Provider、跨领域验收器、OCR/音视频和全仓库回归未验证前，不得声明：

- “任意文件都能被 AI 完全理解”；
- “所有知识依赖都是语义正确的”；
- “系统能证明用户已完全掌握任意领域”；
- “完整产品已经全部完成”。

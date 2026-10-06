# 源代码模拟流程工具

本工具只在源代码工位运行，不启动主界面、不改系统日期，不操作安装版或学生账号数据。创建真实 `AppServices`、`AppBridge`，通过真实 `plan.*` / `classroom.*` 注册入口驱动；Qt 只作隐藏的事件泵与 PDF 字体绘制。**从填表、聊天、完整资料推荐、用户导入和结构确认开始，没有直接塞计划、资料或题库的捷径。**

离线模型与假网页引用现有 `tests/refactor/integration38` 的明确标注合成夹具，资料抓取/提取、地图、准备、题库、存储、账本、课堂、报告与 PDF 都走真实代码。它证明接线和状态能走通，不证明教学质量。填表后不限轮数聊需求，真实 `preview` 阶段展示“需求摘要 + 大致模块”；脚本用户发送 `plan.say` 的“确认并找资料”后才推荐整体资料链接，也可先提出修改、看新版预览再确认。推荐不提取正文、不评分、不判覆盖。用户实际上传或贴网址后先按目录调整结构，用户确认后才填知识点。预览与澄清共用 sufficiency 用途，但假模型按实际系统提示区分两种响应，不将预览算成一次澄清。

第四十四批已移除全局资料层替身和 quick 题查询投影。完整场景由真实 `prepare_point/prepare_module` 定位假网页原文，程序生成 quick，真实 `pick_question/mark_used` 选择题目并持久化使用记录；计分题是原文选择题，不依赖模拟判定员伪造答对。没有直接塞完整场景的题库。其他隔离单元测试的 `seed_bank` 使用 `store.add_bank_questions` 写入真实 bank/quick 字段。

## 开始一个完整离线场景

在源代码工位 PowerShell 运行（示例目录须用没有学生数据的新目录）：

```powershell
cd D:\MentorFlow-office\server\repo
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& D:\MentorFlow\.venv\Scripts\python.exe -B -m tests.refactor.simulation.run --root D:\MentorFlow\.local\test-tmp\fix44-integrate\offline --to free
```

默认脚本用户从屏幕选择动作，固定夹具答案；覆盖追问、答错再答、模块检测与重测、第二模块直接考、第三模块“我会了”、退出并重建服务、跨日、最终检验、学习报告、Markdown/PDF 导出、3题自由检验。任何表单或课堂错误停止，不自动写正确答案或修改进度来绕过失败。

默认测试（不会运行真实模型）：

```powershell
& D:\MentorFlow\.venv\Scripts\python.exe -B -m pytest tests/refactor/simulation -q -p no:cacheprovider
```

## 按段体验与续跑

`--to`：`form`、`dialogue`、`sources`、`map`、`estimate`、`generated`、`classroom`、`final`、`report`、`free`。

- `map` 停在取得真实知识地图后，尚未估算/生成。
- `sources` 停在完整资料链接推荐后；推荐链接不是已导入或已采用的正文。
- `generated` 停在首模块准备完成、计划未开始时。
- `classroom` 停在模块学习结束、最终检验尚未开始时。
- `final` 停在最终检验通过，不作报告与自由检验。
- `report` 取得报告并由真实导出桥保存 Markdown/PDF，不作自由检验。
- `dialogue` 停在待确认的大致模块预览，不确认、不搜索；同目录续到 `sources` 才发送确认。`--dialogue-rounds 3` 可更早停，填表算第1轮；正常继续时预览确认也算一次用户聊天动作。产品若在这之前自动搜索，真实聊天段禁止该用途并止损，不偷做资料检索。

例如只到地图，再从同一隔离存储续到报告：

```powershell
& D:\MentorFlow\.venv\Scripts\python.exe -B -m tests.refactor.simulation.run --root D:\MentorFlow\.local\test-tmp\fix44-integrate\map-run --to map
& D:\MentorFlow\.venv\Scripts\python.exe -B -m tests.refactor.simulation.run --root D:\MentorFlow\.local\test-tmp\fix44-integrate\map-run --from-stage map --to report
```

只跑第3天必须已通过本工具建计划，并存有 `generated` 或 `classroom` 检查点；`generated` 检查点会通过真实桥开始计划后进入当日，不会跳过建计划生成测试数据：

```powershell
& D:\MentorFlow\.venv\Scripts\python.exe -B -m tests.refactor.simulation.run --root D:\MentorFlow\.local\test-tmp\fix44-integrate\day-run --to generated
& D:\MentorFlow\.venv\Scripts\python.exe -B -m tests.refactor.simulation.run --root D:\MentorFlow\.local\test-tmp\fix44-integrate\day-run --from-stage generated --to classroom --day 3
```

`--from-stage` 校验检查点阶段，不匹配就停止。`--day N` 从真实计划开始日算起，固定在该天10点，只学当日区段，到当日目标已满或模块结束即停；禁止回拨已学时间。完整流程在当日目标完成或模块结束时用“离开→模拟时钟过一天→再进入”继续。可在 Python API 中调用 `sim.clock.advance(days=1, minutes=0)`；`sim.clock.day(settings)` 尊重 `refresh_time`，`now` 为含时区时间。默认时区 `+09:00`，可用 `--clock 2026-10-03T10:00:00+08:00`。模型计价、超时、实际耗时始终用真实时钟。

第五十三批：`--first-module` 在第一个模块的第一次检测结束后暂停，保留未重测状态；同root去掉此参数、`--from-stage classroom`继续。`--skip-study-day 3` 在自然跨到第3天时只推进模拟时钟，不进入课堂，便于核对欠账；可以重复指定其他日期。两个参数不修改产品进度或判分。

第五十五批：真实模式可加 `--complete-scenario`，使用现有公开控制确保一次“没听懂”、插话、重开，及首次随口问/模块检测的故意错答意图；题目回答仍由模型根据公开屏幕实时生成，工具不读隐藏答案。实际是否答错由人工核对。覆盖标记随检查点保存，不因续跑重复执行。正常离开时，只有已设置取消事件的 `MODEL_CANCELLED` 不再停止整轮；未知用量仍按原上界记账，预算保护保持有效，意外取消及网络错误仍停止。

## 模拟用户和固定脚本

第七十批：模块检测考本模块全部资料原题，未过只重测错题；最终检验按题记过关，错过的题需连续答对两遍，重复题尽量间隔三道其他题。没有题的模块只学习，整份计划没有题则直接生成学习报告。自由检验也只用保存的资料题，不生成或搜索新题。

问答题 `kind=recall` 使用两步公开动作：先 `class.recall_reveal {question_id, answer}` 提交自己的回答，参考答案和要点才会显示；随后 `class.recall_submit {question_id, ticked}` 勾选 0 起的要点下标。模拟用户在揭示前看不到参考答案；离线合成夹具用公开内容练习这一流程。桥接包裹层照旧补当前 `plan_id`。

人设是JSON文件，字段示例：

```json
{"goal":"计算机术语","final_goal":"读懂基础资料","level":"zero","style":"简短口语，有不懂的会追问","wrong_ratio":0.2,"ask_ratio":0.1,"known_ratio":0.1,"exit_ratio":0.03,"days":7,"daily_cap":60,"notes":"","seed":42}
```

真实模拟用户每一步只看到当前桥层给界面的公开内容、可用动作和人设，不看题库隐藏答案、教师模型内部提示、存储或评测结果。按种子概率给它“本次打算答错/追问/已会/退出”的行为提示；这是意图比例，模型是否遵守由人看证据，不保证精确统计比例。模型实时返回 `{method,params}`，不是预写台词。它不评分教学质量。

建计划阶段只发送聊天、预览、推荐链接、已导入资料的可见字段和地图；不发送长正文或模型内部信息。完整审计仍保存在步骤记录里。它不删用户对话、不替用户选择，也不绕过资料导入。
资料选择的可用参数按真实 checkbox 状态列出：已采用只能取消，未采用可以采用；采用至少一份后才提供“出知识地图”按钮。模拟用户仍自行决定是否够用或继续找资料，工具不替它确认。

离线自定义固定动作 `--script 文件.json` 是JSON数组，例如：

```json
[{"method":"plan.say","params":{"text":"我想先区分队列和栈。"}},{"method":"plan.say","params":{"text":"能在阅读代码时认出两种顺序。"}},{"method":"plan.say","params":{"text":"确认并找资料"}}]
```

不要写隐藏答案查询，也不要直接修改存储。每个动作必须属于当前屏幕允许动作；`plan_id` 由工具补当前计划。固定脚本须配合当前阶段：若已是预览，可先用 `plan.say` 提“路线要先讲队列，再比较栈”，收到新版后再发“确认并找资料”。默认用户在预览阶段自动确认，模型用户按人设表达认可或修改，程序不替它确认。检查点保存剩余脚本，续跑不重新执行已用台词；脚本用完后回到默认离线用户。如果希望精确复现更多动作，请提供整个动作数组。离线固定答案只适用于合成夹具，按公开选项文字选答，不读隐藏答案；不同题型、真实资料应使用模型用户或相应人工脚本。模型用户续跑仍读实时屏幕，概率种子会重新初始化，不宣称跨进程行为比例完全一致。

## 真实小量对话（必须有任务授权）

`--reopen-before-adopt`：用户实际导入资料后关闭并重建本机服务，走真实 `plan.get` 读取同一草稿，再交给实时模型用户确认资料结构；不进入课堂、不重新推荐、不替用户确认。检查点记录已执行标记，重开不会重复执行；模型调用记录与累计预算保持不变。人设可选 `level_note` 传入真实表单的水平补充说明（预设水平仍须使用产品允许的枚举，不能伪造“一般”枚举）。

模拟用户偶尔仍重复采用已勾选卡片。无效动作不送产品桥，保留原始回复与拒绝事件，只给一次“该动作没执行、屏幕没变化”的纠正机会，由模型自己重新选。仍无效就停；连接/协议错误、产品错误均不在这里重试，费用累加。这不等于替用户自动出地图。

真实运行不进pytest。第四十四批只授权离线运行，花费 ¥0；以下是工具能力的用法，执行前必须有当前任务的真实调用授权及预算。指定管理员已存在的账号标识和只读预设根目录，使用现有DeepSeek Flash。只复制 `model-gateway.json` / `connections.json` 元数据到**隔离数据目录**，凭据继续由Windows读取器获取，不读取、显示或保存Key；不改原账号元数据，不连接业务服务器。存在待清理凭据或预设不是Flash时拒绝初始化。

```powershell
& D:\MentorFlow\.venv\Scripts\python.exe -B -m tests.refactor.simulation.run --live --account-id ADMIN_ACCOUNT_ID --preset-root D:\MentorFlowData\account-shell --persona D:\MentorFlow\.local\test-tmp\fix44-integrate\persona-live.json --root D:\MentorFlow\.local\test-tmp\fix44-integrate\live --to dialogue --dialogue-rounds 3 --budget 0.5
```

企业名单和价格客户端被测试容器换成本机内置读取器，不使用登录令牌。模型用户逻辑用途记为 `simulation_user`，通过既有 `plan_intent` 非思考配置发送独立用户提示；不改产品gateway档位。

## 止损与证据

- `--budget 0.5`：产品、模拟用户、后台共用人民币预算。第五十三批按同用途已发生的高峰价单价预留（最近单价/平均价较高者加10%）；首次按不超过8192输出token估算，不改实际发给产品的输出上限。用真实usage结算，另记真实时段价。失败没有usage时保留完整输入/输出封顶估算，不报0。无法计价不发送。达到预算或新调用预留超限即停。预留是实际单价预测，不能保证单次异常大输出绝不超额；续跑累计旧调用花费，不重新清零。
- 第四十八批实测限制：当前预留没有计入服务器搜索次数上界，research 的搜索费可能使单次结算略超 `--budget`，然后才止损；该参数暂不能声明为严格硬上限。真实使用前需预留搜索费余量并核对累计记录，本轮预算缺陷与实际超出金额已在体验报告说明。
- `--stuck-limit 5`：同一步没有实质进展N次就停。指纹看阶段、当前题/模块/掌握状态、最新AI追问、资料选择和准备进度；不把不断增加的消息ID当作进展。问题不同可继续澄清，没有“最多聊3轮”的默认限制。
- `--timeout 240`：一个桥请求最大真实秒数，超时取消并留请求，不回写成功。后台准备等候也受无进展止损约束，可按体验预算设大些。
- 任意新桥错误、课堂`failure`、操作新增的末条聊天`error`或模型断线/预算拒绝即停；只读草稿重开可展示上次保存的错误，不将其误判为读取失败。产品内部已有的解析重试仍按产品执行，但止损后禁止后续模型调用。不会偷偷恢复或跳过失败步骤。
- 协议无效响应 `MODEL_RESPONSE_INVALID` 也保留调用与费用，允许产品对同一请求执行已有的一次重试；记录器不自行重试，同一请求再次协议失败或桥返回可见错误即停。模拟用户传输/协议失败仍立即停；仅动作内容校验失败按上文纠正一次。该规则避免记录器先停止，导致产品的既有重试被替换成“模拟已止损”。
- 第五十二批填点走真实 `generate`：每模块只送匹配章节，按现有12000字符切批。该用途输出截断时，记录器允许产品已有的加输出上限、拆半流程执行，不自行重试；每次调用都累计预算，最终桥失败仍止损。模型不再返回整张地图或调整模块顺序。

每步 `steps/step-*.json` 保存请求、模拟日期、界面返回、秒数、累计费用；`model/call-*.json` 保存每个模型用途、请求、回复或错误、usage、耗时和费用（含失败）；`events.jsonl` 保存流式返回、推送、用户动作与跨天。`checkpoint.json` 是可续跑位置，`issues.json` 指向原始证据，`summary.json/.md` 只报进度、止损、花费，**不自动判教学质量**。报告/PDF位于运行目录，不弹出文件窗，不改安装版。

运行目录只能在 `D:\MentorFlow\.local\test-tmp\` 内；不要指向学生数据。工具不删除既有数据或覆盖缺检查点的目录。检查完后由任务负责人按磁盘规则清理本任务中间产物，只留最后一份证据。


## 第二版资料流程（第五十一批）

推荐链接与已导入资料分开：推荐不能直接勾选采用；打开后自行下载上传，或主动贴网址/GitHub 仓库。上传原件仅留隔离目录的本机资料库，不上传服务器。目录调整是独立确认阶段 `material_preview`，缺失目标可补资料、删除或保留说明继续；不会自动搜索补齐。

真实体验先使用 `--pause-material --to map`，在 sources 检查点暂停，操作员人工挑选推荐链接、下载文件。然后在同一 root 用 `--material-file <隔离文件路径> --from-stage sources --to map` 续跑（真实 QFileDialog 的选定文件替换为操作员指定文件，真实导入实现不换）。也可用 `--material-url <主动提供的HTTP网址或GitHub仓库>`。没有这些参数时，离线脚本用户主动贴合成资料网址；实时模型用户只按公开屏幕动作。

操作员文件只上传一次，随后清空文件选择。模拟用户若拒绝它，工具停在资料选择等待操作员，不自动重复上传，也不改回采用；原件与拒绝记录仍保留。

`--reopen-before-adopt` 现在在用户导入后、按目录调整结构前重建服务一次，核实本机正文、采用状态和累计花费能恢复。使用同一 root 续跑，预算是此 root 的累计人民币高峰价上限，不能当作每次追加额度。真实推荐输出上限为12288，模拟用户4096；不使用旧的自动补搜、评分或覆盖核对用途。

第五十三批：`--timeout` 是桥接无模型活动的等待上限。整本书填点是一项桥接动作中的多次模型调用；每次模型开始或结束延长等待，避免仍在生成时被整步固定时限取消。状态文字和流式事件不延长等待；单次模型的产品时限及累计预算照旧。后台任务等待沿用指定时限，不再固定截成30秒。CLI在关闭并等待桥接线程后才打印/保存最终摘要，纳入取消时最后返回的用量或未知用量上界。续跑按既有请求文件的最大序号继续编号，失败步骤没有完成记录时也不覆盖原文件。
# 第七十一批联调

离线完整场景使用真实资料拆题、真实题库和存储：两个带资料原题的模块（包含问答题）与一个无题模块。随口问是普通对话，不生成 quick 题；无题模块只学不考。问答题先提交回答，再调用 `class.recall_reveal` 看原答案，最后用 `class.recall_submit` 勾要点；客观题使用 `kind=objective` 与 `question_type`。脚本用户在重测中只回答错题，完成最终检验、报告 PDF 与自由检验。

# 源代码模拟流程工具

本工具只在源代码工位运行，不启动主界面、不改系统日期，不操作安装版或学生账号数据。创建真实 `AppServices`、`AppBridge`，通过真实 `plan.*` / `classroom.*` 注册入口驱动；Qt 只作隐藏的事件泵与 PDF 字体绘制。**从填表、聊天、找资料和勾选开始，没有直接塞计划、资料或题库的捷径。**

离线模型与假网页引用现有 `tests/refactor/integration38` 的明确标注合成夹具，资料抓取/提取、地图、准备、题库、存储、账本、课堂、报告与 PDF 都走真实代码。它证明接线和状态能走通，不证明教学质量。填表后不限轮数聊需求，真实 `preview` 阶段展示“需求摘要 + 大致模块”；脚本用户发送 `plan.say` 的“确认并找资料”后才搜索，也可先提出修改、看新版预览再确认。预览与澄清共用 sufficiency 用途，但假模型按实际系统提示区分两种响应，不将预览算成一次澄清。

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
- `sources` 停在已找到并展示资料卡片、尚未勾选时。
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

## 模拟用户和固定脚本

人设是JSON文件，字段示例：

```json
{"goal":"计算机术语","final_goal":"读懂基础资料","level":"zero","style":"简短口语，有不懂的会追问","wrong_ratio":0.2,"ask_ratio":0.1,"known_ratio":0.1,"exit_ratio":0.03,"days":7,"daily_cap":60,"notes":"","seed":42}
```

真实模拟用户每一步只看到当前桥层给界面的公开内容、可用动作和人设，不看题库隐藏答案、教师模型内部提示、存储或评测结果。按种子概率给它“本次打算答错/追问/已会/退出”的行为提示；这是意图比例，模型是否遵守由人看证据，不保证精确统计比例。模型实时返回 `{method,params}`，不是预写台词。它不评分教学质量。

建计划阶段只发送聊天、预览、资料卡片的可见字段和评分说明、地图等；不发送界面不展示的 research 检索池、质量审计、匹配词和长正文证据。完整审计仍保存在步骤记录里。它不删用户对话、不替用户选择，也不绕过资料核验。
资料选择的可用参数按真实 checkbox 状态列出：已采用只能取消，未采用可以采用；采用至少一份后才提供“出知识地图”按钮。模拟用户仍自行决定是否够用或继续找资料，工具不替它确认。

离线自定义固定动作 `--script 文件.json` 是JSON数组，例如：

```json
[{"method":"plan.say","params":{"text":"我想先区分队列和栈。"}},{"method":"plan.say","params":{"text":"能在阅读代码时认出两种顺序。"}},{"method":"plan.say","params":{"text":"确认并找资料"}}]
```

不要写隐藏答案查询，也不要直接修改存储。每个动作必须属于当前屏幕允许动作；`plan_id` 由工具补当前计划。固定脚本须配合当前阶段：若已是预览，可先用 `plan.say` 提“路线要先讲队列，再比较栈”，收到新版后再发“确认并找资料”。默认用户在预览阶段自动确认，模型用户按人设表达认可或修改，程序不替它确认。检查点保存剩余脚本，续跑不重新执行已用台词；脚本用完后回到默认离线用户。如果希望精确复现更多动作，请提供整个动作数组。离线固定答案只适用于合成夹具，按公开选项文字选答，不读隐藏答案；不同题型、真实资料应使用模型用户或相应人工脚本。模型用户续跑仍读实时屏幕，概率种子会重新初始化，不宣称跨进程行为比例完全一致。

## 真实小量对话（必须有任务授权）

真实运行不进pytest。第四十四批只授权离线运行，花费 ¥0；以下是工具能力的用法，执行前必须有当前任务的真实调用授权及预算。指定管理员已存在的账号标识和只读预设根目录，使用现有DeepSeek Flash。只复制 `model-gateway.json` / `connections.json` 元数据到**隔离数据目录**，凭据继续由Windows读取器获取，不读取、显示或保存Key；不改原账号元数据，不连接业务服务器。存在待清理凭据或预设不是Flash时拒绝初始化。

```powershell
& D:\MentorFlow\.venv\Scripts\python.exe -B -m tests.refactor.simulation.run --live --account-id ADMIN_ACCOUNT_ID --preset-root D:\MentorFlowData\account-shell --persona D:\MentorFlow\.local\test-tmp\fix44-integrate\persona-live.json --root D:\MentorFlow\.local\test-tmp\fix44-integrate\live --to dialogue --dialogue-rounds 3 --budget 0.5
```

企业名单和价格客户端被测试容器换成本机内置读取器，不使用登录令牌。模型用户逻辑用途记为 `simulation_user`，通过既有 `plan_intent` 非思考配置发送独立用户提示；不改产品gateway档位。

## 止损与证据

- `--budget 0.5`：产品、模拟用户、后台共用人民币预算。发送前以UTF-8字节上界、实际输出下限及高峰价预留；用真实usage结算高峰价上界，另记真实时段价。失败响应也计费；没有usage的调用保留预留，不报0。无法计价不发送。达到预算或新调用预留超限即停。续跑累计旧调用花费，不重新清零。
- `--stuck-limit 5`：同一步没有实质进展N次就停。指纹看阶段、当前题/模块/掌握状态、最新AI追问、资料选择和准备进度；不把不断增加的消息ID当作进展。问题不同可继续澄清，没有“最多聊3轮”的默认限制。
- `--timeout 240`：一个桥请求最大真实秒数，超时取消并留请求，不回写成功。后台准备等候也受无进展止损约束，可按体验预算设大些。
- 任意可见桥错误、课堂`failure`、末条聊天`error`或模型断线/预算拒绝即停；产品内部已有的解析重试仍按产品执行，但止损后禁止后续模型调用。不会偷偷恢复或跳过失败步骤。
- 协议无效响应 `MODEL_RESPONSE_INVALID` 也保留调用与费用，允许产品对同一请求执行已有的一次重试；记录器不自行重试，同一请求再次协议失败或桥返回可见错误即停。模拟用户响应失败仍立即停。该规则避免记录器先停止，导致产品的既有重试被替换成“模拟已止损”。

每步 `steps/step-*.json` 保存请求、模拟日期、界面返回、秒数、累计费用；`model/call-*.json` 保存每个模型用途、请求、回复或错误、usage、耗时和费用（含失败）；`events.jsonl` 保存流式返回、推送、用户动作与跨天。`checkpoint.json` 是可续跑位置，`issues.json` 指向原始证据，`summary.json/.md` 只报进度、止损、花费，**不自动判教学质量**。报告/PDF位于运行目录，不弹出文件窗，不改安装版。

运行目录只能在 `D:\MentorFlow\.local\test-tmp\` 内；不要指向学生数据。工具不删除既有数据或覆盖缺检查点的目录。检查完后由任务负责人按磁盘规则清理本任务中间产物，只留最后一份证据。

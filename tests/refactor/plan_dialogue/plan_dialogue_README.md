# 新建计划对话用例集（第六批 MF-02）

基线 ae94765。A 类 33 条，B 类 18 条。这里只增加测试，不修产品。
需求依据：CURRENT_STATE 的 ARCH-06、PLAN-01、PLAN-02、PLAN-06、PLAN-07、BUG-09。
派工：[6-MF02]；本轮基线以用户消息为准。

## 数据与断言

plan_dialogue_cases.json 每条记录阶段、前置状态、原句、期望和可执行检查。
A-Q：8 条追问回答；A-S：7 条资料偏好；A-U：7 条链接/仓库；
A-O：8 条轮廓修改；A-G：3 条状态防护。含口语、错别字、长句、
同句两要求，以及“行”“不要”“换一批”。

B-F：6 条改表单；B-R：3 条资料范围；B-C：2 条卡片选择；
B-M：2 条生成后的调整；B-I：3 条问问题；B-X：2 条闲聊。
B 全部 pending，当前实测不执行。表单修改用两次对话核对：
提出修改时不擅改，用户“确认”后检查字段真实变化。准备状态均由运行器建立，
架构师实现后无需手工伪造数据库。

检查包括：阶段/表单/课程库、已采用与已拒绝卡片、实际厂商搜索计数、
链接导入后的资料块、GitHub 实际读取路径、轮廓版本/引用/模块顺序与数量、
时间区间、是否虚报已经开始/生成。拆分和合并根据前置轮廓的实际模块标题与引用匹配，
不是把模型回复中出现“已修改”当作成功。

资料偏好检查模型真实返回的候选列表。“优先官方”要求首项及至少一半卡片为官方文档，
接受 docs.python.org 及 CPython 仓库的 Doc 文档源码，不把“优先”错误收紧成“仅此域名”。
语义检查是有限的关键词/结构断言，不是另一模型自动裁判，不把 PASS 等同于完全正确。
特别是离线网页正文不能证明真实网站的可访问性、难度、语言或教学质量。
完整模型提示、输出、搜索词、原始结果、引用和前后业务快照保留供复核。

## 执行边界

- Harness 调用生产 plan_flow.register 注册的处理器及真实 PlanFlow、CourseStore。
  构造器注入只把一个真实实例交给路由；不替换 say 或业务方法。
- 不构造 AppServices，不启动账号/服务器同步服务。测试账号是 plan_dialogue_synthetic。
- 管理员 ModelGateway 使用派工指定的账号和根目录读取已有预设/凭据。
  构造前把它的 UsageLog 重定向到本次测试目录；禁止迁移/写配置/写凭据。
  只读元数据核对管理员配置及数据库文件前后未变。Key 不输出、不落盘。
- 仅允许当前官方 DeepSeek Flash 预设，不改预设或偷偷换模型。
- 计划、草稿、原始记录、账本、预算状态都放
  D:\MentorFlow\.local\test-tmp\plan-dialogue\；每次新建运行目录。
- plan_dialogue_course.md 是作者编写的八章合成教材。资料/追问前置状态为明确的合成状态；
  轮廓只生成一次真实结果，关闭数据库后复制到各独立用例。
- 普通网页统一使用明确标记的合成 HTML；来源标题和检索结果仍来自真实模型。
  仅 A-U04/U05/U06 使用生产 GitHub 抓取器读取公开仓库，无账号认证。
- B 的 ready 状态用显式脚本替身预生成真实数据库内容；未来测 B 时用户对话才走真实模型。
  替身生成不计入真实业务验证。
- 运行前不要设置全局 MF_LIVE_EVAL；默认 pytest 不访问真实凭据或付费模型。

## 命令

PowerShell，工位 D:\MentorFlow-office\runtime\repo：

~~~powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:QT_QPA_PLATFORM='offscreen'
$env:TEMP='D:/MentorFlow/.local/test-tmp/p6'
$env:TMP=$env:TEMP
$env:PYTEST_ADDOPTS='--basetemp=D:/MentorFlow/.local/test-tmp/p6full'
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$env:MF_LIVE_EVAL=$null
& D:\MentorFlow\.venv\Scripts\python.exe -m pytest tests/refactor -q -p no:cacheprovider
~~~

真实 A 类（会扣费，保留产品真实失败，退出码 1 不表示运行器未产出记录）：

~~~powershell
$env:MF_LIVE_EVAL='1'
& D:\MentorFlow\.venv\Scripts\python.exe -m tests.refactor.plan_dialogue.plan_dialogue_live
$env:MF_LIVE_EVAL=$null
~~~

明确复测子集可加 --only A-Q01 A-O01；仍计入同一个持久预算。
不要用重复全量运行来“刷到通过”。本次不执行 B。
--reuse-outline 可指向先前 run-*/templates/outline，复用同一真实轮廓而不重复付费。
未来架构师完成意图判断并授权后，可同时设置 MF_LIVE_EVAL_B=1，加
--include-b --only B-F01 ...，走相同入口和断言。

## 金额和停止条件

每次 gateway.complete 后调用 gateway.usage_log.summary，包括失败、自动格式重试、
资料评分及共用轮廓生成。金额来自真实厂商 usage × 基线目录价，不是厂商账单；
未返回用量、跨时段无法定价或价格不完整，不能记作零。

budget.json 跨运行累计峰值价格成本，不因重开运行清零。每个实际 HTTP 往返
（含搜索 pause_turn 续接）先持久预留 ¥2.50，覆盖此基线 Flash 的 1M 未缓存输入与
8192 输出峰值价并留余量；收到可定价 usage 才结算释放预留。已花金额加预留不得超过 ¥5。
因此会早于 ¥5 停止，而不是赌剩余额度。意外中断、用量/定价不明也停止后续付费调用。
这是测试端的保守请求预算，不是供应商账户支出限制。不要删除预算文件后继续本轮。

若适配器拒绝一个已有完整 usage 的响应，网关可能遗漏该次用量。运行器在适配器解析前
保存 HTTP 原始响应，并把每个 HTTP 的用量单独记入 http-usage.sqlite3；此观测账本与
网关账本不能相加。只有每个 HTTP 都结算、没有悬挂预留且价格明确，才可继续。
旧版运行器留下的这种已计量缺口可用 --resume-metered 核对恢复；仍不清楚的超时或中断绝不恢复。

普通网页/仓库的零模型调用用例仍可执行；预算停止的模型用例不会被记成通过。
全量默认离线验收与真实 A 的通过率分别报告。

## 产物

- report.md：每个 A 的结果、失败观测、B pending，费用；最终补充源码判因/措辞敏感性。
- run-*/manifest.json、configuration.json、bootstrap.json：基线、预设公共字段、
  合成与真实前置状态及配置指纹。
- run-*/cases/<id>/record.json：完整前后快照、渲染后的用户原句、模型调用、
  网页/仓库日志、每项断言与用量汇总。
- run-*/model_calls.jsonl：全部调用（含共用轮廓与自动重试）的提示与结果；无 Key。
- run-*/usage.sqlite3：隔离的真实用量账本；summary.json 为本次汇总。
- budget.json：跨运行保守累计和每个 HTTP 回应的 usage。
- http-responses/ 与 http-usage.sqlite3：完整 HTTP 响应及独立观测账本，不含请求头/Key。
- offline-full-final.txt：默认全量验收输出；offline-full.txt 保留最初环境失败。

Windows 全量测试应使用上述短 D 盘路径，避免语音下载测试拼出的路径超过 MAX_PATH。
Git 的 core.autocrlf 可能使两份相同 Git blob 的价格文件字节不同；校验需使用基线原始字节，
不要修改价格内容或放宽原有的字节检查。本轮只重新同步工位内同一 blob 的原始 LF 字节，
没有产生产品 Git 差异。报告保留环境失败和修正后的输出。

原始记录含模型与公开资料文本，不提交到 Git；数据集、运行器、检查和合成教材提交到本目录。
发现的产品问题只写测试报告/回执，不修改 refactor/ 或总文档。

## 离线复核

运行 python -m tests.refactor.plan_dialogue.plan_dialogue_review 可只用保存的数据重算断言，
不读 Key、不调用模型。原 record.json 不变；更正输出到 assessments.json 和 report.md。
它只把同一渲染后原句算入当前用例；预算停止记 NOT_RUN_BUDGET，不算产品失败；
同一原句既失败又成功记 UNSTABLE。修改过的早期原句单独保留，不能拿其结果冒充措辞对照。

本次人工复核修正了两类误判：CPython 的 Doc 源码属于官方文档；
“异常处理”模块的说明提到“为后续清洗打基础”不代表它就是清洗模块。
还补上“数据未改，但 AI 声称已修改设置”的失败检查。真实结果没有为通过率重跑或改写。
第一轮只有那次适配器异常的原始输入、错误与完整 usage，未保留供应商响应正文；
后续调用均在适配器解析前保存全文。报告明确该历史证据缺口，不补造响应。

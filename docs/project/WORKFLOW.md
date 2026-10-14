# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读「当前阶段」），这里只写编号引用。规则只在 [AGENTS.md](../../AGENTS.md)，这里只写固定做法。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 的「当前阶段」和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-14 下午）：线上 2.0.14，服务器 MF-76。已合入待发：[BUG-84]～[BUG-86]、[BUG-88]～[BUG-92]、[PLAN-06] 推荐资料直接采用与网页书顺着目录读、[TEACH-03] 跳过按钮拉手、[TEACH-05] 课堂上方收成一行。员工全部空闲。用户计划已在源代码版换入新结构、逾期已平摊（[BUG-86]），用户在源代码版测；满意后发版前总验收 → 发 2.0.15。

## 固定做法（规则见 AGENTS.md）

- **员工实测不占用户的鼠标键盘（2026-10-11）**：真打包实测用 Windows 的 UI Automation（InvokePattern）点原生按钮、读文字；网页里的按钮和输入框用 Chrome DevTools 协议（测试环境设 QTWEBENGINE_REMOTE_DEBUGGING），因为 Qt 6.11 网页的无障碍接口只能聚焦、点不动；不用 computer-use、模拟输入或抢前台（102-MF04 用电脑界面操作时被用户按 Esc 中止）；测试窗口会出现在屏幕上，派任务时先告诉用户。
- **发版**（什么时候发由用户定）：用流水线发版，让用户在装好的软件上点更新亲测（操作手册“发布新版”）。**本机先验、GitHub 一版只跑一次（用户 2026-10-11 提出）**：先在本机用同一个打包脚本打预览版，跑一遍 scripts/release 的发版前总验收（102-MF04c 整理），过了才触发 mf-build / mf-publish；没在本机验过的版本不上 GitHub。正式版不在本机打：本机环境会把别的 DLL 混进包里（102-MF04 实测混入过 ICU/OpenSSL），而且同一版本两边打出来的文件不同，会扰乱以后的增量更新。**版本号只在真正发布时占用（用户 2026-10-12 定）**：定下要发的下一版（现在是 2.0.15），在发布前查出的 bug 修完仍发这个版本号，不跳到下一个号；本机排练用的测试版本号（如 9.103.14）不算。
- **任务状态**：任务写好标“已起草，待转发”；用户说发了才改“进行中”；合入后从员工表和待派队列删掉，任务原文也删。
- **署名**：主目录提交用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不写进共享 git 配置。员工用各自工位名提交。

## 架构师操作手册

- **合入员工分支**：主目录 `git merge --no-ff office/<工位>`（或 `git diff --binary <基线> <员工提交> | git apply --index --3way`），逐文件看改动是否在任务范围内；跑本批改动相关的测试（`pytest $(git diff --name-only <基线> HEAD | grep '^tests/.*test_.*\.py$')`），两份以上改动合在一起时再跑一次全量。
- **全量测试**（约 10 分钟，2026-10-10 约 3280 通过、28 跳过）：`TMP=D:/MentorFlow/.local/tmp TEMP=D:/MentorFlow/.local/tmp .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/test-tmp/<子目录>/pt`，输出重定向到文件、放后台跑，跑完删子目录。
- **基线**：合入并提交后 `git rev-parse --short HEAD` 就是下一批任务的基线。
- **发布新版**（[SYS-06]）：①合入并推送 main；②手动运行 GitHub 工作流 mf-build（版本号、更新说明、是否必须及原因、基线版本，build_only=false），约 5 分钟；③下载它的 unsigned 产物，本机 `.venv/Scripts/python.exe -B scripts/release/sign_and_stage.py --artifact <zip> --private-key .local/release-keys/mentorflow-app-2026.private.key --work .local/test-tmp/release-<版本>/sign --ssh-identity E:/Codex/Secrets/MentorFlow/ssh/mentorflow-e1-ops-ed25519`；④手动运行 mf-publish（版本号；build_installer 默认 false 只发更新约 1～3 分钟，要新安装程序时设 true 约 10 分钟，或事后单独跑 mf-installer）；⑤用客户端代码对公网 stable.json 验签，看 aiteacher.clannad.win/download。触发和查看运行可用 GitHub 网页，或用本机 git 凭据调 GitHub API（.local/tmp/watch_run.sh 轮询到结束）。工作流只能手动触发，额度红线见 scripts/release/README.md。
- **启动/重启用户的源代码版**：`Start-Process -FilePath D:/MentorFlow/.venv/Scripts/pythonw.exe -ArgumentList 'D:/MentorFlow/refactor/desktop/source_start.pyw' -WorkingDirectory D:/MentorFlow -WindowStyle Hidden`。重启前一定先问用户（可能在上课）。
- **查用户软件的问题**：装好的软件日志 D:\MentorFlowApp\MentorFlow\Data\logs\mentorflow.log，源代码版日志 D:\MentorFlowData\account-shell\logs\mentorflow.log（JSON 行，只读）；数据库在对应的 learning\accounts\<账号>\*.sqlite3，只读查看用 `sqlite3.connect('file:...?mode=ro', uri=True)`。
- **界面构建**：PATH 加 `/d/MentorFlow/.local/node`，`node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 不进仓库。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc 或 python -c（会被吃掉，BUG-10），写成文件再运行。
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，mentorflow-admin，sudo -n）。线上后端 mentorflow-slim-backend:MF-76（2026-10-12；切换与回滚脚本 /opt/mentorflow/state/mf76/switch.sh：无参数切换、check 只查配置、rollback 回到 MF-75；上一版 mf75 目录保留）；入口 nginx 容器 mentorflow-e1-preview-edge-1（配置 /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf）；更新与下载目录 /opt/mentorflow/update-static/e108/app/；资料白名单 /opt/mentorflow/state/mentorflow-e1-preview/catalog/。 2026-10-10 起两个 docker 网络设了 ip_range（BUG-45）：以后用 Compose 操作这些容器，命令末尾必须追加 -f /opt/mentorflow/state/bug45-20261010/network45.override.yml。
- **自动等回执（用户 2026-10-14 定）**：只挂一个后台等待：`.local/tmp/receipt_watch.sh wait`（每 30 秒查整个 .local/receipts，和 .local/tmp/receipts.seen 比，有新的或改过的回执就列出并结束），收到就直接读回执、审查合入、清理、更新文档，处理完用 `receipt_watch.sh ack <任务号>` 记为已处理，再挂一次 wait；不用等用户转告，不按任务分别挂多个。
- **磁盘（用户 2026-10-10 定）**：测试与临时产物在 .local/test-tmp/ 下；冒烟测试、构建、中间文件用完就自动删，不留着等以后分类。员工删除被系统拦住时，架构师合入后直接删（只留 report.md 和截图），不用再问用户。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | 以 `git rev-parse --short HEAD` 为准；2.0.14 已推送并发布 |
| 已发布 | 2.0.14 = 1a58917c（2026-10-12，非必须；下载站安装程序 2.0.14）；强制线 2.0.3 |
| GitHub | main 已推送（私有仓库），远端只有 main 一个分支（9-26 遗留的 15 个 Dependabot 升级请求与分支 2026-10-12 经用户同意已关闭并删除，它们的配置已随旧代码删除，不会再生成）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 空闲 | client |
| MF-03 | 133-MF03 已起草，待转发 | classroom |
| MF-04 | 132-MF04 进行中 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 第一百一十四～一百一十九批全部已合入（最后一份 119-MF03，15afe435；主线 efbb0670）。
- 120-MF07 已合入（ada8e9a0，[BUG-89] 已修）。121-MF06 已合入（5650ba9a，网页已重新编译）。122-MF02 已合入（d24882ef，[BUG-85] 第三轮）。
- 123-MF02 已合入（115c8f48，[BUG-90]）；124-MF06 已合入（fe1b9536，[PLAN-06] 直接采用）。
- 125-MF06 与 128-MF06 一起合入（ac3d063a，网页书顺着目录读，[BUG-92] 已修；site128 留下的 1.3 GB 临时文件员工删除被拦，架构师已删，只留 report.md）。
- 126-MF07 已合入（6c1203c6，[BUG-91] 已修，网页已重新编译）。
- 127-MF03 已合入（e70c977e，跳过按钮右侧拉手，网页已重新编译）。
- 129-MF03 已合入（f31c6fb2，聊天区占满高度；layout129 留下的 1.3 GB 临时文件员工删除被拦，架构师已删，只留报告和截图）。
- 之后（用户 2026-10-14 定并确认顺序：等 2 号做完、全部合入后一次性在源代码版测，满意再发 2.0.15）：用户计划已在源代码版换入（2026-10-14 15:10，[BUG-86]；到装上 2.0.15 前不开 2.0.14）→ 用户在源代码版体验 → 满意后发版前总验收 → 用户说可以推送 → 发 2.0.15。

- 用户 2026-10-14 源代码版亲测：BUG-93（只看重点切换跳到老消息）、BUG-94（语音输入行乱跳，改为纠正后的文字只在输入框里）架构师已修（主线 5c8d18f9），待用户重启后亲测。130-MF03（要点卡后不用点继续、要点卡对齐顶部，[TEACH-06]）已合入（273686b6，部分完成：全量 2 项失败是架构师 BUG-94 改动造成，合并时已修）；recap130 残留约 1.4 GB 员工删除被拦，待用户同意后由架构师删（只留 report.md 和截图）。原文如下。

### [130-MF03] classroom｜要点卡后接着讲下一个点

~~~text
【MentorFlow 第一百三十批｜MF-03（工位 classroom）｜大知识点讲完：要点卡出来后不用再点“继续”，紧接着讲下一个点；要点卡对齐在聊天区最上方（TEACH-06）】

现成项目与许可证（第一节）
  - 不引新库。全部沿用本项目现有机制：app/classroom.py 的 next、_recap、_point_done、_publish_today、_target_met、_wait；预取 prefetch／_predicted_teacher（_teacher 里按 request 完全相等才命中）；前端 web/src/useRoundScroll.ts 的“新一轮对齐”。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 5c8d18f9         # 主线：已含 BUG-93（只看重点切换不跳）、BUG-94（语音输入行不跳）
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号；不调用真实模型。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\recap130\，用完即删，只留 report.md 和截图。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（跑浏览器测试前把 D:\MentorFlow\.local\node 加进 PATH）。花费 ¥0。
同时在跑：无。

背景（CURRENT_STATE [TEACH-06]“精炼与重点”）：
  - 现在：新式大知识点讲完最后一个小要点，用户点“懂了，继续”后，next() 走 _recap 出要点卡，然后 _point_done、_publish_today，直接 return；step 回到 teach，界面显示“继续”按钮（Classroom.tsx 里 step==="teach" 那段），用户还得再点一次才开始下一个点。
  - _predicted_teacher 在“最后一个小要点”时返回 None（注释：要点卡是确定性的、不需要预取），所以下一个点的第一句只在要点卡出来、step 变成 teach 之后才开始预取。
  - _teacher 只有在 selected['request']==request 完全相等时才用预取结果；request 里含本模块对话原文，要点卡也在对话里（_dialogue 会把 kind=recap 转成文字）。
  - 用户 2026-10-14 定：要点卡出来后不要再让用户点“继续”，太慢；要点卡显示在当前聊天区的最上方（不特殊样式，就是默认展开、放在顶上），紧接着下面就是下一个点的第一条消息。要点卡不长，不会占满一页。用户知道下一句是提前准备好的，要的就是不用等、不用点。

要做
  1. 后端连续走：next() 里大知识点最后一个小要点那一支，出要点卡、_point_done、_publish_today 之后：
     - 如果 step 是 teach 且今天目标没满（_target_met 为假），在同一次操作里接着开始下一个点，走和平常 next 完全相同的路径（新式点 _concept_begin、旧式点开场、需要备题时 _wait、没有原文的点跳过等）；
     - 如果模块讲完（step 变成 test_offer 等）或今天目标已满，照现在停在要点卡后面；
     - 下一个点的老师请求失败时，要点卡和已记的完成（时长）不能丢、不能重复；失败走现有 failure／retry，重试时直接开始下一个点，不会再出一次要点卡、不会重复记时长。
  2. 预取要命中：用户停在最后一个小要点（step teaching/closing）时，'next' 的预测要准确描述“要点卡之后下一个点的第一条老师请求”：对话里带上这张要点卡、index+1、新点的 concept 状态等，保证用户点“懂了，继续”时命中预取、不再现场等模型。把要点卡内容的生成抽成纯函数，_recap 和预测共用。预测不准的情况（模块讲完、今天目标已满、下一个点要等备题或没原文）就返回 None。
  3. 前端对齐：同一次操作里出现要点卡和下一条老师消息时，把要点卡对齐到聊天区最上方，下一条消息紧接在下面；即使这次操作产生了用户气泡（例如“懂了，继续”），也以要点卡为对齐点。要点卡照现在的样子默认展开，不加特殊样式。用 useRoundScroll 现有的锚点机制实现（例如让它在新增内容里优先选要点卡），不要另写一套滚动。
  4. 不影响别的流程：旧式点（没有小要点）、跳过知识点、跳过模块、模块检测、“只看重点”切换（BUG-93）都照旧。
  5. 测试（文件名带 130）：
     - 后端：最后一个小要点 → 一次操作里出要点卡并开始下一个点；模块讲完时停在要点卡；今天目标已满时停在要点卡；下一个点请求失败后重试只开始下一个点、不重复要点卡和时长；用假模型确认点“懂了，继续”命中预取（日志 classroom prefetch hit、模型只被调用一次）。
     - 真实 React 浏览器测试：要点卡对齐在聊天区顶部、下一条消息紧接其下、中间没有“继续”按钮；两套主题、窄窗口。截改前改后的图放进报告。
  6. 只改 app/classroom.py（next、_recap、_predicted_teacher 及相关小函数）、web/src/useRoundScroll.ts 或 Classroom.tsx 里对齐相关的部分，以及 tests/refactor/。

完成标准：大知识点讲完后不用再点“继续”就接着讲下一个点，且命中预取；要点卡在聊天区最上方、下一条消息紧接其下；模块讲完／目标已满／失败重试三种情况正确；相关测试和最后一次全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\recap130\report.md。
回执：写到 D:\MentorFlow\.local\receipts\130-MF03.md（格式见 WORKFLOW「回执格式」），给用户只回一行“130-MF03 完成/部分完成/受阻，回执已写入”。回执项目：基线 ／ 提交 ／ 后端连续走的做法 ／ 预取是否命中（证据） ／ 前端对齐做法 ／ 三种停住或失败情况 ／ 截图 ／ 相关测试与全量结果 ／ 本地最终占用
~~~

- 131-MF04（协议校验丢掉完整回复，[BUG-96]）已合入（f9780df1，部分完成：未复现，已补原因记录）。recap130 残留已由架构师删（用户同意，2026-10-14），只留 report.md 和截图。源代码版 2026-10-14 18:59 重启，用户在测。原文如下。

### [131-MF04] runtime｜协议校验丢掉完整回复

~~~text
【MentorFlow 第一百三十一批｜MF-04（工位 runtime）｜查清并修掉：DeepSeek 已完整返回的回复被我们的协议校验丢掉（MODEL_RESPONSE_INVALID，BUG-96）】

现成项目与许可证（第一节）
  - 不引新库。只改本项目的模型接入层：refactor/learning/connections/providers/（anthropic_messages.py、openai_chat.py、base.py 的 TextSink）和 gateway.py 写 model-failures.log 的地方。协议以 DeepSeek 官方 Anthropic 兼容接口文档和 Anthropic Messages 流式事件文档为准（先查文档里 stop_reason 的全部取值、思考模式下各内容块与 delta 的类型），回执写明查了哪几页。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard f6031a63
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读（要用的复制到产物目录）；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\proto131\，用完即删，只留 report.md。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。
花费：真实模型只为第 3 条复现，事先在报告里写预期；第一段 ¥3，第一段抓到失败或确认抓不到就停；抓不到可再用第二段 ¥5，上限合计 ¥8。用管理员 DeepSeek 预设，Key 只经现有代码读取。
同时在跑：MF-03 改课堂要点卡（130，app/classroom.py、useRoundScroll.ts）。你不碰 classroom.py 和前端。

背景（CURRENT_STATE [BUG-96]）：
  - 用户 2026-10-14 18:17 在源代码版课堂里，一次老师请求（request_id daf83f4139794ab9b53e3c208bddbb9e，deepseek-v4-flash，Anthropic 协议，思考模式 high，流式；输出 372 token，已计费约 ¥0.008）报 MODEL_RESPONSE_INVALID，回复没能用上。
  - 证据（只读，复制后再看）：D:\MentorFlowData\account-shell\learning\accounts\<账号>\model-failures.log 最后一条：HTTP 200，last_sse_events 以 content_block_delta…content_block_stop、message_delta、message_stop 结尾，stage=validating_anthropic_response；同目录 model-usage.sqlite3 的 calls 表有这条的用量（thinking_blocks=1、thinking_characters=592）。也就是说流已收完，是我们自己最后一步校验没过。
  - anthropic_messages.py 在 stage=validating_anthropic_response 之后可能抛 invalid 的地方：parse()（未知的 web_search 结果类型）、TextSink.finish（流里发出去的文字和最终各 text 块拼起来的文字不一致）、联网搜索次数超限、stop_reason 不在 {end_turn, max_tokens, stop_sequence, refusal, model_context_window_exceeded}。现在不记录具体是哪一项。
  - 一个值得先验证的猜测：streamed() 收到 text_delta 时不管它所属块的类型，都追加到那个块并发给 TextSink；parse() 只拼 type=='text' 的块。如果 DeepSeek 在思考块（或别的非 text 块）的下标上发了 text_delta，就会“发出去的”和“最终文字”不一致。stop_reason 出现文档外的取值是另一个可能。

要做
  1. 补记录：上面每一处抛 invalid 的地方都带上具体原因写进 model-failures.log（例如 reason=stop_reason:<实际值>、reason=text_mismatch（发出去多少字、最终多少字、第一个不同的位置、各块类型序列）、reason=search_limit、reason=unknown_search_result:<type>），openai_chat.py 同类地方一并补；不记录 API Key，回复正文最多记前后各 40 字。
  2. 查文档：确认 DeepSeek Anthropic 兼容接口实际可能返回的 stop_reason 和思考模式下的块/delta 类型，与我们的校验逐条对照，列出不一致处。
  3. 复现：在副本环境用真实模型按课堂老师请求的形状（Anthropic 协议、思考模式 high、流式、带 on_text）连续发请求，抓到失败时用第 1 条的记录定位原因；同时把每次请求的块类型序列和 stop_reason 统计进报告。按上面的分段预算，抓不到就如实写“未复现”，并说明第 1 条的记录已能在下次发生时给出原因。
  4. 修：按查到的原因修（例如只把 text 块的 delta 发给 TextSink、或放行文档里合法的 stop_reason）；拿不准的情况不放行，只保证有记录。不改动计费与用量记账规则。
  5. 测试（文件名带 131）：每种 invalid 原因都有离线用例并断言日志里有对应 reason；如找到原因，加用真实事件序列做成的离线夹具（只保留事件类型和长度，不留正文）。相关测试和最后一次全量通过。

完成标准：每一处协议校验失败都能在 model-failures.log 看到具体原因；文档对照列清；复现结果如实（抓到就定位并修好、离线用例覆盖）；相关测试与全量通过；花费不超上限。
验收：报告写到 D:\MentorFlow\.local\test-tmp\proto131\report.md。
回执：写到 D:\MentorFlow\.local\receipts\131-MF04.md（格式见 WORKFLOW「回执格式」），给用户只回一行“131-MF04 完成/部分完成/受阻，回执已写入”。回执项目：基线 ／ 提交 ／ 补的记录 ／ 文档对照结论 ／ 复现（请求数、抓到几次、原因） ／ 修了什么 ／ 相关测试与全量 ／ 花费 ／ 本地最终占用
~~~

- 待派（下一版）：客户端同步批量上传遇到 413 自动拆小批重发；去掉 sync_transport.py 里批量、翻页、单条三处 20 秒总时限（[BUG-97]，同 [BUG-90]；服务器侧已放宽）。
- 用户 2026-10-14 测完源代码版，说可以发 2.0.15。132-MF04（2.0.15 发版前总验收）进行中（用户已转发），原文如下；过了再推送并触发流水线。

### [132-MF04] runtime｜2.0.15 发版前总验收

~~~text
【MentorFlow 第一百三十二批｜MF-04（工位 runtime）｜2.0.15 发版前总验收：合入后的主线跑一次全量，再用真安装程序和真打包程序把全新安装、覆盖安装（用户要走的路）、软件内更新走一遍】

现成项目与许可证（第一节）
  - 全部用现成的：scripts/release/rehearsal102/ 的排练脚本（build/scenario/refreeze/metrics/clear 系列，107-MF04 用过）和 scripts/release/installer105/（安装位置场景脚本）。
  - 用到的外部项目：PyInstaller（GPL 打包例外）、Inno Setup（Inno Setup License），都是项目已在用的。
  - 不写新框架，不引新库；脚本接不上可以改脚本，不改产品代码。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 386a926a         # 主线：2.0.15 的改动全部合入（到 131-MF04 为止）
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...（只提交脚本修正，没有就不提交）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不访问正式账号服务器，不注册真实账号，不调用真实模型。
D:\MentorFlowApp、D:\MentorFlowData 只读（要用的复制到任务目录）；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。遇到选择自己决定，回执写明理由，不停下来问。
不要结束或重启用户正在用的软件进程：用户这段时间在用源代码版（D:\MentorFlow 启动的 pythonw），不碰它的进程和 D:\MentorFlowData。
不占用户的鼠标键盘（硬规定）：不用 computer-use，不模拟鼠标或键盘，不抢前台。网页用 Chrome DevTools 协议，原生按钮用 UIA。所有排练进程 LOCALAPPDATA、TEMP、TMP 指到任务目录。
排练网络边界（硬规定，[BUG-80]）：
  - 预览包带 timing_hook 的“只许连本机”限制；
  - 每次冻结后用 refreeze103.py --check-only 确认账号地址是本机替身；
  - 发现任何请求发往非本机地址，立即停下写进回执。
产物目录：D:\MentorFlow\.local\test-tmp\gate132\。MF_UAT_ROOT 指到这里，APP_ID 用 MentorFlow.UAT.gate132，模型凭据服务名同步换。冻结构建、安装副本、下载源、测试密钥用完立刻删，只留 report.md、截图和小脚本；删除被拦住就列路径交架构师。花费上限 ¥0。
测试要求：全量只在第 1 条跑一次（跑浏览器测试前把 D:\MentorFlow\.local\node 加进 PATH）。
同时在跑：无。

背景（CURRENT_STATE [SYS-06]「验收清单」[ARCH-03] [BUG-86]）：
  - 线上 2.0.14（源码 64fdf076）。2.0.15 要发的改动都已合入主线：[BUG-84]～[BUG-86]、[BUG-88]～[BUG-96]、[PLAN-06] 网页书顺着目录读、[TEACH-03] 跳过拉手、[TEACH-05] 课堂上方一行、[TEACH-06] 要点卡接着讲。自 2.0.14 以来没有改安装、更新、启动器、Updater 的代码（refactor/desktop、scripts/release、.github 都没动），改的是课堂、建计划、语音、模型接入和界面。
  - 各份各自跑过相关测试或全量，合在一起还没跑过全量。最近一次员工全量里 concept114（浏览器等待超时伴随 GLES 错误）单独跑通过；loading86、self_test81、status88 也是已知偶发。
  - 用户的计划在源代码版数据上换成了新结构（[BUG-86]：旧点做“被取代”标记、新增大知识点和小要点），2.0.14 不认识这个标记，会新旧点一起显示。所以用户这次不先打开 2.0.14，而是从下载站下载 2.0.15 安装程序直接覆盖安装。别的用户照常可能在 2.0.14 里点软件内更新。
  - 按规定（WORKFLOW“本机先验、GitHub 一版只跑一次”），这次总验收过了，架构师才推送并触发 GitHub 发版。版本号 2.0.15 只在真正发布时占用，排练用测试版本号。

要做
  1. 合入后的主线跑一次全量。上面列的已知偶发项失败时单独重跑那一项并写明；其他失败如实写，不修产品代码，交回。
  2. 冻结（都独立冻结，不复制改版本）：
     - 旧版 O：64fdf076，即线上 2.0.14；
     - 新版 P：主线基线，版本号 9.132.15；
     - 再用同一主线冻结 Q：版本号 9.132.16，用于第 6 条；
     - 新版的安装程序用主线的 Inno 脚本编。
  3. 全新安装：P 的安装程序 → 位置页默认是原默认位置（没有 gate132 记录）→ 改选任务目录安装 → 桌面图标 → 几秒内出登录窗、不闪 → 注册（CDP 输入，本机替身验证码）→ 登录 → 填测试 Key → 主界面能打开“新建计划”页。
  4. 用户要走的路（覆盖安装，不先开旧版）：真安装 O 到 gate132\user\MentorFlow → 登录一次、存测试 Key、退出（模拟用户以前用过）→ 不再打开 O，直接运行 P 的安装程序。
     核对：位置页默认就是 gate132\user\MentorFlow、显示已安装可更新；一路下一步覆盖成功；C 盘默认位置没有第二份；打开后是新版、设置和 Key 在、能直接登录到主界面、第二次启动干净。
     另外：把一份带新结构的计划数据放进这份安装的账号数据里（从 D:\MentorFlowData\account-shell 复制用户数据后用其中的计划，或用 tests 里 BUG-86 换入的夹具在替身账号下生成），确认 P 打开计划详情只显示新结构（被取代的旧点不出现）、能进课堂到第一句前（不调真实模型）。做不到的写清卡在哪。
  5. 软件内更新（别的用户的路）：另一份 O 安装 → 登录 → 主界面点“立即更新并重启”→ P。核对：HEALTHY、pending 消失、设置和 Key 在、updater-refresh.json = UPDATED、Updater 版本是新版、已有计划打开正常、第二次启动干净；记下点击到新窗口可输入的时间。
  6. 提速路径：在 P 上等后台就绪 → 主界面点更新 → Q。记下：后台就绪用时、点击到旧 App 退出、点击到新窗口可输入（[SYS-06] 验收清单第 10 条和 [BUG-74]）。
  7. 结束用本轮卸载器清掉 gate132 的预览注册和快捷方式；前后快照对比，正式版注册项、快捷方式、D:\MentorFlowApp 不变。

完成标准：第 1 条全量通过（已知偶发除外）；第 3～6 条在真安装程序和真打包程序上走通，有截图和日志片段。任何一条没过就写“未通过”，并写清停在哪、证据是什么，不能用替身点击或离线测试代替。
回执：写到 D:\MentorFlow\.local\receipts\132-MF04.md。表格列：
  基线 ／ 全量 ／ 全新安装与注册 ／ 覆盖安装（不先开旧版，含新结构计划显示）／ 2.0.14 → P 软件内更新（含用时）／ P → Q 提速（三个时间）／ 网络边界 ／ 正式版前后快照 ／ 新问题 ／ 本地最终占用。
给用户只回一行“132-MF04 通过/未通过，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\gate132\report.md。
~~~

- 用户 2026-10-14 晚定：133-MF03 修好合入后一起发 2.0.15；不要再重复跑安装、卸载、覆盖那套排练（132 的结果照算，133 不碰安装/更新代码），只跑 133 相关测试和一次全量。
- 133-MF03（老师新回复贴顶对齐的回归，[BUG-98]）已起草，待转发，原文如下。

### [133-MF03] classroom｜新回复贴顶对齐回归

~~~text
【MentorFlow 第一百三十三批｜MF-03（工位 classroom）｜回归：老师每条新回复出来后不再贴着聊天区顶部对齐，查出是哪次改坏的并改回（BUG-98）】

现成项目与许可证（第一节）
  - 不引新库。只用本项目现有的 web/src/useRoundScroll.ts（“新一轮对齐”）和 Classroom.tsx；用 git bisect／逐个提交对照定位。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard d2fa4dc3
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号；不调用真实模型。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\align133\，用完即删，只留 report.md 和截图。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（跑浏览器测试前把 D:\MentorFlow\.local\node 加进 PATH）。花费 ¥0。
同时在跑：MF-04 在 gate132 做 2.0.15 发版前总验收（不改产品代码）。

背景（CURRENT_STATE [BUG-98]、[TEACH-05]、[TEACH-06]）：
  - 原来的效果（用户认可的）：每一轮对话，老师的新回复（或这一轮学生的气泡）出来后，聊天区自动滚到让这一轮的开头贴着聊天区顶部（useRoundScroll：begin() 记下已有的 data-turn-key，新内容出来后把锚点 scrollIntoView block:start，用户自己往上滚就停止跟随）。
  - 用户 2026-10-14 晚在源代码版发现：现在老师说完一句，新消息不再贴着顶部了，“改着改着改没了”。
  - 今天下午到晚上动过这一块的提交：cfecb371（BUG-93：只看重点切换时保持顶部那条消息，Classroom.tsx 加了 focusAnchor 和一个 useLayoutEffect）、cf71a1ba／5c8d18f9／273686b6 里的 BUG-94（语音输入区布局，voice.css、MicButton.tsx）、273686b6 里的 130-MF03（useRoundScroll 新增内容优先选要点卡、recap 外层加 data-turn-kind；Classroom.tsx 对齐相关部分）。
要做
  1. 写一个真实 React 浏览器复现：普通的一轮（学生点“懂了，继续”或打字发送 → 老师流式回复 → 落成正式消息）、带要点卡的一轮、只有老师消息的一轮，断言这一轮的锚点（学生气泡，或要点卡，或老师新消息）顶部离聊天区顶部约 12px（scroll-margin-top），并且流式过程中不往回跳。
  2. 用这个复现在上面几个提交之间对照（或 git bisect），找出是哪次、哪几行改坏的，写进报告。
  3. 改回：普通轮恢复原来的对齐；要点卡轮保持 130 的效果（要点卡在顶部、下一条紧接其下）；BUG-93（切换只看重点不跳）、BUG-94（语音输入区不乱跳）的效果都不能丢。
  4. 测试（文件名带 133）：上面三种轮次加“只看重点”切换、语音输入状态出现时都不破坏对齐；两套主题、窄窗口。相关测试和最后一次全量通过。
完成标准：三种轮次的新消息都贴顶对齐，复现测试能抓住这次回归；BUG-93、BUG-94、要点卡效果不变；相关测试与全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\align133\report.md（含是哪次提交改坏的、改前改后截图）。
回执：写到 D:\MentorFlow\.local\receipts\133-MF03.md（格式见 WORKFLOW「回执格式」），给用户只回一行“133-MF03 完成/部分完成/受阻，回执已写入”。回执项目：基线 ／ 提交 ／ 改坏的提交和原因 ／ 怎么改回 ／ 三种轮次对齐结果 ／ BUG-93/94/要点卡是否保持 ／ 相关测试与全量 ／ 本地最终占用
~~~

## 回执格式

- **回执写文件（用户 2026-10-10 定）**：员工把回执表格写到 `D:\MentorFlow\.local\receipts\<批次>-<员工>.md`（例如 98-MF03.md），给用户只回一行“98-MF03 完成/部分完成/受阻，回执已写入”。用户只告诉架构师哪些做完了，架构师去读回执文件。每份任务的“回执”一节都写明这条。


~~~text
【员工｜任务号｜完成/部分完成/受阻】
基线：开工时切到的提交编号。
提交：分支 office/<工位名> 上的提交编号。
改动：修改/新增的文件。
验证：实际运行的命令与结果（通过/失败/跳过）；用了替身的写明。
未完成：没做完或拿不准的地方。
~~~

每条任务还会在末尾列出这次要回报的具体项目。

## 任务模板

新任务照 git 历史里最近一批任务原文的格式写（第一节现成项目与许可证、开工同步、禁止事项与磁盘规则、证据来源、同时在跑、花费、背景、要做、完成标准、验收、回执项目）。

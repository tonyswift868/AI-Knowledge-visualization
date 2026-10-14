# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-14）：线上 2.0.14，服务器 MF-76。已合入待发：[BUG-84]、[BUG-85] 第一轮、[BUG-86] 第一百一十四～一百一十九批（换入工具、按目录切点 440→144、按小要点出题、课堂多轮与提速、高亮与回顾卡、改日期保住欠账 [BUG-88]），新式部分只对新式点生效。120-MF07、121-MF06 已合入（[BUG-89] 已修、新建计划接按目录切）；122-MF02 已起草待转发。2.0.15 先不发：用户先在源代码版体验（[BUG-86] 换入顺序）。全量里的五个不稳定测试已修好（[BUG-87]），以后全量失败按真失败处理。

## 协作方式

- **先讨论、后派活（最优先）**：问题先和用户讨论清楚（现象、原因、牵连范围、前置依赖）再写任务；收到回执只讲审查结果、问题和要用户定的事，用户同意后再把整批任务原文放在回复最后，然后停下等转发。不出看着笃定但有漏洞的方案。
- **不懂就问**：有不懂或感觉有偏差，直接问用户。
- **确认是 bug 的直接派修（用户 2026-10-12 定）**：已经确定是 bug、原因查清的，直接写任务派出去，发现几个派几个，在回复里告诉用户；只有拿不准是不是 bug、或拿不准改了会牵连什么的，才先和用户讨论。
- **一批派完、并行优先**：能并行、互不冲突的一次派完，人数不设上限；改同一文件同一部分的排先后；有依赖的写明先后与原因。
- **不重复造轮子（硬规定）**：每份任务第一节写“用的现成项目与许可证”，参照行业标准；要自己写的写明为什么没有现成的。
- **方案由架构师出**：架构师查证据、读相关代码、找开源做法，在任务里写清“怎么做”；员工照做。
- **派任务前的检查**：每条完成标准都是这个员工凭自己的权限能做到的（取决于外部的写成“查清后二选一”）；互相依赖的工作不拆成并行任务各自要求全量通过；写之前读相关代码、查环境。
- **员工实测不占用户的鼠标键盘（2026-10-11）**：真打包实测用 Windows 的 UI Automation（InvokePattern）点原生按钮、读文字；网页里的按钮和输入框用 Chrome DevTools 协议（测试环境设 QTWEBENGINE_REMOTE_DEBUGGING），因为 Qt 6.11 网页的无障碍接口只能聚焦、点不动；不用 computer-use、模拟输入或抢前台（102-MF04 用电脑界面操作时被用户按 Esc 中止）；测试窗口会出现在屏幕上，派任务时先告诉用户。
- **安装与更新先自己走通再叫用户测（用户 2026-10-11 定）**：凡是改到安装、更新、启动器、Updater 的，任务里必须要求用真打包程序走一遍完整流程（旧版 → 新版：软件内更新的两个入口、下次启动安装、安装程序覆盖升级，按改动取用），架构师看过证据才发版；没走通不叫用户测。替身测试不算（[BUG-71]）。
- **员工的测试（用户 2026-10-10 定）**：工作中只跑与改动相关的测试，全量只在最后跑一次，同一套测试和演练不重复跑；不做 AI 模拟学生的长流程测试。
- **员工自主**：任务写清决策原则，员工自己决定、在回执写理由；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。
- **两轮规则**：同一个问题第一轮按现方法修，没解决第二轮换方法（先查开源做法）；还不行带数据找用户定。
- **花钱的测试**：只为具体改动花钱，事先写下预期；能离线的先离线；预算分段，第一段没达到预期就停。
- **发版**：修好就用流水线发版，让用户在装好的软件上点更新亲测（操作手册“发布新版”）。**本机先验、GitHub 一版只跑一次（用户 2026-10-11 提出）**：先在本机用同一个打包脚本打预览版，跑一遍 scripts/release 的发版前总验收（102-MF04c 整理），过了才触发 mf-build / mf-publish；没在本机验过的版本不上 GitHub。正式版不在本机打：本机环境会把别的 DLL 混进包里（102-MF04 实测混入过 ICU/OpenSSL），而且同一版本两边打出来的文件不同，会扰乱以后的增量更新。**版本号只在真正发布时占用（用户 2026-10-12 定）**：定下要发的下一版（现在是 2.0.14），在发布前查出的 bug 修完仍发这个版本号，不跳到 2.0.15；本机排练用的测试版本号（如 9.103.14）不算。
- **动用户数据要先问**：会让用户已有数据失效、被挡或被隐藏的设计必须先经用户同意；改数据格式一律带迁移（[ARCH-03]）。
- **功能先行**：功能没做完前不加安全加固（AGENTS.md）；审查时删掉员工多加的加固。
- **问题留档**：每修好一个问题，同一次提交里在 CURRENT_STATE 记 BUG 条目或更新原条目。
- **状态如实**：任务写好标“已起草，待转发”；用户说发了才改“进行中”；合入后从员工表和待派队列删掉，任务原文也删。
- **部署**：每次改服务器都先向用户说明改什么、拿到同意再做。
- **向用户汇报**：合入后先给一张“环节 × 状态”表，再说问题和建议；话尽量短。
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
| MF-02 | 122-MF02 已起草，待用户转发（语音整段重识别，[BUG-85] 第三轮） | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 空闲 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 第一百一十四～一百一十九批全部已合入（最后一份 119-MF03，15afe435；主线 efbb0670）。
- 120-MF07 已合入（ada8e9a0，[BUG-89] 已修）。121-MF06 已合入（5650ba9a，网页已重新编译）。122-MF02（语音整段重识别 [BUG-85]）：已起草，待转发，与这两份不冲突。
- 之后（用户 2026-10-14 定并确认顺序：等 2 号做完、全部合入后一次性在源代码版测，满意再发 2.0.15）：122 合入 → 用户开源代码版登录同步后关掉 → 用最新数据重新预演给用户看 → 用户点头 → 架构师用 scripts/plan_restructure.py --apply --end-date 2026-10-22 在源代码版数据上换入（自动备份、可回滚；之后到装上 2.0.15 前不开 2.0.14）→ 用户在源代码版体验 → 满意后发版前总验收 → 用户说可以推送 → 发 2.0.15。

## 第一百二十二批任务原文（2026-10-14，基线 efbb0670）

### [122-MF02] client｜语音整段重识别

~~~text
【MentorFlow 第一百二十二批｜MF-02（工位 client）｜语音识别第三轮：录音结束后把整段录音一次识别作为最终文字，不再按每次停顿切成小段各认各的（BUG-85）】

现成项目与许可证（第一节）
  - 不引新库、不换模型、不加下载：沿用现有 sherpa-onnx（Apache-2.0）和已下载的 SenseVoice 模型。
  - 做法照 sherpa-onnx 官方的“两遍识别”示例（python-api-examples/two-pass-speech-recognition-from-microphone.py）：第一遍边说边出字给人看，第二遍在说完后对整段录音做一次非流式识别，作为最终结果。
  - 这次不做 Whisper。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard efbb0670         # 主线：已合入你的 109（①本课词表纠正、②模型纠正）
提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\asr122\，合成音频等中间产物用完即删，只留 report.md 和结果 JSON；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。
花费：②模型纠正的评估调用上限 ¥0.10（相同输入复用已有结果，不重复花钱）。
同时在跑：MF-06 改建计划流程（planner_agents.py、plan_flow.py、计划流程前端）；MF-07 改 course/report.py。你不碰它们，也不改 classroom.py、teacher_agent.py。

背景（CURRENT_STATE [BUG-85]；你 110 的报告 D:\MentorFlow\.local\test-tmp\asr110\report.md）：
  - 用户在课堂用语音回答，“向量那个”被认成“下料来个”，“GraphRAG 与向量 RAG”被认成乱词。
  - 你 110 的数据里，同一个 SenseVoice 模型有两种跑法：
    - 产品现在的跑法（product-sensevoice）：术语原始 52.5%，加①② 73.0%，字错误率 14.8%；
    - 把整段录音一次交给模型（sensevoice）：术语原始 64.2%，加①② 93.1%，字错误率 4.8%，误换 0，识别最慢 0.36 秒；
    - 作为对比，Whisper 加①② 是 94.1%，但字错误率 9.2%、误换 4，还要另下约 148 MB 模型和约 140 MB 运行库。
  - 原因：voice/asr/engine.py 用 VAD 在每次停顿 0.8 秒（END_SILENCE_SECONDS）处切段，每段单独识别再拼起来（app/voice_input.py 的 _segments、_final）。
    说话中间一停（如“向量……那个”），词就被切开，而且每段都没有前后文。

要做
  1. engine.py：
     - 录音中照旧用 VAD 分段、边说边出字（用户能看到在识别）；
     - 同时保留整段录音（从第一次检测到说话前约 0.3 秒到结束）；
     - finish 时对整段做一次识别，作为最终文字；
     - 录音很长时（超过某个时长，先用 SenseVoice 实测定阈值，参考 25 秒）只在最长的停顿（≥1.5 秒）处切，每块不超过阈值，不在每个 0.8 秒停顿都切；
     - 整段识别失败或超时，回退到现在的拼段结果，不给用户报错；
     - 取消、关闭、换下一次录音的现有行为不变。
  2. voice_input.py：
     - voice.final 的文字用整段识别的结果；
     - ①本课词表纠正和②模型纠正作用在整段结果上；
     - “已按本课用词纠正，原识别：……”里的原识别是整段结果。
     - 录音中推过去的分段文字，结束时要被最终文字替换，不是追加。先查前端怎么处理；要改的话只改语音输入那部分前端，回执里写明，好让架构师重新编译网页。
  3. 离线评估，必须走产品的真实路径（真的 AsrEngine，按 20ms 节奏喂，和 110 的 product-sensevoice 一样）：
     - 用 109/110 的句子和术语表重新合成 110 那套音频，比较修前修后：原始、①、①+② 三档的术语认对、字错误率、新增误换、口语对照误识成术语、停止到最终文字的平均和最慢时间（含②）；
     - 另加一组“停顿”样本：10 句，在术语中间或紧跟术语处插入 0.8～1.5 秒静音（例如“向量……那个”“Graph……RAG”），修前修后对比；
     - 跑之前把预期写进报告：修后应接近 110 的整段一次识别那一行，即①+② 术语 ≥90%、字错误率 ≤6%、新增误换 ≤1、最慢 ≤3 秒。
       明显达不到就查原因并写进回执，不针对测试句调参数。
  4. 真人语音（可选，最多花 30 分钟找）：如果能找到 ≤500 MB、许可证允许的真人普通话录音小测试集（例如 AISHELL-1 测试集的子集、ModelScope 上的小样本），就取 50 句比较修前修后的字错误率，确认整段识别在真人语音上不退步；找不到就写明跳过。
     用户本人会在源代码版里用自己的声音验证。
  5. 测试（文件名带 122）：
     - 最终文字来自整段识别；
     - 中间停顿不再把词切开；
     - 整段识别失败时回退；
     - 长录音按长停顿分块；
     - 取消后不出结果；
     - ①②作用在整段结果上；
     - 已有语音测试照过。
  6. 只改 voice/asr/ 下的文件、app/voice_input.py、语音输入相关前端（如需要）和 tests/refactor/。

完成标准：第 5 条测试通过；第 3 条有修前修后数字；最后全量一次；花费不超过 ¥0.10。
回执：写到 D:\MentorFlow\.local\receipts\122-MF02.md。表格列：基线 ／ 提交 ／ 改法（整段怎么取、长录音怎么分、失败怎么回退）／ 前端改没改 ／ 修前修后（术语、字错误率、误换、口语对照、平均／最慢时间）／ 停顿样本修前修后 ／ 真人样本（或跳过原因）／ 新增测试 ／ 全量 ／ 花费 ／ 本地最终占用。
给用户只回一行“122-MF02 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\asr122\report.md。
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

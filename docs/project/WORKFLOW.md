# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读它开头的「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务；两位架构师（都是 Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。架构师之间不直接对话，交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：先读 AGENTS.md、CURRENT_STATE「当前阶段」和索引、本文件；看 `git log` 里上次基线之后的提交；确认 7 个工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。与用户讨论到一半的事，看「当前阶段」的“等用户决定的事”和各条目里标了“讨论中”的待修。
- **交班**（每次停手前都做，不等用户说换人）：用户定下的事当场写进对应编号条目并标日期；自己查到但还没讨论完的问题写进对应条目的“待修”，标明“讨论中”；更新「当前阶段」、任务索引、待派队列；本地提交。不把事情只留在对话或个人记忆里。
- **现在在做的事**（2026-10-09）：用户要把软件发给第一个外部新用户试用（对方装 2.0.2 安装程序 → 注册 → 点更新到 2.0.3）。三段：①89-MF04 打包 2.0.3（main 81461194，非强制）+ 本机排练新用户首启与 2.0.2→2.0.3（部分完成：未签名包、兼容核对、离线更新排练通过；发现 2.0.2 旧题库计划升级后作废、没 Key 提示不指路）→ 用户定 2.0.3 改为必须更新、改提示，89-MF04b 重打（完成，合入 main 497a2a31）；②2.0.3 已签名上线（2026-10-09，SYS-06）；③待做：用户在已装的 2.0.2 上退出登录、用微软邮箱注册新账号、点更新到 2.0.3、填 Key 新建计划，走通后再发给对方。对方用自己的 DeepSeek Key。收到回执时，回复里列出本批还差谁的回执。

## 协作方式

- **不懂就问（用户 2026-10-05 定）**：有任何不懂或感觉有偏差，直接问用户。要读特别大的东西先告诉用户，机械性的读取整理交给员工读好带回结论。
- **动用户数据要先问（用户 2026-10-10 定）**：会让用户已有数据失效、被挡或被隐藏的设计，必须先经用户同意；改数据格式一律带迁移（CURRENT_STATE [ARCH-03]）。
- **先讨论、不堆屎山（用户 2026-10-05 定，最优先）**：问题先和用户讨论清楚（现象、原因、牵连范围、前置依赖），再写任务；不出看着笃定但有漏洞的方案，宁可慢。用户催时也只针对当前测出的问题，把所有能并行、不冲突的改动一次派完；有前置依赖的写明先后与原因。CURRENT_STATE 开头「工作准则」同此。
- **验收测试不和修复并行（用户 2026-10-05 定）**：修复任务回执合入后，再在新基线上发验收/测试任务；不让测试跑在已知要改的旧代码上。
- **不重复造轮子（用户 2026-10-04 定，硬规定）**：每份任务开头第一节固定写“用的是哪个现成项目、许可证是什么”；要自己写的，写明为什么没有现成的可用。没有这一节或理由站不住的任务，用户直接打回，不转给员工。
- **架构师省额度（用户 2026-10-04 定）**：架构师只读员工交上来的结论，自己不跑测试、不翻大日志；合入后的全量测试与离线完整场景也交给员工跑；回用户的话尽量短。
- **流程**：架构师（Claude）写任务 → 用户转发给员工（GPT/Codex，可能是较弱的模型）→ 员工在自己工位完成 → 用户把回执贴回 → 架构师审查、合入主目录、本地提交。员工不改主目录、不推送、不 ssh、不部署、不碰正式签名私钥。推送 GitHub 要等用户说。
- **分工**：前端、老师/判定/检验等核心编排由架构师自己做；员工做边界清楚、耗时的单模块任务。架构师自己动手的标准：GPT 员工做不好、而架构师做起来便宜的事（例如 BUG-34 这种小修）。员工的结论要用真实数据核对后才采信。
- **先讨论后派活**（2026-10-02 用户再次强调）：每次收到回执，回复里只讲审查结果与发现的问题、给建议和要用户定的事，不附下一批任务文本；用户讨论完、同意了，再一次写出整批任务文本放在回复最后，然后停下等用户转发。不要“拿到回执就直接根据回执把下一批发出来”——那样效率低、大部分时间浪费。用户说“先说问题”时同理只讲问题。
- **一批派完、不挤牙膏**：每轮把当前适合的任务一次派出；同时派几个员工不设上限，以开发效率为准（2026-10-01 用户定，取代原先最多 3 个），但改同一文件同一部分的任务要排先后，避免合并冲突；不派无意义的活；用户没要求的事（例如更新价格表）先问再做。
- **架构师省着用**：测试、跑数据、复查记录这类耗量大的活尽量派给员工，架构师只做判断、审查和合入（2026-10-01 用户提出：架构师自己做消耗太大）。
- **状态如实**：写好的任务在索引里标“已起草，待用户转发”；用户说“发了”才改“进行中”；回执回来后改“已合入/已完成（部分）”。
- **员工自主**：每条任务写清决策原则，员工遇到选择自己决定、在回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停。
- **防止无穷无尽地改**（2026-10-01 用户担心“一直跑没有进步、改个无穷无尽”）：每个问题最多两轮——第一轮按现方法修，没解决就第二轮换方法（先查开源项目和论文）；第二轮还不行，带数据找用户定（接受现状、调整目标或换方案），不再自行反复调。每条任务只做一轮“实现 → 测 → 报告”，写明完成标准；没达标不在任务里继续微调，回执写清没过的样本、原因判断和建议换的方法。不追求把测试做大，看的是发现的问题有没有解决。
- **内测阶段不打包、不发版**（用户 2026-10-04 定，替换 2026-10-02 的“每合入一批就发新版本”）：功能还没跑通前不打包；测试在源代码上用模拟工具跑（DIR-01“测试”）；等用户说可以发了再打包、签名、上传。员工负责打包（不签名），签名、上传由架构师做（私钥与服务器不给员工）。
- **派任务前的检查**（2026-10-03，复盘“部分完成”太多后定）：①每条完成标准都必须是这个员工凭自己的权限能做到的，不依赖别的并行任务、外部网站或用户操作；取决于外部的写成“查清后二选一”，两种结果都算完成；②互相依赖的工作不拆成并行任务各自要求全量通过——要么一个人做，要么并行任务只要求本块测试通过、全量放到联调任务；③写“怎么做”之前先读相关链路的代码（例如更新程序自身怎么升级），并检查环境（服务器、已装软件、路径）。
- **方案由架构师出**（2026-10-02 用户定）：发现的问题由架构师查证据、找开源项目和论文、想清楚做法，在任务里写明“怎么做”，员工照着做；不写“你去解决”让员工自己想方案（员工对项目的理解没有架构师深，容易偏，效率低）。测试剧本也要想仔细，不能把测试里的人为限制当成用户需求（第 30–34 批的“每模块 3 点”就是例子）。
- **先跑真实流程，再看记录定修什么**（2026-10-02，回顾第二十五到三十批后定）：找问题靠完整的真实流程（小测试脚本）和对真实记录的逐条错误分析，不靠给单个组件自定目标；修复任务只针对真实记录里找到的失败，完成标准写成“这个失败不再出现”的二元判断，用同一个脚本或保存的记录复测。一个任务只做一件事：要么实现修复，要么做评测，不把两者和小预算捆在一起。固定评测集长期保留并在每次相关改动后回放：判定考卷（.local/test-tmp/judge-holdout，MF-06 保管）、出题回归集（tests/refactor 下 28-MF04 的回放）、讲稿埋错集（.local/test-tmp/prep-cost）、完整流程脚本（tests/refactor/acceptance/test_math_complete_flow_real.py）。
- **花钱的测试**：只为具体改动花钱，事先写下预期效果；能离线的先离线（假模型、重放保存的请求）；预算分两段，第一段没达到预期就停；派之前告诉用户每个任务的把握和预算。真实模型用管理员现有的 DeepSeek Flash 预设（Key 只在 Windows 凭据里，从不显示）。
- **功能先行**：功能没做完前任务里一律禁止加安全加固（AGENTS.md）；审查时删掉员工多加的加固。
- **问题留档**：每修好一个问题，同一次提交里在 CURRENT_STATE 记 BUG 条目（AGENTS.md 规则 7）。
- **部署**：每次改服务器都先向用户说明改什么、拿到同意再做。
- **向用户汇报**：合入后开头先给一张“环节 × 状态”表（通过/未通过/没测到），再说问题和建议；数字要和报告一致。
- **署名**：主目录提交一律用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不要写进共享的 git 配置（工位 worktree 共用它）。员工提交用各自工位名（见任务模板）。

## 架构师操作手册

- **全量测试**（约 9～10 分钟，2026-10-08 为 3167 通过、28 跳过）：先建好父目录，再 `TMP=D:/MentorFlow/.local/tmp TEMP=D:/MentorFlow/.local/tmp .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/test-tmp/<子目录>/pt`（basetemp 必须在 test-tmp 下；输出重定向到文件、放后台跑，跑完删子目录）。合入的员工提交若基线与主线代码完全相同（中间只有文档提交），员工的全量结果就算数，不必重跑；两份以上改动合在一起时要重跑。
- **启动/重启用户的软件**（用户在源代码上亲测，BUG-58 后的启动方式）：`Start-Process -FilePath D:/MentorFlow/.venv/Scripts/pythonw.exe -ArgumentList 'D:/MentorFlow/refactor/desktop/source_start.pyw' -WorkingDirectory D:/MentorFlow -WindowStyle Hidden`（工作目录 D:\MentorFlow；无控制台窗口，退出原因、崩溃栈、每 5 分钟内存线程都写进 D:\MentorFlowData\account-shell\logs\）。重启 = 先对 pythonw/python 里命令行含 source_start 或 account_shell_main 的进程 CloseMainWindow、等几秒、再强制结束，然后用上面命令启动。用户可能正在上课，重启前一定先问。不要用会超时的后台任务启动。用户机器上另有 Kristina 的语音引擎（D:\Kristina_Runtime，kristina.bridge.launcher）约 8 GB，不是本项目的，不碰。
- **查用户软件的问题**：日志 D:\MentorFlowData\account-shell\logs\mentorflow.log（JSON 行，只读）；数据库在同目录上一级 learning\accounts\<账号>\*.sqlite3，只读查看用 `sqlite3.connect('file:...?mode=ro', uri=True)`，不改不删；Windows 崩溃看事件查看器 Application 日志 1000/1001。
- **界面构建**：先把 `/d/MentorFlow/.local/node` 加进 PATH，再 `node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 不进仓库（.gitignore），打包时现构建；合入改过界面的任务后要构建一次确认能过。
- **合入员工分支**：`git diff --binary <基线> <员工提交> | git apply --index --3way`，逐文件看改动是否在任务范围内，跑全量测试和界面构建，再用上面的署名提交。多人同时改 app/classroom.py 时手工合并冲突。
- **基线**：合入并提交后，主目录 `git rev-parse --short HEAD` 就是下一批任务的基线，写进每条任务的“开工同步”。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc（会被吃掉，BUG-10），写成文件再运行，或用编辑工具。
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，用户 mentorflow-admin，用 sudo -n）。线上后端是 mentorflow-slim-backend:MF-75（2026-10-07 上线，永久登录，数据库 0043），切换脚本 /opt/mentorflow/state/mf75/switch.sh；回到 MF-14 用 `sudo bash /opt/mentorflow/state/mf75/switch.sh rollback`；上线前数据库备份 /opt/mentorflow/state/mf75/mentorflow-before-mf75.dump。更早的 MF-14 切换脚本与备份仍在 /opt/mentorflow/state/mf14/。业务库 mentorflow 所有者是 mentorflow_migrator，迁移用超级用户连接加 options=-c role=mentorflow_migrator。架构师上服务器需要用户把会话切到“每条命令都询问”并逐条批准（自动模式会拦生产读写）。服务器上只有管理员一个账号。
- **资料白名单目录**：服务器 /opt/mentorflow/state/mentorflow-e1-preview/catalog/（source-whitelist.json 129 条、model-prices.json；旧版备份 *.bak-20260929）；本机待发布副本在 .local/catalog-release/。发布前用客户端同一套格式检查，下发的文件不能带 "candidates" 键。
- **发布新版**（SYS-06）：打包用 D:\MentorFlow\.local\release-tools\build_release.py（README.txt 有用法，员工或架构师都能跑，不碰私钥）；签名后上传可参考 .local\test-tmp\release-2.0.2\ 里的 stage_missing.py（只传服务器缺的对象）、upload_each.sh（逐个传、逐个校验）、verify_public.py（公网验签）。原流程：用 scripts/build_learning_installer.py 的 freeze → release（签名清单，--valid-days）→ sign（私钥 .local/release-keys/mentorflow-app-2026.private.key，说明见同目录 README.txt）→ installer。上传到服务器 /opt/mentorflow/update-static/e108/app/ 的 .staging，逐个按 SHA256 校验后放入，最后原子替换 stable/stable.json；公网地址 https://89.45.45.28/mentorflow/updates/app/。给已装的 2.0.0 推的第一版必须 --valid-days 30；线上 stable.json 2026-10-28 过期。发布要用户同意。
- **磁盘**：测试与验收产物都在 .local/test-tmp/ 下。员工的临时目录架构师不删（用户 2026-10-05 定，员工可能还要用）；要清理先问用户。必须保留：installer/build-tools（打包工具）、release-2.0.1/payload（发版脚本的对照包）、已发布版本的构建目录（现为 release-2.0.2）、acceptance/（cache_hit/acceptance_replay.py 读它）。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | d33ca8a4（含第八十八批；2026-10-08 全量 3167 通过、28 跳过、0 失败）；派新任务前 `git rev-parse --short HEAD` 取 |
| 已发布 | 2.0.3 = 8747eeb8（必须更新，3 年签名，2026-10-09）；本机另有 2.0.2/2.0.3 安装程序（SYS-06）；内测阶段不打包；打包脚本 D:\MentorFlow\.local\release-tools\build_release.py |
| 未推送 | 2026-10-09 已推送到 GitHub（私有仓库）；以后推送仍要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07，七个人）转发任务；工位（library、runtime、client、platform、tools、classroom、server 七个目录）每次在任务里另行指定，不和员工固定绑定。派活前先看这张表：同一个员工、同一个工位同时只能有一个任务（2026-10-04 用户纠正：不能给正在跑的员工再派）。任务号写成“批次-员工编号”，例如 46-MF02 就是派给 MF-02。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 空闲 | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 93-MF04（已起草，待转发） | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

空闲工位：除 runtime 外六个。

## 待派队列

- 无。91-MF04（部分完成：actionlint 不认识 concurrency.queue，其余全过）与 92-MF07（完成）已于 2026-10-10 合入 main（b5336649）。下一步：用户在 GitHub 填 MF_SSH_KEY、确认 Billing 超额上限 0 → 架构师依次手动运行 mf-installer 2.0.2、mf-installer 2.0.3（latest）、mf-build 2.0.3（只构建），共 3 次，失败即停；之后用流水线发 2.0.4（含迁移）。每次运行记在这里：
  - 运行 1（2026-10-10）mf-installer 2.0.2：成功，约 10 分钟，releases/2.0.2/ 已有安装程序与清单。
  - 运行 2 mf-installer 2.0.3（latest）：进行中。
  - 运行 3 mf-build 2.0.4（上传到 staging，非强制，基线 2.0.3）：已排队（用户同意改为直接构建 2.0.4，不再空跑 2.0.3）。
- 93-MF04：已起草（2026-10-10），待用户转发。90-MF04 用户已转发且完成（fcff74bc，未合入）：拼包部分保留，Wine/Docker 部分由 91-MF04 删除，合入 91 时一起进 main。

## 第九十三批任务原文（2026-10-10，基线 d3693cc6）

### [93-MF04] runtime｜安装程序只留最近 3 版（¥0）

~~~text
【MentorFlow 第九十三批｜MF-04（工位 runtime）｜下载服务器只保留最近 3 个版本的安装程序】

现成项目与许可证（第一节）
  - 不新增依赖，只改你写的 scripts/release/server_ops.py（发布安装程序的那段）和对应测试；做法与常见软件的“只保留最近 N 个发布产物”一致。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard d3693cc6
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push、禁止在 GitHub 上触发工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md：中间产物用完即删，回执写明最终占用。测试用 D:\MentorFlow\.venv\Scripts\python.exe。遇到选择自己决定，回执写明理由，不停下来问。
同时在跑：只有本任务（架构师正在 GitHub 上用现有流水线发 2.0.4，你的改动下一次发版才生效）。
产物目录：D:\MentorFlow\.local\test-tmp\retain93\。目标一小时内交回。花费上限 ¥0。
背景（用户 2026-10-10 定，CURRENT_STATE [SYS-06]）：每版安装程序约 370 MB，服务器根分区剩约 36 GB。规则：releases/ 下只保留版本号最大的 3 个版本的安装程序 exe；更早版本的 exe 删除，但它们目录里的 stable.json 永远保留（需要时可用 mf-installer 从 objects 重新编出来）；latest 指向的版本永远不删；objects/ 不动。
要做
  1. 在 server_ops.py 发布安装程序成功（含切换 latest）之后执行清理：按版本号排序 releases/<版本>/，只对排名第 4 及以后的版本删除 MentorFlow-Setup-<版本>.exe，保留目录和 stable.json；跳过 latest 当前指向的版本（核对 latest/MentorFlow-Setup.exe 与哪个版本的 exe 是同一个文件）；删除前后写日志（删了哪些、释放多少），返回结果里带上这些信息。保留数做成常量 3，集中一处。
  2. 测试：5 个版本时删最早 2 个的 exe、stable.json 都在；latest 指向旧版本时不删它；不足 3 个时什么都不删；重复执行结果一样。mf-installer 对一个 exe 已被清理、只剩 stable.json 的旧版本再编一次时，能正常重新放回（写一条测试或本机模拟）。全量通过。
  3. scripts/release/README.md 加一段“保留规则”。
完成标准：上面测试全过；全量通过；提交在工位。
验收：报告写到 D:\MentorFlow\.local\test-tmp\retain93\report.md。
回执（尽量短，用表格）：【MF-04｜runtime｜第九十三批｜完成/部分完成/受阻】基线 ／ 提交 ／ 清理规则 ／ 测试 ／ 全量 ／ 本地最终占用
~~~

## 第九十二批任务原文（2026-10-10，基线 c88a6dfa）

### [92-MF07] library｜旧计划数据迁移（¥0）

~~~text
【MentorFlow 第九十二批｜MF-07（工位 library）｜旧计划数据迁移：更新后旧计划照常能学，去掉“旧版计划，已不可用”】

现成项目与许可证（第一节）
  - 不新增依赖。做法用行业通行的“随版本自动迁移数据”：本项目 course/store.py 已有 SCHEMA_VERSION 与 _migrate()（SQLite 按版本号逐级升级），这次在它上面加一级，不另写迁移框架。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard c88a6dfa
提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp（含 D:\MentorFlowApp\MentorFlow\Data）只读；D:\MentorFlowData\account-shell 只读：要用的数据库复制到测试目录再用。E 盘只读运行、不复制不写；不碰 D:\Kristina*。先读 AGENTS.md（磁盘规则、功能先行）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。不要结束或重启用户正在用的软件进程。
同时在跑：91-MF04（runtime）发版流水线，只改 scripts/、.github/，和你不冲突。
产物目录：D:\MentorFlow\.local\test-tmp\migrate92\。目标三小时内交回。花费上限 ¥0（不调用真实模型）。
背景（用户 2026-10-10 定，CURRENT_STATE [ARCH-03]）：第七十批改了题目存法后，course/store.py 的 _plan_availability 把“有旧格式题（没有 kind、挖空计分题、版本 2 里 kind 不是 objective/recall）或 material_version 不是 2/3”的计划标成 unusable，界面显示“旧版计划，已不可用”，课堂（app/classroom.py 约 797 行）、计划详情（app/plan_flow.py 约 2067 行、web/src/pages/PlanDetail.tsx 88 行）、备课（require_material_plan）都被挡；同步收到没有 kind 的旧题时（store.py 约 1732 行）也会把整份计划标成不可用。用户的话：“用户更新一下把计划搞没了是什么操作”。2.0.3 是第一个正式版，规矩是：任何更新都不能让用户已有的计划不能用，改数据格式必须带迁移。
目标：旧计划更新后照常能打开、能上课、能继续备课；学过的进度保留；只有按新规定不可信的旧题（AI 出的题、挖空计分题等第七十批以前的格式）退出考核，这些知识点下次备课按新办法从资料里找题，资料里没有就只学不考（DIR-01）。

要做
  1. 先盘点（写进报告）：旧计划可能长什么样——material_version 1/没有、2、3 各自的结构差异；旧题有哪些格式；新课堂和备课对一个计划/模块/知识点要求哪些字段（例如资料原文、知识点来源）而旧计划可能没有。用真实数据副本核对：D:\MentorFlowApp\MentorFlow\Data 下用户装好的 2.0.2 的课程库（找到 course.sqlite3 一类文件，复制出来）、D:\MentorFlowData\account-shell 下各账号的课程库（复制出来）、tests 里现有的旧计划夹具。每份副本列出：计划数、各版本数量、会被判旧的计划和原因。
  2. 迁移（SCHEMA_VERSION 加一级，在 _migrate 里做，幂等、可重复运行、失败整体回滚）：
     - 旧格式题不删除，改成“已退出考核”（例如 role='retired'，字段名你定，写明），出题、选题、计分、最终检验、统计都不再用它；历史作答记录照常可读。
     - 计划、阶段、模块、知识点、资料、学习进度、已过的模块检测、掌握状态全部保留，不重置。
     - 计划的 material_version 升到当前版本；旧题退出后，受影响的知识点/模块把备题状态标成“需要按新办法重新准备”，交给现有的后台备课流程照常去资料里找题（不在迁移里调模型）。
     - 旧计划缺的、新流程必需的字段：能从已有数据推出来的就补；推不出来的，让对应模块走现有“准备”流程重新补齐（写明哪些字段、怎么补）。不允许因为缺字段再把整份计划挡掉。
  3. 去掉“不可用”这条路：_plan_availability、require_material_plan、classroom 的 OLD_PLAN、plan_flow detail 的 unusable 分支、PlanDetail.tsx 的“旧版计划”显示，迁移后都不应再触发；同步收到没有 kind 的旧题时，按同样规则把这道题存为“已退出考核”，不再把计划标成不可用。保留代码里对真正损坏数据（读不出来的记录）的报错，那和“旧版”不是一回事。
  4. 验证（完成标准以这些为准）：
     a. 第 1 步找到的每份真实数据副本：迁移前后对比——计划数、模块数、知识点数、进度、已过检测数完全一致；原来判旧的计划迁移后能打开详情、能进课堂（离线走到第一条老师消息之前的接口即可）、备课状态正确；旧题数 = 退出考核数。
     b. 离线测试：版本 1、版本 2 有旧题、版本 2 无题、版本 3、同步收到旧题 五种情况各一条；迁移重复跑两次结果一样；迁移中途出错整体回滚。
     c. 全量测试通过；web 构建通过。
  5. 在 CURRENT_STATE 不用改（架构师改）；报告里写清迁移做了什么、对用户可见的变化（例如“这个模块的题需要重新准备”的提示文字）。
完成标准：4 的 a、b、c 全部通过；再也没有“旧版计划，已不可用”的路径；所有改动提交在工位。
验收：报告写到 D:\MentorFlow\.local\test-tmp\migrate92\report.md。
回执（尽量短，用表格）：【MF-07｜library｜第九十二批｜完成/部分完成/受阻】基线 ／ 提交 ／ 盘点结果（每份副本的计划数与判旧原因） ／ 迁移做了什么 ／ 真实副本前后对比 ／ 离线测试 ／ 全量与构建 ／ 用户可见变化 ／ 本地最终占用
~~~

## 第九十一批任务原文（2026-10-10，基线 office/runtime fcff74bc）

### [91-MF04] runtime｜发版流水线 + 下载地址（¥0）

~~~text
【MentorFlow 第九十一批｜MF-04（工位 runtime）｜发版流水线：GitHub Actions 手动触发的构建与发布 + 下载地址 aiteacher.clannad.win/download】

现成项目与许可证（第一节）
  - CI/CD：GitHub Actions（windows-latest 托管机器），官方 actions/checkout、actions/setup-python、actions/setup-node、actions/upload-artifact（均 MIT）。工作流语法检查用 actionlint（MIT）。
  - 编安装程序：Inno Setup 7.1.0 官方安装包（下载地址与 SHA256 写死，你上一轮已核对过），在 Windows 机器上直接跑，不用 Wine/Docker。
  - 复用本项目现有：scripts/build_learning_installer.py（freeze、release、installer）、你上一轮的 scripts/server_installer/assemble_payload.py（按签名清单从 objects 拼出安装目录）、D:\MentorFlow\.local\release-tools\build_release.py（打包总控，目前不在仓库里）。
  - 下载页做法参照常见软件（如 VS Code 的 code.visualstudio.com/download）：固定地址 302 跳到当前最新版的安装程序。
  - 要自己写的只有：把现有本机打包步骤搬进工作流的胶水脚本、上传/上线脚本、nginx 一个 server 块。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard fcff74bc         # 你上一轮的提交，在它上面继续
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push、禁止在 GitHub 上触发任何工作流（推送和触发只由架构师在用户同意后做）；禁止 ssh、连接服务器、部署；不碰正式签名私钥。D:\MentorFlowApp 及注册表安装记录只读，不运行安装程序；D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。本机不装 Docker/WSL。先读 AGENTS.md（磁盘规则、功能先行）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。测试用 D:\MentorFlow\.venv\Scripts\python.exe。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
同时在跑：只有本任务。
产物目录：D:\MentorFlow\.local\test-tmp\release-ci91\（里面已有 edge.conf.reference：服务器入口 nginx 的现行配置，只读参考）。目标四小时内交回。花费上限 ¥0。

背景（用户 2026-10-09～10 定，CURRENT_STATE [SYS-06]）：
  - 发版改成行业通行的流水线，不再在用户电脑上打包、不再从用户家里上传（用户上行到服务器约 0.15 MB/s）。签名仍在用户电脑上由架构师做，私钥绝不进 GitHub。
  - 额度红线（用户被坑过：以前一天把 GitHub Actions 跑了几千次、额度用光、账号受限一个月）：工作流只能 workflow_dispatch 手动触发，不许 push/pull_request/schedule 等任何自动触发；同一时间只跑一个（concurrency 组，排队不并发）；每个 job 设 timeout-minutes（构建 ≤60，发布/安装程序 ≤40）；GitHub 上不跑测试（测试照旧在本机）；不用矩阵。
  - 服务器访问：仓库 Secrets 里放现有管理员账号 mentorflow-admin 的 SSH 私钥（由用户自己填，名字定为 MF_SSH_KEY）；主机公钥是公开信息，直接写进工作流：89.45.45.28 ssh-ed25519 用 D:\MentorFlow\.local\server\ssh_config 里 UserKnownHostsFile 中 89.45.45.28 那一行（只读取公钥行）。服务器上 sudo 免密。
  - 下载地址：https://aiteacher.clannad.win/download，点了直接下载当前最新版安装程序；子域名根地址以后放官网，现在返回简单占位（一个标题 + 下载按钮即可，正式页面以后做）。每个版本的安装程序都保留：releases/<版本>/MentorFlow-Setup-<版本>.exe，同目录放该版签名清单 stable.json；releases/latest 是指向当前最新版本目录的软链接。2.0.3 是第一个正式版，先把 2.0.2、2.0.3 两个版本的安装程序放进去，latest 指向 2.0.3；旧的 installer/MentorFlow-Setup.exe（9-29 老版本）由发布脚本挪到 /opt/mentorflow/state/ 备份。
  - 服务器现状：更新文件目录 /opt/mentorflow/update-static/e108/app/（objects/、stable/stable.json、installer/、voice/），由容器 mentorflow-e108-update-static（容器内 8081）提供，入口 nginx 容器 mentorflow-e1-preview-edge-1 的配置文件在宿主机 /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf（挂到 /etc/nginx/nginx.conf），证书在 docker 卷 mentorflow-e1-preview_edge_secrets（容器内 /run/secrets）。auth.clannad.win 已经按“Cloudflare 代理 + Cloudflare Origin 证书 + 只放行 Cloudflare 来源”接在这台服务器上（见参考配置 240 行起），aiteacher 照抄这个模式。2.0.2 的签名清单在服务器 /opt/mentorflow/state/stable-2.0.2-before-2.0.3.json，2.0.3 的就是线上 stable/stable.json。

要做
  1. 清理上一轮：删掉只为“服务器上用 Wine 编”写的部分（Dockerfile、build_on_server.sh、run_build.py、make_bundle.py 及对应测试、build_learning_installer.py 里只为 Wine 加的分支），保留 assemble_payload.py 及其测试。把 .local\release-tools\build_release.py 搬进仓库 scripts/release/（去掉写死的本机路径，改成参数或环境变量，本机用法不变）。
  2. 依赖锁定：新增 scripts/release/requirements-build.txt（Python 3.12.10，版本全部写死，和本机 .venv 与 .local\test-tmp\installer\build-tools 里实际打包用的版本一致）；前端用 refactor/learning/web/package-lock.json + npm ci，Node 版本写死与本机一致。
  3. 三个工作流（.github/workflows/，都只有 workflow_dispatch）：
     a. mf-installer.yml（输入：版本号；是否设为 latest）：从服务器取 releases/<版本>/stable.json（2.0.2、2.0.3 第一次没有时，按上面给的两个位置取）→ 用包里的信任公钥验签 → 按清单从服务器公网 https://89.45.45.28/mentorflow/updates/app/objects/ 下载全部文件（证书是自签的，靠清单签名和逐文件 SHA256 保证正确）→ assemble_payload.py 拼目录 → Inno 7.1.0 编安装程序 → 通过 SSH 传到服务器 releases/<版本>/ 的临时文件名，核对 SHA256 后原子改名 → 需要时原子切换 releases/latest 软链接。2.0.2、2.0.3 现在就用它。
     b. mf-build.yml（输入：版本号、更新说明、是否必须更新及原因、基线版本；另有“只构建不上传”开关）：本机 build_release.py 的 freeze + release 那一段搬到这里 → 产出未签名清单（作为小 artifact，保留 3 天）→ 不是“只构建”时，把服务器缺的文件传到服务器 .staging-<版本>/objects/（逐个核对 SHA256）。payload 本身不做 artifact（太大，占存储额度）。
     c. mf-publish.yml（输入：版本号）：前提是架构师已把签好名的 stable.json 放到服务器 .staging-<版本>/stable/stable.json → 验签 → 把 staging 里的文件挪进 objects（已有的不覆盖）→ 备份线上 stable.json 到 /opt/mentorflow/state/ → 原子替换 stable.json → 调 mf-installer 的同一套步骤编并上传这个版本的安装程序、切 latest（用可复用工作流或复合 action，不复制粘贴）→ 删 staging。
     所有工作流：开头写清用途和额度红线的注释；concurrency 组 mentorflow-release；timeout-minutes 按上面；缓存 pip 与 npm。
  4. 签名衔接：写一个本机小脚本 scripts/release/sign_and_stage.py（架构师用）：从 GitHub 下载的 artifact 里取未签名清单 → 用 build_learning_installer.py sign 签名（私钥路径作参数，脚本不保存、不打印私钥）→ 用 scp 把签好的 stable.json（约 2 MB）传到服务器 .staging-<版本>/stable/。
  5. 服务器 nginx：写一个 aiteacher.clannad.win 的 server 块（照抄 auth.clannad.win 的写法：listen 8443 ssl、证书放 /run/secrets/e110-aiteacher-origin/cert.pem 与 key.pem、只放行 Cloudflare 来源）：location = /download 302 到 /releases/latest/MentorFlow-Setup-<版本>.exe 的方式要能自动跟随 latest（例如 latest 目录里放一个不带版本号的同名文件或用 try_files，选最简单可靠的，写明理由）；location ^~ /releases/ 只允许 GET/HEAD，转发到 update-static 容器；location = / 返回占位页；其余 404。下载响应带 Content-Disposition: attachment。确认 update-static 容器的 nginx 能跟随软链接（看它的配置能看到的部分，看不到就写成架构师上线时要检查的一项）。写一份 deploy-nginx.md：架构师改配置、nginx -t、reload、回退的完整命令。
  6. 本机验证（完成标准以这些为准）：
     a. actionlint 检查三个工作流无错误；用脚本确认每个工作流只有 workflow_dispatch、都有 concurrency 和 timeout-minutes。
     b. 工作流里调用的每个脚本都能在本机单独跑：在测试目录里模拟服务器目录（objects/、stable/、releases/、.staging-*/），用 2.0.3m 的清单和 payload 走一遍“拼包 → 编安装程序 → 放进 releases/2.0.3/ → 切 latest”，以及“staging → 上线”的整套文件操作（把 SSH 换成本地文件操作的开关），结果目录结构写进报告。
     c. mf-build 的 freeze + release 在本机用新的 requirements 和 npm ci 跑通一次（只构建不上传），和上一轮 release-2.0.3m 的文件数量一致。
     d. 全量测试通过。
  7. 写 docs-ready 的说明 scripts/release/README.md：以后发一个版本的完整步骤（谁在哪一步做什么、点哪个工作流、输入什么、预计各用多少分钟额度），以及第一次启用要做的事：用户在 GitHub 填 MF_SSH_KEY、确认 Billing 超额上限为 0；用户在 Cloudflare 加 aiteacher 的 DNS 记录（代理开启）并生成 Origin 证书；架构师上线 nginx；然后依次手动运行 mf-installer（2.0.2）、mf-installer（2.0.3，设为 latest）、mf-build（2.0.3 代码，只构建不上传，验证构建能跑通）——总共 3 次，失败就停下查原因，不重试第 4 次。
完成标准：6 的 a～d 全部通过；三个工作流和脚本、nginx 片段、两份说明齐全；所有改动提交在工位。
验收：报告写到 D:\MentorFlow\.local\test-tmp\release-ci91\report.md。
回执（尽量短，用表格）：【MF-04｜runtime｜第九十一批｜完成/部分完成/受阻】基线 ／ 提交 ／ 清理了什么 ／ 依赖锁定 ／ 三个工作流（触发方式、并发、时限） ／ actionlint ／ 本机模拟发布结果 ／ 本机构建对比 ／ 全量 ／ 每次运行预计额度 ／ 用户要做的事 ／ 架构师要做的事 ／ 本地最终占用
~~~

## 第九十批任务原文（2026-10-09，基线 2bc131a0）

### [90-MF04] runtime｜服务器自己编安装程序（¥0）——用户已转发并完成（fcff74bc），方案改为 91-MF04，未单独合入

~~~text
【MentorFlow 第九十批｜MF-04（工位 runtime）｜服务器自己编安装程序：按发布清单从已有更新文件拼出完整版，不再从本机上传几百 MB】

现成项目与许可证（第一节）
  - 编安装程序：Inno Setup（自由许可，可商用），与本机现用版本一致（E:\Codex\App\InnoSetup-7.1.0），在 Linux 上用 Wine（LGPL）运行；容器做法参照开源镜像 amake/innosetup（MIT，GitHub amake/innosetup-docker）的 Dockerfile 写法，但 Inno 版本要换成 7.1.0（官方安装包，写死下载地址和 SHA256）。
  - 读包内信任信息沿用现有 scripts/build_learning_installer.py 的 installer 步骤（PyInstaller 的 CArchiveReader，版本与 .local/test-tmp/installer/build-tools 里的一致；Pillow；cryptography）。不新写一套校验和打包逻辑，只把它改成能在 Linux 容器里跑。
  - 不用自己写的理由：没有现成工具能“从按内容存放的更新文件 + 签名清单”还原出我们的安装目录，这一小段（拼文件）只能自己写，其余全部复用。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 2bc131a0
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署（服务器上的运行由架构师做）；不碰正式签名私钥。D:\MentorFlowApp 及其注册表安装记录只读，不在本机运行任何安装程序；D:\MentorFlowData\account-shell 只读；E 盘只读运行（ISCC 只读调用）；不碰 D:\Kristina*。本机没有 Docker/WSL，也不要安装（属于改系统设置）。先读 AGENTS.md（磁盘规则、功能先行）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。测试用 D:\MentorFlow\.venv\Scripts\python.exe。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
同时在跑：只有本任务。
产物目录：D:\MentorFlow\.local\test-tmp\server-installer90\。目标三小时内交回。花费上限 ¥0。
背景（用户 2026-10-09 定，CURRENT_STATE [SYS-06]）：每发一版，更新文件只传改动的部分（按 SHA256 存在服务器 /opt/mentorflow/update-static/e108/app/objects/<sha256>，清单 stable/stable.json 的 signed.full_release.assets 列出每个文件的 path、sha256、size）。但安装程序 installer/MentorFlow-Setup.exe 一直是本机编好后整个上传（368 MB，用户线路约 0.15 MB/s），现在服务器上的还是 9-29 的旧版。用户要求：服务器上已经有新版的全部文件，就让服务器自己拼出完整安装目录、自己编安装程序、自己替换旧的，以后每次发版都这样，本机不再上传安装程序。
服务器情况（架构师已查）：Ubuntu，Docker 29.7，4 核 7 GB 内存，根分区剩 36 GB；更新目录在 /opt/mentorflow/update-static/e108/app/（installer、objects、stable、voice），备份放 /opt/mentorflow/state/。

要做（脚本放 scripts/server_installer/，测试放 tests/refactor/installer/，文件名全 tests/ 唯一）
  1. assemble_payload.py：输入 已验签的 stable.json + objects 目录 + 输出目录；按 assets 把每个 objects/<sha256> 复制（能硬链接就硬链接）成 payload/<path>，逐个核对 SHA256 和大小；最后用现有 per_user_install.verify_tree 对拼好的目录做整体校验。缺文件、哈希不对就报错退出，不出半成品。
  2. 让 installer 步骤能在 Linux 容器里跑：把 build_learning_installer.py 的 installer 步骤里只能在 Windows 跑的部分（路径、调用 ISCC 的方式）改成同时支持“通过 Wine 调 ISCC”（Linux 路径转 Wine 的 Z:\ 路径），Windows 上的现有行为不变、现有测试照过。Inno 脚本 refactor/desktop/packaging/inno/* 如有 Windows 专属写法要调整，改动最小并写明。
  3. Dockerfile：基础镜像固定版本；装 Wine、Inno Setup 7.1.0（官方安装包静默安装，下载地址与 SHA256 写死）、需要的 ChineseSimplified.isl（Inno 7 自带就用自带，没有就说明来源与许可）、Python 与 PyInstaller/Pillow/cryptography（版本写死，PyInstaller 与本机 build-tools 一致）。镜像只编安装程序，不联网下载别的。
  4. build_on_server.sh（架构师在服务器上跑）：读线上 stable/stable.json 并验签（用包里的信任公钥）；若 installer/VERSION.txt 已是这个版本就直接退出；否则在 /opt/mentorflow/state/installer-build/<版本>/ 下拼 payload、在容器里编安装程序；成功后：旧安装程序备份到 /opt/mentorflow/state/installer-<旧版本或日期>.exe → 新的先写 installer/.MentorFlow-Setup.exe.new → 原子替换 installer/MentorFlow-Setup.exe → 写 installer/VERSION.txt（版本、SHA256、大小、时间）→ 删掉 installer-build 下的中间产物。任何一步失败都不碰线上安装程序。一条命令、可重复运行；第一次运行顺带构建镜像。
  5. 本机能做的验证（完成标准以这些为准）：
     a. 用 D:\MentorFlow\.local\test-tmp\release-2.0.3m\payload 和 stable\stable.json 在测试目录里模拟一个按 SHA256 存放的 objects 目录，跑 assemble_payload.py 拼出 payload，与原 payload 逐文件比对完全一致（文件数、路径、内容）。
     b. 用拼出的 payload 在本机按现有方式（Windows ISCC）编出 2.0.3 安装程序，编译成功（不运行安装）。
     c. 离线测试：缺文件、哈希错、清单验签失败各有一条，确认都不出半成品；Windows 现有 installer 测试照过；全量通过。
     d. Linux/Docker 部分本机跑不了：写清楚架构师在服务器上的完整步骤（把 scripts/server_installer/ 与运行所需的仓库文件打成一个小包放到哪、执行哪条命令、预期每一步输出什么、常见失败怎么判断），以及第一次运行大约要下载多少、占多少盘、跑多久的估计。
  6. 写 scripts/server_installer/README.md：以后每次发版的顺序 = 本机打包签名 → 只传缺的更新文件 → 换 stable.json → 服务器上跑 build_on_server.sh。
完成标准：a、b、c 全部通过；d 的服务器步骤写清楚到架构师能照着一次跑通；所有改动提交在工位。
验收：报告写到 D:\MentorFlow\.local\test-tmp\server-installer90\report.md。
回执（尽量短，用表格）：【MF-04｜runtime｜第九十批｜完成/部分完成/受阻】基线 ／ 提交 ／ 拼包比对结果 ／ 本机编译 ／ 离线测试与全量 ／ 服务器上要跑的命令 ／ 镜像与 Inno 版本、来源与许可 ／ 预计下载、占盘、耗时 ／ 本地最终占用
~~~

## 第八十九批任务原文（2026-10-09，基线 81461194）

### [89-MF04b] runtime｜改没 Key 提示，重打 2.0.3 为必须更新（¥0）

~~~text
【MentorFlow 第八十九批｜MF-04（工位 runtime）｜89-MF04b：改一句没 Key 的提示，重打 2.0.3 为“必须更新”版】

现成项目与许可证（第一节）
  - 只用本项目现有工具，不新增依赖：打包同上一轮 D:\MentorFlow\.local\release-tools\build_release.py（README.txt 写了强制版本参数）；提示文字在 refactor/learning/connections/gateway.py、client.py。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 153c41fd
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥（.local/release-keys 不读）。D:\MentorFlowApp 及其注册表安装记录只读；D:\MentorFlowData\account-shell 只读；E 盘只读运行（ISCC 只读调用）；不碰 D:\Kristina*。先读 AGENTS.md（磁盘规则、功能先行）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
同时在跑：只有本任务。
产物目录：新建 D:\MentorFlow\.local\test-tmp\release-2.0.3m\（不要覆盖上一轮的 release-2.0.3，上一轮目录在本任务成功后删除，被拦就列路径）。目标一个半小时内交回。花费上限 ¥0。
背景：你上一轮（89-MF04）的报告发现两点。用户 2026-10-09 定：2.0.3 改成“必须更新”（2.0.2 里建的旧计划升级后按设计作废，强制更新让外部新用户登录后只能先更新，碰不到这种情况）；没 Key 的提示要说清去哪里填。

要做
  1. 改提示文字（只改文字，错误码不变）：
     - refactor/learning/connections/gateway.py 的 MODEL_NOT_CONFIGURED：改为“还没有可用的模型：请到 设置 → 模型连接 选择厂商、填入 API Key 并保存。”
     - gateway.py 与 client.py 的 MODEL_KEY_REQUIRED：改为“当前模型还没有 API Key：请到 设置 → 模型连接 填入 API Key 并保存。”
     - 搜一遍 refactor/ 下前端与后端有没有别处显示同类“没配模型/没 Key”的提示，一并改成指向 设置 → 模型连接（列在回执里）。补一个离线测试断言这两条提示含“设置 → 模型连接”（测试文件名全 tests/ 唯一，例如 tests/refactor/connections/test_model_key_hint89.py）。全量测试与 web 构建通过后提交，记下提交号 <C>。
  2. 用 <C> 重打强制版：
     & 'D:\MentorFlow\.venv\Scripts\python.exe' -B 'D:\MentorFlow\.local\release-tools\build_release.py' --version 2.0.3 --baseline <C> --valid-days 1096 --base-versions 2.0.2 --mandatory --mandatory-reason data_compatibility --notes '必须更新：学习方式大改，2.0.2 里建的计划在新版不能继续用。题目只来自资料或你上传的题；新课堂与知识地图；可选老师人设；用量按天/周/月显示；加载过场；稳定性修复' --output-dir 'D:\MentorFlow\.local\test-tmp\release-2.0.3m'
  3. 核对：unsigned-release.json 里 mandatory 为真、原因 data_compatibility、mandatory_floor 为 2.0.3；按上一轮 B 路线（测试密钥、per_user_install.transact）把“2.0.2 → 2.0.3m”离线再走一遍；读 4430aae 的 refactor/desktop/updates.py 与 web/src/updates/UpdateNotice.tsx，用 2.0.2 的代码确认：①新装 2.0.2 第一次启动时登录前检查只用本地已存的签名信息（check_feed=False），因此先能注册登录；②登录后检查到 2.0.3 必须更新时，弹出的是全屏、不能关闭、只有“立即更新”的框；③更新后再启动，登录前检查是否就会拦住旧版。三条各写结论和依据（代码行）。
完成标准：两条提示改好且有测试；全量通过、web 构建通过；强制版 2.0.3 未签名包打出且字段核对正确；离线更新排练通过；2.0.2 行为三条有结论。
验收：报告写到 D:\MentorFlow\.local\test-tmp\release-2.0.3m\report.md。
回执（尽量短，用表格）：【MF-04｜runtime｜第八十九批 b｜完成/部分完成/受阻】基线 ／ 提交 <C> ／ 改了哪些提示 ／ 全量与构建 ／ 打包与字段核对 ／ 离线更新排练 ／ 2.0.2 行为三条 ／ 架构师签名与编安装程序命令（脚本打印的那份） ／ 本地最终占用
~~~

### [89-MF04] runtime｜打包 2.0.3 并排练新用户装 2.0.2 → 点更新到 2.0.3（¥0）

~~~text
【MentorFlow 第八十九批｜MF-04（工位 runtime）｜打包 2.0.3 并排练“新用户装 2.0.2 → 点更新到 2.0.3”整条链路】

现成项目与许可证（第一节）
  - 只用本项目现有工具，不新增依赖：打包用 D:\MentorFlow\.local\release-tools\build_release.py（先读同目录 README.txt）；底层是 scripts/build_learning_installer.py（PyInstaller，GPL 例外条款允许打包分发；安装程序 Inno Setup，免费许可）；更新链代码 refactor/desktop/delivery/。不自己写新的更新或安装机制。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 81461194
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥（.local/release-keys 不读）。可以只读 GET 公网更新地址 https://89.45.45.28/mentorflow/updates/app/（软件自己也这样访问）。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp（用户装好的 2.0.2）和它在注册表里的安装/卸载记录只读：任何可能覆盖、改动它们的操作都不做。用户正在用 D:\MentorFlowData\account-shell：只读。E 盘只读运行、不复制不写（Inno 编译器 E:\Codex\App\InnoSetup-7.1.0\ISCC.exe 只读调用）。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。不要结束或重启用户正在用的软件进程（从 D:\MentorFlow 主目录启动的 source_start）。
同时在跑：只有本任务。
产物目录：D:\MentorFlow\.local\test-tmp\release-2.0.3\（打包输出）和 D:\MentorFlow\.local\test-tmp\rehearse89\（排练）。目标三小时内交回。花费上限 ¥0（不调用真实模型）。
背景：用户要把软件发给第一个外部新用户。对方会装 2.0.2 安装程序（2026-10-03 发布，代码 4430aae），注册新账号，然后点更新到 2.0.3（= 现在的 main 81461194，含第四十四到八十八批）。这条链路从没有外部用户走过。签名和上传由架构师做；注册新账号只有用户能做，由用户在发布后亲自走一遍。你负责发布前能在本机查出的一切问题。

要做
  1. 打包 2.0.3（不签名）：
     & 'D:\MentorFlow\.venv\Scripts\python.exe' -B 'D:\MentorFlow\.local\release-tools\build_release.py' --version 2.0.3 --baseline 81461194 --valid-days 1096 --base-versions 2.0.2 --notes '题目只来自资料或你上传的题；新课堂与知识地图；可选老师人设；用量按天/周/月显示；加载过场；稳定性修复' --output-dir 'D:\MentorFlow\.local\test-tmp\release-2.0.3'
     非强制版本（不传 --mandatory）。打包失败就修打包层面的问题（漏打的数据文件、新依赖、隐藏导入等），改动最小、提交在工位并在回执列出；产品逻辑不改，发现产品问题写进报告。
  2. 从 2.0.2 到 main 的兼容性代码核对（读 git diff 4430aae..81461194，重点 refactor/desktop/、refactor/desktop/delivery/、packaged_main、数据路径相关代码），逐项写结论：
     a. 2.0.2 的更新程序（Updater 不随 App 更新）启动 2.0.3 的 App：命令行参数、环境变量、进程交接、健康检查（launch_health）是否仍对得上；
     b. 安装版的数据目录（安装目录\Data，shell_paths.prepare_shell_runtime）有没有被源代码版的 D:\MentorFlowData 等写死路径替代或绕过；
     c. 本机数据库/存储结构在 2.0.2 之后有没有变；如有，2.0.2 产生的数据（新用户刚注册登录、可能已建了一点东西）升到 2.0.3 能否自动迁移；
     d. 更新后自动登录（42-MF05）在“2.0.2 发起更新”这一边是否生效；不生效就写明“更新后需重新登录一次”，不算失败。
  3. 新用户首启检查（用打包出的 payload，在 rehearse89 下的全新空目录里，按安装版的方式启动，不读用户的任何数据）：没有旧数据、Windows 凭据里没有模型 Key、没有语音引擎、环境变量里没有代理。逐项确认并截图：能到登录窗；登录窗的“注册”“找回密码”入口能打开、表单能填（不提交）；packaged_main --packaging-smoke-root 离线后端检查通过；代码层面确认登录后没有 Key 时新建计划给出明确提示“去设置→模型连接填 Key”，而不是报错或卡住（可用离线测试或假账号走后端验证）；朗读默认关闭，首启不去找 E 盘或语音引擎。
  4. 2.0.2 → 2.0.3 实际安装更新排练（查清后二选一，两种都算完成）：
     A. 如果能在不触碰 D:\MentorFlowApp 及其注册表安装记录的前提下，在本机把 2.0.2 装进 rehearse89 下的目录（先读 Inno 脚本里的 AppId、UsePreviousAppDir、卸载记录写法，确认不会复用或覆盖用户那份），就用测试密钥搭本地更新源走一遍“2.0.2 → 2.0.3”：要么用打包脚本支持的测试公钥/测试更新地址，要么沿用此前 11-MF06“签名源 A→B”的做法；记录更新耗时、更新后能否启动到登录窗。
     B. 如果会碰到用户那份，或者 2.0.2 的信任公钥和更新地址写死、无法换成测试源，就不装，改为：用 2.0.2 与 2.0.3 两份 payload 和 per_user_install.transact 在测试目录里做离线的“装 2.0.2 → 换到 2.0.3”（测试密钥），并在报告里写明为什么不能做 A。
  5. 安装程序：如果打包脚本的 installer 步骤可以用占位、不签名的清单编出 2.0.3 安装程序，就编一份做首装检查；不行就写明需要签名后由架构师编。
完成标准：2.0.3 未签名包打出（或写清卡在哪一步、原因）；兼容核对 a～d 每项有结论；新用户首启检查每项有结论和截图；第 4 步按 A 或 B 完成；发现的问题按“会挡住外部新用户 / 不挡但难受 / 小问题”分三级列出，每条写证据。
验收：报告写到 D:\MentorFlow\.local\test-tmp\rehearse89\report.md（打包报告在 release-2.0.3\report.md）。
回执（尽量短，用表格）：【MF-04｜runtime｜第八十九批｜完成/部分完成/受阻】基线 ／ 提交 ／ 打包结果 ／ 兼容核对 a～d ／ 首启检查 ／ 第4步走了A还是B及结果 ／ 三级问题清单 ／ 架构师签名与编安装程序命令（脚本打印的那份） ／ 本地最终占用
~~~

## 第八十八批任务原文（2026-10-08，基线 56251799）

### [88-MF05] platform｜原生登录与启动窗口加载过场，与网页一致（¥0）

~~~text
【MentorFlow 第八十八批｜MF-05（工位 platform）｜原生登录与启动窗口也用同样的加载过场：不让用户对着空白或干转圈】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：refactor/desktop/ 下的登录与启动界面（login_shell.py、account_shell_main.py 里显示等待的地方）；用 PySide6 自带能力（QPropertyAnimation、QTimer、样式表）做动画，配色和文案跟 86-MF02 网页里的过场一致（读它的 CSS 取色值和文案风格）。不嵌网页、不引动画库。

开工同步
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard 56251799
提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑（各改各的，冲突由架构师合并）：87-MF04 语音引擎内存（speech.py、voice/tts、useTeacherVoice.ts、老师栏朗读开关）；88-MF05 原生登录窗口加载过场（desktop/ 下登录与启动界面）；88-MF06 后台任务初始状态（plan_flow.py 的 detail 字段、PlanDetail/KnowledgeMap 读取）。
主线已含 86-MF02 的统一加载组件（web/src，三档：＜300ms 不显示、0.3～3s 骨架、＞3s 过场＋文字、＞15s 慢提示与重试/取消），报告见 D:\MentorFlow\.local\test-tmp\fix86-loading\report.md，先读它的盘点表和组件说明。
产物目录：D:\MentorFlow\.local\test-tmp\fix88-login\。目标两小时内交回。花费上限 ¥0。
背景（用户 2026-10-08 定，CURRENT_STATE [UI-00]“加载规则”）：86-MF02 把网页里 38 类等待统一成加载过场，但启动时的登录窗口是 Qt 原生界面，不在网页里，没覆盖。用户要求补上。
要做
  1. 盘点原生界面里所有等待：软件启动到登录窗出现、点登录到进入主界面、自动登录与恢复会话、切换账号、退出登录、登录失败重试、会话恢复失败等；列表写进报告（现在显示什么、实测多久）。
  2. 按 86-MF02 的同一套三档规则做：很短不显示；中等显示轻量动画；较长显示与网页一致的过场（主题色、动效风格）＋一句说清正在做什么（例如“正在连接账号服务…”“正在恢复上次的登录…”）；超过 15 秒提示“比平时慢，可能是网络问题”并给重试（能取消的给取消）。系统开了减少动态效果就只保留静态文字。
  3. 主界面网页加载出来之前的空窗（登录成功到网页第一帧）也不能空白：显示同样的过场直到网页准备好。
  4. 不改登录逻辑、令牌、密码处理；83-MF05 的“只有用户明确退出才退出”和退出诊断保持不变。
  5. 实测：从工位启动（用复制的数据）走启动、登录、进入主界面，记录每段耗时和显示了什么；截图亮暗各一套（启动过场、登录中、慢提示）。离线测试覆盖三档切换和慢提示；全量通过、web 构建通过。
完成标准：原生界面里每处等待都有和网页一致的过场与文字；登录成功到网页第一帧不空白；登录逻辑不变；全量通过。
验收：报告和截图写到 D:\MentorFlow\.local\test-tmp\fix88-login\report.md。
回执（尽量短，用表格）：【MF-05｜platform｜第八十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 盘点处数 ／ 已覆盖 ／ 实测耗时 ／ 截图 ／ 全量与构建 ／ 本地最终占用
~~~

### [88-MF06] tools｜后台任务初始状态：detail 补分钟重算等状态字段，打开页面即显示过场（¥0）

~~~text
【MentorFlow 第八十八批｜MF-06（工位 tools）｜后台任务初始状态：打开页面时就知道知识点连线、分钟重算、后台备题是否还在跑】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：app/plan_flow.py（detail 返回字段，照 82-MF06 的 point_links_status 写法）、你 84/85 写的分钟重算流程、后台备题（_prepare_worker）现有状态；前端只改读取这些字段的地方（web/src/pages/PlanDetail.tsx、planning/KnowledgeMap.tsx 及 86-MF02 的加载组件调用处）。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard 56251799
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑（各改各的，冲突由架构师合并）：87-MF04 语音引擎内存（speech.py、voice/tts、useTeacherVoice.ts、老师栏朗读开关）；88-MF05 原生登录窗口加载过场（desktop/ 下登录与启动界面）；88-MF06 后台任务初始状态（plan_flow.py 的 detail 字段、PlanDetail/KnowledgeMap 读取）。
主线已含 86-MF02 的统一加载组件（web/src，三档：＜300ms 不显示、0.3～3s 骨架、＞3s 过场＋文字、＞15s 慢提示与重试/取消），报告见 D:\MentorFlow\.local\test-tmp\fix86-loading\report.md，先读它的盘点表和组件说明。
产物目录：D:\MentorFlow\.local\test-tmp\fix88-status\。目标一个半小时内交回。花费上限 ¥0。
背景（86-MF02 报告“部分完成”第 2 条）：分钟重算只有状态变化事件（plan.point_minutes_changed），没有初始状态字段；页面打开前已经开始的重算，前端不知道它还在跑，没法显示过场。知识点连线已有 point_links_status，可以照着做。
要做
  1. plan.detail 增加 point_minutes_status（none/running/ready/failed，和 point_links_status 同一套值），反映打开页面那一刻分钟重算的真实状态；检查后台备题等其他会在计划页面体现的后台任务，凡是界面需要显示进度而 detail 里没有初始状态的，一并补上同风格字段（列清楚补了哪些、没补的为什么不需要）。
  2. 前端在打开计划详情时就读这些初始状态，用 86-MF02 的加载组件显示（例如“正在按内容重算知识点时长…”），收到现有事件后照常刷新；完成后自然消失。
  3. 不让 detail 变慢（只读内存或已存状态，不调模型、不做重计算）。
  4. 离线测试覆盖：页面打开前已在跑 → 打开即显示；跑完 → 消失并刷新；失败 → 显示重试（有重试动作的）；全量通过、web 构建通过。截图：重算进行中、连线进行中，亮暗各一张。
完成标准：所有需要显示的后台任务打开页面就有正确初始状态并有过场；detail 不变慢；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix88-status\report.md。
回执（尽量短，用表格）：【MF-06｜tools｜第八十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 新增字段 ／ 前端显示 ／ detail 耗时前后 ／ 截图 ／ 全量与构建 ／ 本地最终占用
~~~

## 第八十七批任务原文（2026-10-08，基线 ec2ffe7e）

### [87-MF04] runtime｜内存：朗读关着不启动语音引擎、关朗读和空闲释放；查清主程序线程（¥0.3）

~~~text
【MentorFlow 第八十七批｜MF-04（工位 runtime）｜内存：朗读关着就不启动语音引擎、关掉朗读就释放；查清主程序 200 个线程】

现成项目与许可证（第一节）
  - 只改本项目现有代码，不新增依赖：refactor/learning/app/speech.py（warm_up、_start_service、_release_service）、refactor/learning/voice/tts/（service.py、engine_server.py、runtime.py）、前端动 web/src/teacher/useTeacherVoice.ts（autoWarm 默认值与调用处传参）和老师栏底部现有的朗读开关（让开关状态一眼看懂），Classroom.tsx 尽量少改。语音引擎是用户机器上 E:\AI_Tools 下的 GPT-SoVITS（只读运行，不改它的文件、不复制）。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard ec2ffe7e
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。D:\MentorFlowApp 只读。用户正在用 D:\MentorFlowData\account-shell：只读，不改不删，要用的数据复制到测试目录再用。E 盘只读运行、不复制不写。先读 AGENTS.md（磁盘规则、功能先行不加安全加固）：中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（不提交 dist）。遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据才停。全量测试、web 构建由你跑并如实写结果。不要结束或重启用户正在用的那个软件进程（从 D:\MentorFlow 主目录启动的，及它启动的语音引擎）；你自己的测试进程从工位目录启动、用复制的数据。
同时在跑：86-MF02 统一加载过场（前端 web/src，可能也改 Classroom.tsx）；你的前端改动尽量只在 useTeacherVoice.ts，冲突由架构师合并。
产物目录：D:\MentorFlow\.local\test-tmp\fix87-memory\。目标两小时内交回。花费上限 ¥0.3（只为进课堂验证时老师开场的真实模型调用）。
背景（用户 2026-10-08 亲测，任务管理器看到软件占内存很大）：架构师查到，软件进程树共约 9 GB 私有内存，其中：
  - 语音引擎 GPT-SoVITS（E:\AI_Tools\GPT-SoVITS-Kurisu\...\runtime\python.exe 跑 refactor/learning/voice/tts/engine_server.py）私有内存约 7.2 GB（工作集只有 61 MB，说明大部分已被换出但一直占着提交量）。用户课堂里“朗读已关闭”，引擎却在跑。原因：useTeacherVoice.ts 第 66 行 autoWarm 默认 true，没有任何调用处关掉；只要语音已安装，进课堂就 speech.warm_up 预热引擎，和朗读开关无关，之后也从不释放。架构师已应用户要求手动结束了这个引擎进程。
  - 主程序 pythonw 私有约 1.6 GB，工作集稳定在 130～150 MB（启动时冲到 1.1 GB 后回落，不像泄漏）；但 83-MF05 的诊断每 5 分钟记一次，线程数一直约 200 个，偏多。
要做
  0. 用户定的规则（2026-10-08 原话的意思）：只要用户没有主动打开，朗读就默认不开启；用户一旦打开，界面上要有一个明显的小按钮随时能关，按钮状态让用户一眼看明白（开着/关着）。默认值改为关闭；用户以前自己打开过的保持打开，从没动过的一律按关闭处理。按钮放在课堂里朗读相关的现有位置（老师栏底部“朗读已关闭”那里），不要另起炉灶。
  1. 语音引擎只在需要时运行：朗读关着时，进课堂、开课、老师说话都不启动引擎；用户打开朗读或点某句的播放时才启动（可以先预热、首句稍慢，用加载提示接住）；关掉朗读后，在当前这句说完或立即停止后释放引擎进程；朗读开着但连续一段时间（例如 10 分钟，你定并写理由）没有朗读也释放，下次需要再启动；软件退出时引擎一定退出，不留孤儿进程（包括主程序被强制结束的情况，能做到就做，例如让引擎自己检测父进程消失后退出）。
  2. 引擎占用：查清 7.2 GB 提交量是什么（模型加载方式、是否加载了用不到的模型或精度、是否 CPU 上整份加载等），在不改 E 盘文件、只改我们这边启动参数和加载方式的前提下，能降就降；降不了写清原因。报告写出启动后、朗读一段后、空闲时的私有内存和工作集。
  3. 主程序线程：列出这约 200 个线程分别是谁开的（Python 线程名与数量、线程池、Qt/WebEngine 原生线程），找出不必要的（例如每个服务各开一个大线程池、空闲时不收缩），能合并或改小就改，写清改前改后数量。
  4. 验证：在用户数据副本上从工位启动软件、进课堂：朗读关着时确认没有引擎进程；打开朗读，老师说一句，引擎启动并正常出声（或至少合成成功）；关掉朗读，引擎在你定的时间内退出；空闲超时后退出；退出软件后没有残留进程。记录每一步整个进程树的私有内存总和，前后对比。
  5. 离线测试覆盖上面的启动与释放规则；全量通过、web 构建通过。
完成标准：朗读默认关闭、开着时有明显的小按钮随时关；朗读关着不占引擎内存；关朗读和空闲都会释放；不留孤儿进程；引擎和线程占用查清并能降则降；全量通过。
验收：报告写到 D:\MentorFlow\.local\test-tmp\fix87-memory\report.md。
回执（尽量短，用表格）：【MF-04｜runtime｜第八十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 默认关闭与开关按钮（截图） ／ 引擎启动与释放规则 ／ 引擎内存（原因、改前改后） ／ 线程（来源、改前改后） ／ 实测进程树内存（朗读关、开、关后、退出后） ／ 全量与构建 ／ 花费 ／ 本地最终占用
~~~

## 更早的任务原文

第五十九至八十六批的任务原文已从本文件删去，需要时用 `git log -p -- docs/project/WORKFLOW.md` 查（删去前最后一版在提交 d33ca8a4）。

## 回执格式

~~~text
【员工｜任务号｜完成/部分完成/受阻】
基线：开工时切到的提交编号。
提交：分支 office/<工位名> 上的提交编号。
改动：修改/新增的文件。
验证：实际运行的命令与结果（通过/失败/跳过）；用了替身的写明。
未完成：没做完或拿不准的地方。
~~~

每条任务还会在末尾列出这次要回报的具体项目。

## 任务索引

只列最近几批；更早的任务原文与索引在 `git show c74ac725:docs/project/WORKFLOW.md`（2026-10-05 精简；再早的在 f2dcc15）。

| 任务 | 员工 | 内容 | 关联需求 | 基线 | 状态 |
| --- | --- | --- | --- | --- | --- |

| 56-MF01 | MF-01 server | 资料结构核对分开“讲法偏好”和“学习内容”：生活化例子等讲法不当成资料缺的章节（¥0.2） | PLAN-06、TEACH-01 | 24dbddd4 | 完成，已合入（a2664eae） |
| 56-MF02 | MF-02 library | 用户给的资料一律当可信：找出并去掉所有按发布者/档位筛资料的旧关卡（¥0.5） | DIR-01 | 24dbddd4 | 部分完成，已合入（1724a2e1）：旧关卡 35 处改 20；真实生成卡在 distractor_check 60 秒超时（架构师已改后台时限） |
| 57-MF02 | MF-02 classroom | 小测试（重跑）：一份小资料、真实模型、模拟时钟，从建计划一直跑到报告与自由检验，只找问题（¥6） | QA-01、DIR-01 | e7c68407 | 完成：第一次跑完全程，P1 3、P2 5、P3 1 |
| 58-MF01 | MF-01 server | 删掉的内容不再被加回；报告不再误称讲稿核对失败 | PLAN-06、RPT-01 | 39a42744 | 完成，已合入 c74ac725（a0e2e777） |
| 58-MF02 | MF-02 library | 错误选项其实成立：检查干扰项时把它出自的原文段落一起给检查器（¥0.3） | DIR-01 | 39a42744 | 部分完成，已合入 c74ac725（fbd0d29c）：真实 4 题拦下 3 题，硬件单选未解决 |
| 58-MF03 | MF-03 classroom | 最终检验隔开重复用题；老师讲解不强制每段标出处、不再贴原文兜底 | DIR-01、CHK-01、TEACH-06 | 39a42744 | 完成，已合入 c74ac725（879af7bd） |
| 59-MF04 | MF-04 调研 | 别人怎么给知识点配题：从资料取题、上网找现成题、核对答案、出题规则（¥0） | DIR-01 | 2a2bbd0c | 完成：9 份指定文件、另 5 个项目 2 篇论文；结论见 .local/test-tmp/research59/report.md |
| 59-MF05 | MF-05 library | 按块测找题：恢复上网找题、关 bank 挖空，从聊需求跑到生成计划（¥10） | DIR-01、QA-01 | 2a2bbd0c | 部分完成，代码已合入 110ef0c2（ca543cf5）；真实测试卡在模拟工具（BUG-51），未到备题 |
| 59-MF05b | MF-05 library | 续跑按块测找题（¥9） | DIR-01、QA-01、BUG-51 | 110ef0c2 | 部分完成：生成 2 模块 163 点，第一个点上网找题截断又被模拟工具止损，未到备题；两轮合计约 ¥5.85 |
| 60-MF01 | MF-01 server | 大资料“按资料调整结构”超上限：目录收拢、去掉抄章节名（¥0.5） | BUG-53、PLAN-04 | e910c914 | 完成，已合入（0ddbbc4d）：目录 906→69 条，真实一次 39.9 秒给出 4 个模块 |
| 61-MF02 | MF-02 client | 结构树与知识地图改成可拖动缩放、可点开详情的好看图；结构步按钮说清下一步、清旧错误提示（¥0） | PLAN-04 | ca0208dd | 完成，已合入（8fb2273a），待用户亲测 |
| 62-MF01 | MF-01 server | 收拢后的目录条目带小标题，“资料没讲到”不再误报（¥0.3） | PLAN-04、BUG-53 | 21c73e2f | 部分完成，已合入（5aca7b76）：带上 726 个小标题后真实一次在 12288 上限被思考用完截断，missing 是否准确未验证；架构师把这一步上限直接设为 32768，待用户在软件里验证 |
| 62-MF02 | MF-02 client | 结构图详情面板不再自己消失、显示能看的内容（¥0） | PLAN-04 | 21c73e2f | 完成，已合入（4e3c03e4） |
| 63-MF01 | MF-01 server | 填知识点前先给时间预估（按字数估点数、每点 8 分钟、按用户上限、超了给三个选择不撤掉）（¥0） | DIR-01、PLAN-05 | 65f70d20 | 完成，已合入（9015c8e0）：比例 300–1700 字/点，用户草稿估 272–1530 点、每天 343–1928 分钟，区间太宽待讨论 |
| 64-MF01 | MF-01 实验 | 用户草稿副本试填一个模块，推算 JavaGuide 8 个模块点数与 15 天负担（¥1） | DIR-01 | 757371b4 | 完成：AI 核心模块 28813 字/62 点（465 字/点），推算 987 点、12437 分钟，15 天每天 830 分钟；¥0.27 |
| 64-MF02 | MF-02 client | 步骤条窄窗溢出（¥0） | PLAN-02 | 757371b4 | 完成，已合入（140ed465） |
| 65-MF01 | MF-01 server | 填点前预估按一般情况显示、三档提示（真完不成才严重警告）、顶部日期跟表单（¥0） | DIR-01 | 60116a27 | 完成，已合入（3f4c705e）：中位 465 字/点，用户草稿每天约 498 分钟、上限 600 无提示 |
| 66-MF01 | MF-01 server | 备题改成学一个备下一个、不出跨模块重复题（¥0） | DIR-01 | 9fac6a46 | 完成，已合入（89c0bc34）：开课备第 1、2 模块，进入第 N 模块备 N+1；入库按规范化题面去重 |
| 66-MF05 | MF-05 实验 | 用户草稿副本上第 1 模块 10 个点试备题：命中率、质量、单价（¥2） | DIR-01 | 9fac6a46 | 部分完成：跑了 2 点半、¥1.60，0 道计分题；每点约 ¥0.61、184 秒（8 次调用、约 40 万输入 token、22 次搜索） |
| 67-MF01 | MF-01 server | 导航、求职、学习路线不进计划；确认前标出资料里没有现成题的模块（¥0.3） | DIR-01、PLAN-04 | 2653eb65 | 完成，已合入（d8c5f631）：非知识内容单列 excluded，JavaGuide 结构 8→5 模块；资料自带题统计没有可复用的离线识别，未做 |
| 67-MF05 | MF-05 library | 找题先按模块整体找、程序先切题块、单点只补 1 轮；Java 核心 10 点新老对比（¥3） | DIR-01 | 2653eb65 | 部分完成，已合入（5a77e5ad）：每点 ¥0.605→¥0.182、184→105 秒、输入 39 万→2.8 万 token；但 7 个点仍 0 道计分题 |
| 68-MF04 | MF-04 调研 | 挖空怎么挖到关键处、要点回想自评、两道题升三级要不要换（¥0） | DIR-01、CHK-01 | — | 完成：三个链接已核实；挖空先选目标句再选核心短答案（Becker 2012）、入库查回拼与泄露（Jiang & Lee 2017）；逐项自评减少高估但仍会误判（Lipko 2009）；没找到“固定两题升三级”的依据 |
| 69-MF05 | MF-05 实验 | JavaGuide 资料自带题覆盖率；20 个点试要点回想与对准目标的填空，逐题人工核对（¥1） | DIR-01 | 06df6250 | 完成：自带题覆盖 39.9%（题少）；要点回想 10/20、填空 7/20 合格；每点 ¥0.025 |
| 70-MF05 | MF-05 library | 拆资料自带题入库、去掉挖空与上网找题、结构步显示每模块有没有题（¥1.5） | DIR-01 | c95b0032 | 部分完成（cf8584ee）：拆题与删路已做；JavaGuide 两模块拆出 1＋10 道问答题、0 道客观题、丢 10 道，人工合格 8/11；全量 7 失败、完整场景停在 QUESTION_UNAVAILABLE（待联调）；¥0.46 |
| 70-MF01 | MF-01 server | 按题考核：模块检测 90% 的题、答错连对两遍、同题隔开、没题只学不考（¥0） | DIR-01、CHK-01 | c95b0032 | 部分完成（8cddaf7e）：按题考核与 recall 桥接已做；全量 12 项旧挖空测试失败（待联调） |
| 70-MF02 | MF-02 client | 问答题先写回答、再看答案、逐条勾要点（¥0） | DIR-01 | c95b0032 | 完成（791a289b）：问答题界面，假桥接浏览器测试通过 |
| 70-MF03 | MF-03 classroom | 随口问改成老师对话直接问，不出题不记分（¥0.3） | DIR-01、TEACH-06 | c95b0032 | 完成（486d660f）：随口问改对话，真实三种回应符合；¥0.02；最终占用 1083.52 MiB（清理被拦，路径在报告） |
| 71-MF01 | MF-01 server | 联调第七十批四份；副本上真实走通备第 1 个有题模块与模块检测（¥2） | DIR-01 | 76bae4b2 | 完成，已合入（3221753c）：四份合并、10 道被丢的题修后全收；全量 2873 通过；副本真实走通备题与模块检测，¥0.02；测试目录约 1.6 GiB 清理被拦 |
| 72-MF01 | MF-01 server | 知识地图这一步直接删模块和知识点，不调模型不重填（¥0.1） | PLAN-04 | ec23c4f6 | 完成，已合入（bab600d3）：副本 8→6 模块、541→507 点，只一次意图调用、无重填；¥0.02 |
| 73-MF01 | MF-01 server | 备题改回一次全备，失败互不影响、显示原因、未备模块插队（¥0） | DIR-01、BUG-54 | 788112d9 | 完成，已合入（1046be6b） |
| 74-MF01 | MF-01 server | 登录改成永久：身份层与 Keycloak 会话不过期、上线脚本、客户端只在明确吊销时重新登录（¥0） | BUG-55、ACC-01 | fb167d82 | 部分完成，已合入（2bae1e5d）：身份层与 realm 永久、上线脚本、客户端不因网络/5xx/睡眠踢人；原生会话四处到期未改 |
| 75-MF01 | MF-01 server | 永久登录第二步：原生会话不过期、存数据库、临时故障返回 503（¥0） | BUG-55 | 77a45e6f | 完成，已合入（9862ce86）：原生会话不过期、表 native_session_families（迁移 0043）、刷新不再走 SPI、临时故障 503；全量 2912 通过；上线脚本 .local/test-tmp/fix75-native-session/deploy75.sh 未执行 |
| 76-MF02 | MF-02 client | 课堂老师改成可收起、可拖动、可改大小的浮动小窗，模型可平移缩放（¥0） | UI-00、TEACH-05 | c9ba7958 | 完成，已合入（a4bb493b）：老师默认收起为圆形头像，展开为半透明浮窗，可拖、可改大小、模型可平移缩放，状态记住，收起暂停渲染；全量 2914 通过；测试目录约 565 MiB 清理被拦 |
| 77-MF03 | MF-03 classroom | 用户计划副本上真实走课堂（开课到第 3 模块模块检测），修掉途中所有拦路问题，含开场选项格式报错（¥2） | TEACH-06、BUG-56 | 130dea0d | 部分完成，已合入（a5fa4a2f）：开场缺选项照常讲并给固定两项；插话上下文过长与原文反复读取导致的等待已修；副本真实走到第 3 模块检测、勾要点、只重测错题、关掉重进都正常；约 ¥1.69；测试目录约 1.1 GB 清理被拦 |
| 77-MF02 | MF-02 client | 老师改成右侧整页高的抽屉（收起细竖条），用上模型自带的动作与表情（¥0） | UI-00、TEACH-05 | 130dea0d | 部分完成，已合入（b602c828）：右侧整页高抽屉、收起 32px 细边；物理、鼠标跟随、表情已用上；模型没有真正的待机动作（idle 文件实为 Cry），待机循环未做；全量 2923 通过 |
| 78-MF02 | MF-02 client | 老师栏往外展开：窗口向右变宽、对话区不变（最大化时才向内）；老师栏背景按主题做层次（¥0） | UI-00、TEACH-05 | 9cf0752c | 完成，已合入（b2973385）：普通窗口 1280→1568、对话区保持 928；右边不够左移；最大化/全屏才向内；樱枝花瓣与深紫极光背景加地面光晕；全量 2936 通过 |
| 79-MF02 | MF-02 client | 课堂原文卡片默认折叠、同资料合并、出处标记跳转；收起细边箭头朝右（¥0） | TEACH-06、UI-00 | d9f920b0 | 完成，已合入（b602a6c3）：原文默认一行折叠、同资料合并、点出处标记跳转展开、箭头朝右；全量 2937 通过 |
| 80-MF02 | MF-02 client | “查看路线”白屏：计划详情改按题显示知识地图与掌握情况、加载提示与超时、全局错误边界（¥0） | MAP-01、UI-00 | 06c356af | 未发出，作废，并入 81-MF02 |
| 81-MF02 | MF-02 client | 计划详情白屏与慢、按题知识地图、错误边界、老师预设选择界面（¥0） | MAP-01、UI-00 | 5801844c | 已合入（a617aca0，部分完成：白屏根因未复现，已加错误边界） |
| 81-MF01 | MF-01 server | 老师预设：最多 4 个可手写人设、计划默认用预设一、随时可换、全链路打通（¥0.2） | TEACH-01 | 5801844c | 已合入（e9028eeb，完成） |
| 81-MF03 | MF-03 classroom | 课堂一个输入框（去提问开关）、知识点收起改进度条、进课堂提速、中文加粗（¥0.5） | TEACH-06、UI-00 | 5801844c | 已合入（93cc94a1，完成） |
| 81-MF05 | MF-05 platform | 模型用量按余额变化显示今天/本周/本月花费（¥0） | SYS-03 | 5801844c | 已合入（c5cf1492，完成） |
| 81-MF04 | MF-04 runtime | 语音模型国内镜像优先、失败回落 GitHub（¥0） | VOICE-01 | 5801844c | 已合入（c90918c2，部分完成：本机测得镜像不提速） |
| 81-MF06 | MF-06 tools | 结构步只排除和学习无关的内容（¥0.3） | DIR-01、PLAN-04 | 5801844c | 已合入（2e9c1350，部分完成：真实确认因网络连接失败没跑成） |
| 81-MF07 | MF-07 library | 上传题目自测新功能（¥0.5） | DIR-01 | 5801844c | 已合入（9654fe0a，完成） |
| 82-MF06 | MF-06 tools | 知识点连线：DeepSeek 配对前置/相关、程序校验、新老计划自动补（¥1.5） | MAP-01、DIR-01 | 52138de0 | 已合入（43c1ebe9，完成；真实 541 点 647 前置＋129 相关，抽检 16/20 合理，4 条前置偏强） |
| 82-MF02 | MF-02 client | 知识地图图形化：路线总览、拉近看知识点网、出处排版（¥0） | MAP-01、UI-00 | 52138de0 | 已合入（329f2cbc，部分完成：缺模块前置字段，架构师合并时补一行；逐点掌握按 DIR-01 本就不存在） |
| 83-MF05 | MF-05 platform | 软件静置时无声退出：查清原因、修掉、以后退出都留原因（¥0.5） | BUG-58 | fdc1d9ea | 已合入（6a24250f，主线 67dd8d74；原事故 60 分钟未复现，修了两处会无声退出的缺陷，加永久退出诊断与源码启动脚本） |
| 84-MF03 | MF-03 classroom | 学习进度状态：逾期待还/已还、今天进度、超前，一次一种、按严重程度配色（¥0） | PLAN-05、UI-00 | 95fa4107 | 已合入（04264b1b，完成；用户副本上应显示逾期待还 331 分、今天已还 24 分） |
| 84-MF06 | MF-06 tools | 知识点分钟数按内容分量重算：程序按原文长度和题量算、AI 只标难度，老计划自动重算（¥1） | PLAN-04、PLAN-05 | 95fa4107 | 已合入（f3fed92d，完成；守住原总量 4328，不受约束估算 3118，用户选按内容算→85-MF06） |
| 85-MF06 | MF-06 tools | 计划总时长改按内容算、老计划自动重排日程（¥0.5） | PLAN-04、PLAN-05 | bf18799b | 已合入（91b636fc，主线 197bc861；副本总量 4967→3750，10-07 未学的 331 分摊进后面每天，顶部变“今天 24 / 289”） |
| 86-MF02 | MF-02 client | 统一加载过场：盘点所有等待处、三档（不显示/骨架/过场＋文字）、老师正在想（¥0.3） | UI-00 | 197bc861 | 已合入（ebb1d0d0，主线 56251799；39 类等待 38 类统一，原生登录与分钟重算初始状态未覆盖→88） |
| 86-MF03 | MF-03 classroom | 模型连接一断自动重试：未出字前最多重试 2 次，用户无感（¥0.3） | UI-00、BUG-58 | 197bc861 | 已合入（007ea156，主线 ec2ffe7e；未发出的连接失败最多重试 2 次，间隔 1、3 秒） |
| 87-MF04 | MF-04 runtime | 内存：朗读关着不启动语音引擎、关朗读和空闲释放；查清主程序线程（¥0.3） | VOICE-01、BUG-58 | ec2ffe7e | 已合入（5ac8a789，主线 98e52ca1；朗读默认关、原位显示“朗读：开/关”，关即释放、空闲约 10 分钟释放；进程树朗读关 1.7 GB、开 8.2 GB；引擎 7.58→6.56 GB；主程序线程约 200 个是 NVIDIA 驱动，OpenBLAS 限线程可减 20 余个，未接入启动入口） |
| 88-MF05 | MF-05 platform | 原生登录与启动窗口加载过场，与网页一致（¥0） | UI-00 | 56251799 | 已合入（77d73527，主线 992376b5；12 类原生等待接入过场与首帧遮罩；初始化与建窗会短暂阻塞主线程、动画停顿，实测均在 1 秒内，暂不再派） |
| 88-MF06 | MF-06 tools | 后台任务初始状态：detail 补分钟重算等状态字段，打开页面即显示过场（¥0） | UI-00、MAP-01 | 56251799 | 已合入（5b6621d7，主线 ce6eb1c7；detail 新增 point_minutes_status、preparation_status 与备题模块计数，541 点 detail 中位 51→38 ms） |

## 任务模板

新任务照上面最近一批任务原文的格式写（第一节现成项目与许可证、开工同步、禁止事项与磁盘规则、证据来源、同时在跑、花费、背景、要做、完成标准、验收、回执项目）。

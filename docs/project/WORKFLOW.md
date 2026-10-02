# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读它开头的「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务；两位架构师（都是 Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。架构师之间不直接对话，交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：先读 AGENTS.md、CURRENT_STATE「当前阶段」和索引、本文件；看 `git log` 里上次基线之后的提交；确认 7 个工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。与用户讨论到一半的事，看「当前阶段」的“等用户决定的事”和各条目里标了“讨论中”的待修。
- **交班**（每次停手前都做，不等用户说换人）：用户定下的事当场写进对应编号条目并标日期；自己查到但还没讨论完的问题写进对应条目的“待修”，标明“讨论中”；更新「当前阶段」、任务索引、待派队列；本地提交。不把事情只留在对话或个人记忆里。
- **现在在做的事**（2026-10-02）：用户定先发版再测试，派第三十六批：MF-01 打包 2.0.1（未签名）→ 架构师签名、生成安装程序、上传 → 宣布上线后转发 MF-02（在用户软件里点更新，再在 2.0.1 上跑第四次小测试）。

## 协作方式

- **流程**：架构师（Claude）写任务 → 用户转发给员工（GPT/Codex，可能是较弱的模型）→ 员工在自己工位完成 → 用户把回执贴回 → 架构师审查、合入主目录、本地提交。员工不改主目录、不推送、不 ssh、不部署、不碰正式签名私钥。推送 GitHub 要等用户说。
- **分工**：前端、老师/判定/检验等核心编排由架构师自己做；员工做边界清楚、耗时的单模块任务。架构师自己动手的标准：GPT 员工做不好、而架构师做起来便宜的事（例如 BUG-34 这种小修）。员工的结论要用真实数据核对后才采信。
- **先讨论后派活**（2026-10-02 用户再次强调）：每次收到回执，回复里只讲审查结果与发现的问题、给建议和要用户定的事，不附下一批任务文本；用户讨论完、同意了，再一次写出整批任务文本放在回复最后，然后停下等用户转发。不要“拿到回执就直接根据回执把下一批发出来”——那样效率低、大部分时间浪费。用户说“先说问题”时同理只讲问题。
- **一批派完、不挤牙膏**：每轮把当前适合的任务一次派出；同时派几个员工不设上限，以开发效率为准（2026-10-01 用户定，取代原先最多 3 个），但改同一文件同一部分的任务要排先后，避免合并冲突；不派无意义的活；用户没要求的事（例如更新价格表）先问再做。
- **架构师省着用**：测试、跑数据、复查记录这类耗量大的活尽量派给员工，架构师只做判断、审查和合入（2026-10-01 用户提出：架构师自己做消耗太大）。
- **状态如实**：写好的任务在索引里标“已起草，待用户转发”；用户说“发了”才改“进行中”；回执回来后改“已合入/已完成（部分）”。
- **员工自主**：每条任务写清决策原则，员工遇到选择自己决定、在回执写明理由，不停下来问；只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停。
- **防止无穷无尽地改**（2026-10-01 用户担心“一直跑没有进步、改个无穷无尽”）：每个问题最多两轮——第一轮按现方法修，没解决就第二轮换方法（先查开源项目和论文）；第二轮还不行，带数据找用户定（接受现状、调整目标或换方案），不再自行反复调。每条任务只做一轮“实现 → 测 → 报告”，写明完成标准；没达标不在任务里继续微调，回执写清没过的样本、原因判断和建议换的方法。不追求把测试做大，看的是发现的问题有没有解决。
- **测试只认装好的最新版**（2026-10-02 用户定）：每合入一批就发新版本；验收测试在用户软件“点更新”拿到的那个版本上跑（点更新本身也是测试），不拿源代码脚本的结果当验收。员工负责打包（不签名）和测试；签名、上传由架构师做（私钥与服务器不给员工）。没合入的分支不拿来做验收测试。
- **方案由架构师出**（2026-10-02 用户定）：发现的问题由架构师查证据、找开源项目和论文、想清楚做法，在任务里写明“怎么做”，员工照着做；不写“你去解决”让员工自己想方案（员工对项目的理解没有架构师深，容易偏，效率低）。测试剧本也要想仔细，不能把测试里的人为限制当成用户需求（第 30–34 批的“每模块 3 点”就是例子）。
- **先跑真实流程，再看记录定修什么**（2026-10-02，回顾第二十五到三十批后定）：找问题靠完整的真实流程（小测试脚本）和对真实记录的逐条错误分析，不靠给单个组件自定目标；修复任务只针对真实记录里找到的失败，完成标准写成“这个失败不再出现”的二元判断，用同一个脚本或保存的记录复测。一个任务只做一件事：要么实现修复，要么做评测，不把两者和小预算捆在一起。固定评测集长期保留并在每次相关改动后回放：判定考卷（.local/test-tmp/judge-holdout，MF-06 保管）、出题回归集（tests/refactor 下 28-MF04 的回放）、讲稿埋错集（.local/test-tmp/prep-cost）、完整流程脚本（tests/refactor/acceptance/test_math_complete_flow_real.py）。
- **花钱的测试**：只为具体改动花钱，事先写下预期效果；能离线的先离线（假模型、重放保存的请求）；预算分两段，第一段没达到预期就停；派之前告诉用户每个任务的把握和预算。真实模型用管理员现有的 DeepSeek Flash 预设（Key 只在 Windows 凭据里，从不显示）。
- **功能先行**：功能没做完前任务里一律禁止加安全加固（AGENTS.md）；审查时删掉员工多加的加固。
- **问题留档**：每修好一个问题，同一次提交里在 CURRENT_STATE 记 BUG 条目（AGENTS.md 规则 7）。
- **部署**：每次改服务器都先向用户说明改什么、拿到同意再做。
- **向用户汇报**：合入后开头先给一张“环节 × 状态”表（通过/未通过/没测到），再说问题和建议；数字要和报告一致。
- **署名**：主目录提交一律用 `git -c user.name="tonyswift868" -c user.email="205869700+tonyswift868@users.noreply.github.com" commit ...`，结尾加 Co-Authored-By 行；不要写进共享的 git 配置（工位 worktree 共用它）。员工提交用各自工位名（见任务模板）。

## 架构师操作手册

- **全量测试**（约 4 分钟）：`TMP=D:/MentorFlow/.local/tmp TEMP=D:/MentorFlow/.local/tmp .venv/Scripts/python.exe -m pytest tests/refactor -q -p no:cacheprovider --basetemp=D:/MentorFlow/.local/tmp/pt`。2026-09-30 为 2436 通过、26 跳过。只单独跑 app 与 timeline 两组测试时偶发 pytest 段错误，属测试进程问题，忽略。
- **界面构建**：先把 `/d/MentorFlow/.local/node` 加进 PATH，再 `node .local/web-deps/node_modules/vite/bin/vite.js build --config refactor/learning/web/vite.config.mjs`；dist 随仓库提交（员工不提交 dist，架构师合入后统一构建）。
- **合入员工分支**：`git diff --binary <基线> <员工提交> | git apply --index --3way`，逐文件看改动是否在任务范围内，跑全量测试和界面构建，再用上面的署名提交。多人同时改 app/classroom.py 时手工合并冲突。
- **基线**：合入并提交后，主目录 `git rev-parse --short HEAD` 就是下一批任务的基线，写进每条任务的“开工同步”。
- **写脚本**：含 Windows 路径或反斜杠的内容不要用 bash heredoc（会被吃掉，BUG-10），写成文件再运行，或用编辑工具。
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，用户 mentorflow-admin，用 sudo -n）。线上后端是瘦身镜像 mentorflow-slim-backend:MF-14，按 refactor/server/DEPLOY.md 用 compose.slim.yml 启动；回滚 `sudo bash /opt/mentorflow/state/mf14/switch.sh rollback`（切回 MF-U11），切换前的数据库备份 /opt/mentorflow/state/mf14/mentorflow-before-mf14.dump。服务器上只有管理员一个账号。
- **资料白名单目录**：服务器 /opt/mentorflow/state/mentorflow-e1-preview/catalog/（source-whitelist.json 129 条、model-prices.json；旧版备份 *.bak-20260929）；本机待发布副本在 .local/catalog-release/。发布前用客户端同一套格式检查，下发的文件不能带 "candidates" 键。
- **发布新版**（SYS-06）：用 scripts/build_learning_installer.py 的 freeze → release（签名清单，--valid-days）→ sign（私钥 .local/release-keys/mentorflow-app-2026.private.key，说明见同目录 README.txt）→ installer。上传到服务器 /opt/mentorflow/update-static/e108/app/ 的 .staging，逐个按 SHA256 校验后放入，最后原子替换 stable/stable.json；公网地址 https://89.45.45.28/mentorflow/updates/app/。给已装的 2.0.0 推的第一版必须 --valid-days 30；线上 stable.json 2026-10-28 过期。发布要用户同意。
- **磁盘**：测试与验收产物都在 .local/test-tmp/ 下（2026-09-30 共约 3.7 GB，release 1.3 GB、eval-plan 0.7 GB、installer 0.5 GB 最大）；acceptance/、acceptance2/ 是两次小测试的证据，保留到修复验收完。其余可在用户同意后清理。

## 当前基线

| 项目 | 值 |
| --- | --- |
| 基线提交 | main 最新提交（本文件与 CURRENT_STATE 于 2026-09-30 精简后的那次提交）；派下一批前用 `git rev-parse --short HEAD` 取 |
| 未推送 | main 比 origin/main 多 100 多个提交；用户说了才推送 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

| 员工 | 工位 | 状态 |
| --- | --- | --- |
| MF-01（library） | D:\MentorFlow-office\library | 空闲 |
| MF-02（runtime） | D:\MentorFlow-office\runtime | 空闲 |
| MF-03（client） | D:\MentorFlow-office\client | 空闲 |
| MF-04（platform） | D:\MentorFlow-office\platform | 空闲 |
| MF-05（tools） | D:\MentorFlow-office\tools | 空闲 |
| MF-06（classroom） | D:\MentorFlow-office\classroom | 空闲 |
| MF-07（server） | D:\MentorFlow-office\server | 空闲 |

工位名只是称呼，不限制能接的任务类型。每个工位下的 repo 是主仓库的 git worktree，员工在分支 `office/<工位名>` 上提交。熟悉的领域：MF-01 老师与备课、MF-02 缓存/网关/报告、MF-03 题库/检测/账本、MF-04 资料检索与权威性、MF-05 语音与提醒、MF-06 验收与发布构建、MF-07 服务器与同步。

## 待派队列

- 36-MF02：等架构师宣布 2.0.1 上线后转发。

## 第三十六批任务原文（2026-10-02，基线 db874ac）

用户 2026-10-02 定：先发版、再测试，测试只在用户软件“点更新”拿到的版本上跑。MF-01 先打包（不签名）；架构师签名、生成安装程序、上传（私钥与服务器不给员工）；架构师宣布 2.0.1 上线后再转发 MF-02。

### [36-MF01] runtime｜打包 2.0.1（未签名），供架构师签名发布

~~~text
【MentorFlow 第三十六批｜MF-01（工位 runtime）｜打包 2.0.1（未签名），供架构师签名发布】

开工同步（第一步）
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard db874ac
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥（你只做到“未签名的发布清单”，签名、生成安装程序、上传由架构师做）。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
用户电脑上已装的 MentorFlow（%LOCALAPPDATA%\Programs\MentorFlow）和用户数据只读：不启动、不改、不删。
遇到需要选择的细节：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有要动服务器或正式密钥、要删用户数据才停下来。回执附“问题记录”（现象、原因、排查、解决、验证）。
背景（用户 2026-10-02 定）：以后先发版、再测试，测试只在用户软件“点更新”拿到的版本上跑。2.0.0（2026-09-29）之后没发过新版，用户的软件点更新拿不到任何改动；已发布的 2.0.0 还缺 course/data（BUG-40）。这次把当前主线打成 2.0.1。
先读：CURRENT_STATE 的 [SYS-06]、[BUG-40]；WORKFLOW「架构师操作手册」里的“发布新版”；scripts/build_learning_installer.py；refactor/desktop/delivery/update_protocol.py（增量更新：changed、incremental_assets）；tests/refactor/delivery、tests/refactor/installer。
要做
  1. 线上参数对齐：只读 GET 公网 https://89.45.45.28/mentorflow/updates/app/stable/stable.json（不 ssh），记下 app_id、feed、epoch、minimum_version、版本号、文件清单；再只读查看已装 2.0.0 目录里有哪些随包文件（运行时、账号 CA、7zr.exe、声音清单等）。据此定 freeze 与 release 的参数，保证已装的 2.0.0 把 2.0.1 认成同一产品的新版本、走增量（只下载变化的文件）。参数取值和依据写进回执。
  2. freeze：--version 2.0.1，用 db874ac 的代码。web dist 若与 web 源码不一致，在隔离目录用 vite 构建，用 --web-dist 传入（不改主目录）。
  3. 检查包内容：payload 里必须有 refactor/learning/course/data（source_lists.json、model-prices.json 等）。在 D:\MentorFlow\.local\test-tmp\release-2.0.1\ 下用打包出的程序做一次最小启动检查（启动到后端就绪、CatalogClient 能读到这两个文件），用临时数据目录，不安装。
  4. release：--valid-days 30（已装 2.0.0 只接受 31 天以内，SYS-06）；--base-versions 按代码实际需要写成能让 2.0.0 走增量；notes 写“学习流程一个月来的改进与 BUG-40 修复”。产出未签名清单。估算 2.0.0 升级时要下载的变化文件数与字节数。
  5. 不签名、不生成安装程序、不上传。回执写清：payload 目录、未签名清单路径、架构师要执行的 sign 与 installer 完整命令（私钥参数写 <PRIVATE_KEY> 占位；installer 的 compiler 路径写本机实际路径）。
  6. 若打包脚本或更新代码必须改才能打出正确的包（例如漏文件），可以改，加测试，提交，写清改了什么。
完成标准：未签名清单与 payload 齐全；payload 含 course/data；最小启动检查通过；增量下载有估算；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；报告写到 D:\MentorFlow\.local\test-tmp\release-2.0.1\report.md。
D 盘：freeze 中间目录用完即删，只留 payload 与未签名清单（架构师发布后删）。
回执：【MF-01｜runtime｜第三十六批｜完成/部分完成/受阻】基线 ／ 提交（没改代码就写“无”）／ 线上参数与取值依据 ／ 包内容检查 ／ 最小启动检查 ／ 增量下载估算 ／ 架构师要执行的命令 ／ 本地最终占用 ／ 问题记录
~~~

### [36-MF02] client｜在用户的软件里点更新到 2.0.1，再在这个版本上跑第四次小测试“压强与浮力”（架构师宣布 2.0.1 上线后再转发）

~~~text
【MentorFlow 第三十六批｜MF-02（工位 client）｜在用户的软件里点更新到 2.0.1，再在这个版本上跑第四次小测试“压强与浮力”】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard db874ac
脚本用 D:\MentorFlow\.venv\Scripts\python.exe。本任务只测、只报告，不改产品代码、不提交产品代码。
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\acceptance4\ 下；中间产物用完即删，回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
被测对象是用户电脑上已装的 MentorFlow（%LOCALAPPDATA%\Programs\MentorFlow），不是源代码。不重装、不另建隔离安装（用户定：不做全新环境重装测试）。不删、不改用户已有的计划和数据；本次新建的测试计划保留，回执写它的名字。
遇到需要选择的细节：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有这几种情况停下来：超出花费上限；软件要求登录或输入密码（停下请用户登录，你不输入任何密码）；要删用户数据；有一节课正在进行、关掉软件会打断它。回执附“问题记录”（现象、原因、排查、解决、验证）。
背景（用户 2026-10-02 定）：测试只在用户软件“点更新”拿到的版本上跑；点更新本身就是要测的一步。小测试标准见 [QA-01]：量小但流程全，DeepSeek 扮学生从零开始聊天，一次跑完 P1、P2 都为 0 才算“完全没有问题”。
先读：CURRENT_STATE「当前阶段」（已知问题、阻塞点）、[QA-01]、[SYS-06]；第三次小测试的脚本与报告 D:\MentorFlow\.local\test-tmp\acceptance3c\（看当时怎么驱动、怎么核对，能复用就复用）。
要做
  1. 点更新：关掉正在运行的 MentorFlow。启动已装软件时设环境变量 QTWEBENGINE_REMOTE_DEBUGGING=<本机空闲端口>，用 CDP（如 Playwright connect_over_cdp）操作界面。走用户的真实路径：设置 → 关于 → 检查更新 → 点更新 → 软件自动重启。记录：发现更新用时、下载大小、点更新到重启完成的用时、有无报错。重启后在“关于”里核对版本是 2.0.1；对不上就停在这里报告。更新失败时：留证（软件日志、界面截图、安装目录与更新目录状态），不手动替换文件、不重装，直接报告——这本身就是要找的问题。
  2. 第四次小测试，全程在更新后的软件界面里做（自动重启的进程若没带调试端口，就关掉按同样方式再启动，并写进回执）：
     - 学生：DeepSeek Flash（管理员现有预设，Key 只在 Windows 凭据里，不显示不保存）扮演初二学生，在“新建计划”里从零聊天，目标“初二物理：压强与浮力”。学生设定写进报告：基础一般、会答错一部分、偶尔追问、偶尔说“这个我会了”。学生的话由模型按设定和界面上的内容实时生成，不预写台词；脚本把学生的话输进界面、读回界面上的回复。
     - 量小但流程全：建计划（含资料覆盖检查的聊天）→ 至少一个模块的引导式课堂（含随口问、答错后的第二道）→ 模块检测（含答错重测）→ 一个模块走“直接考”→ 中途关掉软件再打开、从中断处继续 → 最终检验 → 报告（看页面并导出 PDF）→ 自由检验一次。点数尽量少，但每一类都要走到。
     - 你本人逐条核对：老师讲解、每道上屏的题、每次判定，按 QA-01 标准分级。P1：讲错、判错（冤枉或放水）、答案错/有歧义/泄题的题、流程卡死或丢数据、更新失败；P2：明显影响学习但不算错误的问题；P3：体验问题。每条附证据（界面截图或调用记录位置）。
     - 顺带记：每次调用的用途、花费、用时；纠错题有没有因生成失败导致题不够（阻塞点②）；简答判定有没有触发追问、追问对不对（阻塞点③）。
  3. 只测、只报告。发现的问题写进问题清单，由架构师和用户讨论后决定修什么。
花费：上限 ¥5（学生模型加软件自身调用合计），只记录，要报告。
完成标准：更新这一步有明确结论（成功或失败，附证据）；每一类流程都走到，或写清卡在哪；P1/P2/P3 清单附证据；一句话结论：这次是否“P1=0 且 P2=0”。
报告：D:\MentorFlow\.local\test-tmp\acceptance4\report.md
回执：【MF-02｜client｜第三十六批｜完成/部分完成/受阻】更新结果（用时、下载大小、版本核对）／ 小测试走到了哪些流程 ／ P1、P2、P3 清单（含证据位置）／ 准确度数字（讲错处数；判定次数与错判数；上屏题数与不合格数）／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

## 第三十五批任务原文（2026-10-02，基线 6165200）

用户 2026-10-02 确认的方案，四个任务按文件分工并行；做法都由架构师写定，员工照做。合入后在最新主线上派第三十六批（第四次小测试“压强与浮力”、没见过的考卷公平复测判定）。

### [35-MF04] platform｜建计划改造：资料覆盖在聊天里说清、用户放弃的部分不出现、去掉每点时长与凑点数限制

~~~text
【MentorFlow 第三十五批｜MF-04（工位 platform）｜建计划改造：资料覆盖在聊天里说清、用户放弃的部分不出现、去掉每点时长与凑点数限制】

开工同步（第一步）
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1.5。花费只记录，但要报告。
做法已由架构师定好（用户 2026-10-02 确认），照“要做”逐条实现，不自行换方案；实现中发现方案本身走不通，写清证据和原因交回，不自己另想一套。只做一轮：实现 → 测 → 报告。
遇到需要选择的细节：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，冲突由架构师合并）：
  35-MF04（platform）：建计划——course/planner_agents.py、app/plan_flow.py、course/plan_intent.py、web/src/pages/NewPlan.tsx、connections/gateway.py 里用途档位的几行。
  35-MF01（library）：纠错题自洽——course/question_agent.py 的纠错题部分、course/question_review.py、新增的代码运行模块。
  35-MF03（client）：判定追问——course/judge_agent.py、course/data/ 下的判定示例、app/classroom.py 的 _judge 与各作答处理里的追问。
  35-MF02（runtime）：学习档案与两项偶发测试——course/profile_agent.py、app/classroom.py 的 _profile_later、两个测试文件。
背景（用户 2026-10-02 定，CURRENT_STATE「当前阶段」与 [PLAN-01]、[PLAN-04] 的“资料覆盖”“约 15 分钟只是平均数”）：
  - “一个点约 15 分钟”只是整份计划的平均数，单个点 2 分钟、50 分钟都可以；不设上下限、不自动拆点。现在 planner_agents.py 第 611 行把每点卡在 10–20 分钟，超了整个模块生成失败（第三十四批真实生成就是这样失败的），要去掉。
  - 用户不能规定每模块几个点这类结构；第三十批“按补充说明凑点数”（coverage_limits 等）要去掉。补充说明里“只学第 5 章”“不学 ××”这类范围要求照常交给模型理解。
  - 资料覆盖不了学习目标就不能生成计划：用户觉得资料够了、要往下走时，检查覆盖；缺了就在聊天里说清缺什么、大约多少个点，反问用户（继续找资料 / 上传或贴网址 / 这部分不学），具体情况具体说，不出固定选项。用户说不学，就只按现有资料能覆盖的部分生成，缺的部分在轮廓、知识点、讲课、检验里都不出现，也不特意标出来。勾了“只用我的资料”时，同样要说清缺了哪些、大约多少个点，用户接受就按上一条，不接受就由 AI 帮着补资料。
  - 第三十四批加的“按意思查重复”（plan_point_review）去掉，只留不花钱的字面比对；第三十二批的轮廓“三选一”和“按资料范围生成”这句固定口令也去掉。
先读：AGENTS.md；CURRENT_STATE 的 [PLAN-01]、[PLAN-02]、[PLAN-04]、[PLAN-06]、[PLAN-07]、[ARCH-06]、[QA-01]；app/plan_flow.py（outline、generate、_meaning_duplicates、coverage 相关）、course/planner_agents.py（outline 提示与 coverage、parse_points、cross_module_duplicates）、course/plan_intent.py。
要做（照做）
  1. 每点时长：parse_points 只要求分钟数是正整数，去掉 10–20 的限制；提示里“通常 10–20 分钟”改成“按这个点实际需要估计，可短可长；整份计划平均约 15 分钟只作参考”。建计划时的总时长预估仍按平均 15 分钟推点数。
  2. 去掉 coverage_limits、按补充说明凑点数/模块数的检查与提示、轮廓里的 coverage 数量字段与“按资料范围生成”口令、_meaning_duplicates 与 plan_point_review 用途。字面重复检查保留：发现重复点就静默去掉，模块去空了就去掉这个模块，不报错、不打扰用户。
  3. 新的覆盖检查（新用途 coverage_check，思考“高”）：在用户确认资料、要求出轮廓时调用（资料有增删后、再要求出轮廓时重新查）。输入：规范化后的学习目标、最终目标、水平、补充说明、已采用资料的标题/目录/各块首句（不放全文）。输出严格 JSON：covered（是否覆盖）、missing（每项：缺的内容、为什么需要、大约几个知识点）、message（给用户的一段话，具体说缺什么、问要不要继续找）。covered 为 false 时：把 message 作为 AI 消息发进聊天；不出轮廓，界面上“出轮廓 / 正式生成”的按钮不出现，直到覆盖解决。
  4. 用户的回答交给意图理解：找资料、上传、贴网址走现有流程，补完再查覆盖；新增一种意图“放弃这部分”（参数：放弃的内容列表）——记进草稿的学习范围，规范化目标改写为只含覆盖部分（在聊天里给用户看一句改后的目标），之后轮廓与正式生成的请求里明确“只学这些、不出现被放弃的内容”。程序检查：生成结果的模块、知识点标题和摘要里不能出现被放弃的内容关键词，出现就让该模块按要求重生成一次，仍出现就去掉那些点。
  5. “只用我的资料”：覆盖检查同样执行，message 开头说明“你选了只用自己的资料”，并给出缺的内容与大约总点数；用户同意找资料时才去搜索。
  6. 测试：假模型覆盖：覆盖完整直接往下走；缺内容时聊天说明且按钮不出现；用户补资料后重新检查通过；用户说“乘除先不学”后目标改写、轮廓与知识点里没有乘除；“只用我的资料”的说明；一个 45 分钟的点能正常入库；字面重复静默去掉。真实（≤ ¥1.5）：用第三次小测试的 OpenStax“加减法解方程”页面、目标写“初中一元一次方程（含乘除）”，走一遍：应先在聊天里说缺乘除；回答“乘除先不学”后生成的计划里完全没有乘除。你本人逐条核对聊天内容与生成结果。
完成标准：上面每个场景按描述发生；缺内容时不给生成；放弃的部分在计划里完全不出现；没有每点时长与点数限制；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix35-plan-coverage\report.md。
回执：【MF-04｜platform｜第三十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 去掉了什么 ／ 覆盖检查与“放弃这部分”的实现 ／ 各场景结果（假模型、真实）／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [35-MF01] library｜纠错题必须自洽：先写错因、程序逐行验算、解题员重算错误做法

~~~text
【MentorFlow 第三十五批｜MF-01（工位 library）｜纠错题必须自洽：先写错因、程序逐行验算、解题员重算错误做法】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1。花费只记录，但要报告。
做法已由架构师定好（用户 2026-10-02 确认），照“要做”逐条实现，不自行换方案；实现中发现方案本身走不通，写清证据和原因交回，不自己另想一套。只做一轮：实现 → 测 → 报告。
遇到需要选择的细节：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，冲突由架构师合并）：
  35-MF04（platform）：建计划——course/planner_agents.py、app/plan_flow.py、course/plan_intent.py、web/src/pages/NewPlan.tsx、connections/gateway.py 里用途档位的几行。
  35-MF01（library）：纠错题自洽——course/question_agent.py 的纠错题部分、course/question_review.py、新增的代码运行模块。
  35-MF03（client）：判定追问——course/judge_agent.py、course/data/ 下的判定示例、app/classroom.py 的 _judge 与各作答处理里的追问。
  35-MF02（runtime）：学习档案与两项偶发测试——course/profile_agent.py、app/classroom.py 的 _profile_later、两个测试文件。
背景（CURRENT_STATE [CHK-01] 的“方案”）：题库里出过一道自相矛盾的纠错题——“小华解方程 m+4=−5 时，两边同时加 4，得到 m=−1”。两边加 4 得到的是 m+8=−1，得不到 −1；真正会得到 −1 的常见错误是“移项不变号”（m=−5+4）。出题员、不看答案的解题员、核对员都顺着题面走，没人检查“题面写的错误做法能不能真得出题面给的错误结果”。证据：D:\MentorFlow\.local\test-tmp\fix34-quick-check\calls.json 第 0 次调用（出题）与第 5–7 次（解题、判定、核对）。做法参照 DiVERT（https://arxiv.org/abs/2406.19356，先用文字写明错因、再由错因推出错误答案，保证两者一致）与数学选择题干扰项一致性研究（https://arxiv.org/html/2505.01903v1）。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]（全部，含“方案”）、[TEACH-04]“题型与判法”、[QA-01]；course/question_agent.py（纠错题部分）、course/question_review.py（结构检查、解题、核对）、course/answer_compare.py（_expression 能把一元方程化成标准系数，可判断两个方程是否等价）；你自己 keep/library-28 分支里的“隔离子进程真跑代码”实现（第二十八批写的，没合入）。
要做（照做）
  1. 纠错题结构加三个必填字段：error_cause（一句话写明错因，如“移项时没有变号”）、wrong_work（学生的错误做法，过程类按步骤列成数组，每步一行；概念类写成一段话）、wrong_result（错误结果）。出题提示要求：先写错因，再严格按这个错因一步步写出错误做法与错误结果；题面展示的错误做法与 wrong_work 一致。
  2. 程序验算（数学）：wrong_work 每一行若都能被 answer_compare._expression 解析成方程，则：第一行与题目原方程等价；相邻两行之间恰好有一处不等价，且从这一步起推出的最后一行就是 wrong_result；标准答案满足原方程。不满足就退回修改（计入现有的修改与重出上限）。解析不了就跳过这一步，交给第 4 条。
  3. 程序验算（代码）：把 keep/library-28 里的隔离子进程运行器抽成独立模块（限时、禁止联网与读写文件），纠错题里的错误代码真跑，输出必须等于 wrong_result；改正后的代码真跑，输出必须符合标准答案。
  4. 不看答案的解题员多一步：先按题面的错误做法自己重算一遍，回答“能不能得到题面给的错误结果、错在哪一步、错因是什么”；算不出题面结果，或指出的错因与 error_cause 不同，就判题目自相矛盾，交核对员修改，改不好就丢。
  5. 回归集：把 m+4 那道题（calls.json 第 0 次调用里的原题）加进出题回归集，新流程必须拦下；原有 6 道坏题、8 道好题的结果不变。
  6. 测试：离线覆盖第 2、3、4 条；真实（≤ ¥1）各出 2 道数学、2 道 Python 纠错题过完整流程，你本人逐题核对自洽与正确。
完成标准：m+4 题被拦下；回归集结果不变；真实出的纠错题全部自洽、正确；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix35-fix-error\report.md。
回执：【MF-01｜library｜第三十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 新字段与出题提示 ／ 程序验算（数学、代码）／ 解题员新增步骤 ／ 回归集与真实结果 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [35-MF03] client｜判定：答对但只给结果时追问一次（合入你第二十八批的追问，并收窄触发条件）

~~~text
【MentorFlow 第三十五批｜MF-03（工位 client）｜判定：答对但只给结果时追问一次（合入你第二十八批的追问，并收窄触发条件）】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git branch keep/client-28 396bdb3   # 你第二十八批的提交
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1。花费只记录，但要报告。
做法已由架构师定好（用户 2026-10-02 确认），照“要做”逐条实现，不自行换方案；实现中发现方案本身走不通，写清证据和原因交回，不自己另想一套。只做一轮：实现 → 测 → 报告。
遇到需要选择的细节：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，冲突由架构师合并）：
  35-MF04（platform）：建计划——course/planner_agents.py、app/plan_flow.py、course/plan_intent.py、web/src/pages/NewPlan.tsx、connections/gateway.py 里用途档位的几行。
  35-MF01（library）：纠错题自洽——course/question_agent.py 的纠错题部分、course/question_review.py、新增的代码运行模块。
  35-MF03（client）：判定追问——course/judge_agent.py、course/data/ 下的判定示例、app/classroom.py 的 _judge 与各作答处理里的追问。
  35-MF02（runtime）：学习档案与两项偶发测试——course/profile_agent.py、app/classroom.py 的 _profile_later、两个测试文件。
背景（CURRENT_STATE [TEACH-04] 的“方案”）：没见过的考卷上剩下那 1 条冤枉：题目问“说明 range(1, 6, 2) 是否包含 6、步长 2 怎样决定相邻的数”，学生只写 [1,3,5]——结果全对，但没写说明，被判错。你第二十八批的“信息不足就追问一次 + 同类示例”正是处理这种情况的；当时对比测试显得没用，是架构师的测试设计问题（所有追问都用“我就是这个意思，没有别的了”回答），不是你的做法错。用户 2026-10-02 确认按此合入，并要求所有测试在最新主线上跑。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-04]（全部，含“方案”“过程题”）、[TEACH-06]（判定前不泄题）、[QA-01]；你第二十八批的报告（.local/test-tmp/judge-followup/report.md）与 keep/client-28 上的改动；app/classroom.py 当前的判定与快速确认部分（第三十四批你改过）。仍然不许读 D:\MentorFlow\.local\test-tmp\judge-holdout\。
要做（照做）
  1. 把 keep/client-28 的“信息不足 → 追问一次 → 合并两次回答再判”和同类示例移植到 6165200 上（与第三十四批的快速确认、深问改动共存）。
  2. 收窄触发条件：只有在作答里没有任何说错的内容（所有要点不是 wrong），且缺的只是题面明确要求的说明、过程或理由（作答给了正确结果或正确方向）时才判“信息不足”；作答有说错的、或答非所问的，照常判错，不追问。每题最多追问一次；追问语句中性，程序检查不含任何要点或参考答案内容。
  3. 测试：离线覆盖触发与不触发的各种情况（含 [1,3,5] 这类、只说“这个说法不对”这类、明显错答）；自建 180 个作答真实回归不退步（冤枉 0、放水 0）；报告追问触发了多少次、分别落在人工判对和判错的哪些作答上。真实（≤ ¥1）。
完成标准：触发条件按第 2 条；自建 180 不退步；全量测试通过。之后由独立员工在最新主线上用“没见过的考卷”公平复测（被追问时，人工判对的作答由模拟学生用自己的话补上，判错的补不出来）。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix35-judge-followup\report.md。
回执：【MF-03｜client｜第三十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 移植与收窄后的触发条件 ／ 自建 180 结果与追问分布 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [35-MF02] runtime｜学习档案只写有据的话 + 两项偶发失败的测试修稳

~~~text
【MentorFlow 第三十五批｜MF-02（工位 runtime）｜学习档案只写有据的话 + 两项偶发失败的测试修稳】

开工同步（第一步）
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.3。花费只记录，但要报告。
做法已由架构师定好（用户 2026-10-02 确认），照“要做”逐条实现，不自行换方案；实现中发现方案本身走不通，写清证据和原因交回，不自己另想一套。只做一轮：实现 → 测 → 报告。
遇到需要选择的细节：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，冲突由架构师合并）：
  35-MF04（platform）：建计划——course/planner_agents.py、app/plan_flow.py、course/plan_intent.py、web/src/pages/NewPlan.tsx、connections/gateway.py 里用途档位的几行。
  35-MF01（library）：纠错题自洽——course/question_agent.py 的纠错题部分、course/question_review.py、新增的代码运行模块。
  35-MF03（client）：判定追问——course/judge_agent.py、course/data/ 下的判定示例、app/classroom.py 的 _judge 与各作答处理里的追问。
  35-MF02（runtime）：学习档案与两项偶发测试——course/profile_agent.py、app/classroom.py 的 _profile_later、两个测试文件。
背景（CURRENT_STATE [TEACH-01] 的“待定”，用户 2026-10-02 定要修）：学习档案（老师第二天开课接上进度用、报告里“老师的记录”也用它）把学生“答对了一道‘方程的解’的定义题”写成“已经掌握了解方程的操作”，说过头了（第三次小测试第二次复跑报告 .local/test-tmp/acceptance3c/report.md 的 P3）。另有两项测试在全量里偶发失败、单独重跑能过：tests/refactor/server/test_active_account_closure.py::test_active_account_closure_prepare_commit_recovery、tests/refactor/app/test_plan_read_browser.py::test_home_and_plans_read_failure_retry_and_stale_list。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-01]、[RPT-01]、[QA-01]；course/profile_agent.py；app/classroom.py 的 _profile_later；复跑报告里 P3 的证据；两个测试文件。
要做（照做）
  1. 程序从判定记录整理一份“已验证事实”交给档案：每个知识点考过哪些题（随口问 / 检测 / 检验）、题型与角度（如 recall 定义、apply 应用）、对错、最近一次结果；没考过的写“还没考过”。档案提示要求：关于掌握情况的每句话都要能对应到这些事实，不能把答对定义题写成会操作，不能写没考过的内容。
  2. 用复跑里那次说过头的档案调用（保存的输入）离线回放与一次真实调用（≤ ¥0.3）对比，你本人核对新档案没有说过头。
  3. 两项偶发测试：先找出不稳定的原因（固定等待时间、线程或临时文件竞争等），改成确定性的等待或隔离，不靠加长等待掩盖；各连续跑 20 次全部通过，并在全量里跑通。
完成标准：新档案不再说过头；两项测试连续 20 次通过；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix35-profile-flaky\report.md。
回执：【MF-02｜runtime｜第三十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 已验证事实的内容与档案提示 ／ 回放与真实对比 ／ 两项测试的原因与修法、20 次结果 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

## 第三十四批任务原文（2026-10-02，基线 cfd1181）

修第三次小测试第二次复跑剩下的两处 P2；用保存的记录与小额真实调用复测，不再整轮重跑。两处都通过就算小测试达标。

### [34-MF03] client｜快速确认与深问题目改用核对过的题库题（不再现场出未核对的题）

~~~text
【MentorFlow 第三十四批｜MF-03（工位 client）｜快速确认与深问题目改用核对过的题库题（不再现场出未核对的题）】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项（已知两项偶发失败：服务器账号注销测试、首页读取失败的浏览器测试，单独重跑能过就注明）。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.6。花费只记录，但要报告。
只做一轮（用户定，防止无穷无尽地改）：实现 → 测 → 报告。完成标准是“这个失败不再出现”；没达到不要继续微调，回执写清没过的样本、原因和建议换的方法。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑两个任务（按文件分工，冲突由架构师合并）：
  34-MF03（client）：app/classroom.py 的 _quick_check、_deepen_question 及其取题部分；必要时调用 course/question_review.py 的现有核对入口（不改它）。
  34-MF04（platform）：course/planner_agents.py、app/plan_flow.py 里正式生成后的重复检查部分；connections/gateway.py 里给新用途加档位的几行。
背景：第三次小测试第二次复跑（.local/test-tmp/acceptance3c/report.md 的 P2-01）：说“我会了”后出的快速确认题是现场生成的，没走题库那套“结构检查 → 不看答案的独立解题 → 核对”，结果有两个正确选项，点另一个正确选项会被判错（冤枉风险）；现场出题还等了 24 秒（op-0014）。“拿不准再深问一道”（_deepen_question）题库没合适题时也现场出题，有同样风险。见 CURRENT_STATE [TEACH-03] 的“待修”、[CHK-01]、[TEACH-04]。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-03]、[TEACH-02]、[TEACH-04]、[CHK-01]（题库与核对流程）、[QA-01]；复跑报告的 P2-01 与证据；app/classroom.py 的 _quick_check、_deepen_question；course/question_review.py 与 _bank_fill 里的核对流程。
要做
  1. 快速确认与深问优先从已经核对过的题库里取这个点没用过的单点题（快速确认优先选择题；深问选比原题更深的角度，不复述原题）。取题是即时的，不再等模型。
  2. 题库里没有可用题时：现场出的题必须先过同样的结构检查、不看答案的独立解题和核对（复用现有入口，不另写一套）；没通过就不出这道题，按现有规则继续（快速确认跳过、直接往下讲并记一次“未确认”；深问则按原题现有结果处理），并写进记录。
  3. 用过的题记为已问，不让同一道题在之后的检测里原样重复。
  4. 测试：离线回放复跑里那道双正确选项的题（必须被拦下或不再出现）；假模型覆盖“题库有题即时出 / 题库没题现场出并核对 / 核对不过的处理 / 中断重开”；真实跑 2 次快速确认和 1 次深问（≤ ¥0.6），记录耗时与题目人工核对。
完成标准：快速确认与深问不再出现未核对的题；复跑那道双正确选项题被拦下；题库有题时出题 ≤1 秒；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix34-quick-check\report.md。
回执：【MF-03｜client｜第三十四批｜完成/部分完成/受阻】基线 ／ 提交 ／ 取题与兜底流程 ／ 回放与真实结果（耗时、人工核对）／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [34-MF04] platform｜跨模块重复第二轮：按意思比较（换方法）

~~~text
【MentorFlow 第三十四批｜MF-04（工位 platform）｜跨模块重复第二轮：按意思比较（换方法）】

开工同步（第一步）
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项（已知两项偶发失败：服务器账号注销测试、首页读取失败的浏览器测试，单独重跑能过就注明）。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1。花费只记录，但要报告。
只做一轮（用户定，防止无穷无尽地改）：实现 → 测 → 报告。完成标准是“这个失败不再出现”；没达到不要继续微调，回执写清没过的样本、原因和建议换的方法。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑两个任务（按文件分工，冲突由架构师合并）：
  34-MF03（client）：app/classroom.py 的 _quick_check、_deepen_question 及其取题部分；必要时调用 course/question_review.py 的现有核对入口（不改它）。
  34-MF04（platform）：course/planner_agents.py、app/plan_flow.py 里正式生成后的重复检查部分；connections/gateway.py 里给新用途加档位的几行。
背景：你第三十二批的重复检查按标题、摘要、出处的字面比较，能拦字面重复；但真实生成里换了说法的重复仍漏过：第三十二批的“解应用方程并检验”，第三次小测试第二次复跑里模块二的“应用”点实际考的是解 x + 11 = 41（.local/test-tmp/acceptance3c/report.md 的 P2-02，point-review.json 的 9 对比较）。这是第二轮，按两轮规则换方法：按意思比较（CURRENT_STATE [PLAN-04] 的“第一轮”与说明）。
先读：AGENTS.md；CURRENT_STATE 的 [PLAN-04]（全部）、[PLAN-02]、[QA-01]；你第三十二批的报告（.local/test-tmp/fix32-no-duplicate/report.md）；复跑报告的 P2-02 与 point-review.json；course/planner_agents.py 与 app/plan_flow.py 的重复检查部分。依据：句向量语义相似度（Sentence-BERT，https://arxiv.org/abs/1908.10084；中文开源向量模型 BGE，https://arxiv.org/abs/2309.07597）或让模型逐对判断是否同一学习内容。
要做
  1. 换成按意思比较：优先用一次模型调用（新用途，思考“高”），给出前面模块的知识点与本模块的知识点（标题、摘要、对应原文片段），逐对判断“是否同一学习内容”（区分“在前面基础上的新应用或新要求”与“换个说法的重复”），说明理由与对应的前面知识点。不新增大依赖；如认为本地向量模型更合适，写明理由与体积，不超过 30 MB 才可加。字面检查保留作为第一道。
  2. 判为重复的：这个模块按“不重复”的要求重生成一次；仍重复就不收这些点，并在轮廓或生成结果里告诉用户（资料不够时提示补资料，已有的“资料不够”说明照旧）。
  3. 测试：离线回放第三十二批与本次复跑保存的两组生成输出（两处意思相同的重复都必须被判出），同时上一轮旧样本里 3 个“不重复的应用点”不能误判；真实跑一次同一表单与资料（≤ ¥1），人工逐对核对。
完成标准：两组保存输出里的语义重复全部判出、不重复的点不误判；真实生成没有跨模块重复；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix34-semantic-duplicate\report.md。
回执：【MF-04｜platform｜第三十四批｜完成/部分完成/受阻】基线 ／ 提交 ／ 比较方法与理由 ／ 回放结果（判出与误判）／ 真实生成核对 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

## 第三十三批任务原文（2026-10-02，基线 9d039ec）

### [33-MF06] classroom｜再用同一脚本复跑第三次小测试

~~~text
【MentorFlow 第三十三批｜MF-06（工位 classroom）｜再用同一脚本复跑第三次小测试】

开工同步（第一步）
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 9d039ec
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。只允许提交验收脚本的必要调整（tests/refactor/acceptance/，默认跳过）；提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止改产品代码（refactor/ 下任何文件）；禁止 ssh、连接 MentorFlow 服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀）：所有产物在 D:\MentorFlow\.local\test-tmp\acceptance3c\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：老师讲解、题目、判定都由你本人对照原文逐条核对，不让被测模型给自己打分。回执里的测试结果要和实际一致（已知两项偶发失败的测试：服务器账号注销、首页读取失败的浏览器测试，单独重跑能过就注明）。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥8，第一段（建计划 + 第一个模块）最多 ¥3；花费只记录，按用途报告。
遇到需要选择的事：自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。遇到阻断流程的问题（P1）：记录完整证据后，能绕过就绕过继续（写明怎么绕的），绕不过才停。
同时在跑：无。你是独立验收人，只找问题，不修产品代码。
背景：你上次复跑（.local/test-tmp/acceptance3b/report.md）后，架构师修了 BUG-43（PDF 半个字符导致保存失败）、BUG-44（老师记录把直接考算已讲）、导出里的现场讲标注与“欠账”用词；第三十二批修了点“继续”卡住（改成“正在准备”后自动继续），并加了跨模块重复检查与“资料不够”提示（重复检查只能拦字面重复，CURRENT_STATE [PLAN-04]）。都已在 9d039ec。
先读：AGENTS.md；CURRENT_STATE「当前阶段」和 [QA-01]、[PLAN-04]、[TEACH-08]、[BUG-43]、[BUG-44]；你上次的报告和脚本 tests/refactor/acceptance/test_math_complete_flow_real.py。
要做
  1. 同一表单、同一资料从新数据目录完整重跑。轮廓提示资料不够时，选“按资料覆盖范围生成”继续（写明提示内容）。
  2. 复核：上次的 P3-01（点继续不再等；看到“正在准备”时多久自动继续）、P3-03（Markdown 与 PDF 用词）、N-P1-01（联网找到 PDF 后建计划能保存）、N-P2-01（模块之间有没有重复的点，逐点比对）、N-P2-02（导出里的现场讲标注）、N-P3-01（老师记录的已讲点数）；以及前面已通过的 P1-01、P1-02、P2-01、P2-02、P3-02 没有退步。
  3. 尽量覆盖还没测到的：综合题答错拆问、判定拿不准时再深问一道。
  4. 人工核对：讲错、判定前泄题、上屏题合格、冤枉、放水；现场讲点数；速度与按用途花费。
完成标准：上面每一项给出“通过 / 不通过 / 没测到”及证据；新问题按 P1、P2、P3 分级，写明复现步骤与证据位置。
验收：报告写到 D:\MentorFlow\.local\test-tmp\acceptance3c\report.md（附 dialogue.md 与 calls\）；pytest tests/refactor 全部通过。
回执：【MF-06｜classroom｜第三十三批｜完成/部分完成/受阻】基线 ／ 提交（如有）／ 各环节通过情况 ／ 逐项复核 ／ 新问题清单（P1/P2/P3）／ 人工核对结果 ／ 现场讲点数 ／ 速度 ／ 花费（按用途）／ 本地最终占用
~~~

## 第三十二批任务原文（2026-10-02，基线 dd0215f）

两个任务修第三次小测试复跑剩下的问题；合入后 MF-06 再用同一脚本复跑。

### [32-MF01] library｜点“继续”不能卡住等后台备课（P3-01 第二轮，换方法）

~~~text
【MentorFlow 第三十二批｜MF-01（工位 library）｜点“继续”不能卡住等后台备课（P3-01 第二轮，换方法）】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项（已知两项偶发失败：服务器账号注销测试、首页读取失败的浏览器测试，单独重跑能过就注明）。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.5。花费只记录，但要报告。
只做一轮（用户定，防止无穷无尽地改）：实现 → 测 → 报告。完成标准是“这个失败不再出现”；没达到不要继续微调，回执写清没过的样本、原因和建议换的方法。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑两个任务（按文件分工，冲突由架构师合并）：
  32-MF01（library）：app/classroom.py 的备课与“继续”部分（_teach_point、_prepare_*、_prep_*、锁）、web/src/pages/Classroom.tsx 与 web/src/classroom/ 里显示“正在准备”的部分。
  32-MF04（platform）：course/planner_agents.py、app/plan_flow.py 里轮廓与正式生成的检查部分。
背景：第三十批你修了备课领先，但第三次小测试复跑（.local/test-tmp/acceptance3b/report.md 的 P3-01）仍有：点“继续”等 20 秒（现场备稿，call-0100），答完一题后等 88 秒（op-0053）。原因判断：前台 _prepare_point 会去拿后台备课的锁，等后台那一批写稿 + 核对做完才返回（classroom.py:333–346、1597；diagnose.py 输出）。这是这个问题的第二轮，按两轮规则换方法（CURRENT_STATE [TEACH-08] 的“待修（第二轮）”）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-08]（全部）、[TEACH-06] 课堂方案与速度目标、[QA-01]；复跑报告的 P3-01 与证据；app/classroom.py 的备课与“继续”部分；web/src/pages/Classroom.tsx、web/src/classroom/GuidedLesson.tsx。
要做
  1. 换方法：任何课堂操作（继续、作答、选择）都不再在桥调用里等后台备课。下一点正在后台备时，操作立刻返回，课堂显示“老师正在准备这一点…”（附已等秒数），备好后通过事件自动显示，不用用户再点；后台没在备这一点时，前台立刻单独备这一点（不排在后台那一批后面）。锁只保护写库，不包住模型调用。
  2. 界面：显示“正在准备”的状态，输入框仍可打字插话；备好后自动出第一步。
  3. 测试：用模拟时钟和假模型（假模型故意慢）复现“快速学习者追上后台”的节奏，证明每次桥调用都在 1 秒内返回、备好后自动继续、不会重复备同一点、中断重开后状态正确；真实模型跑一小段（≤ ¥0.5）确认。
完成标准：复现节奏下所有课堂桥调用 ≤1 秒返回；“正在准备”后自动继续；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix32-nonblocking\report.md。
回执：【MF-01｜library｜第三十二批｜完成/部分完成/受阻】基线 ／ 提交 ／ 改法（锁与事件）／ 界面状态 ／ 复现结果（每次桥调用耗时）／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [32-MF04] platform｜资料覆盖不到时不能用重复内容凑满模块（N-P2-01）

~~~text
【MentorFlow 第三十二批｜MF-04（工位 platform）｜资料覆盖不到时不能用重复内容凑满模块（N-P2-01）】

开工同步（第一步）
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项（已知两项偶发失败：服务器账号注销测试、首页读取失败的浏览器测试，单独重跑能过就注明）。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1。花费只记录，但要报告。
只做一轮（用户定，防止无穷无尽地改）：实现 → 测 → 报告。完成标准是“这个失败不再出现”；没达到不要继续微调，回执写清没过的样本、原因和建议换的方法。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑两个任务（按文件分工，冲突由架构师合并）：
  32-MF01（library）：app/classroom.py 的备课与“继续”部分（_teach_point、_prepare_*、_prep_*、锁）、web/src/pages/Classroom.tsx 与 web/src/classroom/ 里显示“正在准备”的部分。
  32-MF04（platform）：course/planner_agents.py、app/plan_flow.py 里轮廓与正式生成的检查部分。
背景：第三次小测试复跑（.local/test-tmp/acceptance3b/report.md 的 N-P2-01）：资料（OpenStax 一节）没有乘除内容，补充说明要求“3 个模块、每模块 3 点”，生成为了凑数，第二个模块的 3 个点和第一个模块重复（解与代入检验 / 加减一步方程 / 先化简再解），又计了 48 分钟。你第三十批加的数量约束是对的，但资料不够时不能用重复内容凑（CURRENT_STATE [PLAN-04]“知识点从资料中提炼”与“待修”）。证据：calls/call-0005～0007、generated.json、points.json、modules.json、outline.json。
先读：AGENTS.md；CURRENT_STATE 的 [PLAN-04]（全部）、[PLAN-02]、[PLAN-06]、[ARCH-06]、[QA-01]；复跑报告的 N-P2-01 与证据；course/planner_agents.py、app/plan_flow.py 的轮廓与正式生成部分。
要做
  1. 程序检查跨模块重复：正式生成后比较各模块知识点（标题、摘要、出处块），发现与前面模块重复的点就让这个模块按“不重复”的要求重生成一次；仍重复就不收这些点。
  2. 资料不够时如实说：用户要求的数量或范围超过资料能覆盖的，在轮廓阶段就告诉用户“资料只够 X 个模块 / Y 个点，缺 ××”，给出选择：补资料（贴网址或再找资料）或按资料能覆盖的范围生成；不用重复或资料外的内容凑数。
  3. 测试：用复跑的同一表单和同一资料离线回放（保存的生成输出）证明重复能被发现；真实跑一次轮廓 + 正式生成（≤ ¥1），核对没有重复点、并出现资料不够的说明。
完成标准：同一表单与资料下，没有跨模块重复的点，并在轮廓里说明资料不够；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix32-no-duplicate\report.md。
回执：【MF-04｜platform｜第三十二批｜完成/部分完成/受阻】基线 ／ 提交 ／ 重复检查做法 ／ 资料不够时的说明与选择 ／ 离线与真实结果 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

## 第三十一批任务原文（2026-10-02，基线 cd09590）

### [31-MF06] classroom｜用同一个脚本重跑第三次小测试（复测第三十批修复）

~~~text
【MentorFlow 第三十一批｜MF-06（工位 classroom）｜用同一个脚本重跑第三次小测试（复测第三十批修复）】

开工同步（第一步）
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard cd09590
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。只允许提交验收脚本的必要调整（tests/refactor/acceptance/，默认跳过）；提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止改产品代码（refactor/ 下任何文件）；禁止 ssh、连接 MentorFlow 服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀）：所有产物在 D:\MentorFlow\.local\test-tmp\acceptance3b\ 下；中间产物用完即删（被系统拦住就列路径），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：老师讲解、题目、判定都由你本人对照原文逐条核对，不让被测模型给自己打分。回执里的测试结果要和实际一致。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥8，第一段（建计划 + 第一个模块）最多 ¥3；花费只记录，按用途报告。
遇到需要选择的事：自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。遇到阻断流程的问题（P1）：记录完整证据后，能绕过就绕过继续（写明怎么绕的），绕不过才停。
同时在跑：无。你是独立验收人，只找问题，不修产品代码。
背景：你的第三次小测试（.local/test-tmp/acceptance3/report.md）找出 P1 2、P2 2、P3 3。架构师已修 P1-01、P3-02、P3-03（BUG-41、BUG-42、报告改名），第三十批修了 P1-02、P2-01、P2-02、P3-01（CURRENT_STATE「当前阶段」与各条目“已修”），都已在 cd09590。现在用同一个脚本、同样的表单（含“8–10 个点、每模块 3 点”的补充说明）和同样的资料（OpenStax 页面）从新的数据目录重跑，确认这些问题真的没了。
先读：AGENTS.md；CURRENT_STATE「当前阶段」和 [QA-01]、[CHK-01]、[PLAN-04]、[PLAN-09]、[TEACH-08]、[BUG-41]、[BUG-42]；你上次的报告和脚本 tests/refactor/acceptance/test_math_complete_flow_real.py。
要做
  1. 从新数据目录完整重跑：建计划 → 分步上课 → 模块检测与重测、直接考 → 最终检验 → 报告与导出 → 自由检验 → 4 个中断点恢复 → 时间账本手算。
  2. 逐条复核上次的 7 个问题：P1-01 说“我会了”后快速确认答对能继续；P1-02 最终检验不会永久卡住（若有题出不了，看提示与“重新准备题目”能否走下去）；P2-01 资料里的分数正确；P2-02 点数符合补充说明；P3-01 点“继续”不用等现场备稿；P3-02 评语不再半句；P3-03 报告用词。
  3. 尽量覆盖上次没测到的：综合题答错拆问、判定拿不准时再深问一道、过程题只给结果时的追问。
  4. 人工核对：讲错、判定前泄题、上屏题合格、冤枉、放水；现场讲的点数；速度与按用途的花费。
完成标准：1–2 每一项给出“通过 / 不通过 / 没测到”及证据；新发现的问题按 P1、P2、P3 分级，写明复现步骤与证据位置。
验收：报告写到 D:\MentorFlow\.local\test-tmp\acceptance3b\report.md（附 dialogue.md 与 calls\）；pytest tests/refactor 全部通过。
回执：【MF-06｜classroom｜第三十一批｜完成/部分完成/受阻】基线 ／ 提交（如有）／ 各环节通过情况 ／ 上次 7 个问题逐条复核 ／ 新问题清单（P1/P2/P3）／ 人工核对结果 ／ 现场讲点数 ／ 速度 ／ 花费（按用途）／ 本地最终占用
~~~

## 第三十批任务原文（2026-10-02，基线 8de04cd）

两个任务修第三次小测试剩下的问题（第一轮）；合入后 MF-06 用同一个脚本重跑第三次小测试。

### [30-MF04] platform｜最终检验缺题永久卡住（P1-02）+ 补充说明里的点数要求被忽略（P2-02）

~~~text
【MentorFlow 第三十批｜MF-04（工位 platform）｜最终检验缺题永久卡住（P1-02）+ 补充说明里的点数要求被忽略（P2-02）】

开工同步（第一步）
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1.5。花费只记录，但要报告。
只做一轮（用户定，防止无穷无尽地改）：实现 → 测 → 报告。达到完成标准就收；没达到不要继续微调，回执写清没过的样本、原因判断和建议换的方法。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑两个任务（按文件分工，冲突由架构师合并）：
  30-MF04（platform）：course/question_review.py、app/classroom.py 的 _bank_fill 与题库补题部分、course/planner_agents.py、app/plan_flow.py 里把补充说明交给轮廓与生成的部分。
  30-MF01（library）：course/extract.py、app/classroom.py 的备课时机部分（_prepare_later、_prep_targets、_schedule_*、讲稿缺稿时的前台准备）。
背景：第三次小测试（.local/test-tmp/acceptance3/report.md）第一次跑到了计划完成，但有两处问题要你修（CURRENT_STATE [CHK-01] 与 [PLAN-04] 的“待修”）：
  P1-02：最终检验时“等式加减性质与天平模型”缺一道题后永久卡住。原因判断：question_review 的结构检查把题面里的“同时”当成一题塞了多个要求而拒题；每点出题 2 次、每题核对 2 次的上限用完后不再补题，重试只在“检验中 / 正在准备题目”之间打转。证据：bundled-repro.json、bank-block-evidence*.json、candidate-recovery.json、operations/op-0089、op-0092。
  P2-02：补充说明里写了“总共 8–10 个点、每模块 3 点”，实际 3 个模块 11 点（3 + 5 + 3），第二个模块标题说乘除、内容主要是化简。证据：outline.json、generated.json、points.json、plan-steps/08正式生成.json。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]（全部）、[PLAN-04]、[PLAN-01]、[ARCH-06]、[QA-01]；验收报告的 P1-02、P2-02 与上面的证据文件；course/question_review.py、app/classroom.py 的 _bank_fill 与补题部分、course/planner_agents.py。
要做
  1. P1-02 原则：任何情况下都不能让用户永久卡住；也不能放松题目质量（错题、歧义、泄题仍然 0）。具体：①结构检查里“一题多个要求”的判断不再单凭“同时、和、以及”等字眼直接拒题，拿不准的交给核对员判断；②某个点出题次数用完后，用户点“重新准备题目”（或下一次进入检验）要能重新开始一轮出题与核对（每次用户操作最多一轮，次数照样持久保存，防止后台无限重试）；③实在出不了，明确告诉用户哪一个点出不了题、为什么，并给出可继续的操作，不在两个状态间打转。
  2. P2-02：补充说明里用户写明的数量与范围要求（总点数、每模块点数、模块数、只学哪些内容）要传给轮廓与正式生成并照做；生成结果超出时程序检查并让模型按要求调整一次，仍不符合就在轮廓里明确告诉用户。不是给全产品加点数上限，用户没写就按原来的每点约 15 分钟划分。
  3. 测试：离线回放验收保存的拒题与出题记录（不花钱）复现 P1-02 并证明修后能走下去；P2-02 用验收的同一份表单和资料真实生成一次轮廓与正式生成（≤ ¥1），核对点数与模块内容。
完成标准：P1-02 离线复现修前卡住、修后能继续，并有“出不了题时的提示与可继续操作”的测试；P2-02 真实生成符合用户写明的数量要求；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix30-bank-plan\report.md。
回执：【MF-04｜platform｜第三十批｜完成/部分完成/受阻】基线 ／ 提交 ／ P1-02 修法与修前修后对照 ／ P2-02 修法与真实生成结果 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [30-MF01] library｜数学网页分数抽错（P2-01）+ 讲稿没提前备好时等 1–2 分钟（P3-01）

~~~text
【MentorFlow 第三十批｜MF-01（工位 library）｜数学网页分数抽错（P2-01）+ 讲稿没提前备好时等 1–2 分钟（P3-01）】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard BASE
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1。花费只记录，但要报告。
只做一轮（用户定，防止无穷无尽地改）：实现 → 测 → 报告。达到完成标准就收；没达到不要继续微调，回执写清没过的样本、原因判断和建议换的方法。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑两个任务（按文件分工，冲突由架构师合并）：
  30-MF04（platform）：course/question_review.py、app/classroom.py 的 _bank_fill 与题库补题部分、course/planner_agents.py、app/plan_flow.py 里把补充说明交给轮廓与生成的部分。
  30-MF01（library）：course/extract.py、app/classroom.py 的备课时机部分（_prepare_later、_prep_targets、_schedule_*、讲稿缺稿时的前台准备）。
背景：第三次小测试（.local/test-tmp/acceptance3/report.md）有两处问题要你修（CURRENT_STATE [PLAN-09]、[TEACH-08] 的“待修”）：
  P2-01：OpenStax 网页里用 MathML 写的分数被抽成整数并与辅助文字重复拼接（3/4 变成 34），数学原文被污染，可能导致讲错。原因判断：course/extract.py 的 HTML 行内文字只把子节点文字拼起来，没有处理 mfrac 等数学结构。证据：check_math_source.py、source-math-audit.json、openstax-original.html、source_chunks.json。
  P3-01：第 3 天第一点和第三点讲稿没提前备好，点“继续”等前台现场备稿 59 秒和 113 秒；与“上课中始终至少领先 3 个点”（第二十五批）不符。证据：ops.json、calls、metrics.json（按 seconds > 50 筛）。
先读：AGENTS.md；CURRENT_STATE 的 [PLAN-09]、[TEACH-08]（全部，含“避开高峰价”）、[TEACH-06]、[QA-01]；验收报告的 P2-01、P3-01 与上面的证据；course/extract.py；app/classroom.py 的备课时机部分。
要做
  1. P2-01：HTML 里的 MathML（分数、上下标、根号、运算符等常见结构）转成可读的线性文字（如 3/4、x^2、√(x+1)），同一公式的可见形式与辅助形式（annotation、屏幕阅读用的重复文字）只保留一份；也检查 PDF、Word 里常见的分数写法有没有类似问题（只修确有问题的）。
  2. P3-01：先用证据查清那两次为什么没提前备好（是领先规则没触发、高峰推迟、新的一天开头、还是前一批核对没完成），写明结论再改；目标是正常上课时点“继续”不再等现场备稿。学到没备好的点时仍然立刻备（不为省钱让人等），但要做到这种情况很少出现。
  3. 测试：P2-01 用验收保存的 OpenStax 原网页离线验证（不花钱），重新抽取后人工核对数学式子；P3-01 用模拟时钟和假模型复现验收的上课节奏，证明讲稿始终提前备好，必要时用真实模型跑一小段（≤ ¥1）确认。
完成标准：OpenStax 页面里的分数、式子抽取正确且不重复；按验收节奏上课时，点“继续”等待现场备稿的次数为 0（假模型复现）；全量测试通过。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\fix30-extract-prep\report.md。
回执：【MF-01｜library｜第三十批｜完成/部分完成/受阻】基线 ／ 提交 ／ P2-01 修法与抽取前后对照 ／ P3-01 原因结论、修法与复现结果 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

## 第二十九批任务原文（2026-10-02，基线 328907c）

### [29-MF06] classroom｜第三次小测试：功能验收（初中数学，完整跑到最后）

~~~text
【MentorFlow 第二十九批｜MF-06（工位 classroom）｜第三次小测试：功能验收（初中数学，完整跑到最后）】

开工同步（第一步）
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 328907c
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。只允许提交验收脚本（放 tests/refactor/acceptance/，默认跳过）；提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止改产品代码（refactor/ 下任何文件）；禁止 ssh、连接 MentorFlow 服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\acceptance3\ 下；中间产物用完即删，回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，老师讲解、题目、判定都由你本人对照原文逐条核对。回执里的测试结果要和实际一致。
花钱规则（用户定：功能优先，花费不离谱就不管）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥10，第一段（建计划 + 第一个模块）最多 ¥4；花费只记录，但要按调用分类报告。
遇到需要选择的事：自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。遇到阻断流程的问题（P1）：记录完整证据后，能绕过就绕过继续（写明怎么绕的），绕不过才停。回执附“问题记录”（现象、原因判断、证据位置）。
同时在跑：无。你是独立验收人，不修产品代码，不和别人交换结论。
背景：这是验收路线第 1 步的第三次小测试（CURRENT_STATE [QA-01]）。前两次（acceptance、acceptance2）都没跑到后半程；之后的第二十五到二十八批把课堂改成了引导式分步讲课（TEACH-06）、加了讲稿核对上限与高峰调度（TEACH-08）、五类题型与逐条判法（TEACH-04）、出题前独立解题核验（CHK-01）等。用户要求这次的重点是“功能能用”，并且不再无穷无尽地改：你只找问题、不修。已知问题清单见 CURRENT_STATE「当前阶段」的“现在的已知问题”，遇到已知问题照样记录，但注明“已知”。
先读：AGENTS.md；CURRENT_STATE「当前阶段」和 [QA-01]、[TEACH-04]、[TEACH-06]、[TEACH-08]、[CHK-01]、[CHK-02]、[RPT-01]、[TEACH-07]、[PLAN-05]；tests/refactor/acceptance/ 里前两次的验收脚本（可复用）；.local/test-tmp/acceptance2/report.md 的写法。
要做
  1. 建计划：新账号（隔离数据目录），模拟时钟；目标“初中数学：一元一次方程（含解法与简单应用）”，3–4 天、3 个模块、8–10 个知识点，每天上限按计划合理值；真实联网找资料（PLAN-06），看能否推荐到权威资料（不行就记录，再贴一个权威网址继续）。
  2. 上课：按新课堂的真实桥方法走（classroom.next 继续、classroom.choose 选引导选项、classroom.back 回到刚才、classroom.reply 作答与插话），覆盖：引导选项选对选错、讲解中插话提问后回到刚才、随口问选择题答对与答错（看第二道随口问）、简答作答写得很简略、只给结果的过程题、说“我会了”、想让老师直接判过。记录每个点是讲稿还是现场讲（live）。
  3. 检测：至少一个模块正常学完做模块检测（含故意答错 → 重测），一个模块用“直接考”；综合题答错拆问。
  4. 最终检验：所有模块通过后跑完最终检验（含答错退级再连续答对），直到计划完成。
  5. 报告与自由检验：生成学习报告（看三组图表数据、逐点评语、现场讲标注、导出 PDF 与 Markdown 能否成功），再做一轮自由检验（题数 5，含答错 → 需要复习）。
  6. 中断恢复：在“判定已记、老师回复前”“模块检测最后一题刚答完”“最终检验中途”“自由检验中途”各中断一次（关掉课堂对象重开），核对没有重复记账、没有丢题、能接着走。
  7. 时间账本：按模拟日核对每日目标、正式/暂记、欠账、超前、逾期是否合理（手算对照）。
  8. 人工核对：老师讲解（含现场讲）事实与表述错误、判定前泄题、题目合格（答案错/歧义/泄题）、判定冤枉与放水、追问与提示是否得当；逐条留证据。
  9. 记录速度（出下一步、判定出结果、老师首字）和按用途分类的花费；跑一次 tests/refactor/cache_prefix 的 prefixcash 诊断（不卡验收）。
完成标准：上面 1–7 每一步给出“通过 / 不通过 / 没测到”及证据；问题按 P1（阻断功能）、P2（教错、冤枉、放水、算错时长等）、P3（体验与表述）分级列出，每条写明复现步骤与证据位置。
验收：报告写到 D:\MentorFlow\.local\test-tmp\acceptance3\report.md（另附完整对话 dialogue.md 与每次调用记录 calls\）；验收脚本提交在 office/classroom（默认跳过，设环境变量才调用真实模型）；pytest tests/refactor 全部通过。
回执：【MF-06｜classroom｜第二十九批｜完成/部分完成/受阻】基线 ／ 提交 ／ 各环节通过情况表（1–7）／ P1、P2、P3 问题清单（含已知问题标注）／ 人工核对结果（讲错、泄题、出题、判定）／ 现场讲的点数 ／ 速度 ／ 花费（按用途）／ 本地最终占用 ／ 问题记录
~~~

## 第二十八批任务原文（2026-10-01，基线 9c9f32b）

四个任务按文件分工并行；MF-02、MF-05、MF-07 本批不派活。这批是“换方法”的第二轮（见协作方式“防止无穷无尽地改”）：没达标就如实报告，不在任务里继续微调。合入后跑第三次小测试。

### [28-MF01b] library｜讲稿按方案 A 收尾：保留第二十七批核对，只并入程序跑代码与“学生”试答

~~~text
【MentorFlow 第二十八批补充｜MF-01（工位 library）｜讲稿按方案 A 收尾：保留第二十七批核对，只并入程序跑代码与“学生”试答】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git branch keep/library-28 3b80451   # 留住你第二十八批的提交，方便取代码
  git reset --hard 4484d87
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.8。花费只记录，但要报告。
只做一轮：实现 → 测 → 报告；不再调核对提示。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑：28-MF06b（MF-06，判定对比，不改代码）。本任务只改 course/lesson_prep.py、app/classroom.py 的备课部分和相关测试。
背景：讲稿核对两轮没解决，用户 2026-10-02 选方案 A（CURRENT_STATE「当前阶段」与 [TEACH-08]）：保留第二十七批“按检查清单核对”的做法；你第二十八批的工作里只保留验证有效的两样——Python 例子由程序在限时、隔离的子进程里真跑并把真实输出写进讲稿；不看答案的“学生”试答引导题和随口问（拦住歧义 1/2、误拦 0/6）。逐条说法核验会把输出上限用光（6 点 3 次截断），不要；调用层的结构化输出参数（DeepSeek 高思考不支持）也不要。合入后直接跑第三次小测试。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-08]（全部）、[TEACH-06] 课堂方案；你第二十八批的报告（.local/test-tmp/prep-claims/report.md）；keep/library-28 分支上的改动。
要做
  1. 在 4484d87 上：保留第二十七批的核对流程与检查清单不变；从 keep/library-28 移植“程序跑代码”（核对只查讲解与真实输出是否一致）和“学生试答”（歧义就退回修改，计入现有的修改与重写上限）；“严格解析加一次格式修复”可以保留。不移植逐条说法核验、不移植 connections/ 里的结构化输出参数。
  2. 离线测试：跑代码的隔离与超时、真实输出写入、学生试答拦歧义与不误拦、重试上限、中断重开计数不重来；全量测试通过。
  3. 真实检查（≤ ¥0.8）：初中数学 2 点、Python 2 点（用你已有的资料），完整走写稿 → 跑代码 → 学生试答 → 核对；你本人逐条核对并列出发现的问题（只报告，不再调提示）。
完成标准：4 个点全流程走完，无截断、无格式失败，代码输出都是真实运行结果；报告列出首次核对通过数、人工核对发现的问题、每点花费（高峰价）。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\prep-final\report.md。
回执：【MF-01｜library｜第二十八批补充｜完成/部分完成/受阻】基线 ／ 提交 ／ 移植了什么、没移植什么 ／ 离线测试 ／ 4 点真实结果（截断、格式失败、首次通过、人工核对问题）／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [28-MF06b] classroom｜用同一份考卷对比 28-MF03 的新判法（追问 + 示例）与当前主线

~~~text
【MentorFlow 第二十八批补充｜MF-06（工位 classroom）｜用同一份考卷对比 28-MF03 的新判法与当前主线】

开工同步（第一步）
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 396bdb3          # 这是 28-MF03 在 office/client 上的提交（含“信息不足就追问”和同类示例），同一仓库里可直接用
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）。不改产品代码，本任务不需要提交。
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀）：所有产物在 D:\MentorFlow\.local\test-tmp\judge-holdout\ 下；中间产物用完即删，回执写明最终占用。
验收按 CURRENT_STATE [QA-01]：标注以你第二十七批的人工标注为准，不改标注，不让被测模型给自己打分。
花钱规则（用户定）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1，第一段最多 ¥0.4。花费只记录，但要报告。
遇到需要选择的事：自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。回执附“问题记录”（现象、原因、排查、解决、验证）。
背景：你刚用当前主线（9c9f32b）跑完 174 条：冤枉 1、放水 0、二元一致 98.24%（.local/test-tmp/judge-holdout/report.md）。28-MF03 做了新判法：作答太简略时判“信息不足”，追问一次“能再具体说说……吗”，把两次回答合起来再判；判定请求里加 3 个同类已判示例（CURRENT_STATE [TEACH-04]）。它自己只测了很少的样本，效果没证明。按“两轮规则”，要用你这份它没见过的考卷来决定合不合入。你仍保持独立，不把考卷给任何人。
要做
  1. 用 396bdb3 的判定把同样 174 条跑一遍。考卷每条只有一次作答；判定返回“信息不足”时，模拟学生追问后的回答“我就是这个意思，没有别的了”，按合并后的最终结果计（这是最不利的情况）；另把“信息不足”出现在哪些作答上单列（按人工标注的对/错分开），并人工检查每次追问有没有透露要点。
  2. 和 9c9f32b 的结果逐条对比：冤枉、放水、二元一致率、格式失败，按题型和作答类型列；列出两边结果不同的每一条。
  3. 第一段（≤ ¥0.4）先跑简略、代词省略、只给结果、半对这几类和上次判错的 9 条；结论已经明确（明显更差或明显更好）可以停下，写明理由。
完成标准：给出“合入 / 不合入 / 部分合入（只要追问或只要示例）”的建议和数据依据。
回执：【MF-06｜classroom｜第二十八批补充｜完成/部分完成/受阻】跑了多少条 ／ 两边对比（冤枉、放水、一致率、格式失败）／ “信息不足”出现情况与追问泄题核对 ／ 结果不同的条目清单路径 ／ 建议与依据 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [28-MF04] platform｜出题换方法：不看答案的独立解题核验 + 题型比例程序把关

~~~text
【MentorFlow 第二十八批｜MF-04（工位 platform）｜出题换方法：不看答案的独立解题核验 + 题型比例程序把关】

开工同步（第一步）
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard 9c9f32b
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥2，第一段最多 ¥0.7。花费只记录、不卡验收（QA-01 2026-10-01：功能优先，花费不离谱就不管），但要报告。
只做一轮（用户定，防止无穷无尽地改）：实现 → 测 → 报告。达到完成标准就收；没达到不要继续微调提示，回执写清没过的每个样本、原因判断、建议换的方法（附依据）。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  28-MF04（你）：course/question_agent.py、course/question_review.py、新增的解题核验代码、app/classroom.py 的 _bank_fill、connections/gateway.py 里给新用途加档位与下限的几行。
  28-MF01（library）：讲稿换方法——course/lesson_prep.py、classroom 备课部分、connections 里的结构化输出。
  28-MF03（client）：判定换方法——course/judge_agent.py、answer_compare.py、classroom 的 _judge 与各作答处理函数里的追问流程。
  28-MF06（classroom）：用当前主线跑完“没见过的考卷”，不改产品代码。
背景：出题已经两轮没达标（25-MF03 8/13；27-MF04 历史 7/8，1 道把原文没说的推论当答案，核对员没拦住；8 道全是选择题）。架构师判断是方法问题：核对员能看到标准答案，容易顺着答案认可。用户 2026-10-01 同意换方法（CURRENT_STATE [CHK-01] 的“方法调整”）。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]（全部，尤其“方法调整”）、[TEACH-04]“题型与判法”、[QA-01]；你第二十七批的报告（.local/test-tmp/bank-eval/report.md）；MQAG（https://arxiv.org/pdf/2301.12307）与 MCQG-SRefine（https://arxiv.org/pdf/2410.13191）里答案唯一性、可答性检查的做法；Chain-of-Verification（https://arxiv.org/pdf/2309.11495）关于“核验时不看原稿”的结论。
要做
  1. 独立解题核验（新用途，思考“高”）：只给解题员资料原文和题目（选择题含选项），不给答案、参考、要点。选择题：让它选出答案，并说明有没有别的选项也说得通；exact：它的答案用 answer_compare 和标准答案比对；short、fix_error：它写出作答，再走现有判定（按题目的必答要点判它的作答）；essay 不做解题核验，只做细则检查。解题结果与标准答案不一致、或有两个以上选项说得通、或简答作答按要点判不过 → 这道题连同解题员的理由交给核对员修改；修改后的题再解一次，还不过就丢，只给缺题的点重出一次（次数持久保存）。顺序：程序结构检查 → 独立解题 → 核对员（这时才看答案）→ 修改或丢弃。
  2. 题型比例程序把关：每个模块入库的单点题里，简答 + 纠错（+ 论述）不少于 30%；一批不够就只给缺的点补出简答或纠错题（每点最多一次），不靠提示自觉。
  3. 固定回归集：25-MF03 那 5 道不合格题、27-MF04 那道“宫殿 → 文化传播”的错题放进离线回归集（保存当时的模型输出回放），新流程必须全部拦下；8 道合格题不能误拦。
  4. 真实测试：历史、Python、初中数学各 4 个点出题库（用你已有的样本），你本人逐题核对。第一段（≤ ¥0.7）先跑历史一科和回归集。
完成标准：三科合计入库题合格 ≥98%；答案错、歧义、泄题 0；简答 + 纠错 ≥30%；回归集全部拦下、合格题不误拦。花费只记录（每点“出题 + 解题 + 核对”高峰价）。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\bank-solver\report.md。
回执：【MF-04｜platform｜第二十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 解题核验流程 ／ 题型比例把关 ／ 回归集结果 ／ 三科结果（合格率、三类错误、题型分布、解题员拦下几道）／ 没达标时：没过的样本、原因、建议换的方法 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [28-MF01] library｜讲稿换方法：逐条说法核对 + “学生”试答引导题 + 程序跑代码 + 结构化输出

~~~text
【MentorFlow 第二十八批｜MF-01（工位 library）｜讲稿换方法：逐条说法核对 + “学生”试答引导题 + 程序跑代码 + 结构化输出】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 9c9f32b
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥2，第一段最多 ¥0.7。花费只记录、不卡验收（QA-01 2026-10-01），但要报告。
只做一轮（用户定，防止无穷无尽地改）：实现 → 测 → 报告。达到完成标准就收；没达到不要继续微调提示，回执写清没过的每个样本、原因判断、建议换的方法（附依据）。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  28-MF04（platform）：出题换方法——course/question_agent.py、question_review.py、classroom 的 _bank_fill、gateway 里新用途几行。
  28-MF01（你）：course/lesson_prep.py、app/classroom.py 的备课部分（_prepare_*、_save_prepared、_prep_*）、connections/ 里结构化输出的支持（providers/anthropic_messages.py、gateway.py 的调用参数，只加不改别人行为）。
  28-MF03（client）：判定换方法——course/judge_agent.py、answer_compare.py、classroom 的 _judge 与作答处理里的追问。
  28-MF06（classroom）：跑“没见过的考卷”，不改产品代码。
背景：讲稿核对已经两轮没达标（26-MF01、27-MF01：首次通过 75%、67%；通过的稿里仍有绝对化表述错误、引导题歧义、引申没标；Python append 一点修稿和重写连续返回错误结构）。架构师判断是方法问题：核对员整篇读、整篇判，细处容易放过；代码结果靠模型推理；格式靠模型自觉。用户 2026-10-01 同意换方法（CURRENT_STATE [TEACH-08] 的“方法调整”）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-08]（全部，尤其“方法调整”）、[TEACH-06] 课堂方案、[QA-01]；27-MF01 的报告、human-audit.md 和植入错误集（.local/test-tmp/prep-cost/）；FActScore（https://arxiv.org/abs/2305.14251）拆成一条条说法再逐条核对的做法；Chain-of-Verification（https://arxiv.org/pdf/2309.11495）“分开核验”；DeepSeek 结构化输出（https://api-docs.deepseek.com/guides/json_mode 与 https://api-docs.deepseek.com/guides/anthropic_api ）。
要做
  1. 逐条说法核对：把讲稿（每一步、选项回应、两道随口问的参考答案）拆成一条条独立说法，逐条对照原文判“原文支持 / 不支持 / 原文没有”，每条判定都要引用原文句子；“不支持”要改，“原文没有”必须标为引申，否则要改。核验时不给写稿时的推理，只给说法和原文。拆分与核验分成几次调用由你决定，写明理由。
  2. “学生”试答：引导小问题和两道随口问交给不看答案、不看选项回应的独立“学生”，只给它学生此时已看到的讲稿内容；它选的答案与设定不符、或认为有两个以上选项说得通，就算有歧义，退回修改。
  3. 程序跑代码：讲稿里的 Python 例子由程序在隔离的子进程里运行（限时、禁止联网和读写文件，只允许讲稿里自己的代码），把真实输出写进讲稿；核对只查讲解和输出是否一致。跑不通的例子退回修改。
  4. 结构化输出：先用几分钱探针查清 DeepSeek 的 Anthropic 兼容接口支持哪种（强制工具调用带 JSON 结构，或 JSON 模式）；在调用层加一个可选参数，备课和核对用上；不支持就写明，保留严格解析加一次修复重试。报告格式失败率前后对比。
  5. 测试：27-MF01 的 18 份植入错误稿 + 27-MF01 human-audit 里发现的问题（含“绝对化表述”那条）；6 个点真实复测（数学 2、历史 2、Python 2，含上次失败的 append）。你本人逐条核对。第一段（≤ ¥0.7）先跑植入错误集和 2 个点。
完成标准：植入错误全部拦下（含歧义类 2/2），无错稿误拦 ≤1；6 点人工核对事实、表述、选项回应错误 0；格式失败 0；首次核对通过率 ≥80%。花费只记录（每点高峰价）。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\prep-claims\report.md。
回执：【MF-01｜library｜第二十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ 逐条核对做法（几次调用、为什么）／ “学生”试答 ／ 程序跑代码的隔离做法 ／ 结构化输出探针结论与改动 ／ 植入错误集结果 ／ 6 点复测（首次通过率、人工核对、格式失败）／ 没达标时：没过的样本、原因、建议换的方法 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [28-MF03] client｜判定换方法：“信息不足就追问一次”+ 同类已判示例

~~~text
【MentorFlow 第二十八批｜MF-03（工位 client）｜判定换方法：“信息不足就追问一次”+ 同类已判示例】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 9c9f32b
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1.5，第一段最多 ¥0.5。花费只记录、不卡验收（QA-01 2026-10-01），但要报告。
只做一轮（用户定，防止无穷无尽地改）：实现 → 测 → 报告。达到完成标准就收；没达到不要继续微调提示，回执写清没过的每个样本、原因判断、建议换的方法（附依据）。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  28-MF04（platform）：出题——course/question_agent.py、question_review.py、classroom 的 _bank_fill。
  28-MF01（library）：讲稿——course/lesson_prep.py、classroom 备课部分、connections 结构化输出。
  28-MF03（你）：course/judge_agent.py、course/answer_compare.py、新增的示例库（放 course/data/ 下）、app/classroom.py 的 _judge 以及 _casual_reply、_test_reply、_check_reply、_practice_reply 里处理判定结果的部分。
  28-MF06（classroom）：正在用当前主线跑“没见过的考卷”。你仍然不许读 D:\MentorFlow\.local\test-tmp\judge-holdout\ 下的任何内容，示例库也不能取自那里。
背景：判定校准两轮没解决简略答案被冤枉（26-MF03 公开集冤枉 12；27-MF03 校准后 9；27-MF06 没见过的考卷上 70 条冤枉 2，都是简略答案被要求再复述）。架构师判断是方法问题：判定只有对/错/拿不准，没写全就判错。用户 2026-10-01 同意换方法（CURRENT_STATE [TEACH-04] 的“方法调整”）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-04]（全部，尤其“方法调整”“过程题”）、[TEACH-06]（判定前不泄题）、[QA-01]；27-MF03 报告（.local/test-tmp/judge-calibrate/report.md）；SemEval-2013 学生作答分析把“部分对、不完整”单列的做法；短答判分加检索示例的研究（https://arxiv.org/html/2504.05276v1）。
要做
  1. 新结果“信息不足”：作答方向对、但太简略，无法确认某条必答要点时，不判对也不判错；系统追问一句“能再具体说说……吗”（程序检查追问里不含任何必答要点或参考答案的内容），把原回答和追问后的回答合起来再判；第二次仍不足就按已有内容判，不再追问。适用于简答、纠错、论述，随口问、检测、重测、直接考、最终检验、自由检验都适用；与过程题的 process_missing 分开。中断重开不重复追问、不重复记账。
  2. 同类已判示例：建一个小示例库（每种题型若干道，每道含判对的简略写法、判错的常见误解），判定时按题型和科目挑 3 个最接近的示例放进请求。示例写在 course/data/ 下，不能取自 27-MF06 的考卷。
  3. 测试：自建 180 个作答全部真实回归（上次只跑了 52）；公开集 16 个分歧和剩余 9 条重判；追问是否泄题逐条人工核对。第一段（≤ ¥0.5）先跑 16 个分歧和自建的简略类作答。
完成标准：自建 180 冤枉 0、放水 0；公开集 16 个分歧里属于“我们判错”的全部改对（标签可疑、缺图的单列，不算）；追问泄题 0；追问只在确实信息不足时出现（报告追问比例）。花费只记录。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\judge-followup\report.md。
回执：【MF-03｜client｜第二十八批｜完成/部分完成/受阻】基线 ／ 提交 ／ “信息不足”判法与追问流程 ／ 示例库与挑选方法 ／ 自建 180 结果 ／ 公开集结果 ／ 追问比例与泄题核对 ／ 没达标时：没过的样本、原因、建议换的方法 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [28-MF06] classroom｜用当前主线跑完“没见过的考卷”（174 条）

~~~text
【MentorFlow 第二十八批｜MF-06（工位 classroom）｜用当前主线跑完“没见过的考卷”（174 条）】

开工同步（第一步）
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 9c9f32b
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）。不改产品代码，本任务不需要提交。
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀）：所有产物在 D:\MentorFlow\.local\test-tmp\judge-holdout\ 下；中间产物用完即删，回执写明最终占用。
验收按 CURRENT_STATE [QA-01]：标注以你第二十七批的人工标注为准，不让被测模型给自己打分。回执里的测试结果要和实际一致。
花钱规则（用户定）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.8，第一段最多 ¥0.3。花费只记录，但要报告。
遇到需要选择的事：自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑：28-MF04 出题、28-MF01 讲稿、28-MF03 判定（改判定的人）。你保持独立：不改产品代码，不把考卷给任何人；28-MF03 合入后架构师会再派你用同一份考卷终测。
背景：你第二十七批写好了 174 条考卷，用 9cd348c 跑了 72 条（冤枉 2/70，.local/test-tmp/judge-holdout/report.md）。主线现在含 27-MF03 的判定校准（b16d6df）。
要做
  1. 用 9c9f32b 的判定把 174 条全部跑一遍（第一段 ≤ ¥0.3 先跑上次没跑的类型：论述、口语错字、常见误解、诱导、答非所问）。
  2. 报告按题型、按作答类型的冤枉、放水、一致率，与第二十七批基线对比；逐条列出判错的作答和判定理由；特别说明上次那 2 条冤枉现在怎样。
  3. 评测脚本保持能直接换代码重跑。
完成标准：174 条全部有结果（格式失败单列并说明原因）；报告与判错清单齐全。
回执：【MF-06｜classroom｜第二十八批｜完成/部分完成/受阻】基线 ／ 跑了多少条 ／ 按题型与作答类型的冤枉、放水、一致率（与上次对比）／ 判错清单路径 ／ 上次 2 条冤枉的现状 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

## 第二十七批任务原文（2026-10-01，基线 9cd348c）

五个任务按文件分工并行；MF-05、MF-07 本批不派活。27-MF06 写的“没见过的考卷”在它的 D 盘目录里，27-MF03 不许读（防止对着考卷调）。

### [27-MF01] library｜讲稿省钱（不降质量）+ 核对员检查清单

~~~text
【MentorFlow 第二十七批｜MF-01（工位 library）｜讲稿省钱（不降质量）+ 核对员检查清单】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 9cd348c
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥2，分两段——第一段最多 ¥0.7。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑五个任务（按文件分工，各改各的，冲突由架构师合并）：
  27-MF01（你）：course/lesson_prep.py；app/classroom.py 的 _prepare_batch、_prepare_point、_save_prepared 与 _prep_* ；app/settings.py 的“讲解详略”默认值。
  27-MF03（client）：判定校准——course/judge_agent.py、course/answer_compare.py、app/classroom.py 的 _judge。
  27-MF04（platform）：出题真实评测与修正——course/question_agent.py、course/question_review.py、app/classroom.py 的 _bank_fill。
  27-MF02（runtime）：缓存真实对照与调用失败留证——connections/、course/request_layout.py、teacher_agent.py、profile_agent.py。
  27-MF06（classroom）：写判定用的“没见过的考卷”并跑基线，不改产品代码。
背景：26-MF01 真实复测（.local/test-tmp/prep-retest/report.md、human-audit.md）：核对一次通过 3/4（目标 ≥80%）；每点“写稿 + 核对”约 ¥0.16（高峰价），目标 ¥0.08，核对占 81%；通过核对的稿里仍有 1 道引导题问法歧义、1 道随口问没写清前提、2 处引申没标清。用户定：不降思考强度、不压缩学习内容，从别处省（[QA-01] 花费、[SYS-07]）；讲课默认节奏偏快、偏简洁（[TEACH-06]）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-06]（课堂方案）、[TEACH-08]（全部）、[QA-01] 花费标准、[SYS-07]；26-MF01 的报告与 human-audit.md；course/lesson_prep.py；app/classroom.py 的备课部分。
要做
  1. 省钱（不降质量，逐项量化）：①“讲解详略”默认改为简洁，简洁时默认 3 步（内容确实需要才多一步）；②相邻两点合在一次核对里（只读一遍原文），但结果逐点给，一点失败不丢另一点的稿；③能由程序检查的（格式、长度、选项个数、随口问有参考答案、引申标记与正文一致、代码能运行）都在核对前由程序查掉，核对提示只留需要判断的内容。每一项都要在报告里写清单独省了多少（用同一批输入对照）。
  2. 核对员检查清单：在核对提示里逐条列出要查的事——事实与原文一致、题面或引导问题有没有歧义、操作类问题的前提写没写清、引申有没有标出、每个选项回应对不对、两道随口问的参考答案对不对。
  3. 核对员能力测试：做一套“植入错误”的讲稿（至少 12 份：事实错、数值错、问法歧义、缺前提、引申没标、选项回应判错各 2 份，另加 6 份无错的），离线保存；真实核对一遍：植入的事实与数值错误必须 100% 拦下，表述类问题拦下 ≥90%，无错稿误拦 ≤1 份。26-MF01 human-audit 里那 4 个问题必须在内。
  4. 真实复测：数学 2 点（26-MF01 已找的资料）、历史 2 点、Python 2 点，完整走写稿 → 核对；你本人逐条核对。目标：核对一次通过率 ≥80%，每点“写稿 + 核对”≤ ¥0.08（高峰价），事实与选项回应错误 0、表述问题 0。达不到时如实报告差多少、主要花在哪。
  5. 第一段（≤ ¥0.7）先做第 3 条和 2 个点；不达标就停下，离线分析原因、改提示，再用第二段。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\prep-cost\report.md。
回执：【MF-01｜library｜第二十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 三项省钱做法各省多少 ／ 核对清单 ／ 植入错误测试结果（按错误类型）／ 六点真实复测（一次通过率、人工核对、每点花费）／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [27-MF03] client｜判定校准：简略写法不冤枉 + 公开数据集补完

~~~text
【MentorFlow 第二十七批｜MF-03（工位 client）｜判定校准：简略写法不冤枉 + 公开数据集补完】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 9cd348c
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥2，分两段——第一段最多 ¥0.7。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑五个任务（按文件分工，各改各的，冲突由架构师合并）：
  27-MF01（library）：讲稿省钱与核对清单——course/lesson_prep.py、classroom 备课部分、settings 默认值。
  27-MF03（你）：course/judge_agent.py、course/answer_compare.py、app/classroom.py 的 _judge。
  27-MF04（platform）：出题真实评测与修正——course/question_agent.py、question_review.py、classroom 的 _bank_fill。
  27-MF02（runtime）：缓存与调用层。
  27-MF06（classroom）：正在写判定用的“没见过的考卷”。为了不对着考卷调，你不许读 D:\MentorFlow\.local\test-tmp\judge-holdout\ 下的任何内容；架构师合入后由 27-MF06 用它来终测你的改动。
背景：26-MF03（.local/test-tmp/judge-rules/report.md）：自建 180 个作答全对，但这套样例也用来调了提示；公开人工评分数据集判了 91/100，一致 75/91（82.4%），比人工冤枉 12、放水 4，原因一部分是参考答案转成的评分细则太细、缺图片上下文，一部分是判定员对代词指代和省略写法太严。真实学生答题常写得简略，这是冤枉风险；用户要求冤枉 0（[QA-01]），判法必须测过才算数（[TEACH-04]）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-04]（全部，含“依据”“检验方法”）、[QA-01]；26-MF03 的报告、public-results.json、public-sample.json；course/judge_agent.py、answer_compare.py；GradeOpt（https://arxiv.org/abs/2410.02165）根据判错样本改评分指引的做法。
要做
  1. 逐条分析公开集的 16 个分歧：每条写清是我们判错、人工标签可疑，还是题目缺图等上下文；补判剩下 9 条（1 条格式错要查原因）。
  2. 校准判定（只改判定，不放水）：简略写法、代词指代、省略主语、口语化、错别字，只要放在这道题里意思清楚就算答到；参考答案转出的评分细则不能比题面要求更细。用 GradeOpt 的做法：从判错样本归纳规则，改判定提示和程序比对，不针对单条样本打补丁。
  3. 回归：自建 180 个作答不许退步（冤枉 0、放水 0）；公开集 100 条全部重判，报告一致率、冤枉、放水的前后对比。第一段（≤ ¥0.7）先重判 16 个分歧和一半自建样例，没改善就停下分析。
  4. 不要自己写新的“没见过的考卷”，也不要读 27-MF06 的目录。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\judge-calibrate\report.md。
回执：【MF-03｜client｜第二十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 16 个分歧逐条结论 ／ 判定改了什么、依据哪些样本 ／ 自建样例回归 ／ 公开集 100 条前后对比 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [27-MF06] classroom｜判定用的“没见过的考卷”（独立出题、人工标注、跑基线）

~~~text
【MentorFlow 第二十七批｜MF-06（工位 classroom）｜判定用的“没见过的考卷”（独立出题、人工标注、跑基线）】

开工同步（第一步）
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 9cd348c
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）。提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：不让被测模型给自己打分，标注由你本人完成。回执里的测试结果要和实际一致。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.8，分两段——第一段最多 ¥0.3。每次花钱前把“预计看到什么”写进报告。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑五个任务：27-MF01 讲稿省钱、27-MF03 判定校准、27-MF04 出题评测、27-MF02 缓存。你保持独立：不改任何产品代码，不和 27-MF03 交换样例；你的考卷只放在 D:\MentorFlow\.local\test-tmp\judge-holdout\，本批不提交进仓库（架构师合入 27-MF03 后再派你用它终测）。
背景：判定员在自建样例上全对，但那套样例也用来调了提示（“对着考卷调”）；在公开数据集上一致只有 82%，对简略写法偏严（CURRENT_STATE [TEACH-04] 验证）。用户要求冤枉 0，判法必须测过才算数。需要一套判定员从没见过的考卷。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-04]（全部，含“题型与判法”“过程题”）、[QA-01]；题目结构见 course/question_agent.py（五类题型：choice、exact、short、fix_error、essay；简答要点、论述评分细则的格式）；判定入口 app/classroom.py 的 _judge。不要读 tests/refactor/judge_rules/ 里 26-MF03 的样例（避免照着写）。
要做
  1. 出题：初中数学、历史、Python 三科，按题目结构契约各出 exact、short、fix_error 题至少 3 道，历史另出 2 道 essay；题目依据权威公开资料（写明出处）。
  2. 作答：每道题 6–8 个像真实学生写的作答，必须包括：很简略但对的、用代词和省略的、口语化或有错别字但意思对的、只答对一半的、常见误解、只给结果、答非所问、带“我肯定对”“老师说过”诱导的。共不少于 150 个作答。
  3. 标注：你本人逐条标出应判结果（对/错；简答逐条要点、论述逐项），写明理由；有争议的单独标出，不算进分母。
  4. 基线：用当前代码（9cd348c）的判定跑一遍全部作答（第一段 ≤ ¥0.3 先跑一半），报告按题型、按作答类型的冤枉、放水、一致率，列出每一个判错的作答。
  5. 做一个能重复运行的评测脚本（放在同一个 D 盘目录），以后换上新代码直接重跑。
验收：报告写到 D:\MentorFlow\.local\test-tmp\judge-holdout\report.md；考卷、标注、脚本、基线结果都在该目录；不改产品代码，本批不需要提交（如有提交只能是空提交或说明文档，不含考卷）。
回执：【MF-06｜classroom｜第二十七批｜完成/部分完成/受阻】基线 ／ 考卷规模（按科目、题型、作答类型）／ 标注方法与争议条目 ／ 基线结果（按题型、作答类型：冤枉、放水、一致率）／ 判错清单路径 ／ 脚本用法 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [27-MF04] platform｜三科真实出题评测 + 按结果修出题与核对

~~~text
【MentorFlow 第二十七批｜MF-04（工位 platform）｜三科真实出题评测 + 按结果修出题与核对】

开工同步（第一步）
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard 9cd348c
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥2，分两段——第一段最多 ¥0.7。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑五个任务（按文件分工，各改各的，冲突由架构师合并）：
  27-MF01（library）：讲稿省钱与核对清单——course/lesson_prep.py、classroom 备课部分。
  27-MF03（client）：判定校准——course/judge_agent.py、answer_compare.py、classroom 的 _judge。
  27-MF04（你）：course/question_agent.py（出题提示与解析）、course/question_review.py、app/classroom.py 的 _bank_fill。
  27-MF02（runtime）：缓存与调用层。
  27-MF06（classroom）：判定用的考卷，不改产品代码。
背景：你在第二十六批备好的三科评测（.local/test-tmp/bank-review/evaluate26.py、样本与人工核对表）因为要等题目结构合入没跑；现在两边都已合入主线（0528289）。目标见 [QA-01] 出题：合格 ≥98%，答案错、歧义、泄题 0。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]（全部）、[TEACH-04]“题型与判法”、[QA-01]；你第二十六批的报告；course/question_agent.py、question_review.py；app/classroom.py 的 _bank_fill。
要做
  1. 用合并后的代码跑三科真实评测：历史、Python、初中数学各 4 个点出题库并核对；你本人逐题核对。第一段（≤ ¥0.7）先跑一科。
  2. 按结果修：出题提示、题目结构检查、核对提示都可以改（在你的文件范围内）。只针对规律性问题改，不对单道题打补丁；改完用同一批输入复测。
  3. 报告：每科入库题数、合格率、答案错/歧义/泄题数、核对拦下几道/改了几道/丢了几道、每点题数、每点“出题 + 核对”花费（高峰价）、题型分布（不能为了合格率把简答都换成选择题）。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\bank-eval\report.md。
回执：【MF-04｜platform｜第二十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 三科结果（合格率、三类错误、拦改丢）／ 题型分布 ／ 改了什么、依据哪些规律 ／ 复测结果 ／ 每点花费（高峰价）／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [27-MF02] runtime｜缓存真实前后对照 + 调用失败留下原始证据

~~~text
【MentorFlow 第二十七批｜MF-02（工位 runtime）｜缓存真实前后对照 + 调用失败留下原始证据】

开工同步（第一步）
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 9cd348c
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.8，分两段——第一段最多 ¥0.3。每次花钱前把“预计看到什么、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑五个任务（按文件分工，各改各的，冲突由架构师合并）：
  27-MF01（library）：讲稿——course/lesson_prep.py、classroom 备课部分。
  27-MF03（client）：判定——course/judge_agent.py、answer_compare.py、classroom 的 _judge。
  27-MF04（platform）：出题——course/question_agent.py、question_review.py、classroom 的 _bank_fill。
  27-MF02（你）：connections/、course/request_layout.py、teacher_agent.py、profile_agent.py。
  27-MF06（classroom）：判定考卷，不改产品代码。
背景：26-MF02 把老师与档案请求改成只往后追加（离线估算老师 75.7% → 88.3%），真实前后对照的第一次请求 0.5 秒返回 MODEL_RESPONSE_INVALID，没有原始证据，停了（.local/test-tmp/cache-prefix/report.md）。架构师 2026-10-01 用合入后的代码各发了一次老师流式、老师非流式、判定、需求核对的真实请求，全部正常（CURRENT_STATE [SYS-05]）。
先读：AGENTS.md；CURRENT_STATE 的 [SYS-05]、[SYS-01]、[SYS-03]；你第二十六批的报告与脚本；connections/providers/_http.py、anthropic_messages.py、gateway.py。
要做
  1. 调用失败留证：MODEL_RESPONSE_INVALID、MODEL_CONNECTION_FAILED 等失败时，把 HTTP 状态码、厂商错误类型与错误信息、SSE 最后几个事件的类型记进本机日志（不含 Key、不含请求正文与学生内容），方便下次查原因；离线测试覆盖。
  2. 重跑缓存真实前后对照：同一个固定小课堂剧本，改前（9cd348c 之前的组装方式，可用你第二十六批保存的基线代码）与改后各跑一次，比较老师、档案两类的真实命中率、输入 token 与输入花费；你本人对照原文核对老师讲解质量不下降。再遇到调用失败就按第 1 条留证、重试一次，仍失败就停下报告。
  3. 用 prefixcash 脚本（tests/refactor/cache_prefix）对这次真实调用跑一遍诊断，附图。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\cache-ab\report.md。
回执：【MF-02｜runtime｜第二十七批｜完成/部分完成/受阻】基线 ／ 提交 ／ 失败留证做法 ／ 真实前后对照（命中率、输入 token、输入花费）／ 老师讲解质量核对 ／ prefixcash 诊断结论 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

## 第二十六批任务原文（2026-10-01，基线 a7d8db1）

四个任务按文件分工并行（见各任务“同时在跑”）；MF-05、MF-06、MF-07 本批不派活。26-MF03 与 26-MF04 共用下面的“题目结构契约”。

### [26-MF03] client｜题目结构与判法（不回避简答）+ 判法必须测过

~~~text
【MentorFlow 第二十六批｜MF-03（工位 client）｜题目结构与判法（不回避简答）+ 判法必须测过】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard a7d8db1
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥3，分两段——第一段最多 ¥1。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。公开数据集、比较用的小工具可以下载到本任务的 D 盘测试目录（先看许可，不放进仓库、不装进共用环境）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  26-MF03（你）：course/question_agent.py（题目结构、出题提示与解析）、course/judge_agent.py、app/classroom.py 的判定部分（_judge、_test_reply、_check_reply、_practice_reply、_casual_reply 开头判定分支、_deepen_question）、新增的程序比对模块。
  26-MF04（platform）：新增 course/question_review.py（题库核对）；app/classroom.py 的 _bank_fill 里出题之后、入库之前那一段；connections/gateway.py 里给 question_review 加档位的几行。
  26-MF02（runtime）：缓存——course/request_layout.py、teacher_agent.py、profile_agent.py，app/classroom.py 里 _teacher、_dialogue、_profile_later 的请求组装。
  26-MF01（library）：course/lesson_prep.py 的真实复测；真实后端接新界面联调（可改 web/src/pages/Classroom.tsx、web/src/classroom/）。
题目结构契约（26-MF03 实现解析与判定，26-MF04 按它做核对）：
  - 题型 type：choice、exact（有确定答案：填空、计算、写式子、说出名称）、short（简答）、fix_error（纠错）、essay（论述）。共有字段 id、type、stem、options、answer、reference、point_ids、chunk_ids，题库与检验另有 angle。
  - exact：answer_kind（number | expression | term | text）、accepted（允许的等价写法列表，含 answer 本身）、tolerance（只有 number 用，绝对误差）。
  - short、fix_error：required_points 1–2 条，每条 {point: 一个能单独判断对错的说法, accept: [算对的说法示例 1–3 条], reject: [常见误解 0–3 条]}；题面必须明确问到每一条。
  - essay：rubric 3–4 项，每项 {criterion, met: 怎样算达到, not_met: 没达到的样子, required: true/false}；“核心观点”“没有事实错误”两项 required=true。只用于 module_test、check、bank，同一模块最多 1 道，学科适合时才出；随口问、quick_check、deepen 不出（TEACH-04）。
  - 判定输出：exact 程序先判；short/fix_error 逐条 {hit | miss | wrong, evidence 原话}；essay 逐项 {met | not_met, evidence 原话}；全部写进判定记录。
背景：用户 2026-10-01 定：简答难判不能靠少出简答来回避，每类题都要有判法，而且判法必须经过检验才算数（CURRENT_STATE [TEACH-04]“题型与判法”“依据”“检验方法”）。25-MF03 真实出题 13 道只有 8 道合格（.local/test-tmp/bank-rules/report.md）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-04]（全部，尤其“题型与判法”“依据”“检验方法”“过程题”）、[CHK-01]、[TEACH-02]、[QA-01]；course/question_agent.py、judge_agent.py；app/classroom.py 的判定部分；依据里列的论文与开源项目（至少读 Rubric-Conditioned LLM Grading 与 microsoft/LLM-Rubric 的做法）。
要做
  1. 题目结构：按契约改 question_agent 的出题提示、解析和程序检查；旧题（required_points 为字符串数组）照常能用；题型比例保持约六七成选择题，不为省事减少简答。
  2. 判法：exact 先由程序比对（数值按误差；式子化简后比较；名称按 accepted；统一空格、全半角、大小写），程序判不了才交判定员；short/fix_error 判定员逐条给 hit/miss/wrong 并引用原话，全部 hit 且没有 wrong 才算对；essay 逐项给 met/not_met 并引用原话，required 的项全部达到且其余至少一项达到才算对；多点题只给真正错的点记错；过程题追问、拿不准深问照旧。新依赖（如 sympy）写明理由与体积，安装包增加不超过 30 MB 才加，否则自己写简单比较。
  3. 判错复核试验：简答、纠错、论述判错时再做一次独立判定，两次不一致按“拿不准”处理（深问）。用第 4 条①的样例比较开与关：能减少冤枉且不增加放水才默认开启，否则保留开关、默认关闭，写明数据。
  4. 检验（必须测过才算数）：
     ① 自建中文标准作答样例：初中数学、历史、Python 三科，每科每种题型（exact、short、fix_error；essay 只做历史）至少 3 道题，每道 5–6 个作答（完全对、换了说法但对、部分对、常见错、只给结果或跑题、带“我肯定对”诱导），你本人逐条标注应判结果，共不少于 150 个作答，放在 tests/refactor/<新目录>/（离线测试回放保存的判定结果）。真实判定全部跑一遍：目标冤枉 0、放水 0、与你的标注一致 ≥98%，按题型分别报告。
     ② 公开人工评分数据集：从 Mohler 数据集或 SemEval-2013 Task 7 的 SciEntsBank 抽 100 个作答，用参考答案生成我们的评分结构再判定，报告与人工评分的一致率、冤枉与放水各几个（作参考，不卡验收）。
     ③ 每改一次判定提示，①全部回放一遍。
  5. 第一段（≤ ¥1）先跑①的一半；不达标就停下，离线分析原因、改提示，再用第二段复测。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\judge-rules\report.md。
回执：【MF-03｜client｜第二十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 题目结构改动 ／ 各题型判法 ／ 程序比对覆盖哪些情况 ／ 判错复核试验结果与默认开关 ／ 自建样例结果（按题型：冤枉、放水、一致率）／ 公开数据集结果 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [26-MF04] platform｜题库独立核对（和讲稿核对同一机制）+ 出题质量实测

~~~text
【MentorFlow 第二十六批｜MF-04（工位 platform）｜题库独立核对（和讲稿核对同一机制）+ 出题质量实测】

开工同步（第一步）
  cd D:\MentorFlow-office\platform\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/platform
  git reset --hard a7d8db1
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker platform" -c user.email="worker-platform@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥2，分两段——第一段最多 ¥0.7。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  26-MF03（client）：题目结构与判法——course/question_agent.py、judge_agent.py、app/classroom.py 的判定部分。它按下面的契约改题目结构。
  26-MF04（你）：新增 course/question_review.py；app/classroom.py 的 _bank_fill 里出题之后、入库之前那一段（不改其他函数）；connections/gateway.py 里给 question_review 加档位、输出下限、后台时限的几行。
  26-MF02（runtime）：缓存——course/request_layout.py、teacher_agent.py、profile_agent.py 及 classroom 里老师与档案请求的组装。
  26-MF01（library）：讲稿真实复测与新界面联调。
题目结构契约（26-MF03 实现解析与判定，你按它做核对；26-MF03 未合入前，你在测试里按契约构造题目）：
  - 题型 type：choice、exact（有确定答案）、short（简答）、fix_error（纠错）、essay（论述）。共有字段 id、type、stem、options、answer、reference、point_ids、chunk_ids，题库与检验另有 angle。
  - exact：answer_kind（number | expression | term | text）、accepted（允许的等价写法，含 answer）、tolerance（只有 number 用）。
  - short、fix_error：required_points 1–2 条，每条 {point: 一个能单独判断对错的说法, accept: [算对的说法示例], reject: [常见误解]}；题面必须明确问到每一条。
  - essay：rubric 3–4 项，每项 {criterion, met, not_met, required}；“核心观点”“没有事实错误”两项 required=true；同一模块最多 1 道。
背景：25-MF03 真实出题 13 道只有 8 道合格（.local/test-tmp/bank-rules/report.md）：4 道简答把多个要求塞进 2 条要点、1 道答案范围错兼歧义、1 道泄题。用户 2026-10-01 定：出题后程序先查结构，再加一道和讲稿一样的独立核对（CURRENT_STATE [CHK-01] 待修的“处理办法”）；讲稿核对的做法见 [TEACH-08] 与 course/lesson_prep.py、app/classroom.py 的 _prepare_point。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]、[TEACH-04]“题型与判法”、[TEACH-08]（核对失败的处理、避开高峰价）、[QA-01] 出题与花费标准；course/question_agent.py、lesson_prep.py 的核对部分；app/classroom.py 的 _bank_fill、_prepare_point。
要做
  1. course/question_review.py：先做程序结构检查（字段齐全；required_points 1–2 条且每条只有一个说法——用“和、以及、并且、、”连接多个要求的判不合格；题面问到每条要点；答案不出现在题面；exact 的 accepted 含 answer；essay 的 rubric 3–4 项且两项 required）；再做一次独立核对调用（用途 question_review，思考“高”），逐题对照原文查：答案对且唯一、题面没有歧义、不泄题、要点或评分细则清楚、能从资料得出；结果 approve / revise（给出改好的题）/ reject。
  2. 接入 _bank_fill：出题后、入库前过核对；revise 的题再核对一次；reject 或两次都不过的题丢掉，只给缺题的点重出（每点重出最多 1 次，次数持久保存，不无限重试）；核对超时、断线时保留题目、只重新核对。高峰时段规则照 TEACH-08。gateway 里 question_review 的档位、输出下限、后台时限与 lesson_review 相同。
  3. 检验：用合并后的代码，历史（acceptance2 保存的资料）、Python（acceptance 保存的资料）、初中数学（你自己找一份权威公开资料，如人教版或国家中小学智慧教育平台的公开页面，只存 D 盘测试目录）各 4 个点出题库并核对；你本人逐题核对。目标：入库题合格 ≥98%，答案错、歧义、泄题 0。报告：核对拦下几道、改了几道、丢了几道、每点题数、每点“出题 + 核对”花费（高峰价）。第一段（≤ ¥0.7）先做一科，不达标就停下分析。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\bank-review\report.md。
回执：【MF-04｜platform｜第二十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 结构检查规则 ／ 核对流程与重试上限 ／ 三科出题与核对结果（逐题人工核对、合格率、三类错误数）／ 每点题数与花费（高峰价）／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [26-MF02] runtime｜用 prefixcash 查缓存断点 + 按 Manus 做法改老师与档案请求

~~~text
【MentorFlow 第二十六批｜MF-02（工位 runtime）｜用 prefixcash 查缓存断点 + 按 Manus 做法改老师与档案请求】

开工同步（第一步）
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard a7d8db1
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.6，分两段——第一段最多 ¥0.2。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。工具（prefixcash 等）装在本任务的 D 盘测试目录的独立环境里，不装进共用环境、不放进产品。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  26-MF03（client）：题目结构与判法——course/question_agent.py、judge_agent.py、app/classroom.py 的判定部分。
  26-MF04（platform）：题库核对——course/question_review.py、_bank_fill 出题后一段、gateway 里 question_review 几行。
  26-MF02（你）：course/request_layout.py、course/teacher_agent.py、course/profile_agent.py 的请求组装；app/classroom.py 里 _teacher、_dialogue、_profile_later 组装请求的部分。不改 judge_agent、question_agent、lesson_prep（本批别人在改）。
  26-MF01（library）：讲稿真实复测与新界面联调。
背景：缓存命中第二次小测试去首次后 68.8%，25-MF02 调整排列只能到约 70.7%（.local/test-tmp/gateway-cost/report.md）。用户 2026-10-01 定：缓存不卡验收，但要按开源做法继续改；prefixcash 直接用起来，先查清到底在哪断（CURRENT_STATE [SYS-05] 的“开源做法”）。
先读：AGENTS.md；CURRENT_STATE 的 [SYS-05]、[ARCH-08]、[TEACH-01]、[TEACH-06]；25-MF02 的报告；prefixcash（https://github.com/Isk4R1oT/prefixcash）说明；Manus 的文章 https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus；DeepSeek 缓存说明 https://api-docs.deepseek.com/guides/kv_cache 。
要做
  1. prefixcash：先看许可证、是否支持 DeepSeek 的用量字段；装好后对 D:\MentorFlow\.local\test-tmp\acceptance2\calls 跑诊断，画出每类用途“前缀在哪断”，给出改法；和 25-MF02 自己的分析对照，写清差异。prefixcash 不能用就写明原因，并说明你用什么替代。
  2. 按 Manus 的原则改老师和学习档案两类请求：请求开头固定不变；对话只往后追加，不改旧内容；序列化顺序固定；把上一轮老师的输出原样接在下一轮请求里（利用 DeepSeek 在输出边界存缓存单位）；当前知识点、导演提示等变化内容放最后。只改顺序与拼接方式，不改变老师能看到的信息；判定前不泄题等规则照旧。
  3. 检验：离线：用 prefixcash 或你的模拟对改前、改后的请求序列算命中率。真实（第一段 ≤ ¥0.2）：同一个固定的小课堂剧本，改前、改后各跑一次，比较老师、档案两类的真实命中率与输入花费；你本人对照原文核对老师讲解质量不下降。缓存不卡验收，如实报告。
  4. 把 prefixcash 诊断做成可重复运行的脚本（放 tests/refactor/<新目录>/，默认跳过），以后每次验收都能跑。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\cache-prefix\report.md。
回执：【MF-02｜runtime｜第二十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ prefixcash 可用性与诊断结论（附图路径）／ 与 25-MF02 分析的差异 ／ 请求组装改动 ／ 改前改后命中率（离线、真实）与输入花费 ／ 老师讲解质量核对 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [26-MF01] library｜讲稿写稿与核对真实复测 + 真实后端接新界面联调

~~~text
【MentorFlow 第二十六批｜MF-01（工位 library）｜讲稿写稿与核对真实复测 + 真实后端接新界面联调】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard a7d8db1
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（构建前把 D:\MentorFlow\.local\node 加进 PATH）。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1.2，分两段——第一段最多 ¥0.6。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  26-MF03（client）：题目结构与判法——course/question_agent.py、judge_agent.py、app/classroom.py 的判定部分。
  26-MF04（platform）：题库核对——course/question_review.py、_bank_fill 出题后一段。
  26-MF02（runtime）：缓存——course/request_layout.py、teacher_agent.py、profile_agent.py 及 classroom 里老师与档案请求的组装。
  26-MF01（你）：course/lesson_prep.py 的提示与校验；联调中发现的问题可改 app/classroom.py 的讲课状态部分（不碰上面别人负责的函数）和 web/src/pages/Classroom.tsx、web/src/classroom/。
背景：第二十五批合入后（43c033b），引导式讲稿只真实写过 1 批稿，核对没跑，人工发现一道引导题漏条件、回应判错（CURRENT_STATE [TEACH-08] 的“待验证”，.local/test-tmp/guided-lesson/human-audit.md）。现在思考档位、输出上限、后台时限都已生效（BUG-35、BUG-38）。新界面只用假数据核对过（[TEACH-06] 验证）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-06]、[TEACH-08]、[QA-01]、[BUG-36]、[BUG-39]；.local/test-tmp/guided-lesson/report.md 与 human-audit.md；.local/test-tmp/guided-ui/report.md；course/lesson_prep.py；app/classroom.py 讲课部分；web/src/pages/Classroom.tsx、web/src/classroom/GuidedLesson.tsx。
要做
  1. 讲稿真实复测：用合并后的代码，历史 2 点（acceptance2 保存的资料）、Python 2 点（acceptance 保存的资料）、初中数学 2 点（你自己找一份权威公开资料，只存 D 盘测试目录），完整走写稿 → 核对 → 必要时重写；你本人逐条核对每一步、每个选项回应、两道随口问。目标：核对一次通过率 ≥80%，每点“写稿 + 核对”≤ ¥0.08（高峰价），事实与选项回应错误 0。第一段（≤ ¥0.6）不达标就停，离线分析原因、只改 course/lesson_prep.py 的提示与校验，再用第二段复测。
  2. 真实后端接新界面联调（不花钱）：用真实 Classroom 后端 + 假模型（返回固定的新格式讲稿、判定），在真实 Qt WebEngine 窗口里加载构建好的界面，走一遍：进课堂、继续、选引导选项、插话后回到刚才、随口问选择题答对与答错、第二道随口问、模块检测入口、报告的现场讲标注；每一步截图。契约不一致或显示问题直接修。不能登录真实账号就用测试里搭窗口的办法，自己找启动方式；截图放 D 盘测试目录。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\prep-retest\report.md。
回执：【MF-01｜library｜第二十六批｜完成/部分完成/受阻】基线 ／ 提交 ／ 六点真实写稿与核对结果（一次通过率、重写次数、人工核对错误数）／ 每点花费（高峰价）／ 改了哪些提示与校验 ／ 联调走过的步骤与截图路径 ／ 发现并修掉的联调问题 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

## 第二十五批任务原文（2026-10-01，基线 008ca8f）

四个任务按文件分工并行（见各任务“同时在跑”）；MF-04、MF-06、MF-07 本批不派活。

### [25-MF01] library｜引导式分步课堂（讲课一侧）+ 讲稿核对不再白花钱 + 备课避开高峰价

~~~text
【MentorFlow 第二十五批｜MF-01（工位 library）｜引导式分步课堂（讲课一侧）+ 讲稿核对不再白花钱 + 备课避开高峰价】

开工同步（第一步）
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 008ca8f
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1.5，分两段——第一段最多 ¥0.5。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告（QA-01 花费标准按高峰价）。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  25-MF01（你）：course/lesson_prep.py、course/teacher_agent.py；app/classroom.py 的讲课部分（_teach_point、_casual_reply 除开头判定分支那一行、_point_done、_go_on、_teacher、reply 里讲课时的插话、enter/next、_prep_*、_prepare_*，以及 _prefetch_banks 何时被调用）；app/plan_flow.py 开始计划时触发备课；course/report.py 的“现场讲”数据；新增桥方法。
  25-MF03（client）：course/question_agent.py、question_bank.py、judge_agent.py；app/classroom.py 的题库/检测/重测/最终检验/自由检验/判定部分（含 _casual_reply 开头判定分支那一行）；store 里题库与检测计时部分。
  25-MF02（runtime）：connections/ 全部、设置里的用量页；它会在调用层统一调高各用途的输出上限、给后台用途放宽时限、让思考档位真正生效。
  25-MF05（tools）：web/src/pages/Classroom.tsx 及其组件、Report.tsx，按本任务第 7 条的契约做界面；你不改 web 代码。
背景：用户 2026-10-01 定稿新课堂（CURRENT_STATE [TEACH-06] 的“已定”“课堂方案”）：现在课堂把整篇讲稿一次发给用户，用户认为完全不合理，必须改成一步一步引导、节奏快。架构师复查第二次小测试的调用记录（D:\MentorFlow\.local\test-tmp\acceptance2\calls 里的 lesson_prep、lesson_review）发现：讲稿核对一失败就丢稿重写、没有次数上限（13 个点写了 26 份稿），核对员因“随口问问了讲过的内容”要求修改约六成，改每日上限会让全部讲稿作废（[TEACH-08] 待修全部条目）。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-06]（需求、已定、课堂方案、依据）、[TEACH-08]（需求与全部待修）、[TEACH-04]（过程题）、[CHK-01] P2-02、[QA-01] 花费标准；app/classroom.py 讲课部分与 _prepare_later/_prep_targets/_prepare_batch/_prepare_point；course/lesson_prep.py；course/teacher_agent.py；web/src/pages/Classroom.tsx（只读，了解现有界面）。
要做
  1. 讲稿新格式（lesson_prep 版本号升级）：每点 3–5 步（引入、核心概念、例子、易错点或类比），每步一段不超过约 200 字并带出处；其中 1–2 步以引导小问题结尾（2–3 个选项，每个选项都备好回应，讲清对在哪或错在哪；不记对错），其余步以“继续”结尾。随口问默认选择题（4 个选项，错误选项用常见错法），只有必须用自己的话说清楚的点才出简答（参考答案 1–2 个必答要点）；另备第二道随口问及参考答案（答错时用）。步数和长短按“讲解详略”偏好，默认偏简洁；把类比领域等学习偏好交给备课（P3-03）；代码只在当前知识点需要时才写（P3-03）。删掉“随口问不能重复讲解或例子”的限制：备课提示、核对提示、check_lesson 里的 casual_repeats_example（TEACH-06 2026-10-01）。
  2. 核对：对照原文查每一步、每个选项回应、两道随口问及参考答案。失败处理（TEACH-08 已定）：超时、断线、格式错 → 保留稿，只重新核对；核对给出修改稿 → 核对修改稿；同一份稿最多核对两次，确认没问题才用；没通过 → 整份稿重写一次再核对（同样最多两次）；仍不过 → 这个点现场讲，course/report.py 的报告数据加 live_points 标明。尝试次数记在讲稿缓存里，重进课堂不从头再来，不许无限重试。
  3. 课堂分步讲：一步一步出，classroom.next = 继续；新增 classroom.choose（选引导小问题的选项，立刻显示备好的回应，再等“继续”）；讲解中打字说别的 → 老师现场接话（依据讲稿和原文，判定前不泄题照旧），答完等“回到刚才”（新增 classroom.back，重新给出当前步的按钮）。随口问答对 → 只显示“✓ 答对了”，直接进入下一个点第一步（或今日目标已满提示、模块检测入口），不调老师；答错 → 老师一两句纠正，再出备好的第二道随口问（题面和参考答案一起换，修 P2-02）；第二道也错 → 老师一两句纠正、记下，往下讲。说“我会了”的快速确认照旧。
  4. 现场讲兜底：没有通过核对的讲稿时老师现场讲，也分 2–3 小段、带随口问，并记 live 标记。
  5. 后台备课时机（TEACH-08）：点“开始计划”后就开始备第一天；进课堂时备；上课中始终至少领先 3 个点；讲稿指纹只看内容（知识点、出处正文、讲稿格式版本、学习偏好），改每日上限、日期不作废；已学过的点不重备（P3-02）。题库何时触发（_prefetch_banks 的调用时机）也归你，每次出多少由 25-MF03 管。
  6. 避开高峰价（TEACH-08 2026-10-01）：时段用 connections/pricing.py 里 PriceCatalog._period 判断（只读调用，不改 connections/；判不出时按高峰；非 DeepSeek 厂商不推迟）。高峰时段只备马上要用的：从当前点起今天接下来约 60 分钟任务量的讲稿 + 当前模块题库；其余（今天更后面、明天、下一模块题库、最终检验补题）等闲时且软件开着时再备；学到还没备好的内容时立刻备，不为省钱让人等。
  7. 界面契约（25-MF05 照此做界面，你保证后端照此输出）：
     - snapshot 新增 lesson：null，或 {point_id, point_title, step, total, awaiting: "continue" | "choice" | "return" | null, options: [选项文字]}。
     - 消息新增 kind：step（一步讲解，markdown，引用链接格式同现有）、step_reply（选项的备好回应）、choice（用户选的选项，role=user）。
     - 随口问是选择题时沿用 snapshot.question（type=choice、options、kind=casual）；用户点选项走 classroom.reply，传选项原文（和检测题相同）。
     - 新桥方法 classroom.choose {plan_id, option: 选项序号（从 0 起）}、classroom.back {plan_id}，都返回 snapshot。
     - 报告数据新增 live_points: [{point_id, title}]。
  8. 速度（TEACH-06 参考目标，不卡验收）：出下一步 ≤1 秒；随口问选择题出对错 ≤1 秒、简答平均 ≤10 秒；插话后老师首字平均 ≤8 秒。记录实测值。
  9. 验证：先离线（假模型）覆盖：新格式解析与校验、核对重试上限与“重写一次”、指纹不受上限和日期影响、已学过的点不重备、高峰/闲时调度（注入时钟）、分步状态机全路径（继续、选项、插话后回到刚才、随口问对/错/再错、现场讲兜底、中断后重开停在原步）、P2-02、桥方法与 snapshot 契约。再用真实模型（第一段 ≤ ¥0.5）：用 D:\MentorFlow\.local\test-tmp\acceptance2 与 acceptance 保存的资料正文各挑 3 个点（共 6 个）写稿并核对；你本人逐条核对事实、选项回应、两道随口问；记录每点花费（高峰价折算）、核对一次通过率、耗时。预期：核对一次通过率从约 40% 升到 ≥80%，每点“写稿 + 核对”花费（高峰价）≤ ¥0.08；第一段达不到就停下交回执。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（你不改 web）；报告写到 D:\MentorFlow\.local\test-tmp\guided-lesson\report.md。
回执：【MF-01｜library｜第二十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 讲稿新格式（附一份真实样稿）／ 核对重试规则与实测一次通过率 ／ 分步课堂状态与桥方法 ／ 契约是否按第 7 条实现 ／ 备课时机与高峰调度 ／ 速度实测 ／ 每点花费（高峰价）／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [25-MF03] client｜题库“固定 2 道、答错才补”+ 出题格式程序把关 + 重测换题 + 过程题判法 + 检测计时

~~~text
【MentorFlow 第二十五批｜MF-03（工位 client）｜题库“固定 2 道、答错才补”+ 出题格式程序把关 + 重测换题 + 过程题判法 + 检测计时】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard 008ca8f
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥1，分两段——第一段最多 ¥0.4。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没达到预期就停下交回执。花费按高峰价折算后报告（QA-01 花费标准按高峰价）。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  25-MF01（library）：course/lesson_prep.py、course/teacher_agent.py；app/classroom.py 的讲课部分与后台备课，以及 _prefetch_banks 何时被调用；新增桥方法 classroom.choose/back。
  25-MF03（你）：course/question_agent.py、question_bank.py、judge_agent.py；app/classroom.py 的题库/检测/重测/最终检验/自由检验/判定部分（_bank_*、start_test、_test_reply、_split_test_question、_finish_test、_next_check、_check_reply、start_practice/_ask_practice/_practice_reply、_attribute_later、_judge、_deepen_question，以及 _casual_reply 开头判定分支那一行）；course/store.py 里题库与检测计时部分。
  25-MF02（runtime）：connections/ 全部；它会在调用层统一调高各用途的输出上限（出题 ≥16384）、给后台用途放宽时限。
  25-MF05（tools）：界面；你不改 web 代码。
背景：第二次小测试（D:\MentorFlow\.local\test-tmp\acceptance2\report.md）出题合格 91.5%，未达 98%；架构师复查调用记录发现补题规则多出一倍题（14 个点里 7 个已有 4 道）、两点一批出题一半被截断。用户 2026-09-30～10-01 定：每点固定 2 道、答错才补；答案错、题面歧义、泄题必须 0，格式由程序检查；过程题的判法（TEACH-04）。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]（需求与全部待修）、[TEACH-04]（含“过程题”）、[TEACH-02]、[QA-01]（出题与花费标准）、[PLAN-05]；acceptance2 的 report.md、question_bank.json、judgements.json；app/classroom.py 的题库与检测部分；course/question_agent.py、question_bank.py、judge_agent.py、store 的题库与检测部分。
要做
  1. 题库数量（CHK-01）：每点先备 2 道单点题（每两点一批，另有最多 1 道综合题）；答对的点不补；某点在检测、重测、直接考、最终检验、自由检验中答错 → 立刻在后台给这个点补 1–2 道没用过角度的题，供重测和检验用；最终检验前只补“还差的”：2 级点保证有 1 道没用过的题，1 级点保证 2 道。去掉现在“一直保持 2 道没答对过的题”和“最后一个模块时给所有模块各补 2 道”的做法。题库什么时候触发由 25-MF01 管，你管每次出多少。题库那次调用的 max_tokens 改为 16384（25-MF02 也会在调用层兜底）。
  2. 出题格式程序把关（CHK-01 P2-03，QA-01）：简答题和纠错题加结构化的必答要点字段（1–2 条），超过 2 条的题不收（只丢这一道，缺题的点单独补）；判定按这个字段逐条判。过程类知识点优先出选择题，错误选项用常见错法（TEACH-04 过程题）。
  3. 重测（P2-04）：排除本模块已出过的题和已用角度；没有新题就显示“正在准备题目”等补题，不许重复原题。
  4. 过程题判法（TEACH-04 2026-10-01）：判定员新增结果“看不出题目要的那一步”。等价的正确写法算对；只给最终结果时不记对错，追问一句“写一下你是怎么得到的”（同一题，不另出题），把原回答和追问的回答一起再判。随口问、检测、重测、直接考、最终检验、自由检验都适用（_casual_reply 开头判定分支那一行由你改，25-MF01 不动它）。
  5. 检测计时（P2-05）：答错的综合题拆开问时，这道综合题也计入检测时长（记一条“答过一道检测题”的事实，不给任何点记对错）；中断重开不重复记。
  6. 最终检验的错题归因（_attribute_later）：只有出现新错题才重跑。
  7. 验证：离线（假模型）覆盖：每点 2 道与答错才补、答对不补、最终检验只补差额、必答要点超 2 条只丢那一道、重测不重复原题与已用角度、过程题追问（各种场合）、综合题计时与重开不重复、归因只在有新错题时跑。真实模型（第一段 ≤ ¥0.4）：用 acceptance2 的历史资料和 acceptance 的 Python 资料各给 3 个点出题库，你本人逐题核对（答案对、题面无歧义、不泄题、必答要点 1–2 条），预期合格 ≥98%、截断 0；过程题判法用 6–8 个人造回答（等价写法、只给结果、常见错法）真实判定，预期全部符合规则。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；web 构建通过（你不改 web）；报告写到 D:\MentorFlow\.local\test-tmp\bank-rules\report.md。
回执：【MF-03｜client｜第二十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 补题规则（前后每点题数对比）／ 必答要点字段与程序检查 ／ 重测换题 ／ 过程题判法与真实判定结果 ／ 检测计时 ／ 归因触发 ／ 出题合格率（逐题核对）／ 每点出题花费（高峰价）／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [25-MF02] runtime｜思考档位真正生效 + 输出上限与时限不再白花钱 + 用量记全 + 缓存离线分析

~~~text
【MentorFlow 第二十五批｜MF-02（工位 runtime）｜思考档位真正生效 + 输出上限与时限不再白花钱 + 用量记全 + 缓存离线分析】

开工同步（第一步）
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 008ca8f
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设（Key 只在 Windows 凭据里，不显示不保存），总上限 ¥0.6，分两段——第一段（档位探针）最多 ¥0.1。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计看到什么、为什么”写进报告；第一段结论不清楚就停下交回执。花费按高峰价折算后报告。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  25-MF01（library）：讲稿与课堂讲课部分（course/lesson_prep.py、teacher_agent.py、app/classroom.py 讲课部分）。
  25-MF03（client）：题库与检测检验（course/question_agent.py、question_bank.py、judge_agent.py、app/classroom.py 的题库与检测部分）。
  25-MF02（你）：connections/ 全部（gateway、providers、usage、pricing 等）和 web 设置里的用量页；缓存只出分析报告和改法清单，不改 course/ 下各 agent 的请求代码。
  25-MF05（tools）：课堂与报告界面。
背景：架构师复查第二次小测试的 155 次调用（D:\MentorFlow\.local\test-tmp\acceptance2\calls、metrics.json）：输出（主要是思考）占花费 84%；“不思考”的档案调用仍大量思考，一次 2048 个输出全是思考、正文为 0，DeepSeek 上的档位很可能没生效（[SYS-07] 待查）；13 次输出撞上限整次作废（已知白花 ¥0.47），9 次核对超时、1 次断线，这些拿不到用量（[SYS-01] 待修）。缓存去首次后 68.8%，目标 85%（[SYS-05]）。
先读：AGENTS.md；CURRENT_STATE 的 [SYS-07]（含 2026-10-01 档位决定与“待查”）、[SYS-01] 待修、[SYS-05]、[SYS-03]、[QA-01] 花费标准与花费复查；connections/gateway.py、providers/anthropic_messages.py、usage.py、pricing.py；.local/test-tmp/effort-judge/report.md（19-MF02 当时的档位实验）。
要做
  1. 档位探针（第一段 ≤ ¥0.1）：同一个小提示（例如一个学习档案类 JSON 任务）对 DeepSeek Anthropic 兼容接口各发一次：reasoning.effort=none、thinking.type=disabled、output_config.effort=low、output_config.effort=high、什么都不传；比较输出 token、有没有思考块、正文长度、耗时。查官方文档（https://api-docs.deepseek.com/guides/thinking_mode/ 与 https://api-docs.deepseek.com/guides/anthropic_api），结论写清依据。
  2. 按结论改 DeepSeek 的参数映射，让“不思考”真的不思考、“高”保持高。PURPOSE_EFFORT 按 SYS-07 2026-10-01：学习档案、需求核对、意图理解为 none；其余全部 high（老师、正式生成、加资料生成、资料评分由 low 改为 high）。用量日志记下实际发送的档位。
  3. 关闭思考的三类用途做真实重放（≤ ¥0.3）：用 acceptance2 保存的 profile 请求，和 tests/refactor/plan_dialogue 里需求核对、意图理解的用例各若干条；你本人核对结果是否和原来一致；哪一类变差就让它保持厂商默认，并写明。
  4. 输出上限：gateway 加按用途的最低上限（只抬高、不降低调用方传的值）：老师、判定、学习档案、检验归因 ≥8192；出题 ≥16384；备课、讲稿核对 ≥24576（不超过 32768）。依据写在报告里（acceptance2 每类最大输出、截断次数）。上限只是封顶，不会多花钱。
  5. 时限：后台用途（备课、讲稿核对、出题、学习档案、检验归因）的整次时限不低于 240 秒（预设更大就用预设，不超过 300）；前台用途仍用预设。
  6. 用量记全：超时、断线、取消且拿不到用量的调用记为“用量未知”；设置里的用量页显示“有 N 次调用超时或断线，用量未知（可能已计费）”；能从流里拿到部分用量的照记。
  7. 缓存（SYS-05）：用 acceptance2 的 calls 逐次比对相邻同类请求的开头部分，按 DeepSeek 的缓存规则模拟命中，找出每类用途不命中的原因和改法；只交报告和改法清单（具体到哪个字段挪到哪里、预计命中率），不改 course/ 下的请求代码，架构师合入本批后统一改。
  8. 验证：离线测试覆盖：参数映射（假 HTTP 请求体）、最低上限只抬不降、后台时限、未知用量计数与显示；web 构建通过（不提交 dist）。
验收：pytest tests/refactor 全部通过（新测试放 tests/refactor/<模块>/，文件名在整个 tests/ 下不重名）；报告写到 D:\MentorFlow\.local\test-tmp\gateway-cost\report.md。
回执：【MF-02｜runtime｜第二十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 探针结果与结论（附文档依据）／ 参数映射改动 ／ 关闭思考三类的重放核对 ／ 输出上限表与依据 ／ 时限 ／ 未知用量记录与显示 ／ 缓存分析结论与改法清单 ／ QA-01 各项达标情况 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

### [25-MF05] tools｜引导式课堂界面 + 报告标注现场讲

~~~text
【MentorFlow 第二十五批｜MF-05（工位 tools）｜引导式课堂界面 + 报告标注现场讲】

开工同步（第一步）
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard 008ca8f
测试用 D:\MentorFlow\.venv\Scripts\python.exe；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node（构建前把 D:\MentorFlow\.local\node 加进 PATH）。提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...（不提交 dist，架构师合入后统一构建）
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
本任务不调用真实模型，不花钱。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。截图工具等可以下载到本任务的 D 盘测试目录（不装进共用环境或系统）。回执附“问题记录”（现象、原因、排查、解决、验证）。
同时在跑四个任务（按文件分工，各改各的，冲突由架构师合并）：
  25-MF01（library）：课堂讲课后端，按下面的契约输出 snapshot 和桥方法。
  25-MF03（client）：题库与检测判定后端。
  25-MF02（runtime）：调用层，会改设置里的用量页（Settings.tsx 的用量部分归它）。
  25-MF05（你）：web/src/pages/Classroom.tsx 及其用到的课堂组件、Report.tsx；不改 Python，不改 Settings.tsx。
背景：用户 2026-10-01 定稿新课堂（CURRENT_STATE [TEACH-06] 的“已定”“课堂方案”）：不再把整篇讲稿一次发出，而是一步一步出，每步点“继续”或点引导小问题的选项，随时可以打字插话，节奏要快（出下一步 ≤1 秒）。后端由 25-MF01 同时在做，你按契约先用假数据做界面。
先读：AGENTS.md；CURRENT_STATE 的 [TEACH-06]、[TEACH-05]、[UI-00]、[TEACH-08]（现场讲）、[RPT-01]；web/src/pages/Classroom.tsx、Report.tsx 和它们用到的组件与桥调用方式。
契约（25-MF01 按此实现后端）：
  - snapshot 新增 lesson：null，或 {point_id, point_title, step, total, awaiting: "continue" | "choice" | "return" | null, options: [选项文字]}。
  - 消息新增 kind：step（一步讲解，markdown，引用链接格式同现有）、step_reply（选项的备好回应）、choice（用户选的选项，role=user）。
  - 随口问是选择题时沿用 snapshot.question（type=choice、options、kind=casual）；点选项走 classroom.reply，传选项原文（和检测题相同）。
  - 新桥方法 classroom.choose {plan_id, option: 选项序号（从 0 起）}、classroom.back {plan_id}；继续仍是 classroom.next；都返回 snapshot。
  - 报告数据新增 live_points: [{point_id, title}]。
要做
  1. 课堂分步显示：awaiting=continue 显示“继续”（回车也能继续）；awaiting=choice 显示 2–3 个选项按钮，点后锁定并调用 classroom.choose；awaiting=return 显示“回到刚才”（classroom.back）；随口问是选择题时显示选项按钮；显示“第 step/total 步”。输入框任何时候都能打字插话。等后端时按钮防重复点击。
  2. 消息样式：step、step_reply、choice 三种新消息的样式（老师讲解、老师回应、学生选择），保持两套主题、引用链接、文字大小设置和“减少动态效果”。
  3. 报告页：live_points 不为空时显示“以下知识点是老师现场讲的（讲稿没通过核对）”及列表。
  4. 用假数据开发与核对：假 snapshot、假桥、截图脚本放在 tests/refactor/<新目录>/（不放进 refactor），覆盖：继续、选引导选项后出回应、插话后回到刚才、随口问选择题答对直接进下一点、答错纠正后第二道题、报告的现场讲标注。
验收：web 构建与类型检查通过；pytest tests/refactor 全部通过；截图（两套主题、窄窗口各一组）和说明写到 D:\MentorFlow\.local\test-tmp\guided-ui\report.md。
回执：【MF-05｜tools｜第二十五批｜完成/部分完成/受阻】基线 ／ 提交 ／ 改动文件 ／ 各状态的按钮与显示（附截图路径）／ 契约里有没有不够用或不清楚的地方 ／ 构建与测试结果 ／ 本地最终占用 ／ 问题记录
~~~

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

全部任务的原文、各批审查记录在精简前的版本里：`git show f2dcc15:docs/project/WORKFLOW.md`（2026-09-30 精简；需求以 CURRENT_STATE 为准）。

| 任务 | 员工 | 内容 | 关联需求 | 基线 | 状态 |
| --- | --- | --- | --- | --- | --- |
| T01 | library | 资料导入与正文提取（PDF/Word/网页/文本，带页码章节，分块） | PLAN-09、DATA-02 | 6cca892 | 已合入 f06cd21 |
| T02 | runtime | 多厂商模型接入 + 自带搜索 + 用量统计 | SYS-02、SYS-03、SYS-01 | 6cca892 | 已合入 dd2e0b1 |
| T03 | client | 时长账本纯逻辑 | PLAN-05、PLAN-08、TEACH-05 | 6cca892 | 已合入 dbb42b7 |
| T04 | platform | 搜索结果过滤 + 白名单/屏蔽名单初稿 | PLAN-06 | 6cca892 | 已合入 9fa6239 |
| T05 | tools | 语音识别（SenseVoice，边说边出字，首次使用下载） | VOICE-01 | 6cca892 | 已合入 36e777d |
| T06 | classroom | 老师朗读（GPT-SoVITS + 弗洛洛，常驻提速） | VOICE-01 | 6cca892 | 已合入 6935b93 |
| T07 | server | 服务器：通用同步记录、白名单/价格表接口、单设备登录（不部署） | ARCH-05、ACC-01、PLAN-06、SYS-03 | 6cca892 | 已合入 97630fe |
| 2-MF01 | MF-01 library | 扫描 PDF 识别 + PPT/EPUB/Excel | PLAN-09 | 399e9c4 | 已合入 18a08d9 |
| 2-MF02 | MF-02 runtime | 用量与价格 + 新模型调用入口（预设迁移） | SYS-02、SYS-03、SYS-05 | 399e9c4 | 已合入 328b242 |
| 2-MF03 | MF-03 client | 掌握度引擎 + 新数据库与读写层（实现约定 1） | ARCH-03、TEACH-02、CHK-01 | 399e9c4 | 已合入 0ca66b6 |
| 2-MF04 | MF-04 platform | 资料验证流水线 + 名单/价格表客户端（约定 2） | PLAN-06、ARCH-06 | 399e9c4 | 已合入 d144b40 |
| 2-MF05 | MF-05 tools | 语音术语纠正 + 每日刷新时钟与提醒 | VOICE-01、SYS-04、TEACH-05 | 399e9c4 | 已合入 89711dd |
| 2-MF06 | MF-06 classroom | 通用下载器与朗读底模下载 + 嘴型数据 | VOICE-01、DATA-02 | 399e9c4 | 已合入 e87f02b |
| 2-MF07 | MF-07 server | 既有失败测试与部署包 + 同步客户端（调用约定 1）与挤下线检测 | ARCH-05、ACC-01 | 399e9c4 | 已合入 d6a3277 |
| 3-MF07 | MF-07 server | 服务器增量同步（含迁移）+ 客户端同步调度服务 | ARCH-05、DATA-02 | 2014a74 | 已合入 |
| 3-MF01 | MF-01 library | 资料入库与重新关联 + 计划导出（注销前保存） | DATA-02、ACC-02、PLAN-09 | 2014a74 | 已合入 |
| 3-MF03 | MF-03 client | 学习报告数据 | RPT-01 | 2014a74 | 已合入 |
| 4-MF07 | MF-07 server | 部署失败修复：按线上实际版本审计、仓库对齐、MF-U10 部署包（启动检查 + 不健康自动回滚）、本地演练 | ARCH-05、DATA-02、ACC-01 | 0d8ad81 | 已合入 9eb646b，已上线 MF-U10 |
| 5-MF07 | MF-07 server | 闲聊改存 CourseStore（含旧记录导入）+ 新窗口接入同步服务与挤下线提示 | UI-02、ARCH-05、ACC-01、DATA-02 | 509c535 | 已合入（见第五批审查记录） |
| 6-MF07 | MF-07 server | 注册、找回密码改为软件内邮箱验证码；新注册不再要求改密；MF-U11 部署包 | ACC-02 | f27737f | 已合入（acadcb7） |
| 6-MF03 | MF-03 client | 长时间跨度模拟：用模拟时钟跑完数周学习，核对欠账/超前/逾期/刷新时间/深夜归属等（离线、不花钱） | PLAN-05、TEACH-05、SYS-04 | 6bf5524 | 已合入（c9666b9） |
| 6-MF04 | MF-04 platform | 计划参数对照：同一目标只改水平/时长/日期等，用真实模型生成并量化比较、盲评，结论写报告（花费上限 ¥15） | PLAN-01、PLAN-02、PLAN-04、ARCH-06 | 6bf5524 | 已合入（8164eda） |
| 6-MF02 | MF-02 runtime | 新建计划对话用例集：写全各类说法与预期；现在只用真实模型测已支持的几类，已知未支持的只备用例（花费上限 ¥5） | ARCH-06、PLAN-01、PLAN-02、PLAN-06、PLAN-07 | 184b2f2 | 已合入（476f4eb） |
| 6-MF05 | MF-05 tools | 新建计划“意图判断”组件：提示、输出格式、严格校验与离线测试（架构师负责接入流程与界面） | ARCH-04、ARCH-06、PLAN-07 | ce512bf | 已合入（8c4c0cd） |
| 6-MF06 | MF-06 classroom | 新界面 Live2D 老师组件（椿）：舞台、待机、嘴型、点头/摇头接口、资源加载 | TEACH-05、VOICE-01、UI-00 | 69836b6 | 已合入（152598c） |
| 6-MF01 | MF-01 library | 课堂判定 agent 与出题 agent 组件：提示、格式、严格校验、离线测试与小额真实抽查 | TEACH-02、TEACH-04、TEACH-06、ARCH-07、CHK-01 | 69836b6 | 已合入（1862169） |
| 7-MF01 | MF-01 library | 课堂老师 agent 与学习档案 agent 组件 + 偏好效果对照实验 | TEACH-01、TEACH-06、ARCH-07、ARCH-08 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF02 | MF-02 runtime | 网关出错也记账；输出截断明确报错（BUG-14） | SYS-01、SYS-03 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF03 | MF-03 client | 账本缺陷修复（BUG-16）+ 今日状态数据接口 + 时间加速复测 | PLAN-05、TEACH-05、CHK-01 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF04 | MF-04 platform | 计划提示改进（水平/补充说明起作用）+ 大模块分批生成工具 + 对照复测（BUG-15） | PLAN-01、PLAN-02、PLAN-04 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF05 | MF-05 tools | 新界面语音输入组件（录音、实时出字、模型下载征得同意） | VOICE-01、UI-00 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 7-MF06 | MF-06 classroom | 新界面老师朗读组件（队列、停止、嘴型驱动 Live2D、模型下载征得同意） | VOICE-01、TEACH-05 | 43d82b1 | 已合入 0db14f2（2026-09-28） |
| 8-MF01 | MF-01 library | 最终检验（CHK-01）用的出题角度与归因画像组件 | CHK-01、TEACH-04、ARCH-07 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF02 | MF-02 runtime | 新建计划对话用例集：按新的“意图判断”接入真实复测 | ARCH-06、BUG-13、PLAN-07 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF03 | MF-03 client | 课堂时长与掌握度：跨天时间加速模拟 | PLAN-05、TEACH-02、TEACH-05、TEACH-07 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF04 | MF-04 platform | 正式生成真实复测：全文分批 + 截断拆半接入后（BUG-15 收尾） | BUG-15、PLAN-02、PLAN-04 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF05 | MF-05 tools | 学习提醒：已有 DailyClock + 系统通知（到点还没开始就提醒） | SYS-04、TEACH-05 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF06 | MF-06 classroom | 朗读边生成边读：老师流式输出时按句子朗读 | VOICE-01、TEACH-05 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 8-MF07 | MF-07 server | 课堂数据变多后的同步性能 | ARCH-05、TEACH-01 | 755b29b | 已合入 3bfe099（2026-09-28） |
| 9-MF02 | MF-02 runtime | DeepSeek 高峰/闲时计价补全 + 花费显示 + 新建计划对话补测 | BUG-20、SYS-03、BUG-13 | de1723f | 已合入 2e41f0e（2026-09-28） |
| 9-MF06 | MF-06 classroom | 朗读流水线（边播边合成）并接入课堂边生成边读 | VOICE-01、TEACH-05 | de1723f | 已合入 2e41f0e（2026-09-28） |
| 10-MF02 | MF-02 runtime | 用量只显示 token + 厂商余额显示在 API 右边 | SYS-03、BUG-20、UI-00 | a6f286e | 已合入 8776ad9（2026-09-28） |
| 10-MF01 | MF-01 library | 模拟学生真实上课评测：判定准不准、老师讲得对不对，并修组件 | ARCH-07、TEACH-02、TEACH-04、TEACH-06、BUG-18 | a6f286e | 已合入 8776ad9（2026-09-28） |
| 10-MF03 | MF-03 client | 所有时间相关行为的全面审查：结果合不合理，有问题直接修 | PLAN-05、PLAN-08、TEACH-05、SYS-04、CHK-01 | a6f286e | 已合入 8776ad9（2026-09-28） |
| 11-MF04 | MF-04 platform | 计划中途加资料：只处理新增部分的组件 | PLAN-10、PLAN-08、PLAN-04 | 0953cfd | 已合入 8d8b76e（2026-09-29） |
| 11-MF06 | MF-06 classroom | 新界面的 Windows 安装程序 + 朗读引擎按需下载 + 自动更新 | SYS-06、VOICE-01、ARCH-01 | 0953cfd | 已合入 8d8b76e（2026-09-29） |
| 11-MF03 | MF-03 client | 检验时长分开预估、分开记 + 全流程阻塞复查 | CHK-01、PLAN-05、TEACH-02、SYS-04 | 0953cfd | 已合入 8d8b76e（2026-09-29） |
| 12-MF06 | MF-06 classroom | 朗读组件改为从官方下载站获取 + 正式发布构建（交架构师签名上传） | SYS-06、VOICE-01 | ed11b90 | 已合入（9d71b15） |
| 12-MF04 | MF-04 platform | 加资料支持贴网址（网页与 GitHub 仓库） | PLAN-10、PLAN-06、BUG-09 | ed11b90 | 已合入（9d71b15） |
| 13-MF01 | MF-01 library | 课堂提速（并行与预取）+ 长模块对话压缩 + 可重复的课堂回归评测 | ARCH-07、TEACH-01、TEACH-04、SYS-05 | b58dc7b | 未发出，已作废（由 15-MF01 取代：用户定不做提速和对话压缩） |
| 14-MF07 | MF-07 server | 服务器瘦身：把新软件在用的约 1.1 万行迁进 refactor/server，做只挂这几组接口的新入口 | DATA-01、ACC-01、ACC-02、DATA-02 | 1284f67 | 已合入（b58e0da） |
| 14-MF03 | MF-03 client | 客户端旧代码迁移 + 删除仓库里不再用的旧代码和旧文档 | DATA-01、SYS-06、UI-00 | 1284f67 | 已合入（b58e0da） |
| 15-MF02 | MF-02 runtime | 缓存命中率提到 95% 以上：先离线算出不命中的原因，再整理请求组装顺序 | QA-01、SYS-05、ARCH-08、SYS-03 | 460f51a | 已合入（离线 68%，未达 95%） |
| 15-MF04 | MF-04 platform | 资料权威性：规则为主的可信度与相关性打分，只采用权威资料 | QA-01、PLAN-06、PLAN-10、BUG-08、BUG-09 | 460f51a | 未合入：召回为 0，由 16-MF04 在同一分支继续 |
| 15-MF01 | MF-01 library | 课堂教准：判定零冤枉、讲解零错误（改为回放已有记录，预算 ¥6） | QA-01、TEACH-01、TEACH-04、BUG-18、BUG-21 | 待定 | 由 16-MF01 取代（并入提前备课） |
| 16-MF01 | MF-01 library | 课堂教准 + 提前备课（提前一到两天，备好的内容先对照原文核对） | QA-01、TEACH-08、TEACH-01、TEACH-04、BUG-18、BUG-23 | b019f54 | 已合入（部分完成） |
| 16-MF04 | MF-04 platform | 资料权威性第二段：补召回（跟进讲义/PDF/字幕、识别发布日期、相关性），跑完 8 个主题 | QA-01、PLAN-06、PLAN-10 | b019f54 | 已合入（部分完成） |
| 16-MF02 | MF-02 runtime | 学习报告升级：图表、导出 PDF、逐点老师评语、历史欠账明细 | RPT-01、CHK-01、BUG-16、QA-01 | b019f54 | 已合入（部分完成） |
| 17-MF01 | MF-01 library | 备课第二步：下一个点直接用备好的讲稿，老师只现场点评；备课不再截断重试；补核对漏洞 | TEACH-08、QA-01、TEACH-01、TEACH-04 | 12a58e4 | 作废：由第十八批取代 |
| 17-MF02 | MF-02 runtime | 逐点评语改为直接用判定员写下的错误原因，不调模型 | RPT-01、QA-01 | 12a58e4 | 作废：由第十八批取代 |
| 17-MF04 | MF-04 platform | 资料权威性收尾：编程主题补到至少 3 份 | PLAN-06、QA-01 | 12a58e4 | 作废：由第十八批取代 |
| 18-MF01 | MF-01 library | 讲课这一侧：直接用备好的讲稿、老师只现场点评；备课不截断重试；学习档案按模块更新；知识点按约 15 分钟切 | TEACH-08、TEACH-01、PLAN-04、QA-01 | 6d34557 | 已合入（部分完成） |
| 18-MF03 | MF-03 client | 检测与检验这一侧：题库与挑题、综合题答错拆开问、新过关等级、自由检验、报告评语用判定原因 | CHK-01、TEACH-02、TEACH-04、RPT-01、MAP-01、QA-01 | 6d34557 | 已合入（部分完成） |
| 19-MF02 | MF-02 runtime | 按用途设定模型思考强度（SYS-07）+ 主观题判定改造（TEACH-04） | SYS-07、TEACH-04、QA-01、SYS-03、SYS-05 | 94f9de6 | 已合入（部分完成） |
| 19-MF06 | MF-06 classroom | 全仓库只读自查：找问题、不改代码（为之后外部模型挑错做准备） | 全部条目（以 CURRENT_STATE 为准） | 94f9de6 | 已完成（只读报告：P1 2、P2 3、P3 2） |
| 20-MF06 | MF-06 classroom | 修复全仓库自查找到的 7 个问题（同步冲突、重复判定、完成计划、加资料、读取失败、开始计划、旧代码） | DATA-02、TEACH-02、CHK-01、PLAN-10、UI-01、BUG-23 | 1a0d8ca | 已合入（完成） |
| 21-MF02 | MF-02 runtime | 课堂反复调用的缓存命中率提到 95% 以上（在新的思考强度下） | SYS-05、SYS-07、ARCH-08、QA-01 | 6791655 | 已合入（部分完成） |
| 21-MF04 | MF-04 platform | 资料权威性收尾：编程主题补到至少 3 份，并换一个编程题目验证 | PLAN-06、QA-01 | 6791655 | 已合入（部分完成） |
| 22-MF01 | MF-01 library | 全流程验收：模拟时钟 + 真实模型，从建计划跑到报告与自由检验（只找问题，不改产品代码） | QA-01 及全部已实现条目 | ad8368e | 已完成（部分：P1 卡在第 2 模块检测，最终检验/报告/自由检验未验收） |
| 23-MF03 | MF-03 client | 题库提前出且分小批（修检测卡死 P1）+ 主观题拿不准时再问一道更深的题、判定依据不要求逐字 | CHK-01、TEACH-04、PLAN-10、QA-01 | a7e1dd8 | 已合入（部分完成） |
| 23-MF01 | MF-01 library | 判定之前老师不透露答案；随口问不和讲稿例子原样重复 | TEACH-06、TEACH-08、QA-01 | a7e1dd8 | 已合入（部分完成） |
| 23-MF02 | MF-02 runtime | 查清课堂缓存命中只有 62.6% 的原因并修（先用验收保存的真实请求离线分析） | SYS-05、ARCH-08、SYS-07、QA-01 | a7e1dd8 | 已合入（部分完成） |
| 24-MF06 | MF-06 classroom | 新样本重跑全流程验收（历史：唐朝与丝绸之路）：只找问题，不改产品代码 | QA-01 及全部已实现条目 | 1d3f292 | 已完成（部分：预算用完停在第 2 天；P2 5 条、P3 3 条） |
| 25-MF01 | MF-01 library | 引导式分步课堂（讲课一侧）+ 讲稿核对不再白花钱 + 备课避开高峰价 | TEACH-06、TEACH-08、CHK-01 P2-02、QA-01 | 008ca8f | 已合入 43c033b（部分：真实只写 1 批稿，核对未跑） |
| 25-MF03 | MF-03 client | 题库固定 2 道、答错才补 + 出题格式程序把关 + 重测换题 + 过程题判法 + 检测计时 | CHK-01、TEACH-04、TEACH-02、QA-01 | 008ca8f | 已合入 43c033b（部分：真实出题 8/13 合格，停在第一段） |
| 25-MF02 | MF-02 runtime | 思考档位真正生效 + 输出上限与时限 + 用量记全 + 缓存离线分析 | SYS-07、SYS-01、SYS-03、SYS-05、QA-01 | 008ca8f | 已合入 43c033b（部分：缓存排列只能到约 70.7%，档案保持思考） |
| 25-MF05 | MF-05 tools | 引导式课堂界面 + 报告标注现场讲 | TEACH-06、TEACH-08、RPT-01、UI-00 | 008ca8f | 已合入 43c033b（完成，假数据核对） |
| 26-MF03 | MF-03 client | 题目结构与判法（不回避简答）+ 判法必须测过 | TEACH-04、CHK-01、QA-01 | a7d8db1 | 已合入 0528289（部分：自建样例 180/180；公开集 75/91，9 条未判） |
| 26-MF04 | MF-04 platform | 题库独立核对 + 出题质量实测 | CHK-01、TEACH-04、TEACH-08、QA-01 | a7d8db1 | 已合入 0528289（部分：真实三科评测待跑，脚本已备） |
| 26-MF02 | MF-02 runtime | 用 prefixcash 查缓存断点 + 按 Manus 做法改老师与档案请求 | SYS-05、ARCH-08 | a7d8db1 | 已合入 0528289（部分：真实前后对照第一次请求失败后停止） |
| 26-MF01 | MF-01 library | 讲稿写稿与核对真实复测 + 真实后端接新界面联调 | TEACH-08、TEACH-06、QA-01 | a7d8db1 | 已合入 0528289（部分：4/6 点核对，一次通过 75%，¥0.16/点超标） |
| 27-MF01 | MF-01 library | 讲稿省钱（不降质量）+ 核对员检查清单 | TEACH-08、TEACH-06、QA-01 | 9cd348c | 已合入 b16d6df（部分：每点 ¥0.09–0.37 超标，首次核对 4/6） |
| 27-MF03 | MF-03 client | 判定校准：简略写法不冤枉 + 公开数据集补完 | TEACH-04、QA-01 | 9cd348c | 已合入 b16d6df（部分：分歧冤枉 12→9，回归只跑 52 条） |
| 27-MF06 | MF-06 classroom | 判定用的“没见过的考卷”：独立出题、人工标注、跑基线 | TEACH-04、QA-01 | 9cd348c | 已完成（部分：考卷 174 条，基线跑了 72 条，冤枉 2/70；无代码） |
| 27-MF04 | MF-04 platform | 三科真实出题评测 + 按结果修出题与核对 | CHK-01、TEACH-04、QA-01 | 9cd348c | 已合入 b16d6df（部分：只测历史，8 道全选择、1 道错） |
| 27-MF02 | MF-02 runtime | 缓存真实前后对照 + 调用失败留下原始证据 | SYS-05、SYS-01 | 9cd348c | 已合入 b16d6df（部分：失败留证完成，真实对照因测试脚本问题未做成） |
| 28-MF04 | MF-04 platform | 出题换方法：不看答案的独立解题核验 + 题型比例程序把关 | CHK-01、TEACH-04、QA-01 | 9c9f32b | 已合入 28064c1（三科入库 20 道全合格；回归集好题误拦 1/8） |
| 28-MF01 | MF-01 library | 讲稿换方法：逐条说法核对 + 学生试答 + 程序跑代码 + 结构化输出 | TEACH-08、TEACH-06、QA-01 | 9c9f32b | 未合入（部分：逐条核验截断，6 点只核完 2 点；等用户定） |
| 28-MF03 | MF-03 client | 判定换方法：信息不足就追问一次 + 同类已判示例 | TEACH-04、QA-01 | 9c9f32b | 已完成（部分；28-MF06b 对比后不合入） |
| 28-MF06 | MF-06 classroom | 用当前主线跑完没见过的考卷（174 条） | TEACH-04、QA-01 | 9c9f32b | 已完成（174 条：冤枉 1、放水 0、一致 98.24%；无代码） |
| 28-MF06b | MF-06 classroom | 用同一份考卷对比 28-MF03 新判法与当前主线 | TEACH-04、QA-01 | 396bdb3 | 已完成（新判法冤枉同为 1、一致 98.8%；建议不合入，已采纳） |
| 28-MF01b | MF-01 library | 讲稿按方案 A 收尾：保留第二十七批核对，只并入程序跑代码与学生试答 | TEACH-08 | 4484d87 | 已完成（部分：真实 4 点最终批准 0/4；不合入） |
| 29-MF06 | MF-06 classroom | 第三次小测试：功能验收（初中数学，完整跑到最后），只找问题不改代码 | QA-01 及全部已实现条目 | 328907c | 已完成（功能验收未通过：P1 2、P2 2、P3 3；讲错 0、冤枉 0） |
| 30-MF04 | MF-04 platform | 最终检验缺题永久卡住（P1-02）+ 补充说明点数要求被忽略（P2-02） | CHK-01、PLAN-04 | 8de04cd | 已合入 cd09590（完成：P1-02、P2-02） |
| 30-MF01 | MF-01 library | 数学网页分数抽错（P2-01）+ 讲稿没提前备好时等 1–2 分钟（P3-01） | PLAN-09、TEACH-08 | 8de04cd | 已合入 cd09590（P2-01、P3-01 完成；全量里 1 项范围外测试偶发失败） |
| 31-MF06 | MF-06 classroom | 用同一个脚本重跑第三次小测试（复测第三十批修复） | QA-01 | cd09590 | 已完成（7 项旧问题 5 过；新问题 P1 1、P2 2、P3 1；讲错 0、冤枉 0） |
| 32-MF01 | MF-01 library | 点“继续”不能卡住等后台备课（P3-01 第二轮） | TEACH-08、TEACH-06 | dd0215f | 已合入 808acf0（P3-01 解决） |
| 32-MF04 | MF-04 platform | 资料覆盖不到时不能用重复内容凑满模块 | PLAN-04、PLAN-02 | dd0215f | 已合入 808acf0（部分：意思相同的重复仍漏拦） |
| 33-MF06 | MF-06 classroom | 再用同一脚本复跑第三次小测试 | QA-01 | 9d039ec | 已完成（P1 0；P2 2、P3 1；讲错 0、冤枉 0） |
| 34-MF03 | MF-03 client | 快速确认与深问题目改用核对过的题库题 | TEACH-03、TEACH-04、CHK-01 | cfd1181 | 已合入 3b8c687（快速确认达标；发现一道未上屏错题） |
| 34-MF04 | MF-04 platform | 跨模块重复第二轮：按意思比较 | PLAN-04 | cfd1181 | 已合入 3b8c687（部分：回放 2/2；真实生成被分钟矛盾卡住） |
| 35-MF04 | MF-04 platform | 建计划改造：覆盖在聊天里说清、放弃部分不出现、去掉时长与凑点数限制 | PLAN-01、PLAN-04、ARCH-06 | 6165200 | 已合入 a00e808（完成） |
| 35-MF01 | MF-01 library | 纠错题自洽：先写错因、程序逐行验算、解题员重算 | CHK-01 | 6165200 | 已合入 d256178（部分：真实只 1/4 完整通过，Python 撞输出上限） |
| 35-MF03 | MF-03 client | 判定：答对只给结果时追问一次（合入第二十八批并收窄） | TEACH-04 | 6165200 | 已合入 d256178（完成；新提示未真实调用，待公平复测） |
| 35-MF02 | MF-02 runtime | 学习档案只写有据的话 + 两项偶发测试修稳 | TEACH-01 | 6165200 | 已合入 d256178（完成） |

## 任务模板

新任务照这个写（下面是第二十三批 MF-03 的原文，换掉编号、工位、基线、背景和要做的事）。固定部分：开工同步、禁止事项、磁盘规则、QA-01 验收、花钱规则、决策原则、同时在跑的任务、先读、要做、验收、回执项目。

### 示例：[23-MF03] client｜题库提前出且分小批（修检测卡死 P1）+ 主观题拿不准时再问一道更深的题、判定依据不要求逐字

~~~text
【MentorFlow 第二十三批｜MF-03（工位 client）｜题库提前出且分小批（修检测卡死 P1）+ 主观题拿不准时再问一道更深的题、判定依据不要求逐字】

开工同步（第一步）
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard a7e1dd8
测试用 D:\MentorFlow\.venv\Scripts\python.exe（已装服务器依赖）；前端依赖用 D:\MentorFlow\.local\web-deps 和 D:\MentorFlow\.local\node。提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...
禁止 push；禁止改主目录；禁止 ssh、连接服务器、部署；不碰正式签名私钥。不碰 D:\Kristina* 下任何东西。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）：E 盘只读运行、不复制不写；所有产物在 D:\MentorFlow\.local\test-tmp\ 下；中间产物用完即删（被系统拦住就列路径，架构师来删），回执写明最终占用。含反斜杠的内容写成脚本文件再运行（BUG-10）。
验收按 CURRENT_STATE [QA-01]：回执对每条相关标准写“达标/未达标”；不让被测模型给自己打分，由你本人核对。回执里的测试结果要和实际一致：全量测试有几项失败就写几项。
花钱规则（用户定，严格执行）：真实模型用管理员现有 DeepSeek Flash 预设，总上限 ¥2，分两段——第一段最多 ¥1。能离线做的一律先离线做（假模型、规则、回放已保存的记录）。每次花钱前把“预计改善多少、为什么”写进报告；第一段没有明显改善就停下交回执。
遇到需要选择的事：按 CURRENT_STATE 条目和本任务写的原则自己决定，在回执写明决定与理由，继续做完，不要停下来问。只有超出花费上限、要动服务器或正式密钥、要删用户数据这三类才停下来。对原因的判断要用真实数据验证后再写（BUG-20）。回执附“问题记录”（现象、原因、排查、解决、验证）。
背景：第二十二批全流程验收（小测试）在第 5 天卡住：报告 D:\MentorFlow\.local\test-tmp\acceptance\report.md，完整对话 dialogue.md，117 次调用的完整请求与用量在 calls\。用户要求把查出的问题一次修好，再换一个新样本重跑小测试。用户 2026-09-30 的决定已写进 CURRENT_STATE 的 [CHK-01]、[TEACH-04]、[TEACH-06]（各条“需求”下 2026-09-30 的那一行）。
同时在跑三个任务：23-MF03 改题库与判定；23-MF01 改老师不透露答案与随口问；23-MF02 查并修缓存（只动请求里内容的先后顺序，不改提示文字）。三人都可能碰 app/classroom.py 与 course/ 下的请求构建：各自只改自己那部分，冲突由架构师合并。
现象（验收报告 P1、P2 第 3 条）：6 个点的模块一次性生成整套题库，第一次输出超过 8192 被截断，加倍后超过 60 秒超时，第三次拿到 14 题却因一个点没有单点题被整批丢弃；每次“开始模块检测”约 90 多秒后仍没有题，流程卡死，“直接考”同样超时；模块 1 成功那次也等了 83.75 秒。另一处：判定员用“……”把学生原话几段拼起来当依据，逐字校验不过被当成“拿不准”，完整答对的综合题被要求补充。
先读：AGENTS.md；CURRENT_STATE 的 [CHK-01]、[TEACH-04]、[PLAN-10]、[QA-01]；验收报告与 calls\call-0113～0118.json；app/classroom.py 的题库与检测部分（_bank_select、_bank_fill、_prepare_later 里的出题、start_test、start_check、start_practice）、course/question_bank.py、question_agent.py、judge_agent.py、store 的题库部分。
要做
  1. 题库按 1–2 个点一小批出题（一次请求只管这几个点，含它们的单点题和最多 3–4 点的综合题）；一批失败只丢这一批，缺哪个点就只补哪个点；严格校验以“批”为单位，不因一个点缺题丢掉整个模块的题。
  2. 提前出：进入一个模块就在后台出这个模块的题；做这个模块检测时同时准备下一个模块；学最后一个模块时准备最终检验要用的题；“直接考”同样用提前备好的题。开始检测时题应已备好（目标：点开始后 2 秒内出第一题）；后台还没备好时显示“正在准备题目”，并在备好后自动出题，不让用户反复点；实在出不了明确说原因。
  3. 中途加资料（PLAN-10）：新模块自带题库；补充到已有知识点的，只让受影响模块的题库在后台重出。
  4. 主观题判定（TEACH-04 2026-09-30 行）：判定依据不要求逐字引用，同义说法算数；“拿不准”只看判定员自己标的不确定。拿不准时，针对同一个知识点再问一道更深的题（从题库取或现场出一道），这道答错就记错、答对就记对；替换掉现在“再独立判一次、不一致就追问一句”的做法。
  5. 验证：先离线（假模型覆盖：分批、单批失败只补缺点、提前备题的时机、备题中进入检测、加资料只重出受影响模块、拿不准时出深问题并按结果记账、同义说法判对）。再用真实模型在一个小固定计划上各测一次：一个 6 个点左右的模块提前备题并开始检测、一次“直接考”、一小段最终检验（第一段 ≤ ¥1）；记录开始检测等待时间、每批出题耗时与是否截断；你本人核对题目与判定。
验收：pytest tests/refactor 全部通过；web 构建通过（不提交 dist）；报告写到 D:\MentorFlow\.local\test-tmp\bank-judge\report.md。
回执：【MF-03｜client｜第二十三批｜完成/部分完成/受阻】基线 ／ 提交 ／ 分批与补缺做法 ／ 提前备题的时机 ／ 开始检测等待时间（前后）／ 主观题新判法与核对结果 ／ 加资料的处理 ／ 花费 ／ 本地最终占用 ／ 问题记录
~~~

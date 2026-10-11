# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-12）：线上 2.0.13。2.0.14 三件：103-MF03（99fcb13c，[BUG-78]）、104-MF04（a359073a，[BUG-79]）已合入；105-MF03（[BUG-81] 上课位置跟着账号走）已起草，待用户转发。用户定：不先发 2.0.14，等 105 合入后用户在源代码版测过、说可以推送，才推送并发 2.0.14（只发一次 GitHub，建议 build_installer=true），再由用户在装好的软件里点更新，架构师看 launcher-startup.jsonl 和 updater-refresh.json（应为 UPDATED）。源代码版的本机位置文件停在 10-08 的第 1 模块（[BUG-81]），105 合入前不要让用户在源代码版上课。全量里 loading86、self_test81、status88 三个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。

## 协作方式

- **先讨论、后派活（最优先）**：问题先和用户讨论清楚（现象、原因、牵连范围、前置依赖）再写任务；收到回执只讲审查结果、问题和要用户定的事，用户同意后再把整批任务原文放在回复最后，然后停下等转发。不出看着笃定但有漏洞的方案。
- **不懂就问**：有不懂或感觉有偏差，直接问用户。
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
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，mentorflow-admin，sudo -n）。线上后端 mentorflow-slim-backend:MF-75（切换与回滚脚本 /opt/mentorflow/state/mf75/switch.sh，上线前备份同目录）；入口 nginx 容器 mentorflow-e1-preview-edge-1（配置 /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf）；更新与下载目录 /opt/mentorflow/update-static/e108/app/；资料白名单 /opt/mentorflow/state/mentorflow-e1-preview/catalog/。 2026-10-10 起两个 docker 网络设了 ip_range（BUG-45）：以后用 Compose 操作这些容器，命令末尾必须追加 -f /opt/mentorflow/state/bug45-20261010/network45.override.yml。
- **磁盘（用户 2026-10-10 定）**：测试与临时产物在 .local/test-tmp/ 下；冒烟测试、构建、中间文件用完就自动删，不留着等以后分类。员工删除被系统拦住时，架构师合入后直接删（只留 report.md 和截图），不用再问用户。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | 以 `git rev-parse --short HEAD` 为准；103-MF03 合入 99fcb13c，104-MF04 合入 a359073a。本地比 origin 多几条（文档 + 103、104 合入），随 2.0.14 发版推送 |
| 已发布 | 2.0.13 = 10e625f4（非必须；下载站安装程序 2.0.13）；强制线 2.0.3 |
| GitHub | main 已推送（私有仓库）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 空闲 | client |
| MF-03 | 105-MF03 已起草，待用户转发（[BUG-81]） | classroom |
| MF-04 | 空闲 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 105-MF03（classroom）：[BUG-81] 上课位置跟着账号走。已起草，待转发（原文见下）。
- 105 合入 → 用户在源代码版测（换设备接着最新进度、随口聊聊 [BUG-78]）→ 用户说可以推送 → 发 2.0.14 → 用户在软件里点更新 → 用户走通注册（[ACC-02]）→ 发给朋友。
- 下一批候选：[BUG-74] 更新提速、[BUG-77] 安装程序默认找已有安装位置（用户点头再派）。

## 第一百零五批任务原文（2026-10-12，基线 d1e64674）

### [105-MF03] classroom｜上课位置跟着账号走：换一台设备开课，接着最近一次学到的地方（[BUG-81]）

~~~text
【MentorFlow 第一百零五批｜MF-03（工位 classroom）｜上课位置跟着账号走：换一台设备开课，接着最近一次学到的地方，不回到这台设备上次停的旧位置（BUG-81）】

现成项目与许可证（第一节）
  - 不引新库，也不新造同步机制。上课位置直接作为一种新的课程记录，走项目现有的同步：
    - course/store.py 的 PARENTS、_records 待同步队列、apply_remote 逐字段合并（同一项都改了，保留最后修改的，[DATA-02]、[BUG-24]）；
    - 服务器的通用 course_record 增量拉取（[ARCH-05]）。
  - 做法参照行业通行的“多设备续读/续播”：
    - Kindle Whispersync 同步“最近读到的位置”，Netflix/YouTube 同步“继续观看”的位置：位置是账号下的一条记录，换设备时取最近写入的那条；
    - QQ/微信的消息漫游：服务器存全量，各设备按序号增量拉取。我们的同步已经是这样，缺的只是“上课位置”没进同步。
    - 这些只借做法，没有代码可引，不涉及许可证。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard d1e64674         # 主线：已合入 103-MF03、104-MF04
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不调用真实模型。
D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读：可以只读打开里面的 classroom\<计划>.json 和 course.sqlite3 做对照（sqlite3 用 mode=ro），不改不删。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md（磁盘规则、D 盘不膨胀、功能先行不加安全加固）。所有产物在 D:\MentorFlow\.local\test-tmp\sync105\ 下，用完即删，只留 report.md。删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有要动服务器、要删用户数据、或下面写明“停”的情况才停。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。花费上限 ¥0。同时在跑：无。

背景（CURRENT_STATE [BUG-81] [DATA-02] [ARCH-05] [ACC-01]）：
  - 用户 10-08 之后一直用装好的软件，已跳过第 1、2 模块，学到第 3 模块第 3 个点。10-12 换到源代码版点开课，却进了第 1 模块，要从第 4 个点接着讲。
  - 原因：上课位置（模块、第几个点、讲到哪一步、进行中的测验）只存在本机文件 classroom\<计划>.json（app/classroom.py 的 _file/_load/_save），不在同步范围；打开课堂时直接用本机位置，不和已同步的进度核对。
  - 现成的 _rebuild(course) 已经能按已同步的进度算出位置：第一个没过关、没跳过的模块里，第一个没讲的点。
  - 用户定的规则：哪台设备上学的，换到另一台都要接着最近一次的进度；来回切换，每次都取最新的。同一账号同一时间只有一台在线（[ACC-01]），所以正常切换时不会两台同时改。

要做
  1. 上课位置进同步。
     - 在 course/store.py 加一种按计划一条的记录（名字自定，例如 classroom_positions），用现有的待同步队列和 apply_remote，同一项保留最后修改的。
     - classroom.py 的 _load/_save 改为读写这条记录。
     - 本机专用、换设备无意义的内容（例如正在等的模型请求、流式中间态）不要带到另一台：要么不同步，要么另一台读到时清掉。自己判断哪些字段属于这一类，回执列出。
     - 进行中的测验、讲到一半的点、随口聊聊这些，换设备后接着原样继续。
     - 单条记录有 256 KiB 上限：查一下现在的位置文件最大有多大，测验队列会不会超；会超就拆分或瘦身，回执写数据。
  2. 打开课堂时先核对，位置不能落后于已同步的进度。
     - 位置所在模块已经过关或跳过，却还停在讲课的步骤；或者停在一个已经讲过的点的开头：就按 _rebuild 重新算位置。
     - 只改位置，不动作答、时长和判定。
     - 同一台设备的正常流程不能受影响，例如刚考完停在结果页再打开。判断条件自己定，回执写清并给测试。
     - 这一条保证没上传位置的旧版本设备（2.0.13）、或上传失败时，新设备也不会回到旧位置。
  3. 迁移（[ARCH-03]）：已有的本机 classroom\<计划>.json 第一次运行时导入成这条记录。
     - 修改时间用文件里或文件本身的最后保存时间，这样两台设备各自导入时，最近用过的那台胜出；导入后再走第 2 条核对。
     - 旧文件保留不删。
     - 数据库结构要升版本的照现有 SCHEMA_VERSION 迁移写法做，不能让用户已有数据打不开。
  4. 新旧版本混用（必须查清）：
     - 用户在发 2.0.14 前，会用源代码版（新代码）和装好的 2.0.13 来回切。查清 2.0.13（10e625f4）拉到不认识的新记录时会怎样：现在 apply_remote 对未知记录抛 UNKNOWN_ENTITY。
     - 要查的是整轮拉取卡住、游标不前进，还是跳过这一条继续。写清证据，用 10e625f4 的代码离线复现即可。
     - 如果 2.0.13 只是跳过：照常做。
     - 如果会卡住：照常做，但回执单独写明“2.0.13 在用户更新前同步会卡住，更新后恢复，期间数据不丢”的证据，架构师告诉用户。
     - 新代码本身要做到：拉到不认识的记录类型就跳过并记日志，不卡整轮。以后再加新记录类型就不会再出这种事。
     - 服务器：查清 refactor/server 的 course_record 是否按记录类型做白名单。
       - 如果需要改服务器才能存这种新记录：停下，写清要改哪里，交回架构师（改服务器要用户同意），不要自己改。
  5. 测试（文件名带 105，别和已有的重名），用现有的两台设备同步测试写法（参照 BUG-24、BUG-66 的测试），两个数据目录 + 本机替身服务器：
     - 设备一学到第 3 模块第 3 点，上传；设备二登录拉取，开课直接到第 3 模块第 3 点；
     - 设备二再学两个点，切回设备一，开课到设备二的最新位置；
     - 设备一有进行中的模块测验，换到设备二接着答同一份测验，不重复记分；
     - 设备二本机有旧位置文件（迁移前），开课不回到旧位置；
     - 停在已过关模块的结果页再打开，仍在结果页；
     - 拉到未知记录类型不卡整轮；
     - 迁移：旧文件导入、数据库升级后已有计划照常打开。
  6. 只读对照：用用户两份真实的位置文件（D:\MentorFlowApp\MentorFlow\Data\learning\accounts\<账号>\classroom\029b076315034c00bbd6842a5c9c31d9.json 和 D:\MentorFlowData\account-shell\ 下同名文件）的副本 + 只读的 course.sqlite3 副本，放在 sync105 下做一次离线演算。
     - 预期：源代码版那份按第 2 条核对后到第 3 模块“AI 大模型、Agent 与 RAG…”的第一个没讲的点，而不是第 1 模块。
     - 回执写演算结果。副本用完删掉，不改原件。
  7. 只改 refactor/learning/course/store.py、refactor/learning/app/classroom.py、同步相关的客户端文件（apply_remote 及调用它的同步循环）和 tests/refactor/。要动别处的先写清原因，再自己决定，回执说明。不改服务器。

完成标准：
  - 第 5 条测试全部通过；第 4 条两个问题有明确结论和证据；第 6 条离线演算到第 3 模块；
  - 最后全量跑一次并通过。loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明。
  - 达不到就写“部分完成”或“受阻”。

回执：写到 D:\MentorFlow\.local\receipts\105-MF03.md。表格列：
  基线 ／ 提交 ／ 记录设计（存哪些字段、哪些不带到另一台、最大体积）／ 核对条件 ／ 迁移 ／ 2.0.13 遇到新记录的表现（证据）／ 服务器是否要改 ／ 新增测试 ／ 离线演算结果 ／ 全量 ／ 本地最终占用。
给用户只回一行“105-MF03 完成/部分完成/受阻，回执已写入”。
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

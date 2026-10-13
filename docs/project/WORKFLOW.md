# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-14）：线上 2.0.14，服务器 MF-76。已合入待发：[BUG-84]、[BUG-85] 第一轮、[BUG-86] 第一百一十四批（换入工具、按小要点出题、课堂多轮，只对新式点生效）。115-MF03、115-MF04 已起草待转发；115-MF06 等用户点头。全量里的五个不稳定测试已修好（[BUG-87]），以后全量失败按真失败处理。

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
| MF-02 | 空闲 | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 空闲 | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 121-MF06 已起草，待用户转发（新建计划接按目录切、讲义改到模块准备时） | tools |
| MF-07 | 120-MF07 已起草，待用户转发（报告最高欠账回放，[BUG-89]） | library |

## 待派队列

- 第一百一十四批四份已合入（be709040，网页已编进 dist）。
- 115-MF04 已合入（90577e31）；115-MF06 已合入（d5d6bf84，440→144 大点）。121-MF06（新建计划接按目录切、讲义改到模块准备时）：已起草，待转发。
- 117-MF02 已合入（2ce351a3，组件未接线）；117-MF07 已合入（673394c6，截止日只能预演）。118-MF07 已合入（8d41ea5b，[BUG-88] 已修）。120-MF07（报告最高欠账回放 [BUG-89]）：已起草，待转发。
- 116-MF03 已合入（cf79b127；loading86 测试冲突取 115-MF04 的版本）。119-MF03 已合入（15afe435）。
- 之后：119-MF03、120-MF07、121-MF06 合入 → 发版前总验收 → 用户说可以推送 → 发 2.0.15 → 用户更新 → 关掉软件、用最新数据重新预演给用户看 → 用户点头 → 架构师用 scripts/plan_restructure.py --apply --end-date 2026-10-22 换入（自动备份、可回滚）。
- 待用户定：[BUG-85] 第三轮换 Whisper。

## 第一百一十五批任务原文（2026-10-14，基线 be709040）

### [115-MF03]

~~~text
【MentorFlow 第一百一十五批｜MF-03（工位 classroom）｜新式知识点讲课第二轮：不越出讲义和原文下结论、例子不说过头、不漏原文前提、结尾不总是提问、收束只一两句（BUG-86）】

现成项目与许可证（第一节）
  - 不引新库，在你 114 的 concept 分支上改老师提示与解析（course/teacher_agent.py、app/classroom.py）。规则沿用 [DIR-01]：老师可以打比方，但比方不能产生原文没有的结论；不加 AI 核对老师这一层（用户定过）。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard be709040         # 主线：第一百一十四批四份已合入（新式点只在 structure=concept_v1 时生效，用户计划里还没有）
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：要用用户数据就用 sqlite3 mode=ro 的 backup 复制到任务目录再用，不改原件、不改用户进度。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\class115\，中间产物用完即删，只留 report.md 和必要的小文件；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限或要动用户数据才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。
花费：替身离线做完；最后一次真实核对上限 ¥2，真实模式参数同以往（--live --account-id mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932 --preset-root D:\MentorFlowData\account-shell；Key 不读、不显示、不保存）。
同时在跑：MF-04（runtime）修不稳定测试，只改测试文件和测试工具，不碰你的范围。

背景（CURRENT_STATE [BUG-86] [TEACH-06] [DIR-01]；你的上一轮报告 D:\MentorFlow\.local\test-tmp\class114\report.md 与 review114.json）：
  - 114-MF03 已合入 3aca0463：新式点按小要点多轮讲、小要点进度、讲义、资料导航都能用，位置与时长正确。
  - 真实核对发现的讲课质量问题（原话、原文和调用号见你的 review114.json）：
    1. 越出原文下结论：Redis 被外推成“通道只有凭键取值、不能复杂查询”；
    2. 例子说过头：列存的例子过度绝对化；
    3. 漏了原文前提：完全函数依赖的例子漏了“班级内学号唯一”的前提；
    4. 9 个小要点的第一次讲解都以提问收尾，若干回应套路重复，用户原话嫌“太公式”；
    5. 第一个大点的收束超过一两句。

要做
  1. 提示与上下文：
     - 每个小要点把它的讲义 notes 当作“这一步要讲到的事实”，原文段落当依据；
     - 明确：结论、限定和前提只能来自讲义和原文；打比方时要说清比方的边界；原文写的前提（如“班级内”）讲例子时必须带上；
     - 原文没有的绝对化说法（只能、一定、完全不能）不能出现。
  2. 去公式化：
     - 每轮结尾可以是一个问题、一句小结、一个让学生动手想的小任务或直接接下一步，按上一轮学生的表现选；
     - 同一个大点内不能连续两个小要点用同一种收尾；
     - 回应学生时不复用同一句套话。
     - 用程序记录最近用过的收尾方式和开场方式交给提示，不靠固定轮换顺序。
  3. 收束：大点讲完的收束限制在一两句（例如 ≤80 字），超出时要求重写一次，再超就截到第一句。
  4. 测试（文件名带 115）：
     - 替身输出带“只能/一定”这类原文没有的绝对化词，会被要求重写；
     - 连续两个小要点同种收尾，第二次会换；
     - 收束超长会被处理；
     - 114 的 concept 测试和旧式点课堂测试照过。
  5. 真实核对（≤¥2）：用 114 同一份样例的 K2、K4，加一个别的大点，各讲一遍，学生有对有错有“没懂”。
     - 逐条人工核对：越出原文的结论数、说过头的例子数、漏前提数、收尾方式分布、收束字数；
     - 和 114 那次并排对比。
  6. 只改 course/teacher_agent.py、app/classroom.py 中 concept 分支相关部分和 tests/refactor/。旧式点讲法不变。

完成标准：第 4 条测试通过；第 5 条核对里越出原文、说过头、漏前提都为 0，收尾不再全是提问，收束一两句；最后全量一次（loading86、self_test81、status88、integration38、gateway 的 coverage_check 时限断言是已知不稳定项，MF-04 正在修，失败时单独重跑那一项并写明）。花费不超过 ¥2。达不到写“部分完成”。
回执：写到 D:\MentorFlow\.local\receipts\115-MF03.md。表格列：基线 ／ 提交 ／ 改法（依据约束、去公式化、收束）／ 新增测试 ／ 真实核对（114 对比 115 的各项数字、摘录）／ 花费 ／ 全量 ／ 本地最终占用。
给用户只回一行“115-MF03 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\class115\report.md。
~~~

### [115-MF04]

~~~text
【MentorFlow 第一百一十五批｜MF-04（工位 runtime）｜修掉全量里反复偶发失败的五个测试，让全量结果可信】

现成项目与许可证（第一节）
  - 不引新库。用 pytest 现有的 monkeypatch／固定时钟写法，以及项目里已有的可注入 now/clock 参数；浏览器测试沿用现有 Chromium 测试写法与等待工具。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard be709040         # 主线：第一百一十四批四份已合入（新式点只在 structure=concept_v1 时生效，用户计划里还没有）
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：要用用户数据就用 sqlite3 mode=ro 的 backup 复制到任务目录再用，不改原件、不改用户进度。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\flaky115\，中间产物用完即删，只留 report.md 和必要的小文件；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限或要动用户数据才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。
花费：¥0。
同时在跑：MF-03（classroom）改老师提示，只改 teacher_agent.py、classroom.py 的 concept 分支，你不碰。

背景：最近几批全量里这些测试单独重跑都能过，满载时偶发失败，每次都要人工说明，全量结果不可信：
  1. loading86：startup placeholder 断言（114-MF07 全量）；
  2. status88（114-MF01 全量）；
  3. self_test81（以往多次）；
  4. integration38：总时长一致性断言 42 ≠ 9 + 23（114-MF06 全量，单跑通过，原因未确认）；
  5. gateway 的 coverage_check 时限断言要求正好 1800，实际 1799.985：源码是用总 deadline 减去已耗时（114-MF03 全量）。

要做
  1. 逐个查清偶发原因（时间、并发、浏览器加载时序、共享临时目录等），每个写一句话原因和证据。
  2. 修测试本身，让它确定性地通过或失败：
     - 时间相关的注入固定时钟；
     - 浮点时限的断言改成验证“剩余时限 = 总时限 − 已耗时”的关系，或允许合理的毫秒误差并写理由；
     - 浏览器等待改成等明确的就绪信号，不靠固定 sleep。
     - 不得用删掉断言、跳过、放宽到失去意义的方式“修好”。
  3. 如果查到是产品代码的真问题（例如 integration38 真有时长算错的路径）：
     - 不在本任务改产品代码，写清复现步骤和证据，交架构师另派。
  4. 验证：
     - 五个测试各自在满载下（例如同时跑 -n 或循环 20 次）稳定通过，记录次数；
     - 最后全量跑一次。
  5. 只改 tests/refactor/ 下相关测试和测试工具。

完成标准：五个测试都有原因和修法，循环验证不再偶发；全量一次通过（真产品问题另列，不算失败遮掩）。
回执：写到 D:\MentorFlow\.local\receipts\115-MF04.md。表格列：基线 ／ 提交 ／ 五个测试各自的原因、证据、修法 ／ 循环验证次数与结果 ／ 发现的产品问题（如有）／ 全量 ／ 本地最终占用。
给用户只回一行“115-MF04 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\flaky115\report.md。
~~~

### [115-MF06]

~~~text
【MentorFlow 第一百一十五批｜MF-06（工位 tools）｜知识点重切第四轮：硬规则不过才停，软问题标出来交用户在网页上看；跑完第 5～8 模块；产出能直接交给换入工具的完整结果（BUG-86）】

现成项目与许可证（第一节）
  - 不引新库，在你 112～114 的原型上改；模型调用用现有 gateway.complete。
  - 换入格式以 114-MF07 合入的 course/restructure.py 和 scripts/plan_restructure.py 为准（顶层 plan_id、可选 today、modules[{module_id, replace_point_ids, points, navigation, expected_points?}]，点与小要点字段照 114 共用约定）。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard be709040         # 主线：第一百一十四批四份已合入（新式点只在 structure=concept_v1 时生效，用户计划里还没有）
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：要用用户数据就用 sqlite3 mode=ro 的 backup 复制到任务目录再用，不改原件、不改用户进度。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\points115\，中间产物用完即删，只留 report.md 和必要的小文件；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限或要动用户数据才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。
花费：真实模型总上限 ¥15，分两段：第 5 模块剩余部分过了硬规则再跑第 6、7、8 模块。真实模式参数同以往（--live --account-id mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932 --preset-root D:\MentorFlowData\account-shell；Key 不读、不显示、不保存）。
同时在跑：MF-03 改课堂提示、MF-04 修不稳定测试，都不碰你的范围。

背景（CURRENT_STATE [BUG-86]；你 113、114 的报告）：
  - 第二轮（113）几乎可用：书单全进导航，讲义无书籍简介，7 大点 43 小要点 241 分，只剩 NoSQL 重复和一个大点缺讲义。
  - 第三轮（114）加了更严的自动检查，报警一律停，结果讲义一条都没生成、第 6～8 模块没跑。
  - 用户本来就要逐个看知识点。所以改成：硬规则自动把关，软问题标出来交用户定。
  - 换入工具（114-MF07）已合入，预演在副本上跑通：第 5 模块样例逾期 1535→1535 不变，之后每天目标 238→约 236。

要做
  1. 规则分两类：
     - 硬规则（不过就停、不交付）：书单、学习路线类全进导航；讲义每条都标出处，且只摘知识段落的原文；知识内容未覆盖为 0；不编原文没有的内容；字段与换入格式一致。
     - 软问题（不停，标在结果和网页上，写清原因和建议）：可能重复的小要点或大点；大点不在 20～40 分钟或小要点不在 3～8 个；某个小要点重试一次后仍没有讲义；总时长与旧知识类估时差超过 ±20%。
  2. 跑完第 5 模块剩余部分（以运行时副本的用户位置为准）和第 6、7、8 模块。
  3. 产出：
     - apply-ready.json，直接符合 scripts/plan_restructure.py 的输入格式，complete=true；
     - 对照网页：沿用用户认可的样子，软问题醒目标出，每条附建议做法（合并到哪、保留、拆开），顶部按模块统计软问题数；
     - 用 scripts/plan_restructure.py --dry-run 在用户库副本上预演整份结构，把预演报告（重排前后总分钟、逾期待还、今天目标、之后每天目标、保留/取代/新增数）贴进回执。
     - 预演必须没有被拒的硬错误。
  4. 测试（文件名带 115）：硬规则不过就停；软问题只标不停；导出格式能被 plan_restructure 的预演接受；112～114 的测试照过。
  5. 只改 course/planner_agents.py 中你加的新函数和 points 系列脚本与测试；不改 restructure.py（格式问题写进回执）。

完成标准：第 5～8 模块全部产出、硬规则全过、预演无硬错误；网页标出全部软问题；测试通过；最后全量一次（已知不稳定项单独重跑写明）。花费不超过 ¥15。
回执：写到 D:\MentorFlow\.local\receipts\115-MF06.md。表格列：基线 ／ 提交 ／ 各模块旧点数 → 新点数与分钟 ／ 硬规则结果 ／ 软问题清单（按模块计数与典型例子）／ 整份预演报告 ／ 文件路径 ／ 新增测试 ／ 花费 ／ 全量 ／ 本地最终占用。
给用户只回一行“115-MF06 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\points115\report.md。
~~~

## 第一百一十六批任务原文（2026-10-14，基线 453647c5）

### [116-MF03] classroom｜课堂提速与跳过按钮

~~~text
【MentorFlow 第一百一十六批｜MF-03（工位 classroom）｜课堂提速与跳过按钮：可点选项的回复提前生成、老师回复边生成边显示；“跳过该知识点”按钮，“跳过该模块”藏到开关后并加确认；修收束被截成残句（TEACH-06、TEACH-03、TEACH-07）】

现成项目与许可证（第一节）
  - 不引新库。
  - 提前生成用现有 gateway.complete 在后台线程并发调用，取消用现有的 cancel 机制。
  - 边生成边显示用 gateway.complete 已有的 on_text 流式回调和课堂现有的 ctx.stream 推送；同一前缀的并发请求沿用 [SYS-05] 的缓存写法，让 DeepSeek 前缀缓存命中。
  - 做法是对话产品通行的“预测性预取”（speculative prefetch）与流式输出，只借做法。
  - 前端沿用现有课堂组件与样式；开关用现有按钮样式做，不引组件库。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard 453647c5         # 主线：已合入你的 115（讲课约束、收尾去重复、收束限长）
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\speed116\，中间产物用完即删，只留 report.md 和必要的小文件；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（原来的五个不稳定测试已修好，全量失败按真失败处理）。
花费：替身离线做完；最后一次真实计时与核对上限 ¥3（提前生成会多花，用户同意）。真实模式参数同以往（--live --account-id mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932 --preset-root D:\MentorFlowData\account-shell；Key 不读、不显示、不保存）。
同时在跑：MF-06 重切知识点（只改 planner_agents.py 新函数与 points 脚本），不碰你的范围。

背景（CURRENT_STATE [TEACH-06] [TEACH-03] [TEACH-07]）：
  - 用户定（2026-10-14）：在意的是等老师回复的延迟，不在意多花的模型费用。
    - 老师给出可点选项时（老师自己的 2～3 个选项、〔懂了，继续〕〔没太懂〕），后台立刻为每个选项预先生成回复，点了马上出，或接着正在生成的继续出；
    - 〔懂了，继续〕同时预备下一个小要点（或下一个点）的开场；
    - 用户自己打字时，作废预备，按输入回答。
  - 架构师读代码发现：classroom._teacher 调 gateway.complete 时 on_text 是空函数，整条生成完、parse_teaching 解析后才 ctx.stream 推给界面，所以用户一直看着“老师正在想…”。
  - 用户定的跳过按钮：
    - 课堂里显眼的按钮改成“跳过该知识点”：点了直接跳到下一个点（新式点就是下一个大知识点），不弹窗、不随口问，按 [TEACH-03]“我会了”记；这个点的题照样进模块检测。
    - 按钮旁一个往右拉的小开关，拉过去按钮变成“跳过该模块”，点了先弹窗确认，确认后走现有 [TEACH-07] 的流程。
    - 目的是防误触：误点跳过一个知识点关系不大，跳过整个模块要多一步确认。
  - 你 115 的遗留：K6 的收束被截成“UTF-8/16/32 。”这样的残句。

要做
  1. 先量现状：在真实模式下量“点选项到出第一个字”“到整条出完”“打字发送到出第一个字”，各取 5 次以上，回执列平均和最慢。
  2. 边生成边显示：
     - 老师回复可见的正文边生成边推给界面；
     - 选项块、出处标记等要解析的部分先不显示，整条完成、解析后再定稿替换；
     - 格式不合格触发现有重写时，界面要能撤掉已显示的半截，再显示重写结果（现有 RESET 机制）。
     - 新式点和旧式点都适用。
  3. 提前生成：
     - 界面出现可点选项时，后台为每个选项各发一个生成；〔懂了，继续〕预备下一步开场；
     - 用户点了：已生成完的直接显示，还在生成的接上继续流式显示；
     - 用户打字、插话、切走、点了别的，其余预备全部取消作废，不写进记录、不记账、不改位置；
     - 预备结果必须对应当前课堂状态（用状态版本号对上），状态变了就作废，不能把旧预备显示出来。
     - 同一时刻预备数有上限（例如 4 个），自己定。
  4. 跳过按钮：按上面的用户规定实现。
     - “跳过该知识点”不弹窗、不随口问，按“我会了”记，新式点跳过剩下所有小要点；
     - 小开关拉过去变“跳过该模块”，弹窗确认后走现有直接考流程；
     - 开关状态不跨点保留（每次进新点恢复成“跳过该知识点”）。
  5. 收束：不再把超长收束截成第一句。改成重写一次仍超长时，取到第一个完整句号为止；如果截下来不成句，就用不超过 80 字的原收束整句，字数略超可以接受。不能出现残句。
  6. 改后再量第 1 条的三个时间。目标：点选项到出字 ≤1 秒（预备已完成时）；打字发送到出第一个字 ≤3 秒。回执列改前改后对比和这次多花的钱。
  7. 测试（文件名带 116）：
     - 预备命中直接显示；
     - 预备进行中被点击接着流式显示；
     - 打字后预备作废且不留痕；
     - 状态变化后旧预备不显示；
     - 边生成边显示后格式重写会撤掉半截；
     - 跳过该知识点不弹窗、按“我会了”记；
     - 开关切到跳过该模块会弹窗，取消不跳、确认走直接考；
     - 收束不出残句；
     - 114、115 的课堂测试照过，旧式点课堂测试照过。
     - 前端用现有浏览器测试写法。
  8. 只改 app/classroom.py、course/teacher_agent.py（如需）、课堂前端相关文件和 tests/refactor/。前端改了要编一次网页确认能编过，不提交 dist。

完成标准：第 7 条测试通过；第 6 条有改前改后实测；最后全量一次。花费不超过 ¥3。达不到写“部分完成”。
回执：写到 D:\MentorFlow\.local\receipts\116-MF03.md。表格列：基线 ／ 提交 ／ 边生成边显示怎么做 ／ 提前生成怎么做（命中、作废、上限）／ 跳过按钮与开关 ／ 收束修法 ／ 改前改后延迟与多花的钱 ／ 新增测试 ／ 全量 ／ 本地最终占用。
给用户只回一行“116-MF03 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\speed116\report.md。
~~~

## 第一百一十八批任务原文（2026-10-14，基线 673394c6）

### [118-MF07] library｜改截止日保住欠账

~~~text
【MentorFlow 第一百一十八批｜MF-07（工位 library）｜改截止日也要保住过去的欠账：查清 App 里正常改截止日会不会像 BUG-83 那样把逾期抹掉，会就修；让重排时能同时改截止日（BUG-86、BUG-83）】

现成项目与许可证（第一节）
  - 不引新库。在现有时长账本 course/ledger.py（Ledger.replan 的普通重排与 preserve_past 两条路）、CourseStore.replan_plan 和你做的 course/restructure.py、scripts/plan_restructure.py 上改。规则沿用 [BUG-16]“历史按当天冻结”和 [BUG-83]“重排不能抹掉过去仍要还的欠账”。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 673394c6         # 主线：已合入你的 117（截止日预演）
提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号；不调用真实模型。
D:\MentorFlowApp、D:\MentorFlowData 只读：要用用户数据就用 sqlite3 mode=ro 的 backup 复制到任务目录再用，不改原件。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\window118\，中间产物用完即删，只留 report.md；删除被拦住就列路径交架构师。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。花费 ¥0。
同时在跑：MF-03 改课堂（classroom.py、teacher_agent.py、前端），MF-06 改 planner_agents.py；你不碰它们。

背景（CURRENT_STATE [BUG-86]“重排后的逾期”、[BUG-83]、[BUG-16]）：
  - 用户定（2026-10-14）：
    - 重排不重算过去的欠账，逾期保持约 1500 分；
    - 截止日从 10-20 延到 10-22，之后每天目标按新总时长和新天数平摊。
  - 你 117 的预演：样例下逾期 1535 不变，每天 238→183。
  - 执行被拦住，原因是：
    - Ledger.replan(preserve_past=True) 禁止改窗口；
    - CourseStore.replan_plan 固定写 explicit_replan，不能按 restructure 回放。
  - 架构师读 ledger.py 发现：
    - preserve_past 只给自动重估和 restructure 用；
    - 用户在 App 里正常改截止日走的是普通 replan（注释写“Ordinary user-requested rescheduling retains its existing semantics”）。
    - [BUG-83] 当时的问题正是普通 replan 把过去的欠账移出了“还在还”的那一段，逾期从 1132 显示成 114。
    - 所以要怀疑：用户在 App 里正常改截止日，可能同样把逾期抹掉。

要做
  1. 先查清：
     - 用固定时钟搭一个“窗口进行到一半、已有欠账、当天已还一部分”的计划，在 App 的正常改截止日路径（plan_flow 里改结束日期调用的那条）上把截止日往后延；
     - 看改前改后：逾期待还、今天已还、过去各天目标。
     - 也用用户计划的只读副本做一次（只在副本上）。
     - 回执写清：会不会抹掉，抹掉多少，原因在哪一行。
  2. 如果会抹掉：这是和 [BUG-83] 同类的问题。
     - 修成：改截止日（以及其他用户主动改日期、每天上限的重排）也保住过去仍要还的欠账，只重新分配今天及以后；
     - 另一台设备回放结果一致。
     - 架构师会另记一条 BUG。
  3. 让重排能同时改截止日：
     - preserve_past 的重排允许同时换窗口（只允许往后延或改未来部分，不能改已冻结的日子）；
     - CourseStore 的重排能写 restructure 原因并按它回放；
     - scripts/plan_restructure.py --apply --end-date 能真正执行；
     - 只走一次重排；回滚能恢复原截止日。
  4. 测试（文件名带 118）：
     - 正常改截止日保住欠账（如果第 1 条查出会抹掉，修前先写出失败的测试）；
     - 重排加改截止日一次完成，过去目标与逾期不变、之后按新天数平摊；
     - 两台设备回放一致；
     - 回滚恢复截止日与账本；
     - BUG-16、BUG-83 原有测试照过。
  5. 用用户计划的只读副本 + 117 的样例结构，带 --end-date 2026-10-22 做一次完整的预演与（副本上的）执行，贴出执行前后的逾期、今天目标、之后每天目标。
  6. 只改 course/ledger.py、course/store.py 中与重排相关的部分、course/restructure.py、scripts/plan_restructure.py、app/plan_flow.py 中改日期调用重排的地方（如需）和 tests/refactor/。

完成标准：第 1 条有结论和证据；第 3 条能执行；第 4 条测试通过；第 5 条副本上执行前后数字一致可解释；最后全量一次。
回执：写到 D:\MentorFlow\.local\receipts\118-MF07.md。表格列：基线 ／ 提交 ／ 正常改截止日会不会抹掉欠账（证据、原因）／ 修法 ／ 重排加改截止日怎么做 ／ 副本上执行前后数字 ／ 新增测试 ／ 全量 ／ 本地最终占用。
给用户只回一行“118-MF07 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\window118\report.md。
~~~

## 第一百一十九批任务原文（2026-10-14，基线 cf79b127）

### [119-MF03] classroom｜课堂精炼接线

~~~text
【MentorFlow 第一百一十九批｜MF-03（工位 classroom）｜课堂精炼：老师用荧光标重点、回复控制在两三百字、大知识点讲完发要点卡；把 117-MF02 的三个组件接进课堂，加“只看重点”开关（TEACH-06）】

现成项目与许可证（第一节）
  - 不引新库。接线用 117-MF02 已合入的组件：ClassroomMarkdown 的 focus、messageKey，以及 RecapCard。接线说明见 tests/refactor/focus117/README117.md。
  - 老师提示与解析在现有 course/teacher_agent.py、app/classroom.py 上改，沿用你 116 的边生成边显示和提前生成。

开工同步
  cd D:\MentorFlow-office\classroom\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/classroom
  git reset --hard cf79b127         # 主线：已合入你的 116 和 117-MF02 的组件
提交：git -c user.name="MentorFlow Worker classroom" -c user.email="worker-classroom@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\focus119\，中间产物用完即删，只留 report.md 和截图；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（原来的五个不稳定测试已修好，全量失败按真失败处理；跑浏览器测试前把 D:\MentorFlow\.local\node 加进 PATH，basetemp 的父目录先建好）。
花费：替身离线做完；最后一次真实核对上限 ¥2。真实模式参数同以往（--live --account-id mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932 --preset-root D:\MentorFlowData\account-shell；Key 不读、不显示、不保存）。
同时在跑：
  - MF-07 改账本重排（ledger.py、store.py 的重排部分、restructure.py、plan_restructure.py，可能动 plan_flow.py 改日期那处）；
  - MF-06 改 planner_agents.py。
  - 你不碰这些文件。

背景（CURRENT_STATE [TEACH-06]“精炼与重点”）：
  - 用户要的是精炼，不是大段搬原文让人逐句读；有的用户赶时间只看重点。
  - 用户定（2026-10-14）：
    - 荧光笔标出每次回复的 1～3 句关键结论，加粗仍留给术语；
    - 课堂加“只看重点”开关，打开后没标荧光的文字收起或变淡；
    - 平时一次回复两三百字左右，学生要求展开时可以长；原文放在“原文出处”里，不大段搬；
    - 大知识点讲完用要点卡（每个小要点一行结论，摘自讲义）代替长总结；
    - 任何时候都不截断老师的话。
  - 117-MF02 已做好组件（合入 2ce351a3）：
    - 正文 ==重点句== 渲染为荧光；
    - ClassroomMarkdown 的 focus 模式会把无荧光段落收成“…（点开）”，问题句保留；
    - RecapCard 渲染 kind=recap 的消息 {title, items:[{title, text, chunk_id}]}。

要做
  1. 老师提示（新旧两种点都适用）：
     - 每次回复用 ==…== 标出 1～3 句最关键的结论，只标结论，不整段标；术语照旧加粗；
     - 平时一次回复控制在两三百字左右，学生说“展开讲讲”“详细点”这类话时可以长；
     - 不大段引用原文，要看原文的指向“原文出处”。
     - 超长时按现有方式请老师重写一次，重写后仍长就原样保留，不截断。
     - 荧光句数不对（0 句或超过 3 句）同样只请重写一次，不硬改文字。
  2. 要点卡（只对新式点）：
     - 一个大知识点讲完时发一条 kind=recap 的消息：title 是大点标题，每个小要点一项，结论取自它的讲义 notes，chunk_id 带上；
     - 一项一行：从讲义里选最能概括这个小要点的一条完整句子，不截断半句；讲义都很长时，可以让模型用原文词语写一句 ≤40 字的结论，并标出处。用哪种自己定，写理由。
     - 老师自己的收束这时改成一句话或省掉，不和要点卡重复。
     - 要点卡同步、重开后还在，旧消息没有它不受影响。
  3. 接线：
     - 课堂所有老师消息用 ClassroomMarkdown，按说明传 focus 和 messageKey（边生成边显示时保持稳定）；
     - kind=recap 的消息渲染 RecapCard，“原文”按钮接现有原文出处打开方式；
     - 课堂顶部加“只看重点”开关，默认关，状态记在本机设置里（换页面、重开后保持）。
  4. 测试（文件名带 119）：
     - 提示要求荧光与长度；
     - 0 句或超过 3 句荧光会请重写一次、不硬改；
     - 超长重写一次后原样保留；
     - 新式点讲完发 recap、结论都是完整句；
     - 旧式点不发 recap；
     - 只看重点开关切换与保持；
     - recap 的“原文”能打开；
     - 114～116 的课堂测试和旧式点课堂测试照过。
     - 前端用现有浏览器测试写法。
  5. 真实核对（≤¥2）：用约定样例的两个大点各讲一遍。统计每次回复的荧光句数、字数分布；看要点卡每项是否是完整句、是否忠于讲义；截图打开和关闭“只看重点”两种样子（亮色、暗色窄窗）。
  6. 只改 app/classroom.py、course/teacher_agent.py、课堂前端相关文件和 tests/refactor/。编一次网页确认能编过，不提交 dist。

完成标准：第 4 条测试通过；第 5 条核对完成且截图齐全；最后全量一次。花费不超过 ¥2。
回执：写到 D:\MentorFlow\.local\receipts\119-MF03.md。表格列：基线 ／ 提交 ／ 提示改法（荧光、长度）／ 要点卡怎么取结论 ／ 接线与开关 ／ 新增测试 ／ 真实核对（荧光句数、字数分布、要点卡质量、花费）／ 截图路径 ／ 全量 ／ 本地最终占用。
给用户只回一行“119-MF03 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\focus119\report.md。
~~~

## 第一百二十批任务原文（2026-10-14，基线 8d41ea5b）

### [120-MF07] library｜报告最高欠账回放

~~~text
【MentorFlow 第一百二十批｜MF-07（工位 library）｜报告里的“最高欠账”回放仍用旧重排规则，峰值算少（实测账本 14、报告只记 10）：让报告回放和账本用同一套规则（BUG-89）】

现成项目与许可证（第一节）
  - 不引新库。报告回放 course/report.py 改为复用 store/ledger 里按重排原因选择 preserve_past 的同一段逻辑（你 118 改过的那处），不另写一份规则。

开工同步
  cd D:\MentorFlow-office\library\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/library
  git reset --hard 8d41ea5b         # 主线：已合入你的 118
提交：git -c user.name="MentorFlow Worker library" -c user.email="worker-library@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号；不调用真实模型。
D:\MentorFlowApp、D:\MentorFlowData 只读：要用用户数据就用 sqlite3 mode=ro 的 backup 复制到任务目录再用，不改原件。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\report120\，用完即删，只留 report.md。
遇到选择自己决定，回执写明理由，不停下来问。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。花费 ¥0。
同时在跑：MF-03 改课堂（classroom.py、teacher_agent.py、前端），MF-06 改 planner_agents.py；你不碰它们。

背景（CURRENT_STATE [BUG-89] [BUG-88] [BUG-83]）：
  - 你在 118 发现：course/report.py 第 90 行左右的独立峰值回放，replan 时直接调 ledger.replan(today, window, total)，不看重排原因。
  - 所以 restructure、point_minutes、用户改日期这几种保住欠账的重排，在报告回放里仍按旧规则把欠账移走。
  - 实测活跃账本峰值曾为 14，报告只记 10。当前欠账和每日目标是对的，只是报告里的“最高欠账”偏小。

要做
  1. 报告回放按事件里记录的重排原因选择与账本相同的规则，复用同一个函数或判断，不在 report.py 里另写一份。
  2. 测试（文件名带 120）：
     - 有保住欠账的重排时，报告最高欠账与活跃账本一致（复现 14 对 10 的例子）；
     - 旧的普通重排事件按原规则；
     - 已有报告测试照过。
  3. 用用户计划的只读副本对比修前修后报告里的最高欠账与提前量，贴进回执。
  4. 只改 course/report.py（以及为复用而必须调整的 store.py/ledger.py 的一两处）和 tests/refactor/。

完成标准：第 2 条测试通过；第 3 条有数字；最后全量一次。
回执：写到 D:\MentorFlow\.local\receipts\120-MF07.md。表格列：基线 ／ 提交 ／ 改法 ／ 副本修前修后数字 ／ 新增测试 ／ 全量 ／ 本地最终占用。
给用户只回一行“120-MF07 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\report120\report.md。
~~~

## 第一百二十一批任务原文（2026-10-14，基线 37a1743c）

### [121-MF06] tools｜新建计划接按目录切

~~~text
【MentorFlow 第一百二十一批｜MF-06（工位 tools）｜把按原文目录切点接进“新建计划”：新计划直接出新式大知识点、小要点和资料导航；讲义改到模块备题时再生成，不在建计划时一次花完（BUG-86）】

现成项目与许可证（第一节）
  - 不引新库。用你 115 合入的按原文目录切分函数（planner_agents.py），接进现有建计划流程 app/plan_flow.py（替换 _generate_module_batches 按 12000 字分批出点那一步）。
  - 讲义生成挂到现有的模块后台准备（plan_flow 里给模块备题的那条后台流程，[TEACH-08]），和备题同一时机。
  - 新式点的数据格式照 114 共用约定（structure=concept_v1、goal、subpoints、notes；modules.navigation）。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard 37a1743c         # 主线：已合入你的 115、MF-07 的 118（重排保住欠账）
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读；不要启动、结束或重启用户的软件和源代码版。E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。
产物在 D:\MentorFlow\.local\test-tmp\wire121\，中间产物用完即删，只留 report.md；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有超出花费上限才停。测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（跑浏览器测试前把 D:\MentorFlow\.local\node 加进 PATH，basetemp 的父目录先建好）。
花费：替身离线做完；最后一次真实建计划核对上限 ¥4。真实模式参数同以往（--live --account-id mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932 --preset-root D:\MentorFlowData\account-shell；Key 不读、不显示、不保存）。
同时在跑：
  - MF-03 改课堂（classroom.py、teacher_agent.py、课堂前端）；
  - MF-07 改 course/report.py。
  - 你不碰它们。

背景（CURRENT_STATE [BUG-86] [PLAN-06] [TEACH-08]）：
  - 用户的计划已用你的按原文目录切法跑完：440 个旧点 → 144 个大点、408 个小要点、214 条导航，硬规则全过。等 2.0.15 发布后由架构师换入用户计划。
  - 但新建计划仍走旧的按 12000 字分批出点，新用户（马上要发给朋友）建出来的计划还是碎点、书单也成点。
  - 讲义成本：你 115 里 408 个小要点的讲义约 99 次调用、¥8 左右（高峰价）。用户用自己的 Key，建计划时一次全生成太贵，很多模块不一定学到。
    所以讲义改成和备题一样，到模块后台准备时再生成；建计划时只做章节分类和切分（便宜）。

要做
  1. 建计划流程：
     - 在按资料目录定好模块之后，每个模块的点改用按原文目录切分：大点、小要点、资料导航，写成新式点（不带讲义）；
     - 旧的分批出点不再用于新计划；已有计划不受影响。
     - 估时用现有内容估时；计划总分钟、每日目标照现有流程。
  2. 讲义生成挪到模块后台准备：
     - 模块开始准备（备题）时，为该模块的新式小要点生成讲义（照 115 的摘句规则：只摘原文、标出处、短代码可原样、失败重试一次、仍失败就不带讲义，老师照原文讲）；
     - 讲义写进点的 subpoints[].notes 并同步；
     - 已有讲义的不重复生成；
     - 准备失败不挡课堂。
  3. 课堂在讲义还没准备好时：新式点照样能讲（老师按小要点原文讲）；讲义生成好后下一次显示即用上。确认现有课堂代码（114～116 已合入）在 notes 为空时不报错，有问题写进回执，不改课堂文件。
  4. 建计划页面上“大致模块 → 知识点”那一步的展示，如果按点数显示，改成显示大点数和小要点数；资料导航在计划详情里能看到。只改计划流程相关的前端，不碰课堂页面。
  5. 测试（文件名带 121）：
     - 新计划出新式点和导航、不再出书单点；
     - 已有旧计划不变；
     - 讲义在模块准备时生成、已有不重复、失败不挡课；
     - notes 为空时课堂能讲；
     - 计划总分钟与每日目标正常。
  6. 真实核对（≤¥4）：用一份小资料（例如以前测试用过的 gcf-small.html 或 JavaGuide 的一个小章节）完整建一份计划，再让第一个模块完成后台准备。
     - 回执写：模块数、大点和小要点数、导航条目、建计划花费、模块准备（讲义＋备题）花费、用时；
     - 抽 3 个小要点核对讲义都在原文里。
  7. 只改 course/planner_agents.py、app/plan_flow.py、计划流程相关前端（不含课堂页面）和 tests/refactor/。

完成标准：第 5 条测试通过；第 6 条核对完成；最后全量一次。花费不超过 ¥4。
回执：写到 D:\MentorFlow\.local\receipts\121-MF06.md。表格列：基线 ／ 提交 ／ 建计划怎么接 ／ 讲义改到何时生成 ／ notes 为空时课堂表现 ／ 前端改了什么 ／ 新增测试 ／ 真实核对（数量、花费、用时、讲义抽查）／ 全量 ／ 本地最终占用。
给用户只回一行“121-MF06 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\wire121\report.md。
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

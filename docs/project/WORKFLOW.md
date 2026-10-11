# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-11）：线上 2.0.12。最要紧的是把安装与更新整条流程在真打包程序上走通（还没给外部用户发过任何版本，就卡在这里）。已合入：102-MF05b（登录窗口不闪）、102-MF04 + 102-MF04b（软件内更新自己退出、下次启动安装、目录清理；登录窗口入口等四条真打包已通过）、102-MF05c（真安装程序全新安装与覆盖升级通过）。还差主界面入口（102-MF04c，已起草）。**用户 2026-10-11 说先别发新版本**：不推送、不触发 GitHub 流程，等用户说再发。102-MF04b、102-MF05c 合入后全量 3471 通过（66f29547）。全量里 loading86、self_test81、status88 三个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。101 合入后全量 3445 通过、status88 偶发 1 项。

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
- **发版**：修好就用流水线发版，让用户在装好的软件上点更新亲测（操作手册“发布新版”）。**本机先验、GitHub 一版只跑一次（用户 2026-10-11 提出）**：先在本机用同一个打包脚本打预览版，跑一遍 scripts/release 的发版前总验收（102-MF04c 整理），过了才触发 mf-build / mf-publish；没在本机验过的版本不上 GitHub。正式版不在本机打：本机环境会把别的 DLL 混进包里（102-MF04 实测混入过 ICU/OpenSSL），而且同一版本两边打出来的文件不同，会扰乱以后的增量更新。
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
| main | 以 `git rev-parse --short HEAD` 为准；第九十五批合入 c77aaf07 |
| 已发布 | 2.0.12 = 5d7c211e（非必须；启动提速、BUG-59；下载站安装程序 2.0.12）；强制线 2.0.3。main 已含未发布的第一百零二批（主界面入口未经真打包验证），102-MF04c 通过前不发版 |
| GitHub | main 已推送（私有仓库）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 空闲 | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 102-MF04c 真打包补验主界面入口、注册登录填 Key、更新计时（已起草，待转发） | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 102-MF04c（runtime）：发 2.0.13 前的总验收，全部用最终代码：真安装程序全新安装与覆盖升级、主界面入口的软件内更新（网页按钮用 Chrome DevTools 协议点）、新用户注册·登录·填 Key、更新分段计时（[BUG-74]）、前台授权链路核对，并整理成发版前可重复跑的脚本。已起草，待转发。
- 过了 → 发 2.0.13（含安装程序）→ 验证公网清单与老客户端能下载 → 用户用安装程序覆盖一次（2.0.12 及更早的版本里不要点更新，老代码会失败）→ 用户在自己机器上走一遍注册（[ACC-02]）→ 发给外部用户。

## 第一百零二批补做二任务原文（2026-10-11，基线 66f29547）

### [102-MF04c] runtime｜发版前总验收：真安装程序全新安装与覆盖、主界面入口更新、注册登录填 Key、更新分段计时（¥0）

~~~text
【MentorFlow 第一百零二批补做二｜MF-04（工位 runtime）｜发 2.0.13 前用最终代码做一次总验收：真安装程序全新安装和覆盖升级；主界面入口的软件内更新；新用户注册、登录、填 Key 的界面能用；更新各阶段计时；网页里的按钮和输入框用 Chrome DevTools 协议操作，全程不占用户的鼠标键盘】

现成项目与许可证（第一节）
  - 网页内容：Qt WebEngine 自带 Chrome DevTools 协议（CDP）。只在测试进程的环境里设 QTWEBENGINE_REMOTE_DEBUGGING=127.0.0.1:<端口>，产品代码不改。用下面两种办法之一发 CDP 命令（Runtime.evaluate 点按钮，Input.insertText 输入文字）：
      - D:\MentorFlow\.local\node 里 Node 24 自带的 WebSocket，零依赖；
      - 在任务目录的独立 venv 装 Playwright（Apache-2.0），用 connect_over_cdp。
    你上一轮查到 Qt 6.11 的 WebEngine 无障碍接口只支持聚焦，所以网页内容不再用 UIA；原生 Qt 按钮照旧用 UIA InvokePattern。
  - 复用你留在 D:\MentorFlow\.local\test-tmp\inapp102b\ 的脚本：构建、本机更新源、HTTPS 账号替身、uia.ps1。冻结构建等大文件架构师已删，需要就重新构建。
  - 编预览版安装程序、用 UIA 走 Inno 向导、前后对比注册表和快捷方式，照 102-MF05c 的做法：报告在 D:\MentorFlow\.local\test-tmp\setup102c\report.md。
  - 打包用预览版通道（APP_ID 带 .UAT.）。主线已合入 102-MF05c 的钩子：预览版用自己的 Windows 凭据服务名，不碰正式版。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 66f29547         # 主线：已合入你的 ed5a2726 和 102-MF05c
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不访问正式账号服务器，不注册真实账号，不调用真实模型。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
不占用户的鼠标键盘（硬规定）：不用 computer-use，不模拟鼠标或键盘，不靠抢前台去操作窗口。测试窗口出现在屏幕上可以，不能关或操作用户自己的任何窗口。所有进程（包括 UIA、CDP 辅助脚本和 PowerShell）的 LOCALAPPDATA、TEMP、TMP 都指到任务目录。
另：你上一轮报告的 C:\Users\21533\AppData\Local\.mfu\c08abdd8ee07，架构师查过已经不存在，不用管。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次（没改代码就不跑）。冻结构建、安装副本、下载源用完立刻自己删，只留 report.md、截图和可复用的小脚本。删除被拦住就列出路径交架构师，不换工具绕过。
同时在跑：无。
产物目录：D:\MentorFlow\.local\test-tmp\inapp102c\。目标三小时内交回。花费上限 ¥0。
背景（CURRENT_STATE [BUG-71] [BUG-74]，「当前阶段」里“发给外部新用户”的放行标准）：102-MF04b 真打包跑通了四条：登录窗口入口、退不掉转下次启动、下次启动安装、用户那台的升级路径。主界面入口没跑，因为主界面的更新按钮在网页里，UIA 点不动。用户日常是在主界面里看到更新的，这一条不过不发 2.0.13。102-MF05c 的真安装程序全新安装和覆盖升级已通过；注册页它用 UIA 输不进字，没确认能输入。

要做（都用基线代码冻结的预览版真打包程序，两个测试版本 X → Y，测试签名、本机更新源、本机 HTTPS 账号替身；启动走桌面图标指向的启动器）
  1. 新用户界面（朋友的路径）：用 X 的真安装程序（预览版）全新安装，用 UIA 走完向导，空账号。依次检查：
     - 注册页用 CDP 输入邮箱、密码等，提交给本机账号替身，走完注册（有验证码步骤就让替身给固定码）；
     - 登录；
     - 设置页填一个假的 DeepSeek Key，存进预览版自己的凭据服务；
     - 重启软件后 Key 仍显示“已设置”。
     不建计划，因为建计划要调用真实模型。
  2. 主界面入口：接着第 1 条，在主界面用 CDP 改一项能核对的设置。然后让更新提示在主界面出来：按产品实际，出网页提示就点网页的，出原生更新框就用 UIA 点。没有自动弹出就去设置页点“检查更新”。
     点“立即更新并重启”后应当：App 自己退出 → “正在更新 MentorFlow…” → Y 的窗口出来。
     核对：版本是 Y；HEALTHY；pending 消失；Updater 是 Y；刚才改的设置还在；Key 还在；界面上没有英文。更新后是直接进主界面还是回登录窗口，按“更新后自动登录”的设计如实记录。
  3. 覆盖升级（用户那台的路径）：照 102-MF05c 第 3 条再做一遍，用最终代码确认。102-MF05c 是在你 102-MF04b 的改动之前跑的。
     - 用 2.0.12 代码（5d7c211e）编预览版旧版 O，装好并启动一次；
     - 把安装目录做成用户那台的样子：过期 pending、ROLLED_BACK、几个 staging-/retired- 目录，Data 里留几份文件；
     - 用 X 的真安装程序覆盖。
     核对：不假报失败；HEALTHY；Updater 已换；桌面图标还在；两次启动后垃圾目录清掉；Data 里那几份文件逐字节不变。
  4. 分段计时（[BUG-74]）：上一轮 Y 只改了版本号，点击到新窗口却用了 124.6 秒：准备 73.7 秒，准备完到交接约 10 秒，App 退出到新窗口 40.5 秒；下次启动安装 29.8 秒。
     这一轮在第 2 条那次更新里给每个阶段打点：下载、重建暂存（复制和哈希了多少文件、多少字节）、刷新更新程序、协调器启动、事务里复制到 staging、校验、换目录、新 App 冷启动到窗口出来。
     写出最耗时的三段和改法建议（例如同一磁盘用移动代替复制、不重复哈希），这一轮不改。
  5. 新窗口有没有到最前面：Windows 只让用户刚点过的程序抢前台，UIA 和 CDP 的点击都不算，所以自动化测不出来，这条不当完成标准。只做两件事：
     - 核对 App → 更新程序 → 新 App 的前台授权链路完整不完整（谁调了 AllowSetForegroundWindow，谁调了 SetForegroundWindow），缺一步就补上；
     - 在日志里记下每一步的返回值和当时的前台窗口，以后用户第一次真更新时，看日志就知道卡在哪一步。
  6. 发现问题当场修，只改 refactor/desktop/delivery/、updates.py、update_dialog.py、startup_window.py 和相关测试；要改网页或账号代码的，先写清现象交架构师。改完重新冻结，把受影响的场景重跑到通过。
  7. 把这套真打包测试脚本整理成 scripts/release/ 下能重复运行的一套，附一页说明。范围：构建两个测试版本、编预览版安装程序、本机更新源、账号替身、UIA/CDP 操作，以及本轮场景 1～3 和上一轮的场景，以后每次发版前都能跑。不放进 tests/ 的自动收集。
完成标准：第 1、2、3 条用最终代码在真安装程序和真打包程序上跑通，有截图和日志片段；第 4 条有分段数据；第 5 条有链路核对结果；第 7 条提交在工位。改了产品代码的话，最后全量跑一次并通过（loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明）。达不到就写“部分完成”，不能用替身点击或离线测试代替。
回执：写到 D:\MentorFlow\.local\receipts\102-MF04c.md。表格列：基线 ／ 提交 ／ 全新安装与注册·登录·Key ／ 主界面入口结果与耗时 ／ 覆盖升级 ／ 分段计时前三与建议 ／ 前台链路 ／ 新问题与修法 ／ 可重复脚本位置 ／ 截图 ／ 全量 ／ 本地最终占用。给用户只回一行“102-MF04c 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\inapp102c\report.md。
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

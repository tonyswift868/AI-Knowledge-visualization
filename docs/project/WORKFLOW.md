# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-12 交班）：线上 2.0.13，用户 2026-10-11 已用安装程序覆盖（HEALTHY，垃圾已清）。103-MF03 已合入（99fcb13c：随口聊聊保留回答、三个对话页这一轮顶到最上面，[BUG-78]；网页已编进 dist）。它的真打包排练查出 [BUG-79]（更新后 Updater 没换新），104-MF04 已起草待转发。接班后：①把 104-MF04 交用户转发；②同时问用户方不方便看 103 的界面：用户先关掉装好的软件（同一账号同时只能一台在线），架构师用操作手册的命令启动源代码版，用户试随口聊聊，三句答完选“接着学”（选“跳过”会真记进度并同步）；③两件都好了发 2.0.14（只发一次 GitHub，建议 build_installer=true，让下载站也是 2.0.14），用户在装好的软件里点更新，架构师看 launcher-startup.jsonl 的前台结果和 updater-refresh.json。全量里 loading86、self_test81、status88 三个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。

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
| main | 以 `git rev-parse --short HEAD` 为准；103-MF03 合入 99fcb13c。本地比 origin 多几条（文档 + 103 合入），随 2.0.14 发版推送 |
| 已发布 | 2.0.13 = 10e625f4（非必须；下载站安装程序 2.0.13）；强制线 2.0.3 |
| GitHub | main 已推送（私有仓库）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 空闲 | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 104-MF04 更新后马上换 Updater（[BUG-79]）+ 2.0.13 → 新版排练（已起草，待转发） | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 空闲 | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 104-MF04（runtime）：[BUG-79] 更新后马上换 Updater + 2.0.13 → 新版真打包排练。已起草，待转发（原文见下）。
- 同时：用户在源代码版看 103 的界面（[BUG-78]）。
- 都好了 → 发 2.0.14 → 用户在软件里点更新 → 用户走通注册（[ACC-02]）→ 发给朋友。
- 下一批候选：[BUG-74] 更新提速、[BUG-77] 安装程序默认找已有安装位置（用户点头再派）。

## 第一百零四批任务原文（2026-10-12，基线 99fcb13c）

### [104-MF04] runtime｜更新后马上把 Updater 换成新版、2.0.13 → 新版真打包排练（¥0）

~~~text
【MentorFlow 第一百零四批｜MF-04（工位 runtime）｜软件内更新后，新版启动时要把更新程序（Updater）也换成新版：现在只试一次，撞上还没退出的旧协调器就放弃（BUG-79）。修好后用预览版再走一遍 2.0.13 代码 → 新代码，给发 2.0.14 把关】

现成项目与许可证（第一节）
  - 不引新库，也不新增更新机制。重试照抄同一文件里已有的做法：schedule_program_cleanup 在锁忙时按 2/3/5/10/20/40 秒有限重试（102-MF04b），refresh_updater 用同一套。
  - 排练用 scripts/release/rehearsal102/ 现成的一套。103 轮新增了 scenario103.py、refreeze103.py、final103.py，看 README 的“103 轮”一节。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard 99fcb13c         # 主线：已合入 103-MF03（随口聊聊、对话滚动、103 排练脚本）
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不访问正式账号服务器，不注册真实账号，不调用真实模型。D:\MentorFlowApp、D:\MentorFlowData\account-shell 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。遇到选择自己决定，回执写明理由，不停下来问。不要结束或重启用户正在用的软件进程。
不占用户的鼠标键盘（硬规定）：不用 computer-use，不模拟鼠标或键盘，不靠抢前台去操作窗口。网页里用 Chrome DevTools 协议，原生按钮用 UIA InvokePattern。测试窗口出现在屏幕上可以，不能关或操作用户自己的任何窗口。排练的所有进程 LOCALAPPDATA、TEMP、TMP 都指到任务目录。
排练网络边界（硬规定，[BUG-80]）：
  - 预览包必须带 timing_hook 里“只许连本机”的限制。
  - 每次冻结后先用 refreeze103.py 的检查确认包内账号地址是本机替身，再启动。
  - 补旧资源时不要整目录覆盖已准备好的本机配置。
  - 发现任何请求发往非本机地址，立即停下，写进回执。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。冻结构建、安装副本、下载源、测试密钥用完立刻自己删，只留 report.md、截图和小脚本。删除被拦住就列出路径交架构师，不换工具绕过。
同时在跑：无（用户可能同时在用源代码版看 103 的界面，不要碰它的进程和 D:\MentorFlowData）。
产物目录：D:\MentorFlow\.local\test-tmp\inapp104\。MF_UAT_ROOT 指到这里，common.py 的 APP_ID 换成 MentorFlow.UAT.inapp104，模型凭据服务名同步换。目标两小时内交回。花费上限 ¥0。
背景（CURRENT_STATE [BUG-79] [BUG-71] [SYS-06]）：103-MF03 真打包排练：真安装 2.0.13 代码（10e625f4）的预览版，在主界面点“立即更新并重启”更新到新代码。App 部分全部正常：约 36 秒旧 App 退出，70 秒新窗口出来，HEALTHY，pending 清掉，设置和 Key 都在。但更新程序还是 2.0.13，updater-refresh.json = DEFERRED，原因是“another update operation is active”。
  原因：新 App 启动 2 秒后由 per_user_install.schedule_updater_refresh 只试一次 refresh_updater；这时旧协调器还拿着安装更新锁，没退出，于是推迟到“下次启动”。而 schedule_program_cleanup 也在 2 秒时去拿同一把锁（它会重试，刷新不会），所以平常启动也可能一直推迟。
  后果：App 是新的，下一次更新却仍由旧更新程序执行，以后对更新程序本身的修复（比如 [BUG-74] 提速）就到不了用户手里。这个修复在新 App 的代码里，所以随 2.0.14 发出去，用户那台 2.0.13 → 2.0.14 这一次就能生效。

要做
  1. 修 refresh_updater 的调度（per_user_install.py、updates.py 里调用它的地方）
     - 锁忙或事务还没结束（RecoverableError）时，按 2/3/5/10/20/40 秒有限重试；预算用完才写 DEFERRED。
     - 也可以先等 ACK 里的协调器进程退出再刷新；两种选一种或合用，回执写理由。
     - 刷新和清理不要在同一时刻抢锁：可以排成先刷新、后清理，或者错开，自己定。
     - 不能卡住界面线程和启动。
     - 每次尝试的结果用 maintenance_log 写进 launcher-startup.jsonl，以后在用户机器上看日志就知道换没换、试了几次。
  2. 平常启动（不是刚更新完）也要可靠：更新程序比 App 旧，就在这次启动里换掉。用户那台以后万一推迟过一次，也会在下次启动补上。
  3. 测试（文件名带 104，别和已有的重名）：
     - 锁先忙 N 次再空出来，刷新成功；
     - 一直忙到预算用完，写 DEFERRED，不抛到界面；
     - 和清理同时启动，两件都完成；
     - 不阻塞启动。
  4. 预览版排练：用 scenario103.py 这条路，旧版 O 用 10e625f4（2.0.13 代码），新版用你的最终代码，都独立冻结，不用复制改版本的 Y。
     - 走一遍：真安装 O → 登录 → 存测试 Key → 主界面网页按钮“立即更新并重启”→ 新版自己打开。
     - 核对：HEALTHY；pending 消失；设置和 Key 在；updater-refresh.json = UPDATED；Updater 的版本文件是新版；从点击到 Updater 换好的总时长。
     - 再正常关掉、从桌面图标启动一次：没有 DEFERRED，清理完成。
     - 记下这次的分段耗时，作为 [BUG-74] 的一条数据，这一轮不改提速。
     - 结束用本轮卸载器清掉预览版的注册和快捷方式，前后快照对比正式版不变。
  5. 只改 refactor/desktop/delivery/per_user_install.py、refactor/desktop/updates.py、tests/refactor/ 和 scripts/release/rehearsal102/。要动别处的先写清原因，再自己决定，回执里说明。
完成标准：
  - 第 4 条在真安装程序和真打包程序上走通：新版第一次启动后 Updater 已换成新版，第二次启动干净；有截图和日志片段。
  - 第 3 条测试通过。
  - 最后全量跑一次并通过。loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明。
  - 达不到就写“部分完成”，不能用替身点击或离线测试代替。
回执：写到 D:\MentorFlow\.local\receipts\104-MF04.md。表格列：基线 ／ 提交 ／ 修法与理由 ／ 新增测试 ／ 排练结果（Updater 换新耗时、第二次启动）／ 分段耗时 ／ 网络边界核对 ／ 新问题与修法 ／ 全量 ／ 本地最终占用。给用户只回一行“104-MF04 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\inapp104\report.md。
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

# MentorFlow — 员工调度（架构师维护）

只记怎么派活、员工与工位、任务与交付状态、架构师的操作步骤；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)（先读开头的「文档维护规则」和「当前阶段」），这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)。本文件同样按“状态位”维护：做完的任务原文和过时的规则直接删（过程在 git 里）。

## 当前工作形式（接手先读）

- **谁在做什么**：用户定需求、做决定、转发任务、亲自测试；两位架构师（Claude 会话）轮流当班，同一时间只有一位动主目录；GPT 员工在各自工位干活。交接只靠本文件、CURRENT_STATE 和 git 提交。
- **接班**：读 AGENTS.md、CURRENT_STATE 开头三节和索引、本文件；看 `git log`；确认工位 `git status` 干净；问用户哪些已起草的任务真的发出去了。
- **交班**（每次停手前都做）：用户定下的事当场改进对应条目；更新「当前阶段」、员工表、待派队列；本地提交。
- **现在在做的事**（2026-10-12）：线上 2.0.13。2.0.14 的改动已全部合入（103-MF03 [BUG-78]、104-MF04 [BUG-79]、105-MF05 0dd7db0c [BUG-77]、105-MF04 071c5da3 [BUG-74]、105-MF03 c2cf62bd [BUG-81]）。两件事并行：①用户在源代码版测（换设备接着最新进度、随口聊聊）；②106-MF04 发版前总验收（已起草，待转发）。两件都过、用户说可以推送，才推送并发 2.0.14（只发一次 GitHub，build_installer=true），再由用户在装好的软件里点更新，架构师看 launcher-startup.jsonl 和 updater-refresh.json（应为 UPDATED）。注意：用户用过源代码版（新代码）后，装好的 2.0.13 同步会卡住直到更新到 2.0.14（[BUG-81]，数据不丢），测试期间只用源代码版。用户测出问题、改了代码，106 要在新基线上重跑。全量里 loading86、self_test81、status88 三个浏览器测试满载时偶发失败（单独跑通过），记为已知不稳定测试。

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
- **服务器**：`ssh -F "D:/MentorFlow/.local/server/ssh_config" mentorflow`（89.45.45.28，mentorflow-admin，sudo -n）。线上后端 mentorflow-slim-backend:MF-75（切换与回滚脚本 /opt/mentorflow/state/mf75/switch.sh，上线前备份同目录）；入口 nginx 容器 mentorflow-e1-preview-edge-1（配置 /opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf）；更新与下载目录 /opt/mentorflow/update-static/e108/app/；资料白名单 /opt/mentorflow/state/mentorflow-e1-preview/catalog/。 2026-10-10 起两个 docker 网络设了 ip_range（BUG-45）：以后用 Compose 操作这些容器，命令末尾必须追加 -f /opt/mentorflow/state/bug45-20261010/network45.override.yml。
- **磁盘（用户 2026-10-10 定）**：测试与临时产物在 .local/test-tmp/ 下；冒烟测试、构建、中间文件用完就自动删，不留着等以后分类。员工删除被系统拦住时，架构师合入后直接删（只留 report.md 和截图），不用再问用户。

## 当前基线

| 项目 | 值 |
| --- | --- |
| main | 以 `git rev-parse --short HEAD` 为准；2.0.14 的改动全部合入（最后一份 105-MF03 c2cf62bd）。本地比 origin 多若干条，随 2.0.14 发版推送 |
| 已发布 | 2.0.13 = 10e625f4（非必须；下载站安装程序 2.0.13）；强制线 2.0.3 |
| GitHub | main 已推送（私有仓库）；发版需要的推送由架构师做，其余推送要用户说 |
| 工位分支 | office/* 只在本地，永不推送；每条任务第一步把工位 reset 到基线 |

## 员工与工位

用户按员工编号（MF-01～MF-07）转发任务；工位（library、runtime、client、platform、tools、classroom、server）在任务里指定。同一个员工、同一个工位同时只能有一个任务。任务号写成“批次-员工编号”。

| 员工 | 当前任务 | 工位 |
| --- | --- | --- |
| MF-01 | 空闲 | server |
| MF-02 | 106-MF02 已起草，待用户转发（[BUG-82] 同步卡住、批量上传） | client |
| MF-03 | 空闲 | classroom |
| MF-04 | 106-MF04 已起草，待用户转发（2.0.14 发版前总验收） | runtime |
| MF-05 | 空闲 | platform |
| MF-06 | 106-MF06 已起草，待用户转发（[BUG-83] 两台设备逾期不一致、重估只做一次） | tools |
| MF-07 | 空闲 | library |

## 待派队列

- 106-MF04（runtime）：2.0.14 发版前总验收。已起草，待转发（原文见下）。
- 106-MF02（client）：[BUG-82] 同步卡住、太慢，查原因并改批量上传（可能要改服务器，上线前问用户）。已起草，待转发。第 1 条原因架构师已查到（[BUG-83] 的重估），估时与重排的修改归 106-MF06；已发出的话转发下面的“给 MF-02 的补充”。合入后 106-MF04 的全量和 2.0.13 → 新版那条要在新基线上重跑。
- 106-MF06（tools）：[BUG-83] 两台设备逾期差一千分钟，查清哪个对、修推算、重估只做一次。已起草，待转发。与 106-MF02 都可能改 store.py，各改各的函数，合并时逐个看。
- 同时：用户在源代码版测 [BUG-81]、[BUG-78]。
- 两件都过、用户说可以推送 → 发 2.0.14 → 用户在软件里点更新 → 用户走通注册（[ACC-02]）→ 发给朋友。

## 第一百零六批任务原文（2026-10-12，基线 c2cf62bd）

### [106-MF04] runtime｜2.0.14 发版前总验收

~~~text
【MentorFlow 第一百零六批｜MF-04（工位 runtime）｜2.0.14 发版前总验收：合入后的主线跑一次全量，再用真安装程序和真打包程序把安装、注册、软件内更新、覆盖升级走一遍】

现成项目与许可证（第一节）
  - 全部用现成的：scripts/release/rehearsal102/ 的排练脚本（你在 104、105 用过的 build/scenario/refreeze/metrics/clear 系列）和 scripts/release/installer105/（MF-05 的安装位置场景脚本）。
  - 用到的外部项目：PyInstaller（GPL 打包例外）、Inno Setup（Inno Setup License），都是项目已在用的。
  - 不写新框架，不引新库；脚本接不上可以改脚本，不改产品代码。

开工同步
  cd D:\MentorFlow-office\runtime\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/runtime
  git reset --hard c2cf62bd         # 主线：2.0.14 的改动全部合入（103、104、105 三份）
提交：git -c user.name="MentorFlow Worker runtime" -c user.email="worker-runtime@mentorflow.local" commit ...（只提交脚本修正，没有就不提交）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不访问正式账号服务器，不注册真实账号，不调用真实模型。
D:\MentorFlowApp、D:\MentorFlowData 只读；E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。遇到选择自己决定，回执写明理由，不停下来问。
不要结束或重启用户正在用的软件进程：用户这段时间可能在用源代码版测试，不碰它的进程和 D:\MentorFlowData。
不占用户的鼠标键盘（硬规定）：不用 computer-use，不模拟鼠标或键盘，不抢前台。网页用 Chrome DevTools 协议，原生按钮用 UIA。所有排练进程 LOCALAPPDATA、TEMP、TMP 指到任务目录。
排练网络边界（硬规定，[BUG-80]）：
  - 预览包带 timing_hook 的“只许连本机”限制；
  - 每次冻结后用 refreeze103.py --check-only 确认账号地址是本机替身；
  - 发现任何请求发往非本机地址，立即停下写进回执。
产物目录：D:\MentorFlow\.local\test-tmp\gate106\。MF_UAT_ROOT 指到这里，APP_ID 用 MentorFlow.UAT.gate106，模型凭据服务名同步换。冻结构建、安装副本、下载源、测试密钥用完立刻删，只留 report.md、截图和小脚本；删除被拦住就列路径交架构师。花费上限 ¥0。
同时在跑：无其他员工任务。

背景（CURRENT_STATE [SYS-06] [BUG-74] [BUG-77] [BUG-78] [BUG-79] [BUG-81]）：
  - 2.0.14 的改动已全部合入主线：
    - 103-MF03 随口聊聊、对话一轮顶到最上面；
    - 104-MF04 更新后 Updater 换新；
    - 105-MF03 上课位置跟着账号同步，课程库 v9→v10，新客户端跳过不认识的记录；
    - 105-MF04 后台准备、重命名换文件；
    - 105-MF05 安装程序默认找到已有安装位置。
  - 三份 105 各自在自己的基线上跑过全量和排练，合在一起还没跑过。105-MF03 的最后一次全量有 1 项失败，事后修了，单项通过，没再跑全量。
  - 按规定（WORKFLOW“本机先验、GitHub 一版只跑一次”），这次总验收过了，架构师才触发 GitHub 发版。用户这期间在源代码版测；如果用户发现问题又改了代码，你这套要在新基线上重跑。

要做
  1. 合入后的主线跑一次全量。loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明；其他失败如实写，不修产品代码，交回。
  2. 冻结（都独立冻结，不复制改版本）：
     - 旧版 O：10e625f4，即用户现在的 2.0.13；
     - 新版 P：主线基线，版本号 9.106.14；
     - 再用同一主线冻结 Q：版本号 9.106.15，用于第 6 条；
     - 新版的安装程序用主线的 Inno 脚本编。
  3. 全新安装：新安装程序 → 位置页默认是原默认位置（没有 gate106 记录）→ 改选任务目录安装 → 桌面图标 → 几秒内出登录窗、不闪 → 注册（CDP 输入，本机替身验证码）→ 登录 → 填测试 Key → 主界面能打开“新建计划”页。
  4. 用户的真实路径：真安装 O 到 gate106\user\MentorFlow → 登录 → 存测试 Key → 主界面点“立即更新并重启”→ P。
     核对：HEALTHY、pending 消失、设置和 Key 在、updater-refresh.json = UPDATED、Updater 版本是新版、课程库升到 v10 且打开正常、第二次启动干净。
     记下点击到新窗口可输入的时间。
  5. 安装程序覆盖升级：在第 4 条那份（自定义位置）上运行 P 的安装程序。
     核对：位置页默认就是 gate106\user\MentorFlow，显示“已安装，可更新应用”；一路下一步覆盖成功，数据不变；C 盘默认位置没有第二份。
  6. 提速路径：在 P 上等后台就绪 → 主界面点更新 → Q。
     记下：后台就绪用时、点击到旧 App 退出、点击到新窗口可输入。目标：可输入不超过 30 秒。
  7. 结束用本轮卸载器清掉 gate106 的预览注册和快捷方式；前后快照对比，正式版注册项、快捷方式、D:\MentorFlowApp 不变。

完成标准：第 1 条全量通过（已知偶发除外）；第 3～6 条在真安装程序和真打包程序上走通，有截图和日志片段。任何一条没过就写“未通过”，并写清停在哪、证据是什么，不能用替身点击或离线测试代替。
回执：写到 D:\MentorFlow\.local\receipts\106-MF04.md。表格列：
  基线 ／ 全量 ／ 全新安装与注册 ／ 2.0.13 → P（含 v10、Updater、用时）／ 覆盖升级与安装位置 ／ P → Q 提速（三个时间）／ 网络边界 ／ 正式版前后快照 ／ 新问题 ／ 本地最终占用。
给用户只回一行“106-MF04 通过/未通过，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\gate106\report.md。
~~~

### [106-MF02] client｜同步卡住、太慢（[BUG-82]）

~~~text
【MentorFlow 第一百零六批｜MF-02（工位 client）｜同步太慢、卡住：源代码版 479 条待上传几分钟一条没动；查清为什么一台设备会改出 471 个知识点要上传，并把同步改成批量（BUG-82）】

现成项目与许可证（第一节）
  - 不引新库，在现有同步里改：
    - 客户端 refactor/learning/sync_source.py、sync_transport.py；
    - course/store.py 的 _records 待同步队列；
    - 服务器 refactor/server/enterprise/structured_sync_api.py 与 structured_sync。
  - 批量上传照行业通行做法：
    - CouchDB _bulk_docs（Apache-2.0）、Firestore 批量写（一次最多 500 条）：一个请求带一批记录，逐条返回成功或冲突；
    - 拉取照现有 /changes 的分页增量。
  - 只借接口形状，不引代码。

开工同步
  cd D:\MentorFlow-office\client\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/client
  git reset --hard c2cf62bd         # 主线：2.0.14 的改动全部合入，含 105-MF03 上课位置同步
提交：git -c user.name="MentorFlow Worker client" -c user.email="worker-client@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署（服务器代码可以改、在本机测，上线由架构师征得用户同意后做）；不碰正式签名私钥；不调用真实模型，不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：可以把 D:\MentorFlowData\account-shell\learning\accounts\<账号>\course.sqlite3 复制一份（用 sqlite3 mode=ro 的 backup 复制，软件正在运行）到任务目录分析，不改原件；也可以只读看 D:\MentorFlowData\account-shell\logs\mentorflow.log。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\sync106\，用完即删，只留 report.md；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有要删用户数据才停。花费上限 ¥0。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。
同时在跑：MF-04 在 runtime 工位做 2.0.14 发版前总验收（用本机 18763/18764 端口）。你起本机服务器用别的端口，不改 scripts/release/。

背景（CURRENT_STATE [BUG-82] [BUG-66] [ARCH-05] [DATA-02] [BUG-81]）：
  - 2026-10-12 11:14 用户用最新主线启动源代码版登录，界面一直“正在同步”，显示共 400 多条。
  - 架构师只读查看源代码版的库：
    - 本地共 2199 条记录，装好的软件那边 2197 条，说明另一台的数据其实已经都拉到了；
    - 待上传 479 条：points 471、modules 6、plans 1、ledger_events 1；
    - 11:14:33 开始同步，到 11:18 待上传数一直是 479，日志里只有“syncing”，没有完成、失败或任何报错；
    - 进程只有一条连到本机代理 127.0.0.1:7897 的连接（用户走系统代理上网）。
    - 11:18:36 日志出现一次 sync reauth（重新认证）后重新 syncing，随后不到 1 分钟传了 306 个点，11:19:31 到 11:19:46 又停在 165 个点不动。卡住和认证、令牌过期或某个请求挂住的关系要查清。
  - 同一个源代码版今天 03:11 登录时，同步几秒就完成了（03:12:09、03:12:35 synced）。
  - 03:13:58 日志有一次 point_difficulty 模型调用（18 秒），附近有 plan_flow._prepare_worker 的警告和 CLASS_BUSY。03:16:46 开始的同步也一直没完成，用户关了软件。
  - 推测（未验证）：这台设备在后台重算了一遍知识点（难度或时长之类），把 471 个点都改了，于是要全部重新上传。而上传是一条一个 PUT（structured_sync_api.py 的 PUT /entities/{kind}/{entity_id}），没有批量接口。
  - 用户关心的是：新设备第一次拉取、两台设备来回切换时，同步要多久。

要做
  1. 查清“为什么改出 471 个点”：
     - 用库副本和日志找出这些 points 改了哪些字段、是哪段代码在什么时候改的；
     - 这是不是每台设备打开计划都会重做一遍；有没有重复花模型的钱；另一台设备已经算过的结果为什么没直接用。
     - 结论写进回执。
     - 如果是“每台设备都会重算、重写”的设计问题：改成算过的结果随同步共享、不在每台设备重复计算；算出相同值不再标记待上传（_put 已有“内容相同不写”的判断，查清为什么没生效）。
  2. 查清“为什么一条没动”：
     - 在本机用 refactor/server 起一个服务器，导入库副本的数据，复现 479 条上传；
     - 找出卡在哪一步：拉取、上传、冲突重试、退避、锁，还是界面线程；为什么既不完成也不报错。
     - 修好：同步的每一轮、每一批都写日志（条数、用时、结果），以后在用户机器上看日志就知道卡在哪。
  3. 改成批量：
     - 服务器加批量上传接口：一次带多条，逐条返回成功、冲突或拒绝；
     - 客户端按批上传，一批建议 100～500 条或按大小上限，自己定；
     - 拉取每页的条数和应用方式也一起看，一页的记录在一个事务里写入。
     - 旧客户端（2.0.13）仍用逐条接口，服务器两种都支持。
     - 界面上的“已完成 x / 共 y 条”照常更新。
  4. 计时（本机服务器，用一个人为加的每请求 250 毫秒延迟模拟用户走代理连服务器）：
     - 改前、改后各记一次：
       a. 新设备第一次拉这个账号的全部数据（约 2200 条）；
       b. 上传这 479 条；
       c. 学了一节课（约 30～50 条变化）后换到另一台设备。
     - 目标：a 不超过 30 秒，b 不超过 10 秒，c 不超过 5 秒。
     - 达不到就写清每段数据和剩下的大头。
  5. 测试（文件名带 106，别和已有的重名）：
     - 批量上传部分冲突，其余成功、冲突的按现有逐字段合并处理；
     - 批量中途断网，下次续传，不重复、不丢；
     - 旧客户端逐条接口仍可用；
     - 第 1 条修好后，同一计划在第二台设备打开不再产生成批的待上传；
     - 同步卡住时有日志。
  6. 只改上面列的客户端同步文件、course/store.py 中与待同步队列和第 1 条原因直接相关的部分、refactor/server 的同步接口和 tests/refactor/。要动别处的先写清原因，再自己决定，回执说明。
     - store.py、classroom.py 刚合入 105-MF03 的上课位置同步，不要改坏它：positions105 测试必须继续通过。

完成标准：
  - 第 1、2 条有明确原因和证据；
  - 第 3、4 条做完并有改前改后计时；
  - 第 5 条测试通过；最后全量跑一次并通过。loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明。
  - 达不到就写“部分完成”或“受阻”。

回执：写到 D:\MentorFlow\.local\receipts\106-MF02.md。表格列：
  基线 ／ 提交 ／ 471 个点为什么被改（字段、代码、是否每台设备都会做、是否重复花钱）／ 为什么卡住不动 ／ 批量接口设计（客户端、服务器、旧客户端兼容）／ 计时改前改后（a、b、c）／ 新增测试 ／ 服务器要上线的改动清单 ／ 全量 ／ 本地最终占用。
给用户只回一行“106-MF02 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\sync106\report.md。
~~~

### [106-MF06] tools｜两台设备逾期不一致、重估只做一次（[BUG-83]）

~~~text
【MentorFlow 第一百零六批｜MF-06（工位 tools）｜同一计划两台设备显示的逾期差了一千分钟：一台在后台按内容重估了时长并重排，逾期从 1132 分变成 114 分。查清哪个对，修好，并让重估只做一次（BUG-83）】

现成项目与许可证（第一节）
  - 不引新库，不新造机制。在项目已有的时长账本（course/ledger.py 的 Ledger/PlanWindow/Credit、course/today.py 的 build_today_status、CourseStore.today_status）和按内容估时（84-MF06 bf18799b、85-MF06 197bc861 合入的代码，用 git log -S point_minutes 查）里查和改。
  - 账本是“事件记录 + 按事件推算”的做法（event sourcing，行业通行）：历史事件不改写，数字由事件推出。照这个原则修，不改已有事件。

开工同步
  cd D:\MentorFlow-office\tools\repo
  git status                        # 应干净；不干净先停下说明
  git checkout office/tools
  git reset --hard c2cf62bd         # 主线：2.0.14 的改动全部合入
提交：git -c user.name="MentorFlow Worker tools" -c user.email="worker-tools@mentorflow.local" commit ...（不提交 dist）
禁止 push、禁止触发 GitHub 工作流；禁止 ssh、连接服务器、部署；不碰正式签名私钥；不调用真实模型（要复现 point_difficulty 用替身），不注册真实账号。
D:\MentorFlowApp、D:\MentorFlowData 只读：两边的 learning\accounts\<账号>\course.sqlite3 各复制一份到任务目录分析（用 sqlite3 mode=ro 的 backup 复制，源代码版可能正在运行），不改原件。不要启动、结束或重启用户的软件和源代码版。
E 盘只读运行；不碰 D:\Kristina*。先读 AGENTS.md。产物在 D:\MentorFlow\.local\test-tmp\ledger106\，用完即删，只留 report.md；删除被拦住就列路径交架构师。含反斜杠的内容写成脚本文件再运行（BUG-10）。
遇到选择自己决定，回执写明理由，不停下来问；只有要改用户数据（包括服务器上已同步的事件）才停：写清要改什么，交回架构师问用户。花费上限 ¥0。
测试要求：工作中只跑与改动相关的测试，全量只在最后跑一次。
同时在跑：
  - MF-02 在 client 工位改同步（sync_source.py、sync_transport.py、服务器同步接口、store.py 的待同步队列）；
  - MF-04 在 runtime 工位做发版前总验收。
  - 你不改同步传输、服务器和上课位置（classroom_positions）的代码；store.py 只改与账本和估时直接相关的部分，回执列出改了哪些函数，方便合并。

背景（CURRENT_STATE [BUG-83] [BUG-82] [BUG-16]，以及索引里的时长账本、估时条目）：
  - 计划“ai-agent学习”（id 029b076315034c00bbd6842a5c9c31d9，JavaGuide，541 个知识点，窗口 2026-10-07～10-20，每日上限 600 分）。
  - 用户在装好的 2.0.13 上看到：2026-10-12 凌晨 3 点（学习日仍是 10-11），课堂顶部“逾期待还 1132 分，今天已还 264 分（含待确认 16 分）”。
  - 10-12 11:20 在源代码版（主线 c2cf62bd）上，同一计划首页和课堂都是“逾期待还 114 分，今天已还 0 分”。课堂横幅“正在读取云端学习记录，已读取 0 条…”读完后数字不变。
  - 用户认为自己确实落后了一千多分钟，114 不对。
  - 架构师只读对比两边的库：
    - 装好的那份：plans.total_minutes = 4967，point_minutes_version 为空，知识点没有 difficulty。
    - 源代码版那份：total_minutes = 3800，point_minutes_unconstrained_total = 3092，point_minutes_version = 2；541 个点的 difficulty、minute_estimate、difficulty_fingerprint、point_minutes_version 被改，其中 471 个点 minutes 变了；6 个模块 learning_minutes 变了。
    - 多出一条账本事件：{"op":"replan","cause":"point_minutes","operation_seq":97,"today":"2026-10-11","total_minutes":3800,"window":{"start":"2026-10-07","end":"2026-10-20","daily_cap_minutes":600,…}}。
    - 时间线：这是源代码版 10-12 03:11 登录后，03:13:58 一次 point_difficulty 调用触发的。装好的 2.0.13 用这个计划学了 5 天，一直没做过这次重估。两份代码都含 84/85-MF06 的估时。
  - 这 471 个点、6 个模块和这条 replan 已在 11:2x 全部上传到服务器（[BUG-82]）。装好的软件更新到 2.0.14 并同步后，也会拿到这条 replan。
  - 文档里的规则：总时长按内容算；[BUG-16] 的修法写明“重排分配从今天与开始日中较晚的一天开始，历史仍按当天冻结”。

要做
  1. 用两份库的副本，在同一个时刻（例如 2026-10-12 11:20，用户的刷新时间设置）分别算 today_status。
     - 把 1132 和 114 的差逐日拆出来：哪些天的目标变了、各变多少、已还的分钟有没有变。
     - 回执给出这张逐日表的摘要。
  2. 按文档规则判断哪个对。
     - 如果这次 replan 改动了今天以前各天的目标（违反“历史按当天冻结”）：这是 bug。改推算：重排只影响今天及以后，过去的欠账照旧；不改已有事件。修好后用副本验证：源代码版那份算出的逾期回到和装好那份一致的量级。
     - 回执写修好后两份各是多少、差在哪。
     - 如果代码和文档都说明 114 才对：不改代码，写清为什么并给数字，架构师去和用户讨论。
  3. 为什么装好的 2.0.13 五天没做重估、源代码版一登录两分钟就做了：查清触发条件，回执写明。
  4. 重估一份计划只做一次。
     - 哪台设备先做，结果随同步给另一台；另一台看到版本已是 2、指纹一致就不再调模型、不再重写知识点、不再追加 replan。
     - 两台在同步前都做了的情况也要幂等：不能出现两条重复的 replan 把账算两遍。
     - 算出的值和已有的一样时，不要把知识点标成待上传。
  5. 已在服务器上的这条 replan 和 471 个点：
     - 第 2 条如果改的是推算方法，更新后自然就对，用副本验证即可。
     - 如果非要改数据或删事件才能对：不要做，写清方案交回。
  6. 测试（文件名带 106，别和已有的重名）：
     - 窗口进行到一半时重估，过去各天目标与欠账不变、今天及以后重新分配；
     - 两台设备先后重估只记一次；
     - 已重估的计划在另一台打开不调模型、不产生待上传；
     - 已有的账本测试（含 BUG-16 的时间加速剧本）全部照过。
  7. 只改 course/ 下账本与估时相关文件、app/plan_flow.py 中触发重估的部分、store.py 中与此直接相关的函数和 tests/refactor/。要动别处的先写清原因，再自己决定，回执说明。

完成标准：
  - 第 1、3 条有数字和证据；
  - 第 2 条有明确结论：修了就有副本验证；没修就写清理由；
  - 第 4、6 条完成且测试通过；最后全量跑一次并通过。loading86、self_test81、status88 是已知偶发，失败时单独重跑那一项并写明。
  - 达不到就写“部分完成”或“受阻”。

回执：写到 D:\MentorFlow\.local\receipts\106-MF06.md。表格列：
  基线 ／ 提交 ／ 1132 与 114 逐日差 ／ 哪个对、依据 ／ 修法（改了哪些函数）／ 修后两份副本的逾期 ／ 2.0.13 为什么没重估 ／ 只做一次的做法 ／ 服务器上已有数据怎么办 ／ 新增测试 ／ 全量 ／ 本地最终占用。
给用户只回一行“106-MF06 完成/部分完成/受阻，回执已写入”。报告写到 D:\MentorFlow\.local\test-tmp\ledger106\report.md。
~~~

### [106-MF02 补充] 给 MF-02

~~~text
【给 MF-02 的补充｜106-MF02】第 1 条“为什么改出 471 个点”架构师已查到：是源代码版 10-12 03:13 按内容重估时长（84/85-MF06 的 point_minutes v2），改了 541 个点的 difficulty、minute_estimate 等字段和 471 个点的 minutes，追加一条 replan 账本事件。这部分的修改（只做一次、不重复调模型、相同值不标待上传）交给 MF-06（106-MF06）。你第 1 条只在回执里确认这个结论，不改估时、重排和账本代码；第 5 条里“第二台设备打开不再产生成批待上传”那一项也归 MF-06，你不用测。其余照原任务做。另：课堂横幅“正在读取云端学习记录，已读取 0 条…”在上传卡住时一直挂着，读完数字也不变，用户以为出错了；改成按真实的拉取和上传进度显示，没有要拉的就不挂这个横幅。
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
